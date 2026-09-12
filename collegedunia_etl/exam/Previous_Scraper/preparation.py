import asyncio
from playwright.async_api import async_playwright
import json
import time

def parse(table):
    details = []
    head = [v.replace('\n','') for v in table[0].split('\t')]
    for data in table[1:]:
        values = [d.replace('\n','') for d in data.split('\t')]
        details.append(dict(zip(head,values)))
    return details

async def preparation(page, url):
    data = []
    await page.goto(url + "/preparation-tips")
    if await page.get_by_text("Someone is so lost").is_visible():
        return {"Preparation Tips":""}
    container = page.locator('//div[@class="jsx-2649715491 content-section college-content-section"]')
    ov = container.locator('div.cdcms_exam_highlights')
    overview = await ov.locator('xpath=child::p | child::ol').all_inner_texts()
    data.append({
        "Overview": overview
    })
    
    hr = page.locator('//hr')
    hr_num = await hr.count()
    
    if hr_num > 1:
        for i in range(hr_num - 1):
            xpath = f'(//hr)[{i+1}]/following-sibling::*[following::hr[{hr_num - (i+1)}]]'
            nodes = page.locator(xpath)
            node_count = await nodes.count()
            
            if node_count > 0:
                header_xpath = f'(//hr)[{i+1}]/following-sibling::*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6][following::hr[{hr_num - (i+1)}]]'
                header_locator = page.locator(header_xpath).first
                
                header_text = "Untitled Section"
                try:
                    if await header_locator.count() > 0:
                        header_text = await header_locator.inner_text()
                except:
                    pass
                
                content_xpath = f'(//hr)[{i+1}]/following-sibling::*[self::p or self::li or self::ul or self::div][following::hr[{hr_num - (i+1)}]]'
                content_nodes = page.locator(content_xpath)
                content_count = await content_nodes.count()
                
                segment_data = []
                for j in range(content_count):
                    node = content_nodes.nth(j)
                    node_name = await node.evaluate('el => el.tagName.toLowerCase()')
                    
                    if node_name == 'p':
                        text = await node.inner_text()
                        if text.strip():
                            segment_data.append(text.strip())
                    elif node_name == 'li':
                        text = await node.inner_text()
                        if text.strip():
                            segment_data.append(text.strip())
                    elif node_name == 'ul':
                        li_elements = node.locator('li')
                        li_count = await li_elements.count()
                        for k in range(li_count):
                            li_text = await li_elements.nth(k).inner_text()
                            if li_text.strip():
                                segment_data.append(li_text.strip())
                    elif node_name == 'div':
                        class_attr = await node.get_attribute('class')
                        if class_attr and 'table-responsive' in class_attr:
                            table = node.locator('table')
                            table_rows = table.locator('tr')
                            table_n = await table_rows.all_inner_texts()
                            tabel = parse(table_n)
                            segment_data.append({'Table': tabel})
                
                if segment_data:
                    data.append({
                        header_text: segment_data
                    })
    
    return {"Preparation Tips":data}

async def main():
    async with async_playwright() as p:
        start=time.time()
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = "https://collegedunia.com/exams/jeecup"
        result = await preparation(page, url)

        with open('test/preparationtips.json', "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        await browser.close()
        end=time.time()
        print(f"Time take: {end-start} seconds")

if __name__ == "__main__":
    asyncio.run(main())
