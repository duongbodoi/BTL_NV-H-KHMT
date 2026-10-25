"""Cấu hình đánh giá (configs/eval.yaml), kiểm chặt. Phase 12a chỉ dùng phần ca/nhãn/freeze."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steel_xai.config import PROJECT_ROOT, ConfigError, load_config


@dataclass(frozen=True)
class EvalSettings:
    split: dict[str, int]  # số ca mỗi phần, ví dụ {"dev": 5, "test": 13}
    cases_path: Path
    labels_dir: Path
    adjudication_path: Path
    annotators: int
    other_id: str
    min_source_title_chars: int
    failure_causes: tuple[str, ...]
    freeze_file: Path
    freeze_globs: tuple[str, ...]
    min_unfreeze_reason_chars: int
    disclaimer: str


def _int(v: Any, where: str, lo: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise ConfigError(f"eval.{where} phải là số nguyên >= {lo}, nhận {v!r}")
    return v


def _path(v: Any, where: str) -> Path:
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"eval.{where} phải là đường dẫn")
    p = Path(v)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _str_list(v: Any, where: str) -> tuple[str, ...]:
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"eval.{where} phải là danh sách chuỗi không rỗng")
    return tuple(x.strip() for x in v)


def load_eval_settings(cfg: dict[str, Any] | None = None) -> EvalSettings:
    cfg = load_config("eval") if cfg is None else cfg
    split = cfg.get("split")
    if not isinstance(split, dict) or set(split) != {"dev", "test"}:
        raise ConfigError("eval.split phải có đúng hai khóa dev và test")
    paths = cfg.get("paths")
    if not isinstance(paths, dict):
        raise ConfigError("eval.paths phải là mapping")
    other = cfg.get("other_id")
    if not isinstance(other, str) or not other.strip():
        raise ConfigError("eval.other_id phải là chuỗi không rỗng")
    disclaimer = cfg.get("disclaimer")
    if not isinstance(disclaimer, str) or not disclaimer.strip():
        raise ConfigError("eval.disclaimer phải là chuỗi không rỗng (spec mục 15)")
    return EvalSettings(
        split={k: _int(split[k], f"split.{k}", 1) for k in ("dev", "test")},
        cases_path=_path(paths.get("cases"), "paths.cases"),
        labels_dir=_path(paths.get("labels_dir"), "paths.labels_dir"),
        adjudication_path=_path(paths.get("adjudication"), "paths.adjudication"),
        annotators=_int(cfg.get("annotators"), "annotators", 2),
        other_id=other.strip(),
        min_source_title_chars=_int(cfg.get("min_source_title_chars"), "min_source_title_chars", 1),
        failure_causes=_str_list(cfg.get("failure_causes"), "failure_causes"),
        freeze_file=_path(cfg.get("freeze_file"), "freeze_file"),
        freeze_globs=_str_list(cfg.get("freeze_globs"), "freeze_globs"),
        min_unfreeze_reason_chars=_int(
            cfg.get("min_unfreeze_reason_chars"), "min_unfreeze_reason_chars", 1
        ),
        disclaimer=disclaimer.strip(),
    )
