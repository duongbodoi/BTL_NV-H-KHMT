"""Test anomaly engine (Phase 5) trên fact tổng hợp; fixture thật chỉ là ví dụ ở cuối file."""

import copy
import csv
import json
import random
from decimal import Decimal
from pathlib import Path

import pytest

from steel_xai.anomaly import (
    candidate_rows,
    detect_anomalies,
    load_anomaly_settings,
    write_candidates_csv,
)
from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import Anomaly, FinancialFact, Provenance, ValueStatus
from steel_xai.extraction import extract_document
from steel_xai.normalization import build_context, load_normalization_settings, normalize
from steel_xai.parsing import parse_file

PINNED = Path(__file__).parent / "fixtures" / "anomaly_config"
CFG = load_config("anomaly", PINNED)
DATASET = load_config("dataset")


def _settings(mutate=None, dataset_mutate=None):
    cfg, ds = copy.deepcopy(CFG), copy.deepcopy(DATASET)
    if mutate:
        mutate(cfg)
    if dataset_mutate:
        dataset_mutate(ds)
    return load_anomaly_settings(cfg, ds)


S = _settings(dataset_mutate=lambda d: d.update(companies=[]))  # công ty giả định trong test


def F(company, year, metric, value, end=None, flags=(), status=ValueStatus.OK):
    end = end or f"{year}-12-31"
    v = None if status is not ValueStatus.OK else Decimal(str(value))
    prov = Provenance("doc", 1, source_text=str(value))
    return FinancialFact(
        company, f"FY{year}", metric, v, "VND", status, "ocr", prov, tuple(flags), (), end
    )


def series(company, rows, end="{y}-12-31", flags=()):
    """rows: {năm: (tồn kho, doanh thu, giá vốn, biên gộp)}; None = bỏ qua metric."""
    out = []
    for y, (inv, rev, cogs, gm) in rows.items():
        e = end.format(y=y)
        for metric, value in (
            ("inventory_total", inv),
            ("net_revenue", rev),
            ("cogs", cogs),
            ("gross_margin", gm),
        ):
            if value is not None:
                out.append(F(company, y, metric, value, e, flags))
    return out


def anomalies(facts, settings=S, years=None):
    return detect_anomalies(facts, settings, years).anomalies


def by_id(result_anomalies):
    return {a.anomaly_id: a for a in result_anomalies}


# ---------------- config


def test_repo_config_loads_and_is_marked_provisional():
    s = load_anomaly_settings()
    assert s.provisional_thresholds is True  # ngưỡng là đề xuất, nhóm chưa chốt
    assert s.target_years == (2016, 2023) and s.company_start_year == {"NKG": 2017}
    assert s.reference_only_from_year == 2024
    assert {t.name for t in s.types} == {"inventory_increase", "gross_margin_change"}
    for t in s.types:
        assert all(r.up is not None and r.down is not None for r in t.rules)  # hai phía


def _set(path, value):
    def f(cfg):
        node = cfg
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value

    return f


@pytest.mark.parametrize(
    "mutate",
    [
        _set(("two_sided",), False),
        _set(("compare_order",), ["yoy", "qoq"]),
        _set(("provisional_thresholds",), "yes"),
        _set(("anomaly_types",), ["inventory_increase"]),
        _set(("inventory_increase", "requires"), "gm_change_pp"),
        _set(("inventory_increase", "rules"), []),
        _set(("inventory_increase", "rules", 0, "up"), -0.3),
        _set(("inventory_increase", "rules", 0, "down"), 0.3),
        _set(("inventory_increase", "rules", 0, "down"), None),  # thiếu một phía
        _set(("inventory_increase", "rules", 0, "indicator"), "khong_co"),
        _set(("inventory_increase", "rules", 1, "indicator"), "inv_growth_yoy"),  # lặp
        _set(("history", "min_changes_mad"), 3),  # nhỏ hơn min_changes_z
        _set(("history", "min_changes_z"), 2),
        _set(("peers", "min_peers"), 0),
        _set(("asof_dates",), {"HPG": {"FY2023": "30/01/2024"}}),
    ],
)
def test_config_rejects_bad_values(mutate):
    with pytest.raises(ConfigError):
        _settings(mutate)


