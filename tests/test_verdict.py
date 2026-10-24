"""Golden test Phase 10: gộp check → trạng thái giả thuyết (mục 12.1) và xếp hạng/hòa (mục 12.2).
Thiếu dữ liệu không bao giờ thành SUPPORTED. Không dùng LLM hay mạng."""

import copy
import json
import random
from decimal import Decimal

import pytest
from test_verification import INV, REV, ctx, facts_for

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import (
    Anomaly,
    Check,
    CheckResult,
    CheckStatus,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    Provenance,
    TextCheckStatus,
    VerdictStatus,
)
from steel_xai.hypotheses import load_library
from steel_xai.llm import TextCheckOutcome
from steel_xai.verification import (
    VerdictRules,
    analyze_anomaly,
    build_findings,
    load_verdict_rules,
    rank_verdicts,
    verify_hypothesis,
)
from steel_xai.verification.verdict import STATUS_ORDER

S, P, C, U = (
    VerdictStatus.SUPPORTED,
    VerdictStatus.PARTIAL,
    VerdictStatus.CONTRADICTED,
    VerdictStatus.UNVERIFIED,
)
PASS, FAIL, NE = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.NOT_EVALUABLE


def qcheck(cid, importance="critical", polarity="support", metric="inventory_total"):
    return Check(
        id=cid,
        kind="quant",
        importance=importance,
        polarity=polarity,
        tool="growth_yoy",
        params={"metric": metric},
        pass_when={"op": ">", "value": 0},
    )


def tcheck(cid, importance="critical", polarity="support"):
    return Check(
        id=cid, kind="text", importance=importance, polarity=polarity, queries=("truy vấn",)
    )


def hyp(*checks, hid="h", direction="up", status="reviewed"):
    return Hypothesis(
        id=hid,
        anomaly_type="inventory_increase",
        claim="Tồn kho tăng.",
        business_rationale="r",
        expected_quantitative_evidence=(),
        expected_textual_evidence=(),
        contradictory_evidence=(),
        required_external_data=(),
        critical_missing_evidence=(),
        checks=tuple(checks),
        direction=direction,
        status=status,
    )


def res(cid, status):
    reason = "thiếu dữ liệu" if status is NE else ""
    return CheckResult(
        cid, status, {"value": Decimal("0.5")} if status is not NE else {}, (), reason
    )


def ev(cid, polarity="support", quote="trích dẫn nguyên văn của đoạn gốc"):
    return Evidence("text", polarity, cid, Provenance("doc", 3, chunk_id="doc:c1"), quote)


def tout(cid, status, evidence=(), dropped=(), reason=""):
    return TextCheckOutcome(cid, status, tuple(evidence), tuple(dropped), reason=reason)


F, M, UV = TextCheckStatus.FOUND, TextCheckStatus.MISSING, TextCheckStatus.UNVERIFIED


def status_of(h, quant=None, text=None, rules=None):
    return verify_hypothesis(
        h, {k: res(k, v) for k, v in (quant or {}).items()}, text, rules
    ).status


# ---------------- golden: từng trạng thái (định lượng)

H5 = hyp(
    qcheck("c1"),
    qcheck("c2"),
    qcheck("s1", "supporting"),
    qcheck("k1", "supporting", "contradict"),
    qcheck("kc", "critical", "contradict"),
)


