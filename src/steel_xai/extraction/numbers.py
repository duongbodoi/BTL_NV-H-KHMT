"""Parse một chuỗi số trong BCTC thành `Decimal` (spec mục 16, 23).

Định dạng `vn` (BCTC Việt Nam): dấu chấm ngăn nghìn (mỗi nhóm đúng 3 chữ số), dấu phẩy
thập phân, số âm trong ngoặc `(1.234)` hoặc có dấu trừ. Định dạng `plain` (CSV máy xuất):
`-1234567.89`, chấp nhận số mũ.

Không biết > đoán sai:
- ô rỗng → `MISSING`;
- dấu gạch → `value=None`, status theo config (mặc định `NOT_APPLICABLE`), **không bao giờ là 0**;
- mọi chuỗi khác không khớp trọn vẹn → `UNRESOLVED` kèm `reason`; không sửa OCR, không tách số dính.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from steel_xai.contracts import ValueStatus

NumberFormat = Literal["vn", "plain"]

DEFAULT_DASH_TOKENS = ("-", "–", "—", "−", "‐", "‑")

# Lý do không có giá trị (ghi vào `ParsedNumber.reason` và cờ của giá trị).
REASON_EMPTY = "empty"
REASON_DASH = "dash"
REASON_MISSING_MARKER = "missing_marker"
REASON_NO_DIGITS = "no_digits"
REASON_MULTIPLE_NUMBERS = "multiple_numbers"
REASON_MIXED_TEXT = "mixed_text"
REASON_BAD_FORMAT = "bad_format"
# Ghi chú cho giá trị OK: "1,234" theo quy ước VN là 1,234 (thập phân) nhưng theo định dạng
# Anh là 1234 — giữ cách hiểu VN và gắn cờ để Phase 3/4 kiểm.
NOTE_AMBIGUOUS_SEPARATOR = "ambiguous_separator"

_SPACE_RE = re.compile(r"\s+")
# Chỉ chữ số ASCII ([0-9], không dùng \d vốn nhận cả chữ số Ả Rập/full-width).
# Có dấu chấm ngăn nghìn thì nhóm đầu không bắt đầu bằng 0: "0.500" không phải 500.
_VN_RE = re.compile(
    r"^(?P<open>\()?(?P<sign>[-−])?"
    r"(?P<int>[1-9][0-9]{0,2}(?:\.[0-9]{3})+|[0-9]+)(?:,(?P<frac>[0-9]+))?"
    r"(?P<pct1>%)?(?P<close>\))?(?P<pct2>%)?$"
)
_PLAIN_RE = re.compile(
    r"^(?P<open>\()?(?P<num>[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)"
    r"(?P<pct1>%)?(?P<close>\))?(?P<pct2>%)?$"
)
# Một "cụm số": chữ số nối bởi dấu chấm/phẩy. Nhiều cụm trong một ô → số dính, không tách.
_NUMBER_RUN_RE = re.compile(r"\d+(?:[.,]\d+)*")
_ALLOWED_CHARS_RE = re.compile(r"^[0-9.,()%+\-−eE]+$")


@dataclass(frozen=True)
class ParsedNumber:
    raw: str
    value: Decimal | None
    status: ValueStatus
    is_percent: bool = False
    # Lý do không có giá trị; với giá trị OK có thể là ghi chú (NOTE_AMBIGUOUS_SEPARATOR).
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "value": None if self.value is None else str(self.value),
            "status": self.status.value,
            "is_percent": self.is_percent,
            "reason": self.reason,
        }


def normalize_space(text: str) -> str:
    """NFC + gộp mọi khoảng trắng Unicode (kể cả NBSP) thành một dấu cách, bỏ hai đầu."""
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()  # \s gồm cả NBSP


def parse_number(
    raw: str,
    number_format: NumberFormat = "vn",
    *,
    dash_tokens: Iterable[str] = DEFAULT_DASH_TOKENS,
    dash_status: ValueStatus = ValueStatus.NOT_APPLICABLE,
    missing_tokens: Iterable[str] = (),
) -> ParsedNumber:
    """Parse `raw` theo `number_format`; `raw` được giữ nguyên văn trong kết quả."""
    if number_format not in ("vn", "plain"):
        raise ValueError(f"number_format không hợp lệ: {number_format!r}")
    if dash_status == ValueStatus.OK:
        raise ValueError("dash_status không được là OK: dấu gạch không phải là số")
    text = normalize_space(raw)
    if not text:
        return ParsedNumber(raw, None, ValueStatus.MISSING, reason=REASON_EMPTY)
    if text.casefold() in {t.casefold() for t in missing_tokens}:
        return ParsedNumber(raw, None, ValueStatus.MISSING, reason=REASON_MISSING_MARKER)
    if text in set(dash_tokens):
        return ParsedNumber(raw, None, dash_status, reason=REASON_DASH)

    m = (_VN_RE if number_format == "vn" else _PLAIN_RE).match(text)
    if (
        m
        and bool(m.group("open")) == bool(m.group("close"))
        and not (m.group("pct1") and m.group("pct2"))
    ):
        value = _to_decimal(m, number_format)
        if value is not None:
            if m.group("open"):
                if value < 0:  # "(-1.234)": hai dấu âm, không đoán
                    return _unresolved(raw, text)
                value = -value
            is_percent = bool(m.group("pct1") or m.group("pct2"))
            note = None
            if (
                number_format == "vn"
                and not is_percent
                and "." not in m.group("int")
                and len(m.group("frac") or "") == 3
            ):
                note = NOTE_AMBIGUOUS_SEPARATOR
            return ParsedNumber(raw, value, ValueStatus.OK, is_percent=is_percent, reason=note)
    return _unresolved(raw, text)


def _to_decimal(m: re.Match[str], number_format: NumberFormat) -> Decimal | None:
    try:
        if number_format == "plain":
            return Decimal(m.group("num"))
        digits = m.group("int").replace(".", "")
        if m.group("frac"):
            digits += "." + m.group("frac")
        value = Decimal(digits)
        return -value if m.group("sign") else value
    except InvalidOperation:
        return None


def _unresolved(raw: str, text: str) -> ParsedNumber:
    if not any(ch.isdigit() for ch in text):
        reason = REASON_NO_DIGITS
    elif len(_NUMBER_RUN_RE.findall(text)) > 1:
        reason = REASON_MULTIPLE_NUMBERS
    elif not _ALLOWED_CHARS_RE.match(text.replace(" ", "")):
        reason = REASON_MIXED_TEXT
    else:
        reason = REASON_BAD_FORMAT
    return ParsedNumber(raw, None, ValueStatus.UNRESOLVED, reason=reason)
