import asyncio
from playwright.async_api import async_playwright
import json
import time
import os

# -----------------------------------------
# Parse table rows into dicts
# -----------------------------------------
def parse_table(rows):
    rows = [r.strip() for r in rows if r.strip()]
    if len(rows) < 2:
        return []

    headers = rows[0].split("\t")
    data = []

    for row in rows[1:]:
        cols = row.split("\t")
        cols += [""] * (len(headers) - len(cols))
        data.append(dict(zip(headers, cols)))

    return {"Headers": headers, "Rows": data}

# -----------------------------------------
# Check if paragraph is important
# -----------------------------------------
def is_cutoff_paragraph(text):
    keywords = [
        "cutoff", "rank", "jee", "admission",
        "opening", "closing", "round", "seat"
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)

# -----------------------------------------
# Scrape Cutoff Page into structured JSON
# -----------------------------------------
async def scrape_cutoff(page, url):
    print("Loading cutoff page...")
    await page.goto(url, wait_until="networkidle", timeout=120000)

    # Scroll to load lazy content
    for _ in range(40):
        await page.mouse.wheel(0, 1200)
        await asyncio.sleep(0.3)

    # Get college name
    college_name = (await page.locator("h1").first.inner_text()).strip()

    # Get rendered HTML
    html = await page.content()

    # New context to parse clean DOM
    temp_context = await page.context.browser.new_context()
    temp = await temp_context.new_page()
    await temp.set_content(html)

    # Select headings, tables, and paragraphs
    elements = await temp.locator("h2, h3, table, p").all()
    print("Total elements found:", len(elements))

    details = []
    last_subheading = None

    for el in elements:
        tag = (await el.evaluate("e => e.tagName")).lower()

        # -------- Headings --------
        if tag in ["h2", "h3"]:
            heading_text = (await el.inner_text()).strip()
            if "cutoff" in heading_text.lower():
                last_subheading = heading_text
            else:
                last_subheading = None
            continue

        # -------- Important Paragraphs --------
        elif tag == "p":
            text = (await el.inner_text()).strip()
            if text and is_cutoff_paragraph(text):
                details.append({
                    "Type": "Paragraph",
                    "Title": last_subheading if last_subheading else "Cutoff Info",
                    "Content": text
                })

        # -------- Tables --------
        elif tag == "table":
            rows = await el.locator("tr").all_inner_texts()
            table_data = parse_table(rows)

            if not table_data["Rows"]:
                continue

            title = last_subheading if last_subheading else "Cutoff Table"
            last_subheading = None

            details.append({
                "Type": "Table",
                "Title": title,
                "Table": table_data
            })

    await temp_context.close()
    return {
        "College": college_name,
        "CutoffDetails": details
    }

# -----------------------------------------
# Main Runner
# -----------------------------------------
async def main():
    async with async_playwright() as p:
        start = time.time()
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = "https://collegedunia.com/university/25703-iit-bombay-indian-institute-of-technology-iitb-mumbai/cutoff"
        result = await scrape_cutoff(page, url)

        os.makedirs("output", exist_ok=True)
        with open("output/iitb_cutoff.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        await browser.close()
        print("JSON saved: output/iitb_cutoff.json")
        print("Time taken:", round(time.time() - start, 2), "seconds")

if __name__ == "__main__":
    asyncio.run(main())