@pytest.mark.parametrize(
    ("quant", "expected"),
    [
        # SUPPORTED: mọi critical-support đạt, critical-contradict đã kiểm và không thấy
        ({"c1": PASS, "c2": PASS, "kc": FAIL}, S),
        (
            {"c1": PASS, "c2": PASS, "kc": FAIL, "s1": FAIL, "k1": FAIL},
            S,
        ),  # supporting trượt: không sao
        # PARTIAL: có ủng hộ nhưng thiếu một mảnh critical
        ({"c1": PASS, "c2": NE, "kc": FAIL}, P),
        (
            {"c1": PASS, "c2": PASS, "kc": NE},
            P,
        ),  # critical-contradict chưa kiểm được → không SUPPORTED
        ({"s1": PASS}, P),  # chỉ có bằng chứng supporting
        ({"c1": PASS}, P),
        # UNVERIFIED: không đủ dữ liệu để nói gì
        ({}, U),
        ({"c1": NE, "c2": NE, "s1": NE, "k1": NE, "kc": NE}, U),
        ({"s1": FAIL}, U),  # supporting không đạt không phải bằng chứng chống lại
        ({"k1": FAIL, "kc": FAIL}, U),  # đã kiểm mà không thấy chống đối, nhưng cũng chưa có ủng hộ
        # CONTRADICTED
        ({"c1": PASS, "c2": FAIL}, P),  # còn critical đạt: chính sách mặc định if_none_pass
        ({"c1": FAIL}, C),
        ({"c1": PASS, "c2": PASS, "k1": PASS}, C),  # có chống đối dù critical đều đạt
        ({"kc": PASS}, C),
        ({"c1": PASS, "c2": PASS, "kc": PASS}, C),
    ],
)
def test_quant_golden_statuses(quant, expected):
    assert status_of(H5, quant) is expected


def test_contradicted_keeps_supporting_evidence_visible_too():
    v = verify_hypothesis(H5, {"c1": res("c1", PASS), "k1": res("k1", PASS)})
    assert v.status is C
    assert [f.check_id for f in v.evidence] == ["c1"]
    assert [f.check_id for f in v.contradictions] == ["k1"]
    assert {f.check_id for f in v.missing} == {"c2", "s1", "kc"}
    assert [f.check_id for f in v.missing][0] in {"c2", "kc"}  # critical đứng trước supporting


def test_hypothesis_without_checks_or_without_critical_support_is_never_supported():
    assert verify_hypothesis(hyp()).status is U
    only_supporting = hyp(qcheck("s1", "supporting"), qcheck("s2", "supporting"))
    assert status_of(only_supporting, {"s1": PASS, "s2": PASS}) is P
    only_contra_critical = hyp(qcheck("kc", "critical", "contradict"))
    assert status_of(only_contra_critical, {"kc": FAIL}) is U


# ---------------- golden: văn bản


T = hyp(tcheck("t1"), tcheck("tk", "critical", "contradict"), tcheck("ts", "supporting"))


def test_text_found_with_verified_quote_supports():
    out = {"t1": tout("t1", F, [ev("t1")])}
    v = verify_hypothesis(T, None, out)
    assert v.status is S  # tk không thấy → mặc định không chặn
    assert [f.check_id for f in v.evidence] == ["t1"] and v.evidence[0].text[0].quote


@pytest.mark.parametrize("status", [UV, M])
def test_text_unverified_or_missing_never_supports(status):
    out = {"t1": tout("t1", status, dropped=[object()] if status is M else (), reason="x")}
    v = verify_hypothesis(T, None, out)
    assert v.status is U and not v.evidence and "t1" in {f.check_id for f in v.missing}
    assert status.value in next(f for f in v.missing if f.check_id == "t1").detail


def test_text_absence_is_never_not_satisfied():
    v = verify_hypothesis(T, None, {"tk": tout("tk", UV)})
    assert all(f.outcome != "not_satisfied" for f in v.evidence + v.contradictions + v.missing)


def test_critical_text_contradict_unknown_is_a_config_choice():
    out = {"t1": tout("t1", F, [ev("t1")])}
    assert verify_hypothesis(T, None, out, VerdictRules(False)).status is S
    blocked = verify_hypothesis(T, None, out, VerdictRules(True))
    assert blocked.status is P and "tk" in {f.check_id for f in blocked.missing}


def test_critical_quant_contradict_unknown_always_blocks_supported():
    h = hyp(tcheck("t1"), qcheck("kq", "critical", "contradict"))
    out = {"t1": tout("t1", F, [ev("t1")])}
    assert verify_hypothesis(h, {"kq": res("kq", NE)}, out).status is P
    assert verify_hypothesis(h, {"kq": res("kq", FAIL)}, out).status is S


