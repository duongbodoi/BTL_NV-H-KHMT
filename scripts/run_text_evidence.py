"""Chạy bước bằng chứng văn bản (truy xuất + LLM phân loại + kiểm trích dẫn) cho bất thường thật.

Chạy:
  python scripts/run_text_evidence.py --dry-run            # không gọi API: chỉ đếm số lời gọi LLM
  python scripts/run_text_evidence.py --run-id run1        # gọi API thật (cần biến môi trường key)
Ghi data/processed/text_evidence.jsonl. Giả thuyết `draft` cũng được chạy (để nhóm xem), nhưng chỉ
`reviewed` mới vào pipeline thật (spec mục 9). TỐN TOKEN khi không dùng --dry-run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_xai.anomaly import detect_anomalies, load_anomaly_settings
from steel_xai.anomaly.corpus import asof_from_corpus, find_documents, load_corpus
from steel_xai.config import load_all_configs, load_config
from steel_xai.hypotheses import load_library
from steel_xai.llm import AnthropicTransport, LLMClient, gather_text_evidence, load_llm_settings
from steel_xai.normalization import load_normalization_settings, normalize
from steel_xai.retrieval import BM25Retriever, build_chunks
from steel_xai.validation import load_validation_settings, validate_facts
from steel_xai.verification import hypothesis_applies


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows: tránh UnicodeEncodeError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="chỉ đếm, không gọi API")
    ap.add_argument("--run-id", default="run1", help="nằm trong khóa cache (spec: 3 lần chạy)")
    ap.add_argument("--no-rephrase", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="chỉ chạy N bất thường đầu (0 = tất cả)")
    args = ap.parse_args(argv)

    dataset = load_config("dataset")
    ocr = Path(dataset["sources"]["ocr_dir"])
    out_path = Path(dataset["paths"]["processed"]) / "text_evidence.jsonl"

    a_settings = load_anomaly_settings()
    ns = load_normalization_settings()
    corpus = load_corpus(find_documents(ocr, a_settings.companies), ns)
    facts = validate_facts(
        normalize(corpus.tables, settings=ns, contexts=[c for _, c in corpus.docs]).facts,
        load_validation_settings(),
    ).facts
    asof, _ = asof_from_corpus(corpus, a_settings.reference_only_from_year)
    anomalies = list(detect_anomalies(facts, a_settings, asof_extracted=asof).anomalies)
    if args.limit:
        anomalies = anomalies[: args.limit]

    library = load_library()
    hypotheses = [*library.reviewed, *library.drafts]
    retriever = BM25Retriever(build_chunks(ocr, asof=asof).chunks)

    pairs = [
        (a, h)
        for a in anomalies
        for h in hypotheses
        if h.anomaly_type == a.type
        and hypothesis_applies(h, a.direction)
        and any(c.kind == "text" for c in h.checks)
    ]
    text_checks = sum(sum(c.kind == "text" for c in h.checks) for _, h in pairs)
    n_checks = f"{text_checks} check văn bản"
    print(f"{len(anomalies)} bất thường, {len(pairs)} cặp (bất thường × giả thuyết), {n_checks}")
    # mỗi cặp: 1 lời gọi diễn đạt lại (nếu bật) + 1 lời gọi phân loại cho mỗi check văn bản
    print(f"Số lời gọi LLM tối đa: {text_checks + (0 if args.no_rephrase else len(pairs))}")
    if args.dry_run:
        return 0

    llm_cfg = load_config("llm")
    client = LLMClient(load_llm_settings(llm_cfg), AnthropicTransport.from_env(llm_cfg))
    load_all_configs()  # kiểm mọi config hợp lệ trước khi tiêu token
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        for a, h in pairs:
            outcomes = gather_text_evidence(
                client=client,
                retriever=retriever,
                hypothesis=h,
                anomaly=a,
                run_id=args.run_id,
                rephrase=not args.no_rephrase,
            )
            row = {
                "anomaly": a.anomaly_id,
                "hypothesis": h.id,
                "outcomes": {k: v.to_dict() for k, v in outcomes.items()},
            }
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    t = client.totals()
    print(f"Xong → {out_path}")
    print(f"Lời gọi: {t.calls} (cache {t.cache_hits}), lỗi: {t.failures}")
    print(f"Token vào/ra: {t.input_tokens}/{t.output_tokens}, độ trễ tổng: {t.latency_s:.1f}s")
    for w in client.warnings:
        print("CẢNH BÁO:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
