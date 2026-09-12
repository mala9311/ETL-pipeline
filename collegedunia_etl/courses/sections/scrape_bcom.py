"""
CollegeDunia B.Com Course Scraper - Corrected Version

Key fixes vs original:
1. Syllabus: use text-match selectors instead of hashed CSS classes
2. Jobs: target the correct career section, not CollegeDunia's own job board
3. FAQ: scroll + wait longer to let accordion content expand
4. All selectors are class-agnostic (uses :has-text, text-match, structural traversal)
"""

import asyncio
import csv
import json
import re
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning)
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
BASE_DIR = Path(__file__).parent  # points to courses/sections/
INPUT_CSV = BASE_DIR / "bcom_branch_url.csv"
# INPUT_CSV = "bcom_branch_url.csv"
OUTPUT_JSON = "bcom_scraped.json"
OUTPUT_CSV = "bcom_scraped_flat.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


async def safe_text(page, selector: str, timeout: int = 5000) -> str:
    try:
        el = await page.wait_for_selector(selector, timeout=timeout)
        return clean(await el.inner_text()) if el else ""
    except PlaywrightTimeout:
        return ""


async def all_texts(page, selector: str) -> list[str]:
    els = await page.query_selector_all(selector)
    texts = []
    for el in els:
        t = clean(await el.inner_text())
        if t:
            texts.append(t)
    return texts


async def scroll_page(page):
    """Scroll down to trigger lazy-loaded content."""
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    await page.wait_for_timeout(1000)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(1500)


# ---------------------------------------------------------------------------
# Section scrapers
# ---------------------------------------------------------------------------

async def scrape_overview(page) -> dict:
    """
    Overview tab — course intro, highlights table, admission info.
    Uses broad structural selectors since CollegeDunia has dynamic class names.
    """
    data = {}

    # Intro paragraph — grab the first substantial <p> on the page
    paras = await page.query_selector_all("main p, article p, #content p")
    intro = ""
    for p in paras:
        t = clean(await p.inner_text())
        if len(t) > 80:
            intro = t
            break
    data["intro"] = intro

    # Highlights — all table rows and list items on the page
    # (CollegeDunia embeds highlights, exam dates, fee tables as <tr> rows)
    rows = await page.query_selector_all("table tr, ul li")
    highlights = []
    for row in rows:
        t = clean(await row.inner_text())
        if t and len(t) > 5:
            highlights.append(t)
    data["highlights"] = highlights

    # Admission section — look for h2/h3 containing "Admission" then grab sibling text
    admission_text = await page.evaluate(r"""() => {
        const headers = [...document.querySelectorAll('h2, h3')];
        const admHeader = headers.find(h => /admission/i.test(h.innerText));
        if (!admHeader) return '';
        // collect text from following siblings until next h2/h3
        let text = '';
        let el = admHeader.nextElementSibling;
        let count = 0;
        while (el && count < 10) {
            if (['H2','H3'].includes(el.tagName)) break;
            text += ' ' + (el.innerText || '');
            el = el.nextElementSibling;
            count++;
        }
        return text.trim();
    }""")
    data["admission"] = clean(admission_text)[:3000]

    # Top colleges — anchor tags inside sections with "college" in their text
    colleges = await page.evaluate(r"""() => {
        return [...document.querySelectorAll('a')]
            .filter(a => /college|university|institute/i.test(a.innerText) && a.innerText.length < 100)
            .map(a => a.innerText.trim())
            .filter((v, i, arr) => arr.indexOf(v) === i)
            .slice(0, 20);
    }""")
    data["top_colleges_mentioned"] = [clean(c) for c in colleges if clean(c)]

    # Raw overview text from main content area
    raw = await safe_text(page, "main, article, #content, #overview-content", timeout=3000)
    data["raw_overview_text"] = raw[:5000]

    return data