def test_text_contradicting_evidence_found_under_a_support_check_contradicts():
    out = {"t1": tout("t1", UV, [ev("t1", "contradict")])}  # check tìm ủng hộ, LLM thấy chống lại
    v = verify_hypothesis(T, None, out)
    assert v.status is C and [f.check_id for f in v.contradictions] == ["t1"]
    both = {"t1": tout("t1", F, [ev("t1", "support"), ev("t1", "contradict", "câu khác hẳn")])}
    v2 = verify_hypothesis(T, None, both)
    assert v2.status is C and v2.evidence and v2.contradictions  # hiển thị cả hai (mục 12.1)


def test_text_contradict_check_found_contradicts_and_support_found_under_it_counts_as_support():
    out = {"tk": tout("tk", F, [ev("tk", "contradict")])}
    assert verify_hypothesis(T, None, out).status is C
    out2 = {"tk": tout("tk", UV, [ev("tk", "support")])}
    v = verify_hypothesis(T, None, out2)
    assert v.status is P and [f.check_id for f in v.evidence] == ["tk"]


def test_mixed_quant_and_text():
    h = hyp(qcheck("q1"), tcheck("t1"))
    q = {"q1": res("q1", PASS)}
    assert verify_hypothesis(h, q, {"t1": tout("t1", F, [ev("t1")])}).status is S
    assert verify_hypothesis(h, q, {"t1": tout("t1", UV)}).status is P
    assert verify_hypothesis(h, q, None).status is P
    assert (
        verify_hypothesis(h, {"q1": res("q1", NE)}, {"t1": tout("t1", F, [ev("t1")])}).status is P
    )


def test_results_for_unknown_or_wrong_kind_checks_are_loud_errors():
    h = hyp(qcheck("q1"), tcheck("t1"))
    with pytest.raises(ValueError, match="không tồn tại"):
        build_findings(h, {"zzz": res("zzz", PASS)})
    with pytest.raises(ValueError, match="không phải quant"):
        build_findings(h, {"t1": res("t1", PASS)})
    with pytest.raises(ValueError, match="không phải text"):
        build_findings(h, None, {"q1": tout("q1", F)})


def test_findings_exist_for_every_library_check_nothing_added_or_dropped():
    fs = build_findings(H5)
    assert [f.check_id for f in fs] == [c.id for c in H5.checks]
    assert all(f.outcome == "unknown" for f in fs)


# ---------------- thiếu dữ liệu không bao giờ thành SUPPORTED


def test_no_results_at_all_is_unverified_for_every_library_hypothesis():
    for h in load_library().reviewed:
        assert verify_hypothesis(h).status is U, h.id


def _random_case(rng):
    checks = []
    for i in range(rng.randint(1, 6)):
        kind = rng.choice(["quant", "quant", "text"])
        make = qcheck if kind == "quant" else tcheck
        checks.append(
            make(
                f"k{i}",
                rng.choice(["critical", "supporting"]),
                rng.choice(["support", "contradict"]),
            )
        )
    quant = {
        c.id: rng.choice([PASS, FAIL, NE])
        for c in checks
        if c.kind == "quant" and rng.random() < 0.9
    }
    text = {c.id: rng.choice([F, M, UV]) for c in checks if c.kind == "text" and rng.random() < 0.9}
    return hyp(*checks), quant, text


def _verdict(h, quant, text):
    texts = {
        k: tout(k, st, [ev(k, next(c.polarity for c in h.checks if c.id == k))] if st is F else [])
        for k, st in text.items()
    }
    return verify_hypothesis(h, {k: res(k, v) for k, v in quant.items()}, texts)


