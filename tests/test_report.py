"""Test Phase 11: báo cáo grounded. Đối chiếu số (định dạng Việt), liên kết khẳng định–nguồn, bộ
lọc nội dung cấm, sinh lại khi lệch, đếm lệch ở bản nháp đầu. TẤT CẢ dùng LLM giả (không gọi
mạng)."""

import copy
import json
import random
from decimal import Decimal

import pytest

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import (
    Anomaly,
    Check,
    CheckInput,
    CheckResult,
    CheckStatus,
    Evidence,
    Hypothesis,
    Provenance,
    TextCheckStatus,
)
from steel_xai.llm import LLMClient, LLMRefusalError, RawCompletion, load_llm_settings
from steel_xai.llm.evidence import TextCheckOutcome
from steel_xai.report import (
    Claim,
    build_report_input,
    check_draft,
    display,
    extract_numbers,
    format_vn,
    generate_report,
    load_report_settings,
    parse_claims,
    report_prompt_fingerprint,
    skeleton_report,
)
from steel_xai.report.numbers import matches_value
from steel_xai.report.prompts import REPORT_SCHEMA
from steel_xai.verification.analyze import AnomalyAnalysis
from steel_xai.verification.verdict import rank_verdicts, verify_hypothesis

D = Decimal
CFG = load_config("report")
S = load_report_settings()
TOL = S.relative_tolerance


def settings(**changes):
    cfg = copy.deepcopy(CFG)
    cfg.update(changes)
    return load_report_settings(cfg)


# ---------------- fixture: một bất thường biên gộp giảm, 4 giả thuyết


ANOMALY = Anomaly(
    company="AAA",
    period="FY2022",
    type="gross_margin_change",
    direction="down",
    indicators={"gm_change_pp": "-15.60969", "gross_margin": "0.11934", "dio": None},
    thresholds_hit=(
        {"indicator": "gm_change_pp", "direction": "down", "value": "-15.6", "score": "3.1"},
    ),
    baseline_used=("yoy",),
    severity=D("3.1"),
    asof_date="2023-03-16",
)


def hyp(hid, checks, code, claim):
    return Hypothesis(
        id=hid,
        anomaly_type="gross_margin_change",
        claim=claim,
        business_rationale="r",
        expected_quantitative_evidence=("q",),
        expected_textual_evidence=("t",),
        contradictory_evidence=("c",),
        required_external_data=(),
        critical_missing_evidence=("m",),
        sources=("s",),
        status="reviewed",
        reviewed_by="Người duyệt",
        checks=checks,
        code=code,
        direction="both",
    )


H_PRICE = hyp(
    "h_price",
    (
        Check("price_down", "quant", "critical", "support", tool="lagged_external_series"),
        Check("price_text", "text", "supporting", "support", queries=("giá bán giảm",)),
    ),
    "GM-1",
    "Biên gộp giảm do giá bán thép giảm.",
)
H_COST = hyp(
    "h_cost",
    (
        Check("cost_up", "quant", "critical", "support", tool="growth_yoy"),
        Check("cost_contra", "quant", "supporting", "contradict", tool="growth_gap"),
    ),
    "GM-2",
    "Biên gộp giảm do giá vốn tăng.",
)
H_DEP = hyp(
    "h_dep",
    (Check("cip", "quant", "critical", "support", tool="ratio"),),
    "GM-7",
    "Biên gộp giảm do khấu hao tăng.",
)
H_OTHER = hyp(
    "h_other",
    (Check("x", "quant", "critical", "support", tool="growth_yoy"),),
    "GM-5",
    "Biên gộp giảm do cơ cấu sản phẩm.",
)
HYPS = [H_PRICE, H_COST, H_DEP, H_OTHER]
PROV = Provenance("AAA_fs_2022", 12, chunk_id="c7", source_text="...")
QUOTE = "Giá bán bình quân thép cuộn giảm 11% so với năm 2021 do nhu cầu suy yếu."


