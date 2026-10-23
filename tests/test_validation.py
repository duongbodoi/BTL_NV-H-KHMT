"""Test validation (Phase 4) trên fact tổng hợp; fixture thật chỉ là ví dụ ở cuối file."""

import copy
import json
import random
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import FinancialFact, Provenance, ValueStatus
from steel_xai.extraction import extract_document
from steel_xai.normalization import build_context, load_normalization_settings, normalize
from steel_xai.parsing import parse_file
from steel_xai.validation import (
    FAIL,
    NOT_EVALUABLE,
    PASS,
    ReferenceRow,
    draw_sample,
    load_validation_settings,
    read_reference_csv,
    reconciliation_report,
    render_markdown,
    validate_facts,
    write_sample_template,
)

BASE = load_config("validation")
KNOWN = frozenset(load_normalization_settings().metrics)
V = load_validation_settings(known_metrics=KNOWN)


def _settings(mutate=None, **top):
    cfg = copy.deepcopy(BASE)
    cfg.update(top)
    if mutate:
        mutate(cfg)
    return load_validation_settings(cfg, KNOWN)


def mk(
    metric,
    value,
    status=ValueStatus.OK,
    period="FY2023",
    company="AAA",
    end="2023-12-31",
    source="ocr",
):
    v = None if value is None else Decimal(str(value))
    if status is not ValueStatus.OK:
        v = None
    prov = (
        Provenance("doc", 7, block_id="b1", source_text=str(value)) if source != "derived" else None
    )
    return FinancialFact(company, period, metric, v, "VND", status, source, prov, (), (), end)


def _checks(facts, rule, settings=V):
    return [c for c in validate_facts(facts, settings).checks if c.rule == rule]


def _flags(result, metric, period="FY2023", company="AAA"):
    return next(f for f in result.facts if f.key == (company, period, metric)).validation_flags


# ---------------------------------------------------------------- config


def test_repo_config_loads_and_matches_decisions():
    assert V.nil_as_zero is True
    assert V.relative_tolerance == Decimal("0.001")
    assert load_config("dataset")["reconciliation_sample_n"] == 20
    assert {r.id for r in V.relations} >= {"gross_profit_identity", "inventory_net_identity"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(nil_as_zero="yes"),
        lambda c: c.update(relative_tolerance=1),
        lambda c: c.update(relative_tolerance=-0.1),
        lambda c: c.update(relations=[]),
        lambda c: c["relations"][0].update(kind="khac"),
        lambda c: c["relations"][0].update(target="khong_co_metric"),
        lambda c: c["relations"][0]["terms"].append({"metric": "gross_profit", "coef": 1}),
        lambda c: c["relations"][0]["terms"][0].update(coef=2),
        lambda c: c["relations"][1].update(id=c["relations"][0]["id"]),
        lambda c: c["reconciliation"].update(sample_seed="x"),
    ],
)
def test_config_rejects_bad_values(mutate):
    with pytest.raises(ConfigError):
        _settings(mutate)


# ---------------------------------------------------------------- quan hệ kế toán


def _income(rev=1000, cogs=700, gp=300, **kw):
    return [mk("net_revenue", rev, **kw), mk("cogs", cogs, **kw), mk("gross_profit", gp, **kw)]


def test_identity_pass_within_tolerance_and_fail_beyond():
    ok = _checks(_income(gp=300.2), "gross_profit_identity")  # lệch 0,07% < 0,1%
    assert [c.status for c in ok] == [PASS]
    bad = _checks(_income(gp=310), "gross_profit_identity")
    assert [c.status for c in bad] == [FAIL] and bad[0].values["terms_sum"] == "300"


def test_failed_check_flags_all_inputs_but_does_not_change_facts():
    facts = _income(gp=310)
    r = validate_facts(facts, V)
    for m in ("net_revenue", "cogs", "gross_profit"):
        assert "validation_failed:gross_profit_identity" in _flags(r, m)
    by_key = {f.key: f for f in facts}
    for f in r.facts:  # trạng thái và giá trị không đổi
        assert (f.value, f.status) == (by_key[f.key].value, by_key[f.key].status)


