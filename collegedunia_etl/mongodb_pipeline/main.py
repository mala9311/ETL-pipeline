#!/usr/bin/env python3
"""
Unified ETL Pipeline for Collegedunia Data Scraping
====================================================

This script orchestrates the execution of all scrapers:
- Colleges scraper (colleges.py)
- Courses scraper (courses.py)
- Exams scraper (exams.py)


All scraped data is stored in MongoDB with proper error handling,
logging, and progress tracking.

Usage:
    python main.py                              # Run all scrapers sequentially
    python main.py --scrapers colleges          # Run only colleges scraper
    python main.py --scrapers exams             # Run only exams scraper
    python main.py --scrapers courses
    python main.py --scrapers colleges,exams    # Run both scrapers
    python main.py --scrapers courses           # Run only courses scraper
    python main.py --scrapers colleges,courses,exams  # Run all scrapers
    python main.py --parallel                   # Run all scrapers in parallel
    python main.py --test-db                    # Test MongoDB connection and exit
    python main.py --scrapers exams --limit 10  # Run exams scraper with a URL limit
"""

import asyncio
import argparse
import os
import sys
import time
import traceback
from datetime import datetime
from typing import List, Dict, Any, Optional
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

# Add the repo root (parent of collegedunia_etl) to Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Import individual scrapers
from collegedunia_etl.mongodb_pipeline.colleges import main as colleges_main
from collegedunia_etl.mongodb_pipeline.courses import main as courses_main
from collegedunia_etl.mongodb_pipeline.exams import main as exams_main
from collegedunia_etl.utils.logger import get_logger

# ----------------- Configuration ----------------- #
class Config:
    """Configuration settings for the ETL pipeline."""

    # MongoDB Configuration
    MONGO_URI = os.getenv("MONGO_URI_LOCAL", "mongodb://localhost:27017/")
    DB_NAME = "collegedunia"

    # Collection names
    COLLECTIONS = {
        "colleges": "College",
        "courses": "Course",
        "exams": "exam_sections",
        "logs": "scraper_logs"
    }

    # Timeout settings
    MONGO_TIMEOUT = 30_000   # 30 seconds
    SCRAPER_TIMEOUT = 28_800  
    
 
    # Retry settings
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds


# ----------------- Logger Setup ----------------- #
logger = get_logger("main_etl")


# ----------------- MongoDB Helpers ----------------- #
def get_mongo_client(uri: str) -> MongoClient:
    """Return a MongoClient for the given URI."""
    return MongoClient(uri, serverSelectionTimeoutMS=Config.MONGO_TIMEOUT)


def test_mongodb_connection() -> bool:
    """Test MongoDB connection and return True if successful."""
    try:
        client = get_mongo_client(Config.MONGO_URI)
        client.admin.command("ping")
        logger.info("✅ MongoDB connection successful")
        return True
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected MongoDB error: {e}")
        return False


def get_database_stats() -> Dict[str, int]:
    """Return current document counts for each scraper collection."""
    try:
        client = get_mongo_client(Config.MONGO_URI)
        db = client[Config.DB_NAME]
        return {
            name: db[col].count_documents({})
            for name, col in Config.COLLECTIONS.items()
            if name != "logs"
        }
    except Exception as e:
        logger.error(f"Failed to get database stats: {e}")
        return {}


def log_scraper_event(scraper: str, status: str, message: str) -> None:
    """Insert a single log entry into the scraper_logs collection."""
    try:
        client = get_mongo_client(Config.MONGO_URI)
        db = client[Config.DB_NAME]
        db[Config.COLLECTIONS["logs"]].insert_one({
            "scraper": scraper,
            "status": status,
            "message": message,
            "logged_at": datetime.utcnow()
           
        })
    except Exception as e:
        logger.warning(f"Could not write log entry to MongoDB: {e}")


