"""Chuyển file giá .xlsx (cột A: tháng, cột B: giá) thành CSV cho kiểm chứng định lượng.

Chạy: python scripts/import_price_xlsx.py <file.xlsx> <tên_chuỗi>
Ví dụ: python scripts/import_price_xlsx.py data/external/iron_ore_price.xlsx iron_ore_price
`tên_chuỗi` phải có trong configs/verification.yaml (external.series); CSV ghi tại đường dẫn ở đó.
"""

from __future__ import annotations

import sys

from steel_xai.verification import load_verification_settings
from steel_xai.verification.price_import import monthly_series_from_xlsx, write_series_csv


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    xlsx, name = argv
    settings = load_verification_settings()
    if name not in settings.series:
        print(
            f"Chuỗi {name!r} không có trong verification.external.series: {sorted(settings.series)}"
        )
        return 2
    rows = monthly_series_from_xlsx(xlsx)
    out = settings.series[name].path
    write_series_csv(rows, out)
    print(f"{name}: {len(rows)} tháng ({rows[0][0]} .. {rows[-1][0]}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
