
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
        # return []
        return None  # FIX: return None instead of [] so callers can do a simple truthiness check

    headers = rows[0].split("\t")
    data = []

    for row in rows[1:]:
        cols = row.split("\t")
        cols += [""] * (len(headers) - len(cols))
        data.append(dict(zip(headers, cols)))

    return {"Headers": headers, "Rows": data}

# -----------------------------------------
# Check if paragraph is admission-related
# -----------------------------------------
def is_admission_paragraph(text):
    keywords = [
        "admission", "eligibility", "criteria", "process",
        "application", "selection", "entrance", "exam",
        "jee", "gate", "how to apply", "requirements"
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)

# -----------------------------------------
# Scrape Admission Page into structured JSON
# -----------------------------------------
async def scrape_admission(page, url):
    print("Loading admission page...")
    await page.goto(url, wait_until="networkidle", timeout=120000)

    # Scroll to load lazy content
    for _ in range(40):
        await page.mouse.wheel(0, 1200)
        await asyncio.sleep(0.3)

    # Get college name
    college_name = (await page.locator("h1").first.inner_text()).strip()

    # Get rendered HTML
    html = await page.content()

    # Create clean DOM context
    temp_context = await page.context.browser.new_context()
    temp = await temp_context.new_page()
    # await temp.set_content(html)
    await temp.set_content(html, timeout=60000, wait_until="domcontentloaded")

    elements = await temp.locator("h2, h3, table, p").all()
    print("Total elements found:", len(elements))

    details = []
    last_subheading = None

    for el in elements:
        tag = (await el.evaluate("e => e.tagName")).lower()

        # -------- Headings --------
        if tag in ["h2", "h3"]:
            heading_text = (await el.inner_text()).strip()
            last_subheading = heading_text
            continue

        # -------- Important Paragraphs --------
        elif tag == "p":
            text = (await el.inner_text()).strip()
            if text and is_admission_paragraph(text):
                details.append({
                    "Type": "Paragraph",
                    "Title": last_subheading if last_subheading else "Admission Info",
                    "Content": text
                })

        # -------- Tables --------
        elif tag == "table":
            rows = await el.locator("tr").all_inner_texts()
            table_data = parse_table(rows)

            # FIX: table_data is now None (empty) or a dict — safe to check with `not`
            if not table_data or not table_data["Rows"]:
                continue

            details.append({
                "Type": "Table",
                "Title": last_subheading if last_subheading else "Admission Table",
                "Table": table_data
            })

    await temp_context.close()
    return {
        "College": college_name,
        "AdmissionDetails": details
    }

# -----------------------------------------
# Main Runner
# -----------------------------------------
async def main():
    async with async_playwright() as p:
        start = time.time()
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = "https://collegedunia.com/university/25703-iit-bombay-indian-institute-of-technology-iitb-mumbai/admission"
        result = await scrape_admission(page, url)

        os.makedirs("output", exist_ok=True)
        with open("output/iitb_admission.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        await browser.close()
        print("JSON saved: output/iitb_admission.json")
        print("Time taken:", round(time.time() - start, 2), "seconds")

if __name__ == "__main__":
    asyncio.run(main())
