from playwright.async_api import async_playwright
from tqdm import tqdm
import asyncio
import logging
import os
import pandas as pd
from collegedunia_etl.exam.New_Scraper.constants import  (
    BASE_URL,
    EXAM_LISTING_URL,
    OUTPUT_CSV_PATH,
    OUTPUT_DIR,
    PAGE_TIMEOUT,
    EXAM_CARD_SELECTOR,
    EXAM_TITLE_LINK_SELECTOR,
)

logging.basicConfig(
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


async def collect_exam_urls(url: str) -> list[dict]:
    """
    Launch a headless Chromium browser, load the Collegedunia exam
    listing page, and collect every exam title + URL from the cards.

    Returns a list of {"Title": ..., "URL": ...} dicts.
    Duplicate URLs are removed before returning.
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page    = await browser.new_page()

        log.info("Navigating to: %s", url)
        await page.goto(url, timeout=PAGE_TIMEOUT)
        await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        await asyncio.sleep(5)

        log.info("Scrolling page to load all exam cards ...")
        for _ in range(20):
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(1.5)

        exam_cards = await page.query_selector_all(EXAM_CARD_SELECTOR)
        log.info("Exam cards found: %d", len(exam_cards))

        seen_urls = set()
        results   = []
        skipped   = 0
        failed    = 0

        for card in tqdm(exam_cards, desc="Scraping exams", unit="card"):
            try:
                link = await card.query_selector(EXAM_TITLE_LINK_SELECTOR)
                if not link:
                    failed += 1
                    continue

                href  = await link.get_attribute("href")
                title = await link.get_attribute("data-csm-title") or await link.inner_text()

                if not href:
                    failed += 1
                    continue

                full_url = BASE_URL + href if href.startswith("/") else href
                full_url = full_url.split("?")[0].rstrip("/")

                if "/exams/" not in full_url:
                    skipped += 1
                    continue

                if full_url in seen_urls:
                    skipped += 1
                    continue

                seen_urls.add(full_url)
                results.append({
                    "Title": title.strip() if title else None,
                    "URL":   full_url,
                })

            except Exception as e:
                failed += 1
                log.warning("Error: %s", e)

        await browser.close()

        log.info("Total found  : %d", len(exam_cards))
        log.info("Total saved  : %d", len(results))
        log.info("Total skipped: %d", skipped)
        log.info("Total failed : %d", failed)
        return results


async def save_exam_urls_csv():
    log.info("=" * 50)
    log.info("Collegedunia All-Exam Scraper — starting")
    log.info("=" * 50)

    results  = await collect_exam_urls(EXAM_LISTING_URL)
    exams_df = pd.DataFrame(results, columns=["Title", "URL"])

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    exams_df.to_csv(OUTPUT_CSV_PATH, index=False, encoding="utf-8")

    log.info("Saved %d rows → %s", len(exams_df), os.path.abspath(OUTPUT_CSV_PATH))
    log.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(save_exam_urls_csv())