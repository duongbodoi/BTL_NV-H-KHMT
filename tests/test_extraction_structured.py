import copy
import hashlib
import unicodedata
from decimal import Decimal

import pytest

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import ValueStatus
from steel_xai.extraction import (
    RawStructuredValue,
    StructuredSourceError,
    load_structured_csv,
    load_structured_settings,
    parse_structured_csv,
)
from steel_xai.extraction.structured import (
    FLAG_COMPANY_MISSING,
    FLAG_DUPLICATE,
    FLAG_DUPLICATE_CONFLICT,
    FLAG_PERIOD_UNRESOLVED,
    FLAG_RAGGED_ROW,
    FLAG_UNIT_MISSING,
)

BASE = load_config("extraction")


def _settings(**structured):
    cfg = copy.deepcopy(BASE)
    cols = structured.pop("columns", {})
    cfg["structured"].update(structured)
    cfg["structured"]["columns"].update(cols)
    return load_structured_settings(cfg)


WIDE = _settings(unit="VND", columns={"quarter": "quarter"})
LONG = _settings(layout="long", columns={"quarter": "quarter", "unit": "unit"})


def _get(values, item, company="AAA", year=2022, quarter=4):
    found = [v for v in values if v.key == (company, year, quarter, item)]
    assert len(found) == 1, found
    return found[0]


def test_repo_config_loads():
    s = load_structured_settings()
    assert s.layout in ("wide", "long")
    assert s.numbers.dash_status == ValueStatus.NOT_APPLICABLE


def test_wide_layout_statuses():
    text = (
        "ticker,year,quarter,inventory,revenue,cogs,gross_profit,capex\n"
        "AAA,2022,4,1234.5,,-,abc,nan\n"
    )
    vals = parse_structured_csv(text, WIDE, source_name="x.csv")
    assert [v.item for v in vals] == ["inventory", "revenue", "cogs", "gross_profit", "capex"]
    inv = _get(vals, "inventory")
    assert (inv.value, inv.status, inv.unit) == (Decimal("1234.5"), ValueStatus.OK, "VND")
    assert _get(vals, "revenue").status == ValueStatus.MISSING
    dash = _get(vals, "cogs")
    assert dash.value is None and dash.status == ValueStatus.NOT_APPLICABLE  # không thành 0
    bad = _get(vals, "gross_profit")
    assert bad.value is None and bad.status == ValueStatus.UNRESOLVED and "no_digits" in bad.flags
    assert _get(vals, "capex").status == ValueStatus.MISSING


def test_provenance_line_and_hash():
    text = "ticker,year,quarter,x\nAAA,2021,1,1\nAAA,2022,4,2\n"
    vals = parse_structured_csv(text, WIDE, source_name="bctc.csv")
    v = _get(vals, "x")
    assert (v.ref.file, v.ref.line, v.ref.column) == ("bctc.csv", 3, "x")
    assert v.ref.content_sha256 == hashlib.sha256(text.encode()).hexdigest()


def test_long_layout_and_unit_column():
    text = (
        "ticker,year,quarter,item,value,unit\n"
        "AAA,2022,4,inventory,100,VND\n"
        "AAA,2022,4,revenue,50,\n"
    )
    vals = parse_structured_csv(text, LONG)
    assert _get(vals, "inventory").unit == "VND"
    rev = _get(vals, "revenue")
    assert rev.unit is None and FLAG_UNIT_MISSING in rev.flags
    assert rev.status == ValueStatus.OK


def test_vn_number_format():
    s = _settings(number_format="vn", columns={"quarter": None})
    vals = parse_structured_csv('ticker,year,x\nAAA,2022,"(1.234,5)"\n', s)
    assert vals[0].value == Decimal("-1234.5") and vals[0].quarter is None


@pytest.mark.parametrize("quarter", ["0", "5", "Q4", "", "4.5"])
def test_bad_quarter_unresolved(quarter):
    vals = parse_structured_csv(f"ticker,year,quarter,x\nAAA,2022,{quarter},1\n", WIDE)
    v = vals[0]
    assert v.quarter is None and v.status == ValueStatus.UNRESOLVED
    assert FLAG_PERIOD_UNRESOLVED in v.flags
    assert v.value is None and v.raw == "1"  # chỉ OK mới có value; số gốc vẫn ở raw


@pytest.mark.parametrize(
    ("year", "ok"), [("2022", True), ("2022.0", True), ("22", False), ("x", False)]
)
def test_year_parsing(year, ok):
    v = parse_structured_csv(f"ticker,year,quarter,x\nAAA,{year},4,1\n", WIDE)[0]
    assert (v.year == 2022) is ok
    assert (v.status == ValueStatus.OK) is ok


def test_missing_cell_stays_missing_even_if_period_bad():
    v = parse_structured_csv("ticker,year,quarter,x\nAAA,x,4,\n", WIDE)[0]
    assert v.status == ValueStatus.MISSING and FLAG_PERIOD_UNRESOLVED in v.flags


def test_company_missing_and_ragged_row():
    vals = parse_structured_csv("ticker,year,quarter,x,y\n,2022,4,1,2\nAAA,2022,4,3\n", WIDE)
    assert FLAG_COMPANY_MISSING in vals[0].flags and vals[0].status == ValueStatus.UNRESOLVED
    ragged = [v for v in vals if FLAG_RAGGED_ROW in v.flags]
    assert len(ragged) == 2 and all(v.status != ValueStatus.OK for v in ragged)


