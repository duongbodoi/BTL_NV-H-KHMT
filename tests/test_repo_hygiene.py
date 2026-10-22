import importlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SUBPACKAGES = [
    "parsing",
    "extraction",
    "normalization",
    "validation",
    "anomaly",
    "hypotheses",
    "retrieval",
    "verification",
    "llm",
    "report",
    "eval",
    "demo",
]

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "cache"}
TEXT_SUFFIXES = {
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".md",
    ".txt",
    ".cfg",
    ".ini",
    ".ipynb",
    ".csv",
    ".sh",
    ".ps1",
}

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(
        r"(?i)(?<![A-Za-z0-9])(api_key|apikey|secret|password|passwd|token)"
        r"\s*[:=]\s*['\"]?[^\s'\"]{8,}"
    ),
    re.compile(r"(?m)^[A-Z][A-Z0-9_]*(KEY|TOKEN|SECRET)\s*=\s*\S{8,}"),
]

HAS_GIT = shutil.which("git") is not None and (REPO_ROOT / ".git").exists()


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_importable(name):
    importlib.import_module(f"steel_xai.{name}")


def _candidate_files():
    """File sẽ vào repo: tracked + untracked chưa bị ignore (secret trong .env cục bộ là hợp lệ)."""
    if HAS_GIT:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        return [REPO_ROOT / p for p in out.split("\0") if p]
    return [
        p
        for p in REPO_ROOT.rglob("*")
        if p.is_file()
        and not any(
            part in SKIP_DIRS or part.endswith(".egg-info")
            for part in p.relative_to(REPO_ROOT).parts
        )
    ]


def _is_text(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name.startswith(".env")


def test_no_secrets_in_repo():
    hits = []
    for path in _candidate_files():
        if not path.is_file() or not _is_text(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{path.relative_to(REPO_ROOT)}: {pat.pattern}")
    assert not hits, f"Nghi có secret: {hits}"


@pytest.mark.parametrize(
    "text",
    [
        "sk-ant-" + "a" * 20,
        "sk-proj-" + "c" * 24,
        "api_key: " + "b" * 20,
        "ANTHROPIC_API_KEY=" + "d" * 12,
        "pass" + "word: MySecurePass!2024",  # nối chuỗi để chính file này không bị quét trúng
    ],
)
def test_secret_patterns_detect(text):
    assert any(p.search(text) for p in SECRET_PATTERNS)


@pytest.mark.parametrize(
    "text",
    ["api_key_env: ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY=", "log token/độ trễ mỗi lần gọi"],
)
def test_secret_patterns_no_false_positive(text):
    assert not any(p.search(text) for p in SECRET_PATTERNS)


@pytest.mark.skipif(not HAS_GIT, reason="cần git")
@pytest.mark.parametrize(
    "rel", [".env", ".env.local", "data/raw/x.pdf", "data/processed/x.parquet"]
)
def test_sensitive_paths_ignored(rel):
    r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO_ROOT)
    assert r.returncode == 0, f"{rel} không bị gitignore"


@pytest.mark.skipif(not HAS_GIT, reason="cần git")
def test_env_example_not_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=REPO_ROOT)
    assert r.returncode == 1


def test_required_project_files_exist():
    for rel in ["AI_USAGE_LOG.md", "DEVIATIONS_FROM_MIDTERM.md", "pyproject.toml", ".env.example"]:
        assert (REPO_ROOT / rel).is_file(), rel
