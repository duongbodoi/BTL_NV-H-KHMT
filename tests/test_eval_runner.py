"""Test Phase 12: runner A/B/C + chạy phụ + ablation, metric (Hit@k/MRR có hòa hạng, bootstrap,
RQ2/RQ3, chi phí), bảng cho người (ánh xạ mù, chấm trích dẫn), chặn tập test khi chưa có freeze.
TẤT CẢ dùng LLM giả (không gọi mạng, không tốn token)."""

import copy
import csv
import datetime as dt
import json
import math
import re
from dataclasses import replace
from decimal import Decimal

import pytest

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import (
    Anomaly,
    CaseRecord,
    Check,
    FinancialFact,
    Hypothesis,
    Provenance,
    RunRecord,
    ValueStatus,
)
from steel_xai.eval import baselines as bl
from steel_xai.eval import export as X
from steel_xai.eval import metrics as M
from steel_xai.eval.freeze import default_prompt_fingerprints
from steel_xai.eval.runner import (
    EvalContext,
    estimate_calls,
    free_hypotheses,
    load_records,
    record_path,
    run_eval,
    run_one,
)
from steel_xai.eval.settings import load_runner_settings
from steel_xai.hypotheses.library import load_library_settings
from steel_xai.llm import LLMClient, RawCompletion, load_llm_settings
from steel_xai.llm.prompts import CLASSIFY_SCHEMA, REPHRASE_SCHEMA
from steel_xai.report import load_report_settings
from steel_xai.report.prompts import REPORT_SCHEMA
from steel_xai.retrieval import BM25Retriever, load_retrieval_settings
from steel_xai.retrieval.chunking import Chunk
from steel_xai.verification import (
    CheckContext,
    ExternalStore,
    FactStore,
    load_verification_settings,
)
from steel_xai.verification.verdict import load_verdict_rules

D = Decimal
EVAL_CFG = load_config("eval")
VS = load_verification_settings()
RS_REPORT = load_report_settings()
LIB_S = load_library_settings()


def runner_settings(tmp_path, **changes):
    cfg = copy.deepcopy(EVAL_CFG)
    cfg["runner"]["output_dir"] = str(tmp_path / "out")
    cfg["runner"]["sheets_dir"] = str(tmp_path / "sheets")
    cfg["runner"]["bootstrap"]["samples"] = 200
    cfg["runner"].update(changes)
    return load_runner_settings(cfg)


# ---------------- fixture: một bất thường tồn kho tăng, 3 giả thuyết reviewed

ANOMALY = Anomaly(
    company="AAA",
    period="FY2021",
    type="inventory_increase",
    direction="up",
    indicators={"inv_growth_yoy": "0.5"},
    thresholds_hit=({"indicator": "inv_growth_yoy", "direction": "up", "value": "0.5"},),
    baseline_used=("yoy",),
    severity=D("2"),
    asof_date="2022-03-21",
)
NO_ASOF = replace(ANOMALY, period="FY2020", asof_date=None)

P_STOCK = "Công ty tăng dự trữ nguyên liệu do giá quặng sắt ở mức thấp trong năm 2021."
P_POLICY = "Hàng tồn kho được tính theo phương pháp bình quân gia quyền trong năm 2021."
P_FUTURE = "Tài liệu công bố sau mốc as-of: tồn kho giảm mạnh vì xả hàng giá rẻ năm 2022."


def chunk(cid, text, published="2022-03-01", year=2021):
    return Chunk(
        chunk_id=cid,
        doc_id=f"AAA_{year}",
        company="AAA",
        period=f"FY{year}",
        period_year=year,
        doc_published=published,
        published_source="audit_report_date",
        page=3,
        page_end=3,
        section=None,
        kind="text",
        block_ids=(f"AAA_{year}:p0003:b000",),
        text=text,
    )


RETRIEVER = BM25Retriever(
    [
        chunk("AAA_2021:c1", P_STOCK),
        chunk("AAA_2021:c2", P_POLICY),
        chunk("AAA_2022:c1", P_FUTURE, published="2023-03-01", year=2022),
    ],
    load_retrieval_settings(),
)


def hyp(hid, checks, code, claim, direction="up", status="reviewed"):
    return Hypothesis(
        id=hid,
        anomaly_type="inventory_increase",
        claim=claim,
        business_rationale="r",
        expected_quantitative_evidence=("q",),
        expected_textual_evidence=("t",),
        contradictory_evidence=(),
        required_external_data=(),
        critical_missing_evidence=("m",),
        sources=("s",),
        status=status,
        reviewed_by="Người Duyệt" if status == "reviewed" else None,
        checks=tuple(checks),
        code=code,
        direction=direction,
    )


H_STOCK = hyp(
    "h_stock",
    [
        Check(
            "inv_up",
            "quant",
            "critical",
            "support",
            tool="growth_yoy",
            params={"metric": "inventory_total"},
            pass_when={"op": ">=", "value": 0.2},
        ),
        Check("stock_text", "text", "supporting", "support", queries=("dự trữ nguyên liệu",)),
    ],
    "INV-1",
    "Tồn kho tăng do tích trữ nguyên vật liệu khi giá đầu vào thấp.",
)
H_POLICY = hyp(
    "h_policy",
    [Check("policy_text", "text", "critical", "support", queries=("thay đổi chính sách kế toán",))],
    "INV-6",
    "Tồn kho biến động do thay đổi chính sách kế toán.",
    direction="both",
)
H_DEMAND = hyp(
    "h_demand",
    [
        Check(
            "rev_down",
            "quant",
            "critical",
            "support",
            tool="growth_yoy",
            params={"metric": "net_revenue"},
            pass_when={"op": "<", "value": 0},
        ),
    ],
    "INV-3",
    "Tồn kho tăng do nhu cầu yếu.",
)
H_DOWN = hyp("h_down", [], "DEC-1", "Tồn kho giảm do nhu cầu mạnh.", direction="down")
LIBRARY = (H_STOCK, H_POLICY, H_DEMAND, H_DOWN)


def fact(metric, year, value):
    return FinancialFact(
        "AAA",
        f"FY{year}",
        metric,
        D(str(value)),
        "VND",
        ValueStatus.OK,
        "ocr",
        Provenance(f"AAA_{year}", 3, source_text=str(value)),
        (),
        (),
        f"{year}-12-31",
    )


FACTS = [fact("inventory_total", 2020, 100), fact("inventory_total", 2021, 150)]


def quant_ctx(a):
    return CheckContext(
        company=a.company,
        year=int(a.period[2:]),
        direction=a.direction,
        asof=dt.date(2022, 3, 21),
        facts=FactStore(FACTS, VS.reference_only_from_year),
        external=ExternalStore({}, VS),
        settings=VS,
    )


# ---------------- LLM giả: trả lời theo schema của lời gọi


def _chunk_texts(user):
    out = {}
    for m in re.finditer(r'<<<passage-\w+ chunk_id="([^"]+)"[^>]*>>>\n(.*?)\n<<<end', user, re.S):
        out[m.group(1)] = m.group(2)
    return out