def test_cutoff_must_follow_base_period():
    with pytest.raises(ConfigError):
        _settings(dataset_mutate=lambda d: d.update(reference_only_from_year=2023))


# ---------------- chỉ báo


BASE = {
    2020: (100, 1000, 800, "0.20"),
    2021: (150, 1100, 880, "0.20"),
}


def test_inventory_indicators_are_exact():
    a = anomalies(series("AAA", BASE), years=[2021])
    inv = by_id(a)["AAA|FY2021|inventory_increase|up"]
    ind = inv.indicators
    assert Decimal(ind["inv_growth_yoy"]) == Decimal("0.5")
    assert Decimal(ind["rev_growth_yoy"]) == Decimal("0.1")
    assert Decimal(ind["inv_minus_rev_growth"]) == Decimal("0.4")
    q = Decimal("0.000001")
    assert Decimal(ind["inv_to_revenue"]) == (Decimal(150) / Decimal(1100)).quantize(q)
    # số ngày tồn kho = tồn kho / giá vốn × số ngày của năm tài chính (2020-12-31 → 2021-12-31)
    assert Decimal(ind["dio"]) == (Decimal(150) / 880 * 365).quantize(Decimal("0.000001"))
    assert ind["dio_change_days"] is None  # năm 2019 không có → không tính được DIO năm trước


def test_days_in_period_comes_from_fiscal_ends_not_a_constant():
    rows = {
        2019: (100, 1000, 800, "0.2"),
        2020: (100, 1000, 800, "0.2"),
        2021: (200, 1000, 800, "0.2"),
    }
    a = anomalies(series("AAA", rows), years=[2021])
    ind = by_id(a)["AAA|FY2021|inventory_increase|up"].indicators
    # 2020 là năm nhuận (366 ngày), 2021 là 365 ngày
    dio21, dio20 = Decimal(200) / 800 * 365, Decimal(100) / 800 * 366
    assert Decimal(ind["dio_change_days"]) == (dio21 - dio20).quantize(Decimal("0.000001"))


def test_missing_fiscal_end_gives_no_dio_and_a_flag():
    rows = {2020: (100, 1000, 800, "0.2"), 2021: (200, 1000, 800, "0.2")}
    facts = [
        FinancialFact(
            f.company,
            f.period,
            f.metric,
            f.value,
            "VND",
            f.status,
            "ocr",
            f.provenance,
            (),
            (),
            None,
        )
        for f in series("AAA", rows)
    ]
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert a.indicators["dio"] is None and "days_in_period_missing" in a.flags


def test_gross_margin_change_in_percentage_points():
    rows = {2020: (1, 1, 1, "0.20"), 2021: (1, 1, 1, "0.1650")}
    a = by_id(anomalies(series("AAA", rows), years=[2021]))["AAA|FY2021|gross_margin_change|down"]
    assert Decimal(a.indicators["gm_change_pp"]) == Decimal("-3.5")
    assert a.severity == Decimal("3.5") / Decimal("3.0")


def test_composition_shares_when_components_exist():
    facts = series("AAA", BASE) + [
        F("AAA", 2021, "inventory_gross", 160),
        F("AAA", 2021, "raw_materials", 80),
        F("AAA", 2021, "finished_goods", 40),
    ]
    ind = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"].indicators
    assert Decimal(ind["raw_share"]) == Decimal("0.5") and Decimal(ind["fg_share"]) == Decimal(
        "0.25"
    )
    assert ind["wip_share"] is None  # thiếu thành phần → None, không phải 0


# ---------------- luật hai phía


def _inv_only(growth, company="AAA", year=2021):
    prev = 100
    cur = prev * (1 + Decimal(str(growth)))
    rev = 1000 * (1 + Decimal(str(growth)))  # doanh thu tăng cùng tồn kho: chỉ còn luật tồn kho
    return series(company, {year - 1: (prev, 1000, 800, "0.2"), year: (cur, rev, 800, "0.2")})