def test_random_invariants_supported_requires_real_evidence_and_missing_data_never_upgrades():
    rng = random.Random(20261024)
    for _ in range(1500):
        h, quant, text = _random_case(rng)
        v = _verdict(h, quant, text)
        crit_support = [
            c for c in h.checks if c.importance == "critical" and c.polarity == "support"
        ]
        by_state = {
            **{k: s is PASS for k, s in quant.items()},
            **{k: s is F for k, s in text.items()},
        }
        if v.status is S:
            assert crit_support and all(by_state.get(c.id, False) for c in crit_support)
            assert not v.contradictions
        # thay MỘT kết quả "đạt" của check ủng hộ bằng "thiếu dữ liệu" không bao giờ làm tốt lên
        for c in h.checks:
            if c.polarity != "support" or not by_state.get(c.id, False):
                continue
            q2, t2 = dict(quant), dict(text)
            (q2 if c.kind == "quant" else t2)[c.id] = NE if c.kind == "quant" else UV
            worse = _verdict(h, q2, t2)
            assert STATUS_ORDER[worse.status] >= STATUS_ORDER[v.status], (
                h.checks,
                quant,
                text,
                c.id,
            )
        # bỏ một kết quả bất kỳ (thành thiếu dữ liệu) không biến UNVERIFIED/PARTIAL thành SUPPORTED
        if v.status is not S:
            for k in list(quant):
                q2 = {kk: vv for kk, vv in quant.items() if kk != k}
                assert _verdict(h, q2, text).status is not S or all(
                    by_state.get(c.id, False) for c in crit_support
                )


# ---------------- xếp hạng


def vd(hid, status, crit_total=0, crit_passed=0, supporting=0):
    return HypothesisVerdict(
        hid,
        status,
        critical_total=crit_total,
        critical_passed=crit_passed,
        supporting_passed=supporting,
    )


def ranks(vs):
    return [(v.hypothesis_id, v.rank) for v in rank_verdicts(vs)]


def test_status_order_is_supported_partial_unverified_contradicted():
    vs = [vd("c", C), vd("u", U), vd("p", P), vd("s", S)]
    assert ranks(vs) == [("s", 1), ("p", 2), ("u", 3), ("c", 4)]


def test_within_status_critical_ratio_then_supporting_count():
    vs = [
        vd("half", P, 2, 1, 5),
        vd("two_thirds", P, 3, 2, 0),
        vd("full", P, 1, 1, 0),
        vd("half_more_supporting", P, 4, 2, 3),  # cùng tỷ lệ 1/2 với "half", ít supporting hơn
    ]
    order = [h for h, _ in ranks(vs)]
    assert order == ["full", "two_thirds", "half", "half_more_supporting"]


def test_exact_ties_share_a_rank_with_competition_numbering():
    vs = [vd("a", S, 2, 2, 1), vd("b", S, 2, 2, 1), vd("c", S, 1, 1, 0), vd("d", P, 1, 0, 0)]
    assert ranks(vs) == [("a", 1), ("b", 1), ("c", 3), ("d", 4)]
    same_ratio = [vd("x", P, 2, 1, 0), vd("y", P, 4, 2, 0)]  # 1/2 == 2/4: hòa chính xác
    assert [r for _, r in ranks(same_ratio)] == [1, 1]


def test_ranking_is_independent_of_input_order_and_does_not_mutate_inputs():
    vs = [
        vd(f"h{i}", s, t, p, q)
        for i, (s, t, p, q) in enumerate(
            [
                (P, 2, 1, 0),
                (S, 1, 1, 0),
                (C, 1, 0, 0),
                (U, 0, 0, 0),
                (P, 3, 2, 1),
                (S, 2, 2, 2),
                (P, 2, 1, 0),
            ]
        )
    ]
    base = ranks(vs)
    rng = random.Random(1)
    for _ in range(25):
        shuffled = vs[:]
        rng.shuffle(shuffled)
        assert ranks(shuffled) == base
    assert all(v.rank is None for v in vs)  # đối tượng gốc không bị sửa


def test_ranking_edge_cases():
    assert rank_verdicts([]) == []
    assert ranks([vd("only", U)]) == [("only", 1)]
    with pytest.raises(ValueError, match="trùng"):
        rank_verdicts([vd("a", S), vd("a", P)])