class Router:
    """Mỗi schema một câu trả lời hợp lệ. `free_claims` cho phép test đổi khẳng định của A/B."""

    def __init__(self, free_claims=None, fail_schema=None):
        self.calls = []
        self.free_claims = free_claims
        self.fail_schema = fail_schema

    def complete(self, *, model, system, user, schema, max_tokens, effort, timeout_s):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if schema is self.fail_schema:
            raise ConnectionError("mất mạng")
        if schema is bl.FREE_SCHEMA:
            data = json.loads(user)
            p = next(iter(data.get("passages") or []), None)
            claims = self.free_claims
            if claims is None:
                n1 = data["numbers"][0]
                claims = [
                    {
                        "section": "quantitative",
                        "text": f"Tồn kho tăng {n1['display']}.",
                        "sources": [n1["id"]],
                        "quotes": [],
                    },
                    {
                        "section": "top_hypotheses",
                        "text": "Doanh nghiệp có thể tích trữ hàng.",
                        "sources": [],
                        "quotes": [],
                    },
                ]
                if p is not None:
                    claims.append(
                        {
                            "section": "evidence",
                            "text": "Thuyết minh nêu việc dự trữ nguyên liệu.",
                            "sources": [p["id"]],
                            "quotes": [{"source": p["id"], "quote": p["text"][:60]}],
                        }
                    )
            body = {
                "hypotheses": [
                    {"rank": 1, "text": "Tích trữ nguyên liệu giá rẻ"},
                    {"rank": 2, "text": "Nhu cầu tiêu thụ yếu"},
                    {"rank": 2, "text": "Nhu cầu tiêu thụ yếu"},  # lặp: bị bỏ
                    {"rank": 3, "text": "Mở rộng công suất"},
                ],
                "claims": claims,
            }
        elif schema is bl.LIBRARY_SCHEMA:
            ids = [h["hypothesis_id"] for h in json.loads(user)["candidate_hypotheses"]]
            body = {
                "ranking": [{"hypothesis_id": h, "rank": i} for i, h in enumerate(reversed(ids), 1)]
                + [{"hypothesis_id": "bịa", "rank": 9}]
            }
        elif schema is bl.GENERATE_SCHEMA:
            body = {
                "hypotheses": [
                    {"rank": 1, "text": "Dự trữ nguyên liệu", "queries": ["dự trữ nguyên liệu"]},
                    {"rank": 2, "text": "Đổi phương pháp tính giá", "queries": []},
                ]
            }
        elif schema is REPHRASE_SCHEMA:
            body = {"checks": []}
        elif schema is CLASSIFY_SCHEMA:
            claim = re.search(r"GIẢ THUYẾT: (.*)", user).group(1).lower()

            def ok(t):  # chỉ đoạn nói về dự trữ ủng hộ giả thuyết nói về tích trữ/dự trữ
                return "dự trữ" in t and "trữ" in claim

            body = {
                "judgements": [
                    {
                        "chunk_id": cid,
                        "label": "support" if ok(t) else "irrelevant",
                        "quote": t if ok(t) else "",
                        "reason": "r",
                    }
                    for cid, t in _chunk_texts(user).items()
                ]
            }
        elif schema is REPORT_SCHEMA:
            body = {
                "claims": [
                    {
                        "section": "anomaly",
                        "hypothesis_id": "",
                        "text": "Tồn kho tăng bất thường.",
                        "sources": ["N1"],
                    },
                    {
                        "section": "conclusion",
                        "hypothesis_id": "",
                        "text": "Có thể do tích trữ.",
                        "sources": ["H1"],
                    },
                ]
            }
        else:
            raise AssertionError("schema lạ")
        return RawCompletion(json.dumps(body, ensure_ascii=False), 1000, 200, "end_turn", "req")


def llm_client(router):
    cfg = copy.deepcopy(load_config("llm"))
    cfg["cache_dir"] = None
    cfg["call_log"] = None
    return LLMClient(load_llm_settings(cfg), router)


def context(tmp_path, router=None, anomalies=None, **rs):
    router = router or Router()
    c = llm_client(router)
    return EvalContext(
        anomalies=anomalies or {ANOMALY.anomaly_id: ANOMALY},
        library=LIBRARY,
        quant_ctx=quant_ctx,
        retriever=RETRIEVER,
        client=c,
        rules=load_verdict_rules(),
        report_settings=RS_REPORT,
        evidence_settings=c.settings.evidence,
        runner=runner_settings(tmp_path, **rs),
    ), router


CASE = CaseRecord("C01", "dev", ANOMALY.anomaly_id, reference_set=("h_stock",))


# ---------------- config


def test_runner_settings_validation(tmp_path):
    rs = runner_settings(tmp_path)
    assert rs.configs[:3] == ("A", "B", "C") and rs.run_ids() == ("run1", "run2", "run3")
    bad = copy.deepcopy(EVAL_CFG)
    bad["configs"] = ["A", "B"]
    with pytest.raises(ConfigError, match="phải gồm"):
        load_runner_settings(bad)
    bad = copy.deepcopy(EVAL_CFG)
    bad["ablations"] = ["no_rag", "lạ"]
    with pytest.raises(ConfigError, match="tên lạ"):
        load_runner_settings(bad)
    bad = copy.deepcopy(EVAL_CFG)
    bad["runner"]["pricing"]["input_per_mtok"] = -1
    with pytest.raises(ConfigError, match="pricing"):
        load_runner_settings(bad)
    bad = copy.deepcopy(EVAL_CFG)
    bad["runner"]["other_counts_as_hit"] = "no"
    with pytest.raises(ConfigError, match="other_counts_as_hit"):
        load_runner_settings(bad)


def test_freeze_hashes_baseline_prompts():
    fp = default_prompt_fingerprints()
    assert set(fp) == {"baselines", "llm_evidence", "report"}
    assert fp["baselines"] == bl.baseline_prompt_fingerprint()


def test_run_record_roundtrip():
    r = RunRecord("C01", "dev", "a", "A", "run1", "ok", {"x": 1}, {"input": 3}, 1.5)
    assert RunRecord.from_dict(json.loads(json.dumps(r.to_dict()))) == r
    with pytest.raises(ValueError):
        RunRecord("C01", "dev", "a", "A", "run1", "lạ")
    with pytest.raises(ValueError):
        RunRecord("C01", "train", "a", "A", "run1", "ok")


# ---------------- cấu hình


def test_config_A_free_hypotheses_and_report(tmp_path):
    ctx, router = context(tmp_path)
    rec = run_one(ctx, CASE, "A", "run1")
    assert rec.status == "ok", rec.error
    out = rec.outputs
    assert out["mode"] == "free"
    assert [(r["rank"], r["text"]) for r in out["ranking"]] == [
        (1, "Tích trữ nguyên liệu giá rẻ"),
        (2, "Nhu cầu tiêu thụ yếu"),
        (3, "Mở rộng công suất"),
    ]
    first = out["report"]["first_draft"]
    assert first["claims"] == 2 and first["unsupported"] == 1 and first["numeric_mismatches"] == 0
    assert out["report"]["citations"] == []
    payload = json.loads(router.calls[0]["user"])
    assert "passages" not in payload and "candidate_hypotheses" not in payload  # A: không RAG
    assert rec.tokens == {"input": 1000, "output": 200} and rec.llm["calls"] == 1


