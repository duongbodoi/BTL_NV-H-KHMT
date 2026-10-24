"""Test hồi quy từ review độc lập Phase 8."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from test_verification import (
    INV,
    D,
    F,
    S,
    _peer_facts,
    _published,
    ctx,
    facts_for,
    monthly,
    not_evaluable,
    quant,
    series_settings,
    value_of,
    write_series,
)

from steel_xai.contracts import Check, CheckStatus, Hypothesis
from steel_xai.verification import (
    ExternalStore,
    UnknownToolError,
    hypothesis_applies,
    read_series_csv,
    run_check,
    validate_check,
)


def test_contra_metric_growth_uses_magnitude_of_negative_balances():
    prov = facts_for("AAA", "inventory_provision", {2021: -100, 2022: -300})
    assert value_of("growth_yoy", {"metric": "inventory_provision"}, ctx(prov)) == D("2")
    neg = facts_for("AAA", "inventory_total", {2021: -100, 2022: -300})
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx(neg), "<= 0")


def test_hypotheses_are_not_applied_to_directions_the_library_does_not_cover():
    def hyp(t):
        return Hypothesis("h", t, "c", "r", (), (), (), (), ())

    assert hypothesis_applies(hyp("inventory_increase"), "up", S)
    assert not hypothesis_applies(hyp("inventory_increase"), "down", S)
    assert hypothesis_applies(hyp("gross_margin_change"), "down", S)
    assert not hypothesis_applies(hyp("gross_margin_change"), None, S)
    assert not hypothesis_applies(hyp("unknown_type"), "up", S)


def test_ratio_can_use_different_periods_for_numerator_and_denominator():
    flow = facts_for("AAA", "cip_transfer_to_fixed_assets", {2021: 10, 2022: 900})
    cip = facts_for("AAA", "construction_in_progress", {2021: 100, 2022: 1000})
    p = {
        "numerator": "cip_transfer_to_fixed_assets",
        "denominator": "construction_in_progress",
        "denominator_period": "prior_year_end",
    }
    assert value_of("ratio", p, ctx(flow + cip)) == D("9")  # 900 (kỳ này) / 100 (số dư đầu kỳ)
    both = {k: v for k, v in p.items() if k != "denominator_period"} | {"period": "prior_year_end"}
    assert value_of("ratio", both, ctx(flow + cip)) == D("0.1")


def test_ratio_of_changes_level_mode_for_flow_items():
    charge = facts_for("AAA", "inventory_provision_charge", {2021: 500, 2022: 500})
    gp = facts_for("AAA", "gross_profit", {2021: 1000, 2022: 900})  # Δ = -100
    p = {
        "numerator": "inventory_provision_charge",
        "denominator": "gross_profit",
        "numerator_mode": "level",
    }
    assert value_of("ratio_of_changes", p, ctx(charge + gp)) == D("5")
    assert value_of("ratio_of_changes", p | {"numerator_mode": "change"}, ctx(charge + gp)) == D(0)


def test_days_inventory_change_records_the_day_counts_it_used():
    inv = facts_for("AAA", "inventory_total", {2020: 100, 2021: 100, 2022: 150})
    cogs = facts_for("AAA", "cogs", {2020: 730, 2021: 730, 2022: 730})
    r = run_check(quant("days_inventory_change", {}, ">", 0), ctx(inv + cogs))
    days = [i for i in r.inputs if i.name == "days_in_fiscal_year"]
    assert {i.period: i.value for i in days} == {"AAA:FY2022": D(365), "AAA:FY2021": D(365)}
    assert all("fiscal_period_end" in str(i.source) for i in days)


def test_peer_result_reports_used_and_excluded_peers_and_omits_unused_inputs():
    facts = _peer_facts({"BBB": 0.1, "CCC": 0.3, "DDD": 0.2})
    facts += facts_for("EEE", "inventory_total", {2022: 120})  # EEE thiếu FY2021 → bị loại
    ab = _published("BBB", "CCC", "DDD", "EEE")
    ab[("DDD", 2022)] = dt.date(2023, 3, 11)  # công bố sau as-of
    chk = quant("growth_vs_peer", {"metric": "inventory_total"}, ">", 0)
    r = run_check(chk, ctx(facts, asof_by=ab))
    assert r.status is CheckStatus.PASS
    assert "peer dùng: BBB, CCC" in r.reason and "DDD" in r.reason and "EEE" in r.reason
    assert not any(i.period.startswith(("DDD", "EEE")) for i in r.inputs)


def test_inconsistent_fiscal_year_end_across_facts_is_not_evaluable():
    inv = [F("AAA", 2021, "inventory_total", 100), F("AAA", 2022, "inventory_total", 150)]
    cogs = [F("AAA", 2021, "cogs", 730), F("AAA", 2022, "cogs", 730, end="2022-06-30")]
    not_evaluable("days_inventory_change", {}, ctx(inv + cogs), "không nhất quán")


def test_partial_price_window_is_flagged_on_the_input(tmp_path):
    st = series_settings(tmp_path)
    vals = monthly({2021: 100, 2022: 120})
    for m in (10, 11, 12):  # thiếu 3 tháng cuối 2022 → còn 9/12, vẫn >= min_months
        vals.pop((2022, m))
    write_series(tmp_path, "iron_ore_price", vals)
    c = ctx(INV, settings=st, external=ExternalStore.load(st))
    chk = quant("lagged_external_series", {"series": "iron_ore_price", "lag": 0}, ">", 0)
    r = run_check(chk, c)
    assert r.status is CheckStatus.PASS
    flagged = [i for i in r.inputs if "partial_window" in i.flags]
    assert len(flagged) == 1 and "9/12" in str(flagged[0].source)


def test_failed_series_leaves_no_dangling_inputs(tmp_path):
    st = series_settings(tmp_path)
    write_series(tmp_path, "iron_ore_price", monthly({2022: 120}))  # có kỳ này, không có kỳ trước
    c = ctx(INV, settings=st, external=ExternalStore.load(st))
    chk = quant("lagged_external_series", {"series": "iron_ore_price", "lag": 0}, ">", 0)
    r = run_check(chk, c)
    assert r.status is CheckStatus.NOT_EVALUABLE and r.inputs == ()


def test_non_finite_fact_values_are_ignored_not_crashing():
    facts = facts_for("AAA", "inventory_total", {2021: 100})
    bad = F("AAA", 2022, "inventory_total", 150)
    for value in (Decimal("NaN"), Decimal("Infinity")):
        not_evaluable(
            "growth_yoy",
            {"metric": "inventory_total"},
            ctx([*facts, replace(bad, value=value)]),
            "thiếu",
        )


BASE = {
    "id": "c",
    "kind": "quant",
    "importance": "critical",
    "polarity": "support",
    "tool": "growth_yoy",
    "params": {"metric": "inventory_total"},
    "pass_when": {"op": ">", "value": 0},
}


@pytest.mark.parametrize(
    "changes",
    [
        {"tool": ["growth_yoy"]},
        {"tool": None},
        {"params": ["metric"]},
        {"params": None},
        {"params": {"metric": ["m"]}},
        {"params": {"metric": None}},
        {"params": {"metric": 5}},
        {"params": {"metric": "typo_metric"}},
        {"pass_when": ["op"]},
        {"pass_when": "x"},
        {"pass_when": None},
        {"pass_when": {"op": ["<"], "value": 1}},
        {"pass_when": {"op": {}, "value": 1}},
        {"pass_when": {"op": ">", "value": "0.1"}},
        {"pass_when": {"op": ">", "value": True}},
        {"pass_when": {"op": ">", "value": float("nan")}},
        {"pass_when": {"op": ">", "value": float("inf")}},
        {"pass_when": {"op": ">", "value": None}},
    ],
)
def test_validate_check_returns_errors_instead_of_raising_on_garbage(changes):
    chk = Check(**{**BASE, **changes})
    assert validate_check(chk, S), changes
    with pytest.raises((ValueError, UnknownToolError)):
        run_check(chk, ctx(INV))


@pytest.mark.parametrize(
    "params",
    [
        {"series": 5, "lag": 0},
        {"series": None, "lag": 0},
        {"series": [], "lag": 0},
        {"series": {"iron_ore_price": 1}, "lag": 0},
        {"series": "gold", "lag": 0},
        {"series": "iron_ore_price", "lag": True},
        {"series": "iron_ore_price", "lag": "FROM_CONFIG"},
        {"series": "iron_ore_price", "lag": -1},
        {"series": "iron_ore_price", "lag": 2.5},
    ],
)
def test_series_tool_params_are_type_checked(params):
    assert validate_check(quant("lagged_external_series", params, ">", 0), S)


def test_typo_in_metric_name_is_a_loud_error_not_missing_data():
    chk = quant("growth_yoy", {"metric": "inventroy_total"}, ">", 0)
    with pytest.raises(ValueError, match="không có trong danh sách metric"):
        run_check(chk, ctx(INV))
    # metric đã biết nhưng Phase 3 chưa trích → chạy được và ra NOT_EVALUABLE
    not_evaluable("growth_yoy", {"metric": "goods_in_transit"}, ctx(INV), "thiếu goods_in_transit")


def test_read_series_csv_is_strict_about_dates_values_and_shape(tmp_path):
    bad = {
        "d1.csv": "date,value\n2023-01-99,1\n",
        "d2.csv": "date,value\n2023-01-xx,1\n",
        "d3.csv": "date,value\n2023-02-30,1\n",
        "big.csv": "date,value\n2023-01,1e400\n",
        "short.csv": "date,value\n2023-01\n",
    }
    for name, text in bad.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            read_series_csv(tmp_path / name)
