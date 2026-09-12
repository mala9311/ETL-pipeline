#!/usr/bin/env python3
"""
CollegeDunia MSc Course Scraper
Scrapes Overview, Syllabus, Jobs/Careers, and FAQ sections from CollegeDunia course pages.

Requirements:
    pip install playwright pandas
    playwright install chromium

Usage:
    python scraper.py
    
Output:
    collegedunia_msc_data.json   - Full structured JSON
    collegedunia_msc_data.csv    - Flat CSV summary
"""

import json
import time
import random
import re
import csv
import traceback
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import pandas as pd


# ─────────────────────────────── helpers ────────────────────────────────── #

def human_delay(lo=1.5, hi=3.5):
    time.sleep(random.uniform(lo, hi))


def clean(text: str) -> str:
    """Strip excess whitespace."""
    return re.sub(r"\s+", " ", text or "").strip()


# ─────────────────────────── section extractors ─────────────────────────── #

def scroll_full_page(page):
    """Scroll gradually to trigger lazy-load."""
    height = page.evaluate("document.body.scrollHeight")
    for y in range(0, height, 600):
        page.evaluate(f"window.scrollTo(0, {y})")
        time.sleep(0.15)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.5)


def click_tab_if_exists(page, keywords: list[str]) -> bool:
    """Try to click a navigation tab matching any keyword. Returns True if clicked."""
    selectors = [
        "nav a", "ul.tabs a", "ul.tab-list a", "[role=tab]",
        "a.tab", "li.tab a", ".course-tab a", ".section-tab a",
        "div.tabs a", "[data-tab]", "a[href*='#']",
    ]
    for sel in selectors:
        for el in page.query_selector_all(sel):
            txt = clean(el.inner_text()).lower()
            if any(kw in txt for kw in keywords):
                try:
                    el.click()
                    time.sleep(1.5)
                    return True
                except Exception:
                    pass
    return False


def extract_section_by_heading(page, keywords: list[str]) -> str:
    """Find a heading matching keywords and grab the surrounding content block."""
    heading_sels = ["h1", "h2", "h3", "h4", "h5", "strong"]
    for sel in heading_sels:
        for el in page.query_selector_all(sel):
            txt = clean(el.inner_text()).lower()
            if any(kw in txt for kw in keywords):
                # Try to get the parent section's text
                for parent_sel in ["section", "div", "article"]:
                    try:
                        parent = el.evaluate_handle(
                            f"el => el.closest(\'{parent_sel}\')"
                        )
                        parent_text = clean(parent.inner_text())
                        if len(parent_text) > 100:
                            return parent_text[:8000]
                    except Exception:
                        pass
                # Fallback: just return siblings until next heading
                try:
                    return clean(el.evaluate("""
                        el => {
                            let result = el.innerText + '\\n';
                            let sib = el.nextElementSibling;
                            let count = 0;
                            while (sib && count < 20) {
                                const tag = sib.tagName.toLowerCase();
                                if (['h1','h2','h3'].includes(tag)) break;
                                result += sib.innerText + '\\n';
                                sib = sib.nextElementSibling;
                                count++;
                            }
                            return result;
                        }
                    """))[:6000]
                except Exception:
                    pass
    return ""


def extract_faq(page) -> list[dict]:
    """Extract FAQ Q&A pairs."""
    faqs = []
    
    # Try accordion / details elements
    for details in page.query_selector_all("details, [class*=faq], [class*=accordion]"):
        try:
            q_el = details.query_selector("summary, [class*=question], h3, h4, strong")
            question = clean(q_el.inner_text()) if q_el else ""
            full_text = clean(details.inner_text())
            answer = full_text.replace(question, "").strip()[:2000]
            if question:
                faqs.append({"question": question, "answer": answer})
        except Exception:
            pass
    
    if faqs:
        return faqs

    # Try generic Q/A pattern by heading + paragraph
    items = page.query_selector_all("h3, h4, [class*=question]")
    for item in items:
        try:
            txt = clean(item.inner_text())
            if "?" in txt:
                answer_el = item.evaluate_handle(
                    "el => el.nextElementSibling"
                )
                ans = clean(answer_el.inner_text())[:2000] if answer_el else ""
                if ans:
                    faqs.append({"question": txt, "answer": ans})
        except Exception:
            pass
    
    return faqs


def extract_syllabus_tables(page) -> list[dict]:
    """Extract syllabus semester/year tables."""
    tables = []
    for table in page.query_selector_all("table"):
        try:
            rows = []
            for row in table.query_selector_all("tr"):
                cells = [clean(c.inner_text()) for c in row.query_selector_all("th,td")]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append({"rows": rows})
        except Exception:
            pass
    return tables


def extract_list_items(page, section_text: str) -> list[str]:
    """Split section text into meaningful bullet points."""
    lines = [l.strip() for l in section_text.splitlines() if len(l.strip()) > 10]
    return list(dict.fromkeys(lines))  # deduplicate


# ─────────────────────────────── main scraper ────────────────────────────── #

