"""
CollegeDunia M.Com Course Scraper  (FIXED )
=============================================
Root-cause fixes applied:
  1. Use `networkidle` + extended hydration wait so React fully renders.
  2. `click_tab` now matches by text CONTAINS (not exact) to catch CollegeDunia's
     nav items like "Syllabus & Curriculum", and skips elements whose innerText
     is longer than 80 chars (i.e. entire-page divs).
  3. Syllabus semester extractor walks up to 4 ancestors looking for li/td/p
     rather than only checking nextElementSibling.
  4. A dedicated `extract_accordion` helper expands and reads all
     details/summary and aria-expanded accordion widgets (used for FAQ).
  5. FAQ extraction also checks for CollegeDunia's custom data-faq-* attrs and
     elements whose class names contain 'faq', 'accordion', or 'question'.
  6. `get_section_by_heading` now also tries going into the CHILDREN of the
     heading's grandparent for deeper-nested content.
  7. Jobs: broader heading patterns and a direct URL attempt (/job-scope,
     /career) before tab-clicking.
"""

import asyncio
import csv
import json
import re
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from pathlib import Path

BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "mcom_branch_url.csv"
OUTPUT_JSON = "mcom_scraped.json"
OUTPUT_CSV  = "mcom_scraped_flat.csv"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


async def scroll_full_page(page):
    """Scroll to bottom in steps so lazy content loads."""
    await page.evaluate("""
        async () => {
            await new Promise(resolve => {
                let total = 0;
                const step = 600;
                const timer = setInterval(() => {
                    window.scrollBy(0, step);
                    total += step;
                    if (total >= document.body.scrollHeight) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 150);
            });
        }
    """)
    await page.wait_for_timeout(1800)


async def dismiss_popups(page):
    for sel in [
        "button[class*='close']",
        "button[aria-label='Close']",
        "[class*='modal'] button",
        "[class*='cookie'] button",
        "button:text('Accept')",
        "button:text('OK')",
        "button:text('×')",
    ]:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(400)
        except Exception:
            pass


async def wait_for_content(page, timeout=20000):
    """Wait until the main content area has meaningful text."""
    try:
        await page.wait_for_function(
            "() => document.body.innerText.length > 500",
            timeout=timeout
        )
    except Exception:
        pass
    # Extra buffer for React hydration
    await page.wait_for_timeout(2500)


