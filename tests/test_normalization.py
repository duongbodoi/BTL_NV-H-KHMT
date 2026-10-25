"""Test chuẩn hóa metric trên dữ liệu tổng hợp; fixture thật chỉ là ví dụ ở cuối file."""

import copy
import datetime as dt
import json
import random
from decimal import Decimal
from pathlib import Path

import pytest

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import FinancialFact, Provenance, ValueStatus
from steel_xai.extraction import (
    extract_document,
    iter_values,
    load_structured_settings,
    parse_structured_csv,
)
from steel_xai.normalization import (
    DocContext,
    build_context,
    load_normalization_settings,
    norm_label,
    normalize,
)
from steel_xai.normalization.mapping import map_tables
from steel_xai.parsing import parse_file, parse_text

BASE = load_config("normalization")

STRUCT_ALIASES = {
    "inventory_total": ["inventory"],
    "net_revenue": ["revenue"],
    "cogs": ["cogs"],
    "gross_profit": ["gross_profit"],
    "capex": ["capex"],
}


def _settings(*, structured_aliases=None, tolerance=0, mutate=None):
    cfg = copy.deepcopy(BASE)
    for metric, aliases in (structured_aliases or {}).items():
        cfg["metrics"][metric]["structured_aliases"] = aliases
    cfg["resolution"]["relative_tolerance"] = tolerance
    if mutate:
        mutate(cfg)
    return load_normalization_settings(cfg)


S = _settings(structured_aliases=STRUCT_ALIASES)


def _table(*rows: str) -> str:
    return "<table>" + "".join(rows) + "</table>"


def _row(*cells: str) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _doc(*bodies: str, doc_id: str = "gd", text: str = ""):
    head = f"===== PAGE 1 =====\n{text}\n" if text else ""
    start = 2 if text else 1
    body = "".join(f"===== PAGE {i} =====\n{b}\n" for i, b in enumerate(bodies, start=start))
    return parse_text(head + body, doc_id)


def _ocr(*bodies: str, company: str | None = "AAA", doc_id: str = "gd"):
    return extract_document(_doc(*bodies, doc_id=doc_id), company=company)


def _bs(header: str, inv: str, code: str = "140") -> str:
    return _table(
        _row("", "Mã số", "Thuyết minh", header),
        _row("IV. Hàng tồn kho", code, "", inv),
    )


def _income(header: str, rev: str, cogs: str, gp: str) -> str:
    return _table(
        _row("", "Mã số", "Thuyết minh", header),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ (10 = 01 - 02)", "10", "", rev),
        _row("Giá vốn hàng bán và dịch vụ cung cấp", "11", "", cogs),
        _row("Lợi nhuận gộp (20 = 10 - 11)", "20", "", gp),
    )


def _csv_settings(unit: str | None = "VND"):
    cfg = load_config("extraction")
    cfg["structured"].update(unit=unit)
    cfg["structured"]["columns"]["quarter"] = None
    return load_structured_settings(cfg)


def _csv(text: str, settings=None):
    return parse_structured_csv(text, settings or _csv_settings(), source_name="s.csv")


def _fact(result, period, metric, company="AAA"):
    f = result.fact(company, period, metric)
    assert f is not None, [x.key for x in result.facts]
    return f


# ---------------- config / nhãn


def test_repo_config_loads():
    s = load_normalization_settings()
    assert {"inventory_total", "net_revenue", "cogs", "gross_profit", "capex"} <= set(s.metrics)
    assert s.unit_scales["million_VND"] == Decimal(10**6)
    assert "gross_margin" in s.derived


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IV. Hàng tồn kho", "hàng tồn kho"),
        ("1. Nguyên vật liệu", "nguyên vật liệu"),
        ("Lợi nhuận gộp (20 = 10 - 11)", "lợi nhuận gộp"),
        ("Chi phí sản xuất kinh doanh dở dang (i)", "chi phí sản xuất kinh doanh dở dang"),
        ("  Thành   phẩm ", "thành phẩm"),
        ("Hàng tồn kho (*) (a)", "hàng tồn kho"),
    ],
)
def test_norm_label(raw, expected):
    assert norm_label(raw) == expected


def test_alias_shared_only_when_codes_disjoint():
    s = load_normalization_settings()  # 140 và 141 dùng chung nhãn "hàng tồn kho"
    assert s.metrics["inventory_total"].aliases == s.metrics["inventory_gross"].aliases

    def overlap(cfg):
        cfg["metrics"]["cogs"]["aliases"].append("hàng tồn kho")
        cfg["metrics"]["cogs"]["codes"] = ["140"]

    with pytest.raises(ConfigError, match="trùng"):
        _settings(mutate=overlap)


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
        _set(("metrics", "raw_materials", "column_includes"), [""]),
        _set(("metrics", "raw_materials", "column_excludes"), [""]),
        _set(("blocking_flags",), ["period_missng"]),
        _set(("unit_scales", "VND"), 2),
        _set(("unit_scales", "million_VND"), 0),
        _set(("derived", "gross_margin", "numerator"), "khong_co"),
        _set(("derived", "gross_margin", "denominator"), "inventory_total"),
        _set(("metrics", "capex", "expected_sign"), None),  # flip_sign cần expected_sign
        _set(("metrics", "cogs", "expected_sign"), "positive"),  # magnitude không đi cùng
        _set(("periods", "prior_period_keywords"), ["năm nay"]),
        _set(("document_unit", "unit"), "USD"),
    ],
)
def test_config_rejects_bad_values(mutate):
    with pytest.raises(ConfigError):
        _settings(mutate=mutate)


