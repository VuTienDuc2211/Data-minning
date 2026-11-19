"""cung cấp các hàm dùng chung"""

import json
import random
import time
from pathlib import Path
from datetime import datetime

from bs4 import BeautifulSoup


def timestamp(rows: list[dict]) -> list[dict]:
    """Thêm cột `Timestamp` (giờ Việt Nam, dạng ngày/tháng/năm giờ:phút:giây) vào mỗi bản ghi."""
    for row in rows:
        row["Timestamp"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return rows


# ghi vào file jsonl
def write_jsonl(path: Path, rows) -> None:
    """định nghĩa hàm nhận đường dẫn path và một tập bản ghi có thể duyệt bằng for, path được kỳ vọng là một Path và hàm trả về None"""
    path.parent.mkdir(parents=True, exist_ok=True) # tạo thư mục chứa file
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n") # ghi chuỗi vào file


# nạp dữ liệu từ tệp JSONL vào Python để xử lý tiếp
def read_jsonl(path) -> list: 
    """Đọc tất cả các bản ghi dạng JSON-line từ một tệp (trả về danh sách trống nếu tệp không tồn tại).g)."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def interactive_scrape(page, parse_fn, out_path, *, interval=2.0, label="") -> dict:
    """Liên tục thu thập dữ liệu từ một trang web trong khi người dùng thao tác thủ công trên đó.

    Trong khi người dùng thực hiện các thao tác như đăng nhập, cuộn trang hoặc nhấp vào nút "trang tiếp theo" trên trình duyệt, 
    vòng lặp này sẽ liên tục đọc lại DOM sau mỗi khoảng thời gian `interval` (giây), loại bỏ các mục trùng lặp dựa trên `job_url`, rồi cập nhật tệp tại `out_path` với toàn bộ dữ liệu đã thu thập được. 
    Chương trình sẽ tiếp tục chạy cho đến khi nhận được tín hiệu ngắt từ bàn phím (`KeyboardInterrupt`)
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    collected = {}
    if out.exists():
        for row in read_jsonl(out):
            if row.get("job_url"):
                collected[row["job_url"]] = row
    try:
        while True:
            try:
                soup = BeautifulSoup(page.content(), "lxml")
                for row in parse_fn(soup):
                    url = row.get("job_url")
                    if url and url not in collected:
                        collected[url] = row
                with out.open("w", encoding="utf-8") as fh:
                    for row in collected.values():
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"[{label}] {len(collected)} unique jobs", flush=True)
            except Exception as exc:  # noqa: BLE001 - navigation in progress, keep scraping
                print(f"[{label}] scrape err: {str(exc)[:60]}", flush=True)
            time.sleep(interval + random.random())
    except KeyboardInterrupt:
        pass
    print(f"[{label}] done: {len(collected)} jobs -> {out}")
    return collected