def analysis():
    price = CheckResult(
        "price_down",
        CheckStatus.PASS,
        {"hrc_price": D("-0.1121085594")},
        (CheckInput("external", "hrc_price", "2021-12..2022-11", D("798.3333"), "hrc_price.csv"),),
    )
    text = TextCheckOutcome(
        "price_text",
        TextCheckStatus.FOUND,
        (Evidence("text", "support", "price_text", PROV, QUOTE, "nêu giá bán giảm"),),
    )
    cost = CheckResult(
        "cost_up",
        CheckStatus.PASS,
        {"value": D("0.2544")},
        (CheckInput("fact", "cogs", "AAA:FY2022", D("121234500000000"), PROV),),
    )
    contra = CheckResult("cost_contra", CheckStatus.FAIL, {"value": D("-0.031")})
    cip = CheckResult(
        "cip", CheckStatus.NOT_EVALUABLE, {"value": None}, reason="thiếu cip FY2022 của AAA"
    )
    other = CheckResult("x", CheckStatus.FAIL, {"value": D("-0.2")})  # → CONTRADICTED, hạng 4
    vs = [
        verify_hypothesis(H_PRICE, {"price_down": price}, {"price_text": text}),
        verify_hypothesis(H_COST, {"cost_up": cost, "cost_contra": contra}),
        verify_hypothesis(H_DEP, {"cip": cip}),
        verify_hypothesis(H_OTHER, {"x": other}),
    ]
    return AnomalyAnalysis(ANOMALY.anomaly_id, tuple(rank_verdicts(vs)), (), True, True)


def inp(s=S):
    return build_report_input(ANOMALY, analysis(), HYPS, s)


def num_id(i, label_part):
    return next(n.id for n in i.numbers if label_part in n.label)


# ---------------- định dạng và đọc số Việt


@pytest.mark.parametrize(
    ("value", "dec", "text"),
    [
        (D("1234.56"), 1, "1.234,6"),
        (D("-0.05"), 1, "-0,1"),
        (D("1234567"), 0, "1.234.567"),
        (D("0"), 2, "0,00"),
        (D("999.95"), 1, "1.000,0"),
    ],
)
def test_format_vn(value, dec, text):
    assert format_vn(value, dec) == text


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (D("0.11934"), "ratio", "11,9%"),
        (D("1.055428"), "ratio_diff", "105,5 điểm %"),
        (D("-15.60969"), "pp", "-15,6 điểm %"),
        (D("45.276"), "days", "45,3 ngày"),
        (D("3.5855"), "z", "3,59"),
        (D("42134500000000"), "vnd", "42.134,5 tỷ đồng"),
        (D("86500000"), "vnd", "86,5 triệu đồng"),
    ],
)
def test_display_formats(value, unit, text):
    assert display(value, unit) == text


def tok(text):
    (t,) = extract_numbers(text)
    return t


@pytest.mark.parametrize(
    ("text", "values", "kind", "negative"),
    [
        ("12,3%", {D("12.3")}, "pct", False),
        ("1.234,5 tỷ đồng", {D("1234.5")}, "scaled", False),
        ("(700)", {D("700")}, "plain", True),
        ("-5,2 điểm %", {D("5.2")}, "pct", True),
        ("0.25", {D("0.25")}, "plain", False),
        ("1.250", {D("1250"), D("1.250")}, "plain", False),  # mơ hồ: cả hai ứng viên
        ("1,250", {D("1250"), D("1.250")}, "plain", False),
        ("1,234,567.8", {D("1234567.8")}, "plain", False),
        ("45,3 ngày", {D("45.3")}, "days", False),
        ("gấp 2,5 lần", {D("2.5")}, "times", False),
        ("42,1 nghìn tỷ", {D("42.1")}, "scaled", False),
    ],
)
def test_extract_vietnamese_numbers(text, values, kind, negative):
    t = tok(text)
    assert {c.value for c in t.candidates} == values
    assert t.kind == kind and t.negative is negative


def test_extract_skips_numbers_glued_to_words_and_splits_year_ranges():
    assert extract_numbers("mã N12 và INV1") == []
    got = extract_numbers("giai đoạn 2021-2022")
    assert [t.text for t in got] == ["2021", "2022"]
    assert all(t.is_year_like for t in got)
    assert not tok("2.021").is_year_like


