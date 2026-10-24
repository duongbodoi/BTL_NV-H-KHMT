"""Test bảng ứng viên đầy đủ, hướng theo biến động của chính công ty, as-of từ báo cáo kiểm toán."""

import csv
import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from test_anomaly import F, S, _inv_only, _set, _settings, series

from steel_xai.anomaly import AsofEntry, candidate_rows, detect_anomalies
from steel_xai.anomaly.corpus import asof_from_corpus, build_candidates, find_documents, load_corpus
from steel_xai.config import ConfigError
from steel_xai.normalization import DocContext, load_normalization_settings
from steel_xai.normalization.context import audit_report_date
from steel_xai.parsing import parse_text

NS = load_normalization_settings()
FIXTURES = Path(__file__).parent / "fixtures"


def row(res, company, type_="inventory_increase"):
    return next(r for r in res.rows if (r.company, r.type) == (company, type_))


def three(a, b, c, *, a_name="AAA"):
    """Ba công ty cùng năm 2021 với tăng trưởng tồn kho a, b, c (doanh thu tăng cùng tồn kho)."""
    return _inv_only(a, a_name) + _inv_only(b, "BBB") + _inv_only(c, "CCC")


# ---------------- hướng = biến động của chính công ty


def test_own_increase_flagged_up_by_peer_rule_alone():
    # tồn kho +10% (chưa vượt luật YoY) nhưng hai peer giảm 20% → lệch peer +30%: cùng phía "up"
    res = detect_anomalies(three(0.10, -0.20, -0.20), S, [2021])
    r = row(res, "AAA")
    assert r.direction == "up" and r.exceeded and r.rules_hit == ("inv_growth_vs_peer",)
    assert r.peer_relative == "above" and not r.candidate  # chỉ do luật peer
    assert "peer_only_hit" in r.flags


def test_own_decrease_stays_down_even_when_peers_fell_more():
    # tồn kho -10%, peer -50%: lệch peer = +40% (phía up) nhưng công ty GIẢM → hướng down
    res = detect_anomalies(three(-0.10, -0.50, -0.50), S, [2021])
    r = row(res, "AAA")
    assert r.direction == "down" and not r.exceeded and r.peer_relative == "above"
    assert r.rules_hit_opposite == ("inv_growth_vs_peer",)
    assert "opposite_side_hit_only" in r.flags and not r.mixed_direction and not r.candidate
    assert not [a for a in res.anomalies if a.company == "AAA"]


def test_peer_relative_never_decides_direction():
    res = detect_anomalies(three(0.10, 0.50, 0.50), S, [2021])  # tăng 10%, thấp hơn peer 40%
    r = row(res, "AAA")
    assert r.direction == "up" and r.peer_relative == "below" and not r.exceeded


def test_unchanged_value_has_no_direction_and_is_never_flagged():
    res = detect_anomalies(three(0.0, 2.0, 2.0), S, [2021])
    r = row(res, "AAA")
    assert r.direction is None and not r.exceeded and r.deviation == 0


def test_mixed_direction_case_is_listed_flagged_and_not_a_candidate():
    res = detect_anomalies(three(0.60, 3.0, 3.0), S, [2021])
    r = row(res, "AAA")
    assert r.exceeded and r.mixed_direction and not r.candidate
    found = [a for a in res.anomalies if a.company == "AAA" and a.type == "inventory_increase"]
    assert [a.direction for a in found] == ["up"] and "mixed_direction" in found[0].flags
    table = {x["company"]: x for x in candidate_rows(res) if x["type"] == "inventory_increase"}
    assert table["AAA"]["mixed_direction"] == "true" and table["AAA"]["candidate"] == "false"


def test_peer_relative_is_a_side_attribute_on_the_anomaly():
    res = detect_anomalies(three(0.45, 0.10, 0.20), S, [2021])
    a = next(a for a in res.anomalies if a.company == "AAA" and a.type == "inventory_increase")
    assert a.direction == "up" and a.peer_relative == "above"
    assert a.to_dict()["peer_relative"] == "above"


