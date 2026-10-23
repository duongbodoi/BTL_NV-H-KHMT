"""Test hồi quy từ review độc lập vòng 2 (Phase 3 sửa + Phase 4)."""

import copy
import datetime as dt
import unicodedata
from decimal import Decimal

import pytest
from test_normalization import S, _doc, _fact, _income, _ocr, _report, _row, _table
from test_validation import (
    BASE,
    FAIL,
    NOT_EVALUABLE,
    PASS,
    V,
    _checks,
    mk,
)

from steel_xai.config import ConfigError
from steel_xai.contracts import ValueStatus
from steel_xai.extraction import extract_document
from steel_xai.normalization import build_context, normalize
from steel_xai.validation import (
    ReferenceRow,
    load_validation_settings,
    reconciliation_report,
    render_markdown,
    validate_facts,
)

# ---------------------------------------------------------------- normalization


def _doc_with(text: str, body: str):
    return _doc(body, text=text)


def test_report_end_tie_between_closing_date_and_signing_date_is_not_guessed():
    text = "Cho năm kết thúc ngày 31 tháng 12 năm 2022. Hà Nội, ngày 31 tháng 3 năm 2023."
    assert build_context(_doc_with(text, "x"), S).report_end is None
    clear = text + " Tại ngày 31 tháng 12 năm 2022."
    assert build_context(_doc_with(clear, "x"), S).report_end == dt.date(2022, 12, 31)


def test_relative_headers_through_build_context_use_the_dominant_date():
    text = (
        "Tại ngày 30 tháng 9 năm 2017. Cho năm kết thúc ngày 30 tháng 9 năm 2017.\n"
        "Báo cáo được trình bày bằng đồng Việt Nam. Ngày 31 tháng 12 năm 2017."
    )
    body = _table(
        _row("Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay", "Năm trước"),
        _row("10", "3. Doanh thu thuần từ bán hàng và cung cấp dịch vụ", "", "1.000", "900"),
    )
    doc = _doc_with(text, body)
    r = normalize(
        extract_document(doc, company="AAA"), settings=S, contexts=[build_context(doc, S)]
    )
    now, before = _fact(r, "FY2017", "net_revenue"), _fact(r, "FY2016", "net_revenue")
    assert (now.fiscal_period_end, before.fiscal_period_end) == ("2017-09-30", "2016-09-30")
    assert (now.value, before.value) == (Decimal(1000), Decimal(900))
    assert "unit_from_document" in now.validation_flags


def test_unrecognized_unit_is_not_overridden_by_document_statement():
    text = "Báo cáo được trình bày bằng đồng Việt Nam. Ngày 31 tháng 12 năm 2023."
    body = _table(
        "<tr><td>Đơn vị: 1.000.000 đồng</td></tr>",
        _row("Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay"),
        _row("10", "Doanh thu thuần từ bán hàng và cung cấp dịch vụ", "", "1.000"),
    )
    doc = _doc_with(text, body)
    r = normalize(
        extract_document(doc, company="AAA"), settings=S, contexts=[build_context(doc, S)]
    )
    assert r.facts == () or all(
        f.value is None and "unit_from_document" not in f.validation_flags
        for f in r.facts
        if f.metric == "net_revenue"
    )


def test_restated_flag_when_one_side_is_dash():
    t22, c22 = _report(2022, "-", "100", "r2022")
    t23, c23 = _report(2023, "1.000", "500", "r2023")
    r = normalize(t22 + t23, settings=S, contexts=[c22, c23])
    fy22 = _fact(r, "FY2022", "net_revenue")
    assert fy22.status == ValueStatus.NOT_APPLICABLE and "restated_later" in fy22.validation_flags


def test_derived_fact_inherits_comparative_only_flag():
    body = _table(
        _row("Mã số", "CHỈ TIÊU", "Thuyết minh", "Năm nay VND", "Năm trước VND"),
        _row("10", "Doanh thu thuần từ bán hàng và cung cấp dịch vụ", "", "10", "9"),
        _row("20", "Lợi nhuận gộp", "", "3", "2"),
    )
    ctx = build_context(_doc_with("Ngày 31 tháng 12 năm 2024.", body), S)
    r = normalize(_ocr(body, doc_id="gd"), settings=S, contexts=[ctx])
    assert "comparative_only" in _fact(r, "FY2023", "gross_margin").validation_flags
    assert "comparative_only" not in _fact(r, "FY2024", "gross_margin").validation_flags


