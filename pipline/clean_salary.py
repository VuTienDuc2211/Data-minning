"""Chuẩn hóa lương về VND/tháng, giữ nguyên các trường dữ liệu gốc.

Chạy từ thư mục gốc: python -m pipline.clean_salary
Mặc định: data/full.jsonl -> data/salary.jsonl (ghi lại, không ghi nối).

Quy ước cho báo cáo:
- USD_TO_VND = 25.000 là tỷ giá cố định của bài tập, không phải tỷ giá thị trường.
- Lương năm chia 12; chỉ đổi khi biết tiền tệ và kỳ trả (từ chuỗi hoặc JSON-LD).
- Thiếu kỳ trả, kể cả "15 - 20 triệu", giữ unknown, không tự đoán theo tháng
  (ngoại lệ duy nhất: TopCV, xem dưới).
- Thương lượng/không công bố/ẩn đăng nhập dùng null, không thay bằng 0.
- Khoảng hợp lệ sau khi đổi về VND/tháng: 1.000.000 – 300.000.000 (MIN/MAX_MONTHLY_VND).
  Lương thực tập 2,5 triệu vẫn hợp lệ; "$ 1-1 /tháng", "0tr-0tr/tháng" là giá trị giả.

salary_status:
- closed_range: đủ hai cận, gồm cả lương cố định (min = max).
- min_only / max_only: "Từ X" / "Tới X"; cận còn lại là null.
- negotiable: không công bố số (thỏa thuận, cạnh tranh, attractive...), chuỗi không có chữ số.
- hidden: phải đăng nhập mới xem được lương.
- missing: không có chuỗi lương (None, rỗng, chỉ khoảng trắng).
- invalid: đọc được số nhưng ngoài khoảng hợp lệ; Luong_Min/Max = null,
  vẫn giữ salary_currency_orig / salary_period_orig.
- unknown: có chuỗi nhưng không đọc được, hoặc thiếu tiền tệ/kỳ trả.

Giả định TopCV (salary_period_assumed = True): TopCV hiển thị lương theo tháng nhưng
chuỗi không ghi kỳ trả ("15 - 20 triệu"). Trong dữ liệu, cận trên trung vị của
"a - b triệu" là 25 triệu, cao nhất 85 triệu; nếu là lương năm thì vô lý với vị trí IT.
Chỉ áp dụng cho site topcv; mọi site khác vẫn giữ unknown.
Hết là giả định khi tiêu đề/JD ghi lại ĐÚNG khoảng đó kèm "/tháng" ("Mức lương: 15 - 20 triệu/tháng").

Khoảng lương trong JD (salary_source = "jd"): khi chuỗi lương của nguồn không đủ hai cận (thỏa thuận, ẩn,
chỉ một cận, thiếu), dùng khoảng "a - b triệu/tháng" trong tiêu đề/JD nếu: có chữ "/tháng", đứng sau
lương/thu nhập/salary/income (không lấy "phụ cấp 1 - 2 triệu/tháng"), JD chỉ có ĐÚNG MỘT khoảng như vậy,
và khoảng khớp cận đã biết ("Tới 30 triệu" + JD "25 - 30 triệu/tháng"). Có thể là tổng thu nhập gồm thưởng.
"""

import argparse
import math
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

from crawler.base import read_jsonl, write_jsonl


USD_TO_VND = 25_000
# Ngoài khoảng này là giá trị giả/nhập sai, không phải lương thật (VND/tháng).
MIN_MONTHLY_VND = 1_000_000
MAX_MONTHLY_VND = 300_000_000
SALARY_COLUMNS = (
    "Luong_Min", "Luong_Max", "salary_status",
    "salary_currency_orig", "salary_period_orig",
)
HIDDEN = {"sign in to view salary", "login to view salary", "log in to view salary"}
# Từ khóa (so khớp chuỗi con trên chuỗi đã normalize), chỉ dùng khi chuỗi không có chữ số.
NEGOTIABLE = (
    "thoa thuan", "thuong luong", "negotiable", "you'll love it", "attractive",
    "competitive", "high paid", "let's discuss", "canh tranh", "hap dan",
)
PERIODS = {"thang": "month", "month": "month", "nam": "year", "year": "year"}

# Phân biệt số thập phân (20.7) với dấu ngăn hàng nghìn (1,000 / 10.000.000).
NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+(?:\.[0-9]+)?)"
CURRENCY = r"(?:usd|\$|vnd|₫|d)"
AMOUNTS = [
    rf"(?P<before{i}>{CURRENCY})?\s*(?P<number{i}>{NUMBER})\s*"
    rf"(?P<scale{i}>trieu|tr|mil|m)?\s*(?P<after{i}>{CURRENCY})?"
    for i in (1, 2)
]
SALARY_RE = re.compile(
    r"(?P<prefix>up\s*to|from|tu|toi|den|under|below|over|above|tren)?\s*"
    + AMOUNTS[0]
    # Kỳ trả có thể ghi sau số đầu: "12M ₫/month - 18M ₫/month".
    + r"(?:\s*/\s*(?P<period1>thang|month|nam|year))?"
    + rf"(?:\s*(?:-|to)\s*{AMOUNTS[1]})?"
    # NET/GROSS chỉ có ở vài tin: chấp nhận rồi bỏ qua.
    + r"(?:\s*(?:net|gross))?"
    + r"(?:\s*/\s*(?P<period>thang|month|nam|year))?"
)


