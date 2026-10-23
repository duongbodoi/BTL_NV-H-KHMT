"""Điểm vào Phase 3: bảng OCR + CSV có cấu trúc → fact đã chuẩn hóa."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from steel_xai.contracts import FinancialFact
from steel_xai.extraction.structured import RawStructuredValue
from steel_xai.extraction.tables import TableExtraction
from steel_xai.normalization.mapping import Rejection, map_structured, map_tables
from steel_xai.normalization.resolve import Conflict, derive_facts, resolve_facts
from steel_xai.normalization.settings import NormalizationSettings, load_normalization_settings


@dataclass(frozen=True)
class NormalizationResult:
    facts: tuple[FinancialFact, ...]
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
            "rejections": [r.to_dict() for r in self.rejections],
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


def normalize(
    tables: Sequence[TableExtraction] = (),
    structured: Sequence[RawStructuredValue] = (),
    settings: NormalizationSettings | None = None,
) -> NormalizationResult:
    """Deterministic: cùng đầu vào → cùng đầu ra, không phụ thuộc thứ tự đầu vào."""
    s = load_normalization_settings() if settings is None else settings
    t_cands, t_rej = map_tables(tables, s)
    s_cands, s_rej = map_structured(structured, s)
    facts, conflicts = resolve_facts(t_cands + s_cands, s)
    facts = facts + derive_facts(facts, s)
    rejections = sorted(t_rej + s_rej, key=lambda r: (r.source, r.ref, r.metric))
    return NormalizationResult(tuple(facts), tuple(rejections), tuple(conflicts))