def test_document_text_is_nfc_normalized():
    raw = "Cho năm kết thúc ngày 31 tháng 12 năm 2023. Tại ngày 31 tháng 12 năm 2023."
    text = unicodedata.normalize("NFD", raw)
    assert build_context(_doc_with(text, "x"), S).report_end == dt.date(2023, 12, 31)


def test_income_helper_still_works_without_context():
    r = normalize(_ocr(_income("2023 VND", "1.000", "700", "300")), settings=S)
    assert _fact(r, "FY2023", "gross_margin").value == Decimal("0.3")


# ---------------------------------------------------------------- validation


def test_relation_with_inputs_on_different_fiscal_ends_is_not_evaluable():
    facts = [mk("net_revenue", 1000), mk("cogs", 700), mk("gross_profit", 300, end="2023-09-30")]
    c = _checks(facts, "gross_profit_identity")[0]
    assert c.status == NOT_EVALUABLE and "fiscal_period_end" in c.missing


def test_leap_day_fiscal_end_is_not_a_change():
    facts = [
        mk("net_revenue", 1, period="FY2019", end="2019-02-28"),
        mk("net_revenue", 1, period="FY2020", end="2020-02-29"),
        mk("net_revenue", 1, period="FY2021", end="2021-02-28"),
    ]
    st = {c.status for c in validate_facts(facts, V).checks if c.rule.startswith("fiscal")}
    assert st == {PASS}


def test_component_bound_fails_even_with_a_missing_component():
    facts = [mk("inventory_gross", 100), mk("raw_materials", 80), mk("finished_goods", 50)]
    c = _checks(facts, "inventory_components_bound")[0]
    assert c.status == FAIL and c.flags == ("partial_sum",) and "work_in_progress" in c.missing
    ok = [mk("inventory_gross", 100), mk("raw_materials", 40), mk("finished_goods", 10)]
    assert _checks(ok, "inventory_components_bound")[0].status == NOT_EVALUABLE


def test_config_typo_in_metric_name_is_caught_by_default():
    cfg = copy.deepcopy(BASE)
    cfg["non_negative"] = ["net_revnue"]
    with pytest.raises(ConfigError, match="không có trong normalization"):
        load_validation_settings(cfg)
    cfg = copy.deepcopy(BASE)
    cfg["relations"][0]["terms"].append({"metric": "cogs", "coef": 1})  # triệt tiêu số hạng
    with pytest.raises(ConfigError, match="lặp"):
        load_validation_settings(cfg)


def _rep(facts, raw, metric="cogs", expected_n=None, rows=1):
    refs = [ReferenceRow("AAA", "FY2023", metric, raw, "")] * rows
    return reconciliation_report(facts, refs, V, expected_n)


@pytest.mark.parametrize("reference", ["(1.000)", "1.000"])
def test_reference_for_magnitude_metric_ignores_sign(reference):
    assert _rep([mk("cogs", 1000)], reference)["counts"]["MATCH"] == 1


def test_signed_metric_reference_keeps_sign():
    ok = _rep([mk("inventory_provision", -100)], "(100)", "inventory_provision")
    assert ok["counts"]["MATCH"] == 1
    bad = _rep([mk("inventory_provision", -100)], "100", "inventory_provision")
    assert bad["counts"]["MISMATCH"] == 1


@pytest.mark.parametrize("raw", ["1,000", "12,5%"])
def test_ambiguous_or_percent_reference_is_rejected(raw):
    rep = _rep([mk("cogs", 1)], raw)
    assert rep["counts"]["REFERENCE_UNPARSEABLE"] == 1 and rep["n_compared"] == 0


def test_duplicate_reference_rows_are_counted_once():
    rep = _rep([mk("cogs", 5)], "5", expected_n=3, rows=3)
    assert rep["counts"]["MATCH"] == 1 and rep["counts"]["REFERENCE_DUPLICATE"] == 2
    assert rep["n_compared"] == 1 and rep["sample_size_ok"] is False


def test_reconciliation_markdown_without_comparisons_says_none_yet():
    assert "chưa có" in render_markdown(reconciliation_report([], [], V, expected_n=20))


def test_unresolved_fact_with_reference_is_reported_not_hidden():
    rep = _rep([mk("cogs", None, ValueStatus.UNRESOLVED)], "5")
    assert rep["counts"]["FACT_NOT_OK"] == 1 and rep["mismatch_rate"] is None
