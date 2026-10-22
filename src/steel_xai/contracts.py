"""Data contract dùng chung giữa các phase (spec mục 18).

Chỉ định nghĩa những gì phase hiện tại cần; các contract khác thêm ở phase tương ứng.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ValueStatus(StrEnum):
    """Trạng thái một giá trị số (spec mục 18, 23). Chỉ `OK` mới có `value`; không ép về 0."""

    OK = "OK"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class Provenance:
    """Nguồn gốc của một mảnh dữ liệu: tài liệu, trang PDF, block/chunk, văn bản gốc.

    `page` luôn là số trang PDF (1-based), không phải số trang in trên tài liệu.
    """

    doc_id: str
    page: int
    block_id: str | None = None
    chunk_id: str | None = None
    source_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Provenance:
        return cls(
            doc_id=d["doc_id"],
            page=d["page"],
            block_id=d.get("block_id"),
            chunk_id=d.get("chunk_id"),
            source_text=d.get("source_text"),
        )
