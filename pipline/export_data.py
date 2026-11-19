"""Xuất hai file nộp: raw_data.csv (văn bản gốc) và clean_data.csv (đã chuẩn hóa cho EDA).

Chạy từ thư mục gốc: python -m pipline.export_data
Đầu vào (chỉ đọc, cùng thứ tự job_url):
  data/full.jsonl         tin đúng phạm vi IT + JD chi tiết   (fetch_detail.py)
  data/salary.jsonl       Luong_Min/Luong_Max VND/tháng         (clean_salary.py)
  data/tech_skills.jsonl  tech / keywords theo từ điển          (extract_tech_skill_.py)

Chuẩn hóa từng trường (bước 2.1) nằm ở pipline/lam_sach.py; file này chỉ ghép, chốt phạm vi IT,
gom nhóm trùng và ghi CSV.

Quy ước cho báo cáo:
- Timestamp đổi sang ISO 8601 giờ Việt Nam (+07:00); giá trị gốc là thời điểm cào.
- Ô trống = nguồn không cung cấp hoặc không suy ra được; không bao giờ điền 0.
- List (skills, tech, keywords, cities) ghi dạng mảng JSON trong ô CSV.
- city: thành phố xuất hiện đầu tiên trong location (tin nhiều nơi tính cho nơi đầu);
  cities giữ đủ danh sách để EDA tách dòng nếu cần. Ngoài HN/HCM/ĐN/Remote -> "Khác".
- work_mode / work_mode_source: theo thứ tự trường work_model ("field", ITviec) -> title/location ("title") ->
  regex ngữ cảnh chặt trong JD ("jd", ưu tiên Hybrid -> Remote -> Onsite; tránh cụm kỹ thuật như hybrid cloud/on-prem,
  remote desktop, đi công tác onsite). Trống khi không xác định được, không tự gán Onsite.
- seniority theo thứ tự: chức danh -> nhãn cấp bậc của nguồn -> số năm kinh nghiệm.
  seniority_source ghi lý do, để bước phân lớp bỏ đặc trưng đã dùng tạo nhãn (tránh leakage).
  Nhóm PM/PO (Product/Project Manager, Product Owner, Scrum Master, PM, PO; title_role = "pm_po"):
  chữ Manager/Owner là tên vai trò, không phải cấp bậc -> chỉ gán Lead khi title có Lead/Leader/Head/
  Principal/Director/Giám đốc/Trưởng nhóm|phòng|bộ phận|dự án|ban; nhãn "Manager" của nguồn cũng không cho Lead.
  C-level (CTO/CIO/CISO/Chief ... Officer) gán Lead trừ khi có Assistant/Trợ lý/Report to.
- is_intern: True nếu tiêu đề là thực tập sinh/cộng tác viên/trainee, tiện tách riêng khi vẽ lương Fresher.
- requirements_raw / benefits_raw (raw): các dòng gốc của JD từ tiêu đề mục mẫu trang ("Yêu cầu ứng viên",
  "Your Skills and Experience", "Quyền lợi ứng viên", "Why You'll Love Working Here", "Các phúc lợi dành cho bạn"...)
  tới tiêu đề mục kế tiếp; trống khi JD không có tiêu đề đó (phần lớn LinkedIn viết tự do).
- benefits / n_benefits (clean): nhóm phúc lợi tìm thấy trong benefits_raw (BENEFIT_RE, so khớp CÓ DẤU vì bỏ dấu thì
  "thưởng" = "thường"); trống khi tin không có mục phúc lợi (không biết), [] và 0 khi có mục mà không khớp nhóm nào.
- posted_date (YYYY-MM-DD giờ VN) + posted_date_source: "field" = date_posted của nguồn (ITviec/TopDev datePosted,
  VietnamWorks createdOn, LinkedIn createdAt) = ngày đăng gốc; "relative" = ƯỚC LƯỢNG Timestamp - chuỗi "Đăng 2 tuần
  trước"/"5 days ago" (TopCV, tin ITviec thiếu JSON-LD), tuần/tháng chỉ chính xác tới đơn vị đó và là lần đăng/làm mới gần nhất.
- company (clean): bỏ huy hiệu "Pro" dính đầu tên công ty TopCV ("ProTập đoàn FPT" -> "Tập đoàn FPT"); raw giữ nguyên.
- exp_min_years: trường TopCV -> mục Yêu cầu của JD (bỏ qua tuổi công ty, chặn > 15 năm) -> toàn JD -> mốc ITviec.
- Chốt tập tin IT (it_decision / it_decision_source), xét lần lượt:
  1. manual: nhóm ghi keep/drop ở cột quyet_dinh của data/it_review.csv (thắng mọi luật, kể cả để sửa tin keep);
  2. filter: filter_it.py đã keep theo title/tag;
  3. jd_tech: tin ambiguous có >= 1 tech trong JD ngoài GENERAL_TOOLS (Excel, Jira, AutoCAD...);
  4. còn lại vẫn ambiguous và được ghi vào data/it_review.csv để đọc tay.
  Tin drop bị bỏ khỏi cả raw_data.csv lẫn clean_data.csv; quyết định đã ghi trong it_review.csv không bao giờ bị xóa.
- Luong_TB chỉ tính khi có cả Luong_Min và Luong_Max (khoảng đóng hoặc lương cố định).
- languages: mảng JSON các ngôn ngữ mà JD CÓ NHẮC TỚI, KHÔNG phải "bắt buộc" ("Korean partners",
  "Chinese is a plus" cũng tính), theo thứ tự English, Japanese, Korean, Chinese; n_languages = độ dài mảng.
  Cả hai trống khi tin không có JD (không biết); JD không nhắc ngôn ngữ nào -> [] và 0.
"""

