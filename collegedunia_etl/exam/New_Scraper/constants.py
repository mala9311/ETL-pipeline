# Base listing url
BASE_URL         = "https://collegedunia.com"
EXAM_LISTING_URL = "https://collegedunia.com/exams"

# Timeout / retry values 
PAGE_TIMEOUT        = 60000
MAX_RETRIES         = 3
RETRY_DELAY_SECONDS = 2

# Output CSV path
OUTPUT_DIR      = "scraped_data"
OUTPUT_CSV_PATH = "scraped_data/exam_urls.csv"

# Optional header / user-agent 
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
EXTRA_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
}  
# CSS selectors — found by inspecting collegedunia.com/exams
EXAM_CARD_SELECTOR       = ".exam-card.exam-card--desktop"
EXAM_TITLE_LINK_SELECTOR = "a.text-heading"

