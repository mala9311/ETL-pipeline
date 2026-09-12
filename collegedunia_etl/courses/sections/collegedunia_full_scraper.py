import asyncio
import json
import traceback
from playwright.async_api import async_playwright, BrowserContext

# ─── CHANGE THIS URL ──────────────────────────────────────────────────────────
URL = "https://collegedunia.com/courses/bachelor-of-technology-btech-computer-science-and-engineering"
# ─────────────────────────────────────────────────────────────────────────────


async def auto_scroll(page):
    await page.evaluate("""
        async () => {
            await new Promise((resolve) => {
                let total = 0;
                const timer = setInterval(() => {
                    window.scrollBy(0, 400);
                    total += 400;
                    if (total >= document.body.scrollHeight) { clearInterval(timer); resolve(); }
                }, 150);
            });
        }
    """)
    await page.wait_for_timeout(1500)


async def click_tab(page, keywords: list) -> bool:
    selectors = [
        "a[class*='tab']", "li[class*='tab'] a", "ul[class*='tab'] li a",
        "div[class*='tab'] a", "nav a", "a[role='tab']", "button[role='tab']",
        ".tabs a", ".tab-nav a", "[class*='tabItem']", "[class*='tab-item']",
        "[class*='NavItem']", "[class*='nav-link']", "ul li a", "header a",
    ]
    for sel in selectors:
        for tab in await page.query_selector_all(sel):
            label = (await tab.inner_text()).strip().lower()
            if any(kw in label for kw in keywords):
                try:
                    await tab.scroll_into_view_if_needed()
                    await tab.click()
                    await page.wait_for_timeout(2000)
                    return True
                except Exception:
                    pass
    return False


async def scrape_sections(page) -> list:
    visited = set()
    result  = []

    containers = await page.query_selector_all(
        "div[class*='course'], div[class*='Course'], "
        "div[class*='overview'], div[class*='Overview'], "
        "div[class*='section'], div[class*='Section'], "
        "div[class*='content'], div[class*='Content'], "
        "div[class*='detail'],  div[class*='Detail'], "
        "div[class*='about'],   div[class*='About'], "
        "div[class*='highlight'], "
        ".box, section[id], article, main > div"
    )

    for box in containers:
        h_el    = await box.query_selector("h1, h2, h3, h4, h5, .heading, [class*='heading'], [class*='title']")
        heading = (await h_el.inner_text()).strip() if h_el else ""

        section = {"heading": heading, "paragraph": [], "tables": []}

        for el in await box.query_selector_all("p, li, table"):
            tag  = (await (await el.evaluate_handle("el => el.tagName")).json_value()).lower()
            text = (await el.inner_text()).strip()
            if not text:
                continue
            if tag in ["p", "li"]:
                if text not in visited and len(text) >= 10:
                    visited.add(text)
                    section["paragraph"].append(text)
            elif tag == "table":
                rows  = await el.query_selector_all("tr")
                tdata = []
                for row in rows:
                    cells  = await row.query_selector_all("th, td")
                    values = [(await c.inner_text()).strip() for c in cells]
                    if any(values):
                        tdata.append(values)
                if tdata:
                    section["tables"].append({"table_heading": heading, "table": tdata})

        if section["paragraph"] or section["tables"]:
            result.append(section)

    if not result:
        section = {"heading": "", "paragraph": [], "tables": []}
        for p in await page.query_selector_all("p"):
            text = (await p.inner_text()).strip()
            if text and text not in visited and len(text) >= 10:
                visited.add(text)
                section["paragraph"].append(text)
        if section["paragraph"]:
            result.append(section)

    return result


