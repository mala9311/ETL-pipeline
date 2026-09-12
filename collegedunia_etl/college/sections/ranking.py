import asyncio
import json
import os
from playwright.async_api import async_playwright

def clean_text(text):
    if not text:
        return ""
    # Remove UI artifacts and 'Read Less' buttons
    return text.replace('Read Less', '').replace('Read More', '').strip()

async def get_formatted_ranking(page, url):
    print(f"--- Scraping Ranking in DB Format: {url} ---")
    
    try:
        # 1. Navigation
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # 2. Force click all "Read More" buttons to reveal full text
        buttons = page.locator("text='Read More'")
        btn_count = await buttons.count()
        for i in range(btn_count):
            try:
                await buttons.nth(i).click(force=True)
            except:
                continue
        if btn_count > 0:
            await asyncio.sleep(2) # Wait for text expansion

        # 3. Structured Data Extraction
        # This script creates objects with "type" and "content" fields
        db_formatted_data = await page.evaluate("""
            () => {
                const rankingList = [];
                const containers = document.querySelectorAll('div.collegeDetailContainer, div[class*="rankingBlock"]');

                containers.forEach(container => {
                    // 1. Get Title (h2)
                    const h2 = container.querySelector('h2');
                    if (h2) {
                        rankingList.push({
                            "type": "title",
                            "content": h2.innerText.trim()
                        });
                    }

                    // 2. Get Paragraphs
                    const pElements = Array.from(container.querySelectorAll('p'))
                        .map(p => p.innerText.replace('Read Less', '').trim())
                        .filter(t => t.length > 20);
                    
                    if (pElements.length > 0) {
                        rankingList.push({
                            "type": "paragraphs",
                            "content": pElements
                        });
                    }

                    // 3. Get Tables
                    const tables = container.querySelectorAll('table');
                    tables.forEach(table => {
                        const rows = Array.from(table.querySelectorAll('tr'));
                        if (rows.length > 1) {
                            const headers = Array.from(rows[0].querySelectorAll('th, td'))
                                .map(h => h.innerText.trim());
                            
                            const tableContent = rows.slice(1).map(tr => {
                                const cells = Array.from(tr.querySelectorAll('td'))
                                    .map(c => c.innerText.trim());
                                let rowObj = {};
                                headers.forEach((header, idx) => {
                                    rowObj[header || `Column_${idx + 1}`] = cells[idx] || "";
                                });
                                return rowObj;
                            });

                            rankingList.push({
                                "type": "table",
                                "heading": h2 ? h2.innerText.trim() : "Ranking Table",
                                "content": tableContent
                            });
                        }
                    });
                });
                return rankingList;
            }
        """)

        return {"Ranking": db_formatted_data}

    except Exception as e:
        print(f"Error during scrape: {e}")
        return {"Ranking": [], "error": str(e)}

async def main():
    target_url = "https://www.collegedekho.com/colleges/iitm/ranking"
    
    # Save setup
    os.makedirs("test", exist_ok=True)
    output_file = "test/ranking_db_format.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        final_data = await get_formatted_ranking(page, target_url)
        
        # Write to JSON exactly like your example
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
            
        print(f"\n✅ SUCCESS!")
        print(f"Data saved in DB format to: {os.path.abspath(output_file)}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())