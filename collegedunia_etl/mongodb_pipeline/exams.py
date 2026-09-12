import sys
import os
import asyncio
import pandas as pd
from pymongo import MongoClient
from tqdm import tqdm
from datetime import datetime, timezone
from playwright.async_api import async_playwright

##--------- Setting path to import other files ---------##
DIR = os.path.dirname(__file__)
PACKAGE_ROOT = os.path.abspath(os.path.join(DIR, '..'))
DATA_ROOT = os.path.abspath(os.path.join(DIR, '..', '..'))   ## repo root containing scraped_data
# To import `collegedunia_etl.*`, Python must see the parent directory of `collegedunia_etl`.
if DATA_ROOT not in sys.path:
    sys.path.insert(0, DATA_ROOT)

from collegedunia_etl.exam.New_Scraper.final_exam_section import full_exam_section as fe_module
from collegedunia_etl.exam.New_Scraper.all_exam_urls import collect_exam_urls as urls_module
from collegedunia_etl.exam.New_Scraper.constants import EXAM_LISTING_URL, OUTPUT_CSV_PATH
from collegedunia_etl.exam.New_Scraper.logger import get_logger, LOG_FILE


DEFAULT_CSV = os.path.join(DATA_ROOT, OUTPUT_CSV_PATH)  # uses path from constants: scraped_data/exam_urls.csv
MONGO_URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017/")
DB_NAME = "collegedunia"    # database name
FRONTEND_COLLECTION_NAME = "rawExamData"
LIMIT = None  # None means all the exam urls

##--------- Fn to override the Limit of urls passing (override the LIMIT we set above) ---------##
def _exam_urls_limit():
    if len(sys.argv) > 1:
        try:
            val = sys.argv[1]
            if val.lower() in ('none', 'all', '0', ''):
                return None      ## If used "python exams_collegedunia.py KEYWORD" on terminal then it will run all the urls (KEYWORDS are 'none', 'all', '0', '')   Eg: python .\collegedunia_etl\exam\exams_collegedunia.py all
            return int(val)
        except Exception:
            return LIMIT
    return LIMIT   ## Else it will execute the LIMIT we gave above in variable

##--------- Connects to MongoDB ---------##
def connect_mongo(uri, db_name):
    client = MongoClient(uri)
    return client[db_name]

##--------- To convert title/exam_name into filename for saving data into MongoDB ---------##
def slug_from_title(title):
    if not isinstance(title, str):
        title = str(title or "")  ## Just for error handling: if title is numeric, it will be converted to string
    slug = ''.join(ch.lower() if ch.isalnum() else '-' for ch in title).strip('-')
    slug = '-'.join([p for p in slug.split('-') if p])
    return slug or 'unknown exam'   # If title is empty then return unknown exam

##--------- Saving Logs to MongoDB ---------##
def upload_log_to_mongo(db, logger, pipeline_summary=None, events=None, note=None):
    log_file = LOG_FILE
    content = ""
    if os.path.isfile(log_file):  ## This if statement will read and then save the current log file in content variable
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.exception(f"Failed to read log file content: {e}")

    ## Dict of data that we got from log file that will get saved into mongodb
    doc = {
        "log_filename"    : os.path.basename(log_file),   # log file name
        "log_path"        : log_file,                      # log file path
        "content"         : content,                       # text content of log
        "note"            : note,                          # to know how many exams were successfully processed
        "pipeline_summary": pipeline_summary or {},        # statistics about the running of file or details about empty dict
        "events"          : events or [],                  # list of information about exams and errors that took place
        "inserted_at"     : datetime.now(timezone.utc)    # timestamp to know when the log was saved
    }

    ## the above doc dict will be inserted into scraper_logs collection in mongodb
    try:
        db['scraper_logs'].insert_one(doc)
        logger.info("Log file + pipeline summary uploaded to MongoDB successfully")
        return True
    except Exception as e:
        logger.exception(f"Failed to upload log file and pipeline summary: {e}")
        return False

##--------- Async fn to process single url using final_exam_section.py file ---------##
async def process_one_exam(browser, url, slug, logger):
    page = await browser.new_page()
    try:
        logger.info(f"Processing: {url}  (slug: {slug})")
        combined = await fe_module(page, url, EXAM=slug)
        return combined
    except Exception as e:
        logger.exception(f"Error processing {url}: {e}")
        return None
    finally:
        try:
            await page.close()
        except Exception:
            pass