@pytest.mark.parametrize("tol", ["1", "5", "-0.1"])
def test_tolerance_must_be_in_unit_interval(tol):
    with pytest.raises(ConfigError, match="relative_tolerance"):
        _settings(tolerance=tol)


# ---------------- OCR → fact (năm tài chính)


def test_balance_sheet_stock_fact_with_fiscal_end():
    r = normalize(_ocr(_bs("31/12/2023 VND", "1.234.567")), settings=S)
    f = _fact(r, "FY2023", "inventory_total")
    assert (f.value, f.unit, f.status, f.source) == (Decimal(1234567), "VND", ValueStatus.OK, "ocr")
    assert f.fiscal_period_end == "2023-12-31"
    assert f.provenance is not None and (f.provenance.doc_id, f.provenance.page) == ("gd", 1)
    assert f.provenance.source_text == "1.234.567"


def test_fiscal_end_other_than_december_is_kept():
    f = _fact(
        normalize(_ocr(_bs("30/9/2017 VND", "5.000")), settings=S), "FY2017", "inventory_total"
    )
    assert f.fiscal_period_end == "2017-09-30"


def test_opening_balance_date_is_rejected_not_dropped():
    r = normalize(_ocr(_bs("1/1/2024 VND", "1.000")), settings=S)
    assert r.facts == ()
    assert [(x.metric, x.reasons) for x in r.rejections] == [
        ("inventory_total", ("date_not_month_end",))
    ]


def test_flow_annual_and_quarter_headers():
    annual = _income("2023 VND", "1.000", "700", "300")
    quarter = _income("Quý 3/2023 VND", "250", "175", "75")
    r = normalize(_ocr(annual), settings=S)
    assert _fact(r, "FY2023", "net_revenue").value == Decimal(1000)
    rq = normalize(_ocr(quarter), settings=S)
    assert rq.facts == () and {x.reasons for x in rq.rejections} == {("not_annual",)}


@pytest.mark.parametrize(
    "header",
    [
        "Cho quý kết thúc ngày 30/9/2023 VND",
        "Giữa niên độ 30/6/2023 VND",
        "30/6/2023 VND",  # ngày trần: không biết kỳ dài bao lâu
    ],
)
def test_flow_with_unknown_duration_is_rejected(header):
    r = normalize(_ocr(_income(header, "1.000", "700", "300")), settings=S)
    assert r.facts == ()
    assert {x.reasons for x in r.rejections} == {("flow_duration_unknown",)}


@pytest.mark.parametrize(
    "header",
    [
        "Cho năm tài chính kết thúc ngày 31 tháng 12 năm 2023 VND",
        "Cho năm kết thúc ngày 31 tháng 12 năm 2023 VND",
        "Cho năm kết thúc ngày 31/12/2023 VND",
    ],
)
def test_annual_wording_with_date_is_annual(header):
    r = normalize(_ocr(_income(header, "1.000", "700", "300")), settings=S)
    f = _fact(r, "FY2023", "net_revenue")
    assert f.value == Decimal(1000) and f.fiscal_period_end is not None


def test_n_months_header_is_still_not_annual():
    body = _income("Cho 6 tháng kết thúc ngày 30 tháng 6 năm 2023 VND", "1.000", "700", "300")
    assert not normalize(_ocr(body), settings=S).facts


def test_year_only_header_takes_fiscal_end_from_report_context():
    ctx = DocContext("gd", dt.date(2017, 9, 30), None)
    r = normalize(_ocr(_income("2017 VND", "1", "1", "1")), settings=S, contexts=[ctx])
    f = _fact(r, "FY2017", "net_revenue")
    assert f.fiscal_period_end == "2017-09-30" and "fiscal_end_from_report" in f.validation_flags
    bare = _fact(normalize(_ocr(_income("2017 VND", "1", "1", "1")), settings=S), "FY2017", "cogs")
    assert bare.fiscal_period_end is None and "fiscal_end_unknown" in bare.validation_flags


def test_exact_label_match_only():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "2023 VND"),
        _row("Doanh thu thuần ra bên ngoài", "", "", "5.000"),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "", "1.000"),
    )
    r = normalize(_ocr(body), settings=S)
    assert _fact(r, "FY2023", "net_revenue").value == Decimal(1000)
    assert r.rejections == ()


