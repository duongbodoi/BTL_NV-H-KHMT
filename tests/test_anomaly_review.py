"""Test hồi quy từ review độc lập Phase 5."""

import random
from dataclasses import replace
from decimal import Decimal

import pytest
from test_anomaly import (
    CFG,
    DATASET,
    F,
    S,
    _inv_only,
    _set,
    _settings,
    anomalies,
    by_id,
    series,
)

from steel_xai.anomaly import detect_anomalies, load_anomaly_settings
from steel_xai.config import ConfigError
from steel_xai.contracts import FinancialFact, ValueStatus

REAL = load_anomaly_settings()  # cấu hình thật: có dataset.companies = HPG, HSG, NKG


def with_end(facts, end_md):
    return [replace(f, fiscal_period_end=f"{f.period[2:]}-{end_md}") for f in facts]


def ev(res, company, period, type_="inventory_increase"):
    return next(
        e for e in res.evaluations if (e.company, e.period, e.type) == (company, period, type_)
    )


# ---------------- H1: fact trùng khóa


def test_duplicate_key_with_different_values_is_excluded_not_order_dependent():
    base = series("AAA", {2020: (100, 1000, 800, "0.2"), 2021: (150, 1000, 800, "0.2")})
    dup = [F("AAA", 2021, "inventory_total", 300)]
    a = detect_anomalies(base + dup, S, [2021])
    b = detect_anomalies(dup + base, S, [2021])
    assert a.to_dict() == b.to_dict()
    assert any(n.startswith("duplicate_conflict:AAA|FY2021|inventory_total") for n in a.notes)
    assert not ev(a, "AAA", "FY2021").evaluable  # không chọn bừa một trong hai giá trị


def test_duplicate_key_prefers_non_comparative_fact():
    base = series("AAA", {2020: (100, 1000, 800, "0.2"), 2021: (150, 1000, 800, "0.2")})
    other = F("AAA", 2021, "inventory_total", 300, flags=("comparative_only",))
    res = detect_anomalies(base + [other], S, [2021])
    inv = by_id(res.anomalies)["AAA|FY2021|inventory_increase|up"]
    assert Decimal(inv.indicators["inv_growth_yoy"]) == Decimal("0.5") and not res.notes


def test_duplicate_key_with_same_value_is_harmless():
    base = series("AAA", {2020: (100, 1000, 800, "0.2"), 2021: (150, 1000, 800, "0.2")})
    res = detect_anomalies(base + [F("AAA", 2021, "inventory_total", 150)], S, [2021])
    assert ev(res, "AAA", "FY2021").evaluable and not res.notes


# ---------------- M1: mẫu số đầy đủ


def test_period_with_only_broken_facts_still_counts_in_the_denominator():
    facts = series("AAA", {2020: (100, 1, 1, "0.2"), 2021: (150, 1, 1, "0.2")})
    facts.append(F("AAA", 2022, "inventory_total", 0, status=ValueStatus.UNRESOLVED))
    res = detect_anomalies(facts, S, [2021, 2022])
    e = ev(res, "AAA", "FY2022")
    assert not e.evaluable and e.reason == "no_data"
    assert res.summary()["inventory_increase"]["periods_total"] == 2


def test_configured_companies_without_any_data_are_listed():
    res = detect_anomalies([], REAL)
    assert {(e.company, e.period, e.reason) for e in res.evaluations} == {
        (c, f"FY{y}", "no_data") for c in ("HPG", "HSG", "NKG") for y in (2021, 2022, 2023)
    }
    s = res.summary()["inventory_increase"]
    assert s["periods_total"] == 9 and s["periods_evaluated"] == 0 and s["anomaly_rate"] is None


# ---------------- M2/M3: peer và năm tài chính lệch


def test_peers_with_later_fiscal_end_are_excluded():
    # AAA kết thúc 30/9; hai peer kết thúc 31/12 cùng năm: số của họ chưa có khi AAA khóa sổ
    facts = (
        with_end(_inv_only(0.45, "AAA"), "09-30")
        + with_end(_inv_only(0.0, "BBB"), "12-31")
        + with_end(_inv_only(0.0, "CCC"), "12-31")
    )
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert a.indicators["inv_growth_vs_peer"] is None and "peer" not in a.baseline_used
    assert {"peer_missing", "peer_excluded_later_fiscal_end"} <= set(a.flags)


def test_peers_with_earlier_fiscal_end_are_used_and_flagged_as_different():
    facts = (
        with_end(_inv_only(0.45, "AAA"), "12-31")
        + with_end(_inv_only(0.10, "BBB"), "09-30")
        + with_end(_inv_only(0.20, "CCC"), "09-30")
    )
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert Decimal(a.indicators["inv_growth_vs_peer"]) == Decimal("0.30")
    assert "peer_fiscal_end_differs" in a.flags and "peer" in a.baseline_used


def test_unknown_fiscal_end_is_flagged_not_treated_as_equal():
    no_end = [replace(f, fiscal_period_end=None) for f in _inv_only(0.45, "AAA")]
    facts = no_end + _inv_only(0.10, "BBB") + _inv_only(0.20, "CCC")
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert "fiscal_end_unknown" in a.flags