async def scrape_syllabus(page) -> dict:
    """
    Extract semester subjects and books from the /syllabus sub-page.

    Strategy (in order of reliability):
    1. Parse structured tables — CollegeDunia often uses <table> with semester columns.
    2. Heading + sibling traversal for h2/h3/h4/strong that mention Semester/Year.
    3. Scan ALL text nodes for lines that look like subject names grouped under
       a semester heading (fallback for deeply-nested or unusual layouts).
    Books are pulled from any heading containing book/reference keywords.
    """
    data = {"semesters": [], "books": [], "raw_syllabus_text": ""}

    await scroll_page(page)

    semesters = await page.evaluate(r"""() => {
        const results = [];
        const seen = new Set();

        // ── Strategy 1: structured <table> with semester headings ──────────
        document.querySelectorAll('table').forEach(table => {
            // Check if any header cell mentions Semester/Year
            const headers = [...table.querySelectorAll('th, thead td')];
            const semHeaders = headers.filter(th =>
                /semester|year/i.test(th.innerText || '')
            );
            if (semHeaders.length === 0) return;

            // Build one entry per header column
            const colMap = {};
            semHeaders.forEach(th => {
                const txt = (th.innerText || '').trim();
                colMap[th.cellIndex] = { heading: txt, subjects: [] };
            });

            // Walk body rows
            table.querySelectorAll('tbody tr, tr').forEach(tr => {
                [...tr.querySelectorAll('td')].forEach(td => {
                    const idx = td.cellIndex;
                    if (colMap[idx]) {
                        const s = (td.innerText || '').trim();
                        if (s && s.length < 200 && !/semester|year/i.test(s))
                            colMap[idx].subjects.push(s);
                    }
                });
            });

            Object.values(colMap).forEach(entry => {
                if (entry.subjects.length > 0 && !seen.has(entry.heading)) {
                    seen.add(entry.heading);
                    results.push(entry);
                }
            });
        });

        if (results.length > 0) return results;

        // ── Strategy 2: heading → next-sibling traversal ──────────────────
        const headings = [...document.querySelectorAll('h2, h3, h4, strong, b')];
        for (const h of headings) {
            const txt = (h.innerText || '').trim();
            if (!/semester[\s]*[\d\-ivxI]|year[\s]*[\d\-ivxI]|[\b]syllabus[\b]/i.test(txt)) continue;
            if (seen.has(txt)) continue;

            const subjects = [];

            // Walk up to 6 following siblings looking for li / td content
            let sibling = h.nextElementSibling;
            // If heading is inline (strong/b), start from parent's next sibling
            if (['STRONG','B'].includes(h.tagName) && h.parentElement)
                sibling = h.parentElement.nextElementSibling;

            let attempts = 0;
            while (sibling && attempts < 6) {
                if (/^H[23]$/.test(sibling.tagName)) break;
                const items = [...sibling.querySelectorAll('li, td')];
                if (items.length > 0) {
                    items.forEach(item => {
                        const s = (item.innerText || '').trim();
                        if (s && s.length < 200) subjects.push(s);
                    });
                    break;
                }
                // sibling itself may be a list/cell
                if (['LI','TD'].includes(sibling.tagName)) {
                    const s = (sibling.innerText || '').trim();
                    if (s && s.length < 200) subjects.push(s);
                }
                sibling = sibling.nextElementSibling;
                attempts++;
            }

            seen.add(txt);
            results.push({ heading: txt, subjects });
        }

        if (results.length > 0) return results;

        // ── Strategy 3: full-text scan ─────────────────────────────────────
        // Walk every text node; group lines under the nearest semester heading
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT, null
        );
        let currentGroup = null;
        while (walker.nextNode()) {
            const line = (walker.currentNode.textContent || '').trim();
            if (!line || line.length > 300) continue;

            if (/semester[\s]*[\d\-ivxI]|year[\s]*[\d\-ivxI]/i.test(line)) {
                if (!seen.has(line)) {
                    currentGroup = { heading: line, subjects: [] };
                    results.push(currentGroup);
                    seen.add(line);
                }
            } else if (currentGroup && line.length > 3 && line.length < 150) {
                currentGroup.subjects.push(line);
                if (currentGroup.subjects.length >= 20) currentGroup = null; // cap
            }
        }

        return results;
    }""")
    data["semesters"] = semesters

    # ── Books: heading-based + full-table scan ────────────────────────────
    books = await page.evaluate(r"""() => {
        const books = [];
        const seen = new Set();

        const addBook = t => {
            t = (t || '').trim();
            if (t && t.length > 3 && t.length < 250
                && !seen.has(t)
                && !/^(name of|sl[.]?s*no|author|book name|s[.]no)/i.test(t)) {
                seen.add(t);
                books.push(t);
            }
        };

        // 1. Heading-based search
        const headings = [...document.querySelectorAll('h2, h3, h4, strong, b')];
        const bookH = headings.find(h => /books?|reference|reading list/i.test(h.innerText || ''));
        if (bookH) {
            let el = bookH.nextElementSibling;
            if (['STRONG','B'].includes(bookH.tagName) && bookH.parentElement)
                el = bookH.parentElement.nextElementSibling;
            let count = 0;
            while (el && count < 8) {
                if (/^H[23]$/.test(el.tagName)) break;
                [...el.querySelectorAll('li, td')].forEach(n => addBook(n.innerText));
                if (['LI','TD'].includes(el.tagName)) addBook(el.innerText);
                el = el.nextElementSibling;
                count++;
            }
        }

        // 2. Tables that have "book" or "reference" in a header cell
        if (books.length === 0) {
            document.querySelectorAll('table').forEach(table => {
                const headers = [...table.querySelectorAll('th, thead td')]
                    .map(th => (th.innerText || '').toLowerCase());
                if (!headers.some(h => /book|reference|title/i.test(h))) return;
                table.querySelectorAll('tbody tr').forEach(tr => {
                    const first = tr.querySelector('td');
                    if (first) addBook(first.innerText);
                });
            });
        }

        return books.slice(0, 25);
    }""")
    data["books"] = books

    # Raw fallback text
    raw = await page.evaluate(r"""() => {
        const el = document.querySelector('main, article, #content, [class*="content"], [id*="syllabus"]');
        return el ? el.innerText.slice(0, 6000) : document.body.innerText.slice(0, 6000);
    }""")
    data["raw_syllabus_text"] = clean(raw)[:6000]

    return data