def test_shared_label_is_split_by_code():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND"),
        _row("Hàng tồn kho", "141", "", "900"),
        _row("Hàng tồn kho", "140", "", "800"),
    )
    r = normalize(_ocr(body), settings=S)
    assert _fact(r, "FY2023", "inventory_total").value == Decimal(800)
    assert _fact(r, "FY2023", "inventory_gross").value == Decimal(900)
    assert r.rejections == ()
    odd = _table(_row("", "Mã số", "31/12/2023 VND"), _row("Hàng tồn kho", "999", "5"))
    r2 = normalize(_ocr(odd), settings=S)
    assert r2.facts == () and r2.rejections[0].reasons == ("code_mismatch",)
    none = _table(_row("", "31/12/2023 VND"), _row("Hàng tồn kho", "5"))
    assert normalize(_ocr(none), settings=S).rejections[0].reasons == ("code_missing",)


def test_code_mismatch_and_missing_for_single_metric():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "2023 VND"),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "99", "", "900"),
    )
    assert normalize(_ocr(body), settings=S).rejections[0].reasons == ("code_mismatch",)
    no_code = _table(
        _row("", "2023 VND"), _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "9")
    )
    assert normalize(_ocr(no_code), settings=S).rejections[0].reasons == ("code_missing",)


def test_unit_scaling_to_vnd():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 Triệu VND"),
        _row("Hàng tồn kho", "140", "", "1.500"),
    )
    f = _fact(normalize(_ocr(body), settings=S), "FY2023", "inventory_total")
    assert f.value == Decimal(1_500_000_000)


def test_unknown_unit_is_unresolved_not_vnd():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023"),
        _row("Hàng tồn kho", "140", "", "1.500"),
    )
    f = _fact(normalize(_ocr(body), settings=S), "FY2023", "inventory_total")
    assert (f.value, f.status) == (None, ValueStatus.UNRESOLVED)
    assert "unit_unknown" in f.validation_flags


def test_unit_from_document_statement_only_when_unambiguous():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023"),
        _row("Hàng tồn kho", "140", "", "1.500"),
    )
    ctx = DocContext("gd", dt.date(2023, 12, 31), "VND")
    f = _fact(normalize(_ocr(body), settings=S, contexts=[ctx]), "FY2023", "inventory_total")
    assert f.value == Decimal(1500) and "unit_from_document" in f.validation_flags


def test_unit_not_in_scales_is_unresolved():
    s = _settings(mutate=lambda cfg: cfg["unit_scales"].pop("million_VND"))
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 Triệu VND"),
        _row("Hàng tồn kho", "140", "", "1.500"),
    )
    f = _fact(normalize(_ocr(body), settings=s), "FY2023", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


def test_dash_is_not_applicable_never_zero():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND"),
        _row("Hàng tồn kho", "140", "", "-"),
    )
    f = _fact(normalize(_ocr(body), settings=S), "FY2023", "inventory_total")
    assert (f.value, f.status) == (None, ValueStatus.NOT_APPLICABLE)


def test_empty_and_broken_cells_keep_their_status():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND", "31/12/2022 VND"),
        _row("Hàng tồn kho", "140", "", "1.000 2.000", ""),
    )
    r = normalize(_ocr(body), settings=S)
    assert _fact(r, "FY2023", "inventory_total").status == ValueStatus.UNRESOLVED
    assert _fact(r, "FY2022", "inventory_total").status == ValueStatus.MISSING


def test_relative_headers_need_report_context():
    body = _income("Năm nay VND", "1.000", "700", "300")
    r = normalize(_ocr(body), settings=S)
    assert r.facts == ()
    assert {x.reasons for x in r.rejections} == {("report_period_unknown",)}


def test_relative_headers_resolve_against_report_year():
    cur, prior = "Năm nay VND", "Năm trước VND"
    body = _table(
        _row("Mã số", "CHỈ TIÊU", "Thuyết minh", cur, prior),
        _row("10", "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ", "", "1.000", "800"),
    )
    ctx = DocContext("gd", dt.date(2017, 9, 30), None)
    r = normalize(_ocr(body), settings=S, contexts=[ctx])
    now, before = _fact(r, "FY2017", "net_revenue"), _fact(r, "FY2016", "net_revenue")
    assert (now.value, now.fiscal_period_end) == (Decimal(1000), "2017-09-30")
    assert (before.value, before.fiscal_period_end) == (Decimal(800), "2016-09-30")
    assert "period_from_report_context" in now.validation_flags
    assert "comparative_only" in before.validation_flags  # chỉ có trong báo cáo năm sau


def test_company_missing_is_rejected():
    r = normalize(_ocr(_bs("31/12/2023 VND", "1.000"), company=None), settings=S)
    assert r.facts == () and r.rejections[0].reasons == ("company_missing",)


def test_percent_value_is_rejected():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "2023"),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "", "12,5%"),
    )
    r = normalize(_ocr(body), settings=S)
    assert r.facts == () and "is_percent" in r.rejections[0].reasons


