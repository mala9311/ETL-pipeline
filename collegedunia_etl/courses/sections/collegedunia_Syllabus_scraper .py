import json
import os
import asyncio
import re
import sys
import pandas as pd
from tqdm import tqdm
from playwright.async_api import async_playwright

# Collegedunia base URL for BTech CSE listing page
COLLEGEDUNIA_BASE_URL = (
    "https://collegedunia.com/courses/"
    "bachelor-of-technology-btech-computer-science-and-engineering"
)

MAX_CONCURRENT = 3


def _clean(text: str) -> str:
    """Collapse all whitespace/newlines into a single space."""
    return re.sub(r"\s+", " ", text).strip()


def build_syllabus_url(course_url: str) -> str:
    """Append /syllabus to a college URL, stripping query strings/fragments."""
    course_url = course_url.split("?")[0].split("#")[0].rstrip("/")
    if not course_url.endswith("/syllabus"):
        course_url += "/syllabus"
    return course_url


# ========== Scraper for a single syllabus page ==========
async def scrape_syllabus_section(url: str, browser=None) -> dict:
    owns_browser = browser is None

    async def _do_scrape(browser_obj):
        context = await browser_obj.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            # Pretend to be a real desktop browser
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )
        page = await context.new_page()

        # Block images/fonts to speed up loading
        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", 
                         lambda route: route.abort())

        try:
            print(f"  Opening: {url}")
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")

            # KEY FIX: Collegedunia is heavily JS-rendered.
            # We must wait + scroll to trigger all content to load.
            await asyncio.sleep(3)  # initial JS render wait

            # Scroll down slowly to trigger lazy loading
            for _ in range(10):
                await page.evaluate("window.scrollBy(0, 600)")
                await asyncio.sleep(0.4)

            # Scroll back to top
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(2)  # final settle wait

            # Wait for a table OR heading to appear (real content signal)
            try:
                await page.wait_for_selector("table, h2, h3", timeout=10000)
            except Exception:
                pass

            # ── Find the main content container ──────────────────────────
            # We check each selector and pick the first one that actually
            # contains tables or headings (real syllabus data).
            MAIN_SELECTORS = [
                "div[class*='syllabus']",
                "div[class*='Syllabus']",
                "div[class*='course-detail']",
                "div[class*='courseDetail']",
                "div[class*='content']",
                "main article",
                "main",
                "#main-content",
                "div[class*='container']",
                "body",
            ]

            main_el = None
            used_selector = "body"
            for sel in MAIN_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        has_table   = await el.query_selector("table")
                        has_heading = await el.query_selector("h2, h3")
                        if has_table or has_heading:
                            main_el = el
                            used_selector = sel
                            break
                except Exception:
                    continue

            if main_el is None:
                main_el = await page.query_selector("body")
                used_selector = "body (fallback)"

            print(f"  Selector: '{used_selector}'")

            syllabus = {
                "url": url,
                "headings": [],
                "semester_tables": [],
                "subject_lists": [],
                "paragraphs": [],
            }

            # ── 1. Extract headings ───────────────────────────────────────
            for tag in ["h1", "h2", "h3", "h4"]:
                els = await main_el.query_selector_all(tag)
                for el in els:
                    text = _clean(await el.inner_text())
                    if text and text not in syllabus["headings"]:
                        syllabus["headings"].append(text)

            # ── 2. Extract tables (semester subject grids) ────────────────
            tables = await main_el.query_selector_all("table")
            for table in tables:
                rows_data = []
                rows = await table.query_selector_all("tr")
                for row in rows:
                    cells = await row.query_selector_all("th, td")
                    cell_texts = [_clean(await c.inner_text()) for c in cells]
                    if any(t for t in cell_texts if t):
                        rows_data.append(cell_texts)

                if rows_data:
                    # Find the nearest heading before this table
                    title = ""
                    try:
                        title_el = await table.evaluate_handle(
                            """el => {
                                let sib = el.previousElementSibling;
                                while (sib) {
                                    if (/^H[1-4]$/.test(sib.tagName)) return sib;
                                    sib = sib.previousElementSibling;
                                }
                                // Try parent's previous sibling
                                let parent = el.parentElement;
                                if (parent) {
                                    let psib = parent.previousElementSibling;
                                    while (psib) {
                                        if (/^H[1-4]$/.test(psib.tagName)) return psib;
                                        psib = psib.previousElementSibling;
                                    }
                                }
                                return null;
                            }"""
                        )
                        if title_el:
                            title = _clean(await title_el.inner_text())
                    except Exception:
                        pass

                    syllabus["semester_tables"].append({
                        "title": title,
                        "rows": rows_data,
                    })

            # ── 3. Extract subject lists ──────────────────────────────────
            for list_tag in ["ul", "ol"]:
                lists = await main_el.query_selector_all(list_tag)
                for lst in lists:
                    items_els = await lst.query_selector_all("li")
                    subjects = []
                    for item in items_els:
                        text = _clean(await item.inner_text())
                        if text:
                            subjects.append(text)
                    if subjects:
                        title = ""
                        try:
                            title_el = await lst.evaluate_handle(
                                """el => {
                                    let sib = el.previousElementSibling;
                                    while (sib) {
                                        if (/^H[1-4]$/.test(sib.tagName)) return sib;
                                        sib = sib.previousElementSibling;
                                    }
                                    let parent = el.parentElement;
                                    if (parent) {
                                        let psib = parent.previousElementSibling;
                                        while (psib) {
                                            if (/^H[1-4]$/.test(psib.tagName)) return psib;
                                            psib = psib.previousElementSibling;
                                        }
                                    }
                                    return null;
                                }"""
                            )
                            if title_el:
                                title = _clean(await title_el.inner_text())
                        except Exception:
                            pass

                        syllabus["subject_lists"].append({
                            "title": title,
                            "subjects": subjects,
                        })

            # ── 4. Extract paragraphs ─────────────────────────────────────
            paras = await main_el.query_selector_all("p")
            for p in paras:
                text = _clean(await p.inner_text())
                if text and len(text) > 20:
                    syllabus["paragraphs"].append(text)

            # ── Summary ───────────────────────────────────────────────────
            total = (len(syllabus["headings"]) + len(syllabus["semester_tables"]) +
                     len(syllabus["subject_lists"]) + len(syllabus["paragraphs"]))

            if total == 0:
                print(f"  ⚠️  WARNING: No content extracted from {url}")
                print(f"     Page may be blocked or layout changed.")
            else:
                print(
                    f"  ✅ {len(syllabus['headings'])} headings | "
                    f"{len(syllabus['semester_tables'])} tables | "
                    f"{len(syllabus['subject_lists'])} lists | "
                    f"{len(syllabus['paragraphs'])} paragraphs"
                )

            return syllabus

        finally:
            await context.close()

    async with async_playwright() as p:
        if owns_browser:
            b = await p.chromium.launch(headless=True)
            try:
                result = await _do_scrape(b)
            finally:
                await b.close()
            return result
        else:
            return await _do_scrape(browser)