def test_gross_margin_direction_follows_own_change():
    def gm(company, a, b):
        return series(company, {2020: (1, 1, 1, a), 2021: (1, 1, 1, b)})

    facts = gm("AAA", "0.20", "0.12") + gm("BBB", "0.10", "0.20") + gm("CCC", "0.10", "0.20")
    r = row(detect_anomalies(facts, S, [2021]), "AAA", "gross_margin_change")
    # AAA giảm 8pp (down); các peer tăng 10pp nên lệch peer = -18pp cũng phía down
    assert r.direction == "down" and r.exceeded and r.peer_relative == "below"


# ---------------- bảng đầy đủ


def test_rows_include_pairs_below_threshold_with_scores():
    res = detect_anomalies(three(0.05, 0.04, 0.06), S, [2021])
    r = row(res, "AAA")
    assert not r.exceeded and r.deviation > 0
    scores = {x.indicator: x.score for x in r.rules}
    assert scores["inv_growth_yoy"] == Decimal("0.05") / Decimal("0.30")
    assert all(x.hit is False for x in r.rules)


def test_rows_exclude_unevaluable_pairs_but_evaluations_keep_them():
    facts = series("AAA", {2020: (100, 1, 1, "0.2"), 2021: (None, 1, 1, "0.2")})
    res = detect_anomalies(facts, S, [2021])
    assert not [r for r in res.rows if r.type == "inventory_increase"]
    assert any(e.type == "inventory_increase" and not e.evaluable for e in res.evaluations)


def test_rows_sorted_by_deviation_descending_and_deterministic():
    res = detect_anomalies(three(0.10, 0.50, 0.90), S, [2021])
    dev = [r.deviation for r in res.rows]
    assert dev == sorted(dev, reverse=True)
    assert res.to_dict() == detect_anomalies(three(0.10, 0.50, 0.90), S, [2021]).to_dict()


# ---------------- cửa sổ chọn ca


def test_company_start_year_from_config_limits_the_window():
    def window(d):
        d["case_selection_window"].update(company_start_year={"HSG": 2018})

    s = _settings(dataset_mutate=window)
    years = {y: (100 + y, 1000, 800, "0.2") for y in range(2014, 2026)}
    facts = [f for c in ("HPG", "HSG", "NKG") for f in series(c, years)]
    res = detect_anomalies(facts, s)
    first = {}
    for e in res.evaluations:
        first[e.company] = min(first.get(e.company, e.period), e.period)
    assert first == {"HPG": "FY2016", "HSG": "FY2018", "NKG": "FY2016"}
    assert max(e.period for e in res.evaluations) == "FY2023"  # 2024-2025 không vào


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["case_selection_window"].update(start_year=2024),  # bắt đầu sau kết thúc
        lambda d: d["case_selection_window"].update(end_year=2024),  # chạm năm tham chiếu
        lambda d: d["case_selection_window"].update(company_start_year={"ZZZ": 2018}),
        lambda d: d["case_selection_window"].update(
            company_start_year={"NKG": 2012}
        ),  # trước cửa sổ
        lambda d: d["case_selection_window"].update(company_start_year={"NKG": 2030}),
        lambda d: d.pop("case_selection_window"),
    ],
)
def test_window_config_is_validated(mutate):
    with pytest.raises(ConfigError):
        _settings(dataset_mutate=mutate)


# ---------------- as-of


def test_extracted_asof_fills_date_and_source_and_manual_wins():
    auto = {("AAA", "FY2021"): AsofEntry("2022-01-05", "audit_report_date")}
    facts = _inv_only(0.45)
    r = row(detect_anomalies(facts, S, [2021], auto), "AAA")
    assert (r.asof_date, r.asof_source) == ("2022-01-05", "audit_report_date")
    assert "asof_date_unknown" not in r.flags
    manual = _settings(_set(("asof_dates",), {"AAA": {"FY2021": "2022-02-02"}}),
                       lambda d: d.update(companies=[]))  # fmt: skip
    r2 = row(detect_anomalies(facts, manual, [2021], auto), "AAA")
    assert (r2.asof_date, r2.asof_source) == ("2022-02-02", "manual")
    r3 = row(detect_anomalies(facts, S, [2021]), "AAA")
    assert r3.asof_date is None and "asof_date_unknown" in r3.flags


