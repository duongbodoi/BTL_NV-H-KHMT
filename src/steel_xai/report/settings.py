"""Cấu hình báo cáo grounded (configs/report.yaml), kiểm chặt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from steel_xai.config import ConfigError, load_config

UNITS = ("ratio", "ratio_diff", "pp", "days", "z", "vnd", "number")
ON_EXHAUSTED = ("skeleton_only", "drop_failing")
FORBIDDEN_GROUPS = ("investment_advice", "fraud", "certainty")


@dataclass(frozen=True)
class ReportSettings:
    top_k: int
    max_attempts: int
    on_exhausted: str
    max_claims: int
    max_claim_chars: int
    relative_tolerance: Decimal
    exempt_patterns: tuple[re.Pattern[str], ...]
    indicator_units: dict[str, str]
    tool_units: dict[str, str]
    ratio_metrics: frozenset[str]
    indicator_labels: dict[str, str]
    forbidden: dict[str, tuple[re.Pattern[str], ...]]
    causal: tuple[re.Pattern[str], ...]
    hedges: tuple[re.Pattern[str], ...]
    disclaimer: str
    draft_warning: str


def _int(v: Any, where: str, lo: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise ConfigError(f"report.{where} phải là số nguyên >= {lo}, nhận {v!r}")
    return v


def _patterns(v: Any, where: str, flags: int = re.IGNORECASE) -> tuple[re.Pattern[str], ...]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise ConfigError(f"report.{where} phải là danh sách regex (chuỗi khác rỗng)")
    try:
        return tuple(re.compile(x, flags) for x in v)
    except re.error as e:
        raise ConfigError(f"report.{where}: regex lỗi: {e}") from e


def _units(v: Any, where: str) -> dict[str, str]:
    if not isinstance(v, dict) or any(u not in UNITS for u in v.values()):
        raise ConfigError(f"report.{where} phải là mapping tên → đơn vị thuộc {UNITS}")
    return {str(k): str(u) for k, u in v.items()}


def _text(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"report.{where} phải là chuỗi khác rỗng")
    return v.strip()


def load_report_settings(cfg: dict[str, Any] | None = None) -> ReportSettings:
    cfg = load_config("report") if cfg is None else cfg
    if cfg.get("on_exhausted") not in ON_EXHAUSTED:
        raise ConfigError(f"report.on_exhausted phải thuộc {ON_EXHAUSTED}")
    nums = cfg.get("numbers")
    if not isinstance(nums, dict):
        raise ConfigError("report.numbers phải là mapping")
    try:
        tol = Decimal(str(nums.get("relative_tolerance")))
    except InvalidOperation as e:
        raise ConfigError("report.numbers.relative_tolerance phải là số") from e
    if not tol.is_finite() or not Decimal(0) <= tol <= Decimal("0.05"):
        raise ConfigError("report.numbers.relative_tolerance phải trong [0, 0.05]")
    forb = cfg.get("forbidden")
    if not isinstance(forb, dict) or set(forb) != set(FORBIDDEN_GROUPS):
        raise ConfigError(f"report.forbidden phải có đúng các nhóm {FORBIDDEN_GROUPS}")
    labels = cfg.get("indicator_labels") or {}
    if not isinstance(labels, dict):
        raise ConfigError("report.indicator_labels phải là mapping")
    ratio_metrics = cfg.get("ratio_metrics") or []
    if not isinstance(ratio_metrics, list):
        raise ConfigError("report.ratio_metrics phải là danh sách")
    return ReportSettings(
        top_k=_int(cfg.get("top_k"), "top_k", 1),
        max_attempts=_int(cfg.get("max_attempts"), "max_attempts", 1),
        on_exhausted=cfg["on_exhausted"],
        max_claims=_int(cfg.get("max_claims"), "max_claims", 1),
        max_claim_chars=_int(cfg.get("max_claim_chars"), "max_claim_chars", 20),
        relative_tolerance=tol,
        exempt_patterns=_patterns(nums.get("exempt_patterns"), "numbers.exempt_patterns"),
        indicator_units=_units(cfg.get("indicator_units"), "indicator_units"),
        tool_units=_units(cfg.get("tool_units"), "tool_units"),
        ratio_metrics=frozenset(str(m) for m in ratio_metrics),
        indicator_labels={str(k): str(v) for k, v in labels.items()},
        forbidden={g: _patterns(forb[g], f"forbidden.{g}") for g in FORBIDDEN_GROUPS},
        causal=_patterns(cfg.get("causal"), "causal"),
        hedges=_patterns(cfg.get("hedges"), "hedges"),
        disclaimer=_text(cfg.get("disclaimer"), "disclaimer"),
        draft_warning=_text(cfg.get("draft_warning"), "draft_warning"),
    )
