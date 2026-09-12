import asyncio
from playwright.async_api import async_playwright
import json

def parse(table):
    details = []
    head = table[0].split('\t')
    for row in table[1:]:
        vals = row.split('\t')
        details.append(dict(zip(head, vals)))
    return details

async def info_section(page, url):
    data = []
    await page.goto(url)

    # Correct info container for this university page
    container = page.locator('xpath=//div[contains(@class,"college-content")]')

    # find any tables inside
    tables = container.locator('xpath=.//table')
    table_count = await tables.count()

    if table_count > 0:
        for i in range(table_count):
            table = tables.nth(i)
            rows = await table.locator('xpath=.//tr').all_inner_texts()
            parsed = parse(rows)

            # find preceding heading (h2/h3/h4/p)
            header_locator = table.locator(
                'xpath=preceding::h2 | preceding::h3 | preceding::h4 | preceding::p'
            ).last

            header_text = await header_locator.inner_text() if await header_locator.element_handle() else f"Table {i+1}"

            data.append({header_text: parsed})
    else:
        # if no tables, get paragraphs
        paras = await container.locator('xpath=.//p').all_text_contents()
        data.append(paras)

    return {"InfoSection": data}

async def main():
    url = "https://collegedunia.com/university/25703-iit-bombay-indian-institute-of-technology-iitb-mumbai"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        result = await info_section(page, url)
        await browser.close()

    with open("test/info_section_university.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
