"""Demo dạng dòng lệnh (Phase 13): hiển thị một ca từ kết quả đã chạy. KHÔNG gọi API.

Chạy:
  python scripts/demo.py --list                       # các ca đã có kết quả C
  python scripts/demo.py --case C01                   # tập dev, lần chạy đầu tiên có kết quả
  python scripts/demo.py --case C01 --run run2 --split dev
Giao diện web (cần `pip install -e ".[demo]"`):
  streamlit run src/steel_xai/demo/app.py
Cần chạy `scripts/run_eval.py run` trước để có kết quả.
"""

from __future__ import annotations

import argparse
import sys

from steel_xai.demo.text import render_text
from steel_xai.demo.view import default_inputs, list_cases, run_hint


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows: tránh UnicodeEncodeError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=("dev", "test"), default="dev")
    ap.add_argument("--case")
    ap.add_argument("--run", help="mặc định: lần chạy đầu tiên có kết quả")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--top", type=int, default=3, help="số hạng hiển thị chi tiết bằng chứng")
    args = ap.parse_args(argv)

    inp = default_inputs()
    cases = dict(list_cases(inp.output_dir, args.split))
    if not cases:
        print(f"Chưa có kết quả C trong {inp.output_dir / args.split}. {run_hint(args.split)}")
        return 1
    if args.list or not args.case:
        print(f"Các ca có kết quả ({args.split}):")
        for cid, runs in cases.items():
            print(f"  {cid}: {', '.join(runs)}")
        return 0 if args.list else 2
    if args.case not in cases:
        print(f"Ca {args.case} chưa có kết quả C; có: {', '.join(cases)}")
        return 1
    run = args.run or cases[args.case][0]
    if run not in cases[args.case]:
        print(f"{args.case} không có lần chạy {run}; có: {', '.join(cases[args.case])}")
        return 1
    print(render_text(inp.case_view(args.split, args.case, run), inp.disclaimer, top=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