##--------- Loads the exam_urls from csv file (all_exam_urls.py) ---------##
async def main(limit_override=None, csv_path=DEFAULT_CSV, mongo_uri=MONGO_URI, db_name=DB_NAME):
    logger = get_logger("collegedunia_mongodb")
    logger.info("Starting CollegeDunia mongodb run")

    rows = []
    if os.path.exists(csv_path):  # checks for csv file if it exists or not
        try:
            df = pd.read_csv(csv_path)
            if 'URL' not in df.columns:  # checks for URL column in csv file and if not it will log the error
                logger.error("CSV does not have a 'URL' column.")
                return   # if no URL column, it logs error and exits
            titles = df['Title'].fillna('').tolist() if 'Title' in df.columns else [''] * len(df)
            urls = df['URL'].dropna().tolist()
            rows = [(titles[i] if i < len(titles) else '', urls[i]) for i in range(len(urls))]
            logger.info(f"Loaded {len(rows)} rows from existing CSV: {csv_path}")
        except Exception as e:
            logger.exception(f"Failed to read CSV {csv_path}: {e}")
            return

    else:  # if no csv file is found the all_exam_urls.py file will run and scrape the urls and create csv file.
        logger.info("CSV not found — scraping exam urls using all_exam_urls module...")
        try:
            results = await urls_module(EXAM_LISTING_URL)
            if not results:
                logger.error("Scraper returned no exam URLs.")
                return
            os.makedirs(os.path.dirname(csv_path), exist_ok=True)
            pd.DataFrame(results).to_csv(csv_path, index=False, encoding='utf-8')
            titles = [r.get('Title', '') for r in results]
            urls = [r.get('URL') for r in results]
            rows = [(titles[i] if i < len(titles) else '', urls[i]) for i in range(len(urls))]
            logger.info(f"Scraped {len(rows)} exam URLs and wrote CSV to {csv_path}")
        except Exception as e:
            logger.exception(f"Failed to scrape exam URLs: {e}")
            return

    ## this will apply the row limit overriding the default LIMIT set above using limit_override fn argument else it will run for urls we set from index 0 to limit
    limit = limit_override if limit_override is not None else LIMIT
    if limit:
        rows = rows[:limit]

    ## runs connect_mongo fn then connects to mongodb and logs it
    db = connect_mongo(mongo_uri, db_name)
    frontend_collection = db[FRONTEND_COLLECTION_NAME]
    logger.info(f"Connected to MongoDB at {mongo_uri}, DB: {db_name}")

##--------- Launches the playwright in async mode ---------##
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)   # change headless to 'False' for debugging

        processed = 0  # counts no. of exams successfully processed
        pipeline_start_time = datetime.now(timezone.utc)  # timestamp record for when pipeline started
        pipeline_events = []  # list of dicts for processing of each exam
        total_rows = len(rows)  # no. of exams that are going to run
        failures = 0  # count of exams failed to create content

        ## Loop through all the exams with progress bar using tqdm
        for title, url in tqdm(rows, desc="Exams"):
            try:
                if not url:  # skips url if url is empty
                    logger.warning("Skipping empty URL row")
                    continue
                slug = slug_from_title(title) if title else (url.rstrip('/').split('/')[-1] or slug_from_title(url))  # if Title is missing in csv file this slug will create slug using slug_from_title fn
                ## running process_one_exam fn
                combined = await process_one_exam(browser, url, slug, logger)
                if combined:  # it will help us to create structure in which the information will get saved after scraping in mongodb
                    try:
                        # CollegeDunia scraper returns sections as a list of {name, url, data} dicts
                        # Store as-is — the transformer reads this list format directly
                        sections_list = combined.get("sections", [])
                        section_dict = {
                            sec["name"]: sec.get("data",{})
                            for sec in sections_list
                        }

                        # doc structure in which the data is saved in mongodb
                        doc = {
                            "Exam Url"   : url,
                            "Exam Name"  : title or "",
                            "Sections"   : section_dict,  # list of {name, url, data} dicts
                            "inserted_at": datetime.now(timezone.utc)
                        }

                        # frontend_doc = build_frontend_exam_document(doc)

                        db['exam_sections'].delete_many({"Exam Url": url})  # first deletes existing doc in exam_sections with same URL to avoid duplicate records
                        db['exam_sections'].insert_one(doc)  # Then inserting new one
                       
                        logger.info(f"Inserted combined exam document for {slug} / {url}")  # logs that insert
                        processed += 1  # increasing the processed count

                        # to record events for exam in pipeline events list stored as part of log in mongodb at the end
                        pipeline_events.append({
                            "url"       : url,
                            "title"     : title or "",
                            "slug"      : slug,
                            "status"    : "inserted",  # for successful appending of the exams
                            "error"     : None,         # No error occurred
                            "timestamp" : datetime.now(timezone.utc)
                        })

                    except Exception as e:
                        logger.exception(f"Failed to write combined exam for {slug}: {e}")
                # if combined variable is None i.e. scraping has failed we run the below else statement to store the log about failure into mongodb
                else:
                    logger.warning(f"No combined content for {slug}; skipping DB insert")
                    failures += 1
                    pipeline_events.append({
                        "url"       : url,
                        "title"     : title or "",
                        "slug"      : slug,
                        "status"    : "no_content",          # scraping failed or returned empty
                        "error"     : "no combined content",
                        "timestamp" : datetime.now(timezone.utc)
                    })

            except Exception as e:
                # error handling if process_one_exam fn fails due to some error during processing of url
                logger.exception(f"Unexpected error processing row ({title}, {url}): {e}")
                failures += 1
                pipeline_events.append({
                    "url"       : url,
                    "title"     : title or "",
                    "slug"      : slug if 'slug' in locals() else "",
                    "status"    : "error",
                    "error"     : str(e),
                    "timestamp" : datetime.now(timezone.utc)
                })

        # after processing all exams, final summary of error logs will get saved to mongodb
        pipeline_end_time = datetime.now(timezone.utc)
        pipeline_summary = {
            "start_time"       : pipeline_start_time,
            "end_time"         : pipeline_end_time,
            "duration_seconds" : (pipeline_end_time - pipeline_start_time).total_seconds(),
            "total_rows"       : total_rows,
            "processed"        : processed,
            "failures"         : failures,
            "limit_used"       : limit
        }
        # format in which this failed log will get saved in mongodb
        upload_log_to_mongo(
            db,
            logger,
            pipeline_summary=pipeline_summary,
            events=pipeline_events,
            note=f"Processed {processed} exams (limit = {limit})"
        )

        try:
            await browser.close()
        except Exception:
            pass

    logger.info("Completed CollegeDunia mongodb run")


if __name__ == "__main__":
    cli_limit = _exam_urls_limit()
    asyncio.run(main(limit_override=cli_limit))