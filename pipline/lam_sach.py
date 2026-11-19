"""Bước 2.1 – Chuẩn hóa từng trường của tin tuyển dụng (Data Cleaning).

Module thuần hàm: không đọc/ghi file, không có main(). Mỗi hàm nhận một dòng tin thô
(hoặc một chuỗi) và trả về giá trị đã chuẩn hóa, nên gọi lại được từ notebook để
trình bày trước/sau cho từng bước.

Phần 2.1 của đề được chia làm ba module, mỗi module một nhóm việc:
  pipline/clean_salary.py        lương: regex -> Luong_Min/Luong_Max, quy đổi USD->VND, "Thỏa thuận"
  pipline/lam_sach.py  (file này) địa điểm, số năm kinh nghiệm, cấp bậc, ngày đăng,
                                 hình thức làm việc, phúc lợi, ngôn ngữ, tên công ty
  pipline/extract_tech_skill_.py kỹ năng: từ điển data/skill_dictionary.json -> tech / keywords

pipline/export_data.py chỉ còn việc ghép ba nguồn trên, chốt phạm vi IT, gom nhóm trùng và ghi CSV.

Quy ước (chi tiết đầy đủ ở docstring của export_data.py):
- Ô trống = nguồn không cung cấp hoặc không suy ra được; không bao giờ điền 0.
- Chuỗi so khớp đã qua normalize() của filter_it (bỏ dấu, chữ thường), trừ BENEFIT_RE
  so khớp CÓ DẤU vì bỏ dấu thì "thưởng" = "thường".
- Hàm nào cũng trả thêm "nguồn" (field / title / jd / relative / years...) để bước phân lớp
  biết đặc trưng nào đã dùng tạo nhãn mà loại ra, tránh rò rỉ nhãn.
"""

import re
import unicodedata
from datetime import datetime, timedelta, timezone

from pipline.filter_it import normalize


CITIES = {  # tên chuẩn -> cách viết sau normalize() (không dấu, chữ thường)
    "Hà Nội": ("ha noi", "hanoi"),
    "TP.HCM": ("ho chi minh", "hcm", "sai gon", "saigon", "thu duc"),
    "Đà Nẵng": ("da nang", "danang"),
    "Remote": ("remote", "tu xa"),
}
CITY_RE = {name: re.compile(r"\b(?:" + "|".join(aliases) + r")\b") for name, aliases in CITIES.items()}

