"""OCR text → Document / Page / Block(text|table), giữ provenance (spec Phase 1, mục 16).

Định dạng đầu vào: văn bản trích từ PDF, mỗi trang mở đầu bằng một dòng marker
`===== PAGE N =====`, trong đó N là **số trang PDF** (1-based). Số trang in trên tài liệu
(thường lệch so với số trang PDF, hoặc không có) không bao giờ được dùng làm `page`.

Nguyên tắc:
- Giữ nguyên văn: không sửa lỗi OCR, không bỏ dòng nào (kể cả header/footer, rác OCR).
  Mọi dòng không trống nằm trong đúng một block.
- Bảng là đoạn `<table>...</table>` (một hay nhiều dòng, có thể dính văn bản cùng dòng);
  chỉ tách cấu trúc ô, chưa parse số (Phase 2).
- Không biết > đoán sai: marker trang thiếu/trùng/giảm dần hoặc có nội dung trước marker đầu
  → `ParseError`; bảng hỏng → giữ nguyên văn và gắn `flags`, không bỏ qua im lặng.
- Id deterministic: `<doc_id>:p<page>:b<index>`, chỉ phụ thuộc nội dung đầu vào.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

from steel_xai.contracts import Provenance

PAGE_MARKER_RE = re.compile(r"^\s*=+\s*PAGE\s+([0-9]+)\s*=+\s*$", re.IGNORECASE)
# Dòng trông giống marker nhưng không khớp dạng chuẩn → không đoán, báo lỗi.
_MARKER_LIKE_RE = re.compile(r"=\s*PAGE\s*\d|PAGE\s*\d+\s*(?:/\s*\d+\s*)?=", re.IGNORECASE)
_TABLE_TAG_RE = re.compile(r"<(/?)table\b[^>]*>", re.IGNORECASE)
# Vùng HTML không mang nội dung bảng; che đi trước khi dò thẻ <table>.
_HTML_OPAQUE_RE = re.compile(
    r"<!--.*?(?:-->|\Z)|<(script|style)\b.*?(?:</\1\s*>|\Z)", re.IGNORECASE | re.DOTALL
)
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

BlockKind = Literal["text", "table"]

# Cờ gắn vào block khi cấu trúc không như mong đợi.
FLAG_UNCLOSED_TABLE = "unclosed_table"
FLAG_NESTED_TABLE = "nested_table"
FLAG_EMPTY_TABLE = "empty_table"
FLAG_INVALID_SPAN = "invalid_span"
FLAG_TEXT_OUTSIDE_CELL = "text_outside_cell"
FLAG_STRAY_TABLE_TAG = "stray_table_tag"
FLAG_UNEXPECTED_HTML = "unexpected_html"  # comment, script/style hoặc thẻ lạ trong bảng

# Thẻ trong bảng: thẻ khối tách nội dung bằng khoảng trắng; thẻ trung tính bỏ qua; thẻ khác gắn cờ.
_BLOCK_TAGS = frozenset({"br", "p", "div", "li", "ul", "ol"})
_NEUTRAL_TAGS = frozenset(
    {
        *("thead", "tbody", "tfoot", "colgroup", "col"),
        *("b", "i", "u", "em", "strong", "span", "sup", "sub"),
    }
)


class ParseError(Exception):
    """Đầu vào không đủ thông tin để gán trang một cách chắc chắn."""


@dataclass(frozen=True)
class TableCell:
    text: str
    row_span: int = 1
    col_span: int = 1
    header: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "header": self.header,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TableCell:
        return cls(d["text"], d.get("row_span", 1), d.get("col_span", 1), d.get("header", False))


@dataclass(frozen=True)
class Block:
    block_id: str
    doc_id: str
    page: int
    index: int
    kind: BlockKind
    raw: str
    line_start: int
    line_end: int
    rows: tuple[tuple[TableCell, ...], ...] | None = None
    flags: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Văn bản hiển thị/trích dẫn: nguyên văn với text, các ô nối bằng ` | ` với table."""
        if self.kind == "text" or not self.rows:
            return self.raw
        return "\n".join(" | ".join(c.text for c in row) for row in self.rows)

    def provenance(self) -> Provenance:
        return Provenance(
            doc_id=self.doc_id, page=self.page, block_id=self.block_id, source_text=self.text
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "doc_id": self.doc_id,
            "page": self.page,
            "index": self.index,
            "kind": self.kind,
            "raw": self.raw,
            "text": self.text,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "rows": None
            if self.rows is None
            else [[c.to_dict() for c in row] for row in self.rows],
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Block:
        rows = d.get("rows")
        return cls(
            block_id=d["block_id"],
            doc_id=d["doc_id"],
            page=d["page"],
            index=d["index"],
            kind=d["kind"],
            raw=d["raw"],
            line_start=d["line_start"],
            line_end=d["line_end"],
            rows=None
            if rows is None
            else tuple(tuple(TableCell.from_dict(c) for c in row) for row in rows),
            flags=tuple(d.get("flags", ())),
        )


@dataclass(frozen=True)
class Page:
    number: int
    marker_line: int
    blocks: tuple[Block, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "marker_line": self.marker_line,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Page:
        return cls(d["number"], d["marker_line"], tuple(Block.from_dict(b) for b in d["blocks"]))


@dataclass(frozen=True)
class Document:
    doc_id: str
    # sha256 của văn bản sau khi bỏ BOM và chuẩn hóa xuống dòng, không phải của byte file.
    content_sha256: str
    pages: tuple[Page, ...]
    source_name: str | None = None
    _index: dict[str, Block] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Kiểm cả khi dựng từ JSON: provenance sai trang thì không được nạp im lặng.
        for p in self.pages:
            for b in p.blocks:
                expected = f"{self.doc_id}:p{p.number:04d}:b{b.index:03d}"
                if b.doc_id != self.doc_id or b.page != p.number or b.block_id != expected:
                    raise ParseError(
                        f"Block {b.block_id!r} không khớp tài liệu {self.doc_id!r} trang {p.number}"
                    )
                if b.block_id in self._index:
                    raise ParseError(f"block_id trùng: {b.block_id}")
                self._index[b.block_id] = b

    def iter_blocks(self, kind: BlockKind | None = None) -> Iterator[Block]:
        for p in self.pages:
            for b in p.blocks:
                if kind is None or b.kind == kind:
                    yield b

    def get_block(self, block_id: str) -> Block:
        try:
            return self._index[block_id]
        except KeyError:
            raise KeyError(f"Không có block {block_id!r} trong {self.doc_id}") from None

    def get_page(self, number: int) -> Page:
        for p in self.pages:
            if p.number == number:
                return p
        raise KeyError(f"Không có trang PDF {number} trong {self.doc_id}")

    @property
    def missing_pages(self) -> tuple[int, ...]:
        """Số trang PDF trong khoảng 1..trang cuối mà đầu vào không có marker."""
        present = {p.number for p in self.pages}
        last = max(present, default=0)
        return tuple(n for n in range(1, last + 1) if n not in present)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "content_sha256": self.content_sha256,
            "source_name": self.source_name,
            "pages": [p.to_dict() for p in self.pages],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Document:
        return cls(
            doc_id=d["doc_id"],
            content_sha256=d["content_sha256"],
            pages=tuple(Page.from_dict(p) for p in d["pages"]),
            source_name=d.get("source_name"),
        )


# ---------------------------------------------------------------------------
# Parse


def parse_text(text: str, doc_id: str, source_name: str | None = None) -> Document:
    """Parse văn bản OCR có marker trang thành `Document`."""
    if not _DOC_ID_RE.match(doc_id):
        raise ParseError(f"doc_id không hợp lệ: {doc_id!r}")
    text = _normalize_newlines(text)
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    lines = text.split("\n")

    markers = []
    for i, line in enumerate(lines):
        if m := PAGE_MARKER_RE.match(line):
            markers.append((i, m))
        elif _MARKER_LIKE_RE.search(line):
            raise ParseError(
                f"{doc_id}: dòng {i + 1} giống marker trang nhưng sai định dạng: {line.strip()!r}"
            )
    if not markers:
        raise ParseError(f"{doc_id}: không tìm thấy marker trang dạng '===== PAGE N ====='")
    first = markers[0][0]
    if any(line.strip() for line in lines[:first]):
        raise ParseError(
            f"{doc_id}: có nội dung trước marker trang đầu tiên (dòng 1..{first}),"
            " không xác định được trang"
        )

    pages: list[Page] = []
    prev = 0
    for k, (i, m) in enumerate(markers):
        number = int(m.group(1))
        if number < 1:
            raise ParseError(f"{doc_id}: số trang PDF phải >= 1 (dòng {i + 1})")
        if number <= prev:
            raise ParseError(
                f"{doc_id}: marker trang {number} ở dòng {i + 1} trùng hoặc nhỏ hơn trang trước"
                f" ({prev})"
            )
        prev = number
        end = markers[k + 1][0] if k + 1 < len(markers) else len(lines)
        body_lines = lines[i + 1 : end]
        blocks = _parse_page_body("\n".join(body_lines), i + 2, doc_id, number)
        pages.append(Page(number=number, marker_line=i + 1, blocks=tuple(blocks)))

    return Document(
        doc_id=doc_id, content_sha256=content_sha, pages=tuple(pages), source_name=source_name
    )


def parse_file(path: str | Path, doc_id: str | None = None) -> Document:
    """Đọc file UTF-8 (có/không BOM). `doc_id` mặc định lấy từ tên file."""
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ParseError(f"{path.name}: không phải UTF-8 hợp lệ ({e})") from e
    if doc_id is None:
        doc_id = re.sub(r"[^A-Za-z0-9._-]", "_", path.stem).lstrip("._-") or "doc"
    return parse_text(text, doc_id, source_name=path.name)


def _normalize_newlines(text: str) -> str:
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _parse_page_body(body: str, first_line: int, doc_id: str, page: int) -> list[Block]:
    """Tách thân trang thành block; `first_line` là số dòng (1-based) của ký tự đầu `body`."""
    spans: list[tuple[int, int, BlockKind, tuple[str, ...]]] = []
    pos = 0
    for start, end, flags in _table_spans(body):
        spans.extend((s, e, "text", ()) for s, e in _paragraph_spans(body, pos, start))
        spans.append((start, end, "table", flags))
        pos = end
    spans.extend((s, e, "text", ()) for s, e in _paragraph_spans(body, pos, len(body)))

    blocks: list[Block] = []
    for idx, (s, e, kind, flags) in enumerate(spans, start=1):
        raw = body[s:e]
        rows: tuple[tuple[TableCell, ...], ...] | None = None
        if kind == "table":
            rows, cell_flags = _parse_table_html(raw)
            flags = flags + cell_flags
        elif "</table" in raw.lower():
            flags = (FLAG_STRAY_TABLE_TAG,)
        blocks.append(
            Block(
                block_id=f"{doc_id}:p{page:04d}:b{idx:03d}",
                doc_id=doc_id,
                page=page,
                index=idx,
                kind=kind,
                raw=raw,
                line_start=first_line + body.count("\n", 0, s),
                line_end=first_line + body.count("\n", 0, e),
                rows=rows,
                flags=tuple(sorted(set(flags))),
            )
        )
    return blocks


def _table_spans(body: str) -> Iterator[tuple[int, int, tuple[str, ...]]]:
    """Vị trí các bảng cấp ngoài cùng; bảng không đóng kéo tới hết trang."""
    # Che comment/script/style (giữ nguyên độ dài và dòng) để thẻ bên trong không cắt bảng.
    masked = _HTML_OPAQUE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), body)
    depth = 0
    start = 0
    nested = False
    for m in _TABLE_TAG_RE.finditer(masked):
        if m.group(1) == "":
            if depth == 0:
                start, nested = m.start(), False
            else:
                nested = True
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                yield start, m.end(), (FLAG_NESTED_TABLE,) if nested else ()
        # Thẻ đóng lẻ (depth == 0) để lại trong văn bản và bị gắn cờ ở block text.
    if depth > 0:
        flags = (FLAG_UNCLOSED_TABLE, FLAG_NESTED_TABLE) if nested else (FLAG_UNCLOSED_TABLE,)
        yield start, len(body.rstrip()), flags


