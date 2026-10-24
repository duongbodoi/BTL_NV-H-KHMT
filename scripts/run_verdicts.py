"""Chạy bước 3 (check định lượng [+ văn bản] → trạng thái → xếp hạng) cho bất thường thật.

Chạy:
  python scripts/run_verdicts.py                      # chỉ định lượng, KHÔNG gọi API
  python scripts/run_verdicts.py --with-text          # thêm check văn bản bằng LLM (TỐN TOKEN)
Ghi data/processed/verdicts.jsonl. Giả thuyết `draft` cũng được chạy (để nhóm xem thử); pipeline
thật chỉ nhận `reviewed` (spec mục 9). Thiếu chuỗi giá/chỉ tiêu thì check tương ứng là
NOT_EVALUABLE và giả thuyết đi xuống, không bao giờ lên SUPPORTED vì thiếu dữ liệu.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
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
    analyze_anomaly,
    context_from_anomaly,
    load_verdict_rules,
    load_verification_settings,
)


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows: tránh UnicodeEncodeError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--with-text", action="store_true", help="chạy cả check văn bản bằng LLM")
    ap.add_argument("--run-id", default="run1")
    ap.add_argument("--top", type=int, default=3, help="số giả thuyết in ra cho mỗi bất thường")
    args = ap.parse_args(argv)

    dataset = load_config("dataset")
    ocr = Path(dataset["sources"]["ocr_dir"])
    out_path = Path(dataset["paths"]["processed"]) / "verdicts.jsonl"

    a_settings = load_anomaly_settings()
    v_settings = load_verification_settings()
    ns = load_normalization_settings()
    corpus = load_corpus(find_documents(ocr, a_settings.companies), ns)
    facts = validate_facts(
        normalize(corpus.tables, settings=ns, contexts=[c for _, c in corpus.docs]).facts,
        load_validation_settings(),
    ).facts
    asof, _ = asof_from_corpus(corpus, a_settings.reference_only_from_year)
    asof_by = {(c, int(p[2:])): dt.date.fromisoformat(e.date) for (c, p), e in asof.items()}
    anomalies = detect_anomalies(facts, a_settings, asof_extracted=asof).anomalies
    store = FactStore(facts, v_settings.reference_only_from_year)
    external = ExternalStore.load(v_settings)
    library = load_library()
    hypotheses = [*library.reviewed, *library.drafts]
    rules = load_verdict_rules()

    text_fn = None
    client = None
    if args.with_text:
        from steel_xai.llm import (
            LLMClient,
            gather_text_evidence,
            load_llm_settings,
            make_transport,
        )
        from steel_xai.retrieval import BM25Retriever, build_chunks

        llm_cfg = load_config("llm")
        client = LLMClient(load_llm_settings(llm_cfg), make_transport(llm_cfg))
        retriever = BM25Retriever(build_chunks(ocr, asof=asof).chunks)

        def text_fn(h, a):  # noqa: F811
            return gather_text_evidence(
                client=client, retriever=retriever, hypothesis=h, anomaly=a, run_id=args.run_id
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        for a in anomalies:
            ctx = context_from_anomaly(a, store, external, v_settings, asof_by)
            res = analyze_anomaly(
                a, hypotheses, quant_ctx=ctx, text_fn=text_fn, rules=rules, allow_draft=True
            )
            f.write(json.dumps(res.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            counts.update(v.status.value for v in res.verdicts)
            print(f"{a.anomaly_id}")
            for v in res.verdicts[: args.top]:
                print(f"  #{v.rank} {v.status.value:<12} {v.hypothesis_id} "
                      f"(critical {v.critical_passed}/{v.critical_total})")  # fmt: skip
    print(f"→ {out_path}")
    print("Phân bố trạng thái:", dict(counts))
    if client is not None:
        t = client.totals()
        print(
            f"LLM: {t.calls} lời gọi (cache {t.cache_hits}), "
            f"token {t.input_tokens}/{t.output_tokens}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