# ========== Scrape college listing URLs ==========
async def scrape_course_urls(base_url: str = COLLEGEDUNIA_BASE_URL) -> pd.DataFrame:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )
        page = await context.new_page()
        print(f"  Scraping listing page: {base_url}")
        await page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Scroll to load all college cards
        for _ in range(8):
            await page.evaluate("window.scrollBy(0, 1200)")
            await asyncio.sleep(0.6)
        await asyncio.sleep(2)

        links = await page.eval_on_selector_all(
            "a[href*='/college/']",
            "els => els.map(el => ({ name: el.innerText.trim(), href: el.href }))",
        )
        await browser.close()

    seen = set()
    rows = []
    for link in links:
        href = link.get("href", "").split("?")[0].split("#")[0]
        name = re.sub(r"\s+", " ", link.get("name", "")).strip()
        if href and href not in seen and name:
            seen.add(href)
            rows.append({
                "name": name,
                "url": href,
                "syllabus": build_syllabus_url(href),
            })

    df = pd.DataFrame(rows)
    print(f"  Found {len(df)} unique college URLs.")
    return df


# ========== Single page scrape with semaphore + retry ==========
async def _scrape_one(url: str, browser, semaphore: asyncio.Semaphore) -> dict:
    max_retries = 2
    for attempt in range(1, max_retries + 1):
        async with semaphore:
            try:
                return await scrape_syllabus_section(url, browser=browser)
            except Exception as exc:
                if attempt == max_retries:
                    print(f"  FAILED {url}: {exc}")
                    return {"url": url, "error": str(exc)}
                print(f"  Retry {attempt}/{max_retries} for {url}: {exc}")
                await asyncio.sleep(2 * attempt)


# ========== Bulk scrape all colleges ==========
async def collect_all_syllabus_sections(
    courses_df: pd.DataFrame, limit: int = None
) -> list:
    urls = courses_df["syllabus"].tolist()
    if limit:
        urls = urls[:limit]

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            tasks = [_scrape_one(url, browser, semaphore) for url in urls]
            for coro in tqdm(
                asyncio.as_completed(tasks),
                total=len(tasks),
                desc="Scraping syllabus",
            ):
                result = await coro
                results.append(result)
        finally:
            await browser.close()

    return results


# ========== Main entry point ==========
async def scrape_syllabus_section_main(
    url: str = None,
    context=None,
    limit: int = None,
):
    if url:
        try:
            return await scrape_syllabus_section(url)
        except Exception as exc:
            return {"url": url, "error": str(exc)}

    print("Starting Collegedunia full syllabus scrape...")
    os.makedirs("scraped_data", exist_ok=True)
    csv_path = "scraped_data/collegedunia_course_urls.csv"

    if os.path.exists(csv_path):
        courses_df = pd.read_csv(csv_path)
        print(f"  Loaded {len(courses_df)} college URLs from CSV.")
    else:
        print("  CSV not found — scraping listing page first...")
        courses_df = await scrape_course_urls()
        courses_df.to_csv(csv_path, index=False)
        print(f"  Saved {len(courses_df)} college URLs to {csv_path}")

    if "syllabus" not in courses_df.columns:
        courses_df["syllabus"] = courses_df["url"].apply(build_syllabus_url)

    results = await collect_all_syllabus_sections(courses_df, limit=limit)

    output_path = "scraped_data/collegedunia_course_syllabus_section.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n  Saved {len(results)} college syllabuses → {output_path}")
    return results


# ========== Script entry point ==========
if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # ── CHOOSE ONE MODE ─────────────────────────────────────────────────

    # ✅ Option A: Scrape ALL colleges (full run)
    asyncio.run(scrape_syllabus_section_main())

    # 🧪 Option B: Test with first 5 colleges only (run this first!)
    # asyncio.run(scrape_syllabus_section_main(limit=5))

    # 🔍 Option C: Test single URL only
    # asyncio.run(scrape_syllabus_section_main(url=(
    #     "https://collegedunia.com/courses/"
    #     "bachelor-of-technology-btech-computer-science-and-engineering/syllabus"
    # )))