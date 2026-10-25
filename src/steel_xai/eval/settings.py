"""Cấu hình đánh giá (configs/eval.yaml), kiểm chặt: ca/nhãn/freeze (12a), runner (12)."""

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


# ---------------- Phase 12: runner

BASE_CONFIGS = ("A", "B", "C")
SECONDARY_CONFIGS = ("A_lib", "B_lib")
ABLATIONS = ("no_rag", "no_verification", "no_library")
ALL_CONFIGS = (*BASE_CONFIGS, *SECONDARY_CONFIGS, *ABLATIONS)


@dataclass(frozen=True)
class RunnerSettings:
    runs_per_case: int
    configs: tuple[str, ...]  # theo thứ tự chạy: A, B, C, chạy phụ, ablation
    output_dir: Path
    sheets_dir: Path
    max_hypotheses: int
    baseline_queries: dict[str, tuple[str, ...]]
    max_passages: int
    other_counts_as_hit: bool
    top_ks: tuple[int, ...]
    citation_ratings: tuple[str, ...]
    unverified_causes: tuple[str, ...]
    bootstrap_samples: int
    bootstrap_seed: int
    bootstrap_level: float
    currency: str
    input_per_mtok: float | None
    output_per_mtok: float | None
    disclaimer: str

    def run_ids(self) -> tuple[str, ...]:
        return tuple(f"run{i}" for i in range(1, self.runs_per_case + 1))


def _price(v: Any, where: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise ConfigError(f"eval.runner.{where} phải là số >= 0 hoặc null")
    return float(v)


def load_runner_settings(cfg: dict[str, Any] | None = None) -> RunnerSettings:
    cfg = load_config("eval") if cfg is None else cfg
    r = cfg.get("runner")
    if not isinstance(r, dict):
        raise ConfigError("eval.runner phải là mapping")
    configs = [
        *_str_list(cfg.get("configs"), "configs"),
        *_str_list(cfg.get("secondary_configs"), "secondary_configs"),
        *_str_list(cfg.get("ablations"), "ablations"),
    ]
    unknown = [c for c in configs if c not in ALL_CONFIGS]
    if unknown or len(set(configs)) != len(configs):
        raise ConfigError(f"eval.configs/secondary_configs/ablations: tên lạ hoặc lặp {unknown}")
    if "supports" not in (r.get("citation_ratings") or []):
        raise ConfigError("eval.runner.citation_ratings phải có 'supports'")
    if not set(BASE_CONFIGS) <= set(configs):
        raise ConfigError(f"eval.configs phải gồm {list(BASE_CONFIGS)} (spec mục 14)")
    queries = r.get("baseline_queries")
    if not isinstance(queries, dict) or not queries:
        raise ConfigError("eval.runner.baseline_queries phải là mapping loại bất thường → truy vấn")
    top_ks = r.get("top_ks")
    if not isinstance(top_ks, list) or not top_ks:
        raise ConfigError("eval.runner.top_ks phải là danh sách số nguyên")
    boot = r.get("bootstrap")
    if not isinstance(boot, dict):
        raise ConfigError("eval.runner.bootstrap phải là mapping")
    level = boot.get("level")
    if isinstance(level, bool) or not isinstance(level, float) or not 0.5 <= level < 1:
        raise ConfigError("eval.runner.bootstrap.level phải là số trong [0.5, 1)")
    other = r.get("other_counts_as_hit")
    if not isinstance(other, bool):
        raise ConfigError("eval.runner.other_counts_as_hit phải là true/false")
    pricing = r.get("pricing")
    if not isinstance(pricing, dict):
        raise ConfigError("eval.runner.pricing phải là mapping")
    currency = pricing.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise ConfigError("eval.runner.pricing.currency phải là chuỗi")
    disclaimer = cfg.get("disclaimer")
    if not isinstance(disclaimer, str) or not disclaimer.strip():
        raise ConfigError("eval.disclaimer phải là chuỗi không rỗng (spec mục 15)")
    return RunnerSettings(
        runs_per_case=_int(cfg.get("runs_per_case"), "runs_per_case", 1),
        configs=tuple(c for c in ALL_CONFIGS if c in configs),
        output_dir=_path(r.get("output_dir"), "runner.output_dir"),
        sheets_dir=_path(r.get("sheets_dir"), "runner.sheets_dir"),
        max_hypotheses=_int(r.get("max_hypotheses"), "runner.max_hypotheses", 3),
        baseline_queries={
            str(k): _str_list(v, f"runner.baseline_queries.{k}") for k, v in queries.items()
        },
        max_passages=_int(r.get("max_passages"), "runner.max_passages", 1),
        other_counts_as_hit=other,
        top_ks=tuple(sorted({_int(k, "runner.top_ks", 1) for k in top_ks})),
        citation_ratings=_str_list(r.get("citation_ratings"), "runner.citation_ratings"),
        unverified_causes=_str_list(r.get("unverified_causes"), "runner.unverified_causes"),
        bootstrap_samples=_int(boot.get("samples"), "runner.bootstrap.samples", 100),
        bootstrap_seed=_int(boot.get("seed"), "runner.bootstrap.seed", 0),
        bootstrap_level=level,
        currency=currency.strip(),
        input_per_mtok=_price(pricing.get("input_per_mtok"), "pricing.input_per_mtok"),
        output_per_mtok=_price(pricing.get("output_per_mtok"), "pricing.output_per_mtok"),
        disclaimer=disclaimer.strip(),
    )
