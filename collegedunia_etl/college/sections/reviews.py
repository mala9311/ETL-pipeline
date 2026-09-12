import asyncio
import json
from playwright.async_api import async_playwright


def clean_text(text):
    return [t.strip() for t in text.split("\n") if t.strip()]


def deduplicate(lines):
    seen = set()
    out = []
    for l in lines:
        if l not in seen:
            out.append(l)
            seen.add(l)
    return out


def extract_sections(lines):
    sections = {
        "overall": "",
        "placement": "",
        "infrastructure": "",
        "faculty": "",
        "hostel": ""
    }

    for line in lines:
        l = line.lower()

        if "overall" in l:
            sections["overall"] += line + " "

        elif "placement" in l:
            sections["placement"] += line + " "

        elif "infrastructure" in l:
            sections["infrastructure"] += line + " "

        elif "faculty" in l:
            sections["faculty"] += line + " "

        elif "hostel" in l:
            sections["hostel"] += line + " "

    # strip extra spaces
    for k in sections:
        sections[k] = sections[k].strip()

    return sections


def extract_rating(lines):
    # ⚠️ Placeholder logic (since CollegeDunia does not consistently expose real ratings)
    rating = {
        "placement": "",
        "infrastructure": "",
        "faculty": "",
        "hostel": ""
    }

    for line in lines:
        l = line.lower()

        if "placement" in l:
            rating["placement"] = "5"

        if "infrastructure" in l:
            rating["infrastructure"] = "5"

        if "faculty" in l:
            rating["faculty"] = "5"

        if "hostel" in l:
            rating["hostel"] = "5"

    return rating


async def scrape_reviews(page, url):
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)

    # scroll to load content
    for _ in range(10):
        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(1500)

    # broad selector (works best for this site)
    cards = await page.query_selector_all("div[class*='review'], div[class*='Review']")

    print("Found raw blocks:", len(cards))

    results = []
    seen_titles = set()

    for card in cards:
        try:
            text = await card.inner_text()

            if not text or len(text) < 120:
                continue

            # skip container blocks
            if "Why To Join" in text or "Write a Review" in text:
                continue

            lines = deduplicate(clean_text(text))

            if not lines:
                continue

            title = lines[0]

            # skip invalid titles
            if len(title) < 3 or title.replace(".", "").isdigit():
                continue

            # deduplicate reviews
            if title in seen_titles:
                continue
            seen_titles.add(title)

            sections = extract_sections(lines)
            rating = extract_rating(lines)

            # skip empty reviews
            if not any(sections.values()):
                continue

            results.append({
                "title": title,
                "sections": sections,
                "rating": rating
            })

        except Exception as e:
            print("Error:", e)

    return {"reviews": results}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        url = "https://collegedunia.com/university/25356-iit-hyderabad-indian-institute-of-technology-iith-hyderabad/reviews"

        data = await scrape_reviews(page, url)

        with open("clean_reviews.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        await browser.close()

        print("Completed reviews:", len(data["reviews"]))


if __name__ == "__main__":
    asyncio.run(main())