def test_config_B_uses_asof_filtered_passages_and_verifies_quotes(tmp_path):
    ctx, router = context(tmp_path)
    rec = run_one(ctx, CASE, "B", "run1")
    assert rec.status == "ok", rec.error
    texts = [p["text"] for p in json.loads(router.calls[0]["user"])["passages"]]
    assert P_FUTURE not in texts and P_STOCK in texts  # chống rò rỉ as-of
    cits = rec.outputs["report"]["citations"]
    assert len(cits) == 1 and cits[0]["verified"] and cits[0]["evidence_id"] == "E1"
    assert rec.outputs["report"]["first_draft"]["unsupported"] == 1


def test_baseline_bad_citations_are_not_support(tmp_path):
    claims = [
        {
            "section": "evidence",
            "text": "Đoạn trích nói giá giảm.",
            "sources": ["P1"],
            "quotes": [{"source": "P1", "quote": "câu này không có trong đoạn trích nào cả đâu"}],
        },
        {
            "section": "evidence",
            "text": "Dẫn đoạn mà không trích.",
            "sources": ["P1"],
            "quotes": [],
        },
        {
            "section": "evidence",
            "text": "Dẫn mã bịa.",
            "sources": ["P99", "N77"],
            "quotes": [{"source": "P99", "quote": "x" * 30}],
        },
        {"section": "quantitative", "text": "Tồn kho tăng 99,9%.", "sources": ["N1"], "quotes": []},
    ]
    ctx, _ = context(tmp_path, Router(free_claims=claims))
    rec = run_one(ctx, CASE, "B", "run1")
    rep = rec.outputs["report"]
    reasons = [c["reason"] for c in rep["citations"]]
    assert reasons == ["not_found", "no_quote", "unknown_passage"]
    assert not any(c["verified"] for c in rep["citations"])
    first = rep["first_draft"]
    assert first["unsupported"] == 3  # ba khẳng định không còn nguồn hợp lệ
    assert first["numeric_mismatches"] == 1  # 99,9% không khớp N1 (50,0%)


def test_library_ranking_configs(tmp_path):
    ctx, router = context(tmp_path)
    for cfg in ("A_lib", "B_lib", "no_verification"):
        rec = run_one(ctx, CASE, cfg, "run1")
        assert rec.status == "ok", rec.error
        ids = [r["hypothesis_id"] for r in rec.outputs["ranking"]]
        assert set(ids) == {"h_stock", "h_policy", "h_demand"}  # h_down: khác hướng, bị loại
        assert any("id lạ" in p for p in rec.outputs["problems"])
    users = [json.loads(c["user"]) for c in router.calls]
    assert "passages" not in users[0] and "passages" in users[1] and "passages" in users[2]
    nv = [p["text"] for p in users[2]["passages"]]
    assert P_STOCK in nv  # no_verification: truy vấn lấy từ check văn bản của thư viện


def test_config_C_full_pipeline(tmp_path):
    ctx, _ = context(tmp_path)
    rec = run_one(ctx, CASE, "C", "run1")
    assert rec.status == "ok", rec.error
    out = rec.outputs
    st = {r["hypothesis_id"]: r["status"] for r in out["ranking"]}
    assert st["h_stock"] == "SUPPORTED" and st["h_policy"] == "UNVERIFIED"
    assert st["h_demand"] == "UNVERIFIED"  # thiếu doanh thu → không bao giờ SUPPORTED
    assert out["ranking"][0]["hypothesis_id"] == "h_stock" and out["ranking"][0]["rank"] == 1
    rep = out["report"]
    assert rep["first_draft"]["claims"] == 2
    # LLM giả viết thiếu (không nhắc mọi giả thuyết top-k) → bộ kiểm từ chối hết số lần: bản
    # cuối chỉ còn phần code dựng và KHÔNG được tính là "0 lệch số" (review Phase 12, MEDIUM 5)
    assert rep["status"] == "rejected" and rep["final"] is None
    assert len(out["verdicts"]) == 3


def test_config_no_rag_calls_no_llm(tmp_path):
    ctx, router = context(tmp_path)
    rec = run_one(ctx, CASE, "no_rag", "run1")
    assert rec.status == "ok" and router.calls == [] and rec.llm["calls"] == 0
    st = {r["hypothesis_id"]: r["status"] for r in rec.outputs["ranking"]}
    assert st["h_stock"] == "SUPPORTED"  # check critical định lượng đạt, text chỉ supporting
    assert st["h_policy"] == "UNVERIFIED"


def test_config_no_library(tmp_path):
    ctx, _ = context(tmp_path)
    rec = run_one(ctx, CASE, "no_library", "run1")
    assert rec.status == "ok", rec.error
    out = rec.outputs
    assert out["mode"] == "free"
    st = {r["text"]: r["status"] for r in out["ranking"]}
    assert st["Dự trữ nguyên liệu"] == "SUPPORTED"
    assert st["Đổi phương pháp tính giá"] == "UNVERIFIED"
    gen = out["generated"]
    assert gen[1]["checks"][0]["queries"] == ["Đổi phương pháp tính giá"]  # không có truy vấn
    assert all(h["status"] == "draft" for h in gen)


def test_free_hypotheses_never_reviewed():
    hs = free_hypotheses([{"rank": 1, "text": "x", "queries": ["a", "b", "c", "d"]}], ANOMALY, 5)
    assert hs[0].status == "draft" and hs[0].checks[0].queries == ("a", "b", "c")


def test_missing_anomaly_and_llm_error_become_error_records(tmp_path):
    ctx, _ = context(tmp_path)
    rec = run_one(ctx, replace(CASE, anomaly_id="AAA|FY2019|inventory_increase|up"), "A", "run1")
    assert rec.status == "error" and "không phát hiện" in rec.error
    ctx, _ = context(tmp_path, Router(fail_schema=bl.FREE_SCHEMA))
    rec = run_one(ctx, CASE, "A", "run1")
    assert rec.status == "error" and "LLMError" in rec.error


def test_rag_configs_fail_loudly_without_retrieval(tmp_path):
    """B không được lặng lẽ chạy như A khi không truy xuất được (review Phase 12, MEDIUM 12)."""
    ctx, router = context(tmp_path, anomalies={NO_ASOF.anomaly_id: NO_ASOF})
    rec = run_one(ctx, replace(CASE, anomaly_id=NO_ASOF.anomaly_id), "B", "run1")
    assert rec.status == "error" and "as-of" in rec.error and router.calls == []
    ctx, _ = context(tmp_path)
    ctx.retriever = None
    assert "retriever" in run_one(ctx, CASE, "B_lib", "run1").error
    ctx, _ = context(tmp_path, baseline_queries={"gross_margin_change": ["lợi nhuận gộp"]})
    assert "truy vấn" in run_one(ctx, CASE, "B", "run1").error


def test_retrieve_rejects_documents_after_asof(tmp_path):
    from steel_xai.eval.runner import retrieve

    class Leaky:  # retriever hỏng: trả cả tài liệu công bố sau as-of
        def retrieve_many(self, queries, *, company, asof):
            return RETRIEVER.retrieve_many(queries, company=company, asof="2024-01-01")

    ctx, _ = context(tmp_path)
    ctx.retriever = Leaky()
    with pytest.raises(AssertionError, match="sau as-of"):
        retrieve(ctx, ANOMALY, ["xả hàng giá rẻ"])