# Xét từ cao xuống thấp: "Senior Team Lead" -> Lead. Manager/Head gộp vào Lead vì đề chỉ có 5 mức.
# shortcut: "Junior/Middle" lấy mức cao hơn; nếu cần chính xác hơn thì xem lại bằng tay các title có 2 mức.
LEVEL_RE = [
    ("Lead", r"\b(?:lead|leader|truong nhom|principal|head|manager|truong phong|director|giam doc)\b"),
    ("Senior", r"\b(?:senior|sr|cap cao)\b"),
    ("Middle", r"\b(?:middle|mid|mid-level|intermediate)\b"),
    ("Junior", r"\b(?:junior|jr)\b"),
    ("Fresher", r"\b(?:fresher|intern|internship|thuc tap|thuc tap sinh|trainee|entry level|moi tot nghiep)\b"),
]
# PM/PO: "Product Manager" là vai trò, không phải cấp Lead. PM/PO so khớp chữ hoa trên title gốc ("po" thường là chữ khác).
PM_PO_RE = re.compile(r"\b(?:product manager|project manager|product owner|scrum master)\b")
PM_PO_ACRONYM = re.compile(r"\b(?:PM|PO)\b")
# Chỉ các dạng "trưởng ..." chỉ chức vụ: chữ "truong" trần còn là "thị trường", "ra trường", "tăng trưởng".
PM_PO_LEAD = r"\b(?:lead|leader|head|principal|director|giam doc|truong (?:nhom|phong|bo phan|du an|ban))\b"
# C-level (CTO/CIO/CISO) xếp Lead, nhưng loại các chức danh trợ lý / báo cáo cho CTO.
C_LEVEL_RE = re.compile(r"\b(?:cto|cio|ciso|chief (?:technology|information|information security|data|digital|ai) officer)\b")
NOT_LEAD_CONTEXT = re.compile(r"\b(?:assistant|tro ly|report(?:ing)? to)\b")
INTERN_RE = re.compile(r"\b(?:intern|internship|thuc tap|thuc tap sinh|tts|cong tac vien|ctv|trainee)\b")
# TopCV card gắn huy hiệu "Pro" sát tên công ty (vd. "ProTập đoàn FPT"), không ảnh hưởng "PropNex"/"Profectus".
TOPCV_PRO_RE = re.compile(r"^Pro(?=[A-ZÀ-ỸĐ])")
SOURCE_LEVEL = {  # nhãn cấp bậc VietnamWorks / TopDev
    "intern/student": "Fresher", "fresher/entry level": "Fresher",
    "manager": "Lead", "director and above": "Lead", "chuyen vien cap cao": "Senior",
}
# "3+ years of experience", "2-4 năm kinh nghiệm", "Kinh nghiệm: ít nhất 3 năm"
# \b trước số để "110+ years" không bị cắt thành "10+".
YEARS = r"\b(\d{1,2})(?:\s*(?:-|~|to|den)\s*(\d{1,2}))?\s*\+?\s*(?:years?|yrs?|nam)\b"
EXP_RE = [
    re.compile(YEARS + r"[^.\n]{0,25}?(?:experience|kinh nghiem)"),
    re.compile(r"(?:experience|kinh nghiem)[^.\n\d]{0,25}?" + YEARS),
]
# Ưu tiên tìm số năm kinh nghiệm từ mục Yêu cầu/Requirements để tránh nhầm tuổi thành lập công ty ở phần Giới thiệu.
REQ_SECTION_RE = re.compile(r"\b(?:yeu cau|your skills|requirements|qualifications|what you bring|who you are|must[- ]have)\b")
COMPANY_AGE_RE = re.compile(r"\b(?:company|founded|backed by|experts with|operations spanning|history of|global technology experience)\b")
# Không dùng \bN[1-3]\b cho tiếng Nhật: trên dữ liệu nó chỉ thêm 1 tin và là "N2/N3 support" (mức hỗ trợ).
# So khớp trên JD đã normalize() (không dấu, chữ thường); thứ tự khóa = thứ tự trong cột languages.
LANGUAGE_RE = {
    "English": re.compile(r"\b(?:english|tieng anh|ielts|toeic)\b"),
    "Japanese": re.compile(r"\b(?:japanese|tieng nhat|jlpt)\b"),
    "Korean": re.compile(r"\b(?:korean|tieng han|topik)\b"),
    "Chinese": re.compile(r"\b(?:chinese|tieng trung|hsk)\b"),
}
# Tiêu đề mục do mẫu trang sinh ra, so khớp CẢ DÒNG sau normalize(). Tiêu đề con không có ở đây ("Kỹ năng cần có",
# "Phụ cấp", "Preferred Qualifications"...) là nội dung của mục đang đọc; tiêu đề trong OTHER_HEADINGS kết thúc mục.
REQ_HEADINGS = {
    "yeu cau ung vien", "candidate requirements", "yeu cau cong viec", "your skills and experience",
    "your skills & qualifications", "requirements", "job requirements", "basic requirements", "qualifications",
    "minimum qualifications",
}
BENEFIT_HEADINGS = {
    "quyen loi ung vien", "quyen loi", "phuc loi", "benefits", "why you'll love working here",
    "cac phuc loi danh cho ban", "what we offer",
}
OTHER_HEADINGS = {
    "mo ta cong viec", "job description", "the job", "top 3 reasons to join us", "dia diem va thoi gian",
    "dia diem lam viec", "thoi gian lam viec", "location", "work schedule", "address and time work", "xem them",
    "your role & responsibilities", "key responsibilities", "responsibilities", "what you'll do", "about us",
    "about the role", "additional information", "equal opportunity employer", "eeo statement", "company overview",
}
# So khớp trên văn bản có dấu, chữ thường; một tin có thể thuộc nhiều nhóm. Thứ tự khóa = thứ tự trong cột benefits.
BENEFIT_RE = {name: re.compile(pattern) for name, pattern in {
    "Lương tháng 13": r"(?:lương|thưởng) (?:tháng )?(?:thứ )?13|tháng (?:lương )?(?:thứ )?13|13th[- ]?month|13th salary",
    "Thưởng (KPI/dự án/lễ tết)": r"thưởng|bonus",
    "Xét tăng lương định kỳ": r"(?:xét|review|điều chỉnh|tăng|đánh giá) lương|salary (?:review|increase|adjustment)"
                              r"|performance review",
    "Bảo hiểm / chăm sóc sức khỏe": r"bảo hiểm (?:sức kho[ẻe]|y tế tư nhân|chăm sóc|cao cấp|pti|bảo việt|bảo minh)"
                                    r"|(?:health|healthcare|medical|premium|private) insurance|chăm sóc sức kho[ẻe]",
    "Khám sức khỏe định kỳ": r"khám sức kho[ẻe]|health check|medical check",
    "BHXH / chế độ theo luật": r"bhxh|bảo hiểm xã hội|social insurance|labou?r (?:law|code)|luật lao động",
    "Du lịch / team building": r"du lịch|team ?building|company trip|nghỉ mát|outing|year[- ]end party|tiệc cuối năm",
    # shortcut: chữ "laptop" trần còn khớp nhiệm vụ ("sửa máy tính, laptop") nếu JD đặt nhiệm vụ trong mục phúc lợi.
    "Laptop / thiết bị": r"laptop|macbook|máy tính xách tay|(?:cấp|trang bị|cung cấp) (?:máy tính|thiết bị)",
    "Đào tạo / chứng chỉ": r"đào tạo|training|khoá học|khóa học|chứng chỉ|certification|learning",
    "Nghỉ phép có lương": r"nghỉ phép|ngày phép|phép năm|annual leave|paid (?:time off|leave)|days? off|\bpto\b",
    "Làm việc linh hoạt / hybrid": r"hybrid|remote|work from home|\bwfh\b|linh hoạt|flexible",
    "Phụ cấp / ăn trưa / đưa đón": r"phụ cấp|trợ cấp|allowance|ăn trưa|bữa trưa|lunch|căn[- ]tin|canteen|xe đưa đón"
                                   r"|shuttle",
}.items()}
# Hình thức làm việc trong JD (sau normalize, dấu câu -> khoảng trắng). Xét Hybrid -> Remote -> Onsite vì tin Hybrid
# hay ghi cả "tại văn phòng 3 ngày, remote 2 ngày". Chỉ nhận cụm có ngữ cảnh làm việc; KHÔNG lấy "hybrid cloud/app",
# "remote desktop/teams/data", "hỗ trợ từ xa", "onsite tại khách hàng", "travel onsite", "on-site abroad", "onsite allowance".
WEEKDAY = r"(?:working )?(?:mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|thu [2-7])"
AWAY = r"(?:wfh|work from home|remote(?:ly)?|lam viec tu xa)"
# Chặn các cụm kỹ thuật đi sau "hybrid" (vd. "mô hình hybrid on-prem/cloud", "hybrid search").
HYBRID_TECH_AFTER = r"(?! (?:cloud|on ?prem\w*|app\w*|mobile|search|retrieval|ranking|lakehouse|infra\w*|arch\w*|env\w*|casual|delivery))"
WORK_MODE_RE = [
    ("Hybrid", re.compile(
        # Không có \b đầu: JD LinkedIn dính chữ giữa các gạch đầu dòng ("bonushybrid working12 days of leave").
        r"hybrid (?:work\w*|model|mode|schedule|set ?up|policy|arrangement)|hybrid (?:and|&) flexible"
        rf"|\b(?:lam viec|lam|work|working|mo hinh|hinh thuc|che do|working arrangement|work model|working model)"
        rf" (?:theo |in |a )?(?:mo hinh |hinh thuc )?hybrid\b{HYBRID_TECH_AFTER}"
        rf"|\b{AWAY}(?: \w+){{0,3}} (?:per|a|each|moi|1|ngay|days?) (?:week|tuan|month|thang)\b"  # "2 days WFH per week"
        rf"|\b0?[1-4] (?:remote|wfh) (?:days?|ngay)\b"  # "1 WFH day per week"
        rf"|\b0?[1-4] (?:remote |wfh )?(?:days?|ngay) (?:of )?(?:{AWAY}|on site|onsite|in office)\b"
        rf"|\b(?:{AWAY}|tai van phong|in (?:the )?office|on site|onsite) (?:up to |toi da )?[1-4] (?:days?|ngay)\b"
        rf"|\b(?:{AWAY}|on site|onsite)(?: wfh)? (?:on |every )?{WEEKDAY}|\b(?:thu 7|saturdays?)(?: co the)? {AWAY}\b"
        r"|\b(?:che do|chinh sach) wfh\b|\bwfh (?:dinh ky|policy|as needed|culture is flexible|and flexible)\b"
        r"|\bwork from home (?:options?|policy|days?|in shift)\b|\bco the wfh\b"
        # Được phép làm từ xa hoặc tự chọn giữa remote và văn phòng -> Hybrid.
        r"|\b(?:co the |khi (?:ban )?)lam viec tu xa\b|\blam viec tu xa linh hoat\b"
        r"|\b(?:option|flexibility) to work (?:remotely|from home)\b"
        r"|\bchoose between remote(?: work)? (?:or|and) working (?:from|at|in) (?:the )?office\b"
    )),
    ("Remote", re.compile(
        r"\b(?:fully|100%|100) remote\b(?! weeks?\b)|\bremote 100%|\bremote (?:first|only|role|position|job)\b"
        r"|\bwork(?:ing)? (?:fully )?remotely\b|\bwork from anywhere\b|\blam viec (?:hoan toan |100% )?tu xa\b"
    )),
    ("Onsite", re.compile(
        r"\b(?:lam viec|work(?:ing)?) (?:100% |truc tiep |full ?time )*(?:tai|at|in|from) (?:the |our )?(?:van phong|office)\b"
        r"|\b(?:100% |fully )(?:on site|onsite)\b|\b(?:on site|onsite) (?:only|100%|5 days)\b"
    )),
]
VN_TZ = timezone(timedelta(hours=7))
# posted_at sau normalize(): "5 days ago", "dang2 tuan truoc" (TopCV dính chữ), "dang lai 3 ngay truoc".
AGO_RE = re.compile(r"(\d+)\s*(phut|minute|gio|hour|ngay|day|tuan|week|thang|month|nam|year)s?\b")
AGO_DAYS = {"phut": 1 / 1440, "minute": 1 / 1440, "gio": 1 / 24, "hour": 1 / 24, "ngay": 1, "day": 1,
            "tuan": 7, "week": 7, "thang": 30, "month": 30, "nam": 365, "year": 365}