async def scrape_jobs(page) -> dict:
    """
    Extract career/job info from the main course page.
    Looks for the course career section (not CollegeDunia's job board).
    Uses heading text matching to find the right section.
    """
    data = {"job_roles": [], "salary_info": "", "recruiters": [], "raw_jobs_text": ""}

    await scroll_page(page)

    # Find job roles in a section headed by Career/Job/Scope (not a job listing board)
    jobs_data = await page.evaluate(r"""() => {
        const headings = [...document.querySelectorAll('h2, h3')];
        
        // Find the career/scope heading inside the course article (not job board)
        const careerHeading = headings.find(h => {
            const txt = h.innerText || '';
            return /career|job.*after|scope|placement|what.*job/i.test(txt)
                && !/apply now|we are looking|vacancies|openings/i.test(txt);
        });
        
        if (!careerHeading) return { job_roles: [], salary_info: '', recruiters: [] };
        
        const job_roles = [];
        const recruiters = [];
        let salary_info = '';
        
        let el = careerHeading.nextElementSibling;
        let count = 0;
        
        while (el && count < 15) {
            // Stop at next major heading
            if (el.tagName === 'H2') break;
            
            const text = (el.innerText || '').trim();
            
            // Skip job board listings (they are long and contain "APPLY NOW")
            if (/apply now|we are looking|vacancies/i.test(text)) break;
            
            // Pick up list items as job roles
            const items = [...el.querySelectorAll('li, td:first-child')];
            items.forEach(item => {
                const t = (item.innerText || '').trim();
                if (t && t.length < 150 && !/apply now/i.test(t)) job_roles.push(t);
            });
            
            // Pick up salary mention
            if (/lpa|lakhs|salary|package/i.test(text) && !salary_info) {
                salary_info = text.slice(0, 500);
            }
            
            el = el.nextElementSibling;
            count++;
        }
        
        return { job_roles: job_roles.slice(0, 20), salary_info, recruiters };
    }""")

    data["job_roles"] = jobs_data.get("job_roles", [])
    data["salary_info"] = jobs_data.get("salary_info", "")
    data["recruiters"] = jobs_data.get("recruiters", [])

    # Raw jobs text
    raw = await page.evaluate(r"""() => {
        const headings = [...document.querySelectorAll('h2, h3')];
        const careerH = headings.find(h => /career|job.*after|scope/i.test(h.innerText));
        if (!careerH) return '';
        let text = '';
        let el = careerH.nextElementSibling;
        let count = 0;
        while (el && count < 10) {
            if (el.tagName === 'H2') break;
            text += ' ' + (el.innerText || '');
            el = el.nextElementSibling;
            count++;
        }
        return text.slice(0, 3000);
    }""")
    data["raw_jobs_text"] = clean(raw)

    return data


