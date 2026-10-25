"""Runner Phase 12 (spec mục 14, 15): chạy ca × cấu hình × lần, ghi `RunRecord` ra đĩa.

Cấu hình:
- A (LLM only): bất thường + bảng chỉ số do code tính → LLM sinh/xếp hạng giả thuyết tự do, viết
  báo cáo.
- B (LLM + RAG): như A + đoạn truy xuất (truy vấn chung theo loại bất thường, lọc as-of trong code).
- C (Full System): 4 bước (check định lượng + RAG + LLM phân loại + luật trạng thái + báo cáo).
- A_lib / B_lib (chạy phụ, 14.1): như A/B nhưng LLM xếp hạng danh sách giả thuyết của thư viện.
- no_rag: C bỏ RAG (chỉ check định lượng; không gọi LLM).
- no_verification: thư viện + đoạn truy xuất theo truy vấn của thư viện → LLM xếp hạng; không check,
  không trạng thái.
- no_library: LLM sinh giả thuyết tự do kèm truy vấn → mỗi giả thuyết có MỘT check văn bản critical
  (không có check định lượng vì không có công cụ cho giả thuyết tự do) → cùng luật trạng thái và
  xếp hạng.

Kết quả lưu theo (split, cấu hình, ca, lần): chạy lại bỏ qua bản đã có (trừ `force`), LLM có cache
đĩa nên tái lập được. Tập test chỉ chạy khi freeze khớp (người gọi truyền `FreezeCheck`).
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steel_xai.contracts import (
    Anomaly,
    CaseRecord,
    Check,
    Hypothesis,
    HypothesisVerdict,
    RunRecord,
)
from steel_xai.eval import baselines as bl
from steel_xai.eval.settings import RunnerSettings
from steel_xai.llm.client import LLMClient
from steel_xai.llm.evidence import gather_text_evidence
from steel_xai.llm.settings import EvidenceSettings
from steel_xai.report.facts import ReportInput, build_report_input
from steel_xai.report.generate import generate_report
from steel_xai.report.settings import ReportSettings
from steel_xai.retrieval.retriever import BM25Retriever, RetrievedEvidence
from steel_xai.verification.analyze import AnomalyAnalysis, analyze_anomaly
from steel_xai.verification.context import CheckContext
from steel_xai.verification.evaluate import hypothesis_applies
from steel_xai.verification.verdict import VerdictRules

LLM_CONFIGS = frozenset({"A", "B", "C", "A_lib", "B_lib", "no_verification", "no_library"})
FREE_CONFIGS = frozenset({"A", "B", "no_library"})  # giả thuyết tự do: cần ánh xạ mù
REPORT_CONFIGS = frozenset({"A", "B", "C"})  # metric RQ2 (citation, unsupported, numeric)
VERDICT_CONFIGS = frozenset({"C", "no_rag", "no_library"})


@dataclass
class EvalContext:
    """Mọi thứ runner cần, dựng một lần. Test truyền bản giả (LLM giả, dữ liệu tổng hợp)."""

    anomalies: Mapping[str, Anomaly]
    library: Sequence[Hypothesis]  # CHỈ giả thuyết reviewed (spec mục 9)
    quant_ctx: Callable[[Anomaly], CheckContext]
    retriever: BM25Retriever | None
    client: LLMClient | None
    rules: VerdictRules
    report_settings: ReportSettings
    evidence_settings: EvidenceSettings
    runner: RunnerSettings
    fingerprint: str = ""  # run_fingerprint(): ghi vào mọi RunRecord, resume chỉ giữ bản cùng băm


def record_path(out_dir: Path, split: str, config: str, case_id: str, run_id: str) -> Path:
    return out_dir / split / "runs" / config / case_id / f"{run_id}.json"


def applicable(library: Iterable[Hypothesis], a: Anomaly) -> list[Hypothesis]:
    return [h for h in library if h.anomaly_type == a.type and hypothesis_applies(h, a.direction)]


def indicator_input(a: Anomaly, rs: ReportSettings) -> ReportInput:
    """Bảng chỉ số cơ bản do code tính (đầu vào chung của A/B, spec mục 14)."""
    empty = AnomalyAnalysis(a.anomaly_id, (), (), quant_enabled=False, text_enabled=False)
    return build_report_input(a, empty, (), rs)


def retrieve(ctx: EvalContext, a: Anomaly, queries: Sequence[str]) -> dict[str, RetrievedEvidence]:
    """Đoạn truy xuất đã lọc as-of trong code. Cấu hình cần RAG mà không truy xuất được (thiếu
    retriever, as-of hay truy vấn) → lỗi, không lặng lẽ chạy như cấu hình không có RAG."""
    if ctx.retriever is None:
        raise RuntimeError("cấu hình cần RAG nhưng chưa có retriever")
    if not a.asof_date:
        raise ValueError(f"{a.anomaly_id} thiếu mốc as-of: không truy xuất được tài liệu")
    if not queries:
        raise ValueError(f"không có truy vấn cho loại {a.type} (eval.runner.baseline_queries)")
    got = ctx.retriever.retrieve_many(list(queries), company=a.company, asof=a.asof_date)
    late = [p.chunk_id for p in got if p.doc_published > a.asof_date]
    if late:  # lớp phòng thủ thứ hai (retriever đã lọc): không bao giờ đưa tài liệu sau as-of
        raise AssertionError(f"đoạn công bố sau as-of lọt vào truy xuất: {late[:3]}")
    return bl.passage_ids(got[: ctx.runner.max_passages])


def _verdict_ranking(verdicts: Sequence[HypothesisVerdict], text_of: Mapping[str, str]):
    return [
        {
            "rank": v.rank,
            "hypothesis_id": v.hypothesis_id,
            "text": text_of.get(v.hypothesis_id, ""),
            "status": v.status.value,
        }
        for v in verdicts
    ]


def _report_outputs(res: Any, inp: ReportInput) -> dict[str, Any]:
    """Metric báo cáo của C: bản nháp đầu (so sánh với A/B) và bản cuối (người dùng thấy)."""
    first = res.first_draft
    by_id = inp.by_id
    citations = []
    for i, c in enumerate(res.claims, start=1):
        for s in c.sources:
            e = by_id.get(s)
            if s.startswith("E") and e is not None:
                citations.append(
                    {
                        "claim_index": i,
                        "source": s,
                        "quote": e.quote,
                        "verified": True,
                        "reason": "ok",
                        "doc_id": e.provenance.doc_id,
                        "page": e.provenance.page,
                        "chunk_id": e.provenance.chunk_id,
                        "evidence_id": s,
                    }
                )
    final = res.final_check
    rejected = res.status in ("rejected", "llm_error")
    return {
        "status": res.status,
        "attempts": len(res.attempts),
        "claims": [c.to_dict() for c in res.claims],
        "citations": citations,
        "first_draft": None
        if first is None
        else bl.draft_metrics(first.claims, first.violations, first.numbers_checked, inp),
        # báo cáo bị từ chối: bản cuối chỉ có phần code dựng, KHÔNG tính là "0 lệch số"
        "final": None
        if rejected or not res.claims
        else bl.draft_metrics(
            res.claims,
            () if final is None else final.violations,
            0 if final is None else final.numbers_checked,
            inp,
        ),
        "markdown": res.markdown,
    }


def _text_fn(ctx: EvalContext, run_id: str):
    client = _need_client(ctx)
    retriever = ctx.retriever
    if retriever is None:
        raise RuntimeError("cấu hình cần RAG nhưng chưa có retriever")

    def fn(h: Hypothesis, a: Anomaly):
        return gather_text_evidence(
            client=client, retriever=retriever, hypothesis=h, anomaly=a, run_id=run_id
        )

    return fn


def _need_client(ctx: EvalContext) -> LLMClient:
    if ctx.client is None:
        raise RuntimeError("cấu hình cần LLM nhưng chưa có client")
    return ctx.client


# ---------------- từng cấu hình


def run_free(ctx: EvalContext, a: Anomaly, run_id: str, with_rag: bool, config: str):
    inp = indicator_input(a, ctx.report_settings)
    passages = retrieve(ctx, a, ctx.runner.baseline_queries.get(a.type, ())) if with_rag else None
    data = bl.call_free(
        _need_client(ctx), run_id, f"baseline_{config}", a, inp, passages, ctx.runner.max_hypotheses
    )
    return {
        "mode": "free",
        "ranking": bl.free_ranking(data["hypotheses"], ctx.runner.max_hypotheses),
        "report": bl.check_baseline_claims(
            data["claims"], inp, passages or {}, ctx.evidence_settings, ctx.report_settings
        ),
        "passages": [] if passages is None else [p.chunk_id for p in passages.values()],
    }


def run_library(
    ctx: EvalContext, a: Anomaly, run_id: str, config: str, queries: Sequence[str] | None
):
    lib = applicable(ctx.library, a)
    if not lib:
        raise ValueError(f"không có giả thuyết reviewed nào áp dụng cho {a.anomaly_id}")
    inp = indicator_input(a, ctx.report_settings)
    passages = retrieve(ctx, a, queries) if queries is not None else None
    data = bl.call_library(_need_client(ctx), run_id, f"ranking_{config}", a, inp, lib, passages)
    ranking, problems = bl.library_ranking(data["ranking"], lib)
    return {
        "mode": "library",
        "ranking": ranking,
        "problems": problems,
        "passages": [] if passages is None else [p.chunk_id for p in passages.values()],
    }


def run_full(ctx: EvalContext, a: Anomaly, run_id: str, with_rag: bool, with_report: bool):
    analysis = analyze_anomaly(
        a,
        ctx.library,
        quant_ctx=ctx.quant_ctx(a),
        text_fn=_text_fn(ctx, run_id) if with_rag else None,
        rules=ctx.rules,
    )
    text_of = {h.id: h.claim for h in ctx.library}
    out: dict[str, Any] = {
        "mode": "library",
        "ranking": _verdict_ranking(analysis.verdicts, text_of),
        "verdicts": [v.to_dict() for v in analysis.verdicts],
    }
    if with_report:
        inp = build_report_input(a, analysis, ctx.library, ctx.report_settings)
        res = generate_report(inp, _need_client(ctx), ctx.report_settings, run_id)
        out["report"] = _report_outputs(res, inp)
    return out


def free_hypotheses(items: Sequence[Mapping[str, Any]], a: Anomaly, max_n: int) -> list[Hypothesis]:
    """Giả thuyết tự do của ablation no_library → Hypothesis tạm (status draft, không vào thư viện),
    mỗi cái một check văn bản critical/support với truy vấn LLM đề xuất (hoặc chính câu giả
    thuyết)."""
    out: list[Hypothesis] = []
    for i, row in enumerate(bl.free_ranking(items, max_n), start=1):
        src = next(
            (x for x in items if " ".join(str(x.get("text", "")).split()) == row["text"]), {}
        )
        qs = [" ".join(str(q).split()) for q in src.get("queries") or () if str(q).strip()]
        out.append(
            Hypothesis(
                id=f"free_{i}",
                anomaly_type=a.type,
                claim=row["text"],
                business_rationale="",
                expected_quantitative_evidence=(),
                expected_textual_evidence=(),
                contradictory_evidence=(),
                required_external_data=(),
                critical_missing_evidence=(),
                status="draft",
                checks=(
                    Check(
                        "text_1",
                        "text",
                        "critical",
                        "support",
                        queries=tuple(qs[:3]) or (row["text"],),
                    ),
                ),
                direction="both",
            )
        )
    return out


def run_no_library(ctx: EvalContext, a: Anomaly, run_id: str):
    inp = indicator_input(a, ctx.report_settings)
    data = bl.call_generate(_need_client(ctx), run_id, a, inp, ctx.runner.max_hypotheses)
    hyps = free_hypotheses(data["hypotheses"], a, ctx.runner.max_hypotheses)
    analysis = analyze_anomaly(
        a, hyps, quant_ctx=None, text_fn=_text_fn(ctx, run_id), rules=ctx.rules, allow_draft=True
    )
    text_of = {h.id: h.claim for h in hyps}
    return {
        "mode": "free",
        "ranking": _verdict_ranking(analysis.verdicts, text_of),
        "verdicts": [v.to_dict() for v in analysis.verdicts],
        "generated": [h.to_dict() for h in hyps],
    }


def run_config(ctx: EvalContext, a: Anomaly, config: str, run_id: str) -> dict[str, Any]:
    if config == "A":
        return run_free(ctx, a, run_id, with_rag=False, config=config)
    if config == "B":
        return run_free(ctx, a, run_id, with_rag=True, config=config)
    if config == "A_lib":
        return run_library(ctx, a, run_id, config, queries=None)
    if config == "B_lib":
        return run_library(ctx, a, run_id, config, ctx.runner.baseline_queries.get(a.type, ()))
    if config == "no_verification":
        lib_queries = list(
            dict.fromkeys(
                q
                for h in applicable(ctx.library, a)
                for c in h.checks
                if c.kind == "text"
                for q in c.queries
            )
        )
        return run_library(ctx, a, run_id, config, lib_queries)
    if config == "C":
        return run_full(ctx, a, run_id, with_rag=True, with_report=True)
    if config == "no_rag":
        return run_full(ctx, a, run_id, with_rag=False, with_report=False)
    if config == "no_library":
        return run_no_library(ctx, a, run_id)
    raise ValueError(f"cấu hình lạ: {config!r}")


# ---------------- một lần chạy / cả lượt


def run_one(
    ctx: EvalContext,
    case: CaseRecord,
    config: str,
    run_id: str,
    freeze: Mapping[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RunRecord:
    """Một lần chạy. Lỗi (LLM, dữ liệu) → RunRecord status=error kèm lý do, không dừng cả lượt."""
    n0 = len(ctx.client.records) if ctx.client is not None else 0
    t0 = clock()
    outputs: dict[str, Any] = {}
    status, error = "ok", ""
    a = ctx.anomalies.get(case.anomaly_id)
    try:
        if a is None:
            raise ValueError(f"engine không phát hiện bất thường {case.anomaly_id}")
        outputs = run_config(ctx, a, config, run_id)
    except Exception as e:  # noqa: BLE001 — ghi lại mọi lỗi của lần chạy, không bỏ qua im lặng
        status = "error"
        error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
    latency = clock() - t0
    recs = ctx.client.records[n0:] if ctx.client is not None else []
    failed = [r for r in recs if r.status != "ok"]
    if status == "ok" and failed:
        # C/no_library nuốt lỗi LLM thành UNVERIFIED/báo cáo rỗng (an toàn cho người dùng), nhưng
        # trong đánh giá đó là lỗi hạ tầng, không phải "thiếu dữ liệu": cả lần chạy là lỗi, chạy lại
        status = "error"
        error = "lời gọi LLM thất bại: " + "; ".join(
            f"{r.purpose} ({r.status}: {r.error[:80]})" for r in failed[:5]
        )
    llm = {
        "calls": len(recs),
        "cache_hits": sum(r.cached for r in recs),
        "failures": sum(r.status != "ok" for r in recs),
        "llm_latency_s": round(sum(r.latency_s for r in recs), 3),
        "model": ctx.client.settings.model if ctx.client is not None else None,
        "provider": ctx.client.settings.provider if ctx.client is not None else None,
        "by_purpose": {},
    }
    for r in recs:
        llm["by_purpose"][r.purpose] = llm["by_purpose"].get(r.purpose, 0) + 1
    return RunRecord(
        case_id=case.case_id,
        split=case.split,
        anomaly_id=case.anomaly_id,
        config=config,
        run_id=run_id,
        status=status,
        outputs=outputs,
        tokens={
            "input": sum(r.billed_input_tokens for r in recs),
            "output": sum(r.billed_output_tokens for r in recs),
        },
        latency_s=round(latency, 3),
        llm=llm,
        error=error,
        freeze=dict(freeze) if freeze is not None else None,
        fingerprint=ctx.fingerprint,
    )


def save_record(rec: RunRecord, out_dir: Path) -> Path:
    p = record_path(out_dir, rec.split, rec.config, rec.case_id, rec.run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(rec.to_dict(), ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    tmp.replace(p)
    return p


def load_records(out_dir: Path, split: str) -> list[RunRecord]:
    base = out_dir / split / "runs"
    if not base.is_dir():
        return []
    return [
        RunRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(base.glob("*/*/*.json"))
    ]


def run_eval(
    ctx: EvalContext,
    cases: Sequence[CaseRecord],
    configs: Sequence[str],
    run_ids: Sequence[str],
    *,
    freeze: Mapping[str, Any] | None = None,
    force: bool = False,
    progress: Callable[[str], None] = lambda _: None,
) -> list[RunRecord]:
    """Chạy mọi (ca, cấu hình, lần) còn thiếu. Bản đã có status=ok VÀ cùng `fingerprint` được giữ
    (trừ `force`); bản lỗi hoặc khác phiên bản luôn chạy lại. Tập test: `freeze`
    (FreezeCheck.to_dict()) là bắt buộc, ghi vào mọi bản."""
    splits = {c.split for c in cases}
    if "test" in splits and freeze is None:
        raise RuntimeError("chạy tập test cần kết quả kiểm freeze (spec mục 15.1)")
    out_dir = ctx.runner.output_dir
    done: list[RunRecord] = []
    for case in cases:
        for config in configs:
            for run_id in run_ids:
                p = record_path(out_dir, case.split, config, case.case_id, run_id)
                if p.is_file() and not force:
                    old = RunRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
                    if old.status == "ok" and old.fingerprint == ctx.fingerprint:
                        done.append(old)
                        continue
                    if old.status == "ok":
                        progress(
                            f"{case.case_id} {config} {run_id}: code/config/prompt đã đổi, chạy lại"
                        )
                rec = run_one(ctx, case, config, run_id, freeze if case.split == "test" else None)
                save_record(rec, out_dir)
                done.append(rec)
                msg = f"{case.case_id} {config} {run_id}: {rec.status}"
                if rec.status == "error":
                    msg += f" ({rec.error.splitlines()[0]})"
                progress(msg)
    return done


def estimate_calls(ctx: EvalContext, a: Anomaly, config: str, max_report_attempts: int) -> int:
    """Số lời gọi LLM TỐI ĐA cho một lần chạy (để xem trước chi phí; cache có thể giảm)."""
    lib = applicable(ctx.library, a)
    text_pairs = [h for h in lib if any(c.kind == "text" for c in h.checks)]
    n_text = sum(sum(c.kind == "text" for c in h.checks) for h in text_pairs)
    if config in ("A", "B", "A_lib", "B_lib", "no_verification"):
        return 1
    if config == "C":
        return len(text_pairs) + n_text + max_report_attempts
    if config == "no_library":
        return 1 + 2 * ctx.runner.max_hypotheses
    return 0


def build_context(rs: RunnerSettings, client: LLMClient | None) -> EvalContext:
    """Dựng ngữ cảnh từ dữ liệu thật (giống scripts/run_report.py): OCR → fact → bất thường, kho RAG
    lọc as-of, thư viện CHỈ giả thuyết reviewed. Chậm (đọc toàn bộ OCR); dựng một lần mỗi lượt."""
    import datetime as dt

    from steel_xai.anomaly import detect_anomalies, load_anomaly_settings
    from steel_xai.anomaly.corpus import asof_from_corpus, find_documents, load_corpus
    from steel_xai.config import load_config
    from steel_xai.hypotheses import load_library
    from steel_xai.llm.settings import load_llm_settings
    from steel_xai.normalization import load_normalization_settings, normalize
    from steel_xai.report.settings import load_report_settings
    from steel_xai.retrieval import build_chunks
    from steel_xai.validation import load_validation_settings, validate_facts
    from steel_xai.verification import (
        ExternalStore,
        FactStore,
        context_from_anomaly,
        load_verdict_rules,
        load_verification_settings,
    )

    dataset = load_config("dataset")
    ocr = Path(dataset["sources"]["ocr_dir"])
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
    evidence = client.settings.evidence if client is not None else load_llm_settings().evidence
    chunks = build_chunks(ocr, asof=asof).chunks
    from steel_xai.verification.policy_text import PolicyIndex

    policies = PolicyIndex(chunks)
    return EvalContext(
        anomalies={a.anomaly_id: a for a in anomalies},
        library=tuple(load_library().reviewed),
        quant_ctx=lambda a: context_from_anomaly(
            a, store, external, v_settings, asof_by, policy_texts=policies
        ),
        retriever=BM25Retriever(chunks),
        client=client,
        rules=load_verdict_rules(),
        report_settings=load_report_settings(),
        evidence_settings=evidence,
        runner=rs,
        fingerprint=run_fingerprint(rs),
    )


def run_fingerprint(
    rs: RunnerSettings, eval_settings: Any | None = None, root: Path | None = None
) -> str:
    """Băm những gì quyết định đầu ra của một lần chạy: file trong `freeze_globs` (code, config, dữ
    liệu, thư viện) TRỪ nhãn/ca (`data/eval/`) và `configs/eval.yaml` (giá, tiêu chí... không đổi
    đầu ra), cộng tham số runner có ảnh hưởng và dấu vân tay prompt. Sửa nhãn không bắt chạy lại."""
    import hashlib

    from steel_xai.config import PROJECT_ROOT
    from steel_xai.eval.freeze import default_prompt_fingerprints, file_hash
    from steel_xai.eval.settings import load_eval_settings

    es = eval_settings or load_eval_settings()
    root = root or PROJECT_ROOT
    files: dict[str, str] = {}
    for pattern in es.freeze_globs:
        for p in sorted(root.glob(pattern)):
            rel = p.relative_to(root).as_posix()
            if (
                p.is_file()
                and "__pycache__" not in p.parts
                and not rel.startswith("data/eval/")
                and rel not in ("configs/eval.yaml", "configs/eval_freeze.json")
            ):
                files[rel] = file_hash(p)
    payload = {
        "files": dict(sorted(files.items())),
        "prompts": default_prompt_fingerprints(),
        "runner": {
            "baseline_queries": {k: list(v) for k, v in sorted(rs.baseline_queries.items())},
            "max_hypotheses": rs.max_hypotheses,
            "max_passages": rs.max_passages,
        },
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