# ----------------- Scraper Execution ----------------- #
async def run_scraper(
    scraper_name: str,
    scraper_func,
    timeout: int = Config.SCRAPER_TIMEOUT,
    extra_kwargs: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Run a single scraper coroutine with a timeout.
    extra_kwargs are forwarded to the scraper function (e.g. limit for exams).
    """
    start_time = time.time()
    logger.info(f"🚀 Starting {scraper_name} scraper...")

    try:
        kwargs = extra_kwargs or {}
        coro = scraper_func(**kwargs) if kwargs else scraper_func()
        await asyncio.wait_for(coro, timeout=timeout)

        duration = time.time() - start_time
        logger.info(f"✅ {scraper_name} scraper completed in {duration:.2f}s")

        return {
            "scraper": scraper_name,
            "status": "success",
            "duration": duration,
            "start_time": datetime.utcnow(),
            "error": None
        }

    except asyncio.TimeoutError:
        duration = time.time() - start_time
        error_msg = f"Scraper timed out after {timeout}s"
        logger.error(f"⏰ {scraper_name} scraper {error_msg}")
        return {
            "scraper": scraper_name,
            "status": "timeout",
            "duration": duration,
            "start_time": datetime.utcnow(),
            "error": error_msg
        }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(f"❌ {scraper_name} scraper failed: {error_msg}")
        logger.error(traceback.format_exc())
        return {
            "scraper": scraper_name,
            "status": "error",
            "duration": duration,
            "start_time": datetime.utcnow(),
            "error": error_msg
        }

def _build_scraper_map(exam_limit: Optional[int]) -> Dict[str, Dict[str, Any]]:
    """
    Return a mapping of scraper_name → {func, kwargs}.
    The exams scraper accepts an optional limit_override argument.
    """
    return {
        "colleges": {
            "func": colleges_main,
            "kwargs": {}
        },
        "courses": {
            "func": courses_main,
            "kwargs": {}
        },
        "exams": {
            "func": exams_main,
            "kwargs": {"limit_override": exam_limit}
        }
    }


async def run_scrapers_sequential(
    scrapers_to_run: List[str],
    scraper_map: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Run the requested scrapers one after another."""
    logger.info("🔄 Running scrapers sequentially...")
    results = []

    for name in scrapers_to_run:
        if name not in scraper_map:
            logger.warning(f"⚠️  Unknown scraper: {name}")
            continue

        entry = scraper_map[name]
        result = await run_scraper(name, entry["func"], extra_kwargs=entry["kwargs"])
        results.append(result)

        log_scraper_event(
            result["scraper"],
            result["status"],
            f"Duration: {result['duration']:.2f}s | Error: {result['error'] or 'None'}"
        )

    return results

async def run_scrapers_parallel(
    scrapers_to_run: List[str],
    scraper_map: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Run the requested scrapers concurrently."""
    logger.info("⚡ Running scrapers in parallel...")

    valid = [n for n in scrapers_to_run if n in scraper_map]
    unknown = [n for n in scrapers_to_run if n not in scraper_map]
    for name in unknown:
        logger.warning(f"⚠️  Unknown scraper: {name}")

    tasks = [
        run_scraper(name, scraper_map[name]["func"], extra_kwargs=scraper_map[name]["kwargs"])
        for name in valid
    ]

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    processed = []
    for i, result in enumerate(raw_results):
        if isinstance(result, Exception):
            result = {
                "scraper": valid[i],
                "status": "error",
                "duration": 0,
                "start_time": datetime.utcnow(),
                "error": str(result)
            }
        processed.append(result)
        log_scraper_event(
            result["scraper"],
            result["status"],
            f"Duration: {result['duration']:.2f}s | Error: {result['error'] or 'None'}"
        )

    return processed


# ----------------- Main Pipeline ----------------- #
async def main_pipeline(
    scrapers: List[str],
    parallel: bool = False,
    exam_limit: Optional[int] = None
) -> Dict[str, Any]:
    """Main ETL pipeline orchestrator for Collegedunia."""
    pipeline_start = time.time()

    logger.info("=" * 80)
    logger.info("🎯 COLLEGEDUNIA ETL PIPELINE STARTED")
    logger.info(f"📅 Start time  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"🔧 Scrapers    : {', '.join(scrapers)}")
    logger.info(f"⚡ Parallel    : {'Yes' if parallel else 'No'}")
    if exam_limit is not None:
        logger.info(f"🔢 Exam limit  : {exam_limit}")
    logger.info("=" * 80)

    # Verify MongoDB is reachable before doing any work
    if not test_mongodb_connection():
        logger.error("❌ Cannot proceed without MongoDB connection")
        return {
            "status": "failed",
            "error": "MongoDB connection failed",
            "scrapers_run": [],
            "total_duration": 0
        }

    initial_stats = get_database_stats()
    logger.info(f"📊 Initial database stats: {initial_stats}")

    scraper_map = _build_scraper_map(exam_limit)

    if parallel:
        results = await run_scrapers_parallel(scrapers, scraper_map)
    else:
        results = await run_scrapers_sequential(scrapers, scraper_map)

    final_stats = get_database_stats()
    total_duration = time.time() - pipeline_start

    successful = [r for r in results if r["status"] == "success"]
    failed     = [r for r in results if r["status"] != "success"]

    summary = {
        "status": "success" if not failed else "partial",
        "total_duration": total_duration,
        "scrapers_run": scrapers,
        "results": results,
        "successful_count": len(successful),
        "failed_count": len(failed),
        "initial_stats": initial_stats,
        "final_stats": final_stats,
        "data_added": {
            s: final_stats.get(s, 0) - initial_stats.get(s, 0)
            for s in scrapers
        }
    }

    # ---- Print summary ---- #
    logger.info("=" * 80)
    logger.info("📋 ETL PIPELINE SUMMARY")
    logger.info(f"⏱️  Total duration      : {total_duration:.2f}s")
    logger.info(f"✅ Successful scrapers : {len(successful)}")
    logger.info(f"❌ Failed scrapers     : {len(failed)}")

    if successful:
        logger.info("✅ Successful:")
        for r in successful:
            logger.info(f"   - {r['scraper']}: {r['duration']:.2f}s")

    if failed:
        logger.info("❌ Failed:")
        for r in failed:
            logger.info(f"   - {r['scraper']}: {r['error']}")

    logger.info("📊 Records added this run:")
    for scraper, count in summary["data_added"].items():
        logger.info(f"   - {scraper}: {count} records")

    logger.info("=" * 80)

    return summary


# ----------------- CLI Interface ----------------- #
def parse_arguments():
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Collegedunia ETL Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                                    # Run colleges + courses + exams sequentially
  python main.py --scrapers colleges                # Run only colleges scraper
  python main.py --scrapers courses                 # Run only courses scraper
  python main.py --scrapers exams                   # Run only exams scraper
  python main.py --scrapers colleges,courses,exams  # Run all scrapers
  python main.py --parallel                         # Run both scrapers in parallel
  python main.py --scrapers exams --limit 20        # Scrape only first 20 exam URLs
  python main.py --test-db                          # Test MongoDB connection and exit
        """
    )

    parser.add_argument(
        "--scrapers",
        type=str,
        default="colleges,courses,exams",
        help="Comma-separated list of scrapers to run (default: colleges,courses,exams)"
    )

    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run scrapers concurrently instead of sequentially"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit the number of exam URLs processed (exams scraper only)"
    )

    parser.add_argument(
        "--test-db",
        action="store_true",
        help="Test MongoDB connection, print stats, and exit"
    )

    return parser.parse_args()


# ----------------- Entry Point ----------------- #
async def main():
    """Async entry point — parses CLI args then runs the pipeline."""
    args = parse_arguments()

    # ---- Quick DB check mode ---- #
    if args.test_db:
        if test_mongodb_connection():
            print(f"Database stats: {get_database_stats()}")
            sys.exit(0)
        else:
            sys.exit(1)

    # ---- Validate requested scrapers ---- #
    valid_scrapers = ["colleges", "courses", "exams"]
    scrapers = [s.strip() for s in args.scrapers.split(",")]

    invalid = [s for s in scrapers if s not in valid_scrapers]
    if invalid:
        logger.error(f"❌ Invalid scraper(s): {invalid}")
        logger.error(f"   Valid options: {valid_scrapers}")
        sys.exit(1)

    # ---- Run pipeline ---- #
    try:
        summary = await main_pipeline(
            scrapers=scrapers,
            parallel=args.parallel,
            exam_limit=args.limit
        )

        if summary["status"] == "success":
            logger.info("🎉 ETL Pipeline completed successfully!")
            sys.exit(0)
        else:
            logger.warning("⚠️  ETL Pipeline completed with some failures")
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("⏹️  Pipeline interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"💥 Pipeline failed with unexpected error: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())