import csv
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from crawler.base import read_jsonl
from pipline.filter_it import GENERAL_TOOLS
from pipline.group_jobs import assign_groups
from pipline.lam_sach import (benefits, clean_company, is_intern, is_pm_po, iso_timestamp, jd_sections,
                              languages, parse_cities, parse_experience, posted_date, seniority, work_mode)


FULL, SALARY, SKILLS = Path("data/full.jsonl"), Path("data/salary.jsonl"), Path("data/tech_skills.jsonl")
RAW_OUT, CLEAN_OUT = Path("raw_data.csv"), Path("clean_data.csv")
REVIEW = Path("data/it_review.csv")  # nhóm đọc tay: quyet_dinh = keep / drop, để trống = chưa đọc
REVIEW_COLUMNS = ["job_url", "source", "title", "company", "tech", "jd_dau", "quyet_dinh", "ghi_chu"]
GROUPS_OUT = Path("data/duplicate_groups.csv")  # chỉ nhóm >= 2 dòng, để kiểm tay việc ghép chéo nguồn
GROUP_COLUMNS = ["job_group_id", "source", "company", "title", "city", "Luong_TB", "is_primary", "URL_Goc"]

RAW_COLUMNS = [
    "job_uid", "source", "URL_Goc", "Timestamp",
    "title_raw", "company_raw", "location_raw",
    "salary_raw", "salary_detail_raw",
    "experience_level_raw", "experience_months_raw",
    "role_type_raw", "work_model_raw", "employment_type_raw", "industries_raw",
    "skills_raw", "posted_at_raw", "date_posted", "description_raw", "requirements_raw", "benefits_raw",
]
CLEAN_COLUMNS = [
    "job_uid", "job_group_id", "is_primary", "source", "URL_Goc", "Timestamp", "posted_date", "posted_date_source",
    "title", "company", "it_decision", "it_decision_source", "title_role",
    "city", "cities", "work_mode", "work_mode_source",
    "exp_min_years", "exp_max_years", "exp_source",
    "seniority", "seniority_source", "is_intern",
    "Luong_Min", "Luong_Max", "Luong_TB", "salary_status", "salary_currency_orig",
    "salary_period_assumed", "salary_source",
    "tech", "keywords", "n_tech", "n_keywords",
    "languages", "n_languages", "benefits", "n_benefits",
    "has_jd", "jd_chars", "description",
]


def job_uid(row: dict) -> str:
    return f"{row['site']}-{hashlib.sha1(row['job_url'].encode()).hexdigest()[:10]}"


def final_it_decision(row: dict, tech: list, manual: str | None) -> tuple:
    """(quyết định cuối, căn cứ) theo thứ tự ở docstring module: manual > filter > jd_tech > chờ đọc tay."""
    if manual:
        return manual, "manual"
    if row.get("it_decision") == "keep":
        return "keep", "filter"
    if set(tech) - GENERAL_TOOLS:
        return "keep", "jd_tech"
    return "ambiguous", None