def test_ragged_row_is_rejected_not_assigned_to_a_period():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND", "31/12/2022 VND"),
        _row("IV. Hàng tồn kho", "140", "", "900"),  # thiếu một ô: không biết thuộc cột nào
    )
    r = normalize(_ocr(body), settings=S)
    assert r.facts == ()
    assert r.rejections and all("ragged_rows" in x.reasons for x in r.rejections)


def _inventory_note(extra_rows: str = "") -> str:
    return _table(
        _row("", "31/12/2023", ""),
        _row("", "Giá gốc VND", "Dự phòng VND"),
        _row("Nguyên vật liệu", "10.000", "(100)"),
        _row("Thành phẩm", "20.000", "(200)"),
        _row("Chi phí sản xuất kinh doanh dở dang (i)", "5.000", "-"),
        extra_rows,
    )


def test_inventory_components_use_cost_column_only():
    r = normalize(_ocr(_inventory_note()), settings=S)
    assert _fact(r, "FY2023", "raw_materials").value == Decimal(10000)
    assert _fact(r, "FY2023", "finished_goods").value == Decimal(20000)
    assert _fact(r, "FY2023", "work_in_progress").value == Decimal(5000)
    assert r.rejections and all("column_mismatch" in x.reasons for x in r.rejections)


def test_table_context_excludes_unrelated_wip_table():
    other = _table(
        _row("", "31/12/2023", ""),
        _row("", "Giá gốc VND", "Dự phòng VND"),
        _row("Chi phí sản xuất kinh doanh dở dang", "99", "-"),
    )
    r = normalize(_ocr(_inventory_note(), other), settings=S)
    assert _fact(r, "FY2023", "work_in_progress").value == Decimal(5000)
    assert not r.conflicts
    assert any("table_context_mismatch" in x.reasons for x in r.rejections)


def test_capex_sign_flip_and_unexpected_sign():
    def cf(value):
        return _table(
            _row("", "Mã số", "Thuyết minh", "2023 VND"),
            _row(
                "Tiền chi mua sắm, xây dựng tài sản cố định và tài sản dài hạn khác",
                "21",
                "",
                value,
            ),
        )

    f = _fact(normalize(_ocr(cf("(3.000)")), settings=S), "FY2023", "capex")
    assert f.value == Decimal(3000) and "sign_flipped" in f.validation_flags
    bad = _fact(normalize(_ocr(cf("3.000")), settings=S), "FY2023", "capex")
    assert (bad.value, bad.status) == (None, ValueStatus.UNRESOLVED)
    assert "unexpected_sign" in bad.validation_flags
    zero = _fact(normalize(_ocr(cf("0")), settings=S), "FY2023", "capex")
    assert zero.value == Decimal(0) and zero.status == ValueStatus.OK


@pytest.mark.parametrize("reported", ["(700)", "700"])
def test_cogs_is_stored_as_positive_magnitude(reported):
    f = _fact(
        normalize(_ocr(_income("2023 VND", "1.000", reported, "300")), settings=S),
        "FY2023",
        "cogs",
    )
    assert f.value == Decimal(700) and f.status == ValueStatus.OK
    assert ("sign_normalized" in f.validation_flags) == (reported == "(700)")


def test_inventory_provision_keeps_reported_negative_sign():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND", "31/12/2022 VND"),
        _row("Dự phòng giảm giá hàng tồn kho (*)", "149", "", "(100)", "50"),
    )
    r = normalize(_ocr(body), settings=S)
    assert _fact(r, "FY2023", "inventory_provision").value == Decimal(-100)
    assert _fact(r, "FY2022", "inventory_provision").status == ValueStatus.UNRESOLVED


# ---------------- CSV có cấu trúc (nhánh không dùng)


def test_structured_annual_values():
    r = normalize(structured=_csv("ticker,year,inventory,revenue\nAAA,2023,200,400\n"), settings=S)
    assert _fact(r, "FY2023", "inventory_total").value == Decimal(200)
    assert _fact(r, "FY2023", "net_revenue").value == Decimal(400)
    assert _fact(r, "FY2023", "inventory_total").source == "structured"


def test_structured_quarter_rows_are_rejected():
    cfg = load_config("extraction")
    cfg["structured"].update(unit="VND")
    cfg["structured"]["columns"]["quarter"] = "quarter"
    st = _csv("ticker,year,quarter,inventory\nAAA,2023,3,100\n", load_structured_settings(cfg))
    r = normalize(structured=st, settings=S)
    assert r.facts == () and r.rejections[0].reasons == ("not_annual",)


