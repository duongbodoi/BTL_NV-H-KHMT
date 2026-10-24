"""Test nhập giá từ xlsx, tool metric_level, chỉ tiêu Phase 3 mới và dữ liệu giá thật của nhóm."""

import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from test_verification import F, S, ctx, facts_for, not_evaluable, value_of

from steel_xai.config import load_config
from steel_xai.normalization import load_normalization_settings
from steel_xai.parsing import parse_text
from steel_xai.verification import read_series_csv
from steel_xai.verification.price_import import (
    PriceImportError,
    monthly_series_from_xlsx,
    read_xlsx_rows,
    write_series_csv,
)

D = Decimal
REPO = Path(__file__).resolve().parents[1]
_SHEET = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    "{rows}</sheetData></worksheet>"
)


def make_xlsx(path, rows, shared=True):
    """rows: [(A, B)]; chuỗi → shared string hoặc inlineStr, số → ô số."""
    strings: list[str] = []
    xml_rows = []
    for i, (a, b) in enumerate(rows, start=1):
        cells = []
        for col, v in (("A", a), ("B", b)):
            if v is None:
                continue
            if isinstance(v, str) and shared:
                strings.append(v)
                cells.append(f'<c r="{col}{i}" t="s"><v>{len(strings) - 1}</v></c>')
            elif isinstance(v, str):
                cells.append(f'<c r="{col}{i}" t="inlineStr"><is><t>{v}</t></is></c>')
            else:
                cells.append(f'<c r="{col}{i}"><v>{v}</v></c>')
        xml_rows.append(f'<row r="{i}">{"".join(cells)}</row>')
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", _SHEET.format(rows="".join(xml_rows)))
        z.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{sst}</sst>',
        )


# ---------------- importer


def test_pink_sheet_style_months_with_header_row(tmp_path):
    p = tmp_path / "p.xlsx"
    make_xlsx(p, [("Month", "Iron ore prices($/dmtu)"), ("2015M02", 62.8), ("2015M01", "68.2")])
    assert monthly_series_from_xlsx(p) == [("2015-01", D("68.2")), ("2015-02", D("62.8"))]


@pytest.mark.parametrize("shared", [True, False])
def test_other_date_formats_and_inline_strings(tmp_path, shared):
    p = tmp_path / "p.xlsx"
    make_xlsx(p, [("2020-01", 1), ("2020/02/15", 2), ("2020-03-31", 3), (43922, 4)], shared)
    got = monthly_series_from_xlsx(p)
    assert [m for m, _ in got] == ["2020-01", "2020-02", "2020-03", "2020-04"]  # 43922 = 2020-04-01


@pytest.mark.parametrize(
    ("rows", "needle"),
    [
        ([("2020M01", 1), ("2020M01", 2)], "lặp"),
        ([("2020M13", 1)], "tháng không hợp lệ"),
        ([("2020M01", 1), ("hôm qua", 2)], "ngày không hiểu"),
        ([("2020M01", 1), ("2020M02", -3)], "giá không hợp lệ"),
        ([("2020M01", 1), ("2020M02", 0)], "giá không hợp lệ"),
        ([("2020M01", 1), ("2020M02", None)], "thiếu ngày hoặc giá"),
        ([("2020M01", 1), (None, 5)], "thiếu ngày hoặc giá"),
        ([("2020M01", "1e400")], "giá không hợp lệ"),
        ([("Month", "Price")], "không có dòng dữ liệu"),
    ],
)
def test_bad_rows_fail_the_whole_import_with_row_number(tmp_path, rows, needle):
    p = tmp_path / "p.xlsx"
    make_xlsx(p, rows)
    with pytest.raises(PriceImportError, match=needle):
        monthly_series_from_xlsx(p)


def test_midpoint_ranges_and_unit_suffix_only_when_enabled(tmp_path):
    p = tmp_path / "p.xlsx"
    make_xlsx(p, [("Tháng", "Giá"), ("2020-01", "458-462 USD/T,"), ("2020-02", "484 USD/T")])
    with pytest.raises(PriceImportError, match="không có dòng dữ liệu"):
        monthly_series_from_xlsx(p)  # mặc định vẫn chặt: ô chữ chỉ được coi là tiêu đề
    got = monthly_series_from_xlsx(p, midpoint_ranges=True)
    assert got == [("2020-01", D(460)), ("2020-02", D(484))]
    make_xlsx(p, [("2020-01", "438 - 441")])
    assert monthly_series_from_xlsx(p, midpoint_ranges=True) == [("2020-01", D("439.5"))]


