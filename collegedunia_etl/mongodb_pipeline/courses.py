import asyncio
import importlib.util
import os
import sys
import time
import traceback
import pandas as pd
from pymongo import MongoClient
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

# ----------------- MongoDB Setup ----------------- #
MONGO_URI_LOCAL = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017/")
DB_NAME = "collegedunia"
COLLECTION_NAME = "Course"
LOG_COLLECTION_NAME = "scraper_logs"

client = MongoClient(MONGO_URI_LOCAL)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
log_collection = db[LOG_COLLECTION_NAME]

# ----------------- Locate original scraper files ----------------- #
# These six files are imported EXACTLY as uploaded, with zero edits.
# courses.py only adapts how they're CALLED, never their internal code.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SCRAPER_FILENAMES = {
    "bcom":        "scrape_bcom.py",
    "mcom":        "scrape_mcom.py",
    "msc":         "all_msc_scraper.py",
    "polytechnic": "faq_Polytechnic.py",
    "syllabus":    "collegedunia_Syllabus_scraper_.py",
    "general":     "collegedunia_full_scraper.py",
}


def _load_module(module_key: str, filename: str):
    """
    Import one of the original files by file path, without renaming or
    editing it. Searches courses.py's own folder first, then falls back to
    the project's collegedunia_etl/courses/sections folder (the actual home
    of these scraper files), regardless of where courses.py itself lives.
    """
    candidate_paths = [
        os.path.join(BASE_DIR, filename),
        os.path.join(BASE_DIR, "course", "sections", filename),
        os.path.join(BASE_DIR, "courses", "sections", filename),
        os.path.join(BASE_DIR, "scraped_data", filename),
        os.path.join(BASE_DIR, "..", "collegedunia_etl", "courses", "sections", filename),
        os.path.join(BASE_DIR, "..", "courses", "sections", filename),
    ]
    candidate_paths = [os.path.normpath(p) for p in candidate_paths]
    file_path = next((p for p in candidate_paths if os.path.exists(p)), None)
    if not file_path:
        print(f"  [WARN] Could not find {filename} for '{module_key}' — checked: {candidate_paths}")
        return None

    spec = importlib.util.spec_from_file_location(f"collegedunia_{module_key}", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


print("Loading original scraper files (unmodified)...")
bcom_mod        = _load_module("bcom", SCRAPER_FILENAMES["bcom"])
mcom_mod        = _load_module("mcom", SCRAPER_FILENAMES["mcom"])
msc_mod         = _load_module("msc", SCRAPER_FILENAMES["msc"])
polytechnic_mod = _load_module("polytechnic", SCRAPER_FILENAMES["polytechnic"])
syllabus_mod    = _load_module("syllabus", SCRAPER_FILENAMES["syllabus"])
general_mod     = _load_module("general", SCRAPER_FILENAMES["general"])


# ----------------- Category-specific scrape wrappers ----------------- #
# Each wrapper calls the original file's real function with the exact
# signature it already has. No logic inside the original files is touched.

async def _scrape_bcom(name: str, url: str, browser) -> dict:
    # scrape_bcom.scrape_course(context, branch_name, url) -> dict
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
        java_script_enabled=True,
        ignore_https_errors=True,
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    try:
        return await bcom_mod.scrape_course(context, name, url)
    finally:
        await context.close()


async def _scrape_mcom(name: str, url: str, browser) -> dict:
    # scrape_mcom.scrape_course(context, branch_name, url) -> dict
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
        java_script_enabled=True,
        ignore_https_errors=True,
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    try:
        return await mcom_mod.scrape_course(context, name, url)
    finally:
        await context.close()


def _scrape_msc_sync(url: str, branch_name: str) -> dict:
    """
    all_msc_scraper.scrape_course(page, url, branch_name) is SYNC Playwright,
    not async — it cannot share courses.py's async browser. We open a small
    dedicated sync browser here (using the original module's own function,
    untouched) and run it in a worker thread so it can still be awaited.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
        )
        page = context.new_page()
        try:
            return msc_mod.scrape_course(page, url, branch_name)
        finally:
            browser.close()


async def _scrape_msc(name: str, url: str, browser) -> dict:
    return await asyncio.to_thread(_scrape_msc_sync, url, name)


def _scrape_polytechnic_sync(url: str) -> list:
    """
    faq_Polytechnic.extract_faqs_from_page(page) is SYNC Playwright and only
    extracts FAQs (the file has no broader scrape_course function). Same
    sync-in-thread pattern as MSc.
    """
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
        try:
            page.goto(url, timeout=60_000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            return polytechnic_mod.extract_faqs_from_page(page)
        finally:
            browser.close()


async def _scrape_polytechnic(name: str, url: str, browser) -> dict:
    faqs = await asyncio.to_thread(_scrape_polytechnic_sync, url)
    return {"faq": faqs}


async def _scrape_general(name: str, url: str, browser) -> dict:
    """
    collegedunia_full_scraper.py has no standalone per-URL function — its
    logic lives inline inside main(), scraping the module-level URL constant.
    Rather than edit that file, we temporarily point its URL constant at the
    course we're currently processing, call its main(), then read back the
    JSON file it writes (collegedunia_course_overview_data.json) — exactly
    the same behavior as running the script directly.
    """
    general_mod.URL = url
    out_file = "collegedunia_course_overview_data.json"

    await general_mod.main()

    import json
    if os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    return {"error": "general_course_section produced no output file"}


# ----------------- Category -> CSV + scraper mapping ----------------- #
CATEGORY_CONFIG = {
    "bcom":        {"csv": "bcom_branch_url.csv",        "scraper": _scrape_bcom},
    "mcom":        {"csv": "mcom_branch_url.csv",        "scraper": _scrape_mcom},
    "msc":         {"csv": "msc_branch_url.csv",         "scraper": _scrape_msc},
    "polytechnic": {"csv": "polytechnic_branch_url.csv", "scraper": _scrape_polytechnic},
}

DEFAULT_SCRAPER = _scrape_general  # used for any category without a dedicated CSV/scraper


# ----------------- Scrape Single Course ----------------- #
async def scrape_course(name: str, url: str, category: str, browser) -> dict:
    start_time = time.time()
    course_data = {
        "Course Name": name,
        "Course URL": url,
        "Category": category,
    }

    scraper_fn = CATEGORY_CONFIG.get(category, {}).get("scraper", DEFAULT_SCRAPER)

    try:
        print(f"  [{category}] scraping -> {url}")
        result = await scraper_fn(name, url, browser)
        if result:
            course_data.update(result)
        else:
            print(f"  [{category}] returned empty/None for {url}")
    except Exception as e:
        print(f"  [{category}] FAILED for {url}: {e}")
        traceback.print_exc()

    elapsed = time.time() - start_time
    print(f"Finished '{name}' ({category}) in {elapsed:.2f}s")
    return course_data


# ----------------- Load all category CSVs ----------------- #
def load_all_course_urls() -> list:
    candidate_dirs = [
        BASE_DIR,
        os.path.join(BASE_DIR, "scraped_data"),
        os.path.join(BASE_DIR, "course", "sections", "scraped_data"),
        os.path.join(BASE_DIR, "courses", "sections"),
        os.path.join(BASE_DIR, "..", "collegedunia_etl", "courses", "sections"),
        os.path.join(BASE_DIR, "..", "courses", "sections"),
    ]
    candidate_dirs = [os.path.normpath(d) for d in candidate_dirs]

    all_rows = []

    for category, cfg in CATEGORY_CONFIG.items():
        csv_name = cfg["csv"]
        found_path = next(
            (os.path.join(d, csv_name) for d in candidate_dirs
             if os.path.exists(os.path.join(d, csv_name))),
            None,
        )
        if not found_path:
            print(f"  [{category}] CSV not found ({csv_name}) - skipping.")
            continue

        df = pd.read_csv(found_path)
        if df.empty:
            print(f"  [{category}] CSV is empty: {found_path} - skipping.")
            continue

        if {"branch_name", "url"}.issubset(df.columns):
            names = df["branch_name"].tolist()
            urls = df["url"].tolist()
        else:
            names = df.iloc[:, 0].tolist()
            urls = df.iloc[:, 1].tolist()

        for name, url in zip(names, urls):
            all_rows.append((str(name).strip(), str(url).strip().rstrip("/"), category))

        print(f"  [{category}] loaded {len(names)} rows from {found_path}")

    # Optional generic CSV for any course not in a category file above —
    # routed through collegedunia_full_scraper.py's 7-section scrape.
    general_csv = next(
        (os.path.join(d, "course_urls.csv") for d in candidate_dirs
         if os.path.exists(os.path.join(d, "course_urls.csv"))),
        None,
    )
    if general_csv:
        df = pd.read_csv(general_csv)
        if not df.empty:
            if {"course_name", "course_url"}.issubset(df.columns):
                names = df["course_name"].tolist()
                urls = df["course_url"].tolist()
            else:
                names = df.iloc[:, 0].tolist()
                urls = df.iloc[:, 1].tolist()
            for name, url in zip(names, urls):
                all_rows.append((str(name).strip(), str(url).strip().rstrip("/"), "general"))
            print(f"  [general] loaded {len(names)} rows from {general_csv}")

    return all_rows


# ----------------- Main Runner ----------------- #
async def main():
    print("=" * 60)
    print("Starting CollegeDunia Courses Scraper (all categories)")
    print("=" * 60)

    all_rows = load_all_course_urls()

    if not all_rows:
        print("No course URLs found in any category CSV. Exiting.")
        return

    total_urls = len(all_rows)
    success_count = 0
    failure_count = 0
    failed_entries = []

    print(f"\nTotal courses/branches to scrape across all categories: {total_urls}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        for idx, (name, url, category) in enumerate(all_rows, start=1):
            try:
                print(f"[{idx}/{total_urls}] ({category}) {name} -> {url}")
                data = await scrape_course(name, url, category, browser)

                collection.update_one(
                    {"Course URL": url},
                    {"$set": data},
                    upsert=True,
                )
                success_count += 1
                print(f"  Saved '{name}' to MongoDB ({success_count}/{total_urls})")

            except Exception as e:
                failure_count += 1
                failed_entries.append({
                    "name": name,
                    "url": url,
                    "category": category,
                    "error": str(e),
                })
                print(f"  Failed '{name}': {e}")
                traceback.print_exc()

            await asyncio.sleep(2)

        await browser.close()

    log_entry = {
        "scraper": "collegedunia_courses_all_categories",
        "run_start": pd.Timestamp.utcnow(),
        "total_urls": total_urls,
        "success_count": success_count,
        "failure_count": failure_count,
        "failed_urls": failed_entries,
    }
    log_collection.insert_one(log_entry)
    print("\nRun log saved to MongoDB scraper_logs collection.")

    if failed_entries:
        failed_path = os.path.join(BASE_DIR, "failed_course_urls.txt")
        with open(failed_path, "w", encoding="utf-8") as f:
            for entry in failed_entries:
                f.write(f"[{entry['category']}] {entry['name']} -> {entry['url']} | {entry['error']}\n")
        print(f"Failed URLs written to: {failed_path}")

    print("=" * 60)
    print(f"Scraping complete. Success: {success_count} | Failed: {failure_count} | Total: {total_urls}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())