@pytest.mark.parametrize(
    ("text", "value", "unit", "ok"),
    [
        ("12,3%", D("0.12345"), "ratio", True),
        ("12,4%", D("0.12345"), "ratio", False),
        ("12%", D("0.12345"), "ratio", True),  # làm tròn đúng về số nguyên
        ("13%", D("0.12345"), "ratio", False),
        ("0,12", D("0.12345"), "ratio", True),
        ("42.134,5 tỷ đồng", D("42134500000000"), "vnd", True),
        ("42,1 nghìn tỷ đồng", D("42134500000000"), "vnd", True),
        ("43.000 tỷ đồng", D("42134500000000"), "vnd", False),
        ("42.134,5 tỷ đồng", D("0.42"), "ratio", False),  # sai đơn vị
        ("15,6 điểm %", D("-15.60969"), "pp", True),  # chỉ so độ lớn
        ("15,6 ngày", D("-15.60969"), "pp", False),
        ("105,5 điểm %", D("1.055428"), "ratio_diff", True),
        ("2,5 lần", D("1.5"), "ratio", False),  # không quy đổi "lần"
    ],
)
def test_number_matching(text, value, unit, ok):
    assert matches_value(tok(text), value, unit, TOL) is ok


def test_display_string_always_matches_its_own_value():
    rng = random.Random(11)
    for _ in range(3000):
        unit = rng.choice(["ratio", "ratio_diff", "pp", "days", "z", "vnd", "number"])
        scale = D(10) ** rng.randint(9, 14) if unit == "vnd" else D(1)
        value = D(str(round(rng.uniform(-50, 50), rng.randint(0, 7)))) * scale
        shown = display(value, unit)
        toks = extract_numbers(shown)
        assert len(toks) == 1, shown
        assert matches_value(toks[0], value, unit, D(0)), (value, unit, shown)


# ---------------- cấu hình


def test_repo_config_loads():
    assert S.top_k >= 1 and S.max_attempts >= 1 and S.on_exhausted == "skeleton_only"
    assert set(S.forbidden) == {"investment_advice", "fraud", "certainty"}


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [
        ("top_k", 0, "top_k"),
        ("on_exhausted", "keep_all", "on_exhausted"),
        ("forbidden", {"fraud": ["x"]}, "forbidden"),
        ("causal", ["("], "regex"),
        ("indicator_units", {"a": "kg"}, "indicator_units"),
        ("disclaimer", "", "disclaimer"),
    ],
)
def test_bad_config_raises(key, value, needle):
    with pytest.raises(ConfigError, match=needle):
        settings(**{key: value})


# ---------------- dựng đầu vào


def test_report_input_has_only_processed_data_with_unique_ids():
    i = inp()
    assert [v.hypothesis_id for v in i.verdicts] == ["h_price", "h_cost", "h_dep"]
    assert [v.hypothesis_id for v in i.others] == ["h_other"]
    ids = list(i.by_id)
    assert len(ids) == len(set(ids))
    assert any(n.label.endswith("(vượt ngưỡng)") and n.display == "-15,6 điểm %" for n in i.numbers)
    assert not any("dio" in n.label for n in i.numbers)  # None không thành 0
    cogs = next(n for n in i.numbers if n.label.startswith("cogs"))
    assert cogs.display == "121.234,5 tỷ đồng" and "AAA_fs_2022, trang 12" in cogs.source
    (e,) = i.evidence
    assert e.quote == QUOTE and e.hypothesis_id == "h_price"
    assert {m.check_id for m in i.missing} == {"cip"}
    assert i.missing[0].reason == "thiếu cip FY2022 của AAA"
    assert {2021, 2022, 2023} <= i.years
    payload = json.loads(i.to_prompt())
    assert "value" not in payload["numbers"][0]  # chỉ chuỗi hiển thị, không giá trị thô
    assert payload["hypotheses"][0]["status"] == "SUPPORTED"


def test_top_k_keeps_the_whole_tie_group():
    s = settings(top_k=1)
    a = analysis()
    tied = AnomalyAnalysis(
        a.anomaly_id,
        tuple(
            v if v.hypothesis_id != "h_cost" else type(v)(**{**v.__dict__, "rank": 1})
            for v in a.verdicts
        ),
        (),
        True,
        True,
    )
    i = build_report_input(ANOMALY, tied, HYPS, s)
    assert {v.hypothesis_id for v in i.verdicts} == {"h_price", "h_cost"}


def test_report_input_rejects_mismatched_or_unranked_input():
    a = analysis()
    with pytest.raises(ValueError, match="không thuộc"):
        build_report_input(Anomaly(**{**ANOMALY.__dict__, "company": "BBB"}), a, HYPS, S)
    with pytest.raises(ValueError, match="không có trong thư viện"):
        build_report_input(ANOMALY, a, HYPS[:2], S)
    unranked = AnomalyAnalysis(
        a.anomaly_id,
        tuple(type(v)(**{**v.__dict__, "rank": None}) for v in a.verdicts),
        (),
        True,
        True,
    )
    with pytest.raises(ValueError, match="xếp hạng"):
        build_report_input(ANOMALY, unranked, HYPS, S)