async def scrape_faq(page) -> list[dict]:
    """
    FAQ section — expand accordion items and extract Q&A pairs.
    The answer text requires expanding the accordion; we wait after clicking.
    """
    faqs = []

    # Expand all accordion toggles
    toggles = await page.query_selector_all(
        "button[class*='accordion'], "
        "div[class*='faq'] button, "
        "[data-toggle='collapse'], "
        "div[class*='ques'] button, "
        "li[class*='faq'] button"
    )
    for toggle in toggles:
        try:
            await toggle.click()
            await page.wait_for_timeout(400)
        except Exception:
            pass

    # Wait for content to render after expanding
    await page.wait_for_timeout(1000)

    # Extract Q&A pairs using JS — find question elements and their answer siblings
    faq_data = await page.evaluate(r"""() => {
        const faqs = [];
        // Questions typically have "Ques." prefix on CollegeDunia
        const allText = [...document.querySelectorAll('*')];
        const qEls = allText.filter(el => 
            (el.innerText || '').startsWith('Ques.') && 
            el.children.length === 0
        );
        
        for (const q of qEls) {
            const question = (q.innerText || '').trim();
            // The answer is usually in the next sibling or parent's next sibling
            let ansEl = q.parentElement && q.parentElement.nextElementSibling;
            let answer = '';
            if (ansEl) {
                const txt = (ansEl.innerText || '').trim();
                // Skip if it's just a metadata label like "Top Answer By X"
                if (txt && !/^[●][\s]*Top Answer By/i.test(txt)) {
                    answer = txt.slice(0, 800);
                }
            }
            faqs.push({ question, answer });
        }
        return faqs.slice(0, 15);
    }""")

    faqs = faq_data if faq_data else []

    # If nothing found, fall back to question-only list
    if not faqs:
        q_texts = await all_texts(
            page,
            "div[class*='faq'] h3, div[class*='faq'] h4, "
            "section[id*='faq'] h3"
        )
        faqs = [{"question": q, "answer": ""} for q in q_texts[:15]]

    return faqs


# ---------------------------------------------------------------------------
# Post-process: parse syllabus & books from highlights when page gives nothing
# ---------------------------------------------------------------------------

def parse_syllabus_from_highlights(highlights: list[str]) -> tuple[list[dict], list[str]]:
    """
    CollegeDunia's highlights array already contains semester/subject data
    as plain-text rows.  Parse them into structured semesters and books.

    Handles rows like:
      "Semester 1  Financial Accounting, Business Mathematics, ..."
      "Semester I: Cost Accounting | Management Accounting"
      "Year 1  Microeconomics | Business Communication"
      "Books: Author – Title"
    """
    sem_pattern = re.compile(
        r'(?:semester\s*[\dIVXivx]+|year\s*[\dIVXivx]+)',
        re.IGNORECASE
    )
    book_pattern = re.compile(r'(?:books?|reference|reading list)', re.IGNORECASE)

    semesters: list[dict] = []
    books: list[str] = []
    seen_headings: set[str] = set()

    for row in highlights:
        # ── Book rows ────────────────────────────────────────────────────
        if book_pattern.search(row):
            for p in re.split(r'[|\n]', row):
                p = p.strip()
                if p and not book_pattern.match(p) and len(p) > 5:
                    books.append(p)
            continue

        # ── Semester/Year rows ───────────────────────────────────────────
        m = sem_pattern.search(row)
        if not m:
            continue

        heading = re.sub(r'\s+', ' ', m.group(0).strip()).title()
        after = row[m.end():].strip().lstrip(':–-').strip()

        if not after:
            if heading not in seen_headings:
                seen_headings.add(heading)
                semesters.append({"heading": heading, "subjects": []})
            continue

        subjects = [s.strip() for s in re.split(r'[|,\n]', after) if s.strip()]
        subjects = [s for s in subjects if len(s) > 3 and not s.isdigit()]

        if heading not in seen_headings:
            seen_headings.add(heading)
            semesters.append({"heading": heading, "subjects": subjects})
        elif subjects:
            for sem in semesters:
                if sem["heading"] == heading:
                    sem["subjects"].extend(subjects)
                    break

    return semesters, books


