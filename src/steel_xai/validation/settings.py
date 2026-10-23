"""Đọc và kiểm `configs/validation.yaml` thành cấu trúc có kiểu cho Phase 4."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from steel_xai.config import ConfigError, load_config

KINDS = ("identity", "upper_bound")


@dataclass(frozen=True)
class Term:
    metric: str
    coef: int


@dataclass(frozen=True)
class Relation:
    id: str
    kind: str
    target: str
    terms: tuple[Term, ...]


@dataclass(frozen=True)
class ValidationSettings:
    nil_as_zero: bool
    relative_tolerance: Decimal
    non_negative: frozenset[str]
    relations: tuple[Relation, ...]
    sample_seed: int
    match_tolerance: Decimal


def _decimal(value: Any, where: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation as e:
        raise ConfigError(f"{where} phải là số, nhận {value!r}") from e
    if not d.is_finite() or not 0 <= d < 1:
        raise ConfigError(f"{where} phải thuộc [0, 1), nhận {value!r}")
    return d


def load_validation_settings(
    cfg: dict[str, Any] | None = None, known_metrics: frozenset[str] | None = None
) -> ValidationSettings:
    """Mọi metric trong relation/non_negative phải có trong normalization (`known_metrics`, mặc định
    đọc từ configs/normalization.yaml) để gõ sai tên bị bắt ngay."""
    cfg = load_config("validation") if cfg is None else cfg
    if known_metrics is None:
        from steel_xai.normalization.settings import load_normalization_settings

        known_metrics = frozenset(load_normalization_settings().metrics)
    nil = cfg.get("nil_as_zero")
    if not isinstance(nil, bool):
        raise ConfigError("validation.nil_as_zero phải là bool")
    tol = _decimal(cfg.get("relative_tolerance"), "validation.relative_tolerance")

    nn = cfg.get("non_negative", [])
    if not isinstance(nn, list) or not all(isinstance(x, str) for x in nn):
        raise ConfigError("validation.non_negative phải là danh sách chuỗi")

    relations: list[Relation] = []
    ids: set[str] = set()
    raw_relations = cfg.get("relations")
    if not isinstance(raw_relations, list) or not raw_relations:
        raise ConfigError("validation.relations phải là danh sách không rỗng")
    for raw in raw_relations:
        if not isinstance(raw, dict):
            raise ConfigError("mỗi validation.relations phải là mapping")
        rid, kind, target = raw.get("id"), raw.get("kind"), raw.get("target")
        if not isinstance(rid, str) or not rid or rid in ids:
            raise ConfigError(f"relation id thiếu hoặc trùng: {rid!r}")
        ids.add(rid)
        if kind not in KINDS:
            raise ConfigError(f"relation {rid}: kind phải thuộc {KINDS}, nhận {kind!r}")
        if not isinstance(target, str) or not target:
            raise ConfigError(f"relation {rid}: thiếu target")
        terms_raw = raw.get("terms")
        if not isinstance(terms_raw, list) or not terms_raw:
            raise ConfigError(f"relation {rid}: terms phải là danh sách không rỗng")
        terms = []
        for t in terms_raw:
            coef = t.get("coef") if isinstance(t, dict) else None
            metric = t.get("metric") if isinstance(t, dict) else None
            if not isinstance(metric, str) or isinstance(coef, bool) or coef not in (1, -1):
                raise ConfigError(f"relation {rid}: term cần metric và coef ∈ {{1, -1}}")
            terms.append(Term(metric, coef))
        names = [t.metric for t in terms]
        if target in names or len(set(names)) != len(names):
            raise ConfigError(f"relation {rid}: target hoặc metric bị lặp trong terms")
        relations.append(Relation(rid, kind, target, tuple(terms)))

    if known_metrics is not None:
        used = (
            set(nn) | {r.target for r in relations} | {t.metric for r in relations for t in r.terms}
        )
        unknown = used - known_metrics
        if unknown:
            raise ConfigError(
                f"validation dùng metric không có trong normalization: {sorted(unknown)}"
            )

    rec = cfg.get("reconciliation")
    if not isinstance(rec, dict):
        raise ConfigError("validation.reconciliation phải là mapping")
    seed = rec.get("sample_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ConfigError("reconciliation.sample_seed phải là số nguyên")
    return ValidationSettings(
        nil_as_zero=nil,
        relative_tolerance=tol,
        non_negative=frozenset(nn),
        relations=tuple(relations),
        sample_seed=seed,
        match_tolerance=_decimal(rec.get("match_tolerance"), "reconciliation.match_tolerance"),
    )
