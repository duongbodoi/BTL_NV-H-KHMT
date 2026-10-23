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
# Cờ mà Phase 2 có thể gắn; `blocking_flags` gõ sai tên phải báo lỗi, không im lặng bỏ chặn.
KNOWN_FLAGS = frozenset(
    v
    for mod in (_tables, _structured)
    for k, v in vars(mod).items()
    if k.startswith("FLAG_") and isinstance(v, str)
)

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


def _kw(text: str) -> str:
    return normalize_space(text).casefold()


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
    magnitude: bool = False


@dataclass(frozen=True)
class DerivedDef:
    name: str
    numerator: str
    denominator: str


@dataclass(frozen=True)
class DocumentUnitSettings:
    unit: str
    statements: tuple[str, ...]
    scaled_statements: tuple[str, ...]


@dataclass(frozen=True)
class NormalizationSettings:
    unit_scales: dict[str, Decimal]
    blocking_flags: frozenset[str]
    non_annual_keywords: tuple[str, ...]
    annual_keywords: tuple[str, ...]
    current_period_keywords: tuple[str, ...]
    prior_period_keywords: tuple[str, ...]
    document_unit: DocumentUnitSettings
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
    period_lists = {
        k: tuple(_kw(x) for x in _str_list(periods, k, "periods", required=True))
        for k in (
            "non_annual_keywords",
            "annual_keywords",
            "current_period_keywords",
            "prior_period_keywords",
        )
    }
    for k, v in period_lists.items():
        if not all(v):
            raise ConfigError(f"periods.{k} có chuỗi rỗng")
    if set(period_lists["current_period_keywords"]) & set(period_lists["prior_period_keywords"]):
        raise ConfigError("current_period_keywords và prior_period_keywords giao nhau")

    du = _mapping(cfg, "document_unit", "normalization")
    doc_unit = DocumentUnitSettings(
        unit=str(du.get("unit")),
        statements=tuple(
            _kw(x) for x in _str_list(du, "statements", "document_unit", required=True)
        ),
        scaled_statements=tuple(
            _kw(x) for x in _str_list(du, "scaled_statements", "document_unit", required=True)
        ),
    )
    if doc_unit.unit not in scales:
        raise ConfigError(f"document_unit.unit {doc_unit.unit!r} không có trong unit_scales")
    if not all(doc_unit.statements) or not all(doc_unit.scaled_statements):
        raise ConfigError("document_unit có chuỗi rỗng")

    res = _mapping(cfg, "resolution", "normalization")
    try:
        tol = Decimal(str(res.get("relative_tolerance")))
    except InvalidOperation as e:
        raise ConfigError("resolution.relative_tolerance phải là số") from e
    if not tol.is_finite() or not 0 <= tol < 1:
        raise ConfigError("resolution.relative_tolerance phải thuộc [0, 1)")

    metrics: dict[str, MetricDef] = {}
    seen: dict[tuple[str, str], list[str]] = {}  # (nguồn, alias) → các metric dùng chung nhãn
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
        codes = frozenset(_str_list(raw, "codes", name, required=False))
        for source, group in (("ocr", aliases), ("structured", s_aliases)):
            for a in group:
                if not a:
                    raise ConfigError(f"metrics.{name}: alias rỗng")
                for other in seen.get((source, a), []):
                    # Dùng chung nhãn chỉ khi hai metric phân biệt được bằng mã số.
                    o = metrics[other]
                    if source == "structured" or not codes or not o.codes or codes & o.codes:
                        raise ConfigError(f"alias {a!r} trùng giữa {other} và {name} ({source})")
                seen.setdefault((source, a), []).append(str(name))
        sign = raw.get("expected_sign")
        if sign is not None and sign not in SIGNS:
            raise ConfigError(f"metrics.{name}.expected_sign phải thuộc {SIGNS}")
        flip = raw.get("flip_sign", False)
        magnitude = raw.get("magnitude", False)
        if not isinstance(flip, bool) or not isinstance(magnitude, bool):
            raise ConfigError(f"metrics.{name}.flip_sign/magnitude phải là bool")
        if flip and sign is None:
            raise ConfigError(f"metrics.{name}: flip_sign cần expected_sign")
        if magnitude and (flip or sign is not None):
            raise ConfigError(f"metrics.{name}: magnitude không đi cùng expected_sign/flip_sign")
        col_inc = tuple(_kw(x) for x in _str_list(raw, "column_includes", name, required=False))
        col_exc = tuple(_kw(x) for x in _str_list(raw, "column_excludes", name, required=False))
        if not all(col_inc) or not all(col_exc):
            raise ConfigError(f"metrics.{name}: column_includes/column_excludes có chuỗi rỗng")
        metrics[str(name)] = MetricDef(
            name=str(name),
            kind=kind,
            aliases=aliases,
            structured_aliases=s_aliases,
            codes=codes,
            column_includes=col_inc,
            column_excludes=col_exc,
            requires_table_labels=frozenset(
                norm_label(x) for x in _str_list(raw, "requires_table_labels", name, required=False)
            ),
            expected_sign=sign,
            flip_sign=flip,
            magnitude=magnitude,
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
        non_annual_keywords=period_lists["non_annual_keywords"],
        annual_keywords=period_lists["annual_keywords"],
        current_period_keywords=period_lists["current_period_keywords"],
        prior_period_keywords=period_lists["prior_period_keywords"],
        document_unit=doc_unit,
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
