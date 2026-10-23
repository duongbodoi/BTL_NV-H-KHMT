"""Test chuẩn hóa metric trên dữ liệu tổng hợp; HPG chỉ là ví dụ ở test tích hợp cuối file."""

import copy
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


def _doc(*bodies: str, doc_id: str = "gd"):
    text = "".join(f"===== PAGE {i} =====\n{b}\n" for i, b in enumerate(bodies, start=1))
    return parse_text(text, doc_id)


def _ocr(*bodies: str, company: str | None = "AAA", doc_id: str = "gd"):
    return extract_document(_doc(*bodies, doc_id=doc_id), company=company)


def _bs(header: str, inv: str, code: str = "140", unit_row: str = "") -> str:
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


WIDE = load_structured_settings(
    {
        **load_config("extraction"),
        "structured": {
            **load_config("extraction")["structured"],
            "unit": "VND",
            "columns": {
                **load_config("extraction")["structured"]["columns"],
                "quarter": "quarter",
            },
        },
    }
)


def _csv(text: str, settings=WIDE):
    return parse_structured_csv(text, settings, source_name="s.csv")


def _fact(result, period, metric, company="AAA"):
    f = result.fact(company, period, metric)
    assert f is not None, [x.key for x in result.facts]
    return f


# ---------------------------------------------------------------- config / nhãn


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


def test_duplicate_alias_rejected():
    def dup(cfg):
        cfg["metrics"]["cogs"]["aliases"].append("hàng tồn kho")

    with pytest.raises(ConfigError, match="trùng"):
        _settings(mutate=dup)


def test_unknown_derived_metric_rejected():
    def bad(cfg):
        cfg["derived"]["gross_margin"]["numerator"] = "khong_co"

    with pytest.raises(ConfigError):
        _settings(mutate=bad)


def test_flip_sign_requires_expected_sign():
    def bad(cfg):
        del cfg["metrics"]["capex"]["expected_sign"]

    with pytest.raises(ConfigError, match="expected_sign"):
        _settings(mutate=bad)


def test_bad_unit_scale_rejected():
    def bad(cfg):
        cfg["unit_scales"]["VND"] = 0

    with pytest.raises(ConfigError):
        _settings(mutate=bad)


# ---------------------------------------------------------------- OCR → fact


def test_balance_sheet_stock_fact():
    r = normalize(_ocr(_bs("31/12/2023 VND", "1.234.567")), settings=S)
    f = _fact(r, "2023Q4", "inventory_total")
    assert (f.value, f.unit, f.status, f.source) == (Decimal(1234567), "VND", ValueStatus.OK, "ocr")
    assert f.provenance is not None and (f.provenance.doc_id, f.provenance.page) == ("gd", 1)
    assert f.provenance.source_text == "1.234.567"


def test_non_quarter_end_date_is_rejected_not_dropped():
    r = normalize(_ocr(_bs("1/1/2024 VND", "1.000")), settings=S)
    assert r.facts == ()
    assert [(x.metric, x.reasons) for x in r.rejections] == [
        ("inventory_total", ("date_not_quarter_end",))
    ]


def test_flow_annual_and_quarter_keys():
    annual = _income("2023 VND", "1.000", "700", "300")
    quarter = _income("Quý 3/2023 VND", "250", "175", "75")
    r = normalize(_ocr(annual, quarter), settings=S)
    assert _fact(r, "FY2023", "net_revenue").value == Decimal(1000)
    assert _fact(r, "2023Q3", "net_revenue").value == Decimal(250)


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


def test_flow_with_annual_wording_and_date_is_annual():
    body = _income("Cho năm kết thúc ngày 31/12/2023 VND", "1.000", "700", "300")
    assert _fact(normalize(_ocr(body), settings=S), "FY2023", "net_revenue").value == 1000


def test_exact_label_match_only():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "2023 VND"),
        _row("Doanh thu thuần ra bên ngoài", "", "", "5.000"),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "", "1.000"),
    )
    r = normalize(_ocr(body), settings=S)
    assert _fact(r, "FY2023", "net_revenue").value == Decimal(1000)
    assert r.rejections == ()