def _paragraph_spans(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    """Các đoạn liên tiếp dòng không trống trong text[start:end], bỏ khoảng trắng hai đầu."""
    para_start: int | None = None
    para_end = start
    pos = start
    while pos <= end:
        nl = text.find("\n", pos, end)
        line_end = end if nl == -1 else nl
        line = text[pos:line_end]
        if line.strip():
            if para_start is None:
                para_start = pos + len(line) - len(line.lstrip())
            para_end = pos + len(line.rstrip())
        elif para_start is not None:
            yield para_start, para_end
            para_start = None
        if nl == -1:
            break
        pos = nl + 1
    if para_start is not None:
        yield para_start, para_end


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[TableCell]] = []
        self.flags: set[str] = set()
        self._depth = 0
        self._row: list[TableCell] | None = None
        self._cell: list[str] | None = None
        self._cell_attrs: tuple[int, int, bool] = (1, 1, False)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._depth += 1
        elif self._depth != 1:
            return
        elif tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is None:
                self._row = []
            a = dict(attrs)
            self._cell = []
            self._cell_attrs = (
                self._span(a.get("rowspan")),
                self._span(a.get("colspan")),
                tag == "th",
            )
        else:
            self._other_tag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._depth == 1:
                self._close_row()
            self._depth -= 1
        elif self._depth != 1:
            return
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        else:
            self._other_tag(tag)

    def handle_comment(self, data: str) -> None:
        self.flags.add(FLAG_UNEXPECTED_HTML)

    def _other_tag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            # Thẻ khối ngăn cách nội dung: không để "1.000" và "2.000" dính thành một số.
            if self._cell is not None:
                self._cell.append(" ")
        elif tag not in _NEUTRAL_TAGS:
            self.flags.add(FLAG_UNEXPECTED_HTML)

    def handle_data(self, data: str) -> None:
        if self._depth > 1:
            return  # bảng lồng: đã gắn cờ nested_table, nội dung còn trong raw
        if self._cell is not None:
            self._cell.append(data)
        elif data.strip():
            self.flags.add(FLAG_TEXT_OUTSIDE_CELL)

    def close(self) -> None:
        super().close()
        self._close_row()

    def _span(self, value: str | None) -> int:
        if value is None:
            return 1
        try:
            n = int(value.strip())
        except ValueError:
            n = 0
        if n < 1:
            self.flags.add(FLAG_INVALID_SPAN)
            return 1
        return n

    def _close_cell(self) -> None:
        if self._cell is None:
            return
        row_span, col_span, header = self._cell_attrs
        text = "".join(self._cell).strip()
        assert self._row is not None
        self._row.append(TableCell(text, row_span, col_span, header))
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _parse_table_html(raw: str) -> tuple[tuple[tuple[TableCell, ...], ...], tuple[str, ...]]:
    p = _TableHTMLParser()
    p.feed(raw)
    p.close()
    if not p.rows:
        p.flags.add(FLAG_EMPTY_TABLE)
    return tuple(tuple(r) for r in p.rows), tuple(sorted(p.flags))