def test_company_that_is_not_a_peer_does_not_raise_the_fiscal_end_flag():
    facts = _inv_only(0.45, "AAA") + _inv_only(0.10, "BBB") + _inv_only(0.20, "CCC")
    lone = [
        F("DDD", 2021, "inventory_total", 100, end="2021-09-30")
    ]  # không có FY2020 → không là peer
    a = by_id(anomalies(facts + lone, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert "peer_fiscal_end_differs" not in a.flags


# ---------------- M4: cờ chất lượng của peer và lịch sử


def test_flags_of_peer_and_history_inputs_are_propagated_with_their_own_tags():
    bad = ("restated_later", "validation_failed:x")
    peer = [replace(f, validation_flags=bad) for f in _inv_only(0.10, "BBB")]
    old = F("AAA", 2018, "inventory_total", 90, flags=("restated_later",))
    facts = _inv_only(0.45, "AAA") + peer + _inv_only(0.20, "CCC") + [old]
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert {"input_peer:restated_later", "input_peer:validation_failed:x"} <= set(a.flags)
    assert "input_history:restated_later" in a.flags
    assert not any(f.startswith("input:") for f in a.flags)  # chính AAA năm y, y-1 sạch


def test_future_peer_data_does_not_change_results():
    facts = _inv_only(0.45, "AAA") + _inv_only(0.10, "BBB") + _inv_only(0.20, "CCC")
    future = (
        series("BBB", {2022: (1, 1, 1, "0.9"), 2023: (900, 1, 1, "0.01")})
        + series("CCC", {2022: (3, 1, 1, "0.5")})
        + series("BBB", {2024: (9, 1, 1, "0.9")})
    )
    assert (
        detect_anomalies(facts, S, [2021]).to_dict()
        == detect_anomalies(facts + future, S, [2021]).to_dict()
    )


# ---------------- L1-L5


def test_duplicate_anomaly_types_in_config_are_rejected():
    types = ["inventory_increase", "inventory_increase", "gross_margin_change"]
    with pytest.raises(ConfigError):
        _settings(_set(("anomaly_types",), types))


def test_days_flag_only_when_fiscal_end_is_missing():
    rows = {2019: (100, 1000, 800, "0.2"), 2020: (100, 1000, None, "0.2")}
    rows[2021] = (200, 1000, 800, "0.2")
    a = by_id(anomalies(series("AAA", rows), years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert "days_in_period_missing" not in a.flags  # thiếu giá vốn, không phải thiếu ngày
    no_end = [replace(f, fiscal_period_end=None) for f in series("AAA", rows)]
    b = by_id(anomalies(no_end, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert "days_in_period_missing" in b.flags


def test_first_year_of_series_does_not_carry_the_days_flag():
    facts = series("AAA", {2020: (100, 1000, 800, "0.2"), 2021: (200, 1000, 800, "0.2")})
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert "days_in_period_missing" not in a.flags


def test_foreign_unit_facts_are_rejected_and_reported():
    facts = series("AAA", {2020: (100, 1000, 800, "0.2"), 2021: (150, 1000, 800, "0.2")})
    facts = [
        replace(f, unit="million_VND")
        if (f.metric, f.period) == ("inventory_total", "FY2021")
        else f
        for f in facts
    ]
    res = detect_anomalies(facts, S, [2021])
    assert any(n.startswith("unit_rejected:AAA|FY2021|inventory_total") for n in res.notes)
    assert not ev(res, "AAA", "FY2021").evaluable


@pytest.mark.parametrize(
    "asof",
    [
        {"ZZZ": {"FY2021": "2022-03-15"}},  # công ty không có trong dataset
        {"HPG": {"2021": "2022-03-15"}},  # kỳ sai dạng
        {"HPG": {"FY2021": "15/03/2022"}},  # ngày sai dạng
    ],
)
def test_asof_dates_are_validated(asof):
    with pytest.raises(ConfigError):
        _settings(_set(("asof_dates",), asof))


def test_asof_dates_accepted_for_known_company():
    s = _settings(_set(("asof_dates",), {"HPG": {"FY2021": "2022-03-15"}}))
    assert s.asof_dates == {"HPG": {"FY2021": "2022-03-15"}}


def test_input_order_independence_with_peers_and_duplicates():
    facts = (
        _inv_only(0.45, "AAA")
        + with_end(_inv_only(0.10, "BBB"), "09-30")
        + _inv_only(0.20, "CCC")
        + [F("CCC", 2021, "inventory_total", 999)]
    )
    base = detect_anomalies(facts, S, [2021]).to_dict()
    for seed in range(8):
        shuffled = list(facts)
        random.Random(seed).shuffle(shuffled)
        assert detect_anomalies(shuffled, S, [2021]).to_dict() == base


def test_repo_configs_unchanged_by_test_helpers():
    assert CFG["provisional_thresholds"] is True and DATASET["companies"] == ["HPG", "HSG", "NKG"]
    assert isinstance(FinancialFact, type)
