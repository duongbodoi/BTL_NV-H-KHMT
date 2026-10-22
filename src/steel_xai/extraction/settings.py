"""Đọc và kiểm `configs/extraction.yaml` thành cấu trúc có kiểu cho Phase 2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import ValueStatus
from steel_xai.extraction.numbers import NumberFormat, normalize_space

LAYOUTS = ("wide", "long")
COLUMN_ROLES = ("code", "note", "index")
STRUCTURED_COLUMNS = ("company", "year", "quarter", "item", "value", "unit")


@dataclass(frozen=True)
class NumberSettings:
    dash_tokens: tuple[str, ...]
    dash_status: ValueStatus


@dataclass(frozen=True)
class TableSettings:
    numbers: NumberSettings
    column_roles: dict[str, tuple[str, ...]]  # role → từ khóa (đã chuẩn hóa, chữ thường)
    units: dict[str, str]  # nhãn (đã chuẩn hóa, chữ thường) → mã đơn vị
    unit_statement_prefixes: tuple[str, ...]
    restated_keywords: tuple[str, ...]
    scale_abbreviations: tuple[str, ...] = ()
    foreign_currency_tokens: tuple[str, ...] = ()
    relative_period_keywords: tuple[str, ...] = ()
    cumulative_keywords: tuple[str, ...] = ()
    period_words: tuple[str, ...] = ()


@dataclass(frozen=True)
class StructuredSettings:
    numbers: NumberSettings
    layout: str
    number_format: NumberFormat
    missing_tokens: tuple[str, ...]
    columns: dict[str, str | None]
    unit: str | None
    ignore_columns: tuple[str, ...] = ()


def norm_key(text: str) -> str:
    return normalize_space(text).casefold()


def load_table_settings(cfg: dict[str, Any] | None = None) -> TableSettings:
    cfg = load_config("extraction") if cfg is None else cfg
    t = _section(cfg, "table")
    roles = _mapping(t, "column_roles", "table")
    unknown = set(roles) - set(COLUMN_ROLES)
    if unknown:
        raise ConfigError(f"extraction.table.column_roles có vai trò lạ: {sorted(unknown)}")
    units = _mapping(t, "units", "table")
    if not all(isinstance(k, str) and isinstance(v, str) and k.strip() for k, v in units.items()):
        raise ConfigError("extraction.table.units phải là mapping chuỗi → chuỗi")
    return TableSettings(
        numbers=_numbers(cfg),
        column_roles={
            r: tuple(norm_key(k) for k in _str_list(roles, r, "table.column_roles")) for r in roles
        },
        units={norm_key(k): v for k, v in units.items()},
        unit_statement_prefixes=tuple(
            norm_key(p) for p in _str_list(t, "unit_statement_prefixes", "table")
        ),
        restated_keywords=tuple(norm_key(k) for k in _str_list(t, "restated_keywords", "table")),
        **{
            key: tuple(norm_key(k) for k in _str_list(t, key, "table"))
            for key in (
                "scale_abbreviations",
                "foreign_currency_tokens",
                "relative_period_keywords",
                "cumulative_keywords",
                "period_words",
            )
        },
    )


def load_structured_settings(cfg: dict[str, Any] | None = None) -> StructuredSettings:
    cfg = load_config("extraction") if cfg is None else cfg
    s = _section(cfg, "structured")
    layout = s.get("layout")
    if layout not in LAYOUTS:
        raise ConfigError(f"extraction.structured.layout phải thuộc {LAYOUTS}, nhận {layout!r}")
    number_format = s.get("number_format")
    if number_format not in ("vn", "plain"):
        raise ConfigError(f"extraction.structured.number_format không hợp lệ: {number_format!r}")
    columns = _mapping(s, "columns", "structured")
    unknown = set(columns) - set(STRUCTURED_COLUMNS)
    if unknown:
        raise ConfigError(f"extraction.structured.columns có khóa lạ: {sorted(unknown)}")
    raw_cols = {k: columns.get(k) for k in STRUCTURED_COLUMNS}
    if any(v is not None and not isinstance(v, str) for v in raw_cols.values()):
        raise ConfigError("extraction.structured.columns: tên cột phải là chuỗi hoặc null")
    # Tên cột chuẩn hóa NFC như header của file.
    cols: dict[str, str | None] = {
        k: normalize_space(v) if v else None for k, v in raw_cols.items()
    }
    required = ("company", "year") + (("item", "value") if layout == "long" else ())
    missing = [k for k in required if not cols[k]]
    if missing:
        raise ConfigError(f"extraction.structured.columns thiếu cột bắt buộc: {missing}")
    unit = s.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise ConfigError("extraction.structured.unit phải là chuỗi hoặc null")
    return StructuredSettings(
        numbers=_numbers(cfg),
        layout=layout,
        number_format=number_format,
        missing_tokens=tuple(_str_list(s, "missing_tokens", "structured")),
        columns=cols,
        unit=unit,
        ignore_columns=tuple(
            normalize_space(c) for c in _str_list(s, "ignore_columns", "structured")
        ),
    )


def _numbers(cfg: dict[str, Any]) -> NumberSettings:
    n = _section(cfg, "numbers")
    try:
        status = ValueStatus(n.get("dash_status"))
    except ValueError:
        raise ConfigError(
            f"extraction.numbers.dash_status không hợp lệ: {n.get('dash_status')!r}"
        ) from None
    if status == ValueStatus.OK:
        raise ConfigError("extraction.numbers.dash_status không được là OK (dấu gạch không phải 0)")
    tokens = tuple(normalize_space(t) for t in _str_list(n, "dash_tokens", "numbers"))
    if not all(tokens) or any(any(ch.isdigit() for ch in t) for t in tokens):
        raise ConfigError("extraction.numbers.dash_tokens không được rỗng hoặc chứa chữ số")
    return NumberSettings(dash_tokens=tokens, dash_status=status)


def _section(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    sec = cfg.get(name)
    if not isinstance(sec, dict):
        raise ConfigError(f"extraction config thiếu mục `{name}` (mapping)")
    return sec


def _mapping(d: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    v = d.get(key)
    if not isinstance(v, dict):
        raise ConfigError(f"extraction.{where}.{key} phải là mapping")
    return v


def _str_list(d: dict[str, Any], key: str, where: str) -> list[str]:
    v = d.get(key)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"extraction.{where}.{key} phải là danh sách chuỗi")
    return v