def test_run_eval_resume_and_test_requires_freeze(tmp_path):
    ctx, router = context(tmp_path)
    recs = run_eval(ctx, [CASE], ["A", "no_rag"], ["run1", "run2"])
    assert len(recs) == 4 and all(r.status == "ok" for r in recs)
    n = len(router.calls)
    again = run_eval(ctx, [CASE], ["A", "no_rag"], ["run1", "run2"])
    assert len(router.calls) == n and [r.to_dict() for r in again] == [r.to_dict() for r in recs]
    assert record_path(ctx.runner.output_dir, "dev", "A", "C01", "run1").is_file()
    assert len(load_records(ctx.runner.output_dir, "dev")) == 4
    run_eval(ctx, [CASE], ["A"], ["run1"], force=True)
    assert len(router.calls) == n + 1
    test_case = replace(CASE, split="test")
    with pytest.raises(RuntimeError, match="freeze"):
        run_eval(ctx, [test_case], ["A"], ["run1"])
    frz = {"ok": True, "differences": [], "current_sha256": "abc", "unfreeze_reason": None}
    rec = run_eval(ctx, [test_case], ["A"], ["run1"], freeze=frz)[0]
    assert rec.freeze == frz


def test_error_records_are_rerun(tmp_path):
    router = Router(fail_schema=bl.FREE_SCHEMA)
    ctx, _ = context(tmp_path, router)
    assert run_eval(ctx, [CASE], ["A"], ["run1"])[0].status == "error"
    router.fail_schema = None
    assert run_eval(ctx, [CASE], ["A"], ["run1"])[0].status == "ok"


def test_run_ids_reach_llm_cache_key(tmp_path):
    ctx, router = context(tmp_path)
    run_eval(ctx, [CASE], ["A"], ["run1", "run2"])
    keys = {r.cache_key for r in ctx.client.records}
    assert len(keys) == 2  # 3 lần chạy không trùng cache (spec 16)


def test_estimate_calls(tmp_path):
    ctx, _ = context(tmp_path)
    assert estimate_calls(ctx, ANOMALY, "A", 3) == 1
    assert estimate_calls(ctx, ANOMALY, "no_rag", 3) == 0
    assert estimate_calls(ctx, ANOMALY, "C", 3) == 2 + 2 + 3  # 2 cặp có text: 2 rephrase + 2 check
    assert estimate_calls(ctx, ANOMALY, "no_library", 3) == 1 + 2 * 5


# ---------------- xếp hạng của LLM


def test_competition_ranks_and_free_ranking():
    assert bl.competition_ranks([1, 2, 2, 5]) == [1, 2, 2, 4]
    r = bl.free_ranking(
        [
            {"rank": 2, "text": "b"},
            {"rank": 1, "text": " a "},
            {"rank": True, "text": "c"},
            {"rank": 1, "text": ""},
            {"rank": 3, "text": "A"},
        ],
        5,
    )
    assert [(x["rank"], x["text"]) for x in r] == [(1, "a"), (2, "b"), (3, "c")]


def test_library_ranking_puts_omitted_last_tied():
    lib = [H_STOCK, H_POLICY, H_DEMAND]
    r, problems = bl.library_ranking(
        [{"hypothesis_id": "h_policy", "rank": 1}, {"hypothesis_id": "h_policy", "rank": 2}], lib
    )
    assert [(x["hypothesis_id"], x["rank"]) for x in r] == [
        ("h_policy", 1),
        ("h_stock", 2),
        ("h_demand", 2),
    ]
    assert any("lặp" in p for p in problems) and any("bỏ sót" in p for p in problems)


# ---------------- metric


def E(*groups):
    """E(["a"], ["b","c"]) → hạng 1: a; hạng 2 (hòa): b, c."""
    out, pos = [], 1
    for g in groups:
        out += [(pos, frozenset({x})) for x in g]
        pos += len(g)
    return out


def test_hit_and_mrr_without_ties():
    r = E(["a"], ["b"], ["c"])
    assert M.hit_at_k(r, frozenset({"b"}), 1) == 0 and M.hit_at_k(r, frozenset({"b"}), 3) == 1
    assert M.reciprocal_rank(r, frozenset({"c", "b"})) == pytest.approx(0.5)
    assert M.reciprocal_rank(r, frozenset({"z"})) == 0
    assert M.hit_at_k([], frozenset({"a"}), 3) == 0


def test_hit_with_ties_expected_and_bounds():
    r = E(["x"], ["a", "b", "c", "d"])  # 1 đúng trong nhóm hòa 4 mục ở vị trí 2-5
    refs = frozenset({"c"})
    assert M.hit_at_k(r, refs, 3) == pytest.approx(0.5)  # 2 vị trí trong top-3 / 4 mục
    assert M.hit_at_k(r, refs, 3, "optimistic") == 1
    assert M.hit_at_k(r, refs, 3, "pessimistic") == 0
    assert M.hit_at_k(r, refs, 5, "pessimistic") == 1
    two = frozenset({"a", "b"})
    assert M.hit_at_k(r, two, 2) == pytest.approx(0.5)
    assert M.hit_at_k(r, two, 3) == pytest.approx(1 - math.comb(2, 2) / math.comb(4, 2))
    # MRR: vị trí 2..5 đều nhau → (1/2+1/3+1/4+1/5)/4
    assert M.reciprocal_rank(r, refs) == pytest.approx((1 / 2 + 1 / 3 + 1 / 4 + 1 / 5) / 4)
    assert M.reciprocal_rank(r, refs, "optimistic") == pytest.approx(0.5)
    assert M.reciprocal_rank(r, refs, "pessimistic") == pytest.approx(0.2)


def test_tie_expectation_matches_brute_force():
    import itertools

    groups = [["a", "b"], ["c", "d", "e"]]
    refs = frozenset({"b", "d", "e"})
    r = E(*groups)
    perms = [
        [*p1, *p2]
        for p1 in itertools.permutations(groups[0])
        for p2 in itertools.permutations(groups[1])
    ]
    for k in (1, 2, 3, 4):
        brute = sum(any(x in refs for x in p[:k]) for p in perms) / len(perms)
        assert M.hit_at_k(r, refs, k) == pytest.approx(brute)
    brute_rr = sum(1 / (next(i for i, x in enumerate(p) if x in refs) + 1) for p in perms)
    assert M.reciprocal_rank(r, refs) == pytest.approx(brute_rr / len(perms))


def test_bootstrap_and_aggregate():
    lo, hi = M.bootstrap_ci([0.0, 1.0, 1.0, 0.0], 500, 1, 0.95)
    assert 0 <= lo <= 0.5 <= hi <= 1
    assert M.bootstrap_ci([0.0, 1.0, 1.0, 0.0], 500, 1, 0.95) == (lo, hi)  # seed cố định
    assert M.bootstrap_ci([], 10, 1, 0.95) == (None, None)
    e = M.aggregate({"c1": {"r1": 1.0, "r2": 0.0}, "c2": {"r1": 1.0, "r2": None}}, 200, 3, 0.95)
    assert e.mean == pytest.approx(0.75) and e.n_cases == 2 and e.n_runs == 2
    assert e.run_std == pytest.approx(math.sqrt(0.5))  # lần 1: 1,0; lần 2: 0,0
    assert M.aggregate({"c": {"r": None}}, 10, 1, 0.9).mean is None


def rec(config, case, run, ranking, mode="library", **outputs):
    return RunRecord(
        case, "test", f"{case}-a", config, run, "ok", {"mode": mode, "ranking": ranking, **outputs}
    )