def test_code_mismatch_and_missing_are_rejected():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND"),
        _row("Hàng tồn kho", "141", "", "900"),
        _row("Hàng tồn kho", "140", "", "800"),
    )
    r = normalize(_ocr(body), settings=S)
    assert _fact(r, "2023Q4", "inventory_total").value == Decimal(800)
    assert [x.reasons for x in r.rejections] == [("code_mismatch",)]
    no_code = _table(_row("", "31/12/2023 VND"), _row("Hàng tồn kho", "800"))
    r2 = normalize(_ocr(no_code), settings=S)
    assert r2.facts == () and r2.rejections[0].reasons == ("code_missing",)


def test_unit_scaling_to_vnd():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 Triệu VND"),
        _row("Hàng tồn kho", "140", "", "1.500"),
    )
    f = _fact(normalize(_ocr(body), settings=S), "2023Q4", "inventory_total")
    assert f.value == Decimal(1_500_000_000)


def test_unknown_unit_is_unresolved_not_vnd():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023"),
        _row("Hàng tồn kho", "140", "", "1.500"),
    )
    r = normalize(_ocr(body), settings=S)
    # Phase 2 gắn unit_missing; số không được coi là VND
    assert r.facts and all(f.value is None for f in r.facts)
    assert {f.status for f in r.facts} == {ValueStatus.UNRESOLVED}
    assert "unit_unknown" in r.facts[0].validation_flags


def test_unit_not_in_scales_is_unresolved():
    s = _settings(mutate=lambda cfg: cfg["unit_scales"].pop("million_VND"))
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 Triệu VND"),
        _row("Hàng tồn kho", "140", "", "1.500"),
    )
    f = _fact(normalize(_ocr(body), settings=s), "2023Q4", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


def test_dash_is_not_applicable_never_zero():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND"),
        _row("Hàng tồn kho", "140", "", "-"),
    )
    f = _fact(normalize(_ocr(body), settings=S), "2023Q4", "inventory_total")
    assert (f.value, f.status) == (None, ValueStatus.NOT_APPLICABLE)


def test_empty_and_broken_cells_keep_their_status():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND", "31/12/2022 VND"),
        _row("Hàng tồn kho", "140", "", "1.000 2.000", ""),
    )
    r = normalize(_ocr(body), settings=S)
    assert _fact(r, "2023Q4", "inventory_total").status == ValueStatus.UNRESOLVED
    assert _fact(r, "2022Q4", "inventory_total").status == ValueStatus.MISSING


def test_relative_period_header_is_rejected():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "Năm trước VND"),
        _row("Doanh thu thuần về bán hàng và cung cấp dịch vụ", "10", "", "1.000"),
    )
    r = normalize(_ocr(body), settings=S)
    assert r.facts == ()
    assert any("period_relative" in x.reasons for x in r.rejections)


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
    assert _fact(r, "2023Q4", "raw_materials").value == Decimal(10000)
    assert _fact(r, "2023Q4", "finished_goods").value == Decimal(20000)
    assert _fact(r, "2023Q4", "work_in_progress").value == Decimal(5000)
    assert r.rejections and all("column_mismatch" in x.reasons for x in r.rejections)


def test_table_context_excludes_unrelated_wip_table():
    other = _table(
        _row("", "31/12/2023", ""),
        _row("", "Giá gốc VND", "Dự phòng VND"),
        _row("Chi phí sản xuất kinh doanh dở dang", "99", "-"),
    )
    r = normalize(_ocr(_inventory_note(), other), settings=S)
    assert _fact(r, "2023Q4", "work_in_progress").value == Decimal(5000)
    assert not r.conflicts
    assert any("table_context_mismatch" in x.reasons for x in r.rejections)


