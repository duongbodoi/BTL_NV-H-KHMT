"""Cấu hình LLM (configs/llm.yaml), kiểm chặt. Không có temperature/top_p/top_k (API từ chối)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steel_xai.config import PROJECT_ROOT, ConfigError, load_config

PROVIDERS = ("anthropic", "gemini")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FORBIDDEN_KEYS = ("temperature", "top_p", "top_k")


@dataclass(frozen=True)
class EvidenceSettings:
    max_passages_per_check: int
    max_extra_queries: int
    max_query_chars: int
    min_quote_chars: int
    max_quote_chars: int
    max_reason_chars: int
    min_alpha_ratio: float


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    effort: str
    max_tokens: int
    timeout_s: float
    api_key_env: str
    cache_dir: Path | None
    call_log: Path | None
    max_retries_invalid_output: int
    evidence: EvidenceSettings


def _int(v: Any, where: str, minimum: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise ConfigError(f"{where} phải là số nguyên >= {minimum}, nhận {v!r}")
    return v


def _bounded(v: Any, where: str, lo: int, hi: int) -> int:
    n = _int(v, where, lo)
    if n > hi:
        raise ConfigError(f"{where} không được lớn hơn {hi}")
    return n


def _path(v: Any, where: str) -> Path | None:
    if v in (None, ""):
        return None
    if not isinstance(v, str):
        raise ConfigError(f"{where} phải là chuỗi đường dẫn hoặc rỗng")
    p = Path(v)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_llm_settings(cfg: dict[str, Any] | None = None) -> LLMSettings:
    cfg = load_config("llm") if cfg is None else cfg
    for k in FORBIDDEN_KEYS:
        if cfg.get(k) is not None:
            raise ConfigError(f"llm.{k} không được đặt: API từ chối tham số lấy mẫu (xem llm.yaml)")
    if cfg.get("provider") not in PROVIDERS:
        raise ConfigError(f"llm.provider phải thuộc {PROVIDERS}")
    model = cfg.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ConfigError("llm.model chưa được đặt (nhóm chốt model)")
    if cfg.get("effort") not in EFFORTS:
        raise ConfigError(f"llm.effort phải thuộc {EFFORTS}")
    timeout = cfg.get("timeout_s")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ConfigError("llm.timeout_s phải là số hữu hạn > 0")
    env = cfg.get("api_key_env")
    if not isinstance(env, str) or not env.strip():
        raise ConfigError("llm.api_key_env phải là tên biến môi trường")
    ev = cfg.get("evidence")
    if not isinstance(ev, dict):
        raise ConfigError("llm.evidence phải là mapping")
    ratio = ev.get("min_alpha_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 <= ratio <= 1:
        raise ConfigError("llm.evidence.min_alpha_ratio phải là số trong [0, 1]")
    evidence = EvidenceSettings(
        max_passages_per_check=_int(
            ev.get("max_passages_per_check"), "evidence.max_passages_per_check", 1
        ),
        max_extra_queries=_int(ev.get("max_extra_queries"), "evidence.max_extra_queries", 0),
        max_query_chars=_int(ev.get("max_query_chars"), "evidence.max_query_chars", 10),
        min_quote_chars=_int(ev.get("min_quote_chars"), "evidence.min_quote_chars", 1),
        max_quote_chars=_int(ev.get("max_quote_chars"), "evidence.max_quote_chars", 10),
        max_reason_chars=_int(ev.get("max_reason_chars"), "evidence.max_reason_chars", 10),
        min_alpha_ratio=float(ratio),
    )
    if evidence.min_quote_chars > evidence.max_quote_chars:
        raise ConfigError("evidence.min_quote_chars không được lớn hơn max_quote_chars")
    return LLMSettings(
        provider=cfg["provider"],
        model=model.strip(),
        effort=cfg["effort"],
        max_tokens=_bounded(cfg.get("max_tokens"), "llm.max_tokens", 256, 128_000),
        timeout_s=float(timeout),
        api_key_env=env.strip(),
        cache_dir=_path(cfg.get("cache_dir"), "llm.cache_dir"),
        call_log=_path(cfg.get("call_log"), "llm.call_log"),
        max_retries_invalid_output=_int(
            cfg.get("max_retries_invalid_output"), "llm.max_retries_invalid_output", 0
        ),
        evidence=evidence,
    )