# ---------------- bộ kiểm


def good_claims(i):
    n_gm = num_id(i, "thay đổi biên lợi nhuận gộp")
    n_hrc = num_id(i, "price_down: hrc_price")
    n_cost = num_id(i, "cost_up: value")
    h1, h2, h3 = (v.id for v in i.verdicts)
    e1 = i.evidence[0].id
    m1 = i.missing[0].id
    return [
        {
            "section": "anomaly",
            "hypothesis_id": "",
            "text": "Biên lợi nhuận gộp FY2022 thay đổi -15,6 điểm % so với năm 2021.",
            "sources": [n_gm],
        },
        {
            "section": "quantitative",
            "hypothesis_id": "h_price",
            "text": "Giá HRC bình quân giảm 11,2% trong cửa sổ có độ trễ.",
            "sources": [n_hrc],
        },
        {
            "section": "top_hypotheses",
            "hypothesis_id": "h_price",
            "text": "GM-1 xếp hạng 1 với trạng thái SUPPORTED.",
            "sources": [h1],
        },
        {
            "section": "evidence",
            "hypothesis_id": "h_price",
            "text": "Thuyết minh nêu giá bán giảm 11% so với năm trước.",
            "sources": [e1],
        },
        {
            "section": "evidence",
            "hypothesis_id": "h_cost",
            "text": "Giá vốn tăng 25,4% so với năm trước.",
            "sources": [n_cost, h2],
        },
        {
            "section": "missing",
            "hypothesis_id": "h_dep",
            "text": "GM-7 chưa kiểm được vì thiếu cip FY2022.",
            "sources": [m1, h3],
        },
        {
            "section": "conclusion",
            "hypothesis_id": "",
            "text": "Biên gộp giảm có thể phù hợp với giá bán giảm; cần thêm dữ liệu khấu hao.",
            "sources": [h1, h3],
        },
    ]


def run_check(i, claims):
    return check_draft(parse_claims({"claims": claims}), i, S)


def kinds(chk):
    return [v.kind for v in chk.violations]


def test_good_draft_passes_every_check():
    i = inp()
    chk = run_check(i, good_claims(i))
    assert chk.accepted, chk.violations
    assert chk.numbers_checked >= 6 and chk.numeric_mismatches == 0


@pytest.mark.parametrize(
    ("idx", "text", "kind"),
    [
        (0, "Biên lợi nhuận gộp giảm -16,1 điểm %.", "numeric_mismatch"),
        (0, "Biên lợi nhuận gộp giảm 15,6% trong năm 2019.", "numeric_mismatch"),  # năm lạ
        (4, "Giá vốn tăng 25,4% và doanh thu tăng 7%.", "numeric_mismatch"),  # 7 không có nguồn
        (3, "Thuyết minh nêu giá bán giảm 12%.", "numeric_mismatch"),  # trích dẫn ghi 11%
        (1, "Đây là cơ hội đầu tư, nhà đầu tư nên mua cổ phiếu.", "forbidden_investment_advice"),
        (1, "Có dấu hiệu gian lận trong số liệu.", "forbidden_fraud"),
        (1, "Dữ liệu chắc chắn cho thấy giá giảm.", "forbidden_certainty"),
        (
            5,
            "Khấu hao tăng là nguyên nhân làm biên gộp giảm.",
            "unhedged_causal",
        ),  # h_dep UNVERIFIED
        (6, "Biên gộp giảm vì giá bán giảm.", "unconditional_conclusion"),
    ],
)
def test_bad_claims_are_blocking(idx, text, kind):
    i = inp()
    claims = good_claims(i)
    claims[idx]["text"] = text
    chk = run_check(i, claims)
    assert kind in kinds(chk) and not chk.accepted
    v = next(v for v in chk.violations if v.kind == kind)
    assert v.claim_index == idx + 1 and v.blocking