async def scrape_tips_syllabus(page) -> list:
    visited = set()
    result  = []

    containers = await page.query_selector_all(
        "div[class*='course'], div[class*='section'], div[class*='content'], "
        "div[class*='detail'], div[class*='overview'], .box, section[id], article, main > div"
    )

    for box in containers:
        headings   = []
        paragraphs = []
        tables     = []

        for h in await box.query_selector_all("h1, h2, h3, h4, h5"):
            t = (await h.inner_text()).strip()
            if t:
                headings.append(t)

        for el in await box.query_selector_all("p, li, table"):
            tag  = (await (await el.evaluate_handle("el => el.tagName")).json_value()).lower()
            text = (await el.inner_text()).strip()
            if not text:
                continue
            if tag in ["p", "li"]:
                if text not in visited and len(text) >= 10:
                    visited.add(text)
                    paragraphs.append(text)
            elif tag == "table":
                rows  = await el.query_selector_all("tr")
                tdata = []
                for row in rows:
                    cells  = await row.query_selector_all("th, td")
                    values = [(await c.inner_text()).strip() for c in cells]
                    if any(values):
                        tdata.append(values)
                if tdata:
                    tables.append(tdata)

        if paragraphs or tables:
            result.append({"headings": headings, "paragraphs": paragraphs, "tables": tables})

    return result


async def scrape_faqs(page, section_name="overview") -> list:
    faqs      = []
    visited_q = set()

    for sel in [
        "div[class*='faq'] div[class*='item']", "div[class*='faq'] li",
        "div[class*='Faq'] div[class*='item']", "div[class*='FAQ'] div[class*='item']",
        "div[class*='accordion'] div[class*='item']", "div[class*='accordion'] li",
        "div[class*='Accordion'] div", "details",
    ]:
        for w in await page.query_selector_all(sel):
            q_el = await w.query_selector(
                ".question, [class*='question'], [class*='Question'], summary, h4, h3, h5, dt, b, strong"
            )
            a_el = await w.query_selector(
                ".answer, [class*='answer'], [class*='Answer'], p, dd, div[class*='content'], div[class*='body']"
            )
            q = (await q_el.inner_text()).strip() if q_el else ""
            a = (await a_el.inner_text()).strip() if a_el else ""
            if q and q not in visited_q and len(q) > 5:
                visited_q.add(q)
                faqs.append({"section": section_name, "question": q, "answer": a})

    if not faqs:
        faq_section = await page.query_selector(
            "div[id*='faq'], section[id*='faq'], div[id*='FAQ'], "
            "div[class*='faq'], section[class*='faq'], div[class*='Faq'], div[class*='FAQ']"
        )
        if faq_section:
            buffer_q = ""
            for item in await faq_section.query_selector_all("p, li, h2, h3, h4, h5"):
                tag  = (await (await item.evaluate_handle("el => el.tagName")).json_value()).lower()
                text = (await item.inner_text()).strip()
                if not text:
                    continue
                if tag in ["h2", "h3", "h4", "h5"]:
                    buffer_q = text
                elif tag in ["p", "li"] and buffer_q:
                    if buffer_q not in visited_q:
                        visited_q.add(buffer_q)
                        faqs.append({"section": section_name, "question": buffer_q, "answer": text})
                    buffer_q = ""

    if not faqs:
        for el in await page.query_selector_all("[class*='question'], [class*='Question']"):
            q = (await el.inner_text()).strip()
            try:
                next_el = await el.evaluate_handle("el => el.nextElementSibling")
                a = (await next_el.as_element().inner_text()).strip()
            except Exception:
                a = ""
            if q and q not in visited_q and len(q) > 5:
                visited_q.add(q)
                faqs.append({"section": section_name, "question": q, "answer": a})

    return faqs


async def scrape_career(page) -> dict:
    career_content = {}
    visited        = set()

    containers = await page.query_selector_all(
        "div[class*='career'], div[class*='Career'], div[class*='job'], "
        "div[class*='scope'], div[class*='section'], div[class*='content'], "
        ".box, article, main > div"
    )
    for box in containers:
        h_el    = await box.query_selector("h2, h3, h4, h5, [class*='heading']")
        heading = (await h_el.inner_text()).strip() if h_el else "Career Overview"
        texts   = []
        for el in await box.query_selector_all("p, li"):
            text = (await el.inner_text()).strip()
            if text and text not in visited and len(text) >= 10:
                visited.add(text)
                texts.append(text)
        if texts:
            career_content[heading] = " ".join(texts)

    career_table = []
    for table in await page.query_selector_all("table"):
        rows  = await table.query_selector_all("tr")
        tdata = []
        for row in rows:
            cells  = await row.query_selector_all("th, td")
            values = [(await c.inner_text()).strip() for c in cells]
            if any(values):
                tdata.append(values)
        if tdata:
            career_table.append(tdata)

    return {
        "career_content": career_content,
        "career_table":   career_table,
        "faqs":           await scrape_faqs(page, "career"),
    }


