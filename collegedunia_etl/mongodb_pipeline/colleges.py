import os
import re
import time
import asyncio
import traceback
import pandas as pd
from playwright.async_api import async_playwright
from pymongo import MongoClient

# ----------------- SECTION IMPORTS ----------------- #
from collegedunia_etl.college.sections.admission_section import scrape_admission
from collegedunia_etl.college.sections.placement_section import placement_section
from collegedunia_etl.college.sections.courseandfee import scrape_courses_fees
from collegedunia_etl.college.sections.cutoff_section_script import scrape_cutoff
from collegedunia_etl.college.sections.info_section_university import info_section
from collegedunia_etl.college.sections.qacollegedunia import qa
from collegedunia_etl.college.sections.Scholarship_section import scrape_scholarship
from collegedunia_etl.utils.logger import get_logger

# ----------------- PATHS ----------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COLLEGE_CSV = os.path.join(BASE_DIR, "Collegedunia-colleges.csv")
FAILED_URLS_PATH = os.path.join(BASE_DIR, "failed_urls.txt")

# ----------------- CONFIG ----------------- #
CONCURRENT_COLLEGES = 6     # Number of colleges scraped in parallel
SECTION_SLEEP = 0.5         # Reduced from 3s — enough for JS to settle
SECTION_COOLDOWN = 0.5      # Reduced from 4s between sections
COLLEGE_COOLDOWN = 2         # Reduced from 8s between colleges
PAGE_TIMEOUT = 30_000        # 60s per page (was 120s — most pages load faster)


# ----------------- SECTION REGISTRY ----------------- #
SECTIONS = {
    "Admission": {
        "func": scrape_admission,
        "path": "/admission"
    },
    "Placement": {
        "func": placement_section,
        "path": "/placement"
    },
    "Courses & Fees": {
        "func": scrape_courses_fees,
        "path": "/courses-fees"
    },
    "Cutoff": {
        "func": scrape_cutoff,
        "path": "/cutoff"
    },
    "Info": {
        "func": info_section,
        "path": ""
    },
    "Q&A": {
        "func": qa,
        "path": None
    },
    "Scholarship": {
        "func": scrape_scholarship,
        "path": "/scholarship"
    }
}

# ----------------- LOGGER ----------------- #
logger = get_logger("collegedunia_colleges")

# ----------------- MONGODB ----------------- #
DB_NAME = "collegedunia"
COLLECTION_NAME = "College"
LOG_COLLECTION_NAME = "scraper_logs"

MONGO_URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
log_collection = db[LOG_COLLECTION_NAME]

# -------------------------------------------------- #
#            EXTRACT COLLEGE ID FROM URL             #
# -------------------------------------------------- #
def extract_college_id(url: str) -> str | None:
    """
    Handles both URL formats:
      - https://collegedunia.com/college/25703-iit-delhi  → 25703
      - https://collegedunia.com/university/25703-iit-delhi → 25703
    """
    match = re.search(r'/(?:college|university)/(\d+)-', url)
    return match.group(1) if match else None

 
