import json
import re
from pathlib import Path

import pytest

from steel_xai.contracts import Provenance
from steel_xai.parsing import Document, ParseError, TableCell, parse_file, parse_text
from steel_xai.parsing.document import PAGE_MARKER_RE

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic_ocr_document.txt"
# Fixture thật chỉ là ví dụ (spec mục 17): kỳ vọng được tính độc lập từ chính file.
HPG = FIXTURES / "HPG_financial_statements_2024_consolidated_extracted.txt"


@pytest.fixture(scope="module")
def doc() -> Document:
    return parse_file(SYNTHETIC, doc_id="synthetic")


def _texts(doc: Document, page: int) -> list[str]:
    return [b.text for b in doc.get_page(page).blocks]


# --- Trang và số trang ---------------------------------------------------------------


def test_pages_come_from_markers_not_printed_numbers(doc):
    assert [p.number for p in doc.pages] == [1, 2, 3, 4, 5, 7]
    # Số trang in "1" nằm ở trang PDF 2 và vẫn được giữ là block text của trang 2.
    assert "1" in _texts(doc, 2)
    assert doc.get_page(2).blocks[-2].text == "1"
    assert all(b.page == 2 for b in doc.get_page(2).blocks)


def test_missing_and_blank_pages(doc):
    assert doc.get_page(4).blocks == ()
    assert doc.missing_pages == (6,)


def test_ocr_text_kept_verbatim(doc):
    texts = _texts(doc, 2)
    assert "Hàng tôn kho được phản ánh theo giá gốc." in texts  # lỗi OCR "tôn" giữ nguyên
    assert "11≤1 ≤≤7 1=11" in texts  # rác OCR không bị bỏ
    assert "Mâu B 09 - DN/HN" in _texts(doc, 3)


def test_paragraphs_split_on_blank_lines(doc):
    assert _texts(doc, 2)[:2] == [
        "Công ty Cổ phần Thép Giả Định\nThuyết minh báo cáo tài chính hợp nhất",
        "1. Đơn vị báo cáo\nCông ty sản xuất thép xây dựng và ống thép.",
    ]


# --- Bảng ------------------------------------------------------------------------------


def test_single_line_html_table_with_text_on_same_line(doc):
    blocks = doc.get_page(3).blocks
    kinds = [b.kind for b in blocks]
    assert kinds == ["text", "text", "table", "text", "text"]
    assert blocks[1].text == "Bảng 1"
    assert blocks[3].text == "Ghi chú sau bảng."
    table = blocks[2]
    assert table.raw.startswith("<table>") and table.raw.endswith("</table>")
    assert table.flags == ()
    assert table.rows[0] == (TableCell("", row_span=2), TableCell("31/12/X1", col_span=2))
    assert table.rows[1] == (
        TableCell("Giá gốc VND", header=True),
        TableCell("Dự phòng VND", header=True),
    )
    # Số chưa được parse (Phase 2): giữ nguyên chuỗi, kể cả số âm trong ngoặc.
    assert [c.text for c in table.rows[2]] == ["Nguyên vật liệu", "1.234.567", "(12.000)"]
    # Entity được giải mã, ký hiệu LaTeX từ OCR giữ nguyên.
    assert table.rows[3][0].text == "Thành phẩm \\( (a) \\) & khác"


def test_table_text_representation(doc):
    table = next(doc.iter_blocks("table"))
    assert table.text.splitlines()[2] == "Nguyên vật liệu | 1.234.567 | (12.000)"


def test_multiline_table(doc):
    blocks = doc.get_page(5).blocks
    assert [b.kind for b in blocks] == ["text", "table", "text", "text"]
    table = blocks[1]
    assert table.flags == ()
    assert table.line_end - table.line_start == 3
    assert [c.text for c in table.rows[1]] == ["Chi phí sản xuất dở dang", "98.765"]


def test_unclosed_table_is_flagged_not_dropped(doc):
    blocks = doc.get_page(7).blocks
    assert [b.kind for b in blocks] == ["text", "table"]
    table = blocks[1]
    assert "unclosed_table" in table.flags
    assert [c.text for c in table.rows[0]] == ["Dòng dở", "12"]


@pytest.mark.parametrize(
    ("html", "flag"),
    [
        ("<table></table>", "empty_table"),
        ('<table><tr><td rowspan="x">a</td></tr></table>', "invalid_span"),
        ('<table><tr><td colspan="0">a</td></tr></table>', "invalid_span"),
        ("<table>lạc<tr><td>a</td></tr></table>", "text_outside_cell"),
        ("<table><tr><td><table><tr><td>b</td></tr></table></td></tr></table>", "nested_table"),
    ],
)
def test_malformed_tables_flagged(html, flag):
    d = parse_text(f"===== PAGE 1 =====\n{html}\n", "x")
    (block,) = d.get_page(1).blocks
    assert block.kind == "table"
    assert flag in block.flags
    assert block.raw == html


