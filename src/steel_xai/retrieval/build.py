"""Dựng kho chunk từ bộ OCR: parse → chunk → gắn ngày công bố (as-of) của từng tài liệu → JSONL."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from steel_xai.anomaly.corpus import DocRef, asof_from_corpus, find_documents, load_corpus
from steel_xai.anomaly.settings import AsofEntry
from steel_xai.config import load_config
from steel_xai.parsing import parse_file
from steel_xai.retrieval.chunking import Chunk, chunk_document
from steel_xai.retrieval.settings import RetrievalSettings, load_retrieval_settings


@dataclass(frozen=True)
class BuildResult:
    chunks: tuple[Chunk, ...]
    notes: tuple[str, ...]  # tài liệu bị bỏ qua / không có ngày công bố, để người xem


def build_chunks(
    ocr_dir: str | Path | None = None,
    companies: list[str] | None = None,
    settings: RetrievalSettings | None = None,
    asof: Mapping[tuple[str, str], AsofEntry] | None = None,
) -> BuildResult:
    """Chunk mọi tài liệu của `companies`. Năm >= mốc tham chiếu KHÔNG vào kho.

    `asof`: (công ty, kỳ) → ngày công bố. Mặc định lấy ngày ký báo cáo kiểm toán trích tự động
    (mốc TẠM, cùng nguồn với Phase 5). Tài liệu không có ngày vẫn được chunk nhưng không bao giờ
    được truy xuất (chunk.doc_published = None) và được ghi vào `notes`.
    """
    s = load_retrieval_settings() if settings is None else settings
    dataset = load_config("dataset")
    ocr = Path(ocr_dir or dataset["sources"]["ocr_dir"])
    comps = list(companies if companies is not None else dataset["companies"])

    refs: list[DocRef] = []
    for kind in s.document_kinds:
        refs += find_documents(ocr, comps, kind)
    corpus = load_corpus(refs)
    if asof is None:
        asof, _ = asof_from_corpus(corpus, s.reference_only_from_year)

    chunks: list[Chunk] = []
    notes: list[str] = []
    for ref, ctx in corpus.docs:
        if ctx.report_end is None:
            notes.append(f"{ref.doc_id}: không đọc được năm tài chính, bỏ qua")
            continue
        year = ctx.report_end.year
        if year >= s.reference_only_from_year:
            notes.append(f"{ref.doc_id}: năm {year} >= mốc tham chiếu, không vào kho")
            continue
        period = f"FY{year}"
        entry = asof.get((ref.company, period))
        if entry is None:
            notes.append(
                f"{ref.doc_id}: chưa có ngày công bố cho {period}, sẽ không được truy xuất"
            )
        doc = parse_file(ref.path, ref.doc_id)
        chunks += chunk_document(
            doc,
            company=ref.company,
            period=period,
            period_year=year,
            published=None if entry is None else entry.date,
            published_source=None if entry is None else entry.source,
            settings=s,
        )
    return BuildResult(tuple(chunks), tuple(notes))


def write_chunks(chunks: tuple[Chunk, ...] | list[Chunk], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def read_chunks(path: str | Path) -> list[Chunk]:
    with Path(path).open(encoding="utf-8") as f:
        return [Chunk.from_dict(json.loads(line)) for line in f if line.strip()]


def default_chunks_path(settings: RetrievalSettings | None = None) -> Path:
    s = load_retrieval_settings() if settings is None else settings
    return Path(load_config("dataset")["paths"]["processed"]) / s.chunks_file
