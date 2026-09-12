"""
collegedunia_syllabus_scraper_v2.py
====================================
Scrapes syllabus data for MULTIPLE courses from Collegedunia.

KEY FIXES over v1:
  1. COURSE_CATALOGUE defines every course slug you want to scrape.
  2. scrape_course_urls() now loops over every course slug and handles
     Collegedunia's "Load More" / pagination pattern.
  3. Syllabus URL builder is smarter — works for any course slug pattern.
  4. Output is tagged per-course so you know which row belongs to which degree.

HOW TO ADD MORE COURSES:
  Just append to COURSE_CATALOGUE.  The key is a human-friendly label;
  the value is the exact Collegedunia /courses/<slug> path segment.
"""

import json
import os
import asyncio
import re
import sys
import pandas as pd
from tqdm import tqdm
from playwright.async_api import async_playwright

# ── ❶  COURSE CATALOGUE ──────────────────────────────────────────────────────
#   key   → human label saved in the output
#   value → the URL slug after https://collegedunia.com/courses/
# Add / remove entries freely.
COURSE_CATALOGUE: dict[str, str] = {
    "BTech CSE":        "bachelor-of-technology-btech-computer-science-and-engineering",
    "BTech ECE":        "bachelor-of-technology-btech-electronics-and-communication-engineering",
    "BTech Mechanical": "bachelor-of-technology-btech-mechanical-engineering",
    "BTech Civil":      "bachelor-of-technology-btech-civil-engineering",
    "MBA":              "master-of-business-administration-mba",
    "MCA":              "master-of-computer-applications-mca",
    "BCA":              "bachelor-of-computer-applications-bca",
    "BSc CS":           "bachelor-of-science-bsc-computer-science",
    "BBA":              "bachelor-of-business-administration-bba",
    "MTech CSE":        "master-of-technology-mtech-computer-science-and-engineering",
    "MBBS":             "bachelor-of-medicine-and-bachelor-of-surgery-mbbs",
    "LLB":              "bachelor-of-legislative-law-llb",
    "BArch":            "bachelor-of-architecture-barch",
    "BPharm":           "bachelor-of-pharmacy-bpharm",
    "BSc Nursing":      "bachelor-of-science-bsc-nursing",
}

COLLEGEDUNIA_COURSES_BASE = "https://collegedunia.com/courses"
MAX_CONCURRENT = 3          # parallel syllabus page scrapes
MAX_LIST_PAGES = 5          # max paginated listing pages per course
SCROLL_ROUNDS   = 10        # scroll rounds on each listing page


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_listing_url(slug: str, page: int = 1) -> str:
    """
    Collegedunia listing pages look like:
      Page 1:  /courses/<slug>
      Page 2:  /courses/<slug>?page=2
    """
    base = f"{COLLEGEDUNIA_COURSES_BASE}/{slug}"
    return base if page == 1 else f"{base}?page={page}"


def build_syllabus_url(college_url: str) -> str:
    """Append /syllabus to a /college/... URL."""
    college_url = college_url.split("?")[0].split("#")[0].rstrip("/")
    # Only append if not already there
    if not college_url.endswith("/syllabus"):
        # Some colleges use /courses/<course>/<college> style — handle both
        college_url += "/syllabus"
    return college_url


def _make_context_kwargs() -> dict:
    return dict(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-IN",
    )


# ── ❷  LISTING SCRAPER (per course, with pagination) ─────────────────────────

