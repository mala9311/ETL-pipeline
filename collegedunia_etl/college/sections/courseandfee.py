
import asyncio
from playwright.async_api import async_playwright
import json
import os
import time

# -------------------------------------------------
# Parse table rows into dictionary
# -------------------------------------------------
def parse_table_rows(rows):
    rows = [r.strip() for r in rows if r.strip()]
    if len(rows) < 2:
        return []

    header = rows[0].split("\t")
    data_list = []

    for row in rows[1:]:
        cols = row.split("\t")
        cols += [""] * (len(header) - len(cols))
        data_list.append(dict(zip(header, cols)))

    return data_list

# -------------------------------------------------
# Filter unwanted paragraphs
# -------------------------------------------------
def is_valid_paragraph(text):
    bad_patterns = [
        "updated on", "content writer", "written by",
        "author", "last updated", "published", 
        "collegedunia", "newsletter", "disclaimer"
    ]

    t = text.lower()

    for b in bad_patterns:
        if b in t:
            return False

    if len(text) < 25:
        return False

    return True

# -------------------------------------------------
# Scraper
# -------------------------------------------------
async def scrape_courses_fees(page, url):
    print("Loading page...")
    await page.goto(url, wait_until="domcontentloaded", timeout=120000)

    # Scroll to load dynamic content
    for _ in range(50):
        await page.mouse.wheel(0, 1500)
        await asyncio.sleep(0.25)

    html = await page.content()

    # Parse HTML in temp context
    ctx = await page.context.browser.new_context()
    temp = await ctx.new_page()
    await temp.set_content(html)

    # Collect all content in order
    elements = await temp.locator("h1, h2, h3, p, table").all()

    final_output = {
        "College": "",
        "Details": []
    }

    current_section = None

    for el in elements:
        tag = (await el.evaluate("e => e.tagName")).lower()
        text = (await el.inner_text()).strip()

        # ------------- HEADINGS -------------
        if tag in ["h1", "h2", "h3"]:
            # set College name from the first heading only
            if final_output["College"] == "":
                final_output["College"] = text

            current_section = {
                "Heading": text,
                "Content": []
            }
            final_output["Details"].append(current_section)

        # ------------- PARAGRAPHS -------------
        elif tag == "p":
            if not current_section:
                continue
            if is_valid_paragraph(text):
                current_section["Content"].append({"p": text})

        # ------------- TABLES -------------
        elif tag == "table":
            if not current_section:
                continue

            rows = await el.locator("tr").all_inner_texts()
            parsed_table = parse_table_rows(rows)

            current_section["Content"].append({"Table": parsed_table})

    await ctx.close()
    return final_output

# -------------------------------------------------
# Main
# -------------------------------------------------
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = "https://collegedunia.com/university/25703-iit-bombay-indian-institute-of-technology-iitb-mumbai/courses-fees"

        result = await scrape_courses_fees(page, url)

        os.makedirs("output", exist_ok=True)
        with open("output/iitb_formatted.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        await browser.close()
        print("Saved: output/iitb_formatted.json")

if __name__ == "__main__":
    asyncio.run(main())