def iso_timestamp(value: str) -> str:
    """'17/11/2026 18:29:12' (base.timestamp) -> '2026-11-17T18:29:12+07:00'."""
    return datetime.strptime(value, "%d/%m/%Y %H:%M:%S").isoformat() + "+07:00"


def jd_sections(description) -> dict:
    """{"requirements": văn bản | None, "benefits": văn bản | None}: các dòng gốc từ tiêu đề mục tới tiêu đề kế tiếp."""
    lines = {"requirements": [], "benefits": []}
    current = None
    for line in (description or "").split("\n"):
        key = normalize(line).replace("’", "'").strip(" :")
        if key in REQ_HEADINGS:
            current = "requirements"
        elif key in BENEFIT_HEADINGS:
            current = "benefits"
        elif key in OTHER_HEADINGS:
            current = None
        elif current and line.strip():
            lines[current].append(line.strip())
    return {name: "\n".join(found) or None for name, found in lines.items()}


def benefits(section) -> dict:
    """{"benefits": [nhóm phúc lợi theo thứ tự BENEFIT_RE], "n_benefits": số nhóm}; không có mục phúc lợi -> None."""
    if not section:
        return {"benefits": None, "n_benefits": None}
    text = unicodedata.normalize("NFC", section).casefold()
    found = [name for name, pattern in BENEFIT_RE.items() if pattern.search(text)]
    return {"benefits": found, "n_benefits": len(found)}


