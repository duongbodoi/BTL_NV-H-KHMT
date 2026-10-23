"""Đọc và kiểm `configs/normalization.yaml` thành cấu trúc có kiểu cho Phase 3."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from steel_xai.config import ConfigError, load_config
from steel_xai.extraction import structured as _structured
from steel_xai.extraction import tables as _tables
from steel_xai.extraction.numbers import normalize_space

KINDS = ("stock", "flow")
SIGNS = ("negative", "positive")
QUARTER_END_MONTHS = frozenset({3, 6, 9, 12})
# Cờ mà Phase 2 có thể gắn; `blocking_flags` gõ sai tên phải báo lỗi, không im lặng bỏ chặn.
KNOWN_FLAGS = frozenset(
    v
    for mod in (_tables, _structured)
    for k, v in vars(mod).items()
    if k.startswith("FLAG_") and isinstance(v, str)
) | {"ragged_rows"}

_LEADING_ENUM_RE = re.compile(r"^(?:[ivxlc]+|[a-z]|\d+)[.)]\s+", re.IGNORECASE)
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")


def norm_label(text: str) -> str:
    """Chuẩn hóa nhãn dòng để khớp trọn: bỏ số thứ tự đầu dòng, chú thích/công thức cuối dòng."""
    t = normalize_space(text).casefold()
    t = _LEADING_ENUM_RE.sub("", t)
    while True:
        stripped = _TRAILING_PAREN_RE.sub("", t)
        if stripped == t or not stripped:
            break
        t = stripped
    return t.strip(" :;.")


@dataclass(frozen=True)
class MetricDef:
    name: str
    kind: str
    aliases: frozenset[str]
    structured_aliases: frozenset[str]
    codes: frozenset[str]
    column_includes: tuple[str, ...]
    column_excludes: tuple[str, ...]
    requires_table_labels: frozenset[str]
    expected_sign: str | None
    flip_sign: bool


@dataclass(frozen=True)
class DerivedDef:
    name: str
    numerator: str
    denominator: str


@dataclass(frozen=True)
class NormalizationSettings:
    unit_scales: dict[str, Decimal]
    blocking_flags: frozenset[str]
    quarter_end_months: frozenset[int]
    non_annual_keywords: tuple[str, ...]
    annual_keywords: tuple[str, ...]
    relative_tolerance: Decimal
    metrics: dict[str, MetricDef]
    derived: dict[str, DerivedDef]


def load_normalization_settings(cfg: dict[str, Any] | None = None) -> NormalizationSettings:
    cfg = load_config("normalization") if cfg is None else cfg

    scales_raw = _mapping(cfg, "unit_scales", "normalization")
    scales: dict[str, Decimal] = {}
    for unit, scale in scales_raw.items():
        try:
            d = Decimal(str(scale))
        except InvalidOperation as e:
            raise ConfigError(f"unit_scales.{unit} không phải số: {scale!r}") from e
        if not d.is_finite() or d <= 0:
            raise ConfigError(f"unit_scales.{unit} phải > 0, nhận {scale!r}")
        scales[str(unit)] = d
    if scales.get("VND") != 1:
        raise ConfigError("unit_scales.VND phải bằng 1 (đơn vị đích)")

    periods = _mapping(cfg, "periods", "normalization")
    months = periods.get("quarter_end_months")
    if (
        not isinstance(months, list)
        or not months
        or not all(isinstance(m, int) and not isinstance(m, bool) for m in months)
        or not set(months) <= QUARTER_END_MONTHS
    ):
        raise ConfigError(
            f"periods.quarter_end_months phải là tập con của {sorted(QUARTER_END_MONTHS)}"
        )

    res = _mapping(cfg, "resolution", "normalization")
    try:
        tol = Decimal(str(res.get("relative_tolerance")))
    except InvalidOperation as e:
        raise ConfigError("resolution.relative_tolerance phải là số") from e
    if not tol.is_finite() or not 0 <= tol < 1:
        raise ConfigError("resolution.relative_tolerance phải thuộc [0, 1)")

    metrics: dict[str, MetricDef] = {}
    seen_alias: dict[tuple[str, str], str] = {}
    for name, raw in _mapping(cfg, "metrics", "normalization").items():
        if not isinstance(raw, dict):
            raise ConfigError(f"metrics.{name} phải là mapping")
        kind = raw.get("kind")
        if kind not in KINDS:
            raise ConfigError(f"metrics.{name}.kind phải thuộc {KINDS}, nhận {kind!r}")
        aliases = frozenset(norm_label(a) for a in _str_list(raw, "aliases", name, required=True))
        s_aliases = frozenset(
            norm_label(a) for a in _str_list(raw, "structured_aliases", name, required=False)
        )
        for source, group in (("ocr", aliases), ("structured", s_aliases)):
            for a in group:
                if not a:
                    raise ConfigError(f"metrics.{name}: alias rỗng")
                if (source, a) in seen_alias:
                    raise ConfigError(
                        f"alias {a!r} trùng giữa {seen_alias[(source, a)]} và {name} ({source})"
                    )
                seen_alias[(source, a)] = name
        sign = raw.get("expected_sign")
        if sign is not None and sign not in SIGNS:
            raise ConfigError(f"metrics.{name}.expected_sign phải thuộc {SIGNS}")
        flip = raw.get("flip_sign", False)
        if not isinstance(flip, bool):
            raise ConfigError(f"metrics.{name}.flip_sign phải là bool")
        if flip and sign is None:
            raise ConfigError(f"metrics.{name}: flip_sign cần expected_sign")
        col_inc = tuple(
            normalize_space(x).casefold()
            for x in _str_list(raw, "column_includes", name, required=False)
        )
        col_exc = tuple(
            normalize_space(x).casefold()
            for x in _str_list(raw, "column_excludes", name, required=False)
        )
        if not all(col_inc) or not all(col_exc):
            raise ConfigError(f"metrics.{name}: column_includes/column_excludes có chuỗi rỗng")
        metrics[str(name)] = MetricDef(
            name=str(name),
            kind=kind,
            aliases=aliases,
            structured_aliases=s_aliases,
            codes=frozenset(_str_list(raw, "codes", name, required=False)),
            column_includes=col_inc,
            column_excludes=col_exc,
            requires_table_labels=frozenset(
                norm_label(x) for x in _str_list(raw, "requires_table_labels", name, required=False)
            ),
            expected_sign=sign,
            flip_sign=flip,
        )
    if not metrics:
        raise ConfigError("normalization.metrics trống")

    derived: dict[str, DerivedDef] = {}
    for name, raw in _mapping(cfg, "derived", "normalization").items():
        if not isinstance(raw, dict):
            raise ConfigError(f"derived.{name} phải là mapping")
        num, den = raw.get("numerator"), raw.get("denominator")
        for role, ref in (("numerator", num), ("denominator", den)):
            if ref not in metrics:
                raise ConfigError(f"derived.{name}.{role} không phải metric đã khai: {ref!r}")
        if name in metrics:
            raise ConfigError(f"derived.{name} trùng tên metric")
        if metrics[num].kind != metrics[den].kind:
            raise ConfigError(f"derived.{name}: tử và mẫu phải cùng kind (stock/flow)")
        derived[str(name)] = DerivedDef(str(name), num, den)

    flags = cfg.get("blocking_flags")
    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
        raise ConfigError("blocking_flags phải là danh sách chuỗi")
    unknown = set(flags) - KNOWN_FLAGS
    if unknown:
        raise ConfigError(f"blocking_flags có cờ không tồn tại ở Phase 2: {sorted(unknown)}")

    return NormalizationSettings(
        unit_scales=scales,
        blocking_flags=frozenset(flags),
        quarter_end_months=frozenset(months),
        non_annual_keywords=tuple(
            normalize_space(k).casefold()
            for k in _str_list(periods, "non_annual_keywords", "periods", required=True)
        ),
        annual_keywords=tuple(
            normalize_space(k).casefold()
            for k in _str_list(periods, "annual_keywords", "periods", required=True)
        ),
        relative_tolerance=tol,
        metrics=metrics,
        derived=derived,
    )


def _mapping(d: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    v = d.get(key)
    if not isinstance(v, dict):
        raise ConfigError(f"{where}.{key} phải là mapping")
    return v


def _str_list(d: dict[str, Any], key: str, where: str, *, required: bool) -> list[str]:
    v = d.get(key)
    if v is None and not required:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{where}.{key} phải là danh sách chuỗi")
    if required and not v:
        raise ConfigError(f"{where}.{key} không được rỗng")
    return list(v)
