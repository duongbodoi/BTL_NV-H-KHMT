"""Chuyển file giá .xlsx (mặc định cột A: tháng, cột B: giá) thành CSV cho kiểm chứng định lượng.

Chạy: python scripts/import_price_xlsx.py <file.xlsx> <tên_chuỗi> [--col C] [--midpoint-ranges]
                                          [--trailing-empty-ok]
Ví dụ: python scripts/import_price_xlsx.py data/external/iron_ore_price.xlsx iron_ore_price
       python scripts/import_price_xlsx.py data/external/vsa_prices.xlsx hrc_price --col C \\
           --midpoint-ranges --trailing-empty-ok
`tên_chuỗi` phải có trong configs/verification.yaml (external.series); CSV ghi tại đường dẫn ở đó.
"""

from __future__ import annotations

import argparse
import sys

from steel_xai.verification import load_verification_settings
from steel_xai.verification.price_import import monthly_series_from_xlsx, write_series_csv


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("xlsx")
    ap.add_argument("name")
    ap.add_argument("--date-col", default="A")
    ap.add_argument("--col", default="B", help="cột giá")
    ap.add_argument("--midpoint-ranges", action="store_true", help="ô '458-462' → 460")
    ap.add_argument("--trailing-empty-ok", action="store_true", help="bỏ dòng chỉ có ngày ở cuối")
    args = ap.parse_args(argv)
    settings = load_verification_settings()
    if args.name not in settings.series:
        print(
            f"Chuỗi {args.name!r} không có trong verification.external.series: "
            f"{sorted(settings.series)}"
        )
        return 2
    rows = monthly_series_from_xlsx(
        args.xlsx,
        date_col=args.date_col,
        value_col=args.col,
        midpoint_ranges=args.midpoint_ranges,
        trailing_empty_ok=args.trailing_empty_ok,
    )
    out = settings.series[args.name].path
    write_series_csv(rows, out)
    print(f"{args.name}: {len(rows)} tháng ({rows[0][0]} .. {rows[-1][0]}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