def test_structured_unit_unknown_is_unresolved():
    st = _csv("ticker,year,inventory\nAAA,2023,100\n", _csv_settings(unit=None))
    f = _fact(normalize(structured=st, settings=S), "FY2023", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


def test_structured_missing_and_dash_keep_status():
    r = normalize(structured=_csv("ticker,year,inventory,revenue\nAAA,2023,,-\n"), settings=S)
    assert _fact(r, "FY2023", "inventory_total").status == ValueStatus.MISSING
    assert _fact(r, "FY2023", "net_revenue").status == ValueStatus.NOT_APPLICABLE


def test_structured_company_missing_rejected_and_unaliased_ignored():
    r = normalize(structured=_csv("ticker,year,inventory,other\n,2023,100,7\n"), settings=S)
    assert r.facts == () and [x.reasons for x in r.rejections] == [("company_missing",)]


def test_structured_duplicate_conflict_is_unresolved():
    text = "ticker,year,inventory\nAAA,2023,100\nAAA,2023,999\n"
    f = _fact(normalize(structured=_csv(text), settings=S), "FY2023", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


def test_structured_capex_magnitude_is_not_rejected_for_sign():
    st = _csv("ticker,year,capex\nAAA,2022,500\nAAA,2023,-700\n")
    r = normalize(structured=st, settings=S)
    a, b = _fact(r, "FY2022", "capex"), _fact(r, "FY2023", "capex")
    assert (a.value, b.value) == (Decimal(500), Decimal(700))
    assert "sign_not_checked" in a.validation_flags


# ---------------- gộp nguồn


def _both(ocr_value: str, struct_value: str, settings=S):
    ocr = _ocr(_bs("31/12/2023 VND", ocr_value))
    st = _csv(f"ticker,year,inventory\nAAA,2023,{struct_value}\n")
    return normalize(ocr, st, settings)


def test_structured_preferred_and_confirmed_by_ocr():
    f = _fact(_both("1.000", "1000"), "FY2023", "inventory_total")
    assert f.source == "structured" and f.value == Decimal(1000)
    assert "confirmed_by_ocr" in f.validation_flags


def test_source_conflict_is_unresolved_with_both_candidates():
    r = _both("1.000", "1001")
    f = _fact(r, "FY2023", "inventory_total")
    assert (f.value, f.status) == (None, ValueStatus.UNRESOLVED)
    assert "source_conflict" in f.validation_flags and len(r.conflicts) == 1
    c = r.conflicts[0]
    assert {s for s, _, _ in c.candidates} == {"structured", "ocr"}
    assert {v for _, v, _ in c.candidates} == {"1000", "1001"}


def test_relative_tolerance_from_config():
    s = _settings(structured_aliases=STRUCT_ALIASES, tolerance="0.01")
    f = _fact(_both("1.000", "1005", s), "FY2023", "inventory_total")
    assert f.status == ValueStatus.OK and f.value == Decimal(1005)
    assert _fact(_both("1.000", "1100", s), "FY2023", "inventory_total").status == (
        ValueStatus.UNRESOLVED
    )


def test_ocr_only_when_no_structured():
    f = _fact(
        normalize(_ocr(_bs("31/12/2023 VND", "1.000")), settings=S), "FY2023", "inventory_total"
    )
    assert f.source == "ocr" and "confirmed_by_ocr" not in f.validation_flags


def test_structured_unresolved_falls_back_to_ocr_with_flag():
    ocr = _ocr(_bs("31/12/2023 VND", "1.000"))
    st = _csv("ticker,year,inventory\nAAA,2023,abc\n")
    f = _fact(normalize(ocr, st, S), "FY2023", "inventory_total")
    assert f.source == "ocr" and f.value == Decimal(1000)
    assert {"structured_unresolved_ocr_fallback", "has_unresolved_candidate"} <= set(
        f.validation_flags
    )


def test_ocr_conflict_between_documents_of_same_report_year_is_unresolved():
    a = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="bc_a")
    b = _ocr(_bs("31/12/2023 VND", "1.200"), doc_id="bc_b")
    r = normalize(a + b, settings=S)
    f = _fact(r, "FY2023", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and "ocr_conflict" in f.validation_flags
    assert r.conflicts[0].reason == "ocr_conflict"


def test_ocr_agreeing_documents_are_fine():
    a = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="bc_a")
    b = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="bc_b")
    f = _fact(normalize(a + b, settings=S), "FY2023", "inventory_total")
    assert f.status == ValueStatus.OK and f.provenance is not None
    assert f.provenance.doc_id == "bc_a"  # chọn xác định theo thứ tự doc_id


def test_dash_in_one_source_vs_number_in_other_is_conflict():
    ocr = _ocr(_bs("31/12/2023 VND", "1.000"))
    st = _csv("ticker,year,inventory\nAAA,2023,-\n")
    r = normalize(ocr, st, S)
    f = _fact(r, "FY2023", "inventory_total")
    assert (f.value, f.status) == (None, ValueStatus.UNRESOLVED)
    assert "not_applicable_vs_value" in f.validation_flags and len(r.conflicts) == 1