@pytest.mark.parametrize("cell", ["462-458", "khoảng 460", "460-", "-460", "460 EUR/T"])
def test_midpoint_ranges_rejects_reversed_or_unknown_cells(tmp_path, cell):
    p = tmp_path / "p.xlsx"
    make_xlsx(p, [("2020-01", 1), ("2020-02", cell)])
    with pytest.raises(PriceImportError, match="dòng 2"):
        monthly_series_from_xlsx(p, midpoint_ranges=True)


def test_trailing_empty_rows_skipped_only_at_the_end(tmp_path):
    p = tmp_path / "p.xlsx"
    make_xlsx(p, [("2020-01", 1), ("2020-02", 2), ("2020-03", None), ("2020-04", None)])
    with pytest.raises(PriceImportError, match="thiếu ngày hoặc giá"):
        monthly_series_from_xlsx(p)
    got = monthly_series_from_xlsx(p, trailing_empty_ok=True)
    assert [m for m, _ in got] == ["2020-01", "2020-02"]
    make_xlsx(p, [("2020-01", 1), ("2020-02", None), ("2020-03", 3)])
    with pytest.raises(PriceImportError, match="dòng 2 thiếu giá"):
        monthly_series_from_xlsx(p, trailing_empty_ok=True)


def test_not_an_xlsx_and_empty_workbook(tmp_path):
    bad = tmp_path / "x.xlsx"
    bad.write_text("không phải zip", encoding="utf-8")
    with pytest.raises(PriceImportError, match="không phải file xlsx"):
        read_xlsx_rows(bad)
    empty = tmp_path / "e.xlsx"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
    with pytest.raises(PriceImportError, match="không có sheet"):
        read_xlsx_rows(empty)


def test_csv_roundtrip_through_external_store_reader(tmp_path):
    rows = [("2021-01", D("100.5")), ("2021-02", D("101"))]
    write_series_csv(rows, tmp_path / "s.csv")
    assert read_series_csv(tmp_path / "s.csv") == {(2021, 1): D("100.5"), (2021, 2): D("101")}


# ---------------- dữ liệu giá thật của nhóm


def test_team_iron_ore_csv_matches_the_xlsx_and_is_sane():
    csv_path = REPO / "data" / "external" / "iron_ore_price.csv"
    xlsx_path = REPO / "data" / "external" / "iron_ore_price.xlsx"
    data = read_series_csv(csv_path)
    assert len(data) == 120 and min(data) == (2015, 1) and max(data) == (2024, 12)
    assert all(D(30) < v < D(300) for v in data.values())  # $/dmtu, khoảng hợp lý
    from_xlsx = {tuple(map(int, m.split("-"))): v for m, v in monthly_series_from_xlsx(xlsx_path)}
    assert from_xlsx == data


@pytest.mark.parametrize(("name", "col"), [("hrc_price", "C"), ("coking_coal_price", "E")])
def test_team_vsa_csvs_match_the_xlsx(name, col):
    """Bảng VSA của nhóm (giá đầu tháng, USD/tấn): ô khoảng giá lấy trung bình hai đầu."""
    data = read_series_csv(REPO / "data" / "external" / f"{name}.csv")
    assert len(data) == 72 and min(data) == (2019, 1) and max(data) == (2024, 12)
    assert all(D(50) < v < D(1500) for v in data.values())
    rows = monthly_series_from_xlsx(
        REPO / "data" / "external" / "vsa_prices.xlsx",
        value_col=col,
        midpoint_ranges=True,
        trailing_empty_ok=True,
    )
    assert {tuple(map(int, m.split("-"))): v for m, v in rows} == data


def test_team_hrc_midpoint_spot_values():
    data = read_series_csv(REPO / "data" / "external" / "hrc_price.csv")
    assert data[(2019, 11)] == D(439)  # "438-440"
    assert data[(2021, 5)] == D(925)


