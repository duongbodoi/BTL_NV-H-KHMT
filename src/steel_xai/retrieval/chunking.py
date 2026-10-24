"""Chunk theo cấu trúc tài liệu (spec mục 10.3): tiêu đề mục, đoạn văn, bảng; giữ trang và mục.

Mọi `Chunk.text` là chuỗi con NGUYÊN VĂN của văn bản block gốc (hoặc các block nối bằng dòng trống),
để Phase 9 kiểm trích dẫn nguyên văn. Tên mục chỉ là siêu dữ liệu; nó được đưa vào chỉ mục tìm kiếm
nhưng không đổi `text`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from steel_xai.contracts import Provenance
from steel_xai.parsing import Block, Document
from steel_xai.retrieval.settings import RetrievalSettings

_HEADING_NUM = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,3}[.)]?\s+[^\W\d_]")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
MAX_HEADING_CHARS = 120


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    company: str
    period: str  # năm tài chính của TÀI LIỆU (ví dụ FY2022)
    period_year: int
    doc_published: (
        str | None
    )  # ngày công bố tài liệu (ISO); None = chưa biết → không bao giờ được truy xuất
    published_source: str | None
    page: int
    page_end: int
    section: str | None
    kind: str  # text | table
    block_ids: tuple[str, ...]
    text: str

    def provenance(self) -> Provenance:
        return Provenance(
            doc_id=self.doc_id,
            page=self.page,
            block_id=self.block_ids[0],
            chunk_id=self.chunk_id,
            source_text=self.text,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "company": self.company,
            "period": self.period,
            "period_year": self.period_year,
            "doc_published": self.doc_published,
            "published_source": self.published_source,
            "page": self.page,
            "page_end": self.page_end,
            "section": self.section,
            "kind": self.kind,
            "block_ids": list(self.block_ids),
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Chunk:
        return cls(
            chunk_id=d["chunk_id"],
            doc_id=d["doc_id"],
            company=d["company"],
            period=d["period"],
            period_year=d["period_year"],
            doc_published=d.get("doc_published"),
            published_source=d.get("published_source"),
            page=d["page"],
            page_end=d["page_end"],
            section=d.get("section"),
            kind=d["kind"],
            block_ids=tuple(d["block_ids"]),
            text=d["text"],
        )


def is_heading(block: Block) -> bool:
    """Khối văn bản một dòng, ngắn, đánh số mục ("3.6 Hàng tồn kho") hoặc viết hoa toàn bộ."""
    if block.kind != "text":
        return False
    t = block.text.strip()
    if not t or "\n" in t or len(t) > MAX_HEADING_CHARS or t.endswith((".", ";", ",")):
        return False
    if _HEADING_NUM.match(t):
        return True
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def split_text(text: str, max_chars: int) -> list[str]:
    """Tách theo câu, rồi theo khoảng trắng nếu câu quá dài; mọi phần là chuỗi con của `text`."""
    if len(text) <= max_chars:
        return [text]
    bounds = [m.end() for m in _SENTENCE_END.finditer(text)] + [len(text)]
    pieces: list[tuple[int, int]] = []
    start = 0
    last: int | None = None
    for end in bounds:
        if end - start > max_chars:
            if last is not None and last > start:
                pieces.append((start, last))
                start = last
            while end - start > max_chars:  # một câu dài hơn max_chars
                cut = text.rfind(" ", start + 1, start + max_chars + 1)
                cut = start + max_chars if cut <= start else cut + 1
                pieces.append((start, cut))
                start = cut
        last = end
    if start < len(text):
        pieces.append((start, len(text)))
    return [p for p in (text[x:y].strip() for x, y in pieces) if p]


def _split_table(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for line in text.split("\n"):
        for part in split_text(line, max_chars):
            if cur and size + len(part) + 1 > max_chars:
                out.append("\n".join(cur))
                cur, size = [], 0
            cur.append(part)
            size += len(part) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def chunk_document(
    doc: Document,
    *,
    company: str,
    period: str,
    period_year: int,
    published: str | None,
    published_source: str | None,
    settings: RetrievalSettings,
) -> list[Chunk]:
    """Chunk một tài liệu theo thứ tự đọc. Đoạn văn liền kề cùng mục được gộp tới `max_chars`."""
    chunks: list[Chunk] = []
    section: str | None = None
    buf: list[Block] = []

    def emit(blocks: list[Block], text: str, kind: str, sec: str | None) -> None:
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}:c{len(chunks):04d}",
                doc_id=doc.doc_id,
                company=company,
                period=period,
                period_year=period_year,
                doc_published=published,
                published_source=published_source,
                page=blocks[0].page,
                page_end=blocks[-1].page,
                section=sec,
                kind=kind,
                block_ids=tuple(b.block_id for b in blocks),
                text=text,
            )
        )

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        text = "\n\n".join(b.text.strip() for b in buf if b.text.strip())
        if text and len(text) <= settings.max_chars:
            emit(buf, text, "text", section)
        elif text:  # mỗi block tự tách, để trang/block_ids của mảnh luôn đúng
            for b in buf:
                for piece in split_text(b.text.strip(), settings.max_chars):
                    emit([b], piece, "text", section)
        buf = []

    for block in doc.iter_blocks():
        if block.kind == "text":
            heading = is_heading(block)
            if heading:
                flush()
                section = block.text.strip()
            size = sum(len(b.text.strip()) + 2 for b in buf) + len(block.text.strip())
            if buf and size > settings.max_chars:
                flush()
            buf.append(block)
        else:
            flush()
            if not settings.include_tables or not block.text.strip():
                continue
            for piece in _split_table(block.text, settings.max_chars):
                emit([block], piece, "table", section)
    flush()
    return chunks
