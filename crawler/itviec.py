"""ITviec crawler — Phase 1: chỉ liệt kê thông tin (URL công việc + dữ liệu cơ bản).

Mô tả chi tiết về mô tả công việc hay công nghệ sẽ được thu thập trong Giai đoạn 2 thông qua tệp fetch_details.py.
"""

import argparse
import random
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError

from bs4 import BeautifulSoup

from crawler.base import timestamp, write_jsonl
from crawler.browser import browser

BASE = "https://itviec.com/"
LIST_URL = "https://itviec.com/it-jobs"


def parse_card(card: BeautifulSoup) -> dict | None:
    """Parse one `div.job-card` into a raw listing record."""
    title_a = card.select_one("h3 a")
    if not title_a:
        return None

    job_url = urljoin(BASE, title_a.get("href", "").split("?")[0])
    title = title_a.get_text(strip=True)

    company = None
    company_a = card.select_one("span.ims-2 a.text-rich-grey")
    if company_a:
        company = company_a.get_text(strip=True)

    posted = None
    posted_span = card.select_one("span.small-text.text-dark-grey")
    if posted_span:
        posted = " ".join(posted_span.get_text(" ", strip=True).split())
        posted = posted.removeprefix("Posted ")

    salary = None
    salary_a = card.select_one("div.salary a")
    if salary_a:
        salary = salary_a.get_text(strip=True)

    role_type = None
    role_a = card.select_one("a[title][href^='/it-jobs/']")
    if role_a:
        role_type = role_a.get_text(strip=True)

    location = None
    loc_div = card.select_one("div.text-rich-grey.text-truncate.text-nowrap")
    if loc_div:
        location = loc_div.get("title")

    work_model = None
    wm_div = card.select_one("div.text-rich-grey.flex-shrink-0")
    if wm_div:
        work_model = wm_div.get_text(strip=True)

    skills = [
        a.get_text(strip=True)
        for a in card.select("a[data-responsive-tag-list-target='tag']")
    ]

    return {
        "site": "itviec",
        "job_url": job_url,
        "title": title,
        "company": company,
        "location": location,
        "work_model": work_model,
        "role_type": role_type,
        "salary": salary,
        "posted_at": posted,
        "skills": skills,
    }


def fetch_page(page, page_num: int) -> list[dict]:
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("div.job-card", timeout=60000)
    soup = BeautifulSoup(page.content(), "lxml")
    return [row for c in soup.select("div.job-card") if (row := parse_card(c))]


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl ITviec job listings (Phase 1)")
    parser.add_argument("--pages", type=int, default=32)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--out", type=str, default="data/raw/itviec.jsonl")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists():
        parser.error(f"{out} đã tồn tại; dùng --out khác để không xóa dữ liệu cũ.")
    out.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with browser(headless=args.headless) as b:
        page = b.new_page()
        for page_num in range(1, args.pages + 1):
            rows = fetch_page(page, page_num)
            if not rows:
                break
            if args.limit and total + len(rows) > args.limit:
                rows = rows[: args.limit - total]
            write_jsonl(out, timestamp(rows))
            total += len(rows)
            print(f"page {page_num}: {len(rows)} jobs (total {total})", flush=True)
            if args.limit and total >= args.limit:
                break
            time.sleep(random.uniform(2, 5))

    print(f"saved {total} jobs to {out}")


if __name__ == "__main__":
    main()