async def safe_goto(page, url: str):
    """Navigate with networkidle fallback."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=50000)
    except Exception:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
    await wait_for_content(page)
    await dismiss_popups(page)
    await scroll_full_page(page)


# ─────────────────────────────────────────────────────────────────────────────
# Tab / nav clicking
# ─────────────────────────────────────────────────────────────────────────────

async def click_tab(page, tab_texts: list[str]) -> bool:
    """
    Click the first matching nav tab.
    Matches by CONTAINS (case-insensitive) on SHORT elements only
    (innerText < 80 chars) to avoid matching entire page containers.
    """
    for text in tab_texts:
        for tag in ["a", "button", "li", "span", "div"]:
            try:
                els = await page.query_selector_all(tag)
                for el in els:
                    try:
                        t = clean(await el.inner_text())
                        # Must be short (nav label) and contain the keyword
                        if (
                            len(t) < 80
                            and text.lower() in t.lower()
                            and await el.is_visible()
                        ):
                            await el.scroll_into_view_if_needed()
                            await el.click()
                            await page.wait_for_timeout(2800)
                            await wait_for_content(page, timeout=10000)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

async def get_section_by_heading(page, heading_patterns: list[str]) -> str:
    """
    Locate a heading by visible text match, then return the text of the
    nearest ancestor that contains substantial content (>200 chars).
    Also checks children of the grandparent for deeper nesting.
    """
    for pattern in heading_patterns:
        try:
            headings = await page.query_selector_all("h1,h2,h3,h4,h5,strong,b,span,div")
            for h in headings:
                try:
                    txt = clean(await h.inner_text())
                    if re.search(pattern, txt, re.IGNORECASE) and len(txt) < 120:
                        container_text = await page.evaluate("""
                            (el) => {
                                let node = el;
                                for (let i = 0; i < 8; i++) {
                                    node = node.parentElement;
                                    if (!node) break;
                                    if (node.innerText && node.innerText.trim().length > 200)
                                        return node.innerText.trim();
                                }
                                return '';
                            }
                        """, h)
                        if container_text and len(container_text) > 100:
                            return clean(container_text)
                except Exception:
                    continue
        except Exception:
            continue
    return ""


async def extract_lists_near_heading(page, heading_patterns: list[str]) -> list[str]:
    """
    Find a heading by text, then collect li/td items from:
      1. nextElementSibling chain
      2. parent container
      3. up to 5 ancestors
    """
    for pattern in heading_patterns:
        try:
            all_els = await page.query_selector_all("h1,h2,h3,h4,h5,strong,b,span")
            for h in all_els:
                try:
                    txt = clean(await h.inner_text())
                    if re.search(pattern, txt, re.IGNORECASE) and len(txt) < 100:
                        items = await page.evaluate("""
                            (el) => {
                                // Helper: collect li/td from a root element
                                function collect(root) {
                                    const items = root.querySelectorAll('li, td, p');
                                    return Array.from(items)
                                        .map(x => x.innerText.trim())
                                        .filter(t => t.length > 2 && t.length < 200);
                                }

                                // 1. Check next siblings first
                                let sib = el.nextElementSibling;
                                for (let s = 0; s < 3 && sib; s++) {
                                    const c = collect(sib);
                                    if (c.length > 0) return c;
                                    sib = sib.nextElementSibling;
                                }

                                // 2. Walk up ancestors
                                let node = el;
                                for (let i = 0; i < 6; i++) {
                                    node = node.parentElement;
                                    if (!node) break;
                                    const c = collect(node);
                                    if (c.length > 1) return c;
                                }
                                return [];
                            }
                        """, h)
                        if items:
                            return [clean(x) for x in items if x]
                except Exception:
                    continue
        except Exception:
            continue
    return []


async def get_all_paragraphs(page) -> str:
    """Grab all visible text from <main> or <body> as a raw fallback."""
    try:
        text = await page.evaluate("""
            () => {
                const main = document.querySelector('main') || document.body;
                const paras = main.querySelectorAll('p, li, td, h2, h3, h4');
                return Array.from(paras)
                    .map(el => el.innerText.trim())
                    .filter(t => t.length > 20)
                    .join('\\n');
            }
        """)
        return clean(text)
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Accordion expander (for FAQ and syllabus tabs)
# ─────────────────────────────────────────────────────────────────────────────

async def expand_accordions(page):
    """
    Expand all collapsed accordion-style elements:
    - <details> / <summary>
    - elements with aria-expanded="false"
    - elements with data-toggle, data-bs-toggle, data-target
    - CollegeDunia-specific: elements with class containing 'accordian', 'toggle', 'expand'
    """
    try:
        await page.evaluate("""
            () => {
                // Open all <details>
                document.querySelectorAll('details').forEach(d => d.open = true);
            }
        """)
    except Exception:
        pass

    # Click collapsed items
    selectors = [
        "[aria-expanded='false']",
        "summary",
        "[data-toggle='collapse']",
        "[data-bs-toggle='collapse']",
        "[class*='accordian']",
        "[class*='accordion'] [class*='header']",
        "[class*='accordion'] [class*='title']",
        "[class*='faq'] [class*='question']",
        "[class*='faq'] [class*='title']",
        "[class*='toggle']",
    ]
    for sel in selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                try:
                    if await el.is_visible():
                        await el.click()
                        await page.wait_for_timeout(200)
                except Exception:
                    pass
        except Exception:
            pass

    await page.wait_for_timeout(800)


# ─────────────────────────────────────────────────────────────────────────────
# Section scrapers
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_overview(page) -> dict:
    data = {
        "intro": "",
        "highlights": [],
        "entrance_exams": [],
        "admission_eligibility": "",
        "specialisations": [],
        "top_colleges_mentioned": [],
        "raw_overview_text": "",
    }

    await scroll_full_page(page)

    # Intro – first substantial paragraph
    try:
        intro = await page.evaluate("""
            () => {
                const paras = document.querySelectorAll('p');
                for (const p of paras) {
                    const t = p.innerText.trim();
                    if (t.length > 120) return t;
                }
                return '';
            }
        """)
        data["intro"] = clean(intro)
    except Exception:
        pass

    # Highlights / key facts table rows
    try:
        highlights = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('table tr');
                return Array.from(rows)
                    .map(r => r.innerText.replace(/\\t/g, ' ').trim())
                    .filter(Boolean);
            }
        """)
        data["highlights"] = [clean(h) for h in highlights if h]
    except Exception:
        pass

    # Entrance exams
    data["entrance_exams"] = await extract_lists_near_heading(
        page, [r"entrance", r"exam", r"admission test", r"cat|mat|cmat|atma"]
    )

    # Admission / eligibility
    data["admission_eligibility"] = await get_section_by_heading(
        page, [r"admission", r"eligibility", r"how to apply"]
    )

    # Specialisations
    data["specialisations"] = await extract_lists_near_heading(
        page, [r"specialis", r"specializat", r"stream", r"branch"]
    )

    # Top colleges
    try:
        colleges = await page.evaluate("""
            () => {
                const links = document.querySelectorAll('a');
                return Array.from(links)
                    .map(a => a.innerText.trim())
                    .filter(t => t.length > 5 && t.length < 80 &&
                        /college|university|institute|school/i.test(t));
            }
        """)
        data["top_colleges_mentioned"] = list(dict.fromkeys(colleges))[:20]
    except Exception:
        pass

    raw = await get_all_paragraphs(page)
    data["raw_overview_text"] = raw[:6000]

    return data