def R(rank, hid, status=None, text=""):
    return {"rank": rank, "hypothesis_id": hid, "text": text, "status": status}


BOOT = M.BootCfg(200, 1, 0.95)


def test_ranking_metrics_free_needs_mapping_and_other_rule():
    cases = [CaseRecord("C1", "test", "x|FY1|t|up", reference_set=("h1", "other"))]
    recs = [
        rec("C", "C1", "run1", [R(1, "h2"), R(2, "h1")]),
        rec("A", "C1", "run1", [R(1, None, text="t1"), R(2, None, text="t2")], mode="free"),
    ]
    kw = dict(top_ks=(1, 3), other_id="other", boot=BOOT)
    out = M.ranking_metrics(recs, cases, other_counts=False, mapping=None, **kw)
    assert out["C"]["hit@1:expected"]["mean"] == 0 and out["C"]["hit@3:expected"]["mean"] == 1
    assert out["C"]["mrr:expected"]["mean"] == pytest.approx(0.5)
    assert out["A"] == {"unscored_runs": 1}  # chưa ánh xạ mù → chưa chấm
    mapping = {("C1", "A", "run1", 1): "other", ("C1", "A", "run1", 2): "h3"}
    out = M.ranking_metrics(recs, cases, other_counts=False, mapping=mapping, **kw)
    assert out["A"]["hit@3:expected"]["mean"] == 0  # `other` không tính trúng (mặc định)
    out = M.ranking_metrics(recs, cases, other_counts=True, mapping=mapping, **kw)
    assert out["A"]["hit@1:expected"]["mean"] == 1
    partial = {("C1", "A", "run1", 1): "h1"}  # thiếu mục hạng 2 → không chấm lần chạy đó
    out = M.ranking_metrics(recs, cases, other_counts=False, mapping=partial, **kw)
    assert out["A"] == {"unscored_runs": 1}


def test_ranking_metrics_skip_cases_without_reference_and_errors():
    cases = [
        CaseRecord("C1", "test", "x|FY1|t|up"),
        CaseRecord("C2", "test", "y|FY1|t|up", reference_set=("h1",)),
    ]
    recs = [
        rec("C", "C1", "run1", [R(1, "h1")]),
        rec("C", "C2", "run1", [R(1, "h1")]),
        replace(rec("C", "C2", "run2", [R(1, "h9")]), status="error"),
    ]
    out = M.ranking_metrics(
        recs, cases, top_ks=(1,), other_id="other", other_counts=False, mapping=None, boot=BOOT
    )
    assert out["C"]["hit@1:expected"]["mean"] == 1 and out["C"]["hit@1:expected"]["n_cases"] == 1
    assert out["C"]["unscored_runs"] == 2


def test_verification_metrics_on_c_top3_pairs():
    cases = [
        CaseRecord(
            "C1",
            "test",
            "x|FY1|t|up",
            reference_set=("h1",),
            reference_statuses={"h1": "SUPPORTED", "h2": "PARTIAL", "h4": "PARTIAL"},
        )
    ]
    c = rec(
        "C",
        "C1",
        "run1",
        [
            R(1, "h1", "SUPPORTED"),
            R(2, "h2", "SUPPORTED"),
            R(3, "h3", "PARTIAL"),
            R(4, "h4", "PARTIAL"),
        ],
    )
    nr = rec(
        "no_rag",
        "C1",
        "run1",
        [R(1, "h1", "PARTIAL"), R(2, "h2", "PARTIAL"), R(3, "h4", "PARTIAL")],
    )
    out = M.verification_metrics([c, nr], cases, configs=["C", "no_rag"])
    assert out["C"] == {
        "pairs": 2,
        "verification_accuracy": 0.5,
        "supported_pairs": 2,
        "false_supported_rate": 0.5,
    }
    # no_rag chấm trên CÙNG cặp thuộc top-3 của C (h1, h2), không phải top-3 của chính nó
    assert out["no_rag"]["pairs"] == 2 and out["no_rag"]["verification_accuracy"] == 0.5
    assert out["no_rag"]["false_supported_rate"] is None


def report(first, cits=(), final=None, claims=()):
    return {"first_draft": first, "final": final, "citations": list(cits), "claims": list(claims)}


def draft(claims, unsupported, mism=0, forbidden=0):
    return {
        "claims": claims,
        "unsupported": unsupported,
        "numeric_mismatches": mism,
        "forbidden": forbidden,
        "numbers_checked": 1,
    }


def test_report_metrics_and_human_ratings():
    cits = [{"claim_index": 1, "verified": True}, {"claim_index": 1, "verified": False}]
    recs = [
        rec("B", "C1", "run1", [], report=report(draft(4, 1, mism=2), cits)),
        rec("C", "C1", "run1", [], report=report(draft(5, 0), cits[:1], final=draft(3, 0))),
    ]
    out = M.report_metrics(recs, ratings=None, citation_key=None, boot=BOOT)
    assert out["B"]["unsupported_rate_first"]["mean"] == 0.25
    assert out["B"]["numeric_mismatch_first"]["mean"] == 2
    assert out["B"]["citation_verbatim_rate"]["mean"] == 0.5
    assert "citation_support_rate" not in out["B"]
    assert out["C"]["numeric_mismatch_final"]["mean"] == 0
    key = {("C1", "B", "run1", 1, 0): "Q1", ("C1", "C", "run1", 1, 0): "Q2"}
    out = M.report_metrics(
        recs, ratings={"Q1": "supports", "Q2": "partial"}, citation_key=key, boot=BOOT
    )
    assert out["B"]["citation_support_rate"]["mean"] == 0.5  # trích dẫn thứ 2 không qua kiểm
    assert out["C"]["citation_support_rate"]["mean"] == 0  # partial không tính là hỗ trợ
    out = M.report_metrics(recs, ratings={"Q1": "supports"}, citation_key=key, boot=BOOT)
    assert out["C"]["citation_support_rate"]["mean"] is None  # chưa chấm đủ


def test_cost_metrics(tmp_path):
    r = replace(
        rec("A", "C1", "run1", []),
        tokens={"input": 2_000_000, "output": 1_000_000},
        latency_s=3.0,
        llm={"calls": 2, "cache_hits": 0},
    )
    cached = replace(rec("A", "C2", "run1", []), latency_s=0.01, llm={"calls": 2, "cache_hits": 2})
    out = M.cost_metrics([r, cached], input_per_mtok=1.0, output_per_mtok=5.0, boot=BOOT)
    assert out["A"]["cost"]["mean"] == pytest.approx(7.0) and out["A"]["llm_calls"] == 4
    # lần chạy lấy từ cache (token 0, độ trễ ~0) không kéo số đo xuống (review Phase 12, HIGH 4)
    assert out["A"]["cached_runs_excluded"] == 1 and out["A"]["latency_s"]["n_cases"] == 1
    out = M.cost_metrics([r], input_per_mtok=None, output_per_mtok=None, boot=BOOT)
    assert "cost" not in out["A"] and out["A"]["latency_s"]["mean"] == 3.0


def verdict(h, status, missing=()):
    return {"hypothesis_id": h, "status": status, "missing": list(missing)}