# ---------------------------------------------------------------------------
# Main per-URL scraper
# ---------------------------------------------------------------------------

async def scrape_course(context, branch_name: str, url: str) -> dict:
    page = await context.new_page()
    result = {
        "branch_name": branch_name,
        "url": url,
        "overview": {},
        "syllabus": {},
        "jobs": {},
        "faq": [],
        "error": None,
    }

    try:
        print(f"  → {branch_name[:60]}")

        # ── Overview ────────────────────────────────────────────────────────
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        await scroll_page(page)
        result["overview"] = await scrape_overview(page)

        # ── Syllabus ────────────────────────────────────────────────────────
        syllabus_url = url.rstrip("/") + "/syllabus"
        await page.goto(syllabus_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        result["syllabus"] = await scrape_syllabus(page)

        # Fallback: if /syllabus page gave no structured data, parse from highlights
        # (CollegeDunia embeds all semester/book data in the highlights array already)
        syl = result["syllabus"]
        highlights = result["overview"].get("highlights", [])
        if not syl.get("semesters") and highlights:
            fb_semesters, fb_books = parse_syllabus_from_highlights(highlights)
            if fb_semesters:
                syl["semesters"] = fb_semesters
                syl["source"] = "highlights_fallback"
            if not syl.get("books") and fb_books:
                syl["books"] = fb_books

        # ── Jobs / Careers ──────────────────────────────────────────────────
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)
        result["jobs"] = await scrape_jobs(page)

        # ── FAQ ─────────────────────────────────────────────────────────────
        result["faq"] = await scrape_faq(page)

        print(f"     ✓ semesters={len(result['syllabus'].get('semesters', []))} "
              f"jobs={len(result['jobs'].get('job_roles', []))} "
              f"faqs={len(result['faq'])}")

    except Exception as e:
        result["error"] = str(e)
        print(f"     ✗ Error: {e}")

    finally:
        await page.close()

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    branches = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            branches.append((row["branch_name"].strip(), row["url"].strip()))

    print(f"Found {len(branches)} B.Com branches to scrape.\n")
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        # Override navigator.webdriver to reduce bot detection
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        for branch_name, url in branches:
            result = await scrape_course(context, branch_name, url)
            results.append(result)
            await asyncio.sleep(2)

        await browser.close()

    # Save JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON saved → {OUTPUT_JSON}")

    # Save flat CSV
    flat_rows = []
    for r in results:
        flat_rows.append({
            "branch_name": r["branch_name"],
            "url": r["url"],
            "overview_intro": (r["overview"].get("intro") or "")[:500],
            "highlights_count": len(r["overview"].get("highlights", [])),
            "admission": (r["overview"].get("admission") or "")[:300],
            "top_colleges": " | ".join(r["overview"].get("top_colleges_mentioned", [])),
            "semesters_count": len(r["syllabus"].get("semesters", [])),
            "books": " | ".join(r["syllabus"].get("books", [])),
            "job_roles": " | ".join(r["jobs"].get("job_roles", [])),
            "salary_info": (r["jobs"].get("salary_info") or "")[:300],
            "recruiters": " | ".join(r["jobs"].get("recruiters", [])),
            "faq_count": len(r["faq"]),
            "faq_questions": " | ".join(q["question"] for q in r["faq"][:5]),
            "error": r.get("error") or "",
        })

    if flat_rows:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(flat_rows[0].keys()))
            writer.writeheader()
            writer.writerows(flat_rows)
        print(f"✅ CSV saved → {OUTPUT_CSV}")

    errors = [r for r in results if r.get("error")]
    print(f"\nSummary: {len(results) - len(errors)} OK, {len(errors)} errors")


if __name__ == "__main__":
    asyncio.run(main())