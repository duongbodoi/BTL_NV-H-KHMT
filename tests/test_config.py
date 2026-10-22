from pathlib import Path

import pytest

from steel_xai.config import (
    CONFIG_DIR_ENV,
    CONFIG_NAMES,
    ConfigError,
    default_config_dir,
    get_api_key,
    load_all_configs,
    load_config,
)

REPO_CONFIGS = Path(__file__).resolve().parents[1] / "configs"


@pytest.fixture(autouse=True)
def _no_config_env(monkeypatch):
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)


def test_repo_configs_load():
    cfgs = load_all_configs()
    assert set(cfgs) == set(CONFIG_NAMES)
    assert all(isinstance(c, dict) for c in cfgs.values())


def test_default_config_dir_is_repo_configs():
    assert default_config_dir().resolve() == REPO_CONFIGS


def test_config_dir_env_override(tmp_path, monkeypatch):
    (tmp_path / "anomaly.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    assert load_config("anomaly") == {"x": 1}


def test_scope_keys_present():
    # Kiểm khung, không kiểm giá trị do nhóm chốt.
    dataset = load_config("dataset")
    assert dataset["industry"] == "steel"
    assert isinstance(dataset["companies"], list)
    anomaly = load_config("anomaly")
    assert set(anomaly["anomaly_types"]) == {"inventory_increase", "gross_margin_change"}
    assert anomaly["two_sided"] is True
    assert load_config("retrieval")["enforce_asof_filter"] is True
    assert load_config("llm")["api_key_env"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="Không tìm thấy"):
        load_config("anomaly", tmp_path)


def test_missing_one_of_all_raises(tmp_path):
    for name in CONFIG_NAMES[:-1]:
        (tmp_path / f"{name}.yaml").write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_all_configs(tmp_path)


@pytest.mark.parametrize("name", ["../secret", "a/b", "", "Anomaly", "x.yaml"])
def test_invalid_name_raises(tmp_path, name):
    with pytest.raises(ConfigError, match="Tên config"):
        load_config(name, tmp_path)


def test_invalid_yaml_raises(tmp_path):
    (tmp_path / "x.yaml").write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML không hợp lệ"):
        load_config("x", tmp_path)


@pytest.mark.parametrize("content", ["", "- a\n- b\n", "just a string\n"])
def test_non_mapping_raises(tmp_path, content):
    (tmp_path / "x.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config("x", tmp_path)


def test_utf8_values(tmp_path):
    (tmp_path / "x.yaml").write_text("label: hàng tồn kho\n", encoding="utf-8")
    assert load_config("x", tmp_path) == {"label": "hàng tồn kho"}


def test_get_api_key_from_env(monkeypatch):
    monkeypatch.setenv("TEST_STEEL_XAI_KEY", "dummy-value")
    assert get_api_key({"api_key_env": "TEST_STEEL_XAI_KEY"}) == "dummy-value"


def test_get_api_key_unset_raises(monkeypatch):
    monkeypatch.delenv("TEST_STEEL_XAI_KEY", raising=False)
    with pytest.raises(ConfigError, match="chưa được đặt"):
        get_api_key({"api_key_env": "TEST_STEEL_XAI_KEY"})


def test_get_api_key_requires_env_name():
    with pytest.raises(ConfigError, match="api_key_env"):
        get_api_key({})