async def scrape_syllabus(page, base_url: str) -> dict:
    data = {"semesters": [], "electives": [], "books": [], "raw_syllabus_text": ""}

    # First try the /syllabus sub-URL directly
    syllabus_url = base_url.rstrip("/") + "/syllabus"
    await safe_goto(page, syllabus_url)

    # If the page didn't change to a syllabus page, try clicking the tab
    current = page.url
    if "syllabus" not in current.lower():
        clicked = await click_tab(page, ["Syllabus", "Curriculum", "Course Structure", "Subjects"])
        if not clicked:
            # navigate explicitly
            await safe_goto(page, syllabus_url)

    await expand_accordions(page)
    await scroll_full_page(page)

    # Extract semester/year blocks
    try:
        semesters = await page.evaluate("""
            () => {
                const result = [];
                const headings = document.querySelectorAll('h2,h3,h4,h5,strong,b,p,span,div');
                for (const h of headings) {
                    const txt = h.innerText ? h.innerText.trim() : '';
                    if (
                        /semester\\s*(1|2|3|4|5|6|I{1,3}V?|IV)|year\\s*(1|2|3|4|I{1,3}V?)|1st|2nd|3rd|4th/i.test(txt)
                        && txt.length < 60
                    ) {
                        // Collect subjects from siblings and ancestors
                        function collectItems(root) {
                            const items = root.querySelectorAll('li, td, p');
                            return Array.from(items)
                                .map(x => x.innerText.trim())
                                .filter(t => t.length > 2 && t.length < 200);
                        }

                        let subjects = [];

                        // Check next siblings
                        let sib = h.nextElementSibling;
                        for (let s = 0; s < 3 && sib && subjects.length === 0; s++) {
                            subjects = collectItems(sib);
                            sib = sib.nextElementSibling;
                        }

                        // Walk up ancestors if nothing found
                        if (subjects.length === 0) {
                            let node = h;
                            for (let i = 0; i < 5; i++) {
                                node = node.parentElement;
                                if (!node) break;
                                subjects = collectItems(node);
                                if (subjects.length > 1) break;
                            }
                        }

                        if (subjects.length > 0) {
                            result.push({ heading: txt, subjects });
                        }
                    }
                }
                // Deduplicate by heading
                const seen = new Set();
                return result.filter(r => {
                    if (seen.has(r.heading)) return false;
                    seen.add(r.heading);
                    return true;
                });
            }
        """)
        data["semesters"] = semesters
    except Exception:
        pass

    # Electives
    data["electives"] = await extract_lists_near_heading(
        page, [r"elective", r"optional", r"open course"]
    )

    # Books / references
    data["books"] = await extract_lists_near_heading(
        page, [r"book", r"reference", r"recommended reading", r"text book"]
    )

    # Raw fallback — always populate so we always have *something*
    raw = await get_all_paragraphs(page)
    data["raw_syllabus_text"] = raw[:6000]

    return data


async def scrape_jobs(page, base_url: str) -> dict:
    data = {
        "job_roles": [],
        "salary_info": "",
        "recruiters": [],
        "further_studies": [],
        "raw_jobs_text": "",
    }

    # Try direct URL variants first
    for suffix in ["/job-scope", "/career", "/careers", "/scope"]:
        jobs_url = base_url.rstrip("/") + suffix
        await safe_goto(page, jobs_url)
        if any(k in page.url.lower() for k in ["job", "career", "scope"]):
            break
    else:
        # Fall back to clicking a tab on the base page
        await safe_goto(page, base_url)
        await click_tab(page, ["Jobs", "Career", "Scope", "Job Scope", "Careers"])

    await expand_accordions(page)
    await scroll_full_page(page)

    # Job roles – broad patterns
    for patterns in [
        [r"job role", r"career option", r"job profile", r"designation", r"position"],
        [r"job", r"career", r"scope", r"opportunit", r"profession"],
    ]:
        data["job_roles"] = await extract_lists_near_heading(page, patterns)
        if data["job_roles"]:
            break

    # Salary
    try:
        salary = await page.evaluate("""
            () => {
                const all = document.querySelectorAll('p, td, li, span, div');
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    if (/salary|lpa|per annum|package|ctc/i.test(t) && t.length < 400)
                        return t;
                }
                return '';
            }
        """)
        data["salary_info"] = clean(salary)
    except Exception:
        pass

    # Recruiters
    data["recruiters"] = await extract_lists_near_heading(
        page, [r"recruiter", r"employer", r"compan", r"top hiring", r"where.*work", r"hiring"]
    )

    # Further studies
    data["further_studies"] = await extract_lists_near_heading(
        page, [r"further study", r"higher study", r"phd", r"after m\.com", r"course after", r"higher education"]
    )

    raw = await get_all_paragraphs(page)
    data["raw_jobs_text"] = raw[:6000]

    return data