def test_capex_sign_flip_and_unexpected_sign():
    def cf(value):
        return _table(
            _row("", "Mã số", "Thuyết minh", "2023 VND"),
            _row("Tiền chi mua sắm, xây dựng tài sản cố định và tài sản dài hạn khác", "21", "",
                 value),
        )  # fmt: skip

    f = _fact(normalize(_ocr(cf("(3.000)")), settings=S), "FY2023", "capex")
    assert f.value == Decimal(3000) and "sign_flipped" in f.validation_flags
    bad = _fact(normalize(_ocr(cf("3.000")), settings=S), "FY2023", "capex")
    assert (bad.value, bad.status) == (None, ValueStatus.UNRESOLVED)
    assert "unexpected_sign" in bad.validation_flags
    zero = _fact(normalize(_ocr(cf("0")), settings=S), "FY2023", "capex")
    assert zero.value == Decimal(0) and zero.status == ValueStatus.OK


# ---------------------------------------------------------------- CSV có cấu trúc


def test_structured_quarter_values():
    r = normalize(
        structured=_csv("ticker,year,quarter,inventory,revenue\nAAA,2023,3,100,50\n"),
        settings=S,
    )
    assert _fact(r, "2023Q3", "inventory_total").value == Decimal(100)
    assert _fact(r, "2023Q3", "net_revenue").value == Decimal(50)
    assert all(f.source in ("structured", "derived") for f in r.facts)


def test_structured_annual_stock_is_year_end_and_flow_is_fy():
    cfg = load_config("extraction")
    cfg["structured"].update(unit="VND")
    cfg["structured"]["columns"]["quarter"] = None
    annual = load_structured_settings(cfg)
    r = normalize(
        structured=_csv("ticker,year,inventory,revenue\nAAA,2023,200,400\n", annual),
        settings=S,
    )
    assert _fact(r, "2023Q4", "inventory_total").value == Decimal(200)  # số dư cuối năm
    assert _fact(r, "FY2023", "net_revenue").value == Decimal(400)  # số dòng cả năm


