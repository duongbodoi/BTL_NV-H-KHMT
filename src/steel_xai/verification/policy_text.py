"""Đoạn chính sách kế toán trong thuyết minh (cho tool `policy_text_similarity`).

Tìm đoạn chính sách của một khoản mục (ví dụ "hàng tồn kho") trong báo cáo năm N: lần xuất hiện ĐẦU
TIÊN của câu mở đầu chính sách ("Hàng tồn kho được ...") mà trong vài trăm ký tự sau có câu chữ đặc
trưng ("giá gốc", "giá trị thuần có thể thực hiện"). Phần chính sách kế toán luôn đứng trước thuyết
minh số liệu cùng tên. Lấy một cửa sổ CỐ ĐỊNH số từ từ câu đó, để so năm N với N−1 không phụ thuộc
cách đánh số tiêu đề ("2.8", "(f)", "4. Nguyên tắc ghi nhận ...") hay ranh giới chunk. Đo mức giống
nhau bằng code (difflib, không LLM). Không tìm thấy → None (check ra NOT_EVALUABLE), không đoán.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

# câu chữ cho biết đoạn là CHÍNH SÁCH (không phải bảng số / thuyết minh số liệu cùng tên)
POLICY_MARKERS: dict[str, tuple[str, ...]] = {
    "hàng tồn kho": ("giá gốc", "giá trị thuần có thể thực hiện"),
}
WINDOW_WORDS = 120  # số từ so sánh, tính từ câu mở đầu chính sách
MARKER_SPAN = 600  # câu chữ đặc trưng phải nằm trong bấy nhiêu ký tự sau câu mở đầu
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class PolicyText:
    company: str
    year: int
    section: str
    text: str  # cửa sổ WINDOW_WORDS từ, bắt đầu ở câu mở đầu chính sách
    doc_id: str
    page: int
    chunk_id: str
    published: dt.date | None


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


def _loose(s: str) -> str:
    """Bỏ dấu tiếng Việt TỪNG KÝ TỰ (giữ nguyên độ dài, vị trí): OCR hay sai dấu ("dược",
    "tổn kho"). Chỉ dùng để tìm và so; văn bản trích làm nguồn vẫn là bản gốc."""
    import unicodedata

    return "".join("d" if ch == "đ" else unicodedata.normalize("NFD", ch)[0] for ch in s)


def words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


class PolicyIndex:
    """Tra đoạn chính sách theo (công ty, năm tài chính của tài liệu, khoản mục)."""

    def __init__(self, chunks: Iterable[Any]) -> None:
        self._by: dict[tuple[str, int], list[Any]] = defaultdict(list)
        for c in chunks:
            if getattr(c, "kind", "text") == "text":
                self._by[(c.company, int(c.period_year))].append(c)
        for v in self._by.values():
            v.sort(key=lambda c: (c.doc_id, c.page, c.chunk_id))

    def find(self, company: str, year: int, item: str) -> PolicyText | None:
        key = _norm(item)
        markers = POLICY_MARKERS.get(key)
        if not markers:
            return None
        anchor = re.compile(re.escape(_loose(key)) + r"\s+(duoc|la)\b")
        loose_markers = tuple(_loose(mk) for mk in markers)
        docs: dict[str, list[Any]] = defaultdict(list)
        for c in self._by.get((company, year), ()):
            docs[c.doc_id].append(c)
        for doc_id in sorted(docs):
            cs = docs[doc_id]
            # nối văn bản cả tài liệu, nhớ vị trí bắt đầu của từng chunk để truy nguồn
            starts, parts, pos = [], [], 0
            for c in cs:
                t = _norm(c.text)
                starts.append(pos)
                parts.append(t)
                pos += len(t) + 1
            full = " ".join(parts)
            loose = _loose(full)
            for m in anchor.finditer(loose):
                span = loose[m.start() : m.start() + MARKER_SPAN]
                if not all(mk in span for mk in loose_markers):
                    continue
                window = " ".join(words(full[m.start() :])[:WINDOW_WORDS])
                i = max(j for j, s in enumerate(starts) if s <= m.start())
                c = cs[i]
                pub = c.doc_published
                return PolicyText(
                    company=company,
                    year=year,
                    section=c.section or item,
                    text=window,
                    doc_id=doc_id,
                    page=c.page,
                    chunk_id=c.chunk_id,
                    published=dt.date.fromisoformat(pub) if isinstance(pub, str) and pub else None,
                )
        return None


def similarity(a: str, b: str) -> Decimal:
    """Mức giống nhau 0..1 của hai đoạn (theo từ đã bỏ dấu, có thứ tự; difflib ratio)."""
    wa, wb = words(_loose(a)), words(_loose(b))
    if not wa or not wb:
        return Decimal(0)
    r = SequenceMatcher(None, wa, wb, autojunk=False).ratio()
    return Decimal(str(round(r, 4)))


def corpus_similarities(
    index: PolicyIndex, companies: Sequence[str], years: Sequence[int], item: str
) -> list[tuple[str, int, Decimal | None]]:
    """(công ty, năm, giống nhau với năm trước | None) cho mọi cặp năm liên tiếp có trong kho."""
    out = []
    for c in companies:
        for y in years:
            cur, prev = index.find(c, y, item), index.find(c, y - 1, item)
            sim = None if cur is None or prev is None else similarity(cur.text, prev.text)
            out.append((c, y, sim))
    return out
