"""Cấu hình kiểm chứng định lượng (configs/verification.yaml), kiểm chặt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steel_xai.config import PROJECT_ROOT, ConfigError, load_config


@dataclass(frozen=True)
class SeriesSettings:
    name: str
    path: Path
    lag_months: int


@dataclass(frozen=True)
class VerificationSettings:
    publication_lag_days: int
    window_months: int
    min_months: int
    percentile_min_months: int
    percentile_lookback_years: int
    series: dict[str, SeriesSettings]
    history_min_changes: int
    min_peers: int
    reference_only_from_year: int
    contra_metrics: frozenset[str]
    known_metrics: frozenset[str]
    applicable_directions: dict[str, tuple[str, ...]]


def _int(v: Any, where: str, minimum: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise ConfigError(f"{where} phải là số nguyên >= {minimum}, nhận {v!r}")
    return v


def load_verification_settings(
    cfg: dict[str, Any] | None = None,
    dataset: dict[str, Any] | None = None,
    normalization: dict[str, Any] | None = None,
) -> VerificationSettings:
    cfg = load_config("verification") if cfg is None else cfg
    dataset = load_config("dataset") if dataset is None else dataset
    normalization = load_config("normalization") if normalization is None else normalization
    ext = cfg.get("external")
    if not isinstance(ext, dict):
        raise ConfigError("verification.external phải là mapping")
    window = _int(ext.get("window_months"), "verification.external.window_months", 1)
    min_months = _int(ext.get("min_months"), "verification.external.min_months", 1)
    if min_months > window:
        raise ConfigError("verification.external.min_months không được lớn hơn window_months")
    raw = ext.get("series")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("verification.external.series phải là mapping không rỗng")
    series: dict[str, SeriesSettings] = {}
    for name, s in raw.items():
        if not isinstance(s, dict) or not isinstance(s.get("file"), str) or not s["file"].strip():
            raise ConfigError(f"verification.external.series.{name}.file phải là chuỗi")
        p = Path(s["file"])
        series[name] = SeriesSettings(
            name=name,
            path=p if p.is_absolute() else PROJECT_ROOT / p,
            lag_months=_int(
                s.get("lag_months"), f"verification.external.series.{name}.lag_months", 0
            ),
        )
    lists: dict[str, list[str]] = {}
    for key in ("contra_metrics", "planned_metrics"):
        v = cfg.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise ConfigError(f"verification.{key} phải là danh sách chuỗi không rỗng")
        lists[key] = v
    metrics = set(normalization.get("metrics") or {}) | set(normalization.get("derived") or {})
    known = frozenset(metrics | set(lists["planned_metrics"]))
    if not set(lists["contra_metrics"]) <= known:
        raise ConfigError("verification.contra_metrics có metric không biết")
    directions = cfg.get("applicable_directions")
    if not isinstance(directions, dict) or not directions:
        raise ConfigError("verification.applicable_directions phải là mapping không rỗng")
    for t, ds in directions.items():
        if not isinstance(ds, list) or not ds or not set(ds) <= {"up", "down"}:
            raise ConfigError(
                f"verification.applicable_directions.{t} phải là danh sách con của [up, down]"
            )
    hist = cfg.get("history") or {}
    peers = cfg.get("peers") or {}
    ref = dataset.get("reference_only_from_year")
    if isinstance(ref, bool) or not isinstance(ref, int):
        raise ConfigError("dataset.reference_only_from_year phải là số nguyên")
    return VerificationSettings(
        publication_lag_days=_int(
            ext.get("publication_lag_days"), "verification.external.publication_lag_days", 0
        ),
        window_months=window,
        min_months=min_months,
        percentile_min_months=_int(
            ext.get("percentile_min_months"), "verification.external.percentile_min_months", 1
        ),
        percentile_lookback_years=_int(
            ext.get("percentile_lookback_years"),
            "verification.external.percentile_lookback_years",
            1,
        ),
        series=series,
        history_min_changes=_int(hist.get("min_changes"), "verification.history.min_changes", 2),
        min_peers=_int(peers.get("min_peers"), "verification.peers.min_peers", 1),
        reference_only_from_year=ref,
        contra_metrics=frozenset(lists["contra_metrics"]),
        known_metrics=known,
        applicable_directions={t: tuple(ds) for t, ds in directions.items()},
    )