def test_stray_closing_tag_flagged_in_text():
    d = parse_text("===== PAGE 1 =====\nvăn bản </table> lạc\n", "x")
    (block,) = d.get_page(1).blocks
    assert block.kind == "text"
    assert block.flags == ("stray_table_tag",)


@pytest.mark.parametrize(
    "html",
    [
        "<table><tr><td>a<!-- </table> --></td><td>b</td></tr></table>",
        "<table><tr><td>a</td><td>b<!-- <table> --></td></tr></table>",
    ],
)
def test_comment_does_not_split_table(html):
    d = parse_text(f"===== PAGE 1 =====\n{html}\n", "x")
    (block,) = d.get_page(1).blocks
    assert block.kind == "table" and block.raw == html
    assert [c.text for c in block.rows[0]] == ["a", "b"]
    assert block.flags == ("unexpected_html",)


def test_block_tags_in_cell_do_not_glue_numbers():
    html = "<table><tr><td><p>1.000</p><p>2.000</p></td><td><b>x</b></td></tr></table>"
    d = parse_text(f"===== PAGE 1 =====\n{html}\n", "x")
    (block,) = d.get_page(1).blocks
    assert block.rows[0][0].text.split() == ["1.000", "2.000"]
    assert block.rows[0][1].text == "x"
    assert block.flags == ()


def test_unknown_tag_in_table_flagged():
    d = parse_text("===== PAGE 1 =====\n<table><tr><td><img src=a>a</td></tr></table>\n", "x")
    assert d.get_page(1).blocks[0].flags == ("unexpected_html",)


def test_cell_without_closing_tags():
    d = parse_text("===== PAGE 1 =====\n<table><tr><td>a<td>b<tr><td>c</table>\n", "x")
    (block,) = d.get_page(1).blocks
    assert [[c.text for c in r] for r in block.rows] == [["a", "b"], ["c"]]


# --- Provenance, id, line number ------------------------------------------------------


def test_block_ids_stable_and_traceable(doc):
    again = parse_file(SYNTHETIC, doc_id="synthetic")
    assert again.to_dict() == doc.to_dict()
    ids = [b.block_id for b in doc.iter_blocks()]
    assert len(ids) == len(set(ids))
    for b in doc.iter_blocks():
        assert b.block_id == f"synthetic:p{b.page:04d}:b{b.index:03d}"
        assert doc.get_block(b.block_id) is b
        assert b in doc.get_page(b.page).blocks


def test_provenance(doc):
    table = next(doc.iter_blocks("table"))
    prov = table.provenance()
    assert prov == Provenance("synthetic", 3, table.block_id, None, table.text)
    assert Provenance.from_dict(json.loads(json.dumps(prov.to_dict()))) == prov


def _source_lines(path: Path) -> list[str]:
    # Parser chỉ tách dòng theo "\n" (sau khi chuẩn hóa CRLF), test cũng vậy.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")


def test_line_numbers_point_into_source(doc):
    lines = _source_lines(SYNTHETIC)
    for b in doc.iter_blocks():
        src = "\n".join(lines[b.line_start - 1 : b.line_end])
        assert b.raw in src
        # Trang của block = marker gần nhất phía trên dòng đầu của block.
        marker = next(
            PAGE_MARKER_RE.match(lines[i])
            for i in range(b.line_start - 1, -1, -1)
            if PAGE_MARKER_RE.match(lines[i])
        )
        assert int(marker.group(1)) == b.page


def _assert_lossless(doc: Document, path: Path) -> None:
    """Mọi ký tự không trắng của từng trang nằm trong đúng một block, đúng thứ tự."""
    lines = _source_lines(path)
    for p in doc.pages:
        end = next((q.marker_line for q in doc.pages if q.number > p.number), len(lines) + 1)
        body = "".join(lines[p.marker_line : end - 1])
        joined = "".join(b.raw for b in p.blocks)
        assert re.sub(r"\s", "", joined) == re.sub(r"\s", "", body), p.number
        spans = [(b.line_start, b.line_end) for b in p.blocks]
        # Block liên tiếp không chồng nhau (chỉ được chung một dòng biên khi cùng dòng).
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False)), p.number


def test_every_content_char_covered_once(doc):
    _assert_lossless(doc, SYNTHETIC)


def test_json_roundtrip(doc):
    restored = Document.from_dict(json.loads(json.dumps(doc.to_dict(), ensure_ascii=False)))
    assert restored == doc
    assert restored.get_block(doc.pages[2].blocks[2].block_id).rows == doc.pages[2].blocks[2].rows


@pytest.mark.parametrize(
    ("field", "value"),
    [("page", 9), ("doc_id", "other"), ("block_id", "synthetic:p0003:b099"), ("index", 7)],
)
def test_from_dict_rejects_inconsistent_provenance(doc, field, value):
    d = json.loads(json.dumps(doc.to_dict()))
    d["pages"][2]["blocks"][0][field] = value
    with pytest.raises(ParseError, match="không khớp"):
        Document.from_dict(d)


