"""Test Phase 8: mọi tool định lượng (giá trị tính tay), điều kiện đạt, NOT_EVALUABLE, rò rỉ."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import (
    Anomaly,
    Check,
    CheckResult,
    CheckStatus,
    FinancialFact,
    Provenance,
    ValueStatus,
)
from steel_xai.hypotheses import load_library, load_library_settings
from steel_xai.verification import (
    TOOLS,
    CheckContext,
    ExternalStore,
    FactStore,
    UnknownToolError,
    context_from_anomaly,
    evaluate_condition,
    load_verification_settings,
    read_series_csv,
    run_check,
    run_quant_checks,
    tool_names,
    validate_check,
)
from steel_xai.verification.settings import SeriesSettings

S = load_verification_settings()
D = Decimal
ASOF = dt.date(2023, 3, 10)


def F(company, year, metric, value, *, end=None, flags=(), status=ValueStatus.OK, doc="doc"):
    v = None if status is not ValueStatus.OK else D(str(value))
    prov = Provenance(f"{doc}_{company}_{year}", 3, source_text=str(value))
    return FinancialFact(
        company,
        f"FY{year}",
        metric,
        v,
        "VND",
        status,
        "ocr",
        prov,
        tuple(flags),
        (),
        end or f"{year}-12-31",
    )


def facts_for(company, metric, values):
    """values: {năm: giá trị}"""
    return [F(company, y, metric, v) for y, v in values.items()]


def ctx(
    facts,
    *,
    year=2022,
    direction="up",
    asof=ASOF,
    external=None,
    asof_by=None,
    company="AAA",
    settings=S,
):
    return CheckContext(
        company=company,
        year=year,
        direction=direction,
        asof=asof,
        facts=FactStore(facts, settings.reference_only_from_year),
        external=external or ExternalStore({}, settings),
        settings=settings,
        asof_by=asof_by,
    )


def quant(tool, params, op, value, cid="c", **flags):
    return Check(
        id=cid,
        kind="quant",
        importance="critical",
        polarity="support",
        tool=tool,
        params=params,
        pass_when={"op": op, "value": value, **flags},
    )


def value_of(tool, params, c):
    """Giá trị `value` mà tool tính được (điều kiện luôn đạt để chỉ xem số)."""
    r = run_check(quant(tool, params, ">=", -1_000_000_000), c)
    assert r.status is CheckStatus.PASS, r.reason
    (only,) = r.values.values() if "value" not in r.values else (r.values["value"],)
    return only


def not_evaluable(tool, params, c, needle):
    r = run_check(quant(tool, params, ">=", -1_000_000_000), c)
    assert r.status is CheckStatus.NOT_EVALUABLE and needle in r.reason, (r.status, r.reason)
    return r


# ---------------- config


def test_repo_config_loads_and_is_validated():
    assert S.window_months == 12 and S.min_months <= S.window_months
    assert set(S.series) >= {"iron_ore_price", "hrc_price", "steel_price", "usd_vnd"}
    cfg = load_config("verification")
    for path, value in [
        (("external", "min_months"), 13),
        (("external", "window_months"), 0),
        (("external", "series"), {}),
        (("history", "min_changes"), 1),
        (("peers", "min_peers"), 0),
    ]:
        bad = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}
        node = bad
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        with pytest.raises(ConfigError):
            load_verification_settings(bad)


# ---------------- tool từ fact (giá trị tính tay)


INV = facts_for("AAA", "inventory_total", {2021: 100, 2022: 150})
REV = facts_for("AAA", "net_revenue", {2021: 200, 2022: 220})
COGS = facts_for("AAA", "cogs", {2021: 730, 2022: 730})


def test_growth_yoy_hand_computed_and_not_evaluable_cases():
    assert value_of("growth_yoy", {"metric": "inventory_total"}, ctx(INV)) == D("0.5")
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx([]), "thiếu inventory_total")
    only_cur = facts_for("AAA", "inventory_total", {2022: 150})
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx(only_cur), "FY2021")
    zero_prev = facts_for("AAA", "inventory_total", {2021: 0, 2022: 150})
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx(zero_prev), "<= 0")


def test_missing_is_never_treated_as_zero():
    unknown = [F("AAA", 2022, "inventory_total", None, status=ValueStatus.UNKNOWN)] + facts_for(
        "AAA", "inventory_total", {2021: 100}
    )
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx(unknown), "thiếu")


def test_growth_gap():
    c = ctx(INV + REV)
    assert value_of("growth_gap", {"a": "inventory_total", "b": "net_revenue"}, c) == D("0.4")


def test_days_inventory_change_non_leap_and_leap_year():
    inv = facts_for("AAA", "inventory_total", {2020: 100, 2021: 100, 2022: 150})
    cogs = facts_for("AAA", "cogs", {2020: 730, 2021: 730, 2022: 730})
    # 2022 vs 2021: 365 ngày mỗi năm → DIO 75 và 50 → +25
    assert value_of("days_inventory_change", {}, ctx(inv + cogs)) == D("25")
    # 2021 vs 2020: FY2020 có 366 ngày → DIO2020 = 100*366/730; DIO2021 = 150*365/730 = 75
    inv2 = facts_for("AAA", "inventory_total", {2019: 100, 2020: 100, 2021: 150})
    cogs2 = facts_for("AAA", "cogs", {2019: 730, 2020: 730, 2021: 730})
    leap = value_of("days_inventory_change", {}, ctx(inv2 + cogs2, year=2021))
    assert float(leap) == pytest.approx(75 - 100 * 366 / 730, abs=1e-9)
    not_evaluable("days_inventory_change", {}, ctx(inv), "thiếu cogs")
    no_end = [replace(f, fiscal_period_end=None) for f in inv + cogs]
    not_evaluable("days_inventory_change", {}, ctx(no_end), "ngày kết thúc năm tài chính")
    zero_cogs = facts_for("AAA", "cogs", {2021: 0, 2022: 730})
    not_evaluable("days_inventory_change", {}, ctx(inv + zero_cogs), "cogs FY2021 <= 0")


def test_ratio_current_and_prior_year_end():
    c = ctx(INV + COGS)
    p = {"numerator": "inventory_total", "denominator": "cogs"}
    assert float(value_of("ratio", p, c)) == pytest.approx(150 / 730)
    assert float(value_of("ratio", {**p, "period": "prior_year_end"}, c)) == pytest.approx(
        100 / 730
    )
    with pytest.raises(ValueError, match="period"):
        run_check(quant("ratio", {**p, "period": "x"}, ">", 0), c)
    zero = facts_for("AAA", "cogs", {2021: 730, 2022: 0})
    not_evaluable("ratio", p, ctx(INV + zero), "cogs FY2022 <= 0")


def test_ratio_change():
    c = ctx(INV + COGS)
    p = {"numerator": "inventory_total", "denominator": "cogs"}
    assert float(value_of("ratio_change", p, c)) == pytest.approx(50 / 730)


def test_ratio_of_changes_uses_absolute_changes():
    gp = facts_for("AAA", "gross_profit", {2021: 300, 2022: 200})  # Δ = -100
    prov = facts_for("AAA", "inventory_provision", {2021: 10, 2022: 60})  # Δ = +50
    c = ctx(gp + prov)
    p = {"numerator": "inventory_provision", "denominator": "gross_profit"}
    assert value_of("ratio_of_changes", p, c) == D("0.5")
    flat = facts_for("AAA", "gross_profit", {2021: 300, 2022: 300})
    not_evaluable("ratio_of_changes", p, ctx(flat + prov), "không đổi")


def test_share_of_change_is_signed():
    raw = facts_for("AAA", "raw_materials", {2021: 20, 2022: 50})  # Δ = +30
    c = ctx(INV + raw)  # Δ tổng = +50
    p = {"component": "raw_materials", "total": "inventory_total"}
    assert value_of("share_of_change", p, c) == D("0.6")
    fin = facts_for("AAA", "finished_goods", {2021: 80, 2022: 70})  # Δ = -10
    assert value_of("share_of_change", {**p, "component": "finished_goods"}, ctx(INV + fin)) == D(
        "-0.2"
    )
    flat = facts_for("AAA", "inventory_total", {2021: 100, 2022: 100})
    not_evaluable("share_of_change", p, ctx(flat + raw), "không đổi")


def test_share_shift():
    sh = facts_for("AAA", "export_revenue_share", {2021: "0.30", 2022: "0.38"})
    assert value_of("share_shift", {"metric": "export_revenue_share"}, ctx(sh)) == D("0.08")
    not_evaluable("share_shift", {"metric": "export_revenue_share"}, ctx([]), "thiếu")


def _peer_facts(growths):
    out = facts_for("AAA", "inventory_total", {2021: 100, 2022: 150})  # +0,5
    for name, g in growths.items():
        out += facts_for(name, "inventory_total", {2021: 100, 2022: 100 * (1 + D(str(g)))})
    return out


def _published(*names, day="2023-02-01"):
    return {(n, 2022): dt.date.fromisoformat(day) for n in names}


def test_growth_vs_peer_uses_median_of_published_peers_only():
    facts = _peer_facts({"BBB": 0.1, "CCC": 0.3})
    ab = _published("BBB", "CCC")
    assert value_of("growth_vs_peer", {"metric": "inventory_total"}, ctx(facts, asof_by=ab)) == D(
        "0.3"
    )  # 0,5 - median(0,1; 0,3)
    # peer công bố SAU as-of bị loại → chỉ còn 1 peer → NOT_EVALUABLE
    late = {**ab, ("CCC", 2022): dt.date(2023, 3, 11)}
    not_evaluable("growth_vs_peer", {"metric": "inventory_total"}, ctx(facts, asof_by=late), "peer")
    # peer chưa biết ngày công bố cũng bị loại
    unknown = {("BBB", 2022): dt.date(2023, 2, 1)}
    not_evaluable(
        "growth_vs_peer", {"metric": "inventory_total"}, ctx(facts, asof_by=unknown), "peer"
    )
    not_evaluable("growth_vs_peer", {"metric": "inventory_total"}, ctx(facts), "as-of")
    not_evaluable(
        "growth_vs_peer", {"metric": "inventory_total"}, ctx(facts, asof=None, asof_by=ab), "as-of"
    )


def test_growth_vs_peer_drops_peers_with_later_fiscal_year_end():
    facts = _peer_facts({"BBB": 0.1})
    facts += [
        F("CCC", 2021, "inventory_total", 100, end="2021-12-31"),
        F("CCC", 2022, "inventory_total", 130, end="2023-03-31"),
    ]
    ab = _published("BBB", "CCC")
    r = run_check(
        quant("growth_vs_peer", {"metric": "inventory_total"}, ">", 0), ctx(facts, asof_by=ab)
    )
    assert r.status is CheckStatus.NOT_EVALUABLE and "muộn hơn" in r.reason


def test_growth_vs_history_z_score_sample_std():
    vals, v = {2016: D(100)}, D(100)
    for y, g in zip(range(2017, 2022), ["0.1", "0.2", "0.3", "0.4", "0.5"], strict=True):
        v *= 1 + D(g)
        vals[y] = v
    vals[2022] = vals[2021] * 2  # tăng trưởng 1,0
    c = ctx(facts_for("AAA", "inventory_total", vals))
    z = value_of("growth_vs_history", {"metric": "inventory_total"}, c)
    assert float(z) == pytest.approx(0.7 / 0.025**0.5, rel=1e-9)  # mean 0,3; sd mẫu sqrt(0,025)


def test_growth_vs_history_needs_enough_nonconstant_history():
    short = facts_for("AAA", "inventory_total", {2019: 100, 2020: 110, 2021: 130, 2022: 200})
    not_evaluable("growth_vs_history", {"metric": "inventory_total"}, ctx(short), "lịch sử")
    const = {2016: D(100), 2017: D(110), 2018: D(121), 2019: D("133.1"), 2020: D("146.41")}
    const[2021] = D("161.051")
    const[2022] = D("300")
    r = run_check(
        quant("growth_vs_history", {"metric": "inventory_total"}, ">", 0),
        ctx(facts_for("AAA", "inventory_total", const)),
    )
    assert r.status is CheckStatus.NOT_EVALUABLE and "độ lệch chuẩn bằng 0" in r.reason


# ---------------- chuỗi giá bên ngoài


def series_settings(tmp_path, lag=0, names=("iron_ore_price",), **changes):
    series = {n: SeriesSettings(n, tmp_path / f"{n}.csv", lag) for n in names}
    return replace(S, series=series, **changes)


def write_series(tmp_path, name, values):
    """values: {(năm, tháng): giá trị}"""
    lines = ["date,value"] + [f"{y}-{m:02d},{v}" for (y, m), v in sorted(values.items())]
    (tmp_path / f"{name}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def monthly(by_year, special=None):
    out = {(y, m): v for y, v in by_year.items() for m in range(1, 13)}
    out.update(special or {})
    return out


def ext(tmp_path, settings, name="iron_ore_price", **kw):
    write_series(tmp_path, name, monthly({2019: 80, 2020: 90, 2021: 100, 2022: 120}, **kw))
    return ExternalStore.load(settings)


def test_lagged_external_series_lag_zero_and_with_lag(tmp_path):
    st = series_settings(tmp_path, lag=3)
    c = ctx(INV, settings=st, external=ext(tmp_path, st))
    p0 = {"series": "iron_ore_price", "lag": 0}
    assert value_of("lagged_external_series", p0, c) == D("0.2")  # 120 / 100 - 1
    # lag 3: cửa sổ 2021-10..2022-09 = (3*100 + 9*120)/12 = 115; kỳ trước = (3*90 + 9*100)/12 = 97,5
    p3 = {"series": "iron_ore_price", "lag": "from_config"}
    assert float(value_of("lagged_external_series", p3, c)) == pytest.approx(115 / 97.5 - 1)
    r = run_check(quant("lagged_external_series", p0, ">", 0), c)
    kinds = {(i.kind, i.name) for i in r.inputs}
    assert ("external", "iron_ore_price") in kinds and all(i.source for i in r.inputs)


def test_external_prices_after_asof_are_never_used(tmp_path):
    st = series_settings(tmp_path)
    store = ext(tmp_path, st, special={(2022, 12): 1320})  # tháng 12/2022 tăng đột biến
    p = {"series": "iron_ore_price", "lag": 0}
    # as-of 2023-01-15: tháng 12/2022 (công bố 2023-02-04) chưa có → giá 12/2022 không được dùng
    early = ctx(INV, settings=st, external=store, asof=dt.date(2023, 1, 15))
    assert value_of("lagged_external_series", p, early) == D("0.2")
    late = ctx(INV, settings=st, external=store, asof=dt.date(2023, 3, 10))
    assert value_of("lagged_external_series", p, late) == D("1.2")  # (11*120 + 1320)/12 = 220


def test_external_series_not_evaluable_cases(tmp_path):
    st = series_settings(tmp_path, names=("iron_ore_price", "hrc_price"))
    store = ext(tmp_path, st)  # chỉ có iron_ore, không có file hrc
    p = {"series": "hrc_price", "lag": 0}
    not_evaluable(
        "lagged_external_series", p, ctx(INV, settings=st, external=store), "chưa có dữ liệu"
    )
    p = {"series": "iron_ore_price", "lag": 0}
    not_evaluable(
        "lagged_external_series", p, ctx(INV, settings=st, external=store, asof=None), "as-of"
    )
    few = dt.date(2022, 6, 30)  # tại ngày này chỉ có vài tháng đã công bố
    not_evaluable(
        "lagged_external_series", p, ctx(INV, settings=st, external=store, asof=few), "tháng"
    )
    with pytest.raises(ValueError, match="không có trong"):
        run_check(
            quant("lagged_external_series", {"series": "gold", "lag": 0}, ">", 0),
            ctx(INV, settings=st),
        )
    with pytest.raises(ValueError, match="lag"):
        run_check(
            quant("lagged_external_series", {"series": "iron_ore_price", "lag": -1}, ">", 0),
            ctx(INV, settings=st, external=store),
        )


def test_multiple_series_all_vs_any_and_partial_availability(tmp_path):
    st = series_settings(tmp_path, names=("iron_ore_price", "hrc_price"))
    write_series(tmp_path, "iron_ore_price", monthly({2021: 100, 2022: 120}))  # +20%
    write_series(tmp_path, "hrc_price", monthly({2021: 100, 2022: 90}))  # -10%
    c = ctx(INV, settings=st, external=ExternalStore.load(st))
    p = {"series": ["iron_ore_price", "hrc_price"], "lag": 0}
    r_all = run_check(quant("lagged_external_series", p, ">", 0), c)
    r_any = run_check(quant("lagged_external_series", p, ">", 0, any=True), c)
    assert r_all.status is CheckStatus.FAIL and r_any.status is CheckStatus.PASS
    assert r_all.values == {"iron_ore_price": D("0.2"), "hrc_price": D("-0.1")}
    # một chuỗi thiếu file: tính trên chuỗi còn lại, thành phần thiếu ghi None + lý do
    (tmp_path / "hrc_price.csv").unlink()
    c2 = ctx(INV, settings=st, external=ExternalStore.load(st))
    # chế độ "tất cả": iron +20% đạt nhưng hrc thiếu → chưa đủ để kết luận (KHÔNG PASS)
    r = run_check(quant("lagged_external_series", p, ">", 0), c2)
    assert r.status is CheckStatus.NOT_EVALUABLE and r.values["hrc_price"] is None
    assert "hrc_price" in r.reason
    # có thành phần đã trượt thì FAIL chắc chắn dù thiếu phần còn lại
    assert run_check(quant("lagged_external_series", p, ">", 0.5), c2).status is CheckStatus.FAIL
    # chế độ any: một thành phần đạt là đủ; không thành phần nào đạt mà còn thiếu → chưa kết luận
    assert run_check(quant("lagged_external_series", p, ">", 0, any=True), c2).status is (
        CheckStatus.PASS
    )
    r_any = run_check(quant("lagged_external_series", p, ">", 0.5, any=True), c2)
    assert r_any.status is CheckStatus.NOT_EVALUABLE


def test_percentile_vs_history(tmp_path):
    st = series_settings(tmp_path)
    vals = {}
    for i in range(120):  # 2013-01 .. 2022-12, giá = chỉ số + 1
        vals[(2013 + i // 12, i % 12 + 1)] = i + 1
    vals[(2022, 12)] = "60.5"
    write_series(tmp_path, "iron_ore_price", vals)
    c = ctx(INV, settings=st, external=ExternalStore.load(st))
    p = {"series": "iron_ore_price", "asof_only": True}
    assert value_of("percentile_vs_history", p, c) == D(61) / D(120)  # 60 giá thấp hơn + chính nó
    with pytest.raises(ValueError, match="asof_only"):
        run_check(quant("percentile_vs_history", {**p, "asof_only": False}, ">", 0), c)
    short = ExternalStore({"iron_ore_price": {(2022, m): D(m) for m in range(1, 13)}}, st)
    not_evaluable(
        "percentile_vs_history", p, ctx(INV, settings=st, external=short), "lịch sử chỉ có"
    )


def test_percentile_ignores_prices_published_after_asof(tmp_path):
    st = series_settings(tmp_path)
    vals = {(2013 + i // 12, i % 12 + 1): i + 1 for i in range(120)}
    vals[(2022, 12)] = 100000  # tháng cuối rất cao nhưng chưa công bố tại as-of sớm
    write_series(tmp_path, "iron_ore_price", vals)
    c = ctx(INV, settings=st, external=ExternalStore.load(st), asof=dt.date(2023, 1, 15))
    p = {"series": "iron_ore_price"}
    # tháng 11/2022 (giá 119) là giá gần nhất đã công bố: nó là cao nhất trong lịch sử đã công bố
    assert value_of("percentile_vs_history", p, c) == D(1)


def test_growth_vs_series(tmp_path):
    st = series_settings(tmp_path, lag=0)
    c = ctx(INV, settings=st, external=ext(tmp_path, st))
    p = {"metric": "inventory_total", "series": ["iron_ore_price"]}
    r = run_check(quant("growth_vs_series", p, "<=", 1), c)
    assert r.values == {"iron_ore_price": D("0.3")}  # 0,5 - 0,2


def test_read_series_csv_validation(tmp_path):
    bad = {
        "h.csv": "month,price\n2022-01,1\n",
        "dup.csv": "date,value\n2022-01,1\n2022-01-15,2\n",
        "neg.csv": "date,value\n2022-01,-1\n",
        "date.csv": "date,value\n2022-13,1\n",
        "num.csv": "date,value\n2022-01,abc\n",
        "fmt.csv": "date,value\n2022/01,1\n",
    }
    for name, text in bad.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            read_series_csv(tmp_path / name)
    (tmp_path / "ok.csv").write_text("date,value\n2022-01,1.5\n2022-02-28,2\n", encoding="utf-8")
    assert read_series_csv(tmp_path / "ok.csv") == {(2022, 1): D("1.5"), (2022, 2): D(2)}


# ---------------- điều kiện đạt


def cond(values, direction="up", **pw):
    return evaluate_condition(values, pw, direction)


def test_operators_and_boundaries():
    v = {"value": D("0.2")}
    assert cond(v, op=">=", value=0.2) is CheckStatus.PASS
    assert cond(v, op=">", value=0.2) is CheckStatus.FAIL
    assert cond(v, op="<=", value=0.2) is CheckStatus.PASS
    assert cond(v, op="<", value=0.2) is CheckStatus.FAIL
    assert cond({"value": D("-0.3")}, op="<", value=-0.2) is CheckStatus.PASS


def test_sign_flags_follow_anomaly_direction():
    v = {"value": D("-0.3")}
    assert cond(v, "down", op=">=", value=0.2, signed_by_anomaly_direction=True) is CheckStatus.PASS
    assert cond(v, "up", op=">=", value=0.2, signed_by_anomaly_direction=True) is CheckStatus.FAIL
    assert (
        cond(v, "up", op=">=", value=0.2, signed_opposite_to_anomaly_direction=True)
        is CheckStatus.PASS
    )
    assert cond(v, "up", op=">=", value=0.2, abs=True) is CheckStatus.PASS
    assert cond(v, None, op=">=", value=0.2, signed_by_anomaly_direction=True) is None


def test_check_with_signed_condition_but_no_direction_is_not_evaluable():
    c = ctx(INV, direction=None)
    r = run_check(
        quant(
            "growth_yoy", {"metric": "inventory_total"}, ">=", 0.2, signed_by_anomaly_direction=True
        ),
        c,
    )
    assert r.status is CheckStatus.NOT_EVALUABLE and "hướng" in r.reason
    assert run_check(quant("growth_yoy", {"metric": "inventory_total"}, ">=", 0.2), c).status is (
        CheckStatus.PASS
    )


def test_signed_check_end_to_end_for_up_and_down_anomaly():
    shrink = facts_for("AAA", "inventory_total", {2021: 100, 2022: 60})  # -40%
    chk = quant(
        "growth_yoy", {"metric": "inventory_total"}, ">=", 0.3, signed_by_anomaly_direction=True
    )
    assert run_check(chk, ctx(shrink, direction="down")).status is CheckStatus.PASS
    assert run_check(chk, ctx(shrink, direction="up")).status is CheckStatus.FAIL


def test_not_evaluable_is_never_fail_and_missing_never_zero():
    r = run_check(quant("growth_yoy", {"metric": "inventory_total"}, "<", 5), ctx([]))
    assert r.status is CheckStatus.NOT_EVALUABLE and r.values == {} and r.reason


# ---------------- validate_check / registry


@pytest.mark.parametrize(
    ("changes", "needle"),
    [
        ({"tool": "no_such_tool"}, "không có trong registry"),
        ({"params": {}}, "thiếu tham số"),
        ({"params": {"metric": "x", "extra": 1}}, "tham số lạ"),
        ({"pass_when": {"op": "=", "value": 1}}, "pass_when.op"),
        ({"pass_when": {"op": ">", "value": "abc"}}, "pass_when.value"),
        ({"pass_when": {"op": ">", "value": 1, "weird": True}}, "cờ lạ"),
        ({"pass_when": {"op": ">", "value": 1, "abs": False}}, "chỉ có thể là true"),
        (
            {
                "pass_when": {
                    "op": ">",
                    "value": 1,
                    "abs": True,
                    "signed_by_anomaly_direction": True,
                }
            },
            "không kết hợp",
        ),
    ],
)
def test_validate_check_catches_bad_checks(changes, needle):
    base = dict(
        id="c",
        kind="quant",
        importance="critical",
        polarity="support",
        tool="growth_yoy",
        params={"metric": "inventory_total"},
        pass_when={"op": ">", "value": 0},
    )
    chk = Check(**{**base, **changes})
    assert any(needle in e for e in validate_check(chk))
    with pytest.raises((ValueError, UnknownToolError)):
        run_check(chk, ctx(INV))


def test_unknown_tool_is_a_loud_error_not_not_evaluable():
    chk = quant("growth_yoyy", {"metric": "m"}, ">", 0)
    with pytest.raises(UnknownToolError):
        run_check(chk, ctx(INV))


def test_registry_matches_library_tool_names_and_config():
    assert load_library_settings().known_tools == tool_names()
    used = set()
    for h in load_library().reviewed:
        for c in h.checks:
            if c.kind == "quant":
                used.add(c.tool)
                assert validate_check(c) == [], (h.id, c.id, validate_check(c))
    assert used and used <= tool_names()
    for name, spec in TOOLS.items():
        assert spec.name == name and spec.required.isdisjoint(spec.optional)


def test_every_tool_is_exercised_by_a_test_in_this_file():
    here = Path(__file__).parent
    src = "".join(
        p.read_text(encoding="utf-8")
        for p in (*here.glob("test_verification*.py"), here / "test_price_import.py")
    )
    for name in TOOLS:
        assert f'"{name}"' in src, name


# ---------------- chống rò rỉ thời gian của fact


def test_comparative_only_fact_of_the_evaluated_year_is_ignored():
    facts = facts_for("AAA", "inventory_total", {2021: 100})
    facts.append(F("AAA", 2022, "inventory_total", 300, flags=("comparative_only",)))
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx(facts), "FY2022")
    # nhưng số so sánh của năm TRƯỚC (đã có trong báo cáo năm kỳ đang xét) vẫn được dùng
    ok = [F("AAA", 2021, "inventory_total", 100, flags=("comparative_only",))] + [
        F("AAA", 2022, "inventory_total", 150)
    ]
    assert value_of("growth_yoy", {"metric": "inventory_total"}, ctx(ok)) == D("0.5")


def test_future_and_reference_only_years_are_ignored():
    facts = INV + facts_for("AAA", "inventory_total", {2023: 999, 2024: 999})
    assert value_of("growth_yoy", {"metric": "inventory_total"}, ctx(facts)) == D("0.5")
    only_ref = facts_for("AAA", "inventory_total", {2023: 100, 2024: 300})
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx(only_ref, year=2024), "thiếu")


def test_conflicting_duplicate_facts_are_dropped_not_chosen():
    facts = INV + [F("AAA", 2022, "inventory_total", 999, doc="other")]
    store = FactStore(facts, 2024)
    assert ("AAA", 2022, "inventory_total") in store.conflicts
    not_evaluable("growth_yoy", {"metric": "inventory_total"}, ctx(facts), "mâu thuẫn")


def test_results_do_not_depend_on_input_order():
    facts = INV + REV + COGS
    a = run_check(
        quant("growth_gap", {"a": "inventory_total", "b": "net_revenue"}, ">", 0), ctx(facts)
    )
    b = run_check(
        quant("growth_gap", {"a": "inventory_total", "b": "net_revenue"}, ">", 0),
        ctx(list(reversed(facts))),
    )
    assert a == b


# ---------------- kết quả có nguồn, hợp đồng, tích hợp


def test_result_carries_inputs_with_provenance_and_flags():
    facts = [F("AAA", 2021, "inventory_total", 100, flags=("restated_later",))] + [
        F("AAA", 2022, "inventory_total", 150)
    ]
    r = run_check(quant("growth_yoy", {"metric": "inventory_total"}, ">", 0), ctx(facts))
    assert [(i.name, i.period, i.value) for i in r.inputs] == [
        ("inventory_total", "AAA:FY2022", D(150)),
        ("inventory_total", "AAA:FY2021", D(100)),
    ]
    assert isinstance(r.inputs[0].source, Provenance) and r.inputs[0].source.page == 3
    assert "restated_later" in r.inputs[1].flags
    assert CheckResult.from_dict(r.to_dict()) == r


def test_check_result_contract():
    with pytest.raises(ValueError, match="reason"):
        CheckResult("c", CheckStatus.NOT_EVALUABLE, {})
    r = CheckResult("c", CheckStatus.NOT_EVALUABLE, {"a": None}, (), "thiếu dữ liệu")
    assert CheckResult.from_dict(r.to_dict()) == r


def test_context_from_anomaly_and_run_quant_checks_over_a_hypothesis():
    anomaly = Anomaly(
        company="AAA",
        period="FY2022",
        type="inventory_increase",
        direction="up",
        indicators={},
        thresholds_hit=[],
        baseline_used=("yoy",),
        severity=D(2),
        asof_date="2023-03-10",
    )
    store = FactStore(INV + REV + COGS, 2024)
    c = context_from_anomaly(anomaly, store, ExternalStore({}, S), S)
    assert (c.company, c.year, c.direction, c.asof) == ("AAA", 2022, "up", ASOF)
    hyp = next(h for h in load_library().reviewed if h.code == "INV-3")
    res = run_quant_checks(hyp, c)
    assert set(res) == {x.id for x in hyp.checks if x.kind == "quant"}
    assert res["inventory_outpaces_revenue"].status is CheckStatus.PASS  # 0,5 - 0,1 > 0
    assert res["revenue_strong"].status is CheckStatus.FAIL  # doanh thu +10% < 20%
    assert res["finished_goods_outpaces_revenue"].status is CheckStatus.NOT_EVALUABLE
    assert all(r.status is not CheckStatus.FAIL or r.values for r in res.values())
    with pytest.raises(ValueError):
        context_from_anomaly(replace(anomaly, period="Q1-2022"), store, ExternalStore({}, S), S)


def test_tools_agree_with_phase5_indicators_on_the_same_facts():
    """Hai chỗ tính độc lập (Phase 5 và Phase 8) phải ra cùng số cho cùng chỉ báo."""
    from test_anomaly import S as ANOMALY_SETTINGS
    from test_anomaly import series

    from steel_xai.anomaly import detect_anomalies

    facts = series(
        "AAA",
        {
            2020: (100, 1000, 800, "0.20"),
            2021: (130, 1200, 900, "0.25"),
            2022: (210, 1250, 1100, "0.12"),
        },
    )
    res = detect_anomalies(facts, ANOMALY_SETTINGS, [2022])
    inv = next(r for r in res.rows if r.type == "inventory_increase")
    gm = next(r for r in res.rows if r.type == "gross_margin_change")
    c = ctx(facts)
    gap = value_of("growth_gap", {"a": "inventory_total", "b": "net_revenue"}, c)
    assert float(gap) == pytest.approx(float(inv.indicators["inv_minus_rev_growth"]), abs=1e-6)
    dio = value_of("days_inventory_change", {}, c)
    assert float(dio) == pytest.approx(float(inv.indicators["dio_change_days"]), abs=1e-6)
    g = value_of("growth_yoy", {"metric": "inventory_total"}, c)
    assert float(g) == pytest.approx(float(inv.indicators["inv_growth_yoy"]), abs=1e-6)
    change_pp = value_of("share_shift", {"metric": "gross_margin"}, c) * 100
    assert float(change_pp) == pytest.approx(float(gm.indicators["gm_change_pp"]), abs=1e-6)
