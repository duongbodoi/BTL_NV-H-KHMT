"""Cấu hình retrieval (configs/retrieval.yaml), kiểm chặt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from steel_xai.config import ConfigError, load_config


@dataclass(frozen=True)
class RetrievalSettings:
    method: str
    top_k: int
    k1: float
    b: float
    ngrams: tuple[int, ...]
    min_score: float
    max_chars: int
    include_tables: bool
    document_kinds: tuple[str, ...]
    chunks_file: str
    reference_only_from_year: int  # từ năm này trở đi KHÔNG vào kho RAG (spec mục 3.3, 7.2)


def _num(v: Any, where: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < minimum:
        raise ConfigError(f"{where} phải là số >= {minimum}, nhận {v!r}")
    if maximum is not None and v > maximum:
        raise ConfigError(f"{where} phải <= {maximum}, nhận {v!r}")
    return float(v)


def load_retrieval_settings(
    cfg: dict[str, Any] | None = None, dataset: dict[str, Any] | None = None
) -> RetrievalSettings:
    cfg = load_config("retrieval") if cfg is None else cfg
    dataset = load_config("dataset") if dataset is None else dataset

    if cfg.get("method") != "bm25":
        raise ConfigError("retrieval.method hiện chỉ hỗ trợ 'bm25'")
    if cfg.get("enforce_asof_filter") is not True:
        raise ConfigError("retrieval.enforce_asof_filter phải là true (spec mục 7.2)")
    top_k = cfg.get("top_k")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ConfigError("retrieval.top_k phải là số nguyên >= 1")

    bm = cfg.get("bm25") or {}
    ngrams = bm.get("ngrams")
    if (
        not isinstance(ngrams, list)
        or not ngrams
        or not all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 3 for n in ngrams)
    ):
        raise ConfigError("retrieval.bm25.ngrams phải là danh sách số nguyên trong 1..3")
    ch = cfg.get("chunking") or {}
    max_chars = ch.get("max_chars")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 200:
        raise ConfigError("retrieval.chunking.max_chars phải là số nguyên >= 200")
    if not isinstance(ch.get("include_tables"), bool):
        raise ConfigError("retrieval.chunking.include_tables phải là bool")
    kinds = cfg.get("document_kinds")
    if (
        not isinstance(kinds, list)
        or not kinds
        or not all(k in ("consolidated", "separate") for k in kinds)
    ):
        raise ConfigError(
            "retrieval.document_kinds phải là danh sách con của [consolidated, separate]"
        )
    cf = cfg.get("chunks_file")
    if not isinstance(cf, str) or not cf.strip():
        raise ConfigError("retrieval.chunks_file phải là chuỗi không rỗng")
    ref = dataset.get("reference_only_from_year")
    if isinstance(ref, bool) or not isinstance(ref, int):
        raise ConfigError("dataset.reference_only_from_year phải là số nguyên")
    return RetrievalSettings(
        method="bm25",
        top_k=top_k,
        k1=_num(bm.get("k1"), "retrieval.bm25.k1"),
        b=_num(bm.get("b"), "retrieval.bm25.b", maximum=1.0),
        ngrams=tuple(sorted(set(ngrams))),
        min_score=_num(bm.get("min_score"), "retrieval.bm25.min_score"),
        max_chars=max_chars,
        include_tables=ch["include_tables"],
        document_kinds=tuple(kinds),
        chunks_file=cf,
        reference_only_from_year=ref,
    )
