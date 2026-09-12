import asyncio
from playwright.async_api import async_playwright
import json

def parse(table):
    details = []
    head = [v for v in table[0].split('\t')]
    for data in table[1:]:
        values = [d for d in data.split('\t')]
        details.append(dict(zip(head,values)))
    return details

async def syllabus(page, url):
    data=[]
    await page.goto(url+"/syllabus")
    if await page.get_by_text("Someone is so lost").is_visible():
        return {"Syllabus":""}
    container = page.locator('//div[@class="jsx-2649715491 content-section college-content-section"]')
    tables = container.locator('//table[@class="table table-striped style_table"]')
    n=await tables.count()
    if n>0:
        for i in range(n):
            table=tables.nth(i)
            table_n = await table.locator('//tr').all_inner_texts()
            tabel = parse(table_n)
            header_locator = table.locator('xpath = preceding::h3 | preceding::h4 | preceding::p').last
            header_handle = await header_locator.element_handle()
            header = await header_locator.inner_text() if header_handle else ""
            data.append({
                header:tabel
            })
    else:
        paras = await container.locator('p').all_text_contents()
        data.append(paras)
    return {"Syllabus":data}


async def main():
    base_url = "https://www.collegedunia.com/exams/jeecup"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        data = await syllabus(page, base_url)
        
        await browser.close()

    with open("test/syllabus.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"Done")

if __name__ == "__main__":
    asyncio.run(main())
