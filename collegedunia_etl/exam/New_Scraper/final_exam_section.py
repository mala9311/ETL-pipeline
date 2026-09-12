import asyncio
from playwright.async_api import async_playwright
import os
import json
import re
import pandas as pd


def _slug_from_url(url):
    slug = url.rstrip('/').split('/')[-1]
    if not slug:
        slug = re.sub(r'[^a-zA-Z0-9\-]', '-', url)
    return slug


def _slug_from_title(title):
    """Return a normalized slug for a title. If empty, fallback to using the URL-like slug logic."""
    if not title:
        return ''
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.strip().lower()).strip('-')
    return slug or _slug_from_url(title)


def _parse_table(table_rows):
    """Parse table rows (list of inner_texts) into list of dicts."""
    details = []
    if not table_rows:
        return details
    head = [v.strip() for v in table_rows[0].split('\t')]
    for row in table_rows[1:]:
        values = [v.strip() for v in row.split('\t')]
        details.append(dict(zip(head, values)))
    return details


async def full_exam_section(page, base_url, EXAM: str = None):
    slug = EXAM.strip() if EXAM else _slug_from_url(base_url)
    output_dir = os.path.join('final_exam_section', slug)
    os.makedirs(output_dir, exist_ok=True)

    combined = {
        "EXAM": slug,
        "source": base_url,
        "college": None,
        "sections": []
    }

    all_sections = [
        "",                  # overview
        "/exam-dates",      
        "/eligibility",      
        "/application-form", 
        "/exam-pattern",     
        "/syllabus",         
        "/preparation-tips", 
        "/important-books",  
        "/question-paper",   
        "/cutoff",           
                     
    ]

    
    CONTAINER_SELECTOR = '//div[contains(@class,"content-section") and contains(@class,"college-content-section")]'
    TABLE_SELECTOR     = '//table[@class="table table-striped style_table"]'

    for extension in all_sections:
        new_url = base_url + extension
        try:
        
            await page.goto(new_url)
            await page.wait_for_load_state('domcontentloaded')
            await asyncio.sleep(2)
            print(f"  Details scraped: {len(data['Details'])} for {new_url}")

        except Exception:
            pass

        data = {
            "Source": new_url,
            "Details": []
        }

        # ✅ Check if page is 404/not found 
        try:
            if await page.get_by_text("Someone is so lost").is_visible():
                sub_section = extension.strip('/') or 'overview'
                filename = os.path.join(output_dir, f"{sub_section}.json")
                with open(filename, "w", encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                combined['sections'].append({
                    "name": sub_section,
                    "url": new_url,
                    "data": data
                })
                print(f"Skipped (404) 🚫 {filename}")
                continue
        except Exception:
            pass

        try:
            container = page.locator(CONTAINER_SELECTOR)

            # ── Overview section ──────────────────────────────────────────
            if extension == "":
                try:
                    div = container.locator('xpath=child::div[1]')
                    paras = await div.locator('p').all_text_contents()
                    if paras:
                        data["Details"].append({
                            "Heading": "Overview",
                            "Content": [{"Summary": p.strip()} for p in paras[:10] if p.strip()]
                        })
                except Exception:
                    pass

            # ── Syllabus section ──────────────────────────────────────────
            elif extension == "/syllabus":
                try:
                    tables = container.locator(TABLE_SELECTOR)
                    n = await tables.count()
                    content = []
                    if n > 0:
                        for i in range(n):
                            table = tables.nth(i)
                            table_rows = await table.locator('//tr').all_inner_texts()
                            parsed = _parse_table(table_rows)
                            header = ""
                            header_locator = table.locator('xpath=preceding::h3 | preceding::h4 | preceding::p').last
                            header_handle = await header_locator.element_handle()
                            header = await header_locator.inner_text() if header_handle else ""
                            content.append({header: parsed})
                    else:
                        paras = await container.locator('p').all_text_contents()
                        content = [{"Summary": p.strip()} for p in paras if p.strip()]
                    if content:
                        data["Details"].append({
                            "Heading": "Syllabus",
                            "Content": content
                        })
                except Exception:
                    pass

            # ── Cutoff section ────────────────────────────────────────────
            elif extension == "/cutoff":
                try:
                    tables = container.locator(TABLE_SELECTOR)
                    n = await tables.count()
                    content = []
                    if n > 0:
                        for i in range(n):
                            table = tables.nth(i)
                            table_rows = await table.locator('//tr').all_inner_texts()
                            parsed = _parse_table(table_rows)
                            header = ""
                            header_locator = table.locator('xpath=preceding::h2 | preceding::h3 | preceding::h4').last
                            if await header_locator.count() > 0:
                                header = await header_locator.inner_text()
                            content.append({header: parsed})
                    else:
                        paras = await container.locator('p').all_text_contents()
                        content = [{"Summary": p.strip()} for p in paras if p.strip()]
                    if content:
                        data["Details"].append({
                            "Heading": "Cutoff",
                            "Content": content
                        })
                except Exception:
                    pass

            # ── Eligibility section ───────────────────────────────────────
            elif extension == "/eligibility":
                try:
                    div = container.locator('xpath=child::div[1]')
                    tables = div.locator(
                        '//table[@class="table table-striped style_table"]'
                        '[following::div[@id="lead_btns_0"]]'
                    )
                    n = await tables.count()
                    content = []
                    if n > 0:
                        for i in range(n):
                            table = tables.nth(i)
                            table_rows = await table.locator('//tr').all_inner_texts()
                            parsed = _parse_table(table_rows)
                            header = ""
                            header_locator = table.locator('xpath=preceding::h2 | preceding::h3 | preceding::h4').last
                            if await header_locator.count() > 0:
                                header = await header_locator.inner_text()
                            content.append({header: parsed})
                    else:
                        paras = await div.locator(
                            '//p[following::div[@id="lead_btns_0"]] | '
                            '//ul[following::div[@id="lead_btns_0"]]'
                        ).all_text_contents()
                        content = [{"Summary": p.strip()} for p in paras[4:] if p.strip()]
                    if content:
                        data["Details"].append({
                            "Heading": "Eligibility",
                            "Content": content
                        })
                except Exception:
                    pass

            # ── Preparation Tips section ──────────────────────────────────
            elif extension == "/preparation-tips":
                try:
                    content = []
                    # Overview part
                    ov = container.locator('div.cdcms_exam_highlights')
                    overview_texts = await ov.locator('xpath=child::p | child::ol').all_inner_texts()
                    if overview_texts:
                        content.append({"Overview": overview_texts})

                    # HR sections
                    hr = page.locator('//hr')
                    hr_num = await hr.count()
                    if hr_num > 1:
                        for i in range(hr_num - 1):
                            xpath = f'(//hr)[{i+1}]/following-sibling::*[following::hr[{hr_num - (i+1)}]]'
                            nodes = page.locator(xpath)
                            node_count = await nodes.count()
                            if node_count > 0:
                                header_xpath = (
                                    f'(//hr)[{i+1}]/following-sibling::*'
                                    f'[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6]'
                                    f'[following::hr[{hr_num - (i+1)}]]'
                                )
                                header_locator = page.locator(header_xpath).first
                                header_text = "Untitled Section"
                                try:
                                    if await header_locator.count() > 0:
                                        header_text = await header_locator.inner_text()
                                except Exception:
                                    pass

                                content_xpath = (
                                    f'(//hr)[{i+1}]/following-sibling::*'
                                    f'[self::p or self::li or self::ul or self::div]'
                                    f'[following::hr[{hr_num - (i+1)}]]'
                                )
                                content_nodes = page.locator(content_xpath)
                                content_count = await content_nodes.count()
                                segment_data = []
                                for j in range(content_count):
                                    node = content_nodes.nth(j)
                                    node_name = await node.evaluate('el => el.tagName.toLowerCase()')
                                    if node_name in ('p', 'li'):
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
                                            table_rows = await table.locator('tr').all_inner_texts()
                                            parsed = _parse_table(table_rows)
                                            segment_data.append({'Table': parsed})
                                if segment_data:
                                    content.append({header_text: segment_data})
                    if content:
                        data["Details"].append({
                            "Heading": "Preparation Tips",
                            "Content": content
                        })
                except Exception:
                    pass

            # ── All Other Sections (exam-dates, application-form, etc.) ───
            else:
                try:
                    div = container.locator('xpath=child::div[1]')
                    tables = div.locator(TABLE_SELECTOR)
                    n = await tables.count()
                    content = []
                    heading = extension.strip('/').replace('-', ' ').title()

                    if n > 0:
                        for i in range(n):
                            table = tables.nth(i)
                            table_rows = await table.locator('//tr').all_inner_texts()
                            parsed = _parse_table(table_rows)
                            header = ""
                            header_locator = table.locator('xpath=preceding::h2 | preceding::h3 | preceding::h4').last
                            if await header_locator.count() > 0:
                                header = await header_locator.inner_text()
                            content.append({header: parsed})
                    else:
                        paras = await div.locator('p, ul, li').all_text_contents()
                        content = [{"Summary": p.strip()} for p in paras if p.strip()]

                    if content:
                        data["Details"].append({
                            "Heading": heading,
                            "Content": content
                        })
                except Exception:
                    pass

        except Exception as e:
            print(f"Error scraping {new_url}: {e}")

        # ── Save individual section JSON ──────────────────────────────────
        sub_section = extension.strip('/')
        if not sub_section:
            sub_section = 'overview'

        filename = os.path.join(output_dir, f"{sub_section}.json")
        try:
            with open(filename, "w", encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to write {filename}: {e}")

        combined['sections'].append({
            "name": sub_section,
            "url": new_url,
            "data": data
        })

        print(f"Done 🚀 {filename}")

    # ── Save combined JSON ────────────────────────────────────────────────
    combined_path = os.path.join(output_dir, "exam.json")
    try:
        with open(combined_path, "w", encoding='utf-8') as f:
            json.dump(combined, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: failed to write combined exam file {combined_path}: {e}")

    return combined


# ── Run for ALL exams from CSV ────────────────────────────────────────────────
async def scrape_all_exams():
    csv_path = "scraped_data/exam_urls.csv"

    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        print("Please run all_exam_urls.py first!")
        return

    df = pd.read_csv(csv_path)
    print(f"Total exams found in CSV: {len(df)}")
    print("=" * 50)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page    = await browser.new_page()

        success = 0
        failed  = 0

        for index, row in df.iterrows():
            try:
                title = row.get("Title", "")
                url   = row.get("URL", "")

                if not url or not isinstance(url, str):
                    print(f"Skipping row {index} — invalid URL")
                    failed += 1
                    continue

                slug = _slug_from_url(url)
                print(f"[{index + 1}/{len(df)}] Scraping: {title} → {url}")

                await full_exam_section(
                    page=page,
                    base_url=url,
                    EXAM=slug
                )
                success += 1

            except Exception as e:
                print(f"Failed: {row.get('URL', '')} → {e}")
                failed += 1
                continue

        await browser.close()

    print("=" * 50)
    print(f"Total success : {success}")
    print(f"Total failed  : {failed}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(scrape_all_exams())