def read_review(path: Path) -> dict:
    """job_url -> dòng của it_review.csv. File do người sửa tay nên kiểm cột và giá trị trước khi dùng."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not {"job_url", "quyet_dinh"} <= set(reader.fieldnames or []):
            sys.exit(f"{path} thiếu cột job_url/quyet_dinh: nếu sửa bằng Excel, lưu lại dạng 'CSV UTF-8 (dấu phẩy)'.")
        review = {}
        for line, row in enumerate(reader, 2):
            row["quyet_dinh"] = (row["quyet_dinh"] or "").strip().lower()
            if row["quyet_dinh"] not in ("", "keep", "drop"):
                sys.exit(f"{path} dòng {line}: quyet_dinh phải là keep, drop hoặc để trống, gặp {row['quyet_dinh']!r}")
            review[row["job_url"]] = row
    return review


def review_rows(rows: list, skills: list, review: dict) -> list:
    """Tin bộ lọc chưa quyết và JD không có tech riêng của IT, kèm quyết định đã ghi; dòng đã quyết nhưng
    không còn cần đọc (nhóm tự thêm, hoặc bộ lọc đổi) vẫn giữ để không mất công đọc."""
    pending = {}
    for row, found in zip(rows, skills):
        if row.get("it_decision") == "ambiguous" and not set(found["tech"]) - GENERAL_TOOLS:
            old = review.get(row["job_url"], {})
            pending[row["job_url"]] = {
                "job_url": row["job_url"], "source": row["site"], "title": row.get("title"),
                "company": row.get("company"), "tech": found["tech"],
                "jd_dau": " ".join((row.get("description") or "").split())[:400],
                "quyet_dinh": old.get("quyet_dinh", ""), "ghi_chu": old.get("ghi_chu", ""),
            }
    kept_old = [{key: old.get(key) for key in REVIEW_COLUMNS}
                for url, old in review.items() if url not in pending and old["quyet_dinh"]]
    # Xếp theo title để đọc liền các tin giống nhau ("Project Manager", "Business Analyst"...).
    return sorted([*pending.values(), *kept_old], key=lambda r: (r["source"] or "", r["title"] or ""))


def raw_record(row: dict) -> dict:
    sections = jd_sections(row.get("description"))
    return {
        "job_uid": job_uid(row), "source": row["site"], "URL_Goc": row["job_url"],
        "Timestamp": iso_timestamp(row["Timestamp"]),
        "title_raw": row.get("title"), "company_raw": row.get("company"), "location_raw": row.get("location"),
        "salary_raw": row.get("salary"), "salary_detail_raw": row.get("salary_detail"),
        "experience_level_raw": row.get("experience_level"), "experience_months_raw": row.get("experience_months"),
        "role_type_raw": row.get("role_type"), "work_model_raw": row.get("work_model"),
        "employment_type_raw": row.get("employment_type"), "industries_raw": row.get("industries"),
        "skills_raw": row.get("skills"), "posted_at_raw": row.get("posted_at"),
        "date_posted": row.get("date_posted"), "description_raw": row.get("description"),
        "requirements_raw": sections["requirements"], "benefits_raw": sections["benefits"],
    }


def clean_record(row: dict, salary: dict, skills: dict, decision: tuple) -> dict:
    cities = parse_cities(row.get("location"))
    exp_min, exp_max, exp_source = parse_experience(row)
    level, level_source = seniority(row, exp_min)
    posted, posted_source = posted_date(row)
    mode, mode_source = work_mode(row)
    low, high = salary["Luong_Min"], salary["Luong_Max"]
    description = " ".join((row.get("description") or "").split())
    return {
        "job_uid": job_uid(row), "source": row["site"], "URL_Goc": row["job_url"],
        "Timestamp": iso_timestamp(row["Timestamp"]),
        "posted_date": posted, "posted_date_source": posted_source,
        "title": " ".join((row.get("title") or "").split()), "company": clean_company(row.get("company"), row["site"]),
        "it_decision": decision[0], "it_decision_source": decision[1],
        "title_role": "pm_po" if is_pm_po(row.get("title")) else row.get("title_role"),
        "city": cities[0] if cities else None, "cities": cities, "work_mode": mode, "work_mode_source": mode_source,
        "exp_min_years": exp_min, "exp_max_years": exp_max, "exp_source": exp_source,
        "seniority": level, "seniority_source": level_source, "is_intern": is_intern(row.get("title")),
        "Luong_Min": low, "Luong_Max": high,
        "Luong_TB": (low + high) / 2 if low is not None and high is not None else None,
        "salary_status": salary["salary_status"], "salary_currency_orig": salary["salary_currency_orig"],
        "salary_period_assumed": salary["salary_period_assumed"],
        "salary_source": salary["salary_source"],  # salary / salary_detail / jd (khoảng trong JD, xem clean_salary)
        "tech": skills["tech"], "keywords": skills["keywords"],
        "n_tech": len(skills["tech"]), "n_keywords": len(skills["keywords"]),
        **languages(description),
        **benefits(jd_sections(row.get("description"))["benefits"]),  # JD gốc: description ở trên đã gộp dòng
        "has_jd": bool(description), "jd_chars": len(description), "description": description,
    }


def cell(value):
    """None -> ô trống, list -> mảng JSON, lương float tròn -> số nguyên VND."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float):
        return round(value)
    return value