# Khoảng lương trong tiêu đề/JD sau normalize(), bắt buộc "/tháng": "13 - 16 trieu/thang", "vnd 25-30 million/month",
# "15.000.000 - 25.000.000 vnd/thang", "9.000.000 vnd - 13.000.000 vnd/ thang", "30.000.000 - 40.000.000 vnd gross/thang".
JD_NUMBER = r"[0-9]{1,3}(?:[.,][0-9]{3}){1,2}(?![0-9])|[0-9]+(?:[.,][0-9]+)?"
JD_UNIT = r"(?:\s*(?:trieu|tr|million|mil|m)\b)?(?:\s*(?:vnd|dong|d)\b)?"
JD_RANGE_RE = re.compile(
    rf"(?<![0-9.,])({JD_NUMBER}){JD_UNIT}\s*(?:-|den|~)\s*({JD_NUMBER}){JD_UNIT}(?:\s*(?:net|gross))?"
    r"\s*/\s*(?:thang|month)"
)
SALARY_LABEL_RE = re.compile(r"luong|thu nhap|salary|income")  # tìm trong 30 ký tự ngay trước khoảng
JD_FALLBACK_STATUSES = ("negotiable", "hidden", "min_only", "max_only", "missing", "unknown")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace("’", "'").split())


def parse_salary(raw, *, currency_hint=None, period_hint=None) -> dict:
    """Đọc trọn chuỗi bằng regex; hint chỉ bổ sung thông tin JSON-LD còn thiếu."""
    result = dict.fromkeys(SALARY_COLUMNS)
    if raw is None or not str(raw).strip():
        result["salary_status"] = "missing"
        return result
    result["salary_status"] = "unknown"
    text = normalize(str(raw))
    if not re.search(r"[0-9]", text) and any(word in text for word in NEGOTIABLE):
        result["salary_status"] = "negotiable"
        return result
    if text in HIDDEN:
        result["salary_status"] = "hidden"
        return result
    text = re.sub(r"[–—−]", "-", text)
    match = SALARY_RE.fullmatch(text)
    if not match:
        return result
    groups = match.groupdict()

    tokens = [groups[key] for key in ("before1", "after1", "before2", "after2") if groups[key]]
    currencies = {"USD" if token in ("usd", "$") else "VND" for token in tokens}
    has_scale = bool(groups["scale1"] or groups["scale2"])
    if has_scale:
        # VietnamWorks có "$ 30tr-50tr": tr/triệu vẫn là VND, bỏ ký hiệu $ sai.
        if "usd" in tokens:
            return result
        currencies = {"VND"}
    if len(currencies) > 1:
        return result
    currency = next(iter(currencies), None)
    if currency is None and currency_hint:
        hint = normalize(str(currency_hint))
        currency = {"usd": "USD", "$": "USD", "vnd": "VND", "₫": "VND", "d": "VND"}.get(hint)
    periods = {PERIODS[groups[key]] for key in ("period1", "period") if groups[key]}
    if len(periods) > 1:
        return result
    period = next(iter(periods), None) or PERIODS.get(normalize(str(period_hint or "")))
    result["salary_currency_orig"] = currency
    result["salary_period_orig"] = period
    if currency is None or period is None:
        return result

    values = []
    for i in (1, 2):
        number = groups[f"number{i}"]
        if number is None:
            continue
        if re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{3})+", number):
            number = number.replace(".", "")
        value = float(number.replace(",", ""))
        own_currency = groups[f"before{i}"] or groups[f"after{i}"]
        if groups[f"scale{i}"] or (has_scale and own_currency not in ("vnd", "₫", "d")):
            value *= 1_000_000
        if currency == "USD":
            value *= USD_TO_VND
        if period == "year":
            value /= 12
        if not math.isfinite(value):
            return result
        values.append(value)

    prefix = groups["prefix"]
    if len(values) == 2:
        if prefix or values[0] > values[1]:
            return result
        minimum, maximum = values
        status = "closed_range"
    elif prefix in ("tu", "from", "over", "above", "tren"):
        minimum, maximum, status = values[0], None, "min_only"
    elif prefix:
        minimum, maximum, status = None, values[0], "max_only"
    else:
        minimum = maximum = values[0]
        status = "closed_range"
    if any(not MIN_MONTHLY_VND <= value <= MAX_MONTHLY_VND for value in values):
        result["salary_status"] = "invalid"
        return result
    result.update(Luong_Min=minimum, Luong_Max=maximum, salary_status=status)
    return result


