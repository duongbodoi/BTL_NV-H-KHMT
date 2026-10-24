"""Nhập chuỗi giá tháng từ file Excel (.xlsx) sang CSV `date,value` của `ExternalStore`.

Chỉ dùng thư viện chuẩn (xlsx là file zip XML). Sheet đầu tiên, cột ngày và cột giá (mặc định A, B).
Ngày chấp nhận: `2015M01` (World Bank Pink Sheet), `2015-01`, `2015-01-31`, `2015/01`, hoặc số ngày
của Excel. Dòng tiêu đề (cột giá không phải số) bị bỏ qua; mọi dòng dữ liệu khác phải hợp lệ, nếu
không cả lần nhập lỗi, không đọc dở.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from steel_xai.verification.data import MAX_PRICE

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PINK = re.compile(r"(\d{4})M(\d{2})")
_YM = re.compile(r"(\d{4})[-/](\d{2})(?:[-/](\d{2}))?")
_EXCEL_EPOCH = dt.date(1899, 12, 30)


class PriceImportError(ValueError):
    """File giá sai định dạng; thông báo nêu dòng lỗi."""


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in root.findall(f"{_NS}si")]


def read_xlsx_rows(path: str | Path) -> list[dict[str, str]]:
    """Các dòng của sheet đầu tiên: {chữ cột: giá trị dạng chuỗi}; ô trống bị bỏ."""
    try:
        with zipfile.ZipFile(path) as z:
            strings = _shared_strings(z)
            sheets = sorted(
                n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
            )
            if not sheets:
                raise PriceImportError(f"{Path(path).name}: không có sheet nào")
            root = ET.fromstring(z.read(sheets[0]))
    except zipfile.BadZipFile as e:
        raise PriceImportError(f"{Path(path).name}: không phải file xlsx hợp lệ") from e
    rows: list[dict[str, str]] = []
    for row in root.iter(f"{_NS}row"):
        cells: dict[str, str] = {}
        for c in row.findall(f"{_NS}c"):
            col = re.match(r"[A-Z]+", c.get("r", ""))
            if not col:
                continue
            kind = c.get("t")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
            else:
                v = c.find(f"{_NS}v")
                if v is None or v.text is None:
                    continue
                text = strings[int(v.text)] if kind == "s" else v.text
            if text.strip():
                cells[col.group(0)] = text.strip()
        if cells:
            rows.append(cells)
    return rows


def _month_of(raw: str) -> tuple[int, int]:
    m = _PINK.fullmatch(raw) or _YM.fullmatch(raw)
    if m:
        y, mth = int(m.group(1)), int(m.group(2))
    else:
        try:
            serial = float(raw)
            d = _EXCEL_EPOCH + dt.timedelta(days=int(serial))
        except (ValueError, OverflowError) as e:
            raise ValueError(f"ngày không hiểu được: {raw!r}") from e
        if not 20000 <= serial <= 80000:
            raise ValueError(f"ngày không hiểu được: {raw!r}")
        y, mth = d.year, d.month
    try:
        dt.date(y, mth, 1)
    except ValueError as e:
        raise ValueError(f"tháng không hợp lệ: {raw!r}") from e
    return y, mth


def monthly_series_from_xlsx(
    path: str | Path, date_col: str = "A", value_col: str = "B"
) -> list[tuple[str, Decimal]]:
    """[(YYYY-MM, giá)] theo thứ tự thời gian từ sheet đầu của file xlsx."""
    out: dict[tuple[int, int], Decimal] = {}
    seen_data = False
    for i, row in enumerate(read_xlsx_rows(path), start=1):
        raw_date, raw_val = row.get(date_col), row.get(value_col)
        if raw_val is None and raw_date is None:
            continue
        try:
            value = Decimal(raw_val) if raw_val is not None else None
        except InvalidOperation:
            value = None
        if value is None and not seen_data:
            continue  # dòng tiêu đề
        seen_data = True
        try:
            if raw_date is None or value is None:
                raise ValueError("thiếu ngày hoặc giá")
            if not value.is_finite() or value <= 0 or value > MAX_PRICE:
                raise ValueError(f"giá không hợp lệ: {raw_val}")
            month = _month_of(raw_date)
            if month in out:
                raise ValueError(f"tháng {month[0]}-{month[1]:02d} bị lặp")
        except ValueError as e:
            raise PriceImportError(f"{Path(path).name} dòng {i}: {e}") from e
        out[month] = value
    if not out:
        raise PriceImportError(f"{Path(path).name}: không có dòng dữ liệu nào")
    return [(f"{y}-{m:02d}", v) for (y, m), v in sorted(out.items())]


def write_series_csv(rows: list[tuple[str, Decimal]], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "value"])
        for d, v in rows:
            w.writerow([d, format(v, "f")])
