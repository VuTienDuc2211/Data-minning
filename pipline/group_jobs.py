"""Đánh dấu cùng một việc làm đăng trên nhiều nền tảng (không xóa dòng nào).

Gọi từ export_data.py: assign_groups(clean_rows) thêm hai khóa
- job_group_id: "g" + 8 ký tự sha1 của job_uid nhỏ nhất trong nhóm (ổn định giữa các lần chạy);
  dòng không trùng với ai vẫn có nhóm riêng.
- is_primary: đúng một dòng mỗi nhóm được giữ khi EDA/mô hình cần mỗi việc làm một dòng.

Quy tắc ghép (duplicate.py đã khử trùng trong cùng nền tảng, nên chỉ ghép hai dòng KHÁC source):
- chia khối theo từ đầu tiên của tên công ty đã chuẩn hóa (giảm số cặp phải so);
- cùng việc làm khi fuzz.token_set_ratio(công ty) >= 90 và fuzz.token_sort_ratio(tiêu đề) >= 90;
- các cặp gộp bằng union-find theo điểm tiêu đề giảm dần (rồi điểm công ty), nên A~B và B~C cho nhóm A, B, C;
- một nhóm không bao giờ có hai dòng cùng source: cặp nào làm hai nhóm có chung source thì bỏ qua,
  vì cặp giống hơn đã được gộp trước, dòng thừa ở lại nhóm riêng
  (ITviec "IoT Security Researcher" ghép với TopCV "IoT ...", không ghép thêm TopCV "AI ...").
"""

import hashlib
import re

from rapidfuzz import fuzz

from pipline.filter_it import normalize


COMPANY_THRESHOLD = 90
TITLE_THRESHOLD = 90
SOURCE_RANK = {"itviec": 0, "vietnamworks": 1, "topcv": 2, "linkedin": 3, "topdev": 4}

# Cụm dài đứng trước để "joint stock company" không bị cắt thành "joint stock".
COMPANY_STOPWORDS = sorted((
    "cong ty", "tnhh", "co phan", "cp", "mtv", "mot thanh vien", "jsc", "joint stock company",
    "company", "co", "ltd", "limited", "corporation", "corp", "inc", "group", "tap doan",
    "viet nam", "vietnam", "vn", "chi nhanh", "branch",
), key=len, reverse=True)
COMPANY_STOP_RE = re.compile(r"\b(?:" + "|".join(COMPANY_STOPWORDS) + r")\b")

UNIT = r"(?:trieu|tr|mil|m|k|usd|vnd|\$)"
AMOUNT = rf"\$?\s*[0-9][0-9.,]*\s*{UNIT}?"
SALARY_RE = re.compile(rf"\b(?:up\s*to|upto|thu nhap|luong)\b[^0-9$]{{0,15}}{AMOUNT}(?:\s*-\s*{AMOUNT})?")
TITLE_STOP_RE = re.compile(
    r"\b(?:ha noi|hn|hcm|ho chi minh|tp|da nang|hybrid|onsite|remote|urgent|gap)\b"
)


def norm_company(name) -> str:
    text = re.sub(r"[^\w\s]", " ", normalize(str(name or "")))
    return " ".join(COMPANY_STOP_RE.sub(" ", text).split())


def norm_title(title) -> str:
    text = re.sub(r"\[[^\]]*\]", " ", normalize(str(title or "")))
    text = SALARY_RE.sub(" ", text)
    # Bỏ dấu câu (kể cả dấu ngoặc tròn) nhưng giữ chữ bên trong: "(Java)" khác "(Golang)".
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(TITLE_STOP_RE.sub(" ", text).split())


def pair_scores(a: dict, b: dict):
    """(điểm tiêu đề, điểm công ty) nếu a, b là cùng việc làm ở hai nguồn khác nhau, ngược lại None."""
    if a["source"] == b["source"]:
        return None
    company = fuzz.token_set_ratio(a["_company"], b["_company"])
    title = fuzz.token_sort_ratio(a["_title"], b["_title"])
    return (title, company) if company >= COMPANY_THRESHOLD and title >= TITLE_THRESHOLD else None


def primary_key(row: dict) -> tuple:
    # min() theo khóa này: có JD -> có Luong_TB -> nhiều tech -> JD dài -> nguồn ưu tiên -> job_uid.
    return (not row["has_jd"], row["Luong_TB"] is None, -row["n_tech"], -row["jd_chars"],
            SOURCE_RANK.get(row["source"], len(SOURCE_RANK)), row["job_uid"])


def assign_groups(rows: list[dict]) -> list[dict]:
    """Trả về bản sao các dòng kèm job_group_id và is_primary; không sửa dòng đầu vào."""
    keyed = [{**row, "_company": norm_company(row.get("company")), "_title": norm_title(row.get("title"))}
             for row in rows]

    parent = list(range(len(keyed)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    blocks = {}
    for i, row in enumerate(keyed):
        if row["_company"] and row["_title"]:  # tên rỗng sau chuẩn hóa thì không đủ căn cứ để ghép
            blocks.setdefault(row["_company"].split()[0], []).append(i)
    pairs = []
    for members in blocks.values():
        for x, i in enumerate(members):
            for j in members[x + 1:]:
                if scores := pair_scores(keyed[i], keyed[j]):
                    title, company = scores
                    pairs.append((-title, -company, keyed[i]["job_uid"], keyed[j]["job_uid"], i, j))
    sources = {i: {row["source"]} for i, row in enumerate(keyed)}  # nguồn có trong nhóm, giữ ở gốc của nhóm
    for *_, i, j in sorted(pairs):  # cặp giống nhất trước; job_uid để thứ tự ổn định khi bằng điểm
        a, b = find(i), find(j)
        if a != b and not sources[a] & sources[b]:
            parent[a] = b
            sources[b] |= sources.pop(a)

    groups = {}
    for i in range(len(keyed)):
        groups.setdefault(find(i), []).append(i)
    result = [dict(row) for row in rows]
    for members in groups.values():
        group_id = "g" + hashlib.sha1(min(rows[i]["job_uid"] for i in members).encode()).hexdigest()[:8]
        primary = min(members, key=lambda i: primary_key(rows[i]))
        for i in members:
            result[i]["job_group_id"] = group_id
            result[i]["is_primary"] = i == primary
    return result