def jd_amount(number: str) -> float:
    """'15.000.000' / '15,000,000' -> 15e6 (dấu ngăn nghìn); '16' / '13,5' -> số triệu."""
    if re.fullmatch(r"[0-9]{1,3}(?:[.,][0-9]{3}){1,2}", number):
        value = float(re.sub(r"[.,]", "", number))
    else:
        value = float(number.replace(",", "."))
    return value * 1_000_000 if value < 1000 else value


def monthly_ranges(text) -> list[tuple]:
    """[(min, max, có nhãn lương ngay trước)] VND/tháng của mọi khoảng "a - b .../tháng" hợp lệ trong text."""
    text = re.sub(r"[–—−]", "-", normalize(str(text or "")))
    found = []
    for match in JD_RANGE_RE.finditer(text):
        low, high = jd_amount(match.group(1)), jd_amount(match.group(2))
        if MIN_MONTHLY_VND <= low <= high <= MAX_MONTHLY_VND:
            labeled = bool(SALARY_LABEL_RE.search(text[max(0, match.start() - 30):match.start()]))
            found.append((low, high, labeled))
    return found


def clean_row(row: dict) -> dict:
    """Ưu tiên salary; dùng salary_detail nếu lương danh sách trống hoặc bị ẩn."""
    raw = row.get("salary")
    source = "salary"
    if normalize(str(raw or "")) in HIDDEN or not str(raw or "").strip():
        detail = row.get("salary_detail")
        if detail is not None and str(detail).strip():
            raw, source = detail, "salary_detail"
    hints = {}
    if source == "salary_detail":
        hints = {"currency_hint": row.get("salary_detail_currency"),
                 "period_hint": row.get("salary_detail_unit")}
    result = parse_salary(raw, **hints)
    assumed = False
    # Giả định lương tháng chỉ cho TopCV (lý do ở docstring đầu file).
    if row.get("site") == "topcv" and result["salary_status"] == "unknown" and result["salary_period_orig"] is None:
        retry = parse_salary(raw, **{**hints, "period_hint": "month"})
        if retry["salary_period_orig"] == "month":
            result, assumed = retry, True
    ranges = monthly_ranges(f"{row.get('title') or ''}\n{row.get('description') or ''}")
    if assumed and (result["Luong_Min"], result["Luong_Max"]) in {(low, high) for low, high, _ in ranges}:
        assumed = False  # tiêu đề/JD ghi lại đúng khoảng kèm "/tháng": kỳ trả có căn cứ
    labeled = {(low, high) for low, high, has_label in ranges if has_label}
    if result["salary_status"] in JD_FALLBACK_STATUSES and len(labeled) == 1:
        low, high = labeled.pop()
        if result["Luong_Min"] in (None, low) and result["Luong_Max"] in (None, high):
            result = {**result, "Luong_Min": low, "Luong_Max": high, "salary_status": "closed_range",
                      "salary_currency_orig": "VND", "salary_period_orig": "month"}
            source, assumed = "jd", False
    return {**row, **result, "salary_source": source, "salary_period_assumed": assumed}


def main() -> None:
    sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="src", default="data/full.jsonl")
    parser.add_argument("--out", default="data/salary.jsonl")
    args = parser.parse_args()
    source, out = Path(args.src).resolve(), Path(args.out).resolve()
    if not source.is_file():
        parser.error(f"Input does not exist: {source}")
    if source == out or (out.exists() and source.samefile(out)):
        parser.error("Input and output must be different files")

    try:
        rows = read_jsonl(source)
        reserved = set(SALARY_COLUMNS) | {"salary_source", "salary_period_assumed"}
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                parser.error(f"Row {index} must be a JSON object")
            if reserved.intersection(row):
                parser.error(f"Row {index} already contains normalized salary fields")
        cleaned = [clean_row(row) for row in rows]
        out.parent.mkdir(parents=True, exist_ok=True)
        # write_jsonl ghi nối: dùng file tạm mới mỗi lần, rồi thay output nguyên tử.
        with tempfile.TemporaryDirectory(dir=out.parent, prefix=".salary-") as directory:
            temporary = Path(directory) / "salary.jsonl"
            write_jsonl(temporary, cleaned)
            temporary.replace(out)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    counts = Counter(row["salary_status"] for row in cleaned)
    print(f"Wrote {len(cleaned)} rows -> {out}")
    print(f"Fixed exercise rate: 1 USD = {USD_TO_VND} VND; annual salary / 12")
    for status in ("closed_range", "min_only", "max_only", "negotiable", "hidden",
                   "missing", "invalid", "unknown"):
        print(f"{status}: {counts[status]}")
    print(f"salary_period_assumed: {sum(row['salary_period_assumed'] for row in cleaned)}")
    examples = []
    for row in cleaned:
        raw = row.get(row["salary_source"])
        if row["salary_status"] == "unknown" and raw not in examples:
            examples.append(raw)
        if len(examples) == 10:
            break
    if examples:
        print("Unknown examples:")
        for raw in examples:
            print(repr(raw))


if __name__ == "__main__":
    main()