async def main():
    print(f"Scraping: {URL}")
    print("-" * 60)

    # ── Define result OUTSIDE try block so it's always accessible ────────────
    result = {
        "Course Name":     "",
        "Course URL":      URL,
        "url":             URL,
        "Career":          {"career_content": {}, "career_table": [], "faqs": []},
        "FAQs":            [],
        "PreparationTips": [],
        "Syllabus":        [],
        "admission":       [],
        "eligibility":     [],
        "overview":        [],
        "error":           None,
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ))
        page = await context.new_page()

        try:
            # ── FIX: removed networkidle — causes timeout on heavy JS sites ──
            await page.goto(URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)   # wait 5s for JS to render
            await auto_scroll(page)

            result["Course Name"] = (await page.title()).strip() or \
                                    URL.split("/")[-1].replace("-", " ").title()

            # 1. Overview
            print("  [1/7] overview...")
            result["overview"] = await scrape_sections(page)
            print(f"        → {len(result['overview'])} sections")

            # 2. Syllabus
            print("  [2/7] syllabus...")
            clicked = await click_tab(page, ["syllabus"])
            if clicked:
                await auto_scroll(page)
                result["Syllabus"] = await scrape_tips_syllabus(page)
            print(f"        → {len(result['Syllabus'])} sections {'✓' if clicked else '✗ tab not found'}")

            # 3. Career
            print("  [3/7] career...")
            clicked = await click_tab(page, ["career", "job", "scope"])
            if clicked:
                await auto_scroll(page)
                result["Career"] = await scrape_career(page)
            print(f"        → {len(result['Career']['career_content'])} keys {'✓' if clicked else '✗ tab not found'}")

            # 4. Admission
            print("  [4/7] admission...")
            clicked = await click_tab(page, ["admission"])
            if clicked:
                await auto_scroll(page)
                result["admission"] = await scrape_sections(page)
            print(f"        → {len(result['admission'])} sections {'✓' if clicked else '✗ tab not found'}")

            # 5. Eligibility
            print("  [5/7] eligibility...")
            clicked = await click_tab(page, ["eligibility"])
            if clicked:
                await auto_scroll(page)
                result["eligibility"] = await scrape_sections(page)
            print(f"        → {len(result['eligibility'])} sections {'✓' if clicked else '✗ tab not found'}")

            # 6. Preparation Tips
            print("  [6/7] preparation tips...")
            clicked = await click_tab(page, ["preparation", "tips", "how to prepare"])
            if clicked:
                await auto_scroll(page)
                result["PreparationTips"] = await scrape_tips_syllabus(page)
            print(f"        → {len(result['PreparationTips'])} sections {'✓' if clicked else '✗ tab not found'}")

            # 7. FAQs
            print("  [7/7] faqs...")
            clicked = await click_tab(page, ["faq", "frequently asked"])
            if clicked:
                await auto_scroll(page)
                result["FAQs"] = await scrape_faqs(page, "overview")
            else:
                result["FAQs"] = await scrape_faqs(page, "overview")
            print(f"        → {len(result['FAQs'])} questions {'✓' if clicked else '(from main page)'}")

        except Exception as e:
            result["error"] = str(e)
            print(f"ERROR: {e}")
            traceback.print_exc()

        finally:
            await context.close()
            await browser.close()

    # ── Always saves, even if error occurred ─────────────────────────────────
    out_file = "collegedunia_course_overview_data.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("-" * 60)
    print(f"Saved to {out_file}")
    print(f"\nSummary:")
    print(f"  overview        : {len(result['overview'])} sections")
    print(f"  Career content  : {len(result['Career']['career_content'])} keys")
    print(f"  FAQs            : {len(result['FAQs'])} items")
    print(f"  PreparationTips : {len(result['PreparationTips'])} sections")
    print(f"  Syllabus        : {len(result['Syllabus'])} sections")
    print(f"  admission       : {len(result['admission'])} sections")
    print(f"  eligibility     : {len(result['eligibility'])} sections")


if __name__ == "__main__":
    asyncio.run(main())