@pytest.mark.parametrize(
    ("growth", "expected"),
    [(0.45, ["up"]), (-0.45, ["down"]), (0.30, ["up"]), (-0.30, ["down"]), (0.29, []), (-0.29, [])],
)
def test_two_sided_threshold_including_boundary(growth, expected):
    found = [
        a for a in anomalies(_inv_only(growth), years=[2021]) if a.type == "inventory_increase"
    ]
    assert sorted(a.direction for a in found) == expected


def test_threshold_hit_records_rule_value_and_score():
    a = by_id(anomalies(_inv_only(0.45), years=[2021]))["AAA|FY2021|inventory_increase|up"]
    hit = [h for h in a.thresholds_hit if h["indicator"] == "inv_growth_yoy"][0]
    assert hit == {
        "indicator": "inv_growth_yoy",
        "direction": "up",
        "value": "0.450000",
        "score": "1.500000",
    }
    assert a.severity == Decimal("1.5") and "yoy" in a.baseline_used


def test_unevaluable_period_is_counted_but_never_flagged():
    facts = series("AAA", {2020: (100, 1, 1, "0.2"), 2021: (None, 1, 1, "0.2")})
    res = detect_anomalies(facts, S, [2021])
    inv = [e for e in res.evaluations if e.type == "inventory_increase"][0]
    assert not inv.evaluable and inv.reason == "missing:inv_growth_yoy" and not inv.flagged
    assert res.summary()["inventory_increase"]["anomaly_rate"] is None


def test_missing_prior_year_or_nonpositive_base_is_not_evaluable():
    zero_base = series("AAA", {2020: (0, 1, 1, "0.2"), 2021: (5, 1, 1, "0.2")})
    res = detect_anomalies(zero_base, S, [2021])
    assert not [e for e in res.evaluations if e.type == "inventory_increase"][0].evaluable
    gap = series("AAA", {2019: (100, 1, 1, "0.2"), 2021: (500, 1, 1, "0.2")})  # thiếu 2020
    inv = [
        e for e in detect_anomalies(gap, S, [2021]).evaluations if e.type == "inventory_increase"
    ]
    assert inv and not inv[0].evaluable


def test_direction_is_the_companys_own_movement_and_mixed_case_is_not_a_candidate():
    # tồn kho +60% (hướng up, vượt luật YoY) nhưng thấp hơn xa hai peer tăng +300% (luật peer: down)
    facts = _inv_only(0.60, "AAA") + _inv_only(3.0, "BBB") + _inv_only(3.0, "CCC")
    res = detect_anomalies(facts, S, [2021])
    found = [a for a in res.anomalies if a.company == "AAA" and a.type == "inventory_increase"]
    assert [a.direction for a in found] == ["up"]  # không sinh bản ghi "down" từ luật peer
    a = found[0]
    assert "mixed_direction" in a.flags and a.peer_relative == "below"
    assert {h["indicator"] for h in a.thresholds_hit} == {"inv_growth_yoy"}
    row = next(r for r in res.rows if (r.company, r.type) == ("AAA", "inventory_increase"))
    assert row.exceeded and row.mixed_direction and not row.candidate
    assert row.rules_hit_opposite == ("inv_growth_vs_peer",)


# ---------------- nền lịch sử


def _history(n_obs, last_growth, pattern=(0.05, -0.02, 0.04, -0.03, 0.02, -0.01, 0.03)):
    """Chuỗi tồn kho với n_obs lần tăng trưởng YoY trước kỳ cuối rồi tới last_growth."""
    inv, rows, year = Decimal(100), {}, 2021 - n_obs - 1
    rows[year] = (inv, 1000, 800, "0.2")
    for g in list(pattern[:n_obs]) + [last_growth]:
        year += 1
        inv = inv * (1 + Decimal(str(g)))
        rows[year] = (inv, 1000, 800, "0.2")
    return series("AAA", rows)


