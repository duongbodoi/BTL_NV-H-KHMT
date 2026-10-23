"""Test trích bảng trên bảng tổng hợp (công ty giả định); HPG chỉ ở test tích hợp cuối file."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from steel_xai.contracts import ValueStatus
from steel_xai.extraction import (
    TableExtraction,
    extract_document,
    extract_table,
    iter_values,
    load_table_settings,
    parse_period,
)
from steel_xai.extraction.tables import (
    FLAG_AFTER_SUBHEADER,
    FLAG_BLOCK_FLAGGED,
    FLAG_COLUMN_NUMBERING,
    FLAG_EMPTY_ROW_LABEL,
    FLAG_LABEL_FROM_ROWSPAN,
    FLAG_NO_DATA_ROWS,
    FLAG_NO_HEADER,
    FLAG_NO_LABEL_COLUMN,
    FLAG_PERIOD_AMBIGUOUS,
    FLAG_PERIOD_CUMULATIVE,
    FLAG_PERIOD_FROM_CORNER,
    FLAG_PERIOD_INCOMPLETE,
    FLAG_PERIOD_INVALID,
    FLAG_PERIOD_MISSING,
    FLAG_PERIOD_RELATIVE,
    FLAG_RESTATED,
    FLAG_SUBHEADER_IN_BODY,
    FLAG_UNIT_CONFLICT,
    FLAG_UNIT_MISSING,
    FLAG_UNIT_UNRECOGNIZED,
    ROLE_LABEL,
    ROLE_TEXT,
    ROLE_VALUE,
    Period,
    _Grid,
    _is_header_cell,
)
from steel_xai.extraction.tables import _parse as _parse_table_cell
from steel_xai.parsing import parse_text

FIXTURES = Path(__file__).parent / "fixtures"
HPG = FIXTURES / "HPG_financial_statements_2024_consolidated_extracted.txt"
SETTINGS = load_table_settings()


def _tr(*cells: str) -> str:
    return "<tr>" + "".join(c if c.startswith("<t") else f"<td>{c}</td>" for c in cells) + "</tr>"


def _table(*rows: str) -> str:
    return "<table>" + "".join(rows) + "</table>"


def _doc(*page_bodies: str, doc_id: str = "gia_dinh"):
    text = "".join(f"===== PAGE {i} =====\n{b}\n" for i, b in enumerate(page_bodies, start=1))
    return parse_text(text, doc_id)


def _one(*page_bodies: str) -> TableExtraction:
    tables = extract_document(_doc(*page_bodies), SETTINGS, company="GD")
    assert len(tables) == 1
    return tables[0]


def _by_raw(t: TableExtraction, raw: str):
    found = [v for v in t.values if v.raw == raw]
    assert len(found) == 1, (raw, [v.raw for v in t.values])
    return found[0]


INVENTORY = _table(
    _tr(
        '<td rowspan="2"></td>', '<td colspan="2">31/12/2022</td>', '<td colspan="2">1/1/2022</td>'
    ),
    _tr("Giá gốc VND", "Dự phòng VND", "Giá gốc VND", "Dự phòng VND"),
    _tr("Nguyên vật liệu", "1.234.567", "(12.000)", "1.000.000", "-"),
    _tr("Thành phẩm", "500", "-", "", "(1)"),
    _tr("", "1.235.067", "(12.000)", "1.000.000", "(1)"),
)


# ---------------------------------------------------------------------------
# Lưới, header, kỳ, đơn vị


def test_inventory_like_table_rowspan_colspan_header():
    t = _one("10. Hàng tồn kho\n" + INVENTORY)
    assert t.header_rows == 2
    assert t.column_roles == (ROLE_LABEL,) + (ROLE_VALUE,) * 4
    assert t.column_headers[1] == ("31/12/2022", "Giá gốc VND")
    assert t.column_headers[4] == ("1/1/2022", "Dự phòng VND")
    assert len(t.values) == 12

    v = _by_raw(t, "1.234.567")
    assert v.value == Decimal("1234567") and v.status == ValueStatus.OK
    assert v.row_label == "Nguyên vật liệu"
    assert v.period == Period("31/12/2022", date="2022-12-31", year=2022)
    assert v.unit == "VND"
    assert v.company == "GD"
    assert v.flags == ()

    negs = [x for x in t.values if x.raw == "(12.000)"]
    assert len(negs) == 2 and all(x.value == Decimal("-12000") for x in negs)
    assert t.values[3].period.date == "2022-01-01"


def test_dash_never_becomes_zero_in_table():
    t = _one(INVENTORY)
    dashes = [v for v in t.values if v.raw == "-"]
    assert len(dashes) == 2
    for v in dashes:
        assert v.value is None
        assert v.status == ValueStatus.NOT_APPLICABLE
        assert "dash" in v.flags


def test_empty_cell_is_missing_and_total_row_flagged():
    t = _one(INVENTORY)
    empty = [v for v in t.values if v.raw == ""]
    assert len(empty) == 1 and empty[0].status == ValueStatus.MISSING and empty[0].value is None
    total = [v for v in t.values if v.row_index == 4]
    assert len(total) == 4
    assert all(FLAG_EMPTY_ROW_LABEL in v.flags and v.row_label == "" for v in total)


def test_provenance_and_ids_deterministic():
    doc = _doc("chữ", "Tiêu đề\n" + INVENTORY)
    a = extract_document(doc, SETTINGS)
    b = extract_document(doc, SETTINGS)
    assert a == b
    v = a[0].values[0]
    assert v.page == 2
    assert v.block_id == "gia_dinh:p0002:b002"
    assert v.value_id == "gia_dinh:p0002:b002:r002:c001"
    prov = v.provenance()
    assert (prov.doc_id, prov.page, prov.block_id, prov.source_text) == (
        "gia_dinh",
        2,
        "gia_dinh:p0002:b002",
        "1.234.567",
    )
    assert doc.get_block(prov.block_id).kind == "table"


def test_code_and_note_columns_are_row_attributes():
    t = _one(
        _table(
            _tr("", "Mã số", "Thuyết minh", "31/12/2022 VND", "1/1/2022 VND"),
            _tr("Hàng tồn kho", "140", "10.1", "46.091", "34.504"),
            _tr("Phải thu", "216", "9.2", "-", "781"),
        )
    )
    assert t.column_roles == (ROLE_LABEL, "code", "note", ROLE_VALUE, ROLE_VALUE)
    assert len(t.values) == 4  # mã số/thuyết minh không sinh giá trị
    v = _by_raw(t, "46.091")
    assert (v.row_code, v.note_ref, v.unit, v.period.date) == ("140", "10.1", "VND", "2022-12-31")


def test_year_headers_and_restated_flag():
    t = _one(
        _table(
            _tr("", "Mã số", "2022 VND", "2021VNDĐã điều chỉnh lại"),
            _tr("Doanh thu", "10", "1.000", "900"),
        )
    )
    cur, prev = t.values
    assert cur.period == Period("2022 VND", year=2022) and FLAG_RESTATED not in cur.flags
    assert prev.period.year == 2021 and FLAG_RESTATED in prev.flags
    assert prev.unit == "VND"  # cụm "đã điều chỉnh lại" không che mất đơn vị


def test_period_missing_and_unit_missing_flagged_not_guessed():
    t = _one(_table(_tr("", "Nguyên tệ", "Số cổ phiếu"), _tr("USD", "1.000", "2.000")))
    for v in t.values:
        assert v.period is None and FLAG_PERIOD_MISSING in v.flags
        assert v.unit is None and FLAG_UNIT_MISSING in v.flags


def test_percent_cell_unit():
    t = _one(_table(_tr("", "31/12/2022"), _tr("Tỷ lệ sở hữu", "65,5%")))
    v = t.values[0]
    assert v.value == Decimal("65.5") and v.is_percent and v.unit == "percent"
    assert FLAG_UNIT_MISSING not in v.flags


def test_percent_cell_in_vnd_column_flags_conflict():
    t = _one(_table(_tr("", "31/12/2022 VND"), _tr("Tỷ lệ", "5%")))
    assert FLAG_UNIT_CONFLICT in t.values[0].flags


# ---------------------------------------------------------------------------
# Không bỏ qua im lặng


def test_merged_numbers_unresolved_not_dropped():
    t = _one(
        _table(
            _tr("", "2022 VND", "2021 VND"),
            _tr("Phân bổ cho", "12.021(1.420)", "6.835(34)"),
            _tr("Lãi cơ bản", "1.751", "Đã điều chỉnh lại1.005"),
        )
    )
    assert t.header_rows == 1
    merged = _by_raw(t, "12.021(1.420)")
    assert merged.status == ValueStatus.UNRESOLVED and merged.value is None
    assert "multiple_numbers" in merged.flags
    assert merged.period.year == 2022
    mixed = _by_raw(t, "Đã điều chỉnh lại1.005")
    assert mixed.status == ValueStatus.UNRESOLVED and "mixed_text" in mixed.flags


def test_row_with_only_broken_numbers_is_not_swallowed_as_header():
    # Dòng đầu chỉ có số dính: không được coi là header.
    t = _one(_table(_tr("Khoản A", "1.000(2.000)"), _tr("Khoản B", "3.000")))
    assert t.header_rows == 0 and FLAG_NO_HEADER in t.flags
    assert _by_raw(t, "1.000(2.000)").status == ValueStatus.UNRESOLVED
    # Dòng giữa bảng chỉ có số hỏng: không được coi là header giữa bảng.
    t = _one(
        _table(
            _tr("", "2022"),
            _tr("Khoản A", "1.000"),
            _tr("Khoản B", "1.000(2.000)"),
            _tr("Khoản C", "5"),
        )
    )
    assert t.subheader_rows == ()
    assert _by_raw(t, "1.000(2.000)").status == ValueStatus.UNRESOLVED
    assert _by_raw(t, "5").period.year == 2022


def test_second_column_of_broken_numbers_is_value_not_label():
    t = _one(_table(_tr("", "Thời gian"), _tr("Nhà cửa", "5 – 50 năm"), _tr("Máy móc", "1 – 25")))
    assert t.column_roles == (ROLE_LABEL, ROLE_VALUE)
    assert {v.status for v in t.values} == {ValueStatus.UNRESOLVED}


def test_leading_text_columns_join_row_label():
    t = _one(
        _table(
            _tr("", "Hoạt động chính", "Tỷ lệ"),
            _tr("Công ty A", "Sản xuất thép", "100%"),
            _tr("Công ty B", "Thương mại", "khoảng 5"),
        )
    )
    assert t.column_roles == (ROLE_LABEL, ROLE_LABEL, ROLE_VALUE)
    assert [v.row_label for v in t.values] == [
        "Công ty A / Sản xuất thép",
        "Công ty B / Thương mại",
    ]
    assert [v.raw for v in t.values] == ["100%", "khoảng 5"]
    assert t.values[1].status == ValueStatus.UNRESOLVED


def test_all_text_body_row_without_period_keeps_column_header():
    # Dòng toàn chữ giữa bảng, không mang kỳ/đơn vị: ghi nhận ở subheader_rows, giữ header cột.
    t = _one(
        _table(
            _tr("", "2022 VND"),
            _tr("Khoản A", "5"),
            _tr("Phương pháp", "Bình quân"),
            _tr("Khoản C", "6"),
        )
    )
    assert t.subheader_rows == (2,) and FLAG_SUBHEADER_IN_BODY in t.flags
    v = _by_raw(t, "6")
    assert (v.period.year, v.unit) == (2022, "VND")
    assert v.column_header == ("Phương pháp", "2022 VND", "Bình quân")
    assert FLAG_AFTER_SUBHEADER in v.flags


def test_label_column_header_period_applies_to_row_block():
    t = _one(
        _table(
            _tr("Cho năm kết thúc ngày 31 tháng 12 năm 2022", "Việt Nam VND", "Hợp nhất VND"),
            _tr("Tổng doanh thu", "100", "120"),
            _tr("Cho năm kết thúc ngày 31 tháng 12 năm 2021", "Việt Nam VND", "Hợp nhất VND"),
            _tr("Tổng doanh thu", "90", "95"),
        )
    )
    assert _by_raw(t, "100").period.date == "2022-12-31"
    assert _by_raw(t, "95").period.date == "2021-12-31"
    # Góc trái mâu thuẫn với kỳ của cột → không chọn.
    t = _one(_table(_tr("Năm 2022", "31/12/2021"), _tr("Tiền", "5")))
    assert t.values[0].period is None and FLAG_PERIOD_AMBIGUOUS in t.values[0].flags


def test_text_only_column_after_values_not_emitted():
    t = _one(
        _table(
            _tr("", "2022", "Ghi chú"),
            _tr("Khoản A", "5", "theo hợp đồng"),
            _tr("Khoản B", "6", ""),
        )
    )
    assert t.column_roles == (ROLE_LABEL, ROLE_VALUE, ROLE_TEXT)
    assert [v.raw for v in t.values] == ["5", "6"]


def test_index_column_kept_as_row_seq():
    t = _one(
        _table(
            '<tr><td rowspan="2">STT</td><td rowspan="2">Công ty con</td>'
            '<td colspan="2">Tỷ lệ lợi ích</td></tr>',
            _tr("31/12/2022", "1/1/2022"),
            _tr("1", "Công ty A", "99,9%", "99,8%"),
            _tr("2", "Công ty B", "-", "50%"),
        )
    )
    assert t.column_roles == ("index", ROLE_LABEL, ROLE_VALUE, ROLE_VALUE)
    v = _by_raw(t, "99,8%")
    assert (v.row_seq, v.row_label, v.period.date, v.unit) == (
        "1",
        "Công ty A",
        "2022-01-01",
        "percent",
    )


def test_table_without_numbers_flagged():
    t = _one(_table(_tr("Hội đồng", "Chức vụ"), _tr("Ông A", "Chủ tịch")))
    assert FLAG_NO_DATA_ROWS in t.flags and t.values == ()


def test_no_label_column_flagged():
    t = _one(_table(_tr("31/12/2022VND", "1/1/2022VND"), _tr("61.555", "48.252")))
    assert FLAG_NO_LABEL_COLUMN in t.flags
    assert [v.period.date for v in t.values] == ["2022-12-31", "2022-01-01"]


def test_blank_rows_skipped_but_label_rows_kept_as_missing():
    t = _one(
        _table(
            _tr("", "2022"),
            _tr("", ""),
            _tr("Điều chỉnh cho các khoản", ""),
            _tr("Khấu hao", "10"),
        )
    )
    assert [(v.row_label, v.status) for v in t.values] == [
        ("Điều chỉnh cho các khoản", ValueStatus.MISSING),
        ("Khấu hao", ValueStatus.OK),
    ]


def test_block_flags_propagate():
    doc = _doc("<table><tr><td>Khoản</td><td>12</td>")  # bảng không đóng (Phase 1 gắn cờ)
    t = extract_document(doc, SETTINGS)[0]
    assert FLAG_BLOCK_FLAGGED in t.flags
    assert all(FLAG_BLOCK_FLAGGED in v.flags for v in t.values)


# ---------------------------------------------------------------------------
# Header giữa bảng


def test_subheader_with_new_period_replaces_header():
    t = _one(
        _table(
            _tr("Cho năm 2022", "Việt Nam VND", "Hợp nhất VND"),
            _tr("Doanh thu", "100", "120"),
            _tr("", "31/12/2021 VND", "31/12/2021 VND"),
            _tr("Doanh thu", "90", "95"),
        )
    )
    assert FLAG_SUBHEADER_IN_BODY in t.flags and t.subheader_rows == (2,)
    first = _by_raw(t, "100")
    assert FLAG_AFTER_SUBHEADER not in first.flags
    later = _by_raw(t, "90")
    assert later.period.date == "2021-12-31"  # không thừa hưởng kỳ của header đầu
    assert FLAG_AFTER_SUBHEADER in later.flags
    assert not any(v.row_index == 2 for v in t.values)


def test_subheader_without_period_drops_old_period():
    # Hai bảng bị OCR gộp: bảng sau có header riêng, không có kỳ.
    t = _one(
        _table(
            _tr("", "31/12/2022VND", "1/1/2022VND"),
            _tr("Công cụ", "61", "48"),
            '<tr><td colspan="3">15.2 Chi phí trả trước dài hạn</td></tr>',
            _tr("", "Công cụ VND", "Tổng cộng VND"),
            _tr("Số dư đầu năm", "176", "4.215"),
        )
    )
    v = _by_raw(t, "176")
    assert v.period is None and FLAG_PERIOD_MISSING in v.flags
    assert v.column_header == ("15.2 Chi phí trả trước dài hạn", "Công cụ VND")
    assert v.unit == "VND"


def test_full_width_section_title_keeps_column_header():
    t = _one(
        _table(
            '<tr><td colspan="3">Lưu chuyển tiền từ hoạt động kinh doanh</td></tr>',
            _tr("", "2022 VND", "2021 VND"),
            _tr("Lợi nhuận", "10", "9"),
            '<tr><td colspan="3">Lưu chuyển tiền từ hoạt động đầu tư</td></tr>',
            _tr("Tiền chi mua TSCĐ", "(5)", "(4)"),
        )
    )
    v = _by_raw(t, "(5)")
    assert v.period.year == 2022 and v.unit == "VND"
    assert v.column_header == ("Lưu chuyển tiền từ hoạt động đầu tư", "2022 VND")


def test_section_label_row_is_not_header():
    t = _one(
        _table(
            _tr("", "2022"),
            _tr("Tài sản ngắn hạn", ""),
            _tr("Tiền", "5"),
        )
    )
    assert t.subheader_rows == ()
    assert _by_raw(t, "5").period.year == 2022


# ---------------------------------------------------------------------------
# Đơn vị


def test_unit_statement_on_page():
    t = _one("Đơn vị tính: triệu đồng\n\n" + _table(_tr("", "2022"), _tr("Tiền", "5")))
    assert t.values[0].unit == "million_VND"


def test_unit_statement_conflicting_with_header():
    t = _one("(Đơn vị: triệu đồng)\n\n" + _table(_tr("", "2022 VND"), _tr("Tiền", "5")))
    v = t.values[0]
    assert v.unit is None and FLAG_UNIT_CONFLICT in v.flags


def test_unit_statement_requires_colon_and_known_unit():
    t = _one("Đơn vị báo cáo triệu đồng\n\n" + _table(_tr("", "2022 VND"), _tr("Tiền", "5")))
    assert t.values[0].unit == "VND"
    t = _one("Đơn vị tính: đô la Mỹ\n\n" + _table(_tr("", "2022"), _tr("Tiền", "5")))
    v = t.values[0]
    assert v.unit is None and FLAG_UNIT_UNRECOGNIZED in v.flags


def test_unknown_scale_unit_not_collapsed_to_vnd():
    t = _one(_table(_tr("", "2022 ngàn VND"), _tr("Tiền", "5")))
    v = t.values[0]
    assert v.unit is None and FLAG_UNIT_UNRECOGNIZED in v.flags


@pytest.mark.parametrize(
    ("header", "unit"),
    [
        ("Giá gốc VND", "VND"),
        ("Tổng cộngVND", "VND"),
        ("2022 triệu VND", "million_VND"),
        ("Số tiền (tỷ đồng)", "billion_VND"),
        ("Giá trị hợp đồng", None),  # "đồng" trong "hợp đồng" không phải đơn vị
    ],
)
def test_unit_from_header(header, unit):
    t = _one(_table(_tr("", header), _tr("Khoản", "5")))
    assert t.values[0].unit == unit


# ---------------------------------------------------------------------------
# Kỳ


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["31/12/2022 VND"], Period("31/12/2022 VND", date="2022-12-31", year=2022)),
        (
            ["Tại ngày 31 tháng 12 năm 2022"],
            Period("Tại ngày 31 tháng 12 năm 2022", "2022-12-31", 2022),
        ),
        (["2022", "31/12/2022"], Period("2022 | 31/12/2022", "2022-12-31", 2022)),
        (["Quý 3/2023"], Period("Quý 3/2023", year=2023, quarter=3)),
        (["Quý IV năm 2023"], Period("Quý IV năm 2023", year=2023, quarter=4)),
        (["Q2 2023"], Period("Q2 2023", year=2023, quarter=2)),
        (["2023(Số cổ phiếu)"], Period("2023(Số cổ phiếu)", year=2023)),
    ],
)
def test_parse_period_ok(texts, expected):
    assert parse_period(texts) == (expected, ())


@pytest.mark.parametrize(
    ("texts", "flag"),
    [
        (["Giá gốc"], FLAG_PERIOD_MISSING),
        ([], FLAG_PERIOD_MISSING),
        (["31/12/2022", "1/1/2022"], FLAG_PERIOD_AMBIGUOUS),
        (["2022", "2021"], FLAG_PERIOD_AMBIGUOUS),
        (["31/12/2022", "2021"], FLAG_PERIOD_AMBIGUOUS),
        (["Quý 3/2023", "30/9/2023"], FLAG_PERIOD_AMBIGUOUS),
        (["31/02/2022"], FLAG_PERIOD_INVALID),
        (["Mã số 1.234"], FLAG_PERIOD_MISSING),
    ],
)
def test_parse_period_not_guessed(texts, flag):
    assert parse_period(texts) == (None, (flag,))


def test_two_period_header_cell_spanning_columns_is_ambiguous():
    t = _one(
        _table(
            _tr("", "31/12/2022", "1/1/2022"),
            '<tr><td>Khoản</td><td colspan="2">1.000</td></tr>',
        )
    )
    v = t.values[0]
    assert v.period is None and FLAG_PERIOD_AMBIGUOUS in v.flags
    assert "cell_spans_columns" in v.flags


# ---------------------------------------------------------------------------
# Serialize, API


def test_json_roundtrip():
    t = _one(INVENTORY)
    d = json.loads(json.dumps(t.to_dict(), ensure_ascii=False))
    assert TableExtraction.from_dict(d) == t
    assert d["values"][0]["value"] == "1234567"  # Decimal → chuỗi, không mất chính xác


def test_status_counts_and_iter_values():
    tables = extract_document(_doc(INVENTORY, INVENTORY), SETTINGS)
    assert tables[0].status_counts() == {"OK": 9, "NOT_APPLICABLE": 2, "MISSING": 1}
    assert len(list(iter_values(tables))) == 24


def test_extract_table_rejects_text_block():
    doc = _doc("chỉ có chữ")
    with pytest.raises(ValueError):
        extract_table(next(doc.iter_blocks()), SETTINGS)


# ---------------------------------------------------------------------------
# Bất biến: không ô chứa chữ số nào biến mất im lặng


def _parse_cell(text):
    return _parse_table_cell(text, SETTINGS)


def _assert_every_digit_cell_accounted(doc, tables):
    """Theo VỊ TRÍ ô gốc (không so chuỗi con): mọi ô có chữ số phải là một giá trị đúng (r, c),
    hoặc nằm ở dòng header/header giữa bảng, hoặc ở cột thuộc tính (nhãn/mã/thuyết minh/STT)."""
    by_block = {t.block_id: t for t in tables}
    for block in doc.iter_blocks("table"):
        t = by_block[block.block_id]
        grid = _Grid.build(block.rows or ())
        value_pos = {(v.row_index, v.col_index): v for v in t.values}
        for s in grid.origins():
            text = s.cell.text.strip()
            if not any(ch.isdigit() for ch in text):
                continue
            if (s.row, s.col) in value_pos:
                assert value_pos[(s.row, s.col)].raw == s.cell.text
                continue
            in_header = s.row < t.header_rows
            if s.row in t.subheader_rows:
                # Ô có số trong dòng header giữa bảng chỉ hợp lệ khi là kỳ; kỳ trần chỉ khi
                # dòng không có ô chữ (khớp luật của extractor, xem N1/N9 review vòng 2).
                row_cells = [x for x in grid.starting_in(s.row) if x.cell.text.strip()]
                bare_ok = not any(_parse_cell(x.cell.text).reason == "no_digits" for x in row_cells)
                in_header = _is_header_cell(_parse_cell(s.cell.text), bare_ok)
            attr_col = t.column_roles[s.col] in (ROLE_LABEL, "code", "note", "index")
            assert in_header or attr_col, (block.block_id, s.row, s.col, text)


def test_every_digit_cell_accounted_synthetic():
    doc = _doc(INVENTORY, _table(_tr("", "2022"), _tr("A", "1.0(2)"), _tr("B", "-")))
    _assert_every_digit_cell_accounted(doc, extract_document(doc, SETTINGS))


# ---------------------------------------------------------------------------
# Tích hợp fixture HPG (chỉ là ví dụ; kỳ vọng lấy từ chính file)


@pytest.fixture(scope="module")
def hpg():
    if not HPG.exists():
        pytest.skip("thiếu fixture HPG")
    from steel_xai.parsing import parse_file

    doc = parse_file(HPG)
    return doc, extract_document(doc, SETTINGS, company="HPG")


def test_hpg_inventory_note_values(hpg):
    _, tables = hpg
    values = list(iter_values(tables))
    raw_material = [v for v in values if v.raw == "20.324.528.389.776"]
    assert len(raw_material) == 1
    v = raw_material[0]
    assert v.value == Decimal("20324528389776") and v.status == ValueStatus.OK
    assert (v.page, v.row_label, v.unit) == (40, "Nguyên vật liệu", "VND")
    assert v.period.date == "2024-12-31"
    assert v.column_header == ("31/12/2024", "Giá gốc VND")


def test_hpg_dashes_and_merged_cells(hpg):
    _, tables = hpg
    values = list(iter_values(tables))
    dashes = [v for v in values if v.raw.strip() == "-"]
    assert dashes and all(
        v.value is None and v.status == ValueStatus.NOT_APPLICABLE for v in dashes
    )
    merged = [v for v in values if v.raw == "12.021.443.836.074(1.420.214.803)"]
    assert len(merged) == 1 and merged[0].status == ValueStatus.UNRESOLVED


def test_hpg_invariants(hpg):
    doc, tables = hpg
    assert len(tables) == sum(1 for _ in doc.iter_blocks("table"))
    for v in iter_values(tables):
        assert (v.value is not None) == (v.status == ValueStatus.OK)
        assert v.company == "HPG"
    _assert_every_digit_cell_accounted(doc, tables)


# ---------------------------------------------------------------------------
# Hồi quy từ review độc lập Phase 2 (input tái hiện của reviewer)


@pytest.mark.parametrize(
    "title",
    ["Thuyết minh báo cáo tài chính hợp nhất", "5. Hàng tồn kho (xem Thuyết minh 3.7)"],
)
def test_c1_title_with_role_keyword_does_not_hide_values(title):
    t = _one(
        _table(
            f'<tr><td colspan="3">{title}</td></tr>',
            _tr("Chỉ tiêu", "31/12/2024 VND", "1/1/2024 VND"),
            _tr("Tiền", "1.000", "900"),
        )
    )
    assert t.column_roles == (ROLE_LABEL, ROLE_VALUE, ROLE_VALUE)
    assert [(v.row_label, v.value) for v in t.values] == [
        ("Tiền", Decimal("1000")),
        ("Tiền", Decimal("900")),
    ]


def test_c1_label_header_mentioning_code_keyword_stays_label():
    t = _one(_table(_tr("Chỉ tiêu (theo mã số thuế)", "2024 VND"), _tr("Tiền", "5")))
    assert t.column_roles[0] == ROLE_LABEL and t.values[0].row_label == "Tiền"


def test_c2_year_values_in_body_are_data():
    t = _one(
        _table(
            _tr("Chỉ tiêu", "2024 VND", "2023 VND"),
            _tr("Doanh thu", "1.000.000", "900.000"),
            _tr("Số lao động", "2015", "1987"),
            _tr("Chi phí", "500", "400"),
        )
    )
    assert t.subheader_rows == ()
    v = _by_raw(t, "2015")
    assert v.value == Decimal("2015") and v.period.year == 2024
    assert _by_raw(t, "400").period.year == 2023


def test_c2_year_values_in_first_data_row_are_data():
    t = _one(_table(_tr("Chỉ tiêu", "2024", "2023"), _tr("Số lao động", "2015", "1987")))
    assert t.header_rows == 1
    assert _by_raw(t, "1987").period.year == 2023


def test_c2_dates_in_body_do_not_open_new_header():
    t = _one(
        _table(
            _tr("Khoản vay", "Ngày đáo hạn", "Số dư 31/12/2024 VND"),
            _tr("Vay A", "30/06/2025", "1.000"),
            _tr("Vay B", "31/12/2026", ""),
            _tr("Vay C", "15/03/2025", "500"),
        )
    )
    assert t.subheader_rows == ()
    assert _by_raw(t, "500").period.date == "2024-12-31"
    assert _by_raw(t, "31/12/2026").status == ValueStatus.UNRESOLVED


def test_h1_relative_period_column_not_taken_from_corner():
    t = _one(
        _table(
            _tr("Cho năm kết thúc ngày 31/12/2024", "Năm nay VND", "Năm trước VND"),
            _tr("Doanh thu", "1.000", "900"),
        )
    )
    for v in t.values:
        assert v.period is None and FLAG_PERIOD_RELATIVE in v.flags


@pytest.mark.parametrize(
    ("page", "header"),
    [
        ("Đơn vị tính: VND\n\n", "Nguyên tệ (USD)"),
        ("", "USD"),
        ("", "Nghìn USD"),
        ("", "2024 Tr. VND"),
        ("Đơn vị tính: VND\n\n", "Nguyên tệ"),
    ],
)
def test_h2_unrecognized_currency_or_scale_not_vnd(page, header):
    t = _one(page + _table(_tr("", header), _tr("Tiền", "1.000")))
    v = t.values[0]
    assert v.unit is None
    assert FLAG_UNIT_UNRECOGNIZED in v.flags


def test_h2_vnd_with_d_stroke_recognized():
    t = _one(_table(_tr("", "2024 triệu VNĐ"), _tr("Tiền", "1")))
    assert t.values[0].unit == "million_VND"


def test_h3_quarter_without_year_joins_parent_year():
    t = _one(
        _table(
            _tr("Chỉ tiêu", '<td colspan="2">Năm 2024</td>'),
            _tr("", "Quý 3", "Quý 4"),
            _tr("Doanh thu", "1.000", "900"),
        )
    )
    assert t.header_rows == 2
    assert [(v.period.year, v.period.quarter) for v in t.values] == [(2024, 3), (2024, 4)]


def test_h3_quarter_without_any_year_is_incomplete():
    assert parse_period(["Quý 3"]) == (None, (FLAG_PERIOD_INCOMPLETE,))


def test_m1_column_numbering_row_is_header():
    t = _one(
        _table(
            _tr("Chỉ tiêu", "Mã số", "31/12/2024 VND", "1/1/2024 VND"),
            _tr("1", "2", "3", "4"),
            _tr("Tiền", "110", "1.000", "900"),
        )
    )
    assert FLAG_COLUMN_NUMBERING in t.flags and t.header_rows == 2
    assert t.column_roles == (ROLE_LABEL, "code", ROLE_VALUE, ROLE_VALUE)
    assert [v.raw for v in t.values] == ["1.000", "900"]
    assert t.values[0].column_header == ("Chỉ tiêu", "31/12/2024 VND")  # không có "3"


def test_m2_section_rows_with_index_column_keep_header():
    t = _one(
        _table(
            _tr("STT", "Chỉ tiêu", "31/12/2024 VND"),
            _tr("", "Tài sản ngắn hạn", ""),
            _tr("1", "Tiền", "1.000"),
            _tr("", "Tài sản dài hạn", ""),
            _tr("2", "TSCĐ", "2.000"),
        )
    )
    assert t.subheader_rows == ()
    v = _by_raw(t, "2.000")
    assert (v.period.date, v.unit, v.row_seq) == ("2024-12-31", "VND", "2")


def test_m3_cumulative_not_quarter():
    t = _one(
        _table(
            _tr("", "Quý 4/2024", "Lũy kế từ đầu năm đến cuối quý 4/2024"),
            _tr("Doanh thu", "100", "400"),
        )
    )
    q, ytd = t.values
    assert (q.period.year, q.period.quarter) == (2024, 4)
    assert ytd.period is None and FLAG_PERIOD_CUMULATIVE in ytd.flags


def test_l1_broken_number_in_first_column_not_swallowed():
    t = _one(_table(_tr("1.234.5", "2024 VND"), _tr("Tiền", "1.000")))
    assert t.header_rows == 0 and FLAG_NO_HEADER in t.flags


def test_l5_restated_in_parentheses_keeps_unit():
    t = _one(_table(_tr("", "2023 VND (trình bày lại)"), _tr("Tiền", "1")))
    v = t.values[0]
    assert v.unit == "VND" and FLAG_RESTATED in v.flags


def test_l7_rowspan_label_flagged():
    t = _one(
        _table(
            _tr("", "2024"),
            '<tr><td rowspan="2">Phải thu</td><td>1</td></tr>',
            _tr("2"),
        )
    )
    first, second = t.values
    assert FLAG_LABEL_FROM_ROWSPAN not in first.flags
    assert second.row_label == "Phải thu" and FLAG_LABEL_FROM_ROWSPAN in second.flags


def test_accumulated_depreciation_is_not_cumulative_period():
    t = _one(_table(_tr("", "Giá trị hao mòn lũy kế 31/12/2024 VND"), _tr("Máy móc", "(5)")))
    v = t.values[0]
    assert v.period.date == "2024-12-31" and FLAG_PERIOD_CUMULATIVE not in v.flags


# ---------------------------------------------------------------------------
# Hồi quy từ review độc lập vòng 2


def test_n1_year_values_with_label_in_second_column_are_data():
    t = _one(
        _table(
            _tr("STT", "Chỉ tiêu", "2024 VND", "2023 VND"),
            _tr("1", "Doanh thu", "1.000", "900"),
            _tr("", "Số lao động", "2015", "1987"),
            _tr("2", "Chi phí", "500", "400"),
        )
    )
    assert t.subheader_rows == ()
    assert _by_raw(t, "2015").value == Decimal("2015")
    assert [_by_raw(t, x).period.year for x in ("500", "400")] == [2024, 2023]


def test_n2_text_row_with_year_does_not_set_period():
    t = _one(
        _table(
            _tr("Khoản vay", "31/12/2024 VND", "1/1/2024 VND"),
            _tr("Vay A", "1.000", "900"),
            _tr("Thời hạn", "Đến năm 2030", "Đến năm 2030"),
            _tr("Vay B", "2.000", "1.800"),
        )
    )
    for raw in ("2.000", "1.800"):
        v = _by_raw(t, raw)
        assert v.period is None and FLAG_PERIOD_AMBIGUOUS in v.flags


def test_n3_bare_year_header_after_title_row():
    t = _one(
        _table(
            '<tr><td colspan="3">Hàng tồn kho</td></tr>',
            _tr("Chỉ tiêu", "2024", "2023"),
            _tr("Tiền", "1.000", "900"),
        )
    )
    assert t.header_rows == 2
    assert [(v.raw, v.period.year) for v in t.values] == [("1.000", 2024), ("900", 2023)]


def test_n3_bare_date_header_after_unit_row():
    t = _one(
        _table(
            _tr("", '<td colspan="2">Giá gốc VND</td>'),
            _tr("Chỉ tiêu", "31/12/2024", "1/1/2024"),
            _tr("Tiền", "1.000", "900"),
        )
    )
    assert t.header_rows == 2
    assert [v.period.date for v in t.values] == ["2024-12-31", "2024-01-01"]


@pytest.mark.parametrize(
    ("cur", "prev"),
    [
        ("Năm tài chính này VND", "Năm tài chính trước VND"),
        ("Kỳ báo cáo VND", "Số liệu so sánh VND"),
        ("Năm nay VND", "Năm ngoái VND"),
    ],
)
def test_n4_relative_columns_not_given_corner_period(cur, prev):
    t = _one(
        _table(
            _tr("Cho năm kết thúc ngày 31/12/2024", cur, prev),
            _tr("Doanh thu", "1.000", "900"),
        )
    )
    for v in t.values:
        assert v.period is None and FLAG_PERIOD_RELATIVE in v.flags


def test_n4_corner_only_period_is_flagged():
    t = _one(
        _table(
            _tr("Cho năm kết thúc ngày 31/12/2024", "Việt Nam VND", "Châu Á VND"),
            _tr("Doanh thu", "1.000", "900"),
        )
    )
    for v in t.values:
        assert v.period.date == "2024-12-31" and FLAG_PERIOD_FROM_CORNER in v.flags


def test_prepaid_label_is_not_relative_period():
    t = _one(_table(_tr("", "Chi phí trả trước 31/12/2024 VND"), _tr("Tiền", "5")))
    assert t.values[0].period.date == "2024-12-31"


def test_n5_letter_and_number_column_numbering_row():
    t = _one(
        _table(
            _tr("Tài sản", "Mã số", "Thuyết minh", "31/12/2024 VND", "1/1/2024 VND"),
            _tr("A", "B", "C", "1", "2"),
            _tr("Tiền", "110", "V.1", "1.000", "900"),
        )
    )
    assert FLAG_COLUMN_NUMBERING in t.flags
    assert [v.raw for v in t.values] == ["1.000", "900"]


def test_n6_first_section_row_after_header_not_in_header():
    t = _one(
        _table(
            _tr("STT", "Chỉ tiêu", "31/12/2024 VND"),
            _tr("", "Tài sản ngắn hạn", ""),
            _tr("1", "Tiền", "1.000"),
        )
    )
    assert t.header_rows == 1
    assert _by_raw(t, "1.000").column_header == ("Chỉ tiêu", "31/12/2024 VND")


def test_n8_word_ending_like_abbreviation_is_still_vnd():
    t = _one(_table(_tr("", "Tổng cộng. VND"), _tr("Tiền", "5")))
    assert t.values[0].unit == "VND"


def test_n10_numbered_title_row_is_header():
    t = _one(
        _table(
            '<tr><td colspan="3">5. Hàng tồn kho (xem Thuyết minh 3.7)</td></tr>',
            _tr("Chỉ tiêu", "31/12/2024 VND", "1/1/2024 VND"),
            _tr("Tiền", "1.000", "900"),
        )
    )
    assert t.header_rows == 2 and FLAG_NO_HEADER not in t.flags


def test_dangling_date_in_two_tier_header_is_a_header_not_data():
    # "Tại ngày 31 tháng 12 năm" + "2023 VND" ở dòng dưới; cột đầu là Mã số (rowspan 2).
    t = _one(
        _table(
            '<tr><td rowspan="2">Mã số</td><td rowspan="2">TÀI SẢN</td>'
            '<td colspan="2">Tại ngày 31 tháng 12 năm</td></tr>',
            "<tr><td>2023 VND</td><td>2022 VND</td></tr>",
            _tr("140", "Hàng tồn kho", "5.000", "4.000"),
        )
    )
    assert t.header_rows == 2 and FLAG_NO_HEADER not in t.flags
    v = _by_raw(t, "5.000")
    assert (v.row_code, v.row_label, v.unit) == ("140", "Hàng tồn kho", "VND")
    assert v.column_header[-2:] == ("Tại ngày 31 tháng 12 năm", "2023 VND")
    assert v.period is not None and v.period.year == 2023