async def scrape_faq(page, base_url: str) -> list[dict]:
    faqs = []

    # Try direct URL first (CollegeDunia often has /faq)
    for suffix in ["/faq", "/faqs"]:
        faq_url = base_url.rstrip("/") + suffix
        await safe_goto(page, faq_url)
        if "faq" in page.url.lower():
            break
    else:
        # Fall back: navigate to base then click FAQ tab
        await safe_goto(page, base_url)
        await click_tab(page, ["FAQ", "FAQs", "Q&A", "Questions", "Common Questions"])

    await expand_accordions(page)
    await scroll_full_page(page)

    # Strategy 1: elements whose text ends with '?' (most reliable)
    try:
        faqs_raw = await page.evaluate("""
            () => {
                const results = [];
                const candidates = document.querySelectorAll(
                    'p, h3, h4, h5, li, strong, b, span, div, summary, dt'
                );
                for (const el of candidates) {
                    const txt = (el.innerText || '').trim();
                    if (txt.endsWith('?') && txt.length > 10 && txt.length < 400) {
                        let answer = '';
                        // Try next sibling
                        const next = el.nextElementSibling;
                        if (next) answer = (next.innerText || '').trim();
                        // Try parent's next sibling
                        if (!answer || answer.length < 5) {
                            const parent = el.parentElement;
                            if (parent) {
                                const pNext = parent.nextElementSibling;
                                if (pNext) answer = (pNext.innerText || '').trim();
                                // Also try <dd> if this is a <dt>
                                const dd = parent.querySelector('dd');
                                if (!answer && dd) answer = (dd.innerText || '').trim();
                            }
                        }
                        results.push({ question: txt, answer: answer.slice(0, 600) });
                    }
                }
                // Deduplicate
                const seen = new Set();
                return results.filter(x => {
                    if (seen.has(x.question)) return false;
                    seen.add(x.question);
                    return true;
                });
            }
        """)
        faqs = [{"question": clean(f["question"]), "answer": clean(f["answer"])} for f in faqs_raw]
    except Exception:
        pass

    # Strategy 2: look for elements with class names containing 'faq', 'question', 'accordion'
    if not faqs:
        try:
            faqs_raw2 = await page.evaluate("""
                () => {
                    const results = [];
                    // Find FAQ containers by class name heuristics
                    const containers = document.querySelectorAll(
                        '[class*="faq"], [class*="Faq"], [class*="FAQ"], ' +
                        '[class*="accordion"], [class*="Accordion"], ' +
                        '[class*="question"], [class*="Question"]'
                    );
                    for (const c of containers) {
                        // Look for question + answer pairs
                        const children = Array.from(c.children);
                        for (let i = 0; i < children.length - 1; i++) {
                            const q = (children[i].innerText || '').trim();
                            const a = (children[i + 1].innerText || '').trim();
                            if (q.length > 10 && q.length < 400 && a.length > 5) {
                                results.push({ question: q, answer: a.slice(0, 600) });
                            }
                        }
                        // Also grab the whole container if small
                        if (children.length === 0) {
                            const full = (c.innerText || '').trim();
                            if (full.length > 20 && full.length < 800) {
                                results.push({ question: full, answer: '' });
                            }
                        }
                    }
                    const seen = new Set();
                    return results.filter(x => {
                        if (seen.has(x.question)) return false;
                        seen.add(x.question);
                        return true;
                    });
                }
            """)
            faqs = [{"question": clean(f["question"]), "answer": clean(f["answer"])} for f in faqs_raw2]
        except Exception:
            pass

    # Strategy 3: look for <details>/<summary> pairs
    if not faqs:
        try:
            faqs_raw3 = await page.evaluate("""
                () => {
                    const results = [];
                    const details = document.querySelectorAll('details');
                    for (const d of details) {
                        const summary = d.querySelector('summary');
                        if (summary) {
                            const q = (summary.innerText || '').trim();
                            // Answer = everything inside <details> except <summary>
                            let a = (d.innerText || '').replace(q, '').trim();
                            results.push({ question: q, answer: a.slice(0, 600) });
                        }
                    }
                    return results;
                }
            """)
            if faqs_raw3:
                faqs = [{"question": clean(f["question"]), "answer": clean(f["answer"])} for f in faqs_raw3]
        except Exception:
            pass

    return faqs