def test_get_missing_raises(doc):
    with pytest.raises(KeyError):
        doc.get_block("synthetic:p0006:b001")
    with pytest.raises(KeyError):
        doc.get_page(6)


# --- Biến thể đầu vào ------------------------------------------------------------------


def test_crlf_and_bom_normalized_to_same_content(tmp_path, doc):
    text = SYNTHETIC.read_text(encoding="utf-8")
    p = tmp_path / "x.txt"
    p.write_bytes(("﻿" + text.replace("\n", "\r\n")).encode("utf-8"))
    assert parse_file(p, doc_id="synthetic").to_dict() == {**doc.to_dict(), "source_name": "x.txt"}


def test_doc_id_affects_ids_but_not_hash(doc):
    other = parse_file(SYNTHETIC, doc_id="other")
    assert other.content_sha256 == doc.content_sha256
    assert next(other.iter_blocks()).block_id.startswith("other:p0001:")


def test_default_doc_id_from_filename(tmp_path):
    p = tmp_path / "Báo cáo 2023.txt"
    p.write_text("===== PAGE 1 =====\nabc\n", encoding="utf-8")
    d = parse_file(p)
    assert re.fullmatch(r"[A-Za-z0-9._-]+", d.doc_id)
    assert d.source_name == "Báo cáo 2023.txt"


@pytest.mark.parametrize(
    "marker",
    ["=== PAGE 3 ===", "  ===== page 3 =====  ", "=====PAGE 3=====", "\f===== PAGE 3 =====\v"],
)
def test_marker_variants(marker):
    d = parse_text(f"{marker}\nabc\n", "x")
    assert [p.number for p in d.pages] == [3]
    assert d.missing_pages == (1, 2)


@pytest.mark.parametrize(
    "bad",
    [
        "===== PAGE 2 ===== (tiếp)",
        "===== PAGE 2 / 80 =====",
        "== PAGE2",
        "PAGE 2 =====",
        "===== PAGE ２ =====",  # chữ số full-width
    ],
)
def test_near_miss_marker_raises_instead_of_merging_pages(bad):
    with pytest.raises(ParseError, match="giống marker"):
        parse_text(f"===== PAGE 1 =====\na\n{bad}\nb\n", "x")


def test_ordinary_text_with_page_word_is_not_marker():
    d = parse_text("===== PAGE 1 =====\nXem PAGE 2 của phụ lục; a = b\n", "x")
    assert len(d.pages) == 1


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("không có marker\n", "marker"),
        ("lời mở đầu\n===== PAGE 1 =====\nabc\n", "trước marker"),
        ("===== PAGE 2 =====\na\n===== PAGE 2 =====\nb\n", "trùng"),
        ("===== PAGE 3 =====\na\n===== PAGE 1 =====\nb\n", "trùng hoặc nhỏ hơn"),
        ("===== PAGE 0 =====\na\n", ">= 1"),
    ],
)
def test_ambiguous_pages_raise(text, match):
    with pytest.raises(ParseError, match=match):
        parse_text(text, "x")


@pytest.mark.parametrize("doc_id", ["", "a:b", "-x", "a b"])
def test_invalid_doc_id(doc_id):
    with pytest.raises(ParseError, match="doc_id"):
        parse_text("===== PAGE 1 =====\na\n", doc_id)


def test_non_utf8_file_raises(tmp_path):
    p = tmp_path / "x.txt"
    p.write_bytes("===== PAGE 1 =====\nHàng tồn kho\n".encode("latin-1", errors="replace"))
    with pytest.raises(ParseError, match="UTF-8"):
        parse_file(p)


# --- Fixture thật (ví dụ, không phải căn cứ viết parser) -------------------------------


@pytest.mark.skipif(not HPG.is_file(), reason="thiếu fixture ví dụ")
def test_real_fixture_parses_robustly():
    doc = parse_file(HPG)
    lines = _source_lines(HPG)
    _assert_lossless(doc, HPG)
    markers = [int(m.group(1)) for line in lines if (m := PAGE_MARKER_RE.match(line))]
    assert [p.number for p in doc.pages] == markers

    tables = list(doc.iter_blocks("table"))
    assert len(tables) == sum(line.count("<table") for line in lines)
    assert all(t.flags == () and t.rows for t in tables)
    assert not any(b.flags for b in doc.iter_blocks("text"))

    for b in doc.iter_blocks():
        assert b.raw in "\n".join(lines[b.line_start - 1 : b.line_end])
        marker_line = max(p.marker_line for p in doc.pages if p.marker_line < b.line_start)
        assert doc.get_page(b.page).marker_line == marker_line

    # Số trang in lệch số trang PDF: thuyết minh hàng tồn kho in số "39" nhưng nằm ở trang PDF 40.
    (heading,) = [b for b in doc.iter_blocks("text") if b.text == "10. Hàng tồn kho"]
    assert heading.page == 40
    assert heading.provenance().page == 40
    assert doc.get_page(40).blocks[-1].text == "39"
    assert Document.from_dict(json.loads(json.dumps(doc.to_dict()))) == doc
