"""Thu thập tin tuyển dụng CNTT từ API tìm kiếm của VietnamWorks."""

import argparse
import json
import random
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

import requests
from bs4 import BeautifulSoup

if __package__:
    from .base import timestamp, write_jsonl
else:
    from base import timestamp, write_jsonl

ENDPOINT = "https://ms.vietnamworks.com/job-search/v1.0/search"

BODY = {
    "userId": 0,
    "query": "",
    "filter": [
        {"field": "jobFunction", "value": '[{"parentId":5,"childrenIds":[-1]}]'}
    ],
    "ranges": [],
    "order": [],
    "hitsPerPage": 50,
    "page": 0,
    "retrieveFields": [
        "address",
        "benefits",
        "jobTitle",
        "salaryMax",
        "isSalaryVisible",
        "jobLevelVI",
        "isShowLogo",
        "salaryMin",
        "companyLogo",
        "userId",
        "jobLevel",
        "jobLevelId",
        "jobId",
        "jobUrl",
        "companyId",
        "approvedOn",
        "approvedOnText",
        "isAnonymous",
        "alias",
        "expiredOn",
        "industries",
        "industriesV3",
        "workingLocations",
        "services",
        "companyName",
        "salary",
        "onlineOn",
        "onlineOnText",
        "simpleServices",
        "visibilityDisplay",
        "isShowLogoInSearch",
        "priorityOrder",
        "skills",
        "profilePublishedSiteMask",
        "jobDescription",
        "jobRequirement",
        "prettySalary",
        "requiredCoverLetter",
        "languageSelectedVI",
        "languageSelected",
        "languageSelectedId",
        "typeWorkingId",
        "createdOn",
        "isAdrLiteJob",
        "applicantSignal",
        "numOfApplications",
        "numberOfRecruits",
        "exposureStatus",
        "isTopPriorityV4",
        "topPriorityType",
    ],
    "summaryVersion": "",
    "variants": "test",
}


def clean_html(html: str) -> str: # trả về văn bản
    """chuyển một đoạn HTML thành văn bản thuần"""
    if not html:
        return "" # Trường hợp None có thể xảy ra khi API thiếu trường mô tả
    return BeautifulSoup(html, "lxml").get_text("\n", strip=True)


"""nhận một tin tuyển dụng từ API VietnamWorks và trả về một dictionary đã chuẩn hóa"""
def parse_job(j: dict) -> dict: # trả về dictionnary. j là dictionary chứa dữ liệu của một tin
    job_url = j.get("jobUrl")
    # kiểm tra điều kiện nếu như link công việc thiếu dữ kiện hoặc None thì tự điền và ghép phần bị thiếu vào link
    if not job_url and j.get("alias") and j.get("jobId"):
        job_url = f"https://www.vietnamworks.com/{j['alias']}-{j['jobId']}-jv" 

    # lấy địa chỉ làm việc 
    locs = j.get("workingLocations") or []
    location = locs[0].get("cityNameVI") or locs[0].get("address") if locs else None

    # lấy lương công việc
    salary = j.get("prettySalary")
    if not salary and j.get("isSalaryVisible") and j.get("salaryMax"):
        salary = f"{j.get('salaryMin') or ''}-{j.get('salaryMax')}".strip("-")

    # lấy skill của công việc
    skills = [s.get("skillName") for s in (j.get("skills") or []) if s.get("skillName")]

    # công ty
    industries = [
        i.get("industryV3NameVI")
        for i in (j.get("industriesV3") or [])
        if i.get("industryV3NameVI")
    ]

    # phần mô tả công việc
    description = "\n".join(
        [
            clean_html(j.get("jobDescription")),
            clean_html(j.get("jobRequirement")),
        ]
    ).strip()
    
    return {
        "site": "vietnamworks",
        "job_url": job_url,
        "title": j.get("jobTitle"),
        "company": j.get("companyName"),
        "location": location,
        "salary": salary,
        "experience_level": j.get("jobLevel"),
        "posted_at": j.get("approvedOnText"),
        "skills": skills,
        "description": description,
        "date_posted": j.get("createdOn"),
        "industries": industries,
    }


"""chuẩn hóa"""
def fetch_page(session: requests.Session, page_num: int) -> list[dict]:

    body = {**BODY, "page": page_num}

    # Gửi yêu cầu và kiểm tra lỗi HTTP
    response = session.post(ENDPOINT, json=body, timeout=20)
    response.raise_for_status()

    # Đọc phản hồi và lấy danh sách tin
    payload = response.json()
    jobs = payload.get("data") if isinstance(payload, dict) else None

    # Kiểm tra dữ liệu có đúng cấu trúc
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise ValueError("API không trả về danh sách tin tuyển dụng trong trường 'data'.")
    
    return [parse_job(job) for job in jobs]


"""lưu danh sách tin tuyển dụng vào file"""
def write_jobs(out: Path, jobs: list[dict]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=out.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        if out.suffix.lower() == ".jsonl": # out.suffix.lower() lấy đuôi file và chuyển về chữ thường
            write_jsonl(temporary, jobs)
        else:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(jobs, file, ensure_ascii=False, indent=2)
                file.write("\n")
        temporary.replace(out)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:

    # tạo test chạy bằng CLI
    parser = argparse.ArgumentParser(description="Thu thập tin tuyển dụng CNTT từ API VietnamWorks")
    parser.add_argument("--pages", type=int, default=16, help="Số trang tối đa (đặt giá trị mặc định: 16)")
    parser.add_argument("--limit", type=int, default=0, help="Số tin tối đa (0: theo số trang)")
    parser.add_argument("--out", type=Path, default=Path("data/raw/vietnamworks.jsonl"))
    args = parser.parse_args()

    if args.pages < 1 or args.limit < 0:
        parser.error("--pages phải lớn hơn 0 và --limit phải từ 0 trở lên.")

    # Tạo nơi chứa tin và chuẩn bị gọi API
    jobs_by_url = {}
    with requests.Session() as session:
        session.headers["Accept"] = "application/json"
        for page_num in range(args.pages):
            if page_num:
                time.sleep(random.uniform(1, 3))

            # Lấy danh sách tin của một trang
            try:
                rows = timestamp(fetch_page(session, page_num))
            except (requests.RequestException, ValueError) as exc:
                parser.exit(1, f"Lỗi khi tải trang {page_num + 1}: {exc}\n")
            if not rows:
                break

            # Duyệt từng tin và loại trùng theo URL
            previous_total = len(jobs_by_url)
            for row in rows:
                if row["job_url"]:
                    jobs_by_url.setdefault(row["job_url"], row)
                if args.limit and len(jobs_by_url) >= args.limit:
                    break

            # thông báo tiến độ
            total = len(jobs_by_url)
            print(f"Trang {page_num + 1}: thêm {total - previous_total} tin (tổng {total})", flush=True)
            if args.limit and total >= args.limit:
                break

    jobs = list(jobs_by_url.values())
    write_jobs(args.out, jobs)
    print(f"Đã lưu {len(jobs)} tin vào {args.out}")


if __name__ == "__main__":
    main()
