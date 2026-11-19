"""Công cụ debug: lưu HTML trang LinkedIn đang mở để xem selector đúng.

Chạy: python -m crawler.dump_linkedin
Đợi trang danh sách việc làm hiện ra, bấm Enter trong terminal -> lưu data/linkedin_dump.html
"""

from crawler.browser import persistent_context
from crawler.linkedin import PROFILE_DIR, SEARCH_URL

with persistent_context(PROFILE_DIR, headless=False) as ctx:
    page = ctx.new_page()
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
    input("Khi danh sách việc làm đã hiện ra, bấm Enter ở đây... ")
    html = page.content()
    with open("data/linkedin_dump.html", "w", encoding="utf-8") as fh:
        fh.write(html)
    print("url:", page.url)
    print("đã lưu", len(html), "ký tự -> data/linkedin_dump.html")