def test_missing_data_hypothesis_ranks_below_a_partial_one_by_design():
    """Hệ quả có chủ ý của mục 12.2 (RQ3): đúng nhưng không kiểm được thì xếp dưới PARTIAL."""
    unverifiable = verify_hypothesis(hyp(qcheck("c1"), hid="true_but_unverifiable"))
    partial = verify_hypothesis(
        hyp(qcheck("c1"), qcheck("c2"), hid="weak"), {"c1": res("c1", PASS)}
    )
    order = [(v.hypothesis_id, v.status) for v in rank_verdicts([unverifiable, partial])]
    assert order == [("weak", P), ("true_but_unverifiable", U)]


# ---------------- cấu hình & hợp đồng


def test_verdict_rules_config_is_validated():
    assert load_verdict_rules() == VerdictRules(False)
    cfg = copy.deepcopy(load_config("verification"))
    cfg["verdict"]["text_critical_contradict_unknown_blocks"] = "yes"
    with pytest.raises(ConfigError):
        load_verdict_rules(cfg)
    cfg.pop("verdict")
    with pytest.raises(ConfigError):
        load_verdict_rules(cfg)


def test_verdict_roundtrips_through_json():
    h = hyp(qcheck("q1"), tcheck("t1"), qcheck("k", "supporting", "contradict"))
    v = verify_hypothesis(
        h,
        {"q1": res("q1", PASS), "k": res("k", PASS)},
        {
            "t1": tout(
                "t1", F, [ev("t1"), ev("t1", "contradict", "một câu chống lại nguyên văn đủ dài")]
            )
        },
    )
    d = json.loads(json.dumps(v.to_dict(), ensure_ascii=False))
    assert HypothesisVerdict.from_dict(d) == v
    ranked = rank_verdicts([v])[0]
    assert ranked.rank == 1 and HypothesisVerdict.from_dict(ranked.to_dict()) == ranked


def test_counts_for_ranking():
    h = hyp(qcheck("c1"), qcheck("c2"), qcheck("s1", "supporting"), qcheck("s2", "supporting"),
            qcheck("kc", "critical", "contradict"))  # fmt: skip
    v = verify_hypothesis(h, {"c1": res("c1", PASS), "c2": res("c2", NE), "s1": res("s1", PASS),
                              "s2": res("s2", FAIL), "kc": res("kc", FAIL)})  # fmt: skip
    # tỷ lệ chỉ tính trên check critical-support (kc là critical-contradict nên không tính)
    assert (v.critical_total, v.critical_passed, v.supporting_passed) == (2, 1, 1)


# ---------------- điều phối một bất thường


def anomaly(direction="up", type_="inventory_increase"):
    return Anomaly(
        company="AAA",
        period="FY2022",
        type=type_,
        direction=direction,
        indicators={},
        thresholds_hit=[],
        baseline_used=(),
        severity=Decimal(2),
        asof_date="2023-03-10",
    )


def qctx():
    return ctx(INV + REV, direction="up")


def test_analyze_ranks_applicable_hypotheses_and_skips_the_rest():
    good = hyp(qcheck("c1"), hid="good")
    weak = hyp(qcheck("c1"), qcheck("c2", metric="raw_materials"), hid="weak")  # c2 thiếu dữ liệu
    down_only = hyp(qcheck("c1"), hid="down_only", direction="down")
    other_type = Hypothesis(**{**weak.__dict__, "id": "gm", "anomaly_type": "gross_margin_change"})
    a = analyze_anomaly(
        anomaly(), [weak, good, down_only, other_type], quant_ctx=qctx(), text_fn=None
    )
    assert [(v.hypothesis_id, v.rank) for v in a.verdicts] == [("good", 1), ("weak", 2)]
    assert [v.status for v in a.verdicts] == [S, P]  # growth_yoy tồn kho = +50% > 0
    assert set(a.skipped) == {"down_only", "gm"} and a.quant_enabled and not a.text_enabled
    assert json.loads(json.dumps(a.to_dict(), ensure_ascii=False))["verdicts"][0]["rank"] == 1


