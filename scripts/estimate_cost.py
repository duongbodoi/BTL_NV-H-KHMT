"""Ước tính chi phí chạy đánh giá bằng các model tính phí → docs/COST_ESTIMATE.md. KHÔNG gọi API.

Chạy:
  python scripts/estimate_cost.py                    # dựng prompt thật cho mọi ca (dev + test)
  python scripts/estimate_cost.py --source measured  # dùng token đo thật (sau khi chạy free tier)
Giá: configs/cost/pricing.yaml.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from steel_xai.config import PROJECT_ROOT, load_config
from steel_xai.eval.cost import (
    cost_table,
    estimate_from_prompts,
    estimate_from_records,
    load_pricing,
    render_markdown,
)
from steel_xai.eval.references import load_references
from steel_xai.eval.runner import build_context, load_records
from steel_xai.eval.settings import load_runner_settings


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows: tránh UnicodeEncodeError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=("prompt", "measured"), default="prompt")
    ap.add_argument("--out", default=str(PROJECT_ROOT / "docs" / "COST_ESTIMATE.md"))
    args = ap.parse_args(argv)

    prices, asm = load_pricing()
    rs = load_runner_settings()
    refs = load_references()
    by_split = {sp: [c for c in refs.cases if c.split == sp] for sp in ("dev", "test")}
    splits = []
    if args.source == "prompt":
        ctx = build_context(rs, None)
        for cases in by_split.values():
            if cases:
                splits.append(estimate_from_prompts(ctx, cases, rs.configs, rs.runs_per_case, asm))
    else:
        records = [r for sp in ("dev", "test") for r in load_records(rs.output_dir, sp)]
        for sp, cases in by_split.items():
            s = estimate_from_records(records, len(cases), rs.configs, rs.runs_per_case, sp)
            if s is None:
                print("chưa có lần chạy đo được token (không dùng cache): chạy free tier trước")
                return 1
            splits.append(s)
    rows = cost_table(splits, prices)
    current = load_config("llm")["model"]
    md = render_markdown(splits, rows, asm, current, dt.date.today().isoformat())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    for r in rows:
        lo, hi = r["total_low"], r["total_high"]
        print(f"{r['model']:<24} ${r['total_mid']:>9.2f}  (${lo:.2f}–${hi:.2f})")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