async def scrape_course_urls_for_slug(
    course_label: str,
    slug: str,
    browser,
) -> list[dict]:
    """
    Scrape all college links from a Collegedunia course listing page.
    Handles:
      • JS lazy-loading  (scroll-to-reveal)
      • Pagination       (?page=N)
      • "Load More" btn  (clicked if found)
    Returns a list of dicts: {course, name, url, syllabus}
    """
    seen_hrefs: set[str] = set()
    rows: list[dict] = []

    for page_num in range(1, MAX_LIST_PAGES + 1):
        listing_url = build_listing_url(slug, page_num)
        context = await browser.new_context(**_make_context_kwargs())
        pg = await context.new_page()
        await pg.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}",
            lambda route: route.abort(),
        )

        try:
            print(f"  [{course_label}] Listing page {page_num}: {listing_url}")
            await pg.goto(listing_url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Scroll to trigger lazy-loaded cards
            for _ in range(SCROLL_ROUNDS):
                await pg.evaluate("window.scrollBy(0, 1000)")
                await asyncio.sleep(0.5)

            # Click "Load More" button if present
            for btn_sel in [
                "button:has-text('Load More')",
                "a:has-text('Load More')",
                "button:has-text('Show More')",
            ]:
                try:
                    btn = await pg.query_selector(btn_sel)
                    if btn:
                        await btn.click()
                        await asyncio.sleep(2)
                        # Scroll again after loading more
                        for _ in range(5):
                            await pg.evaluate("window.scrollBy(0, 1000)")
                            await asyncio.sleep(0.4)
                        break
                except Exception:
                    pass

            # Grab all /college/ links
            links = await pg.eval_on_selector_all(
                "a[href*='/college/']",
                "els => els.map(el => ({ name: el.innerText.trim(), href: el.href }))",
            )

            # Filter duplicates and noise
            new_found = 0
            for link in links:
                href = link.get("href", "").split("?")[0].split("#")[0].rstrip("/")
                name = _clean(link.get("name", ""))
                if href and href not in seen_hrefs and name and len(name) > 3:
                    seen_hrefs.add(href)
                    rows.append({
                        "course":   course_label,
                        "name":     name,
                        "url":      href,
                        "syllabus": build_syllabus_url(href),
                    })
                    new_found += 1

            print(f"  [{course_label}] Page {page_num}: +{new_found} colleges "
                  f"(total so far: {len(rows)})")

            # Stop early if this page yielded nothing new
            if new_found == 0:
                print(f"  [{course_label}] No new colleges on page {page_num} — stopping.")
                break

        except Exception as exc:
            print(f"  [{course_label}] Error on page {page_num}: {exc}")
            break
        finally:
            await context.close()

    return rows


async def scrape_all_course_urls(
    catalogue: dict[str, str] = COURSE_CATALOGUE,
    csv_path: str = "scraped_data/collegedunia_course_urls.csv",
) -> pd.DataFrame:
    """
    Loop over every course in the catalogue and collect college URLs.
    Returns a combined DataFrame and saves it to CSV.
    """
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        print(f"  Loaded {len(df)} college URLs from {csv_path}")
        return df

    all_rows: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for label, slug in catalogue.items():
                print(f"\n{'='*60}")
                print(f"  Scraping listing for: {label}")
                print(f"{'='*60}")
                rows = await scrape_course_urls_for_slug(label, slug, browser)
                all_rows.extend(rows)
        finally:
            await browser.close()

    df = pd.DataFrame(all_rows)
    df.to_csv(csv_path, index=False)
    print(f"\n  Saved {len(df)} college URLs → {csv_path}")
    return df


# ── ❸  SYLLABUS PAGE SCRAPER (unchanged logic, now reusable) ─────────────────

async def _scrape_syllabus_page(url: str, browser) -> dict:
    context = await browser.new_context(**_make_context_kwargs())
    page = await context.new_page()
    await page.route(
        "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}",
        lambda route: route.abort(),
    )

    try:
        print(f"  Opening: {url}")
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        for _ in range(10):
            await page.evaluate("window.scrollBy(0, 600)")
            await asyncio.sleep(0.4)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(2)

        try:
            await page.wait_for_selector("table, h2, h3", timeout=10000)
        except Exception:
            pass

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
                        main_el      = el
                        used_selector = sel
                        break
            except Exception:
                continue

        if main_el is None:
            main_el = await page.query_selector("body")

        print(f"  Selector used: '{used_selector}'")

        syllabus = {
            "url":              url,
            "headings":         [],
            "semester_tables":  [],
            "subject_lists":    [],
            "paragraphs":       [],
        }

        # Headings
        for tag in ["h1", "h2", "h3", "h4"]:
            for el in await main_el.query_selector_all(tag):
                text = _clean(await el.inner_text())
                if text and text not in syllabus["headings"]:
                    syllabus["headings"].append(text)

        # Tables
        for table in await main_el.query_selector_all("table"):
            rows_data = []
            for row in await table.query_selector_all("tr"):
                cells = await row.query_selector_all("th, td")
                cell_texts = [_clean(await c.inner_text()) for c in cells]
                if any(t for t in cell_texts if t):
                    rows_data.append(cell_texts)

            if rows_data:
                title = ""
                try:
                    title_el = await table.evaluate_handle(
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
                syllabus["semester_tables"].append({"title": title, "rows": rows_data})

        # Lists
        for list_tag in ["ul", "ol"]:
            for lst in await main_el.query_selector_all(list_tag):
                subjects = [
                    _clean(await item.inner_text())
                    for item in await lst.query_selector_all("li")
                ]
                subjects = [s for s in subjects if s]
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
                    syllabus["subject_lists"].append({"title": title, "subjects": subjects})

        # Paragraphs
        for p in await main_el.query_selector_all("p"):
            text = _clean(await p.inner_text())
            if text and len(text) > 20:
                syllabus["paragraphs"].append(text)

        total = (
            len(syllabus["headings"]) + len(syllabus["semester_tables"]) +
            len(syllabus["subject_lists"]) + len(syllabus["paragraphs"])
        )
        if total == 0:
            print(f"  ⚠️  No content extracted — page may be blocked.")
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


# ── ❹  CONCURRENCY WRAPPER ────────────────────────────────────────────────────

async def _scrape_one(
    row: dict,
    browser,
    semaphore: asyncio.Semaphore,
) -> dict:
    url   = row["syllabus"]
    course = row.get("course", "")
    name   = row.get("name", "")

    for attempt in range(1, 3):
        async with semaphore:
            try:
                data = await _scrape_syllabus_page(url, browser)
                data["course_type"]    = course
                data["college_name"]   = name
                data["college_url"]    = row.get("url", "")
                return data
            except Exception as exc:
                if attempt == 2:
                    print(f"  FAILED {url}: {exc}")
                    return {
                        "url": url, "course_type": course,
                        "college_name": name, "error": str(exc),
                    }
                print(f"  Retry {attempt}/2 for {url}: {exc}")
                await asyncio.sleep(2 * attempt)


async def collect_all_syllabus_sections(
    courses_df: pd.DataFrame,
    limit: int = None,
) -> list[dict]:
    records = courses_df.to_dict("records")
    if limit:
        records = records[:limit]

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results   = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            tasks = [_scrape_one(row, browser, semaphore) for row in records]
            for coro in tqdm(
                asyncio.as_completed(tasks),
                total=len(tasks),
                desc="Scraping syllabuses",
            ):
                result = await coro
                results.append(result)
        finally:
            await browser.close()

    return results


# ── ❺  MAIN ENTRY POINT ───────────────────────────────────────────────────────

async def main(
    single_url: str = None,
    limit: int = None,
    catalogue: dict = None,
):
    """
    single_url  → scrape just one syllabus URL (for quick testing)
    limit       → cap total college pages scraped (e.g. 10 for a quick run)
    catalogue   → override COURSE_CATALOGUE (dict of label→slug)
    """
    if single_url:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                return await _scrape_syllabus_page(single_url, browser)
            finally:
                await browser.close()

    cat = catalogue or COURSE_CATALOGUE
    print(f"Starting Collegedunia multi-course scrape ({len(cat)} courses)…\n")
    os.makedirs("scraped_data", exist_ok=True)

    csv_path = "scraped_data/collegedunia_course_urls.csv"
    courses_df = await scrape_all_course_urls(cat, csv_path)

    if "syllabus" not in courses_df.columns:
        courses_df["syllabus"] = courses_df["url"].apply(build_syllabus_url)

    results = await collect_all_syllabus_sections(courses_df, limit=limit)

    out_path = "scraped_data/collegedunia_all_courses_syllabus.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n  Done — {len(results)} records saved → {out_path}")
    return results


# ── Script entry ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # ── CHOOSE ONE MODE ────────────────────────────────────────────────────

    # ✅ Option A: Full run — ALL courses in COURSE_CATALOGUE
    asyncio.run(main())

    # 🧪 Option B: Quick test — first 10 colleges only across all courses
    # asyncio.run(main(limit=10))

    # 🔍 Option C: Single URL test
    # asyncio.run(main(single_url=(
    #     "https://collegedunia.com/college/"
    #     "1234-iit-delhi/btech-computer-science-and-engineering/syllabus"
    # )))

    # 🎯 Option D: Scrape only specific courses (override catalogue)
    # asyncio.run(main(catalogue={
    #     "MBA": "master-of-business-administration-mba",
    #     "MCA": "master-of-computer-applications-mca",
    # }))