def test_inventory_identity_uses_negative_provision():
    facts = [
        mk("inventory_total", 900),
        mk("inventory_gross", 1000),
        mk("inventory_provision", -100),
    ]
    assert [c.status for c in _checks(facts, "inventory_net_identity")] == [PASS]
    wrong = [
        mk("inventory_total", 900),
        mk("inventory_gross", 1000),
        mk("inventory_provision", 100),
    ]
    assert [c.status for c in _checks(wrong, "inventory_net_identity")] == [FAIL]


def test_nil_as_zero_counts_dash_as_zero_and_flags_it():
    facts = [mk("inventory_total", 1000), mk("inventory_gross", 1000),
             mk("inventory_provision", None, ValueStatus.NOT_APPLICABLE)]  # fmt: skip
    r = validate_facts(facts, V)
    c = [c for c in r.checks if c.rule == "inventory_net_identity"][0]
    assert c.status == PASS and "nil_as_zero" in c.flags
    assert "nil_as_zero" in _flags(r, "inventory_provision")
    assert "nil_as_zero" not in _flags(r, "inventory_total")


def test_nil_as_zero_is_configurable():
    s = _settings(nil_as_zero=False)
    facts = [mk("inventory_total", 1000), mk("inventory_gross", 1000),
             mk("inventory_provision", None, ValueStatus.NOT_APPLICABLE)]  # fmt: skip
    c = _checks(facts, "inventory_net_identity", s)[0]
    assert c.status == NOT_EVALUABLE and c.missing == {"inventory_provision": "NOT_APPLICABLE"}


@pytest.mark.parametrize(
    "status", [ValueStatus.MISSING, ValueStatus.UNRESOLVED, ValueStatus.UNKNOWN]
)
def test_missing_or_unresolved_is_never_zero(status):
    facts = [mk("inventory_total", 1000), mk("inventory_gross", 1000),
             mk("inventory_provision", None, status)]  # fmt: skip
    r = validate_facts(facts, V)
    c = [c for c in r.checks if c.rule == "inventory_net_identity"][0]
    assert c.status == NOT_EVALUABLE and c.missing == {"inventory_provision": status.value}
    assert "nil_as_zero" not in _flags(r, "inventory_provision")


def test_absent_components_are_reported_as_missing():
    c = _checks([mk("inventory_gross", 1000)], "inventory_components_bound")[0]
    assert c.status == NOT_EVALUABLE
    assert c.missing == {
        "raw_materials": "MISSING",
        "finished_goods": "MISSING",
        "work_in_progress": "MISSING",
    }


def test_component_bound_pass_fail_and_tolerance():
    comps = lambda a, b, c: [  # noqa: E731
        mk("inventory_gross", 1000), mk("raw_materials", a),
        mk("finished_goods", b), mk("work_in_progress", c),
    ]  # fmt: skip
    assert _checks(comps(500, 300, 200), "inventory_components_bound")[0].status == PASS
    assert _checks(comps(500, 300, 200.5), "inventory_components_bound")[0].status == PASS  # 0,05%
    assert _checks(comps(500, 300, 300), "inventory_components_bound")[0].status == FAIL


def test_relation_skipped_for_company_without_any_input():
    assert _checks([mk("capex", 5)], "gross_profit_identity") == []


def test_non_negative():
    r = validate_facts([mk("net_revenue", -5), mk("inventory_total", 5)], V)
    st = {c.rule: c.status for c in r.checks if c.rule.startswith("non_negative")}
    assert st == {"non_negative:net_revenue": FAIL, "non_negative:inventory_total": PASS}
    assert "validation_failed:non_negative" in _flags(r, "net_revenue")


# ---------------------------------------------------------------- năm tài chính


def test_peer_fiscal_end_difference_is_flagged():
    facts = [
        mk("net_revenue", 1, company="HPG", end="2017-12-31", period="FY2017"),
        mk("net_revenue", 1, company="HSG", end="2017-09-30", period="FY2017"),
    ]
    r = validate_facts(facts, V)
    peer = [c for c in r.checks if c.rule == "peer_fiscal_end_aligned"]
    assert [(c.company, c.status) for c in peer] == [("*", FAIL)]
    for company in ("HPG", "HSG"):
        assert "peer_fiscal_end_differs" in _flags(r, "net_revenue", "FY2017", company)


