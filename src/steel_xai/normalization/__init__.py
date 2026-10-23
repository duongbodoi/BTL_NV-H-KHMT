"""Phase 3 — Chuẩn hóa metric: giá trị thô → `FinancialFact` (VND, theo năm tài chính)."""

from steel_xai.normalization.context import DocContext, build_context
from steel_xai.normalization.mapping import Candidate, Rejection
from steel_xai.normalization.pipeline import NormalizationResult, normalize
from steel_xai.normalization.resolve import Conflict
from steel_xai.normalization.settings import (
    MetricDef,
    NormalizationSettings,
    load_normalization_settings,
    norm_label,
)

__all__ = [
    "Candidate",
    "Conflict",
    "DocContext",
    "MetricDef",
    "NormalizationResult",
    "NormalizationSettings",
    "Rejection",
    "build_context",
    "load_normalization_settings",
    "norm_label",
    "normalize",
]
