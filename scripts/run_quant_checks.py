"""Chạy check định lượng của thư viện giả thuyết trên các bất thường ứng viên (dữ liệu OCR thật).

Chạy: python scripts/run_quant_checks.py [thư_mục_ocr] [thư_mục_ra]
Ghi data/processed/quant_check_results.csv. Giả thuyết `draft` cũng được chạy (để nhóm xem), nhưng
kết quả chỉ là bản thử: chỉ giả thuyết `reviewed` mới được vào pipeline thật (spec mục 9).
"""

from __future__ import annotations

import csv
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

from steel_xai.anomaly import detect_anomalies, load_anomaly_settings
from steel_xai.anomaly.corpus import asof_from_corpus, find_documents, load_corpus
from steel_xai.config import load_config
from steel_xai.hypotheses import load_library
from steel_xai.normalization import load_normalization_settings, normalize
from steel_xai.validation import load_validation_settings, validate_facts
from steel_xai.verification import (
    ExternalStore,
    FactStore,
    context_from_anomaly,
    hypothesis_applies,
    load_verification_settings,
    run_quant_checks,
)

COLUMNS = (
    "company",
    "period",
    "anomaly_type",
    "direction",
    "hypothesis",
    "hypothesis_status",
    "check_id",
    "importance",
    "polarity",
    "status",
    "values",
    "reason",
)


def main(argv: list[str]) -> int:
    dataset = load_config("dataset")
    ocr = Path(argv[0] if argv else dataset["sources"]["ocr_dir"])
    out_dir = Path(argv[1] if len(argv) > 1 else dataset["paths"]["processed"])
    out_dir.mkdir(parents=True, exist_ok=True)

    a_settings = load_anomaly_settings()
    v_settings = load_verification_settings()
    ns = load_normalization_settings()
    corpus = load_corpus(find_documents(ocr, a_settings.companies), ns)
    normalized = normalize(corpus.tables, settings=ns, contexts=[c for _, c in corpus.docs])
    facts = validate_facts(normalized.facts, load_validation_settings()).facts
    asof, _ = asof_from_corpus(corpus, a_settings.reference_only_from_year)
    asof_by = {(c, int(p[2:])): dt.date.fromisoformat(e.date) for (c, p), e in asof.items()}

    result = detect_anomalies(facts, a_settings, asof_extracted=asof)
    store = FactStore(facts, v_settings.reference_only_from_year)
    external = ExternalStore.load(v_settings)
    library = load_library()
    hypotheses = [*library.reviewed, *library.drafts]

    rows = []
    skipped = Counter()
    for a in result.anomalies:
        ctx = context_from_anomaly(a, store, external, v_settings, asof_by)
        for h in hypotheses:
            if h.anomaly_type != a.type:
                continue
            if not hypothesis_applies(h, a.direction, v_settings):
                skipped[f"{a.type}/{a.direction}"] += 1  # thư viện chưa có giả thuyết cho hướng này
                continue
            checks = {c.id: c for c in h.checks}
            for cid, r in run_quant_checks(h, ctx).items():
                rows.append(
                    (
                        a.company,
                        a.period,
                        a.type,
                        a.direction,
                        h.id,
                        h.status,
                        cid,
                        checks[cid].importance,
                        checks[cid].polarity,
                        r.status.value,
                        ";".join(f"{k}={v}" for k, v in r.values.items() if v is not None),
                        r.reason,
                    )
                )
    path = out_dir / "quant_check_results.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    by_status = Counter(r[9] for r in rows)
    for key, n in skipped.items():
        print(
            f"  bỏ qua {n} cặp (bất thường × giả thuyết) vì thư viện chưa có giả thuyết cho {key}"
        )
    print(f"{len(result.anomalies)} bất thường × giả thuyết → {len(rows)} kết quả check → {path}")
    print(dict(by_status))
    reasons = Counter(r[11].split(";")[0][:70] for r in rows if r[9] == "NOT_EVALUABLE")
    for reason, n in reasons.most_common(8):
        print(f"  NOT_EVALUABLE ×{n}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