def scrape_course(page, url: str, branch_name: str) -> dict:
    result = {
        "branch_name": branch_name,
        "url": url,
        "overview": "",
        "syllabus": {"text": "", "tables": []},
        "jobs": "",
        "faq": [],
        "error": None,
    }

    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if resp and resp.status == 403:
            result["error"] = "403 Forbidden"
            return result

        human_delay(2, 4)
        scroll_full_page(page)

        # ── OVERVIEW ──────────────────────────────────────────────────────
        overview_keywords = ["overview", "about", "introduction", "what is", "course details"]
        
        # Method 1: look for a dedicated overview section/div
        for sel in ["#overview", "[id*=overview]", "[class*=overview]", 
                    "section[data-section*=overview]"]:
            el = page.query_selector(sel)
            if el:
                result["overview"] = clean(el.inner_text())[:6000]
                break
        
        # Method 2: heading-based extraction
        if not result["overview"]:
            result["overview"] = extract_section_by_heading(page, overview_keywords)
        
        # Method 3: first substantial paragraph block
        if not result["overview"]:
            for el in page.query_selector_all("p"):
                txt = clean(el.inner_text())
                if len(txt) > 200:
                    result["overview"] = txt[:4000]
                    break

        # ── SYLLABUS ─────────────────────────────────────────────────────
        syllabus_keywords = ["syllabus", "curriculum", "subjects", "semester"]
        
        clicked = click_tab_if_exists(page, syllabus_keywords)
        if clicked:
            human_delay(1, 2)

        for sel in ["#syllabus", "[id*=syllabus]", "[class*=syllabus]",
                    "#curriculum", "[class*=curriculum]"]:
            el = page.query_selector(sel)
            if el:
                result["syllabus"]["text"] = clean(el.inner_text())[:6000]
                break
        
        if not result["syllabus"]["text"]:
            result["syllabus"]["text"] = extract_section_by_heading(page, syllabus_keywords)
        
        result["syllabus"]["tables"] = extract_syllabus_tables(page)

        # ── JOBS / CAREERS ────────────────────────────────────────────────
        jobs_keywords = ["job", "career", "employment", "placement", "scope", "prospect"]
        
        clicked = click_tab_if_exists(page, jobs_keywords)
        if clicked:
            human_delay(1, 2)

        for sel in ["#jobs", "[id*=job]", "[class*=job]",
                    "#career", "[id*=career]", "[class*=career]",
                    "#scope", "[class*=scope]"]:
            el = page.query_selector(sel)
            if el:
                result["jobs"] = clean(el.inner_text())[:6000]
                break
        
        if not result["jobs"]:
            result["jobs"] = extract_section_by_heading(page, jobs_keywords)

        # ── FAQ ───────────────────────────────────────────────────────────
        faq_keywords = ["faq", "frequently asked", "question"]
        
        clicked = click_tab_if_exists(page, faq_keywords)
        if clicked:
            human_delay(1, 2)

        for sel in ["#faq", "[id*=faq]", "[class*=faq]",
                    "#faqs", "[class*=accordion]"]:
            el = page.query_selector(sel)
            if el:
                # Try to expand all accordion items
                for btn in el.query_selector_all("button, summary, [class*=toggle]"):
                    try:
                        btn.click()
                        time.sleep(0.3)
                    except Exception:
                        pass
                break
        
        result["faq"] = extract_faq(page)
        
        if not result["faq"]:
            # Fallback: find FAQ section by heading and parse text
            faq_text = extract_section_by_heading(page, faq_keywords)
            if faq_text:
                result["faq"] = [{"question": "FAQ Content", "answer": faq_text[:5000]}]

    except PlaywrightTimeout:
        result["error"] = "Timeout"
    except Exception as e:
        result["error"] = str(e)
        traceback.print_exc()

    return result


# ──────────────────────────────── runner ─────────────────────────────────── #

def main():

    # Load URLs
    BASE_DIR = Path(__file__).parent
    df = pd.read_csv(BASE_DIR / "msc_branch_url.csv")
    urls = list(zip(df["branch_name"], df["url"]))
    
    output_path = BASE_DIR / "collegedunia_msc_data.json"
    csv_path = BASE_DIR /"collegedunia_msc_data.csv"
    
    # Load existing progress
    results = []
    if output_path.exists():
        with open(output_path) as f:
            results = json.load(f)
    
    done_urls = {r["url"] for r in results}
    remaining = [(b, u) for b, u in urls if u not in done_urls]
    print(f"Total: {len(urls)} | Done: {len(done_urls)} | Remaining: {len(remaining)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
                "Referer": "https://www.google.com/",
            }
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        
        page = context.new_page()
        
        for i, (branch_name, url) in enumerate(remaining, 1):
            print(f"[{i}/{len(remaining)}] Scraping: {branch_name}")
            data = scrape_course(page, url, branch_name)
            results.append(data)
            
            # Save progress after each URL
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            
            has_data = bool(
                data["overview"] or 
                data["syllabus"]["text"] or 
                data["jobs"] or 
                data["faq"]
            )
            status = "✓" if has_data else "✗"
            err = f" | ERROR: {data['error']}" if data["error"] else ""
            print(f"  {status} overview={bool(data['overview'])} | syllabus={bool(data['syllabus']['text'])} | jobs={bool(data['jobs'])} | faq={len(data['faq'])} items{err}")
            
            # Random polite delay between requests
            if i < len(remaining):
                human_delay(2.5, 5.0)
        
        browser.close()
    
    # ── Write CSV summary ──────────────────────────────────────────────── #
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "branch_name", "url", "overview", "syllabus_text",
            "syllabus_table_count", "jobs", "faq_count", "error"
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "branch_name": r["branch_name"],
                "url": r["url"],
                "overview": r["overview"][:500] + "..." if len(r.get("overview","")) > 500 else r.get("overview",""),
                "syllabus_text": r["syllabus"]["text"][:500] + "..." if len(r["syllabus"]["text"]) > 500 else r["syllabus"]["text"],
                "syllabus_table_count": len(r["syllabus"]["tables"]),
                "jobs": r["jobs"][:500] + "..." if len(r.get("jobs","")) > 500 else r.get("jobs",""),
                "faq_count": len(r["faq"]),
                "error": r.get("error",""),
            })
    
    print(f"\n✅ Done! Saved to:")
    print(f"   {output_path.resolve()}")
    print(f"   {csv_path.resolve()}")


if __name__ == "__main__":
    main()