def write_csv(path: Path, columns: list[str], records: list[dict]) -> None:
    # utf-8-sig để Excel mở đúng tiếng Việt; ghi file tạm rồi thay để Ctrl+C không làm hỏng file cũ.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=path.parent or ".",
                                     prefix=f".{path.stem}-", suffix=".csv", delete=False) as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: cell(record[key]) for key in columns} for record in records)
    Path(fh.name).replace(path)


def main() -> None:
    sys.stdout.reconfigure(errors="backslashreplace")
    for path in (FULL, SALARY, SKILLS):
        if not path.is_file():
            sys.exit(f"Thiếu {path}. Chạy lần lượt fetch_detail, clean_salary, extract_tech_skill_ trước.")
    rows, salaries, skills = read_jsonl(FULL), read_jsonl(SALARY), read_jsonl(SKILLS)
    urls = [row.get("job_url") for row in rows]
    if urls != [r.get("job_url") for r in salaries] or urls != [r.get("job_url") for r in skills]:
        sys.exit("salary.jsonl / tech_skills.jsonl không khớp full.jsonl: chạy lại clean_salary và extract_tech_skill_.")
    if len(set(urls)) != len(urls):
        sys.exit("full.jsonl có job_url trùng: chạy lại duplicate.py.")
    missing = [i for i, row in enumerate(rows, 1) if not row.get("job_url") or not row.get("Timestamp")]
    if missing:
        sys.exit(f"{len(missing)} dòng thiếu URL_Goc/Timestamp (đề bắt buộc), ví dụ dòng {missing[:5]}")

    review = read_review(REVIEW)
    decisions = [final_it_decision(row, k["tech"], review.get(row["job_url"], {}).get("quyet_dinh"))
                 for row, k in zip(rows, skills)]
    kept = [(row, s, k, d) for row, s, k, d in zip(rows, salaries, skills, decisions) if d[0] != "drop"]
    raw = [raw_record(row) for row, *_ in kept]
    clean = assign_groups([clean_record(row, s, k, d) for row, s, k, d in kept])
    sizes = Counter(r["job_group_id"] for r in clean)
    grouped = sorted((r for r in clean if sizes[r["job_group_id"]] > 1), key=lambda r: (r["job_group_id"], r["source"]))
    to_review = review_rows(rows, skills, review)
    write_csv(REVIEW, REVIEW_COLUMNS, to_review)  # ghi trước: file đang mở trong Excel thì dừng trước khi đổi CSV nộp
    write_csv(RAW_OUT, RAW_COLUMNS, raw)
    write_csv(CLEAN_OUT, CLEAN_COLUMNS, clean)
    write_csv(GROUPS_OUT, GROUP_COLUMNS, grouped)

    print("IT decision:", dict(Counter(f"{d}/{source}" for d, source in decisions).most_common()))
    print(f"Wrote {len(raw)} rows -> {RAW_OUT}, {CLEAN_OUT} (bỏ {len(rows) - len(kept)} tin drop)")
    print(f"{sum(not r['quyet_dinh'] for r in to_review)}/{len(to_review)} tin chưa đọc tay -> {REVIEW}")
    print("source:", dict(Counter(r["source"] for r in clean)))
    for column in ("city", "work_mode", "work_mode_source", "seniority", "seniority_source", "exp_source", "salary_status"):
        print(f"{column}:", dict(Counter(r[column] for r in clean).most_common()))
    print("has Luong_TB:", sum(r["Luong_TB"] is not None for r in clean),
          "| has JD:", sum(r["has_jd"] for r in clean), "| has tech:", sum(r["n_tech"] > 0 for r in clean))
    print(f"cross-source groups: {sum(n > 1 for n in sizes.values())} ({len(grouped)} rows) -> {GROUPS_OUT}",
          "| is_primary:", sum(r["is_primary"] for r in clean))


if __name__ == "__main__":
    main()