def test_rq3_and_failures():
    miss = {
        "check_id": "p",
        "kind": "quant",
        "importance": "critical",
        "quant": {"reason": "thiếu chuỗi giá hrc; chi tiết"},
        "detail": "",
    }
    recs = [
        rec(
            "C",
            "C1",
            "run1",
            [R(1, "h2"), R(2, "h1")],
            verdicts=[verdict("h1", "UNVERIFIED", [miss]), verdict("h2", "PARTIAL")],
        ),
        rec(
            "C",
            "C1",
            "run2",
            [R(1, "h2"), R(2, "h9")],
            verdicts=[verdict("h1", "UNVERIFIED", [miss])],
        ),
        rec("C", "C2", "run1", [R(1, "h5"), R(2, "h6"), R(3, "h7"), R(4, "h1")]),
    ]
    rows = M.rq3_table(recs)
    assert rows[0]["hypothesis_id"] == "h1" and rows[0]["unverified_rate"] == 1
    assert rows[0]["top_reasons"] == ["thiếu chuỗi giá hrc (×2)"]
    cases = [
        CaseRecord("C1", "test", "a|FY1|t|up", reference_set=("h1",)),
        CaseRecord("C2", "test", "b|FY1|t|up", reference_set=("h1",)),
        CaseRecord("C3", "dev", "c|FY1|t|up"),
    ]
    fails = M.failure_cases(recs, cases, other_id="other", other_counts=False)
    assert [f["case_id"] for f in fails] == ["C2"]  # C1: trúng ở 1/2 lần → không quá nửa
    assert fails[0]["c_top3"] == "h5;h6;h7" and fails[0]["missed_runs"] == "1/1"
    with pytest.raises(ValueError, match="chưa có đáp án"):
        M.failure_cases(
            recs, [CaseRecord("C1", "test", "a|FY1|t|up")], other_id="o", other_counts=False
        )
    with pytest.raises(ValueError, match="không có kết quả C"):
        M.failure_cases([], cases[:1], other_id="o", other_counts=False)


# ---------------- bảng cho người + kết quả