def test_analyze_ablations_only_ever_lower_the_status():
    h = hyp(qcheck("q1"), tcheck("t1"), hid="h")
    text_fn = lambda hh, aa: {"t1": tout("t1", F, [ev("t1")])}  # noqa: E731
    full = analyze_anomaly(anomaly(), [h], quant_ctx=qctx(), text_fn=text_fn).verdicts[0]
    no_rag = analyze_anomaly(anomaly(), [h], quant_ctx=qctx(), text_fn=None).verdicts[0]
    no_quant = analyze_anomaly(anomaly(), [h], quant_ctx=None, text_fn=text_fn).verdicts[0]
    nothing = analyze_anomaly(anomaly(), [h], quant_ctx=None, text_fn=None).verdicts[0]
    assert (full.status, no_rag.status, no_quant.status, nothing.status) == (S, P, P, U)
    assert "đã tắt" in nothing.missing[0].detail


def test_analyze_refuses_drafts_unless_explicitly_allowed():
    draft = hyp(qcheck("c1"), hid="d", status="draft")
    with pytest.raises(ValueError, match="chưa reviewed"):
        analyze_anomaly(anomaly(), [draft], quant_ctx=qctx(), text_fn=None)
    a = analyze_anomaly(anomaly(), [draft], quant_ctx=qctx(), text_fn=None, allow_draft=True)
    assert a.verdicts[0].status is S


def test_real_library_runs_end_to_end_without_text_and_never_overclaims():
    facts = facts_for("AAA", "net_revenue", {2021: 200, 2022: 220}) + INV
    a = analyze_anomaly(
        anomaly(), load_library().reviewed, quant_ctx=ctx(facts), text_fn=None, allow_draft=True
    )
    assert a.verdicts and all(v.rank is not None for v in a.verdicts)
    for v in a.verdicts:
        h = next(x for x in load_library().reviewed if x.id == v.hypothesis_id)
        if v.status is S:  # SUPPORTED chỉ khi mọi check critical-support là định lượng và đã PASS
            assert all(
                c.kind == "quant"
                for c in h.checks
                if c.importance == "critical" and c.polarity == "support"
            )


# ---------------- vòng review độc lập


HF = hyp(qcheck("c1"), qcheck("c2"), qcheck("s1", "supporting"))


@pytest.mark.parametrize(
    ("quant", "never", "if_none_pass", "always"),
    [
        ({"c1": FAIL}, U, C, C),  # không critical nào đạt, chỉ có FAIL
        ({"c1": FAIL, "s1": PASS}, P, C, C),  # có ủng hộ supporting nhưng không critical nào đạt
        ({"c1": PASS, "c2": FAIL}, P, P, C),  # còn một critical đạt
        ({"c1": PASS, "c2": PASS}, S, S, S),
        ({"c1": FAIL, "c2": FAIL}, U, C, C),
        ({"s1": FAIL}, U, U, U),  # FAIL của check supporting không bao giờ là bằng chứng chống lại
    ],
)
def test_critical_support_fail_policy(quant, never, if_none_pass, always):
    got = [
        status_of(HF, quant, rules=VerdictRules(critical_support_fail_policy=pol))
        for pol in ("never", "if_none_pass", "always")
    ]
    assert got == [never, if_none_pass, always]


def test_failed_expectations_are_always_listed_even_when_not_a_contradiction():
    v = verify_hypothesis(HF, {"c1": res("c1", PASS), "c2": res("c2", FAIL), "s1": res("s1", FAIL)})
    assert v.status is P and not v.contradictions
    assert {f.check_id for f in v.failed} == {"c2", "s1"}  # báo cáo vẫn thấy kỳ vọng không xảy ra
    assert HypothesisVerdict.from_dict(v.to_dict()) == v


def test_policy_value_is_validated():
    cfg = copy.deepcopy(load_config("verification"))
    cfg["verdict"]["critical_support_fail_policy"] = "sometimes"
    with pytest.raises(ConfigError, match="critical_support_fail_policy"):
        load_verdict_rules(cfg)
    assert load_verdict_rules().critical_support_fail_policy == "if_none_pass"


