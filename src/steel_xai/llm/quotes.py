"""Kiểm trích dẫn nguyên văn bằng code (spec mục 10.4): quote phải là chuỗi con của đoạn gốc.

So khớp sau chuẩn hóa: mọi chuỗi khoảng trắng/xuống dòng (kể cả NBSP) thành một dấu cách, Unicode
về NFC, bỏ ký tự định dạng vô hình (zero-width...). KHÔNG bỏ hoa/thường, KHÔNG bỏ dấu câu/dấu tiếng
Việt, KHÔNG chấp nhận "..." nối các mẩu: chỉ một đoạn liên tục của văn bản gốc mới qua được.

Thêm hai chốt chất lượng: quote không được bắt đầu/kết thúc giữa một từ của văn bản gốc, và phần lớn
ký tự phải là chữ (chặn quote chỉ gồm dấu `|` hoặc số của bảng, khớp tình cờ).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WS = re.compile(r"\s+")


def canonical(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    return _WS.sub(" ", t).strip()


@dataclass(frozen=True)
class QuoteCheck:
    ok: bool
    # ok | empty | too_short | too_long | low_text | not_found | partial_word
    reason: str
    quote: str  # dạng chuẩn hóa của trích dẫn (chuỗi con của canonical(text) khi ok)


def _alpha_ratio(q: str) -> float:
    chars = [c for c in q if not c.isspace()]
    return sum(c.isalpha() for c in chars) / len(chars) if chars else 0.0


def _whole_word_occurrence(q: str, text: str) -> bool | None:
    """True: có lần xuất hiện không cắt giữa từ; False: chỉ có lần cắt giữa từ; None: không có."""
    start, found = text.find(q), False
    while start != -1:
        found = True
        before = text[start - 1] if start > 0 else " "
        end = start + len(q)
        after = text[end] if end < len(text) else " "
        if not (before.isalnum() and q[0].isalnum()) and not (after.isalnum() and q[-1].isalnum()):
            return True
        start = text.find(q, start + 1)
    return False if found else None


def verify_quote(
    quote: str, text: str, *, min_chars: int, max_chars: int, min_alpha_ratio: float = 0.4
) -> QuoteCheck:
    q = canonical(quote)
    if not q:
        return QuoteCheck(False, "empty", q)
    if len(q) < min_chars:
        return QuoteCheck(False, "too_short", q)
    if len(q) > max_chars:
        return QuoteCheck(False, "too_long", q)
    if _alpha_ratio(q) < min_alpha_ratio:
        return QuoteCheck(False, "low_text", q)
    hit = _whole_word_occurrence(q, canonical(text))
    if hit is None:
        return QuoteCheck(False, "not_found", q)
    if hit is False:
        return QuoteCheck(False, "partial_word", q)
    return QuoteCheck(True, "ok", q)