def test_blank_lines_skipped():
    vals = parse_structured_csv("ticker,year,quarter,x\n\n,,,\nAAA,2022,4,1\n", WIDE)
    assert len(vals) == 1


def test_duplicate_same_value_kept_conflict_unresolved():
    text = (
        "ticker,year,quarter,item,value,unit\n"
        "AAA,2022,4,inventory,100,VND\n"
        "AAA,2022,4,inventory,100,VND\n"
        "AAA,2022,4,revenue,50,VND\n"
        "AAA,2022,4,revenue,60,VND\n"
    )
    vals = parse_structured_csv(text, LONG)
    inv = [v for v in vals if v.item == "inventory"]
    assert all(v.status == ValueStatus.OK and FLAG_DUPLICATE in v.flags for v in inv)
    rev = [v for v in vals if v.item == "revenue"]
    assert len(rev) == 2
    assert all(
        v.status == ValueStatus.UNRESOLVED and FLAG_DUPLICATE_CONFLICT in v.flags for v in rev
    )
    assert {v.value for v in rev} == {None}  # không tự chọn bản nào
    assert {v.raw for v in rev} == {"50", "60"}


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("", "rỗng"),
        ("ticker,quarter,x\nAAA,4,1\n", "thiếu cột"),
        ("ticker,year,quarter,x,x\nAAA,2022,4,1,2\n", "trùng"),
        ("ticker,year,quarter\nAAA,2022,4\n", "không có cột chỉ tiêu"),
    ],
)
def test_source_errors(text, match):
    with pytest.raises(StructuredSourceError, match=match):
        parse_structured_csv(text, WIDE)


def test_load_file_with_bom_and_roundtrip(tmp_path):
    p = tmp_path / "bctc.csv"
    p.write_bytes("﻿ticker,year,quarter,tồn_kho\r\nAAA,2022,4,10\r\n".encode())
    vals = load_structured_csv(p, WIDE)
    assert vals[0].item == "tồn_kho" and vals[0].value == Decimal("10")
    assert vals[0].ref.content_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()
    assert RawStructuredValue.from_dict(vals[0].to_dict()) == vals[0]


def test_load_non_utf8_raises(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_bytes(b"ticker,year,x\nAAA,2022,t\xf4n\n")  # Latin-1, không phải UTF-8
    with pytest.raises(StructuredSourceError, match="UTF-8"):
        load_structured_csv(p, WIDE)


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"numbers": {"dash_tokens": ["-"], "dash_status": "OK"}}, "dash_status"),
        ({"numbers": {"dash_tokens": ["-"], "dash_status": "ZERO"}}, "dash_status"),
        ({"numbers": {"dash_tokens": ["0"], "dash_status": "NOT_APPLICABLE"}}, "dash_tokens"),
        ({"structured": {**BASE["structured"], "layout": "pivot"}}, "layout"),
        ({"structured": {**BASE["structured"], "number_format": "en"}}, "number_format"),
        (
            {"structured": {**BASE["structured"], "columns": {"company": None, "year": "y"}}},
            "bắt buộc",
        ),
        ({"structured": {**BASE["structured"], "columns": {"foo": "x"}}}, "khóa lạ"),
    ],
)
def test_settings_validation(patch, match):
    cfg = copy.deepcopy(BASE)
    cfg.update(patch)
    with pytest.raises(ConfigError, match=match):
        load_structured_settings(cfg)


def test_m6_duplicate_key_case_insensitive_company():
    vals = parse_structured_csv("ticker,year,quarter,x\nAAA,2022,4,100\naaa,2022,4,200\n", WIDE)
    assert all(v.status == ValueStatus.UNRESOLVED and v.value is None for v in vals)
    assert all(FLAG_DUPLICATE_CONFLICT in v.flags for v in vals)


def test_l3_duplicate_with_different_unit_conflicts():
    text = (
        "ticker,year,quarter,item,value,unit\n"
        "AAA,2022,4,inventory,100,VND\n"
        "AAA,2022,4,inventory,100,million_VND\n"
    )
    assert {v.status for v in parse_structured_csv(text, LONG)} == {ValueStatus.UNRESOLVED}


def test_m7_ignore_columns():
    s = _settings(unit="VND", ignore_columns=["quarter", "Unnamed: 0"])
    vals = parse_structured_csv("Unnamed: 0,ticker,year,quarter,revenue\n0,AAA,2022,4,100\n", s)
    assert [v.item for v in vals] == ["revenue"]


def test_l2_extra_cells_emitted_unresolved():
    vals = parse_structured_csv("ticker,year,quarter,x\nAAA,2022,4,1,999\n", WIDE)
    extra = [v for v in vals if v.raw == "999"]
    assert len(extra) == 1 and extra[0].status == ValueStatus.UNRESOLVED
    assert extra[0].value is None and FLAG_RAGGED_ROW in extra[0].flags


def test_l4_header_nfc_normalized():
    nfd = unicodedata.normalize("NFD", "doanh_thù")
    nfc = unicodedata.normalize("NFC", "doanh_thù")
    with pytest.raises(StructuredSourceError, match="trùng"):
        parse_structured_csv(f"ticker,year,quarter,{nfd},{nfc}\nAAA,2022,4,1,2\n", WIDE)


def test_every_non_ok_value_has_no_number():
    text = "ticker,year,quarter,a,b,c\nAAA,2022,9,1,-,x\n,2022,4,1,2,3\nAAA,2022,4,5,6,7,8\n"
    for v in parse_structured_csv(text, WIDE):
        assert (v.value is not None) == (v.status == ValueStatus.OK)