def test_peer_check_passes_when_aligned_and_is_skipped_for_single_company():
    two = [mk("net_revenue", 1, company=c) for c in ("A", "B")]
    assert [c.status for c in validate_facts(two, V).checks if c.company == "*"] == [PASS]
    one = [mk("net_revenue", 1, company="A")]
    assert not [c for c in validate_facts(one, V).checks if c.company == "*"]


def test_company_fiscal_end_change_is_flagged():
    facts = [
        mk("net_revenue", 1, period="FY2021", end="2021-12-31"),
        mk("net_revenue", 1, period="FY2022", end="2022-12-31"),
        mk("net_revenue", 1, period="FY2023", end="2023-09-30"),
    ]
    r = validate_facts(facts, V)
    st = {c.period: c.status for c in r.checks if c.rule == "fiscal_end_consistent"}
    assert st == {"FY2021": PASS, "FY2022": PASS, "FY2023": FAIL}
    assert "fiscal_end_changed" in _flags(r, "net_revenue", "FY2023")
    assert "fiscal_end_changed" not in _flags(r, "net_revenue", "FY2021")


def test_facts_without_fiscal_end_are_not_judged():
    r = validate_facts([mk("net_revenue", 1, end=None)], V)
    assert not [c for c in r.checks if c.rule.startswith(("fiscal", "peer"))]


# ---------------------------------------------------------------- tổng quát


def test_validation_is_deterministic_and_order_independent():
    facts = _income(gp=310) + [mk("inventory_gross", 10), mk("net_revenue", 2, company="B")]
    base = validate_facts(facts, V).to_dict()
    for seed in range(5):
        shuffled = list(facts)
        random.Random(seed).shuffle(shuffled)
        assert validate_facts(shuffled, V).to_dict() == base
    json.dumps(base)
    assert validate_facts([], V).checks == ()


def test_summary_counts_by_rule():
    s = validate_facts(_income(gp=310) + _income(period="FY2022"), V).summary()
    assert s["gross_profit_identity"] == {"FAIL": 1, "PASS": 1}


# ---------------------------------------------------------------- đối chiếu mẫu


def _many(n=30):
    return [mk("net_revenue", 1000 + i, period=f"FY{2000 + i}") for i in range(n)]


def test_draw_sample_is_deterministic_and_respects_n():
    facts = _many()
    a, b = draw_sample(facts, 20, 7), draw_sample(list(reversed(facts)), 20, 7)
    assert a == b and len(a) == 20 and len({(r.period) for r in a}) == 20
    assert draw_sample(facts, 20, 8) != a
    assert len(draw_sample(facts, 99, 7)) == 30  # ít fact hơn n → trả hết
    assert draw_sample(facts, 0, 7) == []


def test_draw_sample_skips_derived_and_non_ok_facts():
    facts = [
        mk("net_revenue", 1),
        mk("cogs", None, ValueStatus.MISSING),
        mk("gross_margin", 0.3, source="derived"),
    ]
    assert [r.metric for r in draw_sample(facts, 10, 1)] == ["net_revenue"]


def test_sample_template_has_blank_reference_columns(tmp_path):
    path = tmp_path / "sample.csv"
    write_sample_template(draw_sample(_many(3), 3, 1), path)
    refs = read_reference_csv(path)
    assert len(refs) == 3 and all(r.raw == "" for r in refs)
    header = path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.endswith("reference_value,reference_note") and "source_text" in header