# ---------------- trích ngày ký báo cáo kiểm toán

END = dt.date(2023, 12, 31)


def doc(*pages):
    text = "".join(f"===== PAGE {i} =====\n{p}\n" for i, p in enumerate(pages, start=1))
    return parse_text(text, "d")


BS = "<table><tr><td>Mã số</td><td>TÀI SẢN</td></tr></table>"


def test_signature_after_audit_heading_not_the_management_statement():
    d = doc(
        "Báo cáo của Ban Giám đốc\n\nHà Nội, ngày 10 tháng 3 năm 2024",
        "BÁO CÁO KIỂM TOÁN ĐỘC LẬP\n\nChúng tôi đã kiểm toán.\n\nHà Nội, ngày 15 tháng 3 năm 2024",
        BS + "\nHà Nội, ngày 1 tháng 6 năm 2024",
    )
    day, evidence = audit_report_date(d, END)
    assert day == dt.date(2024, 3, 15) and evidence == "Hà Nội, ngày 15 tháng 3 năm 2024"


def test_standalone_date_line_after_place_is_accepted():
    d = doc(
        "BÁO CÁO KIỂM TOÁN ĐỘC LẬP\n\nThành phố Hồ Chí Minh, Việt Nam\n\nNgày 25 tháng 12 năm 2023",
        BS,
    )
    assert audit_report_date(d, dt.date(2023, 9, 30))[0] == dt.date(2023, 12, 25)


def test_without_heading_the_last_signature_before_the_statements_is_used():
    d = doc("Hà Nội, ngày 1 tháng 3 năm 2024\n\nHà Nội, ngày 2 tháng 3 năm 2024", BS)
    assert audit_report_date(d, END)[0] == dt.date(2024, 3, 2)


def test_dates_inside_sentences_are_not_signatures():
    d = doc("Báo cáo được phê duyệt phát hành ngày 16 tháng 3 năm 2024, được trình bày sau.", BS)
    assert audit_report_date(d, END) == (None, "")


@pytest.mark.parametrize(
    "line",
    [
        "Hà Nội, ngày 31 tháng 12 năm 2023",
        "Hà Nội, ngày 5 tháng 3 năm 2022",
        "Hà Nội, ngày 1 tháng 3 năm 2025",  # quá 400 ngày sau ngày khóa sổ
    ],
)
def test_implausible_dates_are_rejected_but_evidence_is_kept(line):
    day, evidence = audit_report_date(doc(line, BS), END)
    assert day is None and evidence == line  # bằng/trước ngày khóa sổ, hoặc quá 400 ngày sau


def test_no_report_end_means_no_audit_date():
    assert audit_report_date(doc("Hà Nội, ngày 15 tháng 3 năm 2024", BS), None) == (None, "")


def test_signatures_after_the_first_statement_table_are_ignored():
    d = doc(BS, "Hà Nội, ngày 15 tháng 3 năm 2024")
    assert audit_report_date(d, END) == (None, "")


# ---------------- bộ OCR → bảng ứng viên


def _tree(root: Path):
    def put(company, year, kind, src):
        d = root / company / str(year) / f"{company}_financial_statements_{year}_{kind}"
        d.mkdir(parents=True)
        shutil.copy(FIXTURES / src, d / f"{company}_{year}_{kind}_extracted.txt")

    put("HPG", 2022, "consolidated", "HPG_financial_statements_2022_consolidated_extracted.txt")
    put("NKG", 2023, "consolidated", "NKG_financial_statements_2023_consolidated_extracted.txt")
    put("HPG", 2022, "separate", "synthetic_ocr_document.txt")  # phải bị bỏ qua


def test_find_documents_only_takes_the_requested_kind(tmp_path):
    _tree(tmp_path)
    docs = find_documents(tmp_path, ["HPG", "NKG", "HSG"])
    assert [(d.company, d.doc_id) for d in docs] == [
        ("HPG", "HPG_financial_statements_2022_consolidated"),
        ("NKG", "NKG_financial_statements_2023_consolidated"),
    ]


