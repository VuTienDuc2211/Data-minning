"""In đường dẫn của mọi key xuất hiện trong phản hồi API ở trang đầu."""

import sys
from collections.abc import Iterator

import requests

if __package__:
    from .vietnamnetworks import BODY, ENDPOINT
else:
    from vietnamnetworks import BODY, ENDPOINT


def iter_key_paths(value: object, path: str = "") -> Iterator[str]:
    """Duyệt cả dictionary lồng nhau và mọi phần tử của danh sách."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield child_path
            yield from iter_key_paths(child, child_path)
    elif isinstance(value, list):
        for item in value:
            yield from iter_key_paths(item, f"{path}[]")


def main() -> None:
    body = {**BODY, "page": 0}
    # Để API tự trả các trường mặc định cho yêu cầu xem key.
    body.pop("retrieveFields", None)

    try:
        response = requests.post(
            ENDPOINT,
            json=body,
            headers={"Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SystemExit(f"Không lấy được phản hồi từ API: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SystemExit("API không trả về danh sách tin tuyển dụng trong trường 'data'.")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    response_keys = sorted(set(iter_key_paths(payload)))
    print("KEY TRONG BODY CẤU HÌNH", *BODY, sep="\n")
    print("\nKEY TRONG PHẢN HỒI API (TRANG ĐẦU)", *response_keys, sep="\n")


if __name__ == "__main__":
    main()