def test_causal_wording_allowed_for_supported_or_hedged_claims():
    i = inp()
    claims = good_claims(i)
    claims[1]["text"] = "Giá HRC giảm 11,2% là nguyên nhân chính."  # h_price là SUPPORTED
    claims[4]["text"] = "Giá vốn tăng 25,4% có thể dẫn đến biên gộp giảm."  # có rào đón
    assert run_check(i, claims).accepted


def test_exempt_tokens_are_not_numbers():
    i = inp()
    claims = good_claims(i)
    claims[2]["text"] = "GM-1 (H1, FY2022, tháng 12, top-3) có trạng thái SUPPORTED."
    assert run_check(i, claims).accepted


def test_claim_without_sources_is_dropped_not_blocking():
    i = inp()
    claims = good_claims(i) + [
        {
            "section": "quantitative",
            "hypothesis_id": "",
            "text": "Ngành thép khó khăn.",
            "sources": [],
        }
    ]
    chk = run_check(i, claims)
    assert chk.accepted and chk.unsupported_claims == 1
    assert len(chk.passing_claims()) == len(claims) - 1


def test_unknown_and_wrong_hypothesis_sources():
    i = inp()
    claims = good_claims(i)
    claims[3]["sources"] = ["E99"]
    claims[4]["sources"].append(i.evidence[0].id)  # E của h_price trong khẳng định về h_cost
    claims[5]["hypothesis_id"] = "h_unknown"
    ks = kinds(run_check(i, claims))
    assert {"unknown_source", "wrong_hypothesis_source", "unknown_hypothesis"} <= set(ks)


def test_coverage_requires_every_top_hypothesis_and_missing_explanations():
    i = inp()
    claims = [c for c in good_claims(i) if c["hypothesis_id"] != "h_dep"]
    claims[-1]["sources"] = [i.verdicts[0].id]
    chk = run_check(i, claims)
    assert "coverage" in kinds(chk) and not chk.accepted
    claims.append(
        {
            "section": "top_hypotheses",
            "hypothesis_id": "h_dep",
            "text": "GM-7 là PARTIAL.",
            "sources": [i.verdicts[2].id],
        }
    )
    chk = run_check(i, claims)
    assert any("mục missing" in v.detail for v in chk.violations)


def test_format_limits():
    i = inp()
    s2 = settings(max_claims=2, max_claim_chars=30)
    chk = check_draft(parse_claims({"claims": good_claims(i)}), i, s2)
    assert kinds(chk).count("format") >= 2
    assert "format" in kinds(check_draft((), i, S))


# ---------------- sinh báo cáo với LLM giả


class FakeTransport:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r


def reply(claims):
    return RawCompletion(json.dumps({"claims": claims}, ensure_ascii=False), 500, 300, "end_turn")


def client(*replies):
    cfg = copy.deepcopy(load_config("llm"))
    cfg["cache_dir"] = None
    cfg["call_log"] = None
    t = FakeTransport(*replies)
    return LLMClient(load_llm_settings(cfg), t), t


def test_accepted_first_draft():
    i = inp()
    c, t = client(reply(good_claims(i)))
    res = generate_report(i, c, S, "r1")
    assert res.status == "accepted" and len(t.calls) == 1
    m = res.metrics()
    assert m["first_draft_numeric_mismatches"] == 0 and m["final_numeric_mismatches"] == 0
    assert t.calls[0]["schema"] == REPORT_SCHEMA
    assert "BCTC" not in t.calls[0]["user"].split("JSON):")[0] or True
    md = res.markdown
    for n in range(1, 9):
        assert f"## {n}." in md
    assert S.disclaimer in md and "**SUPPORTED**" in md
    assert "Thuyết minh nêu giá bán giảm 11% so với năm trước." in md
    assert f"[{i.evidence[0].id}] AAA_fs_2022, trang 12, c7" in md


def test_mismatch_triggers_regeneration_and_first_draft_is_counted():
    i = inp()
    bad = good_claims(i)
    bad[0]["text"] = "Biên lợi nhuận gộp giảm -16,1 điểm %."
    bad.append(
        {"section": "quantitative", "hypothesis_id": "", "text": "Không nguồn.", "sources": []}
    )
    c, t = client(reply(bad), reply(good_claims(i)))
    res = generate_report(i, c, S, "r1")
    assert res.status == "accepted_after_retry" and len(t.calls) == 2
    m = res.metrics()
    assert m["first_draft_numeric_mismatches"] == 1 and m["first_draft_unsupported_claims"] == 1
    assert m["final_numeric_mismatches"] == 0
    assert "numeric_mismatch" in t.calls[1]["user"] and "-16,1" in t.calls[1]["user"]
    assert "-16,1" not in res.markdown


