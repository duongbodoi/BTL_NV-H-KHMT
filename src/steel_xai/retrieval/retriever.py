"""BM25 trên chunk, có LỌC AS-OF ÉP TRONG CODE (spec mục 7.2) và không trả câu trả lời tổng hợp.

Thứ tự bắt buộc: (1) chọn chunk đủ điều kiện (đúng công ty, công bố <= as-of, không phải năm tham
chiếu), (2) mới tính điểm BM25 trên tập đó. Thống kê IDF/độ dài trung bình cũng chỉ tính trên tập
đủ điều kiện, nên tài liệu công bố sau as-of không ảnh hưởng điểm hay thứ hạng dù chỉ gián tiếp.
Chunk chưa biết ngày công bố KHÔNG bao giờ được trả về.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from steel_xai.contracts import Provenance
from steel_xai.retrieval.chunking import Chunk
from steel_xai.retrieval.settings import RetrievalSettings, load_retrieval_settings

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str, ngrams: Sequence[int] = (1, 2)) -> list[str]:
    """NFKC, chữ thường, tách theo âm tiết; thêm n-gram (nối bằng khoảng trắng) theo cấu hình."""
    words = _WORD.findall(unicodedata.normalize("NFKC", text).lower())
    out: list[str] = []
    for n in ngrams:
        out += [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]
    return out


def parse_asof(value: str | dt.date) -> dt.date:
    """as-of bắt buộc là ngày hợp lệ (ISO); không có giá trị mặc định ở nơi gọi."""
    if isinstance(value, dt.datetime):
        raise TypeError("as-of phải là ngày (date) hoặc chuỗi ISO, không phải datetime")
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"as-of không hợp lệ: {value!r}") from e


@dataclass(frozen=True)
class RetrievedEvidence:
    """Một đoạn gốc được truy xuất kèm provenance. Không có phần tóm tắt hay câu trả lời."""

    chunk_id: str
    doc_id: str
    company: str
    period: str
    page: int
    section: str | None
    kind: str
    text: str  # nguyên văn chunk; Phase 9 kiểm trích dẫn là chuỗi con của chuỗi này
    score: float
    doc_published: str
    queries: tuple[str, ...]  # truy vấn nào đã tìm ra đoạn này

    def provenance(self) -> Provenance:
        return Provenance(
            doc_id=self.doc_id, page=self.page, chunk_id=self.chunk_id, source_text=self.text
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "company": self.company,
            "period": self.period,
            "page": self.page,
            "section": self.section,
            "kind": self.kind,
            "text": self.text,
            "score": self.score,
            "doc_published": self.doc_published,
            "queries": list(self.queries),
        }


@dataclass(frozen=True)
class _Stats:
    n: int
    avgdl: float
    df: dict[str, int]


class BM25Retriever:
    def __init__(self, chunks: Iterable[Chunk], settings: RetrievalSettings | None = None) -> None:
        self.settings = load_retrieval_settings() if settings is None else settings
        self.chunks: tuple[Chunk, ...] = tuple(chunks)
        ids = [c.chunk_id for c in self.chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("chunk_id trùng trong kho")
        self._tf: list[Counter[str]] = []
        self._len: list[int] = []
        for c in self.chunks:
            # tên mục được đưa vào chỉ mục (không đổi `text` dùng để trích dẫn)
            toks = tokenize(f"{c.section or ''}\n{c.text}", self.settings.ngrams)
            self._tf.append(Counter(toks))
            self._len.append(len(toks))
        self._stats_cache: dict[tuple[tuple[str, ...], dt.date], tuple[list[int], _Stats]] = {}

    # ---- đủ điều kiện (as-of)

    def eligible_indices(self, companies: Sequence[str], asof: str | dt.date) -> list[int]:
        """Chỉ số chunk được phép truy xuất cho (công ty, as-of). Đây là NƠI DUY NHẤT lọc as-of."""
        cut = parse_asof(asof)
        wanted = set(companies)
        out = []
        for i, c in enumerate(self.chunks):
            if c.company not in wanted:
                continue
            if c.period_year >= self.settings.reference_only_from_year:
                continue
            if c.doc_published is None:
                continue
            try:
                published = dt.date.fromisoformat(c.doc_published)
            except ValueError:
                continue
            if published <= cut:
                out.append(i)
        return out

    def _stats(self, companies: tuple[str, ...], asof: dt.date) -> tuple[list[int], _Stats]:
        key = (companies, asof)
        hit = self._stats_cache.get(key)
        if hit is None:
            idx = self.eligible_indices(companies, asof)
            df: Counter[str] = Counter()
            for i in idx:
                df.update(self._tf[i].keys())
            total = sum(self._len[i] for i in idx)
            hit = (idx, _Stats(len(idx), total / len(idx) if idx else 0.0, dict(df)))
            self._stats_cache[key] = hit
        return hit

    # ---- truy xuất

    def retrieve(
        self,
        query: str,
        *,
        company: str,
        asof: str | dt.date,
        top_k: int | None = None,
        companies: Sequence[str] | None = None,
    ) -> list[RetrievedEvidence]:
        """Top-k đoạn đủ điều kiện cho một truy vấn. Mặc định chỉ tài liệu của chính `company`."""
        return self.retrieve_many(
            [query], company=company, asof=asof, top_k=top_k, companies=companies
        )

    def retrieve_many(
        self,
        queries: Sequence[str],
        *,
        company: str,
        asof: str | dt.date,
        top_k: int | None = None,
        companies: Sequence[str] | None = None,
    ) -> list[RetrievedEvidence]:
        """Mỗi truy vấn lấy top-k riêng rồi gộp theo chunk (giữ điểm cao nhất, ghi truy vấn)."""
        s = self.settings
        k = s.top_k if top_k is None else top_k
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ValueError("top_k phải là số nguyên >= 1")
        cut = parse_asof(asof)
        scope = tuple(sorted(set(companies) if companies is not None else {company}))
        idx, st = self._stats(scope, cut)
        merged: dict[int, tuple[float, list[str]]] = {}
        for q in queries:
            for i, score in self._top(q, idx, st, k):
                best, qs = merged.get(i, (0.0, []))
                merged[i] = (max(best, score), [*qs, q])
        ranked = sorted(merged.items(), key=lambda kv: (-kv[1][0], self.chunks[kv[0]].chunk_id))
        out = []
        for i, (score, qs) in ranked:
            c = self.chunks[i]
            assert c.doc_published is not None
            out.append(
                RetrievedEvidence(
                    chunk_id=c.chunk_id,
                    doc_id=c.doc_id,
                    company=c.company,
                    period=c.period,
                    page=c.page,
                    section=c.section,
                    kind=c.kind,
                    text=c.text,
                    score=score,
                    doc_published=c.doc_published,
                    queries=tuple(dict.fromkeys(qs)),
                )
            )
        return out

    def _top(self, query: str, idx: list[int], st: _Stats, k: int) -> list[tuple[int, float]]:
        terms = set(tokenize(query, self.settings.ngrams))
        if not idx or not terms:
            return []
        k1, b = self.settings.k1, self.settings.b
        idf = {
            t: math.log(1 + (st.n - st.df[t] + 0.5) / (st.df[t] + 0.5)) for t in terms if t in st.df
        }
        scored: list[tuple[int, float]] = []
        for i in idx:
            tf, dl = self._tf[i], self._len[i]
            norm = k1 * (1 - b + b * dl / st.avgdl) if st.avgdl else k1
            score = sum(w * tf[t] * (k1 + 1) / (tf[t] + norm) for t, w in idf.items() if tf.get(t))
            if score > self.settings.min_score:
                scored.append((i, score))
        scored.sort(key=lambda x: (-x[1], self.chunks[x[0]].chunk_id))
        return scored[:k]