def test_dash_in_one_document_vs_number_in_other_is_conflict():
    a = _ocr(_bs("31/12/2023 VND", "-"), doc_id="a")
    b = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="b")
    f = _fact(normalize(a + b, settings=S), "FY2023", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


def test_blank_in_structured_does_not_hide_ocr_number():
    ocr = _ocr(_bs("31/12/2023 VND", "1.000"))
    st = _csv("ticker,year,inventory\nAAA,2023,\n")
    f = _fact(normalize(ocr, st, S), "FY2023", "inventory_total")
    assert f.value == Decimal(1000) and f.source == "ocr"


def test_different_fiscal_ends_for_same_year_are_unresolved():
    a = _ocr(_bs("30/9/2023 VND", "1.000"), doc_id="a")
    b = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="b")
    f = _fact(normalize(a + b, settings=S), "FY2023", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and "fiscal_end_mismatch" in f.validation_flags


# ---------------- số điều chỉnh ở năm sau


def _report(year: int, current: str, prior: str, doc_id: str):
    ctx = DocContext(doc_id, dt.date(year, 12, 31), None)
    body = _table(
        _row("Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay VND", "Năm trước VND"),
        _row("10", "Doanh thu thuần về bán hàng và cung cấp dịch vụ", "", current, prior),
    )
    return _ocr(body, doc_id=doc_id), ctx


def test_restated_comparative_is_kept_apart_and_flagged():
    t23, c23 = _report(2023, "1.000", "900", "r2023")
    t24, c24 = _report(2024, "1.200", "950", "r2024")  # báo cáo 2024 trình bày lại năm 2023
    r = normalize(t23 + t24, settings=S, contexts=[c23, c24])
    main = _fact(r, "FY2023", "net_revenue")
    assert main.value == Decimal(1000) and main.status == ValueStatus.OK  # lấy từ báo cáo năm 2023
    assert main.provenance is not None and main.provenance.doc_id == "r2023"
    assert "restated_later" in main.validation_flags and not r.conflicts
    comp = [f for f in r.comparatives if f.key == ("AAA", "FY2023", "net_revenue")]
    assert len(comp) == 1 and comp[0].value == Decimal(950)
    assert comp[0].provenance is not None and comp[0].provenance.doc_id == "r2024"
    assert {"comparative", "report_year:2024"} <= set(comp[0].validation_flags)
    # FY2022 chỉ có trong báo cáo 2023 (số so sánh), không có báo cáo 2022
    only = _fact(r, "FY2022", "net_revenue")
    assert only.value == Decimal(900) and "comparative_only" in only.validation_flags
    assert _fact(r, "FY2024", "net_revenue").value == Decimal(1200)


def test_unchanged_comparative_is_not_flagged():
    t23, c23 = _report(2023, "1.000", "900", "r2023")
    t24, c24 = _report(2024, "1.200", "1.000", "r2024")
    r = normalize(t23 + t24, settings=S, contexts=[c23, c24])
    assert "restated_later" not in _fact(r, "FY2023", "net_revenue").validation_flags
    assert len(r.comparatives) == 1


# ---------------- gross margin


def test_gross_margin_is_exact_decimal_ratio_with_fiscal_end():
    r = normalize(
        _ocr(_income("Cho năm kết thúc ngày 30/9/2023 VND", "1.000", "700", "300")), settings=S
    )
    gm = _fact(r, "FY2023", "gross_margin")
    assert gm.value == Decimal(300) / Decimal(1000) == Decimal("0.3")
    assert (gm.unit, gm.source, gm.status) == ("ratio", "derived", ValueStatus.OK)
    assert gm.inputs == ("AAA|FY2023|gross_profit", "AAA|FY2023|net_revenue")
    assert gm.provenance is None and gm.fiscal_period_end == "2023-09-30"


def test_gross_margin_negative_profit():
    r = normalize(_ocr(_income("2023 VND", "1.000", "1.200", "(200)")), settings=S)
    assert _fact(r, "FY2023", "gross_margin").value == Decimal("-0.2")


def test_gross_margin_missing_input_has_no_number():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "2023 VND"),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "", "1.000"),
    )
    gm = _fact(normalize(_ocr(body), settings=S), "FY2023", "gross_margin")
    assert (gm.value, gm.status) == (None, ValueStatus.MISSING)
    assert "input_missing:gross_profit" in gm.validation_flags


def test_gross_margin_zero_and_dash_revenue():
    gm = _fact(
        normalize(_ocr(_income("2023 VND", "-", "0", "5")), settings=S), "FY2023", "gross_margin"
    )
    assert gm.value is None  # doanh thu '-' không phải 0 → thiếu đầu vào
    gm0 = _fact(
        normalize(_ocr(_income("2023 VND", "0", "0", "5")), settings=S), "FY2023", "gross_margin"
    )
    assert gm0.status == ValueStatus.UNRESOLVED and "zero_denominator" in gm0.validation_flags


def test_gross_margin_unresolved_input_propagates():
    r = normalize(_ocr(_income("2023 VND", "1.000 2.000", "700", "300")), settings=S)
    gm = _fact(r, "FY2023", "gross_margin")
    assert gm.value is None and gm.status == ValueStatus.UNRESOLVED


