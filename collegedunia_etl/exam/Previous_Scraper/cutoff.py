import asyncio
from playwright.async_api import async_playwright
import time
import json

def parse(table):
    details = []
    head = [v for v in table[0].split('\t')]
    for data in table[1:]:
        values = [d for d in data.split('\t')]
        details.append(dict(zip(head,values)))
    return details

async def cutoff(page, url):
    await page.goto(url+"/cutoff")
    data=[]
    if await page.get_by_text("Someone is so lost").is_visible():
        return {"Cutoff":""}
    container = page.locator('//div[@class="jsx-2649715491 content-section college-content-section"]')
    tables = container.locator('//table[@class="table table-striped style_table"]')
    n=await tables.count()
    if n>0:
        for i in range(n):
            table=tables.nth(i)
            table_n = await table.locator('//tr').all_inner_texts()
            tabel = parse(table_n)
            header = ""
            header_locator = table.locator('xpath = preceding::h2 | preceding::h3 | preceding::h4').last
            # header_handle = await header_locator.element_handle()
            # header = await header_locator.inner_text() if header_handle else ""
            if await header_locator.count()>0:
                header = await header_locator.inner_text()
            data.append({
                header:tabel
            })
    else:
        paras = await container.locator('p').all_text_contents()
        data.append(paras)
    return {"Cutoff":data}



async def main():
    async with async_playwright() as p:
        start = time.time()
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = "https://collegedunia.com/exams/jeecup"
        result = await cutoff(page, url)

        with open('test/cuttoff.json', "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        await browser.close()
        end = time.time()
        print(f"Time taken: {end-start} seconds")
if __name__=="__main__":
    asyncio.run(main())
