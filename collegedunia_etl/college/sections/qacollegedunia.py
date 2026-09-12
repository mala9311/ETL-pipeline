import asyncio
import json
from playwright.async_api import async_playwright

BASE = "https://collegedunia.com"


async def qa(page, base_url, pages=3):
    all_qna = []

    for page_no in range(1, pages + 1):
        url = f"{base_url}?page={page_no}"
        print(f"Scraping page {page_no}: {url}")

        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        # MAIN QUESTION CARD (FULL CLASS)
        cards = page.locator(
            '//div[contains(@class,"card") '
            'and contains(@class,"container-fluid") '
            'and contains(@class,"question-card") '
            'and contains(@class,"rounded-xl")]'
        )

        card_count = await cards.count()
        print(f"Found {card_count} questions")

        for i in range(card_count):
            card = cards.nth(i)

            # ---------------- QUESTION ----------------
            question = ""
            try:
                question = (await card.locator(
                    'xpath=.//h3[@data-test-id="question-card-title"]//a'
                ).inner_text()).strip()
            except:
                continue

            # ---------------- AUTHOR ----------------
            author = ""
            try:
                author = (await card.locator(
                    'xpath=.//span[contains(@class,"author-img")]'
                ).inner_text()).strip()
            except:
                pass

            # ---------------- DATE ----------------
            date = ""
            try:
                date = (await card.locator(
                    'xpath=.//span[contains(@class,"posted-on")]'
                ).inner_text()).replace("Posted On :", "").strip()
            except:
                pass

            # ---------------- READ MORE (PER CARD) ----------------
            read_more = card.locator(
                'xpath=.//span[@data-test-id="ques-read-more"]'
            )
            if await read_more.count() > 0:
                try:
                    await read_more.first.scroll_into_view_if_needed()
                    await read_more.first.click()
                    await page.wait_for_timeout(400)
                except:
                    pass

            # ---------------- ANSWER ----------------
            answers = []
            answer_container = card.locator(
                'xpath=.//div[contains(@class,"actual-description")]'
            )

            # paragraphs
            paras = answer_container.locator('xpath=.//p')
            for j in range(await paras.count()):
                txt = (await paras.nth(j).inner_text()).strip()
                if txt:
                    answers.append(txt)

            # bullet points
            bullets = answer_container.locator('xpath=.//li')
            for j in range(await bullets.count()):
                txt = (await bullets.nth(j).inner_text()).strip()
                if txt:
                    answers.append(f"- {txt}")

            all_qna.append({
                "Question": question,
                "Author": author,
                "Date": date,
                "Answer": answers
            })

    return {"QnA": all_qna}


async def main():
    # 👉 INPUT URL (ONLY THIS NEEDS TO CHANGE)
    base_url = "https://collegedunia.com/qna?college=25703"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        data = await qa(page, base_url, pages=3)

        await browser.close()

    with open("collegedunia_qna.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("✅ Done! Data saved to collegedunia_qna.json")


if __name__ == "__main__":
    asyncio.run(main())