@pytest.mark.parametrize(
    ("n_obs", "baseline", "has_flag"),
    [(2, None, True), (3, None, True), (4, "history_z", False), (5, "history_z", False),
     (6, "history_mad", False), (7, "history_mad", False)],
)  # fmt: skip
def test_history_method_depends_on_available_observations(n_obs, baseline, has_flag):
    a = by_id(anomalies(_history(n_obs, 0.50), years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert ("weak_baseline" in a.flags) is has_flag
    used = {b for b in a.baseline_used if b.startswith("history")}
    assert used == (set() if baseline is None else {baseline})
    z, mz = a.indicators["inv_growth_z"], a.indicators["inv_growth_mad_z"]
    assert (z is not None, mz is not None) == (baseline == "history_z", baseline == "history_mad")


def test_history_z_matches_hand_calculation():
    facts = _history(4, 0.50)
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    # dựng lại tăng trưởng từ chính các fact đầu vào rồi tính z-score mẫu bằng tay
    inv = {int(f.period[2:]): f.value for f in facts if f.metric == "inventory_total"}
    growth = {y: inv[y] / inv[y - 1] - 1 for y in inv if y - 1 in inv}
    past = [growth[y] for y in sorted(growth) if y < 2021]
    assert len(past) == 4
    m = sum(past) / 4
    sd = (sum((g - m) ** 2 for g in past) / 3).sqrt()
    expected = ((growth[2021] - m) / sd).quantize(Decimal("0.000001"))
    assert Decimal(a.indicators["inv_growth_z"]) == expected


def test_zero_variance_history_gives_no_score_and_a_flag():
    rows = {y: (100, 1000, 800, "0.2") for y in range(2015, 2021)}
    rows[2021] = (200, 1000, 800, "0.2")
    a = by_id(anomalies(series("AAA", rows), years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert a.indicators["inv_growth_z"] is None and a.indicators["inv_growth_mad_z"] is None
    assert "zero_variance" in a.flags


def test_history_never_uses_later_years():
    base = _history(5, 0.50)
    later = series("AAA", {2022: (1, 1, 1, "0.9"), 2023: (900, 1, 1, "0.01")})
    only_2021 = detect_anomalies(base, S, [2021])
    with_future = detect_anomalies(base + later, S, [2021])
    assert only_2021.to_dict() == with_future.to_dict()


def test_facts_from_the_reference_year_onward_are_never_used():
    base = _history(5, 0.50)
    extra = series("AAA", {2024: (9999, 1, 1, "0.99")}) + series("BBB", {2024: (5, 1, 1, "0.1")})
    a = detect_anomalies(base, S)
    b = detect_anomalies(base + extra, S)
    assert a.to_dict() == b.to_dict()
    assert detect_anomalies(base + extra, S, years=[2024]).evaluations == ()  # không xét năm 2024


def test_target_years_default_to_the_case_selection_window():
    facts = series("AAA", {y: (100 * 2 ** (y - 2018), 1000, 800, "0.2") for y in range(2018, 2024)})
    periods = {e.period for e in detect_anomalies(facts, S).evaluations}
    assert periods == {f"FY{y}" for y in range(2016, 2024)}  # 2024 trở đi không bao giờ vào
    no_data = {e.period for e in detect_anomalies(facts, S).evaluations if e.reason == "no_data"}
    assert no_data == {"FY2016", "FY2017"}


# ---------------- peer


def test_peer_indicator_needs_min_peers_and_flags_when_missing():
    solo = by_id(anomalies(_inv_only(0.45), years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert solo.indicators["inv_growth_vs_peer"] is None and "peer_missing" in solo.flags
    one_peer = _inv_only(0.45) + _inv_only(0.10, "BBB")
    a = by_id(anomalies(one_peer, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert a.indicators["inv_growth_vs_peer"] is None and "peer_missing" in a.flags


def test_peer_median_of_other_companies():
    facts = _inv_only(0.45) + _inv_only(0.10, "BBB") + _inv_only(0.20, "CCC")
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert Decimal(a.indicators["inv_growth_vs_peer"]) == Decimal(
        "0.30"
    )  # 0.45 - median(0.10, 0.20)
    assert "peer" in a.baseline_used and "peer_missing" not in a.flags


def test_peer_fiscal_end_difference_is_flagged_on_the_anomaly():
    facts = (
        _inv_only(0.45)
        + [
            FinancialFact(**{**f.__dict__, "fiscal_period_end": f"{f.period[2:]}-09-30"})
            for f in _inv_only(0.10, "BBB")
        ]  # fmt: skip
        + _inv_only(0.20, "CCC")
    )
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert "peer_fiscal_end_differs" in a.flags


def test_gross_margin_vs_peer_uses_the_peers_own_change():
    def gm(company, a, b):
        return series(company, {2020: (1, 1, 1, a), 2021: (1, 1, 1, b)})

    facts = gm("AAA", "0.20", "0.12") + gm("BBB", "0.10", "0.09") + gm("CCC", "0.10", "0.11")
    ind = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|gross_margin_change|down"].indicators
    # thay đổi của AAA = -8pp; trung vị thay đổi peer = (-1 + 1) / 2 = 0
    assert Decimal(ind["gm_change_pp"]) == Decimal("-8")
    assert Decimal(ind["gm_change_vs_peer_pp"]) == Decimal("-8")
    assert Decimal(ind["gm_vs_peer_pp"]) == Decimal("2")  # mức: 12% - trung vị(9%, 11%) = 2pp


# ---------------- cờ, as-of, đầu vào


def test_provisional_flag_follows_config():
    off = _settings(_set(("provisional_thresholds",), False))
    on = by_id(anomalies(_inv_only(0.45), S, [2021]))["AAA|FY2021|inventory_increase|up"]
    assert "provisional_thresholds" in on.flags
    a = by_id(anomalies(_inv_only(0.45), off, [2021]))["AAA|FY2021|inventory_increase|up"]
    assert "provisional_thresholds" not in a.flags


def test_asof_date_comes_only_from_config():
    asof = {"AAA": {"FY2021": "2022-03-15"}}
    s = _settings(_set(("asof_dates",), asof), lambda d: d.update(companies=[]))
    a = by_id(anomalies(_inv_only(0.45), s, [2021]))["AAA|FY2021|inventory_increase|up"]
    assert a.asof_date == "2022-03-15" and "asof_date_unknown" not in a.flags
    b = by_id(anomalies(_inv_only(0.45), S, [2021]))["AAA|FY2021|inventory_increase|up"]
    assert b.asof_date is None and "asof_date_unknown" in b.flags


def test_input_quality_flags_are_propagated():
    flags = ("restated_later", "validation_failed:x", "irrelevant")
    facts = [
        FinancialFact(**{**f.__dict__, "validation_flags": flags})
        if f.period == "FY2021" and f.metric == "inventory_total"
        else f
        for f in _inv_only(0.45)
    ]  # fmt: skip
    a = by_id(anomalies(facts, years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert {"input:restated_later", "input:validation_failed:x"} <= set(a.flags)
    assert "input:irrelevant" not in a.flags


# ---------------- tổng quát


def test_deterministic_and_independent_of_input_order():
    facts = _history(6, 0.5) + _inv_only(0.1, "BBB") + _inv_only(0.2, "CCC")
    base = detect_anomalies(facts, S).to_dict()
    for seed in range(5):
        shuffled = list(facts)
        random.Random(seed).shuffle(shuffled)
        assert detect_anomalies(shuffled, S).to_dict() == base
    json.dumps(base)


def test_summary_reports_flagged_over_evaluated_periods():
    rows = {
        2020: (100, 1000, 800, "0.2"),
        2021: (150, 1500, 800, "0.2"),
        2022: (152, 1500, 800, "0.2"),
        2023: (None, 1500, 800, "0.2"),
    }
    res = detect_anomalies(series("AAA", rows), S, [2021, 2022, 2023])
    inv = res.summary()["inventory_increase"]
    assert inv["periods_total"] == 3 and inv["periods_evaluated"] == 2
    assert inv["periods_flagged"] == 1 and Decimal(inv["anomaly_rate"]) == Decimal("0.5")
    assert inv["not_evaluable"] == 1 and inv["periods_candidate"] == 1


def test_non_ok_facts_are_ignored_not_zero():
    facts = series("AAA", {2020: (100, 1000, 800, "0.2"), 2021: (150, 1000, 800, "0.2")})
    broken = F("AAA", 2020, "inventory_total", 0, status=ValueStatus.UNRESOLVED)
    facts = [broken if (f.metric, f.period) == ("inventory_total", "FY2020") else f for f in facts]
    res = detect_anomalies(facts, S, [2021])
    assert not [e for e in res.evaluations if e.type == "inventory_increase"][0].evaluable


def test_anomaly_roundtrip_and_validation():
    a = by_id(anomalies(_inv_only(0.45), years=[2021]))["AAA|FY2021|inventory_increase|up"]
    assert Anomaly.from_dict(json.loads(json.dumps(a.to_dict()))) == a
    with pytest.raises(ValueError):
        Anomaly("A", "FY1", "t", "sideways", {}, (), (), Decimal(1))


def test_candidate_table_lists_every_evaluable_pair_sorted_by_deviation(tmp_path):
    facts = _inv_only(0.45) + _inv_only(2.0, "BBB") + _inv_only(0.05, "CCC")
    res = detect_anomalies(facts, S, [2021])
    rows = candidate_rows(res)
    inv = [r for r in rows if r["type"] == "inventory_increase"]
    assert {r["company"] for r in inv} == {"AAA", "BBB", "CCC"}  # cả ca chưa vượt ngưỡng
    dev = [Decimal(r["deviation"]) for r in rows]
    assert dev == sorted(dev, reverse=True) and [r["rank"] for r in rows] == [
        str(i) for i in range(1, len(rows) + 1)
    ]
    ccc = next(r for r in inv if r["company"] == "CCC")
    assert ccc["exceeded_threshold"] == "false" and ccc["candidate"] == "false"
    assert Decimal(ccc["inv_growth_yoy__score"]) < 1  # có điểm số dù chưa vượt
    top = next(r for r in rows if r["company"] == "BBB" and r["type"] == "inventory_increase")
    assert top["exceeded_threshold"] == "true" and "inv_growth_yoy" in top["rules_hit"]
    path = tmp_path / "cand.csv"
    write_candidates_csv(res, path)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        read = list(csv.DictReader(fh))
    assert [r["rank"] for r in read] == [r["rank"] for r in rows]
    assert {"inv_growth_yoy", "inv_growth_yoy__score", "gm_change_pp__score"} <= set(read[0])


def test_empty_input_gives_empty_result():
    res = detect_anomalies([], S)
    assert res.anomalies == () and res.evaluations == () and res.summary() == {}


# ---------------- dữ liệu thật (ví dụ)

FIXTURES = Path(__file__).parent / "fixtures"


def test_hpg_2022_gross_margin_fall_matches_hand_calculation():
    ns = load_normalization_settings()
    doc = parse_file(FIXTURES / "HPG_financial_statements_2022_consolidated_extracted.txt", "hpg22")
    facts = normalize(
        extract_document(doc, company="HPG"), settings=ns, contexts=[build_context(doc, ns)]
    ).facts
    a = by_id(anomalies(facts, years=[2022]))["HPG|FY2022|gross_margin_change|down"]
    gm22 = Decimal("16763426239552") / Decimal("141409274460632")
    gm21 = Decimal("41108409532992") / Decimal("149679789979345")
    assert Decimal(a.indicators["gm_change_pp"]) == ((gm22 - gm21) * 100).quantize(
        Decimal("0.000001")
    )
    assert "peer_missing" in a.flags and "weak_baseline" in a.flags  # chỉ một công ty, hai năm