def write(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _end_to_end_records(tmp_path):
    ctx, _ = context(tmp_path)
    case = replace(CASE, split="dev", reference_statuses={"h_stock": "SUPPORTED"})
    recs = run_eval(ctx, [case], ["A", "B", "C", "no_rag", "no_library"], ["run1", "run2"])
    return ctx, case, recs


def test_sheets_blind_and_refuse_overwrite(tmp_path):
    ctx, case, recs = _end_to_end_records(tmp_path)
    rs = ctx.runner
    msgs = X.write_sheets(recs, [case], rs, "dev", {"h_stock": "INV-1"}, salt="s" * 16)
    assert len(msgs) == 3
    sheets, keys, _ = X.split_dirs(rs, "dev")
    blind = X.read_csv(sheets / "blind_mapping_TEMPLATE.csv")
    # A, no_library: 3 + 2 giả thuyết; B: 3; mỗi cấu hình 2 lần chạy
    assert len(blind) == 2 * (3 + 3 + 2)
    assert "config" not in blind[0] and all(r["item_id"].startswith("B") for r in blind)
    cit = X.read_csv(sheets / "citation_rating_TEMPLATE.csv")
    assert cit and all(r["item_id"].startswith("Q") for r in cit) and "config" not in cit[0]
    assert X.read_csv(keys / "citation_key.csv")[0]["salt"] == "s" * 16
    unv = X.read_csv(sheets / "unverified_TEMPLATE.csv")
    assert {r["hypothesis_id"] for r in unv} == {"h_policy", "h_demand"}
    assert unv[0]["runs_unverified"] == "2/2"
    with pytest.raises(FileExistsError):
        X.write_sheets(recs, [case], rs, "dev", {})


def _fill_mapping(rs, value_a, value_b, adjudicate=None):
    sheets, _, _ = X.split_dirs(rs, "dev")
    rows = X.read_csv(sheets / "blind_mapping_TEMPLATE.csv")
    fields = list(rows[0])
    write(
        sheets / "blind_mapping_an.csv",
        fields,
        [{**r, "mapped_id": value_a(r), "annotator": "Nguyễn Văn An"} for r in rows],
    )
    write(
        sheets / "blind_mapping_binh.csv",
        fields,
        [{**r, "mapped_id": value_b(r), "annotator": "Trần Thị Bình"} for r in rows],
    )
    if adjudicate is not None:
        write(sheets / "blind_mapping_adjudication.csv", list(X.ADJ_FIELDS), adjudicate(rows))


def test_mapping_two_annotators_and_adjudication(tmp_path):
    ctx, case, recs = _end_to_end_records(tmp_path)
    rs = ctx.runner
    X.write_sheets(recs, [case], rs, "dev", {})
    taxonomy = {"inventory_increase": {"h_stock": "up", "h_policy": "both", "h_demand": "up"}}

    def first_is_stock(r):
        return "h_stock" if "nguyên liệu" in r["hypothesis_text"].lower() else "other"

    _fill_mapping(
        rs,
        first_is_stock,
        lambda r: "h_demand" if "Nhu cầu" in r["hypothesis_text"] else first_is_stock(r),
    )
    mapping, info = X.final_mapping(rs, "dev", [case], taxonomy, "other", LIB_S)
    assert mapping is None and info["open"] and info["kappa"]["n"] > 0
    _fill_mapping(
        rs,
        first_is_stock,
        lambda r: "h_demand" if "Nhu cầu" in r["hypothesis_text"] else first_is_stock(r),
        lambda rows: [
            {
                "item_id": r["item_id"],
                "value": "h_demand",
                "adjudicator": "Lê Văn Cường",
                "note": "đọc lại",
            }
            for r in rows
            if "Nhu cầu" in r["hypothesis_text"]
        ],
    )
    mapping, info = X.final_mapping(rs, "dev", [case], taxonomy, "other", LIB_S)
    assert info["errors"] == [] and mapping is not None
    assert mapping[("C01", "A", "run1", 1)] == "h_stock"
    assert mapping[("C01", "A", "run1", 2)] == "h_demand"
    m = M.ranking_metrics(
        [r for r in recs if r.config == "A"],
        [case],
        top_ks=(1,),
        other_id="other",
        other_counts=False,
        mapping=mapping,
        boot=BOOT,
    )
    assert m["A"]["hit@1:expected"]["mean"] == 1


def test_adjudicator_must_differ_and_give_reason():
    a = [{"item_id": "i", "mapped_id": "h1", "annotator": "Nguyễn Văn An"}]
    b = [{"item_id": "i", "mapped_id": "h2", "annotator": "Trần Thị Bình"}]
    adj = [{"item_id": "i", "value": "h1", "adjudicator": "Nguyen Van An", "note": "x"}]
    final, open_, errs = X.combine_two(a, b, "mapped_id", adj, LIB_S)
    assert open_ == ["i"] and errs and "khác hai người" in errs[0]
    adj = [{"item_id": "i", "value": "h1", "adjudicator": "Lê Văn Cường", "note": ""}]
    assert "lý do" in X.combine_two(a, b, "mapped_id", adj, LIB_S)[2][0]
    adj[0]["note"] = "ok"
    assert X.combine_two(a, b, "mapped_id", adj, LIB_S)[0] == {"i": "h1"}


def test_citation_ratings_flow(tmp_path):
    ctx, case, recs = _end_to_end_records(tmp_path)
    rs = ctx.runner
    X.write_sheets(recs, [case], rs, "dev", {})
    sheets, _, _ = X.split_dirs(rs, "dev")
    rows = X.read_csv(sheets / "citation_rating_TEMPLATE.csv")
    fields = list(rows[0])
    ratings, ckey, info = X.final_ratings(rs, "dev", LIB_S)
    assert ratings is None and ckey and "cần đúng 2" in info["errors"][0]
    for name, who in (("an", "Nguyễn Văn An"), ("binh", "Trần Thị Bình")):
        write(
            sheets / f"citation_rating_{name}.csv",
            fields,
            [{**r, "rating": "supports", "annotator": who} for r in rows],
        )
    ratings, ckey, info = X.final_ratings(rs, "dev", LIB_S)
    assert info["errors"] == [] and set(ratings.values()) == {"supports"}
    out = M.report_metrics(recs, ratings=ratings, citation_key=ckey, boot=BOOT)
    assert out["B"]["citation_support_rate"]["mean"] == 1
    write(
        sheets / "citation_rating_binh.csv",
        fields,
        [{**r, "rating": "rất tốt", "annotator": "Trần Thị Bình"} for r in rows],
    )
    assert X.final_ratings(rs, "dev", LIB_S)[0] is None


def test_compute_and_write_results(tmp_path):
    ctx, case, recs = _end_to_end_records(tmp_path)
    rs = ctx.runner
    res = X.compute_results(
        recs,
        [case],
        rs,
        "other",
        mapping=None,
        ratings=None,
        citation_key=None,
        human={},
        header=["thử"],
    )
    assert res["ranking"]["C"]["hit@1:expected"]["mean"] == 1
    assert res["verification"]["C"]["verification_accuracy"] == 1
    assert {x["criterion"][:6] for x in res["success"]} >= {"Hit@3:"}
    assert res["success"][0]["result"] == "chưa đủ dữ liệu"  # A/B chưa ánh xạ mù
    paths = X.write_results(res, recs, rs, "dev", {"h_stock": "INV-1"})
    names = {p.name for p in paths}
    assert {"summary.md", "per_case.csv", "rq3.csv", "hit_chart.svg", "status_heatmap.svg"} <= names
    out = X.split_dirs(rs, "dev")[2]
    summary = (out / "summary.md").read_text(encoding="utf-8")
    # câu "khảo sát khả thi" xuất hiện dưới MỌI bảng
    tables = sum(line.startswith("|---") for line in summary.splitlines())
    assert summary.count(rs.disclaimer) == tables >= 6
    assert rs.disclaimer in (out / "per_case.md").read_text(encoding="utf-8")
    assert (out / "hit_chart.svg").read_text(encoding="utf-8").startswith("<svg")
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["disclaimer"]


def test_case_illustration_and_series():
    r = rec(
        "C",
        "C1",
        "run1",
        [R(1, "h1", "PARTIAL")],
        verdicts=[
            {
                "hypothesis_id": "h1",
                "status": "PARTIAL",
                "evidence": [
                    {
                        "quant": {
                            "inputs": [
                                {
                                    "kind": "external",
                                    "name": "hrc_price",
                                    "period": "2021-01",
                                    "value": "600",
                                },
                                {
                                    "kind": "external",
                                    "name": "hrc_price",
                                    "period": "2021-06",
                                    "value": "900",
                                },
                                {
                                    "kind": "fact",
                                    "name": "inventory_total",
                                    "period": "FY2021",
                                    "value": "5",
                                },
                            ]
                        }
                    }
                ],
            }
        ],
        report={"citations": [{"doc_id": "d", "page": 3, "quote": "trích dẫn"}]},
    )
    assert X.series_points(r) == {"hrc_price": [("2021-01", 600.0), ("2021-06", 900.0)]}
    md, svg = X.case_illustration(r, {"h1": "INV-1"}, "khảo sát khả thi")
    assert "INV-1 h1" in md and "trích dẫn" in md and "khảo sát khả thi" in md
    assert "polyline" in svg


def test_vn_number_format():
    assert X.vn(1234.5) == "1.234,50" and X.vn(0.125, 1, pct=True) == "12,5%" and X.vn(None) == "–"
    assert X.fmt_est({"mean": 0.5, "ci_low": 0.2, "ci_high": 0.8, "run_std": 0.1}) == (
        "0,50 [0,20–0,80] ±0,10"
    )


def test_provisional_cases_only_dev_and_one_annotator():
    from steel_xai.eval.labels import Label, LabelSet
    from steel_xai.eval.references import References, provisional_cases

    cases = (
        CaseRecord("C1", "dev", "a|FY1|t|up"),
        CaseRecord("C2", "test", "b|FY1|t|up"),
        CaseRecord("C3", "dev", "c|FY1|t|up"),
    )
    lab = {
        c: Label(c, frozenset({"h1"}), {"h1": "PARTIAL"}, ({"title": "t"},)) for c in ("C1", "C2")
    }
    refs = References(None, cases, False, label_sets=[LabelSet("Nguyễn Văn An", lab)])
    out = provisional_cases(refs, "Nguyen Van An")
    assert [c.case_id for c in out] == ["C1", "C3"]  # ca test bị bỏ
    assert out[0].reference_set == ("h1",) and out[0].reference_statuses == {"h1": "PARTIAL"}
    assert out[1].reference_set == ()  # chưa gán → không có đáp án
    with pytest.raises(ValueError, match="không có file nhãn"):
        provisional_cases(refs, "Trần Thị Bình")


# ---------------- sửa sau review độc lập Phase 12


def test_tied_free_hypotheses_get_separate_mapping_keys(tmp_path):
    """HIGH 1: hai giả thuyết hòa hạng phải có khóa ánh xạ riêng (theo vị trí)."""
    r = rec(
        "A",
        "C1",
        "run1",
        [R(1, None, text="a"), R(2, None, text="b"), R(2, None, text="c")],
        mode="free",
    )
    items = X.blind_items([r])
    assert [i.rank for i in items] == [1, 2, 3]
    mapping = {
        ("C1", "A", "run1", 1): "h9",
        ("C1", "A", "run1", 2): "other",
        ("C1", "A", "run1", 3): "h1",
    }
    assert M.ranking_entries(r, mapping) == [
        (1, frozenset({"h9"})),
        (2, frozenset({"other"})),
        (2, frozenset({"h1"})),
    ]
    case = CaseRecord("C1", "test", "x|FY1|t|up", reference_set=("h1",))
    m = M.ranking_metrics(
        [r], [case], top_ks=(2,), other_id="other", other_counts=False, mapping=mapping, boot=BOOT
    )
    assert m["A"]["hit@2:expected"]["mean"] == pytest.approx(0.5)  # 1 trong 2 mục hòa ở vị trí 2


def test_llm_failure_inside_c_makes_run_error(tmp_path):
    """HIGH 2: lỗi LLM trong C (bị nuốt thành UNVERIFIED) không được tính như thiếu dữ liệu."""
    ctx, _ = context(tmp_path, Router(fail_schema=CLASSIFY_SCHEMA))
    rec_c = run_one(ctx, CASE, "C", "run1")
    assert rec_c.status == "error" and "lời gọi LLM thất bại" in rec_c.error
    assert rec_c.llm["failures"] > 0


def test_resume_reruns_when_fingerprint_changes(tmp_path):
    """HIGH 3: đổi code/config/prompt → bản cũ không được giữ."""
    ctx, router = context(tmp_path)
    ctx.fingerprint = "v1"
    run_eval(ctx, [CASE], ["A"], ["run1"])
    n = len(router.calls)
    run_eval(ctx, [CASE], ["A"], ["run1"])
    assert len(router.calls) == n
    ctx.fingerprint = "v2"
    msgs = []
    rec2 = run_eval(ctx, [CASE], ["A"], ["run1"], progress=msgs.append)[0]
    assert len(router.calls) == n + 1 and rec2.fingerprint == "v2"
    assert any("đã đổi" in m for m in msgs)


def test_run_fingerprint_ignores_labels_but_not_code(tmp_path):
    from steel_xai.eval.runner import run_fingerprint
    from steel_xai.eval.settings import load_eval_settings

    root = tmp_path / "repo"
    files = {
        "src/a.py": "x = 1",
        "data/eval/labels/x.yaml": "a: 1",
        "configs/eval.yaml": "p: 1",
        "configs/llm.yaml": "m: 1",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    es = replace(
        load_eval_settings(),
        freeze_globs=("src/**/*.py", "configs/*.yaml", "data/eval/labels/*.yaml"),
    )
    rs = runner_settings(tmp_path)
    fp = run_fingerprint(rs, es, root)
    (root / "data/eval/labels/x.yaml").write_text("a: 2", encoding="utf-8")
    (root / "configs/eval.yaml").write_text("p: 2", encoding="utf-8")
    assert run_fingerprint(rs, es, root) == fp  # nhãn, giá/tiêu chí: không bắt chạy lại
    (root / "configs/llm.yaml").write_text("m: 2", encoding="utf-8")
    assert run_fingerprint(rs, es, root) != fp
    (root / "configs/llm.yaml").write_text("m: 1", encoding="utf-8")
    rs2 = runner_settings(tmp_path, max_passages=3)
    assert run_fingerprint(rs2, es, root) != fp  # tham số runner ảnh hưởng đầu ra


def test_common_cases_for_cross_config_comparison():
    """MEDIUM 6: so A/B/C trên cùng tập ca."""
    cases = [CaseRecord(c, "test", f"{c}|FY1|t|up", reference_set=("h1",)) for c in ("C1", "C2")]
    recs = [
        rec("C", "C1", "run1", [R(1, "h1")]),
        rec("B", "C1", "run1", [R(1, "h1")]),
        rec("A", "C1", "run1", [R(1, "h1")]),
        rec("A", "C2", "run1", [R(1, "h1")]),
        rec("B", "C2", "run1", [R(1, "h1")]),
        replace(rec("C", "C2", "run1", [R(1, "h1")]), status="error"),
    ]
    assert M.common_cases(recs, cases, ("A", "B", "C"), None) == {"C1"}


def test_mapping_adjudication_rejects_ids_of_other_type(tmp_path):
    """MEDIUM 8: người thứ ba không được ghi id ngoài taxonomy của loại bất thường."""
    ctx, case, recs = _end_to_end_records(tmp_path)
    rs = ctx.runner
    X.write_sheets(recs, [case], rs, "dev", {})
    taxonomy = {"inventory_increase": {"h_stock": "up"}, "gross_margin_change": {"gm_x": "both"}}

    def adjudicate(rows):
        return [
            {"item_id": r["item_id"], "value": "gm_x", "adjudicator": "Lê Văn Cường", "note": "x"}
            for r in rows
        ]

    _fill_mapping(rs, lambda r: "h_stock", lambda r: "other", adjudicate)
    mapping, info = X.final_mapping(rs, "dev", [case], taxonomy, "other", LIB_S)
    assert mapping is None and any("value phải thuộc" in e for e in info["errors"])


def test_unverified_quality_requires_valid_complete_single_file(tmp_path):
    """MEDIUM 9."""
    ctx, case, recs = _end_to_end_records(tmp_path)
    rs = ctx.runner
    X.write_sheets(recs, [case], rs, "dev", {})
    sheets = X.split_dirs(rs, "dev")[0]
    rows = X.read_csv(sheets / "unverified_TEMPLATE.csv")
    fields = list(rows[0])
    filled = [
        {**r, "cause": "public_data_insufficient", "annotator": "Nguyễn Văn An"} for r in rows
    ]
    write(sheets / "unverified_an.csv", fields, filled)
    q = X.unverified_quality(rs, "dev", LIB_S)
    assert q["errors"] == [] and q["public_data_insufficient_rate"] == 1
    write(sheets / "unverified_an.csv", fields, filled[:1])
    q = X.unverified_quality(rs, "dev", LIB_S)
    assert q["public_data_insufficient_rate"] is None and "thiếu cặp" in q["errors"][0]
    write(sheets / "unverified_an.csv", fields, filled)
    write(sheets / "unverified_binh.csv", fields, filled)
    assert X.unverified_quality(rs, "dev", LIB_S)["public_data_insufficient_rate"] is None


def test_results_csv_disclaimer_and_case_pages(tmp_path):
    """MEDIUM 10, 11: CSV có câu "khảo sát khả thi"; có trang ca minh họa."""
    ctx, case, recs = _end_to_end_records(tmp_path)
    rs = ctx.runner
    res = X.compute_results(
        recs,
        [case],
        rs,
        "other",
        mapping=None,
        ratings=None,
        citation_key=None,
        human={},
        header=[],
    )
    X.write_results(res, recs, rs, "dev", {})
    out = X.split_dirs(rs, "dev")[2]
    for name in ("per_case.csv", "rq3.csv"):
        assert rs.disclaimer in (out / name).read_text(encoding="utf-8-sig")
    assert (out / "case_C01.md").is_file() and (out / "case_C01_series.svg").is_file()
    assert "tập ca chung" in (out / "summary.md").read_text(encoding="utf-8")


def test_blind_text_strips_config_traces():
    """MEDIUM 13: bỏ mã nguồn và từ trạng thái khỏi văn bản cho người chấm mù."""
    t = X.blind_text("Giả thuyết H1 là UNVERIFIED (N3, E2); xem P4.")
    assert "H1" not in t and "UNVERIFIED" not in t and "P4" not in t and "N3" not in t
    assert "[trạng thái]" in t and "[mã]" in t


def test_unrated_citation_runs_are_counted():
    """MEDIUM 14."""
    cits = [{"claim_index": 1, "verified": True}]
    recs = [rec("B", "C1", "run1", [], report=report(draft(1, 0), cits))]
    out = M.report_metrics(recs, ratings={}, citation_key={}, boot=BOOT)
    assert out["B"]["citation_unrated_runs"] == 1


def test_report_rejected_rate():
    rejected = {**report(draft(2, 0)), "status": "rejected"}
    accepted = {**report(draft(2, 0), final=draft(2, 0)), "status": "accepted"}
    recs = [
        rec("C", "C1", "run1", [], report=rejected),
        rec("C", "C2", "run1", [], report=accepted),
    ]
    out = M.report_metrics(recs, ratings=None, citation_key=None, boot=BOOT)
    assert out["C"]["report_rejected"]["mean"] == 0.5
    assert out["C"]["numeric_mismatch_final"]["n_cases"] == 1  # bản bị từ chối không tính