def test_structured_unit_unknown_is_unresolved():
    no_unit = load_structured_settings(
        {
            **load_config("extraction"),
            "structured": {
                **load_config("extraction")["structured"],
                "columns": {
                    **load_config("extraction")["structured"]["columns"],
                    "quarter": "quarter",
                },
            },
        }
    )
    r = normalize(
        structured=_csv("ticker,year,quarter,inventory\nAAA,2023,3,100\n", no_unit), settings=S
    )
    f = _fact(r, "2023Q3", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


def test_structured_missing_and_dash_keep_status():
    r = normalize(
        structured=_csv("ticker,year,quarter,inventory,revenue\nAAA,2023,3,,-\n"),
        settings=S,
    )
    assert _fact(r, "2023Q3", "inventory_total").status == ValueStatus.MISSING
    assert _fact(r, "2023Q3", "net_revenue").status == ValueStatus.NOT_APPLICABLE


def test_structured_unaliased_items_are_ignored_and_aliased_company_missing_rejected():
    r = normalize(
        structured=_csv("ticker,year,quarter,inventory,other\n,2023,3,100,7\n"), settings=S
    )
    assert r.facts == ()
    assert [x.reasons for x in r.rejections] == [("company_missing",)]


def test_structured_duplicate_conflict_is_unresolved():
    text = "ticker,year,quarter,inventory\nAAA,2023,3,100\nAAA,2023,3,999\n"
    f = _fact(normalize(structured=_csv(text), settings=S), "2023Q3", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


# ---------------------------------------------------------------- gộp nguồn


def _both(ocr_value: str, struct_value: str, settings=S):
    ocr = _ocr(_bs("31/12/2023 VND", ocr_value))
    st = _csv(f"ticker,year,quarter,inventory\nAAA,2023,4,{struct_value}\n")
    return normalize(ocr, st, settings)


def test_structured_preferred_and_confirmed_by_ocr():
    f = _fact(_both("1.000", "1000"), "2023Q4", "inventory_total")
    assert f.source == "structured" and f.value == Decimal(1000)
    assert "confirmed_by_ocr" in f.validation_flags


def test_source_conflict_is_unresolved_with_both_candidates():
    r = _both("1.000", "1001")
    f = _fact(r, "2023Q4", "inventory_total")
    assert (f.value, f.status) == (None, ValueStatus.UNRESOLVED)
    assert "source_conflict" in f.validation_flags
    assert len(r.conflicts) == 1
    c = r.conflicts[0]
    assert {s for s, _, _ in c.candidates} == {"structured", "ocr"}
    assert {v for _, v, _ in c.candidates} == {"1000", "1001"}


def test_relative_tolerance_from_config():
    s = _settings(structured_aliases=STRUCT_ALIASES, tolerance="0.01")
    f = _fact(_both("1.000", "1005", s), "2023Q4", "inventory_total")
    assert f.status == ValueStatus.OK and f.value == Decimal(1005)  # nguồn có cấu trúc thắng
    f2 = _fact(_both("1.000", "1100", s), "2023Q4", "inventory_total")
    assert f2.status == ValueStatus.UNRESOLVED


def test_ocr_only_when_no_structured():
    f = _fact(
        normalize(_ocr(_bs("31/12/2023 VND", "1.000")), settings=S), "2023Q4", "inventory_total"
    )
    assert f.source == "ocr" and "confirmed_by_ocr" not in f.validation_flags


def test_structured_unresolved_falls_back_to_ocr_with_flag():
    ocr = _ocr(_bs("31/12/2023 VND", "1.000"))
    st = _csv("ticker,year,quarter,inventory\nAAA,2023,4,abc\n")
    f = _fact(normalize(ocr, st, S), "2023Q4", "inventory_total")
    assert f.source == "ocr" and f.value == Decimal(1000)
    assert {"structured_unresolved_ocr_fallback", "has_unresolved_candidate"} <= set(
        f.validation_flags
    )


def test_ocr_conflict_between_documents_is_unresolved():
    a = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="bc_2023")
    b = _ocr(_bs("31/12/2023 VND", "1.200"), doc_id="bc_2024")  # trình bày lại số năm trước
    r = normalize(a + b, settings=S)
    f = _fact(r, "2023Q4", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and "ocr_conflict" in f.validation_flags
    assert r.conflicts[0].reason == "ocr_conflict"


def test_ocr_agreeing_documents_are_fine():
    a = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="bc_2023")
    b = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="bc_2024")
    f = _fact(normalize(a + b, settings=S), "2023Q4", "inventory_total")
    assert f.status == ValueStatus.OK and f.provenance is not None
    assert f.provenance.doc_id == "bc_2023"  # chọn xác định theo thứ tự doc_id


# ---------------------------------------------------------------- gross margin


def test_gross_margin_is_exact_decimal_ratio():
    r = normalize(_ocr(_income("2023 VND", "1.000", "700", "300")), settings=S)
    gm = _fact(r, "FY2023", "gross_margin")
    assert gm.value == Decimal(300) / Decimal(1000) == Decimal("0.3")
    assert (gm.unit, gm.source, gm.status) == ("ratio", "derived", ValueStatus.OK)
    assert gm.inputs == ("AAA|FY2023|gross_profit", "AAA|FY2023|net_revenue")
    assert gm.provenance is None


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


def test_gross_margin_zero_revenue_is_unresolved():
    gm = _fact(
        normalize(_ocr(_income("2023 VND", "-", "0", "5")), settings=S), "FY2023", "gross_margin"
    )
    assert gm.value is None  # doanh thu '-' không phải 0 → thiếu đầu vào
    r = normalize(_ocr(_income("2023 VND", "0", "0", "5")), settings=S)
    gm0 = _fact(r, "FY2023", "gross_margin")
    assert (gm0.status, "zero_denominator" in gm0.validation_flags) == (
        ValueStatus.UNRESOLVED,
        True,
    )


def test_gross_margin_unresolved_input_propagates():
    r = normalize(_ocr(_income("2023 VND", "1.000 2.000", "700", "300")), settings=S)
    gm = _fact(r, "FY2023", "gross_margin")
    assert gm.value is None and gm.status == ValueStatus.UNRESOLVED