def posted_date(row: dict) -> tuple:
    """(ngày đăng ISO, nguồn): date_posted của nguồn ("field"), không có thì ước lượng từ posted_at ("relative")."""
    value = row.get("date_posted")
    if value:
        if str(value).isdigit():  # LinkedIn createdAt: epoch mili giây
            day = datetime.fromtimestamp(int(value) / 1000, VN_TZ).date()
        else:  # ITviec/TopDev "2026-11-10"; VietnamWorks "2026-11-17T10:20:50.000Z" (UTC -> giờ VN)
            parsed = datetime.fromisoformat(str(value))
            day = (parsed.astimezone(VN_TZ) if parsed.tzinfo else parsed).date()
        return day.isoformat(), "field"
    text = normalize(row.get("posted_at") or "")
    crawled = datetime.strptime(row["Timestamp"], "%d/%m/%Y %H:%M:%S")
    if "hom nay" in text:
        return crawled.date().isoformat(), "relative"
    if match := AGO_RE.search(text):
        days = int(match.group(1)) * AGO_DAYS[match.group(2)]
        return (crawled - timedelta(days=days)).date().isoformat(), "relative"
    return None, None


def parse_cities(location) -> list[str]:
    text = normalize(location or "")
    found = sorted((match.start(), name) for name, pattern in CITY_RE.items() if (match := pattern.search(text)))
    names = [name for _, name in found]
    return names or (["Khác"] if text else [])


