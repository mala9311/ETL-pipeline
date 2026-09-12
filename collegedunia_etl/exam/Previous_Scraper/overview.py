import asyncio
from playwright.async_api import async_playwright
import json

async def overview(page, url):
    data=[]
    await page.goto(url)
    container = page.locator('//div[@class="jsx-2649715491 content-section college-content-section"]')
    div = container.locator('xpath = child::div[1]')
    paras = await div.locator('p').all_text_contents()
    data.append(paras[:10])
    return {"Overview":data}


async def main():
    base_url = "https://collegedunia.com/exams/jeecup"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        data = await overview(page, base_url)
        
        await browser.close()

    with open("test/overview.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"Done")

if __name__ == "__main__":
    asyncio.run(main())