def test_gross_margin_inputs_with_different_fiscal_ends_are_unresolved():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "Cho năm kết thúc ngày 30/9/2023 VND"),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "", "1.000"),
    )
    other = _table(
        _row("", "Mã số", "Thuyết minh", "Cho năm kết thúc ngày 31/12/2023 VND"),
        _row("Lợi nhuận gộp", "20", "", "300"),
    )
    gm = _fact(normalize(_ocr(body, other), settings=S), "FY2023", "gross_margin")
    assert gm.status == ValueStatus.UNRESOLVED and "fiscal_end_mismatch" in gm.validation_flags


# ---------------- context tài liệu


def test_context_report_end_is_modal_month_end_date():
    text = (
        "Tại ngày 31 tháng 12 năm 2023. Cho năm kết thúc ngày 31 tháng 12 năm 2023.\n"
        "So với ngày 31 tháng 12 năm 2022 và ngày 20 tháng 3 năm 2024."
    )
    ctx = build_context(_doc(text=text), S)
    assert ctx.report_end == dt.date(2023, 12, 31)
    assert build_context(_doc(text="Không có ngày nào."), S).report_end is None


def test_context_unit_requires_unambiguous_statement():
    stated = "Báo cáo được trình bày bằng đồng Việt Nam."
    assert build_context(_doc(text=stated), S).unit == "VND"
    scaled = stated + " Một số bảng ghi triệu đồng."
    assert build_context(_doc(text=scaled), S).unit is None
    assert build_context(_doc(text="Không nói gì."), S).unit is None


# ---------------- bất biến chung


def test_every_aliased_value_is_a_candidate_or_a_rejection():
    tables = _ocr(
        _bs("31/12/2023 VND", "1.000"),
        _bs("1/1/2024 VND", "900"),
        _income("2023 VND", "1.000", "700", "300"),
        _inventory_note(),
    )
    cands, rejections = map_tables(tables, S)
    aliased = sum(
        any(norm_label(v.row_label) in m.aliases for m in S.metrics.values())
        for v in iter_values(tables)
    )
    assert aliased > 0
    assert aliased == len(cands) + len(rejections)
    r = normalize(tables, settings=S)
    assert {c.key for c in cands} == {f.key for f in r.facts if f.source != "derived"}


def test_ok_facts_have_values_and_non_ok_do_not():
    r = normalize(
        _ocr(_bs("31/12/2023 VND", "-"), _income("2023 VND", "1.000", "700", "300")), settings=S
    )
    for f in r.facts:
        assert (f.value is not None) == (f.status == ValueStatus.OK)


def test_deterministic_and_order_independent():
    tables = _ocr(
        _bs("31/12/2023 VND", "1.000"),
        _income("2023 VND", "1.000", "700", "300"),
        _income("2022 VND", "900", "650", "250"),
    )
    st = _csv("ticker,year,inventory,revenue\nAAA,2021,10,20\n")
    base = normalize(tables, st, S).to_dict()
    for seed in range(5):
        t, s2 = list(tables), list(st)
        random.Random(seed).shuffle(t)
        random.Random(seed).shuffle(s2)
        assert normalize(t, s2, S).to_dict() == base
    json.dumps(base)


def test_financial_fact_roundtrip_and_invariants():
    f = FinancialFact(
        "AAA",
        "FY2023",
        "inventory_total",
        Decimal("12345678901234567890"),
        "VND",
        ValueStatus.OK,
        "ocr",
        Provenance("d", 3, block_id="b", source_text="1"),
        ("x",),
        (),
        "2023-09-30",
    )
    assert FinancialFact.from_dict(json.loads(json.dumps(f.to_dict()))) == f
    with pytest.raises(ValueError):
        FinancialFact("A", "p", "m", None, "VND", ValueStatus.OK, "ocr")
    with pytest.raises(ValueError):
        FinancialFact("A", "p", "m", Decimal(1), "VND", ValueStatus.MISSING, "ocr")
    with pytest.raises(ValueError):
        FinancialFact("A", "p", "m", None, None, ValueStatus.MISSING, "llm")


def test_empty_input_gives_empty_result():
    r = normalize(settings=S)
    assert (r.facts, r.comparatives, r.rejections, r.conflicts) == ((), (), (), ())


# ---------------- fixture thật (ví dụ)

FIXTURES = Path(__file__).parent / "fixtures"


def _run_fixture(name: str, company: str):
    doc = parse_file(FIXTURES / f"{name}_consolidated_extracted.txt", name)
    ctx = build_context(doc, load_normalization_settings())
    return normalize(extract_document(doc, company=company), contexts=[ctx])


@pytest.fixture(scope="module")
def hpg24():
    return _run_fixture("HPG_financial_statements_2024", "HPG")