def work_mode(row: dict) -> tuple:
    """(Onsite/Hybrid/Remote, nguồn): trường work_model (ITviec) -> title/location -> JD (WORK_MODE_RE)."""
    model = {"at office": "Onsite", "hybrid": "Hybrid", "remote": "Remote"}.get(normalize(row.get("work_model") or ""))
    if model:
        return model, "field"
    text = normalize(f"{row.get('title') or ''} {row.get('location') or ''}")
    if re.search(r"\bremote\b", text):
        return "Remote", "title"
    if re.search(r"\bhybrid\b", text):
        return "Hybrid", "title"
    jd = " ".join(re.sub(r"[^\w%]+", " ", normalize(row.get("description") or "")).split())  # "on-site" -> "on site"
    for mode, pattern in WORK_MODE_RE:
        if pattern.search(jd):
            return mode, "jd"
    return None, None


def clean_company(name, site: str = "") -> str:
    text = " ".join((name or "").split())
    if site == "topcv":
        text = TOPCV_PRO_RE.sub("", text)
    return text


def is_intern(title) -> bool:
    return bool(INTERN_RE.search(normalize(title or "")))


def _exp_from_text(text: str) -> tuple | None:
    for pattern in EXP_RE:
        for match in pattern.finditer(text):
            low = int(match.group(1))
            if low > 15:
                continue
            window = text[max(0, match.start() - 60):match.end() + 30]
            if COMPANY_AGE_RE.search(window):
                continue
            high = match.group(2) and int(match.group(2))
            return (low, high, "jd") if high is None or high >= low else (low, None, "jd")
    return None


def parse_experience(row: dict) -> tuple:
    """(min năm, max năm, nguồn). Trường TopCV trước -> mục Yêu cầu của JD -> toàn JD -> ITviec experience_months."""
    level = normalize(row.get("experience_level") or "")
    if level == "khong yeu cau":
        return 0, None, "field"
    if match := re.fullmatch(r"(duoi|tren)?\s*(\d{1,2}) nam", level):  # TopCV: "2 năm", "Dưới 1 năm", "Trên 5 năm"
        years = int(match.group(2))
        return {"duoi": (0, years), "tren": (years, None)}.get(match.group(1), (years, years)) + ("field",)
    jd = normalize(row.get("description") or "")
    if jd:
        req = REQ_SECTION_RE.search(jd)
        if req and (found := _exp_from_text(jd[req.start():])):
            return found
        if found := _exp_from_text(jd):
            return found
    if row.get("experience_months") is not None:
        # ITviec JSON-LD monthsOfExperience chỉ có 3 mốc 10/37/61 = "từ 1/3/5 năm" (201 tin mốc 10: JD ghi 1-5 năm,
        # không tin nào 0 năm), nên xếp sau JD và làm tròn: 10 -> 1 năm, không phải 10 // 12 = 0 (Fresher).
        return round(int(row["experience_months"]) / 12), None, "field"
    return None, None, None


def is_pm_po(title) -> bool:
    title = title or ""
    return bool(PM_PO_RE.search(normalize(title)) or PM_PO_ACRONYM.search(title))


def seniority(row: dict, exp_min) -> tuple:
    title = normalize(row.get("title") or "")
    pm_po = is_pm_po(row.get("title"))
    if C_LEVEL_RE.search(title) and not NOT_LEAD_CONTEXT.search(title):
        return "Lead", "title"
    for level, pattern in LEVEL_RE:
        if pm_po and level == "Lead":
            pattern = PM_PO_LEAD
        if re.search(pattern, title):
            return level, "title"
    level = SOURCE_LEVEL.get(normalize(row.get("experience_level") or ""))
    if level and not (pm_po and level == "Lead"):
        return level, "level_field"
    if exp_min is not None:
        level = "Fresher" if exp_min < 1 else "Junior" if exp_min < 3 else "Middle" if exp_min < 5 else "Senior"
        return level, "years"
    return None, None


def languages(description: str) -> dict:
    """{"languages": [ngôn ngữ JD có nhắc tới, theo thứ tự LANGUAGE_RE], "n_languages": số phần tử}.

    Không có JD -> cả hai None (không biết); JD không nhắc ngôn ngữ nào -> [] và 0.
    """
    text = normalize(description or "")
    if not text:
        return {"languages": None, "n_languages": None}
    found = [name for name, pattern in LANGUAGE_RE.items() if pattern.search(text)]
    return {"languages": found, "n_languages": len(found)}