def test_asof_from_corpus_extracts_audit_date_and_lists_what_it_cannot(tmp_path):
    _tree(tmp_path)
    corpus = load_corpus(find_documents(tmp_path, ["HPG", "NKG"]), NS)
    asof, rows = asof_from_corpus(corpus, 2024)
    assert asof[("HPG", "FY2022")] == AsofEntry("2023-03-16", "audit_report_date")
    assert asof[("NKG", "FY2023")] == AsofEntry("2024-03-20", "audit_report_date")
    ok = {(r.company, r.period): r for r in rows if r.status == "ok"}
    assert ok[("HPG", "FY2022")].evidence.endswith("2023") and ok[
        ("HPG", "FY2022")
    ].doc_id.startswith("HPG")
    # tài liệu không tìm được ngày thì vào danh sách tra tay, không bị đoán
    bare = DocContext("x", dt.date(2021, 12, 31), None, None, "")
    corpus2 = type(corpus)(((find_documents(tmp_path, ["HPG"])[0], bare),), ())
    asof2, rows2 = asof_from_corpus(corpus2, 2024)
    assert asof2 == {} and [r.status for r in rows2] == ["unresolved"]


def test_asof_conflict_between_two_documents_is_not_resolved(tmp_path):
    _tree(tmp_path)
    ref = find_documents(tmp_path, ["HPG"])[0]
    a = DocContext(
        "a", dt.date(2022, 12, 31), None, dt.date(2023, 3, 1), "Hà Nội, ngày 1 tháng 3 năm 2023"
    )
    b = DocContext(
        "b", dt.date(2022, 12, 31), None, dt.date(2023, 3, 9), "Hà Nội, ngày 9 tháng 3 năm 2023"
    )
    corpus = type(load_corpus([], NS))(((ref, a), (ref, b)), ())
    asof, rows = asof_from_corpus(corpus, 2024)
    assert asof == {} and {r.status for r in rows} == {"conflict"}


def test_asof_skips_reference_years(tmp_path):
    _tree(tmp_path)
    ref = find_documents(tmp_path, ["HPG"])[0]
    c = DocContext("c", dt.date(2024, 12, 31), None, dt.date(2025, 3, 1), "x")
    asof, rows = asof_from_corpus(type(load_corpus([], NS))(((ref, c),), ()), 2024)
    assert asof == {} and rows == []


def test_build_candidates_writes_both_csv_files(tmp_path):
    ocr, out = tmp_path / "ocr", tmp_path / "out"
    _tree(ocr)
    built = build_candidates(ocr, out)
    assert built.candidates_path == out / "anomaly_candidates.csv" and built.asof_path.exists()
    with built.candidates_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    hpg = [r for r in rows if r["company"] == "HPG" and r["period"] == "FY2022"]
    gm = next(r for r in hpg if r["type"] == "gross_margin_change")
    assert gm["direction"] == "down" and gm["exceeded_threshold"] == "true"
    assert gm["asof_date"] == "2023-03-16" and gm["asof_source"] == "audit_report_date"
    assert "provisional_thresholds" in gm["flags"]
    assert all(r["period"] <= "FY2023" for r in rows)  # không bao giờ có 2024
    with built.asof_path.open(encoding="utf-8-sig", newline="") as fh:
        asof = list(csv.DictReader(fh))
    assert {(r["company"], r["period"], r["asof_date"]) for r in asof if r["status"] == "ok"} == {
        ("HPG", "FY2022", "2023-03-16"),
        ("NKG", "FY2023", "2024-03-20"),
    }
    # công ty không có tài liệu (HSG) không có dòng trong bảng ứng viên vì không xét được
    assert not [r for r in rows if r["company"] == "HSG"]


def test_build_candidates_is_deterministic(tmp_path):
    _tree(tmp_path / "ocr")
    a = build_candidates(tmp_path / "ocr", tmp_path / "o1")
    b = build_candidates(tmp_path / "ocr", tmp_path / "o2")
    assert a.candidates_path.read_bytes() == b.candidates_path.read_bytes()
    assert a.asof_path.read_bytes() == b.asof_path.read_bytes()
    _ = F  # helper dùng chung với test_anomaly
