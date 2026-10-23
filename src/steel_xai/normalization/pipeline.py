"""Điểm vào Phase 3: bảng OCR (và CSV nếu có) → fact đã chuẩn hóa theo năm tài chính."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from steel_xai.contracts import FinancialFact
from steel_xai.extraction.structured import RawStructuredValue
from steel_xai.extraction.tables import TableExtraction
from steel_xai.normalization.context import DocContext
from steel_xai.normalization.mapping import Rejection, map_structured, map_tables
from steel_xai.normalization.resolve import Conflict, derive_facts, resolve_facts
from steel_xai.normalization.settings import NormalizationSettings, load_normalization_settings


@dataclass(frozen=True)
class NormalizationResult:
    facts: tuple[FinancialFact, ...]
    comparatives: tuple[FinancialFact, ...]  # số so sánh về năm N trong báo cáo năm sau, lưu riêng
    rejections: tuple[Rejection, ...]  # giá trị khớp nhãn metric nhưng không gán được, kèm lý do
    conflicts: tuple[Conflict, ...]

    def fact(self, company: str, period: str, metric: str) -> FinancialFact | None:
        for f in self.facts:
            if f.key == (company, period, metric):
                return f
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "facts": [f.to_dict() for f in self.facts],
            "comparatives": [f.to_dict() for f in self.comparatives],
            "rejections": [r.to_dict() for r in self.rejections],
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


def normalize(
    tables: Sequence[TableExtraction] = (),
    structured: Sequence[RawStructuredValue] = (),
    settings: NormalizationSettings | None = None,
    contexts: Sequence[DocContext] = (),
) -> NormalizationResult:
    """Deterministic: cùng đầu vào → cùng đầu ra, không phụ thuộc thứ tự đầu vào.

    `contexts`: ngữ cảnh từng tài liệu (`build_context`); cần cho header tương đối ("Năm nay"),
    ngày kết thúc năm tài chính và đơn vị khi bảng không ghi.
    """
    s = load_normalization_settings() if settings is None else settings
    ctx_by_doc = {c.doc_id: c for c in contexts}
    t_cands, t_rej = map_tables(tables, s, ctx_by_doc)
    s_cands, s_rej = map_structured(structured, s)
    facts, comparatives, conflicts = resolve_facts(t_cands + s_cands, s)
    facts = facts + derive_facts(facts, s)
    rejections = sorted(t_rej + s_rej, key=lambda r: (r.source, r.ref, r.metric))
    return NormalizationResult(
        tuple(facts), tuple(comparatives), tuple(rejections), tuple(conflicts)
    )