def test_team_iron_ore_series_drives_real_checks():
    """Có giá thật: check chuỗi giá chạy được cho cả công ty năm tài chính lệch lịch."""
    from steel_xai.verification import ExternalStore, load_verification_settings

    settings = load_verification_settings()
    store = ExternalStore.load(settings)
    assert store.has("iron_ore_price") and not store.has("steel_price")
    inv = facts_for("HPG", "inventory_total", {2021: 100, 2022: 150})
    c = ctx(inv, company="HPG", settings=settings, external=store)
    # HPG FY2022 (kết thúc 12/2022): trung bình 2022 so với 2021, lag 0 → tính tay từ CSV
    data = read_series_csv(REPO / "data" / "external" / "iron_ore_price.csv")
    mean = lambda y: sum(data[(y, m)] for m in range(1, 13)) / 12  # noqa: E731
    expected = mean(2022) / mean(2021) - 1
    got = value_of("lagged_external_series", {"series": "iron_ore_price", "lag": 0}, c)
    assert float(got) == pytest.approx(float(expected), rel=1e-9)
    # chuỗi chưa có file vẫn NOT_EVALUABLE
    not_evaluable(
        "lagged_external_series",
        {"series": "steel_price", "lag": 0},
        c,
        "chưa có dữ liệu chuỗi giá",
    )


# ---------------- tool metric_level


def test_metric_level_keeps_the_reported_sign():
    cf = facts_for("AAA", "inventory_change_cf", {2022: "-250", 2021: 40})
    assert value_of("metric_level", {"metric": "inventory_change_cf"}, ctx(cf)) == D("-250")
    assert value_of("metric_level", {"metric": "inventory_change_cf"}, ctx(cf, year=2021)) == D(40)
    not_evaluable("metric_level", {"metric": "inventory_change_cf"}, ctx([]), "thiếu")
    assert F  # noqa: B018  (giữ import dùng chung)


# ---------------- chỉ tiêu Phase 3 mới (cấu hình) trên bảng tổng hợp


def test_new_metrics_are_declared_and_known_to_verification():
    ns = load_normalization_settings()
    cfg = load_config("normalization")["metrics"]
    for m in ("accounts_payable", "depreciation", "inventory_change_cf", "goods_in_transit"):
        assert m in cfg
        assert m in S.known_metrics
    # tên chưa trích vẫn được liệt kê để bắt lỗi gõ sai
    assert "production_volume" in S.known_metrics and ns is not None


_ROWS = [
    ("Lợi nhuận trước thuế", "01", "9.000", "8.000"),
    ("Khấu hao và phân bổ", "02", "1.500", "1.200"),
    ("Biến động hàng tồn kho", "10", "(700)", "300"),
    ("Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", "5.000", "4.000"),
]
CASH_FLOW = "\n".join(
    [
        "===== PAGE 1 =====",
        "Báo cáo tài chính hợp nhất cho năm kết thúc ngày 31 tháng 12 năm 2021",
        "",
        "===== PAGE 2 =====",
        "<table><tr><td></td><td>Mã số</td><td>Thuyết minh</td>"
        "<td>2021 VND</td><td>2020 VND</td></tr>"
        + "".join(
            f"<tr><td>{a}</td><td>{b}</td><td></td><td>{c}</td><td>{d}</td></tr>"
            for a, b, c, d in _ROWS
        )
        + "</table>",
        "",
    ]
)


def test_cash_flow_rows_are_extracted_only_from_a_cash_flow_table():
    from steel_xai.extraction import extract_document
    from steel_xai.normalization import build_context, normalize

    ns = load_normalization_settings()
    doc = parse_text(CASH_FLOW, "AAA_financial_statements_2021_consolidated")
    ctx_ = build_context(doc, ns)
    res = normalize(extract_document(doc, company="AAA"), settings=ns, contexts=[ctx_])
    by = {(f.period, f.metric): f.value for f in res.facts if f.status.value == "OK"}
    assert by.get(("FY2021", "depreciation")) == D(1500) * 10**6 or by.get(
        ("FY2021", "depreciation")
    ) in (D(1500), D(1500) * 1000, D(1500) * 10**6)
    assert ("FY2021", "inventory_change_cf") in by and by[("FY2021", "inventory_change_cf")] < 0
    # cùng nhãn nhưng KHÔNG nằm trong bảng lưu chuyển tiền tệ → không được nhận
    other = CASH_FLOW.replace("Lưu chuyển tiền thuần từ hoạt động kinh doanh", "Dòng khác")
    doc2 = parse_text(other, "AAA_financial_statements_2021_consolidated")
    res2 = normalize(
        extract_document(doc2, company="AAA"), settings=ns, contexts=[build_context(doc2, ns)]
    )
    assert not any(f.metric in ("depreciation", "inventory_change_cf") for f in res2.facts)