def test_reconciliation_report_statuses_and_rate():
    facts = [
        mk("net_revenue", 1_234_567),
        mk("cogs", 1_000),
        mk("gross_profit", 5),
        mk("capex", 9, status=ValueStatus.MISSING),
    ]
    refs = [
        ReferenceRow(
            "AAA", "FY2023", "net_revenue", "1.234.567", "trang 11"
        ),  # khớp (định dạng VN)
        ReferenceRow("AAA", "FY2023", "cogs", "1.001", ""),  # lệch
        ReferenceRow("AAA", "FY2023", "gross_profit", "", ""),  # chưa điền
        ReferenceRow("AAA", "FY2023", "capex", "9", ""),  # có fact nhưng chưa OK
        ReferenceRow("AAA", "FY2023", "raw_materials", "7", ""),  # không có fact
        ReferenceRow("AAA", "FY2023", "inventory_total", "abc", ""),  # không đọc được
    ]
    rep = reconciliation_report(facts, refs, V, expected_n=2)
    counts = rep["counts"]
    assert counts == {
        "MATCH": 1,
        "MISMATCH": 1,
        "NOT_FOUND": 1,
        "FACT_NOT_OK": 1,
        "REFERENCE_PENDING": 1,
        "REFERENCE_UNPARSEABLE": 1,
        "REFERENCE_DUPLICATE": 0,
    }
    assert rep["n_compared"] == 2 and rep["mismatch_rate"] == "0.5"
    assert rep["sample_size_ok"] is True
    cogs = [e for e in rep["entries"] if e["metric"] == "cogs"][0]
    assert Decimal(cogs["deviation_ratio"]) == Decimal(1) / Decimal(1001)


def test_reconciliation_report_flags_small_sample_and_empty():
    rep = reconciliation_report([mk("cogs", 5)], [], V, expected_n=20)
    assert rep["sample_size_ok"] is False and rep["mismatch_rate"] is None
    assert "chưa đủ cỡ mẫu" in render_markdown(rep)
    assert "không phải kết luận thống kê" in render_markdown(rep)


def test_reconciliation_match_tolerance_from_config():
    s = _settings(mutate=lambda c: c["reconciliation"].update(match_tolerance="0.01"))
    facts = [mk("cogs", 1000)]
    ref = [ReferenceRow("AAA", "FY2023", "cogs", "1.005", "")]
    assert reconciliation_report(facts, ref, s, None)["counts"]["MATCH"] == 1
    assert reconciliation_report(facts, ref, V, None)["counts"]["MISMATCH"] == 1


def test_read_reference_csv_requires_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("company,period\nA,FY1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="thiếu cột"):
        read_reference_csv(path)


# ---------------------------------------------------------------- fixture thật (ví dụ)

FIXTURES = Path(__file__).parent / "fixtures"
NS = load_normalization_settings()


def _facts(name: str, company: str):
    doc = parse_file(FIXTURES / f"{name}_consolidated_extracted.txt", name)
    r = normalize(
        extract_document(doc, company=company), settings=NS, contexts=[build_context(doc, NS)]
    )
    return list(r.facts)


def test_hpg2024_identities_hold_on_real_numbers():
    r = validate_facts(_facts("HPG_financial_statements_2024", "HPG"), V)
    by_rule = {(c.period, c.rule): c.status for c in r.checks}
    assert by_rule[("FY2024", "gross_profit_identity")] == PASS
    assert by_rule[("FY2024", "inventory_net_identity")] == PASS
    assert by_rule[("FY2024", "inventory_components_bound")] == PASS


def test_hpg2022_without_note_has_missing_components():
    r = validate_facts(_facts("HPG_financial_statements_2022", "HPG"), V)
    c = [c for c in r.checks if c.rule == "inventory_components_bound" and c.period == "FY2022"][0]
    assert c.status == NOT_EVALUABLE
    assert set(c.missing.values()) == {"MISSING"} and len(c.missing) == 3


def test_hsg_vs_hpg_peer_fiscal_end_flagged():
    def revenue(facts, company, period):
        return [
            f for f in facts if (f.company, f.metric, f.period) == (company, "net_revenue", period)
        ]

    hsg = revenue(_facts("HSG_financial_statements_2017", "HSG"), "HSG", "FY2017")
    hpg_2022 = revenue(_facts("HPG_financial_statements_2022", "HPG"), "HPG", "FY2022")
    # Ghép theo năm tài chính cần cùng FY: dựng cặp FY2017 từ số HPG làm dữ liệu thử.
    hpg = [replace(f, period="FY2017", fiscal_period_end="2017-12-31") for f in hpg_2022]
    peer = [c for c in validate_facts(hpg + hsg, V).checks if c.rule == "peer_fiscal_end_aligned"]
    assert [(c.period, c.status) for c in peer] == [("FY2017", FAIL)]
    assert peer[0].values["month_day"] == "09-end,12-end"
