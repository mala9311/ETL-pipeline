import asyncio
from playwright.async_api import async_playwright
import csv

START_URL = "https://collegedunia.com/engineering-colleges"

async def scrape_colleges():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        print("Opening page...")
        await page.goto(START_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        colleges = []
        max_rank = 0
        last_height = 0

        while max_rank < 500:
            rows = await page.query_selector_all("tr")

            for row in rows:
                tds = await row.query_selector_all("td")
                if len(tds) < 2:
                    continue

                rank_text = (await tds[0].inner_text()).strip()
                if rank_text.startswith("#") and rank_text[1:].isdigit():
                    rank = int(rank_text[1:])
                    if rank > 500:
                        continue

                    link_el = await tds[1].query_selector("a")
                    if link_el:
                        # 🔥 FIX IS HERE
                        name = (await link_el.text_content()).strip()
                        href = await link_el.get_attribute("href")

                        if href:
                            href = "https://collegedunia.com" + href

                        entry = [name, href]
                        if entry not in colleges:
                            colleges.append(entry)

                    if rank > max_rank:
                        max_rank = rank

            print(f"Current max CD Rank collected: {max_rank}")

            height = await page.evaluate("document.body.scrollHeight")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            if height == last_height:
                print("No more content loading from server.")
                break
            last_height = height

        with open("colleges_upto_rank_500.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["College Name", "URL"])
            writer.writerows(colleges)

        print(f"\n✔ DONE — Extracted {len(colleges)} colleges up to CD Rank 500")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape_colleges())