# -------------------------------------------------- #
#                COLLEGE SCRAPER                     #
# -------------------------------------------------- #
async def scrape_college(name: str, base_url: str, browser):
    start = time.time()

    college_data = {
        "College Name": name,
        "College URL": base_url
    }

    # Extract college ID once for Q&A URL
    college_id = extract_college_id(base_url)
    if not college_id:
        logger.warning(f"{name} | Could not extract college ID from URL: {base_url} — Q&A will be skipped")

    context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US"
        )
    page = await context.new_page()
    
    try:
        for section, meta in SECTIONS.items():

            # Build correct URL per section
            if section == "Q&A":
                if not college_id:
                    logger.warning(f"{name} | Skipping Q&A — no college ID found")
                    continue
                full_url = f"https://collegedunia.com/qna?college={college_id}"
            else:
                full_url = base_url.rstrip("/") + meta["path"]


            try:
                logger.info(f"{name} | Scraping {section} → {full_url}")

                await page.goto(full_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await asyncio.sleep(SECTION_SLEEP)  

                result = await meta["func"](page, full_url)

                if result:
                    college_data[section] = result
                    logger.info(f"{name} | {section} ✓")
                else:
                    logger.warning(f"{name} | {section} returned empty result")

            except Exception:
                logger.error(f"{name} | {section} FAILED")
                logger.error(traceback.format_exc())

            # cooldown between sections
            await asyncio.sleep(SECTION_COOLDOWN)

    finally:
        # Always clean up — even if something crashes
        await page.close()
        await context.close()

    logger.info(
        f"Completed {name} in {round(time.time() - start, 2)} sec"
    )

    return college_data

# -------------------------------------------------- #
#                     MAIN                           #
# -------------------------------------------------- #
async def main():
    run_start = pd.Timestamp.utcnow()
    start = time.time()

    success = 0
    failure = 0
    failed_urls = []

    df = pd.read_csv(COLLEGE_CSV)
    names = df.iloc[:, 0].tolist()
    urls = df.iloc[:, 1].tolist()

    logger.info(f"Starting Collegedunia scraping... Total colleges: {len(urls)}")
    logger.info(f"Concurrent workers: {CONCURRENT_COLLEGES}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,     # IMPORTANT: avoid 403
            slow_mo=0,
            args=[
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",  # avoid bot detection
        "--disable-dev-shm-usage",
        ]
        )
   
    
         # ---- One-time warm-up to set cookies/session ----
        logger.info("Warming up browser session...")
        warm_ctx = await browser.new_context()
        warm = await warm_ctx.new_page()
        await warm.goto("https://collegedunia.com", timeout=60000)
        await asyncio.sleep(5)
        await warm.close()
        await warm_ctx.close()
        logger.info("Warm-up done ✓")

        # Semaphore enforces CONCURRENT_COLLEGES limit
        semaphore = asyncio.Semaphore(CONCURRENT_COLLEGES)

        async def scrape_with_semaphore(name: str, url: str):
            async with semaphore:
                try:
                    logger.info(f"━━━ Scraping: {name} ━━━")

                    data = await scrape_college(name, url, browser)

                    # Prevent empty 403 saves
                    if len(data.keys()) <= 2:
                        raise Exception("Empty or blocked response (403)")

                    collection.update_one(
                        {"College URL": url},
                        {"$set": data},
                        upsert=True
                    )

                    logger.info(f"Saved to MongoDB ✓ ({name})")
                    return {"status": "success", "name": name, "url": url}

                except Exception as e:
                    logger.error(f"FAILED: {name} — {e}")
                    logger.error(traceback.format_exc())
                    return {"status": "failed", "name": name, "url": url, "error": str(e)}

                finally:
                    # Cooldown inside semaphore so each slot pauses before freeing
                    await asyncio.sleep(COLLEGE_COOLDOWN)

        # Launch all colleges concurrently (semaphore caps active ones at CONCURRENT_COLLEGES)
        tasks = [scrape_with_semaphore(name, url) for name, url in zip(names, urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()

        # ---- Tally results ----
        for result in results:
            if isinstance(result, Exception):
                failure += 1
                failed_urls.append({"college": "unknown", "url": "unknown", "error": str(result)})
            elif result["status"] == "success":
                success += 1
            else:
                failure += 1
                failed_urls.append({
                    "college": result["name"],
                    "url": result["url"],
                    "error": result.get("error", "unknown error")
                })

        
    duration = round(time.time() - start, 2)

    log_collection.insert_one({
        "run_start": run_start,
        "run_end": pd.Timestamp.utcnow(),
        "duration_seconds": duration,
        "total": len(urls),
        "success": success,
        "failure": failure,
        "failed_urls": failed_urls,
        "status": "success" if failure == 0 else "partial"
    })

    if failed_urls:
        with open(FAILED_URLS_PATH, "w", encoding="utf-8") as f:
            for x in failed_urls:
                f.write(f"{x['college']} -> {x['url']} | {x['error']}\n")

    logger.info("=" * 60)
    logger.info("COLLEGEDUNIA SCRAPING SUMMARY")
    logger.info(f"Total: {len(urls)}")
    logger.info(f"Success: {success}")
    logger.info(f"Failed: {failure}")
    logger.info(f"Duration: {duration} sec")
    logger.info("=" * 60)

# -------------------------------------------------- #
if __name__ == "__main__":
    asyncio.run(main())