def test_supported_always_has_a_critical_ratio_of_one():
    h = hyp(tcheck("t1"), tcheck("tk", "critical", "contradict"), qcheck("c1"))
    v = verify_hypothesis(h, {"c1": res("c1", PASS)}, {"t1": tout("t1", F, [ev("t1")])})
    assert v.status is S and v.critical_passed == v.critical_total == 2
    plain = verify_hypothesis(hyp(qcheck("c1"), hid="plain"), {"c1": res("c1", PASS)})
    # thêm check critical-contradict chưa kiểm được không làm hạng tụt so với giả thuyết không có nó
    assert [x.rank for x in rank_verdicts([v, plain])] == [1, 1]


def test_text_found_without_a_verified_quote_of_the_right_polarity_is_unknown():
    bare = {"t1": tout("t1", F, [])}  # FOUND nhưng không có bằng chứng nào: không đáng tin
    v = verify_hypothesis(hyp(tcheck("t1")), None, bare)
    assert v.status is U and "thiếu bằng chứng đã kiểm" in v.missing[0].detail
    wrong = {"t1": tout("t1", F, [ev("t1", "contradict")])}  # FOUND nhưng chỉ có chiều ngược
    assert verify_hypothesis(hyp(tcheck("t1")), None, wrong).status is C


def test_misattributed_text_results_are_loud_errors():
    h = hyp(tcheck("ka"))
    with pytest.raises(ValueError, match="gán nhầm"):
        verify_hypothesis(h, None, {"ka": tout("zzz", F, [ev("zzz")])})
    with pytest.raises(ValueError, match="gán nhầm"):
        verify_hypothesis(h, None, {"ka": tout("ka", F, [ev("zzz")])})


def test_verified_text_flag_distinguishes_quant_only_supported_from_text_backed_supported():
    q_only = verify_hypothesis(hyp(qcheck("c1")), {"c1": res("c1", PASS)})
    assert q_only.status is S and not q_only.has_verified_text
    both = verify_hypothesis(
        hyp(qcheck("c1"), tcheck("t1", "supporting")),
        {"c1": res("c1", PASS)},
        {"t1": tout("t1", F, [ev("t1")])},
    )
    assert both.status is S and both.has_verified_text
    assert both.to_dict()["has_verified_text"] is True


def test_draft_guard_only_applies_to_hypotheses_that_would_actually_be_analyzed():
    reviewed = hyp(qcheck("c1"), hid="ok")
    other_type_draft = Hypothesis(
        **{
            **reviewed.__dict__,
            "id": "gm",
            "anomaly_type": "gross_margin_change",
            "status": "draft",
        }
    )
    a = analyze_anomaly(anomaly(), [reviewed, other_type_draft], quant_ctx=qctx(), text_fn=None)
    assert [v.hypothesis_id for v in a.verdicts] == ["ok"] and not a.used_drafts
    draft = hyp(qcheck("c1"), hid="d", status="draft")
    with pytest.raises(ValueError, match="chưa reviewed"):
        analyze_anomaly(anomaly(), [reviewed, draft], quant_ctx=qctx(), text_fn=None)
    flagged = analyze_anomaly(
        anomaly(), [reviewed, draft], quant_ctx=qctx(), text_fn=None, allow_draft=True
    )
    assert flagged.used_drafts and flagged.to_dict()["used_drafts"] is True


def test_rank_is_what_metrics_must_use_not_list_position():
    """Trong nhóm hòa, thứ tự hiển thị theo id nhưng hạng là chung (nhắc cho Phase 12)."""
    vs = rank_verdicts([vd("z_first_alphabetically_last", S, 1, 1), vd("a", S, 1, 1)])
    assert [v.hypothesis_id for v in vs] == ["a", "z_first_alphabetically_last"]
    assert [v.rank for v in vs] == [1, 1]