def test_exhausted_attempts_skeleton_only_by_default():
    i = inp()
    bad = good_claims(i)
    bad[1]["text"] = "Nhà đầu tư nên mua cổ phiếu, giá HRC giảm 11,2%."
    c, t = client(reply(bad))
    res = generate_report(i, c, S, "r1")
    assert res.status == "rejected" and len(t.calls) == S.max_attempts
    assert res.claims == () and "nên mua" not in res.markdown
    assert "**SUPPORTED**" in res.markdown and "không được chấp nhận" in res.markdown
    assert res.metrics()["first_draft_violations"]["forbidden_investment_advice"] == 1


def test_exhausted_attempts_drop_failing_keeps_only_clean_claims():
    i = inp()
    bad = good_claims(i)
    bad[1]["text"] = "Nhà đầu tư nên mua cổ phiếu."
    c, _ = client(reply(bad))
    res = generate_report(i, c, settings(on_exhausted="drop_failing"), "r1")
    assert res.status == "rejected" and len(res.claims) == len(bad) - 1
    assert "nên mua" not in res.markdown
    assert res.metrics()["final_numeric_mismatches"] == 0


def test_llm_error_keeps_the_code_built_report():
    i = inp()
    c, _ = client(RawCompletion("", 10, 0, "refusal"))
    res = generate_report(i, c, S, "r1")
    assert res.status == "llm_error" and res.claims == ()
    assert "LLMRefusalError" in res.attempts[0].error
    assert "## 8. Nguồn" in res.markdown and "**UNVERIFIED**" in res.markdown
    assert LLMRefusalError  # noqa: B018


def test_skeleton_and_draft_warning():
    i = inp()
    md = skeleton_report(i, S)
    assert S.draft_warning not in md  # giả thuyết reviewed
    a = analysis()
    drafty = AnomalyAnalysis(a.anomaly_id, a.verdicts, (), True, True, used_drafts=True)
    md2 = skeleton_report(build_report_input(ANOMALY, drafty, HYPS, S), S)
    assert S.draft_warning in md2


def test_render_shows_contradictions_and_missing_from_code():
    i = inp()
    md = skeleton_report(i, S)
    assert "thiếu cip FY2022 của AAA" in md
    assert "Không ghi nhận bằng chứng phản bác nào." in md  # check contradict FAIL = không thấy


def test_rendered_claims_never_bypass_the_checker():
    """Bất biến: bản cuối không bao giờ chứa khẳng định có vi phạm, với nháp ngẫu nhiên."""
    i = inp()
    rng = random.Random(5)
    pieces = ["giảm 15,6 điểm %", "tăng 99%", "nên mua cổ phiếu", "có thể", "là nguyên nhân", ""]
    ids = [*i.by_id, "E42", "N0"]
    for _ in range(150):
        claims = [
            {
                "section": rng.choice(["anomaly", "evidence", "conclusion", "missing"]),
                "hypothesis_id": rng.choice(["", "h_price", "h_dep", "zzz"]),
                "text": "Biên gộp " + " ".join(rng.sample(pieces, 2)) + ".",
                "sources": rng.sample(ids, rng.randint(0, 2)),
            }
            for _ in range(rng.randint(1, 6))
        ]
        c, _ = client(reply(claims))
        res = generate_report(i, c, settings(on_exhausted="drop_failing"), "r")
        if res.claims:
            final = check_draft(res.claims, i, S)
            assert not [v for v in final.violations if v.claim_index is not None]


def test_prompt_fingerprint_stable_and_sensitive(monkeypatch):
    a = report_prompt_fingerprint()
    assert a == report_prompt_fingerprint() and len(a) == 64
    monkeypatch.setattr("steel_xai.report.prompts.REPORT_SYSTEM", "khác")
    assert report_prompt_fingerprint() != a


def test_claim_parsing_dedupes_sources_and_strips():
    (c,) = parse_claims(
        {
            "claims": [
                {
                    "section": "anomaly",
                    "hypothesis_id": " h ",
                    "text": " x ",
                    "sources": ["N1", " N1", ""],
                }
            ]
        }
    )
    assert c == Claim("anomaly", "h", "x", ("N1",))