def test_hpg_spot_values(hpg24):
    f = _fact(hpg24, "FY2024", "raw_materials", "HPG")
    assert f.value == Decimal("20324528389776") and f.provenance.page == 40
    assert _fact(hpg24, "FY2024", "finished_goods", "HPG").value == Decimal("12155686194056")
    assert _fact(hpg24, "FY2024", "inventory_total", "HPG").value == Decimal("46091222189472")
    assert _fact(hpg24, "FY2024", "net_revenue", "HPG").value == Decimal("138855112131387")
    assert _fact(hpg24, "FY2024", "capex", "HPG").value == Decimal("35495026797327")
    assert _fact(hpg24, "FY2024", "inventory_total", "HPG").fiscal_period_end == "2024-12-31"


def test_hpg_gross_margin_matches_hand_calculation(hpg24):
    gm = _fact(hpg24, "FY2024", "gross_margin", "HPG")
    assert gm.value == Decimal("18497549127684") / Decimal("138855112131387")
    assert round(gm.value, 4) == Decimal("0.1332")


def test_hpg_no_conflicts_and_value_status_invariant(hpg24):
    assert hpg24.conflicts == ()
    for f in hpg24.facts:
        assert (f.value is not None) == (f.status == ValueStatus.OK)


def test_nkg2019_merged_numbers_cell_is_unresolved():
    r = _run_fixture("NKG_financial_statements_2019", "NKG")
    f = _fact(r, "FY2019", "net_revenue", "NKG")
    assert (f.value, f.status) == (None, ValueStatus.UNRESOLVED)
    assert "multiple_numbers" in f.validation_flags
    assert _fact(r, "FY2019", "gross_margin", "NKG").value is None  # không đoán từ số hỏng
    assert _fact(r, "FY2019", "inventory_total", "NKG").status == ValueStatus.OK


def test_hsg2017_negative_cogs_and_september_fiscal_end():
    r = _run_fixture("HSG_financial_statements_2017", "HSG")
    cogs = _fact(r, "FY2017", "cogs", "HSG")
    assert cogs.value == Decimal("21730791206018")  # báo cáo ghi (21.730.791.206.018)
    assert "sign_normalized" in cogs.validation_flags
    assert cogs.fiscal_period_end == "2017-09-30"
    assert _fact(r, "FY2017", "gross_margin", "HSG").fiscal_period_end == "2017-09-30"
    prior = _fact(r, "FY2016", "net_revenue", "HSG")
    assert prior.fiscal_period_end == "2016-09-30" and "comparative_only" in prior.validation_flags


def test_nkg2023_code_first_column_and_two_tier_header():
    r = _run_fixture("NKG_financial_statements_2023", "NKG")
    inv = _fact(r, "FY2023", "inventory_total", "NKG")
    assert inv.value == Decimal("5718699385156") and inv.fiscal_period_end == "2023-12-31"
    assert _fact(r, "FY2022", "inventory_total", "NKG").value == Decimal("7000417214505")
    assert _fact(r, "FY2023", "net_revenue", "NKG").value == Decimal("18595974253087")


def test_hpg2022_without_inventory_note_has_no_component_facts():
    r = _run_fixture("HPG_financial_statements_2022", "HPG")
    assert _fact(r, "FY2022", "inventory_total", "HPG").status == ValueStatus.OK
    assert not [f for f in r.facts if f.metric in ("raw_materials", "finished_goods")]


# ---------------------------------------------------------------------------
# So khớp nhãn chịu lỗi OCR (phát hiện 2026-10-25: capex NKG FY2021 bị trích sót)


def test_label_key_ignores_spaces_but_not_diacritics():
    from steel_xai.normalization.settings import label_key, loose_label_key

    assert label_key("Tiền chi để mua sắm") == label_key("Tiềnchi để muasắm")
    assert label_key("tai sản") != label_key("tài sản")  # dấu vẫn phải khớp
    assert loose_label_key("các taisản") == loose_label_key("các tài sản")
    assert loose_label_key("Đầu tư") == "dautu"


def test_diacritic_loose_match_requires_matching_code():
    from steel_xai.normalization.mapping import FLAG_LOOSE_LABEL

    label = "Tiền chi để mua sắm, xây dựng TSCĐ và các taisản dài hạn khác"  # OCR mất dấu "tài"
    rows = (
        "<table><tr><td>Mã số</td><td>Chỉ tiêu</td><td>2021 VND</td></tr>"
        f"<tr><td>21</td><td>{label}</td><td>(145.066)</td></tr>"
        f"<tr><td>99</td><td>{label}</td><td>(1.000)</td></tr>"
        f"<tr><td></td><td>{label}</td><td>(2.000)</td></tr></table>"
    )
    doc = parse_text(f"===== PAGE 1 =====\n{rows}\n", "gd_2021")
    res = normalize(extract_document(doc, company="GD"))
    capex = [f for f in res.facts if f.metric == "capex"]
    assert [f.value for f in capex] == [Decimal("145066")]  # chỉ dòng mã 21; mã sai/không mã: bỏ
    assert FLAG_LOOSE_LABEL in capex[0].validation_flags