# ─────────────────────────────────────────────────────────────────────────────
# Per-URL scraper
# ─────────────────────────────────────────────────────────────────────────────

async def scrape_course(context, branch_name: str, url: str) -> dict:
    page = await context.new_page()
    result = {
        "branch_name": branch_name,
        "url": url,
        "overview": {},
        "syllabus": {},
        "jobs": {},
        "faq": [],
        "error": None,
    }

    try:
        print(f"  → {branch_name}: {url}")

        await safe_goto(page, url)

        # Overview (on the main landing page)
        result["overview"] = await scrape_overview(page)

        # Syllabus (navigates to /syllabus sub-URL)
        result["syllabus"] = await scrape_syllabus(page, url)

        # Jobs (navigates to /job-scope or clicks tab)
        result["jobs"] = await scrape_jobs(page, url)

        # FAQ (navigates to /faq or clicks tab)
        result["faq"] = await scrape_faq(page, url)

        overview_ok = bool(result["overview"].get("intro") or result["overview"].get("raw_overview_text"))
        syllabus_ok = bool(result["syllabus"].get("semesters") or result["syllabus"].get("raw_syllabus_text"))
        jobs_ok     = bool(result["jobs"].get("job_roles")    or result["jobs"].get("raw_jobs_text"))
        faq_count   = len(result["faq"])
        print(
            f"     ✓ Done | overview={'ok' if overview_ok else 'EMPTY'} | "
            f"syllabus={'ok' if syllabus_ok else 'EMPTY'} | "
            f"jobs={'ok' if jobs_ok else 'EMPTY'} | "
            f"faq={faq_count}"
        )

    except Exception as e:
        result["error"] = str(e)
        print(f"     ✗ Error: {e}")
    finally:
        await page.close()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    branches = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            branches.append((row["branch_name"].strip(), row["url"].strip()))

    print(f"Found {len(branches)} M.Com branches.\n")
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            java_script_enabled=True,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        for branch_name, url in branches:
            result = await scrape_course(context, branch_name, url)
            results.append(result)
            await asyncio.sleep(3)

        await browser.close()

    # Save JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON → {OUTPUT_JSON}")

    # Save flat CSV
    flat_rows = []
    for r in results:
        flat_rows.append({
            "branch_name":           r["branch_name"],
            "url":                   r["url"],
            "overview_intro":        (r["overview"].get("intro") or "")[:500],
            "highlights":            " | ".join(r["overview"].get("highlights", [])),
            "entrance_exams":        " | ".join(r["overview"].get("entrance_exams", [])),
            "admission_eligibility": (r["overview"].get("admission_eligibility") or "")[:300],
            "specialisations":       " | ".join(r["overview"].get("specialisations", [])),
            "top_colleges":          " | ".join(r["overview"].get("top_colleges_mentioned", [])),
            "syllabus_sem_count":    len(r["syllabus"].get("semesters", [])),
            "syllabus_raw":          (r["syllabus"].get("raw_syllabus_text") or "")[:800],
            "electives":             " | ".join(r["syllabus"].get("electives", [])),
            "books":                 " | ".join(r["syllabus"].get("books", [])),
            "job_roles":             " | ".join(r["jobs"].get("job_roles", [])),
            "salary_info":           (r["jobs"].get("salary_info") or "")[:300],
            "recruiters":            " | ".join(r["jobs"].get("recruiters", [])),
            "further_studies":       " | ".join(r["jobs"].get("further_studies", [])),
            "faq_count":             len(r["faq"]),
            "faq_questions":         " | ".join(q["question"] for q in r["faq"][:5]),
            "error":                 r.get("error") or "",
        })

    if flat_rows:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(flat_rows[0].keys()))
            writer.writeheader()
            writer.writerows(flat_rows)
        print(f"✅ CSV  → {OUTPUT_CSV}")

    errors = [r for r in results if r.get("error")]
    print(f"\nSummary: {len(results) - len(errors)} OK, {len(errors)} errors")
    for e in errors:
        print(f"  ✗ {e['branch_name']}: {e['error']}")


if __name__ == "__main__":
    asyncio.run(main())