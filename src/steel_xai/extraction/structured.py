"""Adapter đọc BCTC có cấu trúc từ CSV (spec mục 7.1, Phase 2).

CSV xuất từ nguồn công khai (vnstock hoặc nguồn khác, nhóm chốt). Tên cột, layout
(`wide`: mỗi cột ngoài cột định danh là một chỉ tiêu; `long`: một dòng một chỉ tiêu) và định
dạng số nằm trong `configs/extraction.yaml`. Adapter không gọi mạng và không map chỉ tiêu về
metric chuẩn (Phase 3).

Không biết > đoán sai:
- ô rỗng/`nan`/... → `MISSING`; dấu gạch → status theo config, không bao giờ là 0;
- số không parse được, dòng thiếu/thừa cột, kỳ hoặc công ty không đọc được → `UNRESOLVED`;
  chỉ `OK` mới có `value` (số của bản `UNRESOLVED` vẫn đọc được ở `raw`);
- cùng (công ty, năm, quý, chỉ tiêu) xuất hiện nhiều lần với giá trị khác nhau → mọi bản đều
  `UNRESOLVED` kèm cờ `duplicate_conflict`, không tự chọn.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from steel_xai.contracts import ValueStatus
from steel_xai.extraction.numbers import normalize_space, parse_number
from steel_xai.extraction.settings import StructuredSettings, load_structured_settings

FLAG_RAGGED_ROW = "ragged_row"
FLAG_PERIOD_UNRESOLVED = "period_unresolved"
FLAG_COMPANY_MISSING = "company_missing"
FLAG_ITEM_MISSING = "item_missing"
FLAG_DUPLICATE = "duplicate"
FLAG_DUPLICATE_CONFLICT = "duplicate_conflict"
FLAG_UNIT_MISSING = "unit_missing"
FLAG_EXTRA_CELL = "extra_cell"  # ô nằm ngoài số cột của header
EXTRA_ITEM_PREFIX = "__extra_col_"


class StructuredSourceError(Exception):
    """File hoặc cấu hình cột không khớp: lỗi của đầu vào, không phải của một ô dữ liệu."""


@dataclass(frozen=True)
class StructuredRef:
    """Provenance của giá trị có cấu trúc: file, hash nội dung, số dòng vật lý (1-based), cột."""

    file: str
    content_sha256: str
    line: int
    column: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "content_sha256": self.content_sha256,
            "line": self.line,
            "column": self.column,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StructuredRef:
        return cls(d["file"], d["content_sha256"], d["line"], d["column"])


@dataclass(frozen=True)
class RawStructuredValue:
    company: str | None
    year: int | None
    quarter: int | None  # None = dữ liệu năm
    item: str
    unit: str | None
    raw: str
    value: Decimal | None
    status: ValueStatus
    ref: StructuredRef
    flags: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str | None, int | None, int | None, str]:
        return (self.company, self.year, self.quarter, self.item)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "year": self.year,
            "quarter": self.quarter,
            "item": self.item,
            "unit": self.unit,
            "raw": self.raw,
            "value": None if self.value is None else str(self.value),
            "status": self.status.value,
            "ref": self.ref.to_dict(),
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RawStructuredValue:
        return cls(
            company=d.get("company"),
            year=d.get("year"),
            quarter=d.get("quarter"),
            item=d["item"],
            unit=d.get("unit"),
            raw=d["raw"],
            value=None if d.get("value") is None else Decimal(d["value"]),
            status=ValueStatus(d["status"]),
            ref=StructuredRef.from_dict(d["ref"]),
            flags=tuple(d.get("flags", ())),
        )


def load_structured_csv(
    path: str | Path, settings: StructuredSettings | None = None
) -> list[RawStructuredValue]:
    """Đọc CSV UTF-8 (có/không BOM) thành các giá trị thô, theo thứ tự dòng rồi cột."""
    settings = load_structured_settings() if settings is None else settings
    path = Path(path)
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise StructuredSourceError(f"{path.name}: không phải UTF-8 hợp lệ ({e})") from e
    sha = hashlib.sha256(data).hexdigest()
    return parse_structured_csv(text, settings, source_name=path.name, content_sha256=sha)


def parse_structured_csv(
    text: str,
    settings: StructuredSettings | None = None,
    *,
    source_name: str = "<string>",
    content_sha256: str | None = None,
) -> list[RawStructuredValue]:
    settings = load_structured_settings() if settings is None else settings
    if text.startswith("﻿"):
        text = text[1:]
    sha = content_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise StructuredSourceError(f"{source_name}: file rỗng") from None
    header = [normalize_space(h) for h in header]  # NFC: cùng tên hiển thị → cùng cột
    if len(set(header)) != len(header):
        raise StructuredSourceError(f"{source_name}: tên cột trùng trong header {header}")
    cols = settings.columns
    needed = (
        [v for v in cols.values() if v]
        if settings.layout == "long"
        else [v for k, v in cols.items() if v and k not in ("item", "value")]
    )
    missing = [c for c in needed if c not in header]
    if missing:
        raise StructuredSourceError(f"{source_name}: thiếu cột {missing} (xem extraction.yaml)")
    id_cols = {v for k, v in cols.items() if v and k not in ("item", "value")}
    id_cols |= set(settings.ignore_columns)
    metric_cols = [h for h in header if h not in id_cols] if settings.layout == "wide" else []
    if settings.layout == "wide" and not metric_cols:
        raise StructuredSourceError(f"{source_name}: không có cột chỉ tiêu nào")

    out: list[RawStructuredValue] = []
    for row in reader:
        line = reader.line_num  # dòng vật lý cuối của bản ghi (CSV có thể có ô nhiều dòng)
        if not any(cell.strip() for cell in row):
            continue
        base_flags: set[str] = set()
        if len(row) != len(header):
            base_flags.add(FLAG_RAGGED_ROW)
        rec = {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}
        company = normalize_space(rec[cols["company"]]) or None
        if company is None:
            base_flags.add(FLAG_COMPANY_MISSING)
        year = _parse_int(rec[cols["year"]], 1900, 2100)
        quarter = None
        if cols["quarter"]:
            quarter = _parse_int(rec[cols["quarter"]], 1, 4)
            if quarter is None:
                base_flags.add(FLAG_PERIOD_UNRESOLVED)
        if year is None:
            base_flags.add(FLAG_PERIOD_UNRESOLVED)
        unit = (normalize_space(rec[cols["unit"]]) or None) if cols["unit"] else settings.unit

        if settings.layout == "wide":
            cells = [(h, h, rec[h]) for h in metric_cols]
        else:
            cells = [(normalize_space(rec[cols["item"]]), cols["value"], rec[cols["value"]])]
        # Ô thừa ngoài header: không bỏ đi, ghi thành giá trị UNRESOLVED.
        cells += [
            (f"{EXTRA_ITEM_PREFIX}{i + 1}", f"#{i + 1}", row[i])
            for i in range(len(header), len(row))
            if row[i].strip()
        ]
        for item, column, raw in cells:
            flags = set(base_flags)
            if item.startswith(EXTRA_ITEM_PREFIX):
                flags.add(FLAG_EXTRA_CELL)
            if not item:
                flags.add(FLAG_ITEM_MISSING)
            p = parse_number(
                raw,
                settings.number_format,
                dash_tokens=settings.numbers.dash_tokens,
                dash_status=settings.numbers.dash_status,
                missing_tokens=settings.missing_tokens,
            )
            if p.reason:
                flags.add(p.reason)
            status = p.status
            if (
                flags
                & {
                    FLAG_RAGGED_ROW,
                    FLAG_COMPANY_MISSING,
                    FLAG_PERIOD_UNRESOLVED,
                    FLAG_ITEM_MISSING,
                    FLAG_EXTRA_CELL,
                }
                and status != ValueStatus.MISSING
            ):
                status = ValueStatus.UNRESOLVED
            if unit is None and status == ValueStatus.OK:
                flags.add(FLAG_UNIT_MISSING)
            out.append(
                RawStructuredValue(
                    company=company,
                    year=year,
                    quarter=quarter,
                    item=item,
                    unit=unit,
                    raw=raw,
                    value=p.value if status == ValueStatus.OK else None,  # chỉ OK mới có số
                    status=status,
                    ref=StructuredRef(source_name, sha, line, column),
                    flags=tuple(sorted(flags)),
                )
            )
    return _mark_duplicates(out)


def _parse_int(raw: str, lo: int, hi: int) -> int | None:
    t = normalize_space(raw)
    if t.endswith(".0"):  # pandas ghi số nguyên dạng float: "2023.0"
        t = t[:-2]
    if not t.isascii() or not t.isdigit():
        return None
    n = int(t)
    return n if lo <= n <= hi else None


def _mark_duplicates(values: list[RawStructuredValue]) -> list[RawStructuredValue]:
    """Cùng (công ty không phân biệt hoa thường, năm, quý, chỉ tiêu) nhiều lần: giống hệt (cả
    đơn vị) → cờ `duplicate`; khác → mọi bản `UNRESOLVED`, `value=None`, không tự chọn."""
    groups: dict[tuple[str | None, int | None, int | None, str], list[int]] = {}
    for i, v in enumerate(values):
        company = v.company.casefold() if v.company else None
        groups.setdefault((company, v.year, v.quarter, v.item), []).append(i)
    out = list(values)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        distinct = {(values[i].status, values[i].value, values[i].unit) for i in idxs}
        conflict = len(distinct) > 1
        for i in idxs:
            v = values[i]
            flags = set(v.flags) | {FLAG_DUPLICATE_CONFLICT if conflict else FLAG_DUPLICATE}
            if conflict:
                out[i] = replace(
                    v, status=ValueStatus.UNRESOLVED, value=None, flags=tuple(sorted(flags))
                )
            else:
                out[i] = replace(v, flags=tuple(sorted(flags)))

    return out
