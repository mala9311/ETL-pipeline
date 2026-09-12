"""
FAQ Scraper for Collegedunia Polytechnic Branch Pages
=====================================================
Requirements:
    pip install playwright pandas
    playwright install chromium

Usage:
    python faq_Polytechnic.py

Output:
    faq_results.csv  — one row per FAQ item (branch_name, url, question, answer)
    faq_results.json — same data grouped by branch
"""

import json
import time
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
INPUT_CSV = Path(__file__).parent / "polytechnic_branch_url.csv"   # path to your CSV
# INPUT_CSV = "polytechnic_branch_url.csv"   # path to your CSV
OUTPUT_CSV = "faq_results.csv"
OUTPUT_JSON = "faq_results.json"

# ── Selectors to try for FAQ sections ─────────────────────────────────────────
FAQ_SECTION_SELECTORS = [
    "[class*='faq']",
    "[id*='faq']",
    "[class*='FAQ']",
    "[id*='FAQ']",
    "section.faq",
    "div.faq",
    ".accordion",
    "[class*='accordion']",
    "[class*='Accordion']",
]

QUESTION_SELECTORS = [
    "[class*='faq'] [class*='question']",
    "[class*='faq'] [class*='Question']",
    "[class*='faq'] h3",
    "[class*='faq'] h4",
    "[class*='faq'] dt",
    "[class*='accordion'] [class*='title']",
    "[class*='accordion'] [class*='header']",
    "[class*='accordion'] button",
    ".faq-item .question",
    ".faq__question",
    "[class*='faqQuestion']",
]

ANSWER_SELECTORS = [
    "[class*='faq'] [class*='answer']",
    "[class*='faq'] [class*='Answer']",
    "[class*='faq'] p",
    "[class*='faq'] dd",
    "[class*='accordion'] [class*='content']",
    "[class*='accordion'] [class*='body']",
    ".faq-item .answer",
    ".faq__answer",
    "[class*='faqAnswer']",
]


def extract_faqs_from_page(page) -> list[dict]:
    """
    Try multiple strategies to pull FAQ Q&A pairs from a loaded page.
    Returns a list of {"question": ..., "answer": ...} dicts.
    """
    faqs = []

    # ── Strategy 1: structured JSON-LD FAQ schema ──────────────────────────
    try:
        ld_blocks = page.eval_on_selector_all(
            'script[type="application/ld+json"]',
            "els => els.map(e => e.textContent)"
        )
        for block in ld_blocks:
            try:
                data = json.loads(block)
                # handle @graph wrapper
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") == "FAQPage":
                        for entity in item.get("mainEntity", []):
                            q = entity.get("name", "").strip()
                            a_obj = entity.get("acceptedAnswer", {})
                            a = a_obj.get("text", "").strip()
                            if q:
                                faqs.append({"question": q, "answer": a})
            except json.JSONDecodeError:
                pass
        if faqs:
            return faqs
    except Exception:
        pass

    # ── Strategy 2: try to click open accordions then scrape Q+A ──────────
    try:
        for q_sel in QUESTION_SELECTORS:
            q_els = page.query_selector_all(q_sel)
            if not q_els:
                continue
            for q_el in q_els:
                q_text = (q_el.inner_text() or "").strip()
                if not q_text or len(q_text) < 5:
                    continue
                # click to expand
                try:
                    q_el.click()
                    page.wait_for_timeout(400)
                except Exception:
                    pass
                # try to grab sibling / parent answer
                a_text = ""
                try:
                    parent = q_el.evaluate_handle("el => el.closest('[class*=\"faq\"], [class*=\"accordion\"], [class*=\"item\"]')")
                    a_text = parent.as_element().inner_text() if parent.as_element() else ""
                    # remove the question from the answer block
                    a_text = a_text.replace(q_text, "").strip()
                except Exception:
                    pass
                if q_text:
                    faqs.append({"question": q_text, "answer": a_text})
            if faqs:
                return faqs
    except Exception:
        pass

    # ── Strategy 3: plain text fallback — find "FAQ" heading, grab lines ──
    try:
        body_text = page.evaluate("document.body.innerText")
        lines = [l.strip() for l in body_text.split("\n") if l.strip()]
        faq_start = None
        for i, line in enumerate(lines):
            if "frequently asked" in line.lower() or (line.lower() == "faq"):
                faq_start = i
                break

        if faq_start is not None:
            chunk = lines[faq_start + 1: faq_start + 80]  # look at next 80 lines
            i = 0
            while i < len(chunk) - 1:
                q = chunk[i]
                a = chunk[i + 1] if i + 1 < len(chunk) else ""
                # heuristic: questions are shorter and often end with "?"
                if len(q) < 200:
                    faqs.append({"question": q, "answer": a})
                    i += 2
                else:
                    i += 1
    except Exception:
        pass

    return faqs


def scrape_all(urls_df: pd.DataFrame) -> tuple[list[dict], dict]:
    rows = []          # flat list for CSV
    grouped = {}       # branch → list of FAQs for JSON

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
        )
        page = context.new_page()

        total = len(urls_df)
        for idx, row in urls_df.iterrows():
            branch = row["branch_name"]
            url = row["url"]
            print(f"[{idx+1}/{total}] {branch}")
            print(f"        {url}")

            faqs = []
            try:
                page.goto(url, timeout=60_000, wait_until="domcontentloaded")
                # give JS time to hydrate
                page.wait_for_timeout(3000)

                # scroll down to trigger lazy-load
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                page.wait_for_timeout(1500)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)

                faqs = extract_faqs_from_page(page)
                print(f"        → {len(faqs)} FAQ(s) found")

            except PlaywrightTimeout:
                print(f"        ✗ Timeout")
            except Exception as e:
                print(f"        ✗ Error: {e}")

            grouped[branch] = {"url": url, "faqs": faqs}
            for faq in faqs:
                rows.append({
                    "branch_name": branch,
                    "url": url,
                    "question": faq.get("question", ""),
                    "answer": faq.get("answer", ""),
                })

            # polite delay between requests
            time.sleep(1.5)

        browser.close()

    return rows, grouped


def main():
    df = pd.read_csv(INPUT_CSV)
    print(f"Loaded {len(df)} URLs from {INPUT_CSV}\n")

    rows, grouped = scrape_all(df)

    # ── Save CSV ──────────────────────────────────────────────────────────
    out_df = pd.DataFrame(rows, columns=["branch_name", "url", "question", "answer"])
    out_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n✓ CSV saved  → {OUTPUT_CSV}  ({len(out_df)} rows)")

    # ── Save JSON ─────────────────────────────────────────────────────────
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(grouped, f, ensure_ascii=False, indent=2)
    print(f"✓ JSON saved → {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
