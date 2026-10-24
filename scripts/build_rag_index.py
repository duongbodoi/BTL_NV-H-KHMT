"""Dựng kho chunk RAG từ bộ OCR ViFinQA và ghi data/processed/rag_chunks.jsonl.

Chạy: python scripts/build_rag_index.py [thư_mục_ocr]
"""

from __future__ import annotations

import sys
from collections import Counter

from steel_xai.retrieval import build_chunks, default_chunks_path, write_chunks


def main(argv: list[str]) -> int:
    result = build_chunks(argv[0] if argv else None)
    path = default_chunks_path()
    write_chunks(result.chunks, path)
    docs = {c.doc_id for c in result.chunks}
    kinds = Counter(c.kind for c in result.chunks)
    unknown = {c.doc_id for c in result.chunks if c.doc_published is None}
    print(f"Chunk: {len(result.chunks)} ({dict(kinds)}) từ {len(docs)} tài liệu → {path}")
    print(f"Tài liệu chưa có ngày công bố (sẽ không bao giờ được truy xuất): {len(unknown)}")
    for n in result.notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