def test_gross_margin_from_structured_quarter():
    st = _csv("ticker,year,quarter,revenue,gross_profit\nAAA,2023,2,400,100\n")
    gm = _fact(normalize(structured=st, settings=S), "2023Q2", "gross_margin")
    assert gm.value == Decimal("0.25")


# ---------------------------------------------------------------- bất biến chung


def test_every_aliased_value_is_a_fact_input_or_rejection():
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
    # Mỗi giá trị khớp nhãn thành đúng một candidate hoặc đúng một rejection.
    assert aliased == len(cands) + len(rejections)
    # Mỗi candidate nằm trong đúng một fact; không fact nào không có candidate.
    r = normalize(tables, settings=S)
    assert {c.key for c in cands} == {f.key for f in r.facts if f.source != "derived"}


def test_ok_facts_have_values_and_non_ok_do_not():
    r = normalize(
        _ocr(
            _bs("31/12/2023 VND", "-"),
            _income("2023 VND", "1.000", "700", "300"),
        ),
        settings=S,
    )
    for f in r.facts:
        assert (f.value is not None) == (f.status == ValueStatus.OK)
        assert not (f.status == ValueStatus.OK and f.value is None)


def test_deterministic_and_order_independent():
    tables = _ocr(
        _bs("31/12/2023 VND", "1.000"),
        _income("2023 VND", "1.000", "700", "300"),
        _income("Quý 1/2023 VND", "200", "150", "50"),
    )
    st = _csv("ticker,year,quarter,inventory,revenue\nAAA,2023,1,10,20\n")
    base = normalize(tables, st, S).to_dict()
    for seed in range(5):
        t = list(tables)
        s2 = list(st)
        random.Random(seed).shuffle(t)
        random.Random(seed).shuffle(s2)
        assert normalize(t, s2, S).to_dict() == base
    json.dumps(base)  # serialize được


