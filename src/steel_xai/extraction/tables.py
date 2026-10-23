"""Block bảng (Phase 1) → giá trị số thô theo dòng/cột/kỳ (spec Phase 2).

Không phụ thuộc công ty, số trang hay số dòng cố định; mọi suy luận dựa vào nội dung ô:
- trải `rowspan`/`colspan` thành lưới;
- header = các dòng đầu bảng giống header (chữ, hoặc kỳ; năm/ngày trần chỉ được tính là
  header khi dòng không có nhãn), cộng dòng đánh số cột "1 | 2 | 3" nếu có;
- dòng giống header giữa thân bảng (bảng bị OCR gộp, header lặp theo kỳ) mở header mới;
- vai trò cột: mã số/thuyết minh/STT khi ô header khớp trọn từ khóa trong config; cột nhãn là
  các cột đầu không chứa số liệu; cột còn lại sinh giá trị;
- kỳ và đơn vị đọc từ header (đơn vị có thể lấy từ dòng "Đơn vị tính" cùng trang, trước bảng).

Mỗi ô của cột giá trị sinh đúng một `RawTableValue`; ô không parse được là `UNRESOLVED`,
dấu gạch không bao giờ thành 0. Mơ hồ (không có header, kỳ/đơn vị mâu thuẫn) → gắn cờ, không đoán.
Phase 2 chưa map nhãn về metric chuẩn và chưa quy đổi đơn vị (Phase 3).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from steel_xai.contracts import Provenance, ValueStatus
from steel_xai.extraction.numbers import (
    REASON_BAD_FORMAT,
    REASON_DASH,
    REASON_MULTIPLE_NUMBERS,
    REASON_NO_DIGITS,
    ParsedNumber,
    parse_number,
)
from steel_xai.extraction.settings import TableSettings, load_table_settings, norm_key
from steel_xai.parsing.document import Block, Document, TableCell

# Vai trò cột.
ROLE_LABEL = "label"
ROLE_VALUE = "value"
ROLE_TEXT = "text"  # cột chữ thuần (không chữ số) nằm sau cột nhãn, không sinh giá trị số

# Cờ cấp bảng.
FLAG_NO_HEADER = "no_header"
FLAG_NO_DATA_ROWS = "no_data_rows"
FLAG_NO_LABEL_COLUMN = "no_label_column"
FLAG_RAGGED_ROWS = "ragged_rows"
FLAG_ROWSPAN_OVERFLOW = "rowspan_overflow"
FLAG_BLOCK_FLAGGED = "block_flagged"  # block bảng có cờ từ Phase 1
FLAG_SUBHEADER_IN_BODY = "subheader_in_body"
FLAG_COLUMN_NUMBERING = "column_numbering_row"  # dòng "1 | 2 | 3 | 4" sau header
# Cờ cấp giá trị.
FLAG_PERIOD_MISSING = "period_missing"
FLAG_PERIOD_AMBIGUOUS = "period_ambiguous"
FLAG_PERIOD_INVALID = "period_invalid"
FLAG_PERIOD_INCOMPLETE = "period_incomplete"  # "Quý 3" không có năm
FLAG_PERIOD_RELATIVE = "period_relative"  # "Năm trước"/"Số đầu năm" không ghi kỳ cụ thể
FLAG_PERIOD_CUMULATIVE = "period_cumulative"  # số lũy kế, không phải số của một kỳ
FLAG_PERIOD_FROM_CORNER = "period_from_corner"  # kỳ chỉ lấy từ header của cột nhãn
FLAG_UNIT_MISSING = "unit_missing"
FLAG_UNIT_CONFLICT = "unit_conflict"
FLAG_UNIT_UNRECOGNIZED = "unit_unrecognized"
FLAG_RESTATED = "restated"
FLAG_EMPTY_ROW_LABEL = "empty_row_label"
FLAG_SPANS_ROWS = "cell_spans_rows"
FLAG_SPANS_COLUMNS = "cell_spans_columns"
FLAG_AFTER_SUBHEADER = "after_subheader"  # header của giá trị lấy từ dòng header giữa bảng
FLAG_LABEL_FROM_ROWSPAN = "label_from_rowspan"  # nhãn dòng mượn từ ô rowspan của dòng trên
UNIT_PERCENT = "percent"

_DATE_SLASH_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
_DATE_WORDS_RE = re.compile(r"ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})(?!\d)")
_QUARTER_RE = re.compile(r"(?<!\w)(?:quý|q)\s*(iv|iii|ii|i|[1-4])\s*(?:/|-|năm)?\s*(\d{4})(?!\d)")
_QUARTER_NOYEAR_RE = re.compile(r"(?<!\w)(?:quý|q)\s*(iv|iii|ii|i|[1-4])(?!\w)")
_YEAR_RE = re.compile(r"(?<![\d/.,])((?:19|20)\d{2})(?![\d/.,])")
_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
# Ngày thiếu năm ở cuối ô header hai tầng; năm nằm ở ô dưới.
_DANGLING_DATE_RE = re.compile(r"\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s*$")
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)")
_INHERITED_TABLE_FLAGS = frozenset({"no_header", "block_flagged", "ragged_rows"})


# ---------------------------------------------------------------------------
# Kết quả


@dataclass(frozen=True)
class Period:
    """Kỳ đọc được từ header; `text` là nguyên văn các ô header đã dùng."""

    text: str
    date: str | None = None  # ISO yyyy-mm-dd (ngày khóa sổ hoặc ngày kết thúc kỳ)
    year: int | None = None
    quarter: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "date": self.date, "year": self.year, "quarter": self.quarter}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Period:
        return cls(d["text"], d.get("date"), d.get("year"), d.get("quarter"))


@dataclass(frozen=True)
class RawTableValue:
    value_id: str
    doc_id: str
    page: int
    block_id: str
    row_index: int
    col_index: int
    company: str | None
    row_label: str
    row_code: str | None
    note_ref: str | None
    column_header: tuple[str, ...]
    period: Period | None
    unit: str | None
    raw: str
    value: Decimal | None
    status: ValueStatus
    is_percent: bool = False
    flags: tuple[str, ...] = ()
    row_seq: str | None = None  # số thứ tự dòng (cột STT), nguyên văn

    def provenance(self) -> Provenance:
        return Provenance(
            doc_id=self.doc_id, page=self.page, block_id=self.block_id, source_text=self.raw
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_id": self.value_id,
            "doc_id": self.doc_id,
            "page": self.page,
            "block_id": self.block_id,
            "row_index": self.row_index,
            "col_index": self.col_index,
            "company": self.company,
            "row_label": self.row_label,
            "row_code": self.row_code,
            "note_ref": self.note_ref,
            "row_seq": self.row_seq,
            "column_header": list(self.column_header),
            "period": None if self.period is None else self.period.to_dict(),
            "unit": self.unit,
            "raw": self.raw,
            "value": None if self.value is None else str(self.value),
            "status": self.status.value,
            "is_percent": self.is_percent,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RawTableValue:
        return cls(
            value_id=d["value_id"],
            doc_id=d["doc_id"],
            page=d["page"],
            block_id=d["block_id"],
            row_index=d["row_index"],
            col_index=d["col_index"],
            company=d.get("company"),
            row_label=d["row_label"],
            row_code=d.get("row_code"),
            note_ref=d.get("note_ref"),
            row_seq=d.get("row_seq"),
            column_header=tuple(d["column_header"]),
            period=None if d.get("period") is None else Period.from_dict(d["period"]),
            unit=d.get("unit"),
            raw=d["raw"],
            value=None if d.get("value") is None else Decimal(d["value"]),
            status=ValueStatus(d["status"]),
            is_percent=d.get("is_percent", False),
            flags=tuple(d.get("flags", ())),
        )


@dataclass(frozen=True)
class TableExtraction:
    doc_id: str
    page: int
    block_id: str
    n_rows: int
    n_cols: int
    header_rows: int
    column_roles: tuple[str, ...]
    column_headers: tuple[tuple[str, ...], ...]
    values: tuple[RawTableValue, ...]
    flags: tuple[str, ...] = ()
    subheader_rows: tuple[int, ...] = ()  # dòng header giữa thân bảng (không sinh giá trị)

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in self.values:
            counts[v.status.value] = counts.get(v.status.value, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "page": self.page,
            "block_id": self.block_id,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "header_rows": self.header_rows,
            "column_roles": list(self.column_roles),
            "column_headers": [list(h) for h in self.column_headers],
            "values": [v.to_dict() for v in self.values],
            "flags": list(self.flags),
            "subheader_rows": list(self.subheader_rows),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TableExtraction:
        return cls(
            doc_id=d["doc_id"],
            page=d["page"],
            block_id=d["block_id"],
            n_rows=d["n_rows"],
            n_cols=d["n_cols"],
            header_rows=d["header_rows"],
            column_roles=tuple(d["column_roles"]),
            column_headers=tuple(tuple(h) for h in d["column_headers"]),
            values=tuple(RawTableValue.from_dict(v) for v in d["values"]),
            flags=tuple(d.get("flags", ())),
            subheader_rows=tuple(d.get("subheader_rows", ())),
        )


# ---------------------------------------------------------------------------
# API


def extract_document(
    doc: Document, settings: TableSettings | None = None, company: str | None = None
) -> list[TableExtraction]:
    """Trích mọi block bảng của `doc`. `company` do người gọi cung cấp, không suy từ nội dung."""
    settings = load_table_settings() if settings is None else settings
    out: list[TableExtraction] = []
    for page in doc.pages:
        for i, block in enumerate(page.blocks):
            if block.kind == "table":
                page_unit = _page_unit_statement(page.blocks[:i], settings)
                out.append(extract_table(block, settings, company=company, page_unit=page_unit))
    return out


def iter_values(tables: Sequence[TableExtraction]) -> Iterator[RawTableValue]:
    for t in tables:
        yield from t.values


def extract_table(
    block: Block,
    settings: TableSettings | None = None,
    *,
    company: str | None = None,
    page_unit: tuple[str | None, tuple[str, ...]] = (None, ()),
) -> TableExtraction:
    """Trích một block bảng. `page_unit` = (mã đơn vị, cờ) từ dòng "Đơn vị tính" trước bảng."""
    if block.kind != "table":
        raise ValueError(f"{block.block_id} không phải block bảng")
    settings = load_table_settings() if settings is None else settings
    grid = _Grid.build(block.rows or ())
    table_flags = set(grid.flags)
    if block.flags:
        table_flags.add(FLAG_BLOCK_FLAGGED)

    parsed = {(s.row, s.col): _parse(s.cell.text, settings) for s in grid.origins()}
    header_rows, numbering = _header_rows(grid, parsed)
    if grid.n_rows and header_rows == grid.n_rows:
        table_flags.add(FLAG_NO_DATA_ROWS)
    if header_rows == 0:
        table_flags.add(FLAG_NO_HEADER)
    if numbering:
        table_flags.add(FLAG_COLUMN_NUMBERING)
    head = [r for r in range(header_rows) if r not in numbering]
    headers = _header_paths(grid, head)
    keyword_roles = _keyword_roles(grid, head, settings)

    rest = range(header_rows, grid.n_rows)
    header_like = {
        r
        for r in rest
        if _is_header_like(grid, r, parsed, allow_bare=_body_allows_bare(grid, r, parsed))
    }
    roles = _column_roles(grid, keyword_roles, [r for r in rest if r not in header_like], parsed)
    # Dòng giống header giữa thân bảng (bảng bị OCR gộp, header lặp theo kỳ) chỉ là header mới
    # khi có chữ ở cột giá trị hoặc trải hết bảng; chữ chỉ ở cột nhãn là tiêu đề mục.
    subheaders = tuple(
        r
        for r in sorted(header_like)
        if _is_full_width_title(grid, r)
        or any(roles[s.col] in (ROLE_VALUE, ROLE_TEXT) for s in _nonempty_starting(grid, r))
    )
    if subheaders:
        table_flags.add(FLAG_SUBHEADER_IN_BODY)
    if ROLE_LABEL not in roles and len(rest) > len(subheaders):
        table_flags.add(FLAG_NO_LABEL_COLUMN)
    label_cols = tuple(c for c, role in enumerate(roles) if role == ROLE_LABEL)

    values: list[RawTableValue] = []
    current_rows = list(head)
    paths = headers
    period_trusted = True
    pending: list[int] = []
    for r in rest:
        if r in subheaders:
            pending.append(r)
            continue
        if pending:
            current_rows, period_trusted = _next_header_rows(grid, current_rows, pending, settings)
            paths = _header_paths(grid, current_rows)
            pending = []
        row_label = " / ".join(_dedupe(grid.texts(r, roles, ROLE_LABEL)))
        row_cells = []
        for c in range(grid.n_cols):
            slot = grid.at(r, c)
            if roles[c] != ROLE_VALUE or slot is None or (slot.row, slot.col) != (r, c):
                continue
            row_cells.append((c, slot, parsed[(slot.row, slot.col)]))
        if not row_label and all(p.status == ValueStatus.MISSING for _, _, p in row_cells):
            continue  # dòng trống hoàn toàn: không có gì để mất
        ctx = _RowContext(
            r,
            row_label,
            code=" ".join(_dedupe(grid.texts(r, roles, "code"))) or None,
            note=" ".join(_dedupe(grid.texts(r, roles, "note"))) or None,
            seq=" ".join(_dedupe(grid.texts(r, roles, "index"))) or None,
            after_subheader=current_rows != head,
            period_trusted=period_trusted,
            label_from_rowspan=any(
                s is not None and roles[c] == ROLE_LABEL and s.row < r and s.cell.text.strip()
                for c, s in enumerate(grid.slots[r])
            ),
        )
        for c, slot, p in row_cells:
            values.append(
                _make_value(
                    block,
                    ctx,
                    c,
                    slot.cell,
                    p,
                    paths,
                    label_cols,
                    page_unit,
                    table_flags,
                    company,
                    settings,
                )
            )
    return TableExtraction(
        doc_id=block.doc_id,
        page=block.page,
        block_id=block.block_id,
        n_rows=grid.n_rows,
        n_cols=grid.n_cols,
        header_rows=header_rows,
        column_roles=roles,
        column_headers=headers,
        values=tuple(values),
        flags=tuple(sorted(table_flags)),
        subheader_rows=subheaders,
    )


# ---------------------------------------------------------------------------
# Lưới


@dataclass(frozen=True)
class _Slot:
    cell: TableCell
    row: int  # dòng gốc của ô
    col: int  # cột gốc của ô


@dataclass
class _Grid:
    slots: list[list[_Slot | None]]
    n_rows: int
    n_cols: int
    flags: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, rows: Sequence[Sequence[TableCell]]) -> _Grid:
        occupied: dict[tuple[int, int], _Slot] = {}
        flags: set[str] = set()
        n_rows = len(rows)
        for r, row in enumerate(rows):
            c = 0
            for cell in row:
                while (r, c) in occupied:
                    c += 1
                slot = _Slot(cell, r, c)
                if r + cell.row_span > n_rows:
                    flags.add(FLAG_ROWSPAN_OVERFLOW)
                for dr in range(min(cell.row_span, n_rows - r)):
                    for dc in range(cell.col_span):
                        occupied[(r + dr, c + dc)] = slot
                c += cell.col_span
        n_cols = max((c for _, c in occupied), default=-1) + 1
        slots = [[occupied.get((r, c)) for c in range(n_cols)] for r in range(n_rows)]
        if any(s is None for row in slots for s in row):
            flags.add(FLAG_RAGGED_ROWS)
        return cls(slots, n_rows, n_cols, flags)

    def at(self, r: int, c: int) -> _Slot | None:
        return self.slots[r][c]

    def origins(self) -> Iterator[_Slot]:
        for r, row in enumerate(self.slots):
            for c, s in enumerate(row):
                if s is not None and (s.row, s.col) == (r, c):
                    yield s

    def starting_in(self, r: int) -> list[_Slot]:
        return [
            s for c, s in enumerate(self.slots[r]) if s is not None and (s.row, s.col) == (r, c)
        ]

    def texts(self, r: int, roles: tuple[str, ...], role: str) -> list[str]:
        """Chữ của các ô thuộc vai trò `role` trên dòng `r` (kể cả ô rowspan từ dòng trên)."""
        out: list[str] = []
        seen: set[tuple[int, int]] = set()
        for c in range(self.n_cols):
            s = self.slots[r][c]
            if roles[c] == role and s is not None and (s.row, s.col) not in seen:
                seen.add((s.row, s.col))
                if s.cell.text.strip():
                    out.append(s.cell.text.strip())
        return out


def _nonempty_starting(grid: _Grid, r: int) -> list[_Slot]:
    return [s for s in grid.starting_in(r) if s.cell.text.strip()]


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for t in items:
        if not out or out[-1] != t:
            out.append(t)
    return out


def _parse(text: str, settings: TableSettings) -> ParsedNumber:
    return parse_number(
        text,
        "vn",
        dash_tokens=settings.numbers.dash_tokens,
        dash_status=settings.numbers.dash_status,
    )


# ---------------------------------------------------------------------------
# Header và vai trò cột


def _strip_periods(t: str) -> tuple[bool, str]:
    """Bỏ mọi cụm kỳ (ngày, quý, năm) khỏi `t` (đã chuẩn hóa); trả (có cụm kỳ?, phần còn lại)."""
    found = False
    for rx in (_DATE_SLASH_RE, _DATE_WORDS_RE, _QUARTER_RE, _QUARTER_NOYEAR_RE, _YEAR_RE):
        t, n = rx.subn(" ", t)
        found = found or n > 0
    return found, t


def _is_numeric_datum(p: ParsedNumber, allow_bare: bool) -> bool:
    """Ô mang số liệu: số hợp lệ hoặc dấu gạch. Năm đứng một mình chỉ được coi là header khi
    dòng không có nhãn (`allow_bare`)."""
    if p.reason == REASON_DASH:
        return True
    if p.status != ValueStatus.OK:
        return False
    return not (allow_bare and _YEAR_ONLY_RE.match(p.raw.strip()))


def _is_header_cell(p: ParsedNumber, allow_bare: bool) -> bool:
    """Ô có thể là header: chữ không có chữ số, hoặc chữ số chỉ để ghi kỳ.

    Kỳ trần không kèm chữ ("2015", "30/06/2025") chỉ là header khi `allow_bare`: trong dòng có
    nhãn, đó là số liệu. Ô số hỏng (dính số, lẫn chữ...) không bao giờ là header, vì nếu vậy
    giá trị `UNRESOLVED` của nó sẽ biến mất im lặng vào tiêu đề cột.
    """
    if p.reason == REASON_NO_DIGITS:
        return True
    t = norm_key(p.raw)
    if _DANGLING_DATE_RE.search(t):
        return True  # "Tại ngày 31 tháng 12 năm" + năm ở dòng header kế tiếp
    found, rest = _strip_periods(t)
    if not found or any(ch.isdigit() for ch in rest):
        return False
    return allow_bare or any(ch.isalpha() for ch in t)


def _is_header_like(
    grid: _Grid, r: int, parsed: dict[tuple[int, int], ParsedNumber], *, allow_bare: bool
) -> bool:
    """Dòng không có số liệu; mọi ô ngoài cột đầu là chữ/kỳ; có chữ ngoài cột đầu hoặc một
    ô trải hết bảng. Ô cột đầu được là chữ tự do nhưng không được là số hỏng.

    `allow_bare`: năm/ngày trần (không kèm chữ) có được coi là header không — xem
    `_header_rows` (khối header đầu bảng) và `_body_allows_bare` (thân bảng).
    """
    starting = _nonempty_starting(grid, r)
    if not starting:
        return False
    if _is_full_width_title(grid, r) and any(ch.isalpha() for ch in starting[0].cell.text):
        return True  # "5. Hàng tồn kho (xem Thuyết minh 3.7)": tiêu đề, số mục không phải số liệu
    for s in starting:
        p = parsed[(s.row, s.col)]
        if _is_numeric_datum(p, allow_bare):
            return False
        if s.col == 0:
            if p.reason in (REASON_BAD_FORMAT, REASON_MULTIPLE_NUMBERS) and not _is_header_cell(
                p, True
            ):
                return False
        elif not _is_header_cell(p, allow_bare):
            return False
    if any(s.col > 0 for s in starting):
        return True
    # Chỉ ô cột đầu có chữ: tiêu đề mục (không phải header) trừ khi ô trải hết bảng.
    return _is_full_width_title(grid, r)


def _is_full_width_title(grid: _Grid, r: int) -> bool:
    starting = _nonempty_starting(grid, r)
    return (
        len(starting) == 1
        and starting[0].col == 0
        and grid.n_cols > 1
        and starting[0].cell.col_span >= grid.n_cols
    )


def _is_numbering_row(grid: _Grid, r: int, parsed: dict[tuple[int, int], ParsedNumber]) -> bool:
    """Dòng đánh số cột ngay sau header (mẫu BCTC theo TT200): "1 | 2 | 3 | 4" hoặc
    "A | B | C | 1 | 2" (chữ cái liên tiếp từ A, rồi số liên tiếp từ 1)."""
    starting = _nonempty_starting(grid, r)
    if len(starting) < min(3, grid.n_cols) or len(starting) < 2:
        return False
    raws = [s.cell.text.strip() for s in starting]
    letters = [x for x in raws if len(x) == 1 and x.isascii() and x.isalpha() and x.isupper()]
    digits = raws[len(letters) :]
    if raws[: len(letters)] != letters or not digits:
        return False
    if letters != [chr(ord("A") + i) for i in range(len(letters))]:
        return False
    if not all(x.isascii() and x.isdigit() for x in digits):
        return False
    return [int(x) for x in digits] == list(range(1, len(digits) + 1))


def _header_rows(grid: _Grid, parsed: dict[tuple[int, int], ParsedNumber]) -> tuple[int, set[int]]:
    """Số dòng header đầu bảng và tập dòng đánh số cột (thuộc header, không vào đường dẫn).

    Năm/ngày trần là header chỉ khi các dòng header phía trên CHƯA có kỳ nào: "[Hàng tồn kho] /
    [Chỉ tiêu | 2024 | 2023]" là header hai tầng, còn "[Chỉ tiêu | 2024 | 2023] / [Số lao động |
    2015 | 1987]" thì dòng sau là số liệu.
    """
    r = 0
    period_seen = False
    while r < grid.n_rows:
        allow_bare = not period_seen or not any(s.col == 0 for s in _nonempty_starting(grid, r))
        if not _is_header_like(grid, r, parsed, allow_bare=allow_bare):
            break
        if r > 0 and _is_section_row(grid, r, parsed, period_seen):
            break
        period_seen = period_seen or any(
            _strip_periods(norm_key(s.cell.text))[0] for s in _nonempty_starting(grid, r)
        )
        r += 1
    if 0 < r < grid.n_rows and _is_numbering_row(grid, r, parsed):
        return r + 1, {r}
    return r, set()


def _is_section_row(
    grid: _Grid, r: int, parsed: dict[tuple[int, int], ParsedNumber], period_seen: bool
) -> bool:
    """Dòng chỉ một ô chữ (không kỳ, không đơn vị) sau khi header đã có kỳ: tiêu đề mục đầu
    tiên của thân bảng (ví dụ "Tài sản ngắn hạn" khi có cột STT), không phải header."""
    starting = _nonempty_starting(grid, r)
    if not period_seen or len(starting) != 1 or starting[0].cell.col_span != 1:
        return False
    return parsed[(starting[0].row, starting[0].col)].reason == REASON_NO_DIGITS


def _body_allows_bare(grid: _Grid, r: int, parsed: dict[tuple[int, int], ParsedNumber]) -> bool:
    """Trong thân bảng, năm/ngày trần chỉ là header khi dòng không có ô chữ nào (không nhãn ở
    bất kỳ cột nào): "[| Số lao động | 2015 | 1987]" là số liệu."""
    return not any(
        parsed[(s.row, s.col)].reason == REASON_NO_DIGITS for s in _nonempty_starting(grid, r)
    )


def _header_paths(grid: _Grid, rows: Sequence[int]) -> tuple[tuple[str, ...], ...]:
    """Đường dẫn header của từng cột: chữ các ô thuộc `rows`, mỗi ô gốc một lần."""
    paths = []
    for c in range(grid.n_cols):
        out: list[str] = []
        seen: set[tuple[int, int]] = set()
        for r in rows:
            s = grid.at(r, c)
            if s is None or (s.row, s.col) in seen:
                continue
            seen.add((s.row, s.col))
            if s.cell.text.strip():
                out.append(s.cell.text.strip())
        paths.append(tuple(out))
    return tuple(paths)


def _next_header_rows(
    grid: _Grid, current: list[int], pending: list[int], settings: TableSettings
) -> tuple[list[int], bool]:
    """Header cho các dòng sau một khối dòng header giữa bảng; trả (dòng header, kỳ tin được?).

    - Toàn tiêu đề mục trải hết bảng → giữ header cột, thay tiêu đề mục cũ.
    - Không mang kỳ/đơn vị ("Phương pháp | Bình quân") → giữ header cột, ghi thêm chữ.
    - Mang kỳ/đơn vị: thay hẳn. Nhưng nếu ô mang kỳ/đơn vị còn chữ khác ngoài từ chỉ kỳ
      ("Đến năm 2030", "Theo hợp đồng VND") thì đó có thể là dòng dữ liệu chữ, không phải
      header → kỳ của các dòng sau KHÔNG tin được (period_ambiguous).
    """
    if all(_is_full_width_title(grid, p) for p in pending):
        return [x for x in current if not _is_full_width_title(grid, x)] + pending, True
    texts = [s.cell.text for p in pending for s in _nonempty_starting(grid, p)]
    marked = [
        t for t in texts if _strip_periods(norm_key(t))[0] or unit_of(t, settings) != (None, False)
    ]
    if not marked:
        return current + pending, True
    with_period = [t for t in marked if _strip_periods(norm_key(t))[0]]
    return list(pending), all(_is_clean_header_text(t, settings) for t in with_period)


def _is_clean_header_text(text: str, settings: TableSettings) -> bool:
    """Ô chỉ gồm kỳ, đơn vị và từ chỉ kỳ (config `period_words`)."""
    t = _strip_periods(norm_key(text))[1]
    for key in sorted(settings.units, key=len, reverse=True):
        t = t.replace(key, " ")
    for w in sorted(settings.period_words, key=len, reverse=True):
        t = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", " ", t)
    return not any(ch.isalnum() for ch in t)


def _keyword_roles(
    grid: _Grid, head: Sequence[int], settings: TableSettings
) -> tuple[str | None, ...]:
    """Vai trò code/note/index: ô header riêng của cột (không trải nhiều cột) khớp trọn từ khóa.

    Không so khớp chuỗi con: tiêu đề "Thuyết minh báo cáo tài chính" hay nhãn
    "Chỉ tiêu (theo mã số thuế)" không được biến cột thành note/code.
    """
    roles: list[str | None] = []
    for c in range(grid.n_cols):
        role = None
        for r in head:
            s = grid.at(r, c)
            if s is None or s.cell.col_span != 1:
                continue
            t = norm_key(s.cell.text).strip(" .:")
            role = next((k for k, kws in settings.column_roles.items() if t in kws), role)
        roles.append(role)
    return tuple(roles)


def _column_roles(
    grid: _Grid,
    keyword_roles: tuple[str | None, ...],
    body: Sequence[int],
    parsed: dict[tuple[int, int], ParsedNumber],
) -> tuple[str, ...]:
    roles: list[str | None] = list(keyword_roles)

    def body_parsed(c: int) -> list[ParsedNumber]:
        out = []
        for r in body:
            s = grid.at(r, c)
            if s is not None and (s.row, s.col) == (r, c) and s.cell.text.strip():
                out.append(parsed[(s.row, s.col)])
        return out

    n_labels = 0
    leading = True
    for c in range(grid.n_cols):
        if roles[c] is not None:
            continue
        cells = body_parsed(c)
        # Cột nhãn đầu tiên: không có số liệu. Cột nhãn tiếp theo phải là chữ/kỳ thuần, để cột
        # toàn số hỏng không bị nuốt vào nhãn dòng mà phải ra UNRESOLVED.
        is_label = bool(cells) and not any(_is_numeric_datum(p, False) for p in cells)
        if is_label and n_labels:
            is_label = all(_is_header_cell(p, False) for p in cells)
        if leading and is_label:
            roles[c] = ROLE_LABEL
            n_labels += 1
            continue
        leading = False
        if cells and all(p.reason == REASON_NO_DIGITS for p in cells):
            roles[c] = ROLE_TEXT
        else:
            roles[c] = ROLE_VALUE
    return tuple(r or ROLE_VALUE for r in roles)


# ---------------------------------------------------------------------------
# Giá trị, kỳ, đơn vị


@dataclass(frozen=True)
class _RowContext:
    r: int
    label: str
    code: str | None = None
    note: str | None = None
    seq: str | None = None
    after_subheader: bool = False
    label_from_rowspan: bool = False
    period_trusted: bool = True


def _make_value(
    block: Block,
    row: _RowContext,
    c: int,
    cell: TableCell,
    p: ParsedNumber,
    paths: tuple[tuple[str, ...], ...],
    label_cols: tuple[int, ...],
    page_unit: tuple[str | None, tuple[str, ...]],
    table_flags: set[str],
    company: str | None,
    settings: TableSettings,
) -> RawTableValue:
    flags: set[str] = set(table_flags & _INHERITED_TABLE_FLAGS)
    if p.reason:
        flags.add(p.reason)
    own: list[str] = []
    for cc in range(c, min(c + cell.col_span, len(paths))):
        own.extend(h for h in paths[cc] if h not in own)
    # Header của cột nhãn (ô góc trái, ví dụ "Cho năm kết thúc ngày ...") áp cho mọi cột.
    prefix = [h for lc in label_cols for h in paths[lc]]
    header = list(dict.fromkeys(prefix + own))
    if cell.col_span > 1:
        flags.add(FLAG_SPANS_COLUMNS)
    if cell.row_span > 1:
        flags.add(FLAG_SPANS_ROWS)
    if not row.label:
        flags.add(FLAG_EMPTY_ROW_LABEL)
    if row.after_subheader:
        flags.add(FLAG_AFTER_SUBHEADER)
    if row.label_from_rowspan:
        flags.add(FLAG_LABEL_FROM_ROWSPAN)

    header_key = norm_key(" ".join(header))
    own_key = norm_key(" ".join(own))
    own_period = parse_period(own)[0]
    if not row.period_trusted:
        period, period_flags = None, (FLAG_PERIOD_AMBIGUOUS,)
    elif any(k in header_key for k in settings.cumulative_keywords):
        period, period_flags = None, (FLAG_PERIOD_CUMULATIVE,)
    elif own_period is None and any(
        re.search(rf"(?<!\w){re.escape(k)}(?!\w)", own_key)
        for k in settings.relative_period_keywords
    ):
        # "Năm trước" dưới tiêu đề "Cho năm 2024": không được lấy kỳ ở ô góc trái.
        period, period_flags = None, (FLAG_PERIOD_RELATIVE,)
    else:
        period, period_flags = parse_period(header)
        if period is not None and own_period is None:
            # Kỳ chỉ đến từ ô góc trái: đúng với bảng bộ phận (mọi cột cùng kỳ) nhưng cần kiểm
            # khi các cột là các kỳ khác nhau mà header cột không ghi kỳ.
            period_flags = (FLAG_PERIOD_FROM_CORNER,)
    flags.update(period_flags)
    if any(k in header_key for k in settings.restated_keywords):
        flags.add(FLAG_RESTATED)

    unit, unit_flags = _column_unit(header, page_unit, settings)
    if p.is_percent:
        if unit is not None:
            unit_flags = unit_flags | {FLAG_UNIT_CONFLICT}
        unit, unit_flags = UNIT_PERCENT, unit_flags - {FLAG_UNIT_MISSING}
    flags.update(unit_flags)

    return RawTableValue(
        value_id=f"{block.block_id}:r{row.r:03d}:c{c:03d}",
        doc_id=block.doc_id,
        page=block.page,
        block_id=block.block_id,
        row_index=row.r,
        col_index=c,
        company=company,
        row_label=row.label,
        row_code=row.code,
        note_ref=row.note,
        row_seq=row.seq,
        column_header=tuple(header),
        period=period,
        unit=unit,
        raw=cell.text,
        value=p.value,
        status=p.status,
        is_percent=p.is_percent,
        flags=tuple(sorted(flags)),
    )


def parse_period(texts: Sequence[str]) -> tuple[Period | None, tuple[str, ...]]:
    """Đọc kỳ từ các ô header. Nhiều kỳ khác nhau hoặc ngày sai → `None` kèm cờ, không chọn.

    "Quý N" không kèm năm chỉ ghép được với đúng một năm ở ô header khác.
    """
    dates: set[str] = set()
    quarters: set[tuple[int, int]] = set()
    bare_quarters: set[int] = set()
    years: set[int] = set()
    invalid = False
    used: list[str] = []
    for raw in texts:
        t = norm_key(raw)
        found = False
        consumed: list[tuple[int, int]] = []

        def free(m: re.Match[str], consumed: list[tuple[int, int]] = consumed) -> bool:
            return not any(a <= m.start() < b for a, b in consumed)

        for rx in (_DATE_SLASH_RE, _DATE_WORDS_RE):
            for m in rx.finditer(t):
                consumed.append(m.span())
                found = True
                d, mo, y = (int(g) for g in m.groups())
                try:
                    dates.add(dt.date(y, mo, d).isoformat())
                except ValueError:
                    invalid = True
        for m in _QUARTER_RE.finditer(t):
            if free(m):
                consumed.append(m.span())
                found = True
                quarters.add((int(m.group(2)), _quarter_no(m.group(1))))
        for m in _QUARTER_NOYEAR_RE.finditer(t):
            if free(m):
                consumed.append(m.span())
                found = True
                bare_quarters.add(_quarter_no(m.group(1)))
        for m in _YEAR_RE.finditer(t):
            if free(m):
                years.add(int(m.group(1)))
                found = True
        if found:
            used.append(raw)

    text = " | ".join(used)
    if invalid:
        return None, (FLAG_PERIOD_INVALID,)
    if not (dates or quarters or bare_quarters or years):
        return None, (FLAG_PERIOD_MISSING,)
    if (
        len(dates) > 1
        or len(quarters) > 1
        or len(bare_quarters) > 1
        or (dates and (quarters or bare_quarters))
        or (quarters and bare_quarters)
    ):
        return None, (FLAG_PERIOD_AMBIGUOUS,)
    if bare_quarters:
        if not years:
            return None, (FLAG_PERIOD_INCOMPLETE,)
        if len(years) > 1:
            return None, (FLAG_PERIOD_AMBIGUOUS,)
        return Period(text, year=next(iter(years)), quarter=next(iter(bare_quarters))), ()
    anchor_year = None
    if dates:
        anchor_year = int(next(iter(dates))[:4])
    elif quarters:
        anchor_year = next(iter(quarters))[0]
    if (anchor_year is not None and years - {anchor_year}) or (
        anchor_year is None and len(years) > 1
    ):
        return None, (FLAG_PERIOD_AMBIGUOUS,)
    if dates:
        d = next(iter(dates))
        return Period(text, date=d, year=int(d[:4])), ()
    if quarters:
        y, q = next(iter(quarters))
        return Period(text, year=y, quarter=q), ()
    return Period(text, year=next(iter(years))), ()


def _quarter_no(token: str) -> int:
    return _ROMAN.get(token) or int(token)


def unit_of(text: str, settings: TableSettings) -> tuple[str | None, bool]:
    """Mã đơn vị của một nhãn (cuối ô header hoặc trong ngoặc cuối).

    Trả `(None, True)` khi có dấu hiệu đơn vị nhưng không nhận ra (ngoại tệ, "ngàn VND" khi
    config chỉ có "ngàn đồng", "Tr. VND"...): không được rơi về "VND" vì sẽ sai tiền tệ hoặc
    bậc độ lớn.
    """
    t = norm_key(text)
    for k in sorted(settings.restated_keywords, key=len, reverse=True):
        t = t.replace(k, " ")  # "2023VNDĐã điều chỉnh lại" → "2023vnd"
    t = norm_key(_EMPTY_PARENS_RE.sub(" ", t))
    if any(
        re.search(rf"(?<!\w){re.escape(tok)}(?!\w)", t) for tok in settings.foreign_currency_tokens
    ):
        return None, True
    if t.endswith(")") and "(" in t:
        inner = t[t.rfind("(") + 1 : -1].strip()
        if inner in settings.units:
            return settings.units[inner], False
    scale = {k.split(" ")[0] for k in settings.units if " " in k}
    scale |= set(settings.scale_abbreviations)
    for key in sorted(settings.units, key=len, reverse=True):
        if t == key:
            return settings.units[key], False
        if not t.endswith(key):
            continue
        before = t[: -len(key)]
        if before and before[-1].isalpha() and not key.isascii():
            continue  # "hợp đồng" không phải "đồng"
        words = before.split()
        if words and (
            words[-1] in scale
            or any(
                words[-1].endswith(w) and words[-1][: -len(w)][-1:].isdigit()
                for w in scale
                if len(w) >= 3
            )
        ):
            return None, True  # bậc độ lớn có mặt nhưng tổ hợp không có trong config
        return settings.units[key], False
    return None, False


def _column_unit(
    header: Sequence[str],
    page_unit: tuple[str | None, tuple[str, ...]],
    settings: TableSettings,
) -> tuple[str | None, set[str]]:
    codes: set[str] = set()
    flags: set[str] = set()
    for h in header:
        code, unrecognized = unit_of(h, settings)
        if code:
            codes.add(code)
        if unrecognized:
            flags.add(FLAG_UNIT_UNRECOGNIZED)
    page_code, page_flags = page_unit
    flags.update(page_flags)
    if page_code:
        codes.add(page_code)
    if len(codes) > 1:
        return None, flags | {FLAG_UNIT_CONFLICT}
    if not codes:
        return None, flags | {FLAG_UNIT_MISSING}
    if FLAG_UNIT_UNRECOGNIZED in flags:
        return None, flags | {FLAG_UNIT_CONFLICT}
    return next(iter(codes)), flags


def _page_unit_statement(
    before: Sequence[Block], settings: TableSettings
) -> tuple[str | None, tuple[str, ...]]:
    """Dòng "Đơn vị tính: ..." gần nhất trong các block text cùng trang, trước bảng."""
    prefixes = sorted(settings.unit_statement_prefixes, key=len, reverse=True)
    for block in reversed(before):
        if block.kind != "text":
            continue
        for line in reversed(block.raw.split("\n")):
            t = norm_key(line).strip("() ")
            for prefix in prefixes:
                # Bắt buộc có dấu hai chấm: "Đơn vị báo cáo ..." không phải khai báo đơn vị.
                head, sep, rest = t[len(prefix) :].partition(":")
                if not t.startswith(prefix) or not sep or head.strip():
                    continue
                rest = rest.strip(" .;")
                if rest in settings.units:
                    return settings.units[rest], ()
                return None, (FLAG_UNIT_UNRECOGNIZED,)
    return None, ()
