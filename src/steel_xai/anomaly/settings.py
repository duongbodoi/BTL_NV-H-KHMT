"""Đọc và kiểm `configs/anomaly.yaml` (và mốc năm từ `dataset.yaml`) cho Phase 5."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from steel_xai.config import ConfigError, load_config

_PERIOD_RE = re.compile(r"^FY\d{4}$")

INVENTORY = "inventory_increase"
GROSS_MARGIN = "gross_margin_change"

# Chỉ báo được phép dùng trong luật của từng loại (các chỉ báo thông tin khác chỉ để hiển thị).
RULE_INDICATORS = {
    INVENTORY: frozenset(
        {
            "inv_growth_yoy",
            "inv_minus_rev_growth",
            "dio_change_days",
            "inv_growth_vs_peer",
            "inv_growth_z",
            "inv_growth_mad_z",
        }
    ),
    GROSS_MARGIN: frozenset(
        {"gm_change_pp", "gm_change_vs_peer_pp", "gm_change_z", "gm_change_mad_z"}
    ),
}


ASOF_SOURCE_MANUAL = "manual"


def is_peer_indicator(name: str) -> bool:
    """Chỉ báo so với nhóm ngành: lưu thành thuộc tính phụ, không quyết định hướng."""
    return "vs_peer" in name


@dataclass(frozen=True)
class AsofEntry:
    date: str  # ISO
    source: str  # manual | audit_report_date | ...


@dataclass(frozen=True)
class Rule:
    indicator: str
    up: Decimal | None
    down: Decimal | None


@dataclass(frozen=True)
class TypeSettings:
    name: str
    requires: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class AnomalySettings:
    provisional_thresholds: bool
    types: tuple[TypeSettings, ...]
    min_changes_z: int
    min_changes_mad: int
    min_peers: int
    propagate_flags: frozenset[str]
    propagate_prefixes: tuple[str, ...]
    asof_dates: dict[str, dict[str, AsofEntry]]  # khai tay trong config (nguồn `manual`)
    companies: tuple[str, ...]  # công ty trong dataset.yaml (mẫu số của tỷ lệ bất thường)
    target_years: tuple[int, int]  # cửa sổ chọn ca [đầu, cuối] (dataset.case_selection_window)
    company_start_year: dict[str, int]  # năm bắt đầu riêng của từng công ty (dữ liệu bắt đầu muộn)
    reference_only_from_year: int  # từ năm này trở đi KHÔNG được dùng (spec mục 3.3, 7.2)


def _decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"{where} phải là số")
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise ConfigError(f"{where} phải là số, nhận {value!r}") from e
    if not d.is_finite():
        raise ConfigError(f"{where} phải hữu hạn")
    return d


def _int(value: Any, where: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{where} phải là số nguyên >= {minimum}, nhận {value!r}")
    return value


def load_anomaly_settings(
    cfg: dict[str, Any] | None = None, dataset: dict[str, Any] | None = None
) -> AnomalySettings:
    cfg = load_config("anomaly") if cfg is None else cfg
    dataset = load_config("dataset") if dataset is None else dataset

    prov = cfg.get("provisional_thresholds")
    if not isinstance(prov, bool):
        raise ConfigError("anomaly.provisional_thresholds phải là bool")
    if cfg.get("two_sided") is not True:
        raise ConfigError("anomaly.two_sided phải là true (spec mục 8.3)")
    if cfg.get("compare_order") != ["yoy"]:
        raise ConfigError("anomaly.compare_order phải là [yoy] (dữ liệu năm)")

    declared = cfg.get("anomaly_types")
    if (
        not isinstance(declared, list)
        or set(declared) != set(RULE_INDICATORS)
        or len(declared) != len(set(declared))
    ):
        raise ConfigError(f"anomaly.anomaly_types phải đúng {sorted(RULE_INDICATORS)}")

    types = []
    for name in declared:
        node = cfg.get(name)
        if not isinstance(node, dict):
            raise ConfigError(f"anomaly.{name} phải là mapping")
        allowed = RULE_INDICATORS[name]
        requires = node.get("requires")
        if requires not in allowed:
            raise ConfigError(f"anomaly.{name}.requires phải thuộc {sorted(allowed)}")
        raw_rules = node.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ConfigError(f"anomaly.{name}.rules phải là danh sách không rỗng")
        rules, seen = [], set()
        for r in raw_rules:
            ind = r.get("indicator") if isinstance(r, dict) else None
            if ind not in allowed or ind in seen:
                raise ConfigError(f"anomaly.{name}: chỉ báo lạ hoặc lặp: {ind!r}")
            seen.add(ind)
            up = None if r.get("up") is None else _decimal(r["up"], f"{name}.{ind}.up")
            down = None if r.get("down") is None else _decimal(r["down"], f"{name}.{ind}.down")
            if up is None and down is None:
                raise ConfigError(f"anomaly.{name}.{ind}: cần ít nhất một ngưỡng")
            if (up is not None and up <= 0) or (down is not None and down >= 0):
                raise ConfigError(f"anomaly.{name}.{ind}: up phải > 0 và down phải < 0")
            if cfg.get("two_sided") and (up is None or down is None):
                raise ConfigError(f"anomaly.{name}.{ind}: cần cả hai phía (two_sided)")
            rules.append(Rule(ind, up, down))
        if requires not in seen:
            raise ConfigError(f"anomaly.{name}.requires phải nằm trong rules")
        types.append(TypeSettings(name, requires, tuple(rules)))

    hist = cfg.get("history")
    peers = cfg.get("peers")
    if not isinstance(hist, dict) or not isinstance(peers, dict):
        raise ConfigError("anomaly.history và anomaly.peers phải là mapping")
    min_z = _int(hist.get("min_changes_z"), "history.min_changes_z", 3)  # std mẫu cần >= 3 điểm
    min_mad = _int(hist.get("min_changes_mad"), "history.min_changes_mad", 3)
    if min_mad < min_z:
        raise ConfigError("history.min_changes_mad phải >= min_changes_z")

    flags = cfg.get("propagate_flags", [])
    if not isinstance(flags, list) or not all(isinstance(f, str) and f for f in flags):
        raise ConfigError("anomaly.propagate_flags phải là danh sách chuỗi")

    companies = dataset.get("companies") or []
    if not isinstance(companies, list) or not all(isinstance(x, str) and x for x in companies):
        raise ConfigError("dataset.companies phải là danh sách chuỗi")
    asof_raw = cfg.get("asof_dates") or {}
    if not isinstance(asof_raw, dict):
        raise ConfigError("anomaly.asof_dates phải là mapping")
    asof: dict[str, dict[str, AsofEntry]] = {}
    for company, by_period in asof_raw.items():
        if not isinstance(by_period, dict):
            raise ConfigError(f"anomaly.asof_dates.{company} phải là mapping kỳ → ngày")
        if companies and company not in companies:
            raise ConfigError(f"asof_dates: công ty {company!r} không có trong dataset.companies")
        asof[str(company)] = {}
        for period, day in by_period.items():
            if not _PERIOD_RE.match(str(period)):
                raise ConfigError(f"asof_dates.{company}: kỳ phải dạng FYyyyy, nhận {period!r}")
            try:
                iso = dt.date.fromisoformat(str(day)).isoformat()
                asof[str(company)][str(period)] = AsofEntry(iso, ASOF_SOURCE_MANUAL)
            except ValueError as e:
                raise ConfigError(f"asof_dates.{company}.{period}: ngày ISO sai: {day!r}") from e

    window = dataset.get("case_selection_window")
    if not isinstance(window, dict):
        raise ConfigError("dataset.case_selection_window phải là mapping")
    start = _int(window.get("start_year"), "case_selection_window.start_year", 1)
    end = _int(window.get("end_year"), "case_selection_window.end_year", start)
    cutoff = _int(dataset.get("reference_only_from_year"), "dataset.reference_only_from_year", 1)
    if cutoff <= end:
        raise ConfigError("reference_only_from_year phải lớn hơn case_selection_window.end_year")
    starts_raw = window.get("company_start_year") or {}
    if not isinstance(starts_raw, dict):
        raise ConfigError("case_selection_window.company_start_year phải là mapping")
    starts: dict[str, int] = {}
    for company, year in starts_raw.items():
        if companies and company not in companies:
            raise ConfigError(f"company_start_year: công ty {company!r} không có trong companies")
        starts[str(company)] = _int(year, f"company_start_year.{company}", start)
        if starts[str(company)] > end:
            raise ConfigError(f"company_start_year.{company} lớn hơn end_year")

    return AnomalySettings(
        provisional_thresholds=prov,
        types=tuple(types),
        min_changes_z=min_z,
        min_changes_mad=min_mad,
        min_peers=_int(peers.get("min_peers"), "peers.min_peers", 1),
        propagate_flags=frozenset(f for f in flags if not f.endswith(":")),
        propagate_prefixes=tuple(f for f in flags if f.endswith(":")),
        asof_dates=asof,
        companies=tuple(companies),
        target_years=(start, end),
        company_start_year=starts,
        reference_only_from_year=cutoff,
    )
