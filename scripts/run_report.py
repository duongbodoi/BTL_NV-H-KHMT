"""Bước 4: dựng báo cáo grounded cho các bất thường thật (spec mục 13).

Chạy:
  python scripts/run_report.py                     # KHÔNG gọi API: chỉ phần do code dựng + prompt
  python scripts/run_report.py --call-api          # LLM viết, code kiểm, sinh lại (TỐN TOKEN)
  python scripts/run_report.py --anomaly "HPG|FY2022|gross_margin_change|down"
Ghi data/processed/reports/<anomaly>.md (+ .prompt.json khi không gọi API) và reports.jsonl.
Giả thuyết `draft` được dùng để xem thử; báo cáo khi đó có cảnh báo và cờ `used_drafts`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from steel_xai.anomaly import detect_anomalies, load_anomaly_settings
from steel_xai.anomaly.corpus import asof_from_corpus, find_documents, load_corpus
from steel_xai.config import load_config
from steel_xai.hypotheses import load_library
from steel_xai.normalization import load_normalization_settings, normalize
from steel_xai.report import (
    build_report_input,
    generate_report,
    load_report_settings,
    skeleton_report,
)
from steel_xai.validation import load_validation_settings, validate_facts
from steel_xai.verification import (
    ExternalStore,
    FactStore,
    analyze_anomaly,
    context_from_anomaly,
    load_verdict_rules,
    load_verification_settings,
)


def _slug(anomaly_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", anomaly_id).strip("_")


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows: tránh UnicodeEncodeError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--call-api", action="store_true", help="gọi LLM để viết phần diễn giải")
    ap.add_argument("--anomaly", action="append", help="chỉ chạy bất thường này (lặp được)")
    ap.add_argument("--run-id", default="run1")
    args = ap.parse_args(argv)

    dataset = load_config("dataset")
    ocr = Path(dataset["sources"]["ocr_dir"])
    out_dir = Path(dataset["paths"]["processed"]) / "reports"

    a_settings = load_anomaly_settings()
    v_settings = load_verification_settings()
    r_settings = load_report_settings()
    ns = load_normalization_settings()
    corpus = load_corpus(find_documents(ocr, a_settings.companies), ns)
    facts = validate_facts(
        normalize(corpus.tables, settings=ns, contexts=[c for _, c in corpus.docs]).facts,
        load_validation_settings(),
    ).facts
    asof, _ = asof_from_corpus(corpus, a_settings.reference_only_from_year)
    asof_by = {(c, int(p[2:])): dt.date.fromisoformat(e.date) for (c, p), e in asof.items()}
    anomalies = detect_anomalies(facts, a_settings, asof_extracted=asof).anomalies
    if args.anomaly:
        wanted = set(args.anomaly)
        anomalies = [a for a in anomalies if a.anomaly_id in wanted]
        missing = wanted - {a.anomaly_id for a in anomalies}
        if missing:
            print(f"Không có bất thường: {sorted(missing)}")
            return 2
    store = FactStore(facts, v_settings.reference_only_from_year)
    external = ExternalStore.load(v_settings)
    library = load_library()
    hypotheses = [*library.reviewed, *library.drafts]
    rules = load_verdict_rules()

    client = None
    if args.call_api:
        from steel_xai.llm import AnthropicTransport, LLMClient, load_llm_settings

        llm_cfg = load_config("llm")
        client = LLMClient(load_llm_settings(llm_cfg), AnthropicTransport.from_env(llm_cfg))

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "reports.jsonl").open("w", encoding="utf-8", newline="\n") as log:
        for a in anomalies:
            ctx = context_from_anomaly(a, store, external, v_settings, asof_by)
            analysis = analyze_anomaly(
                a, hypotheses, quant_ctx=ctx, text_fn=None, rules=rules, allow_draft=True
            )
            inp = build_report_input(a, analysis, hypotheses, r_settings)
            base = out_dir / _slug(a.anomaly_id)
            if client is None:
                md = skeleton_report(inp, r_settings, "Chưa gọi LLM: chỉ phần do code dựng.")
                base.with_suffix(".prompt.json").write_text(inp.to_prompt(), encoding="utf-8")
                row = {
                    "anomaly_id": a.anomaly_id,
                    "status": "skeleton",
                    "used_drafts": inp.used_drafts,
                }
                print(f"{a.anomaly_id}: {len(inp.numbers)} số, {len(inp.missing)} phần thiếu")
            else:
                res = generate_report(inp, client, r_settings, args.run_id)
                md, row = res.markdown, res.to_dict()
                m = res.metrics()
                print(
                    f"{a.anomaly_id}: {res.status} sau {m['attempts']} lần; nháp đầu lệch số "
                    f"{m['first_draft_numeric_mismatches']}, không nguồn "
                    f"{m['first_draft_unsupported_claims']}"
                )
            base.with_suffix(".md").write_text(md, encoding="utf-8")
            log.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"→ {out_dir}")
    if client is not None:
        t = client.totals()
        print(
            f"LLM: {t.calls} lời gọi (cache {t.cache_hits}), "
            f"token {t.input_tokens}/{t.output_tokens}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