def test_financial_fact_roundtrip_and_invariants():
    f = FinancialFact(
        "AAA",
        "2023Q4",
        "inventory_total",
        Decimal("12345678901234567890"),
        "VND",
        ValueStatus.OK,
        "ocr",
        Provenance("d", 3, block_id="b", source_text="1"),
        ("x",),
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
    assert (r.facts, r.rejections, r.conflicts) == ((), (), ())


# ---------------------------------------------------------------- tích hợp HPG (ví dụ)

HPG = (
    Path(__file__).parent / "fixtures" / "HPG_financial_statements_2024_consolidated_extracted.txt"
)


@pytest.fixture(scope="module")
def hpg_result():
    doc = parse_file(HPG, "HPG2024")
    return normalize(extract_document(doc, company="HPG"))


def test_hpg_spot_values(hpg_result):
    f = _fact(hpg_result, "2024Q4", "raw_materials", "HPG")
    assert f.value == Decimal("20324528389776") and f.provenance.page == 40
    assert _fact(hpg_result, "2024Q4", "inventory_total", "HPG").value == Decimal("46091222189472")
    assert _fact(hpg_result, "FY2024", "net_revenue", "HPG").value == Decimal("138855112131387")
    assert _fact(hpg_result, "FY2024", "capex", "HPG").value == Decimal("35495026797327")


def test_hpg_gross_margin_matches_hand_calculation(hpg_result):
    gm = _fact(hpg_result, "FY2024", "gross_margin", "HPG")
    assert gm.value == Decimal("18497549127684") / Decimal("138855112131387")
    assert round(gm.value, 4) == Decimal("0.1332")


def test_hpg_no_conflicts_and_value_status_invariant(hpg_result):
    assert hpg_result.conflicts == ()
    for f in hpg_result.facts:
        assert (f.value is not None) == (f.status == ValueStatus.OK)


# ---------------------------------------------------------------- hồi quy từ review


def test_ragged_row_is_rejected_not_assigned_to_a_period():
    body = _table(
        _row("", "Mã số", "Thuyết minh", "31/12/2023 VND", "31/12/2022 VND"),
        _row("IV. Hàng tồn kho", "140", "", "900"),  # thiếu một ô: không biết thuộc cột nào
    )
    r = normalize(_ocr(body), settings=S)
    assert r.facts == ()
    assert all("ragged_rows" in x.reasons for x in r.rejections) and r.rejections


@pytest.mark.parametrize(
    "header",
    [
        "Cho năm tài chính kết thúc ngày 31 tháng 12 năm 2023 VND",
        "Cho năm kết thúc ngày 31 tháng 12 năm 2023 VND",
    ],
)
def test_vietnamese_day_month_date_is_not_a_duration(header):
    r = normalize(_ocr(_income(header, "1.000", "700", "300")), settings=S)
    assert _fact(r, "FY2023", "net_revenue").value == Decimal(1000)


def test_n_months_header_is_still_not_annual():
    body = _income("Cho 6 tháng kết thúc ngày 30 tháng 6 năm 2023 VND", "1.000", "700", "300")
    assert not [f for f in normalize(_ocr(body), settings=S).facts if f.source == "ocr"]


def test_dash_in_one_source_vs_number_in_other_is_conflict():
    ocr = _ocr(_bs("31/12/2023 VND", "1.000"))
    st = _csv("ticker,year,quarter,inventory\nAAA,2023,4,-\n")
    r = normalize(ocr, st, S)
    f = _fact(r, "2023Q4", "inventory_total")
    assert (f.value, f.status) == (None, ValueStatus.UNRESOLVED)
    assert "not_applicable_vs_value" in f.validation_flags and len(r.conflicts) == 1


def test_dash_in_one_document_vs_number_in_other_is_conflict():
    a = _ocr(_bs("31/12/2023 VND", "-"), doc_id="a")
    b = _ocr(_bs("31/12/2023 VND", "1.000"), doc_id="b")
    f = _fact(normalize(a + b, settings=S), "2023Q4", "inventory_total")
    assert f.status == ValueStatus.UNRESOLVED and f.value is None


def test_blank_in_structured_does_not_hide_ocr_number():
    ocr = _ocr(_bs("31/12/2023 VND", "1.000"))
    st = _csv("ticker,year,quarter,inventory\nAAA,2023,4,\n")
    f = _fact(normalize(ocr, st, S), "2023Q4", "inventory_total")
    assert f.value == Decimal(1000) and f.source == "ocr"


@pytest.mark.parametrize("tol", ["1", "5", "-0.1"])
def test_tolerance_must_be_in_unit_interval(tol):
    with pytest.raises(ConfigError, match="relative_tolerance"):
        _settings(tolerance=tol)


def test_config_rejects_bad_values():
    def set_(path, value):
        def f(cfg):
            node = cfg
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = value

        return f

    for mutate in (
        set_(("metrics", "raw_materials", "column_includes"), [""]),
        set_(("metrics", "raw_materials", "column_excludes"), [""]),
        set_(("blocking_flags",), ["period_missng"]),
        set_(("unit_scales", "VND"), 2),
        set_(("periods", "quarter_end_months"), [5]),
        set_(("derived", "gross_margin", "denominator"), "inventory_total"),
    ):
        with pytest.raises(ConfigError):
            _settings(mutate=mutate)


def test_structured_capex_magnitude_is_not_rejected_for_sign():
    st = _csv("ticker,year,quarter,capex\nAAA,2023,2,500\nAAA,2023,3,-700\n")
    r = normalize(structured=st, settings=S)
    a, b = _fact(r, "2023Q2", "capex"), _fact(r, "2023Q3", "capex")
    assert (a.value, b.value) == (Decimal(500), Decimal(700))
    assert "sign_not_checked" in a.validation_flags


def test_cogs_negative_is_unresolved_sign():
    f = _fact(
        normalize(_ocr(_income("2023 VND", "1.000", "(700)", "300")), settings=S),
        "FY2023",
        "cogs",
    )
    assert f.status == ValueStatus.UNRESOLVED and "unexpected_sign" in f.validation_flags
