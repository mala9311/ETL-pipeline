import asyncio
from playwright.async_api import async_playwright
import json
import os
import time


# -----------------------------------------
# Stealth browser context
# -----------------------------------------
async def create_stealth_context(browser):
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="en-US"
    )

    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)

    return context


# -----------------------------------------
# Extract table properly
# -----------------------------------------
async def extract_table(table):
    headers = await table.locator("thead tr th").all_inner_texts()

    # fallback if no thead
    if not headers:
        headers = await table.locator("tr").first.locator("th, td").all_inner_texts()

    headers = [h.strip() for h in headers if h.strip()]

    rows_data = []

    rows = await table.locator("tbody tr").all()

    # fallback if no tbody
    if not rows:
        rows = await table.locator("tr").all()[1:]

    for row in rows:
        cols = await row.locator("td").all_inner_texts()
        cols = [c.strip() for c in cols]

        if not cols:
            continue

        # map headers → values
        row_dict = {}
        for i in range(len(headers)):
            value = cols[i] if i < len(cols) else ""
            row_dict[headers[i]] = value

        rows_data.append(row_dict)

    return {
        "Headers": headers,
        "Rows": rows_data
    }


# -----------------------------------------
# Main scraping function (TABLE ONLY)
# -----------------------------------------
async def scrape_scholarship(page, url):
    print("Opening page...")

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(2)

    # Check block
    title = await page.title()
    if "403" in title.lower():
        return {
            "College": "Blocked (403)",
            "Scholarships": []
        }

    college_name = (await page.locator("h1").first.inner_text()).strip()

    # 🔴 DIRECTLY TARGET TABLE
    tables = await page.locator("table").all()

    if not tables:
        return {
            "College": college_name,
            "Scholarships": []
        }

    # assume first relevant table
    table_data = await extract_table(tables[0])

    scholarships = []

    for row in table_data["Rows"]:
        name = list(row.values())[0] if row else "Unknown"

        details = []
        for key, value in row.items():
            details.append(f"{key}: {value}")

        scholarships.append({
            "Name": name,
            "Details": details
        })

    return {
        "College": college_name,
        "TotalScholarships": len(scholarships),
        "Scholarships": scholarships
    }


# -----------------------------------------
# Main runner
# -----------------------------------------
async def main():
    async with async_playwright() as p:
        start = time.time()

        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )

        try:
            context = await create_stealth_context(browser)
            page = await context.new_page()

            url = "https://collegedunia.com/university/25881-iit-madras-indian-institute-of-technology-iitm-chennai/scholarship"

            result = await scrape_scholarship(page, url)

            os.makedirs("output", exist_ok=True)

            with open("output/iitm_scholarship.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            print("Saved to output/iitm_scholarship.json")
            print("Total Scholarships:", result["TotalScholarships"])

        except Exception as e:
            print("Error:", str(e))

        finally:
            await browser.close()

        print("Time taken:", round(time.time() - start, 2), "seconds")


# -----------------------------------------
# Run safely
# -----------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped by user")