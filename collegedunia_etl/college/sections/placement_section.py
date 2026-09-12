import asyncio
from playwright.async_api import async_playwright
import json
import os


# ---------------- HELPERS ----------------
def is_valid_para(text: str) -> bool:
    t = text.lower()
    return (
        len(text) > 80
        and "?" not in text
        and not any(x in t for x in ["question", "answer", "qna", "faq", "q.", "a."])
    )


# ---------------- TABLE PARSER ----------------
async def parse_table_data(table_tag):
    table_data = []

    try:
        headers = []
        thead_th = await table_tag.query_selector_all("thead tr th")
        if thead_th:
            headers = [(await th.inner_text()).strip() for th in thead_th]

        rows = await table_tag.query_selector_all("tbody tr")
        start_index = 0

        if not headers and rows:
            first_row_th = await rows[0].query_selector_all("th")
            if first_row_th:
                headers = [(await th.inner_text()).strip() for th in first_row_th]
                start_index = 1

        for tr in rows[start_index:]:
            tds = await tr.query_selector_all("td")
            values = [(await td.inner_text()).strip() for td in tds]

            if headers:
                row = {
                    headers[i]: values[i]
                    for i in range(min(len(headers), len(values)))
                    if values[i]
                }
                if row:
                    table_data.append(row)
            else:
                clean = [v for v in values if v]
                if clean:
                    table_data.append(clean)

    except Exception:
        pass

    return table_data


# ---------------- PLACEMENT SCRAPER ----------------
async def placement_section(page, url):
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1000)

    data = {
        "College": (await page.locator("h1").first.inner_text()).strip(),
        "Placement": []
    }

    main_section = page.locator("#listing-article > div")
    sections = main_section.locator("> div")

    # -------- INTRO / OVERVIEW PARAS (NO HEADING IN HTML) --------
    intro = {"Heading": "Overview", "Content": []}

    first_section = sections.nth(0)
    intro_paras = await first_section.locator("> p").all()

    for p in intro_paras:
        text = (await p.inner_text()).strip()
        if is_valid_para(text):
            intro["Content"].append({"Summary": text})

    if intro["Content"]:
        data["Placement"].append(intro)

    # -------- NORMAL SECTION PROCESSING --------
    section_count = await sections.count()

    for i in range(section_count):
        section = sections.nth(i)
        section_text = (await section.inner_text()).lower()

        if any(x in section_text for x in ["faq", "question", "answer", "qna"]):
            continue

        elements = await section.locator("h2, h3, p, div").element_handles()

        current_section = None

        for el in elements:
            tag = (await (await el.get_property("tagName")).json_value()).lower()

            # ---------- HEADING ----------
            if tag in ("h2", "h3"):
                if current_section and current_section["Content"]:
                    data["Placement"].append(current_section)

                current_section = {
                    "Heading": (await el.inner_text()).strip(),
                    "Content": []
                }
                continue

            # ---------- STRONG HEADING ----------
            if tag == "p":
                strong = await el.query_selector("strong")
                text = (await el.inner_text()).strip()

                if strong and len(text) < 80:
                    if current_section and current_section["Content"]:
                        data["Placement"].append(current_section)

                    current_section = {
                        "Heading": text,
                        "Content": []
                    }
                    continue

                if current_section and is_valid_para(text):
                    current_section["Content"].append({"Summary": text})

            # ---------- TABLE ----------
            if tag == "div":
                table = await el.query_selector("table")
                if table and current_section:
                    table_data = await parse_table_data(table)
                    if table_data:
                        current_section["Content"].append(
                            {"Table": table_data}
                        )

        if current_section and current_section["Content"]:
            data["Placement"].append(current_section)

    return data


# ---------------- MAIN ----------------
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = (
            "https://collegedunia.com/university/25703-"
            "iit-bombay-indian-institute-of-technology-iitb-mumbai/placement"
        )

        data = await placement_section(page, url)

        print(json.dumps(data, indent=2, ensure_ascii=False))

        os.makedirs("engineering_data", exist_ok=True)
        with open(
            "engineering_data/IIT_Bombay_placement.json",
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
