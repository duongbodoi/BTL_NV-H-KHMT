"""Đọc các file YAML trong `configs/` thành dict.

Mọi ngưỡng, danh sách công ty, mốc thời gian, model LLM nằm trong config (spec mục 19).
API key không bao giờ đọc từ file, chỉ từ biến môi trường.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

# Đúng khi cài editable (`pip install -e`) hoặc chạy từ source; ghi đè bằng STEEL_XAI_CONFIG_DIR.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR_ENV = "STEEL_XAI_CONFIG_DIR"
CONFIG_NAMES = (
    "anomaly",
    "retrieval",
    "llm",
    "eval",
    "dataset",
    "extraction",
    "normalization",
    "validation",
    "hypotheses",
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ConfigError(Exception):
    """Config thiếu, hỏng hoặc sai cấu trúc."""


def default_config_dir() -> Path:
    env = os.environ.get(CONFIG_DIR_ENV)
    return Path(env) if env else PROJECT_ROOT / "configs"


def load_config(name: str, config_dir: str | Path | None = None) -> dict[str, Any]:
    """Đọc `<config_dir>/<name>.yaml`; gốc file phải là mapping."""
    if not _NAME_RE.match(name):
        raise ConfigError(f"Tên config không hợp lệ: {name!r}")
    base = default_config_dir() if config_dir is None else Path(config_dir)
    path = base / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(f"Không tìm thấy config: {path}")
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML không hợp lệ trong {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"Gốc của {path} phải là mapping, nhận {type(data).__name__}")
    return data


def load_all_configs(config_dir: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Đọc toàn bộ config bắt buộc, trả về {tên: dict}."""
    return {name: load_config(name, config_dir) for name in CONFIG_NAMES}


def get_api_key(llm_cfg: dict[str, Any]) -> str:
    """Lấy API key từ biến môi trường mà `llm.yaml` chỉ định (`api_key_env`)."""
    env_name = llm_cfg.get("api_key_env")
    if not env_name:
        raise ConfigError("llm config thiếu khóa `api_key_env`")
    key = os.environ.get(env_name)
    if not key:
        raise ConfigError(f"Biến môi trường {env_name} chưa được đặt")
    return key
