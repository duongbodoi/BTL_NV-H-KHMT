"""Xuất data/processed/anomaly_candidates.csv và asof_dates.csv từ bộ OCR ViFinQA.

Chạy: python scripts/build_anomaly_candidates.py [thư_mục_ocr] [thư_mục_ra]
Mặc định đọc `sources.ocr_dir` và `paths.processed` trong configs/dataset.yaml.
"""

from __future__ import annotations

import json
import sys

from steel_xai.anomaly.corpus import STATUS_OK, build_candidates


def main(argv: list[str]) -> int:
    out = build_candidates(*(argv[:2]))
    print(f"Ứng viên: {out.candidates_path}")
    print(f"As-of:    {out.asof_path}")
    print(json.dumps(out.result.summary(), ensure_ascii=False, indent=2))
    todo = [r for r in out.asof_rows if r.status != STATUS_OK]
    print(f"As-of chưa có (cần tra tay): {len(todo)}")
    for r in todo:
        print(f"  {r.company} {r.period or '?'} [{r.status}] {r.doc_id}: {r.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
