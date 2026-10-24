"""Schema, loader và validator của thư viện giả thuyết (spec mục 9, 10.1, Phase 6).

Nội dung giả thuyết do nhóm soạn; code chỉ kiểm cấu trúc. Chỉ giả thuyết `status: reviewed` (có
`reviewed_by` là người, có nguồn, có check critical) được tải vào pipeline. Giả thuyết sai cấu trúc
làm hỏng cả lần tải (HypothesisError), không bị bỏ qua im lặng.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from steel_xai.config import PROJECT_ROOT, ConfigError, load_config
from steel_xai.contracts import (
    CHECK_IMPORTANCE,
    CHECK_KINDS,
    CHECK_POLARITY,
    HYPOTHESIS_STATUSES,
    Hypothesis,
)

_DATA_SUFFIXES = (".yaml", ".yml", ".json", ".toml")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

_LIST_FIELDS = (
    "expected_quantitative_evidence",
    "expected_textual_evidence",
    "contradictory_evidence",
    "required_external_data",
    "critical_missing_evidence",
)
_TEXT_FIELDS = ("claim", "business_rationale")
_REQUIRED = ("id", "anomaly_type", *_TEXT_FIELDS, *_LIST_FIELDS)  # 9 trường của báo cáo
_OPTIONAL = (
    "sources",
    "status",
    "reviewed_by",
    "checks",
    "code",
    "testable_with_annual_data",
    "source_strength",
    "extension_beyond_midterm",
    "direction",
)
DIRECTIONS = ("up", "down", "both")
_TESTABLE = (True, False, "yes", "no", "partial")  # YAML 1.1: yes/no thành bool
_CHECK_REQUIRED = ("id", "kind", "importance", "polarity")
_CHECK_OPTIONAL = ("tool", "params", "pass_when", "queries")


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader từ chối khóa trùng (mặc định giá trị cuối âm thầm thắng)."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.YAMLError(f"khóa trùng {key!r} (dòng {key_node.start_mark.line + 1})")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


class HypothesisError(ConfigError):
    """Giả thuyết sai cấu trúc hoặc không đủ điều kiện `reviewed`."""


def _letters(text: str) -> str:
    """NFKC, bỏ ký tự định dạng (zero-width...), chỉ giữ chữ cái, chữ thường."""
    t = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in t if ch.isalpha()).lower()


@dataclass(frozen=True)
class LibrarySettings:
    library_dir: Path
    min_reviewed_per_type: int
    non_human_reviewer_words: frozenset[str]
    non_human_reviewer_tokens: frozenset[str]
    placeholder_reviewers: frozenset[str]
    min_source_chars: int
    source_code_re: re.Pattern[str]
    catalog_codes: frozenset[str] | None  # None = không có file danh mục nguồn
    companies: tuple[str, ...]
    known_tools: frozenset[str]
    anomaly_types: tuple[str, ...]


def load_library_settings(
    cfg: dict[str, Any] | None = None,
    anomaly_cfg: dict[str, Any] | None = None,
    dataset_cfg: dict[str, Any] | None = None,
) -> LibrarySettings:
    cfg = load_config("hypotheses") if cfg is None else cfg
    anomaly_cfg = load_config("anomaly") if anomaly_cfg is None else anomaly_cfg
    dataset_cfg = load_config("dataset") if dataset_cfg is None else dataset_cfg

    d = cfg.get("library_dir")
    if not isinstance(d, str) or not d.strip():
        raise ConfigError("hypotheses.library_dir phải là chuỗi không rỗng")
    path = Path(d)
    n = cfg.get("min_reviewed_per_type")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ConfigError("hypotheses.min_reviewed_per_type phải là số nguyên >= 0")
    lists: dict[str, list[str]] = {}
    for name in (
        "non_human_reviewer_words",
        "non_human_reviewer_tokens",
        "placeholder_reviewers",
        "known_tools",
    ):
        v = cfg.get(name, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise ConfigError(f"hypotheses.{name} phải là danh sách chuỗi không rỗng")
        lists[name] = v
    msc = cfg.get("min_source_chars")
    if isinstance(msc, bool) or not isinstance(msc, int) or msc < 1:
        raise ConfigError("hypotheses.min_source_chars phải là số nguyên >= 1")
    pat, cat = cfg.get("source_code_pattern"), cfg.get("sources_catalog")
    if not isinstance(pat, str) or not isinstance(cat, str):
        raise ConfigError("hypotheses.source_code_pattern và sources_catalog phải là chuỗi")
    try:
        code_re = re.compile(pat)
    except re.error as e:
        raise ConfigError(f"hypotheses.source_code_pattern không hợp lệ: {e}") from e
    cat_path = Path(cat) if Path(cat).is_absolute() else PROJECT_ROOT / cat
    catalog_codes = None
    if cat_path.is_file():
        body = cat_path.read_text(encoding="utf-8")
        catalog_codes = frozenset(
            m.group(0) for m in re.finditer(r"" + pat.strip("^$") + r"", body)
        )
    comps = dataset_cfg.get("companies")
    if not isinstance(comps, list) or not all(isinstance(c, str) for c in comps):
        raise ConfigError("dataset.companies phải là danh sách chuỗi")
    types = anomaly_cfg.get("anomaly_types")
    if not isinstance(types, list) or not types or not all(isinstance(t, str) for t in types):
        raise ConfigError("anomaly.anomaly_types phải là danh sách chuỗi không rỗng")
    return LibrarySettings(
        library_dir=path if path.is_absolute() else PROJECT_ROOT / path,
        min_reviewed_per_type=n,
        non_human_reviewer_words=frozenset(_letters(w) for w in lists["non_human_reviewer_words"]),
        non_human_reviewer_tokens=frozenset(
            w.strip().lower() for w in lists["non_human_reviewer_tokens"]
        ),
        placeholder_reviewers=frozenset(w.strip().lower() for w in lists["placeholder_reviewers"]),
        min_source_chars=msc,
        source_code_re=code_re,
        catalog_codes=catalog_codes,
        companies=tuple(c.strip().lower() for c in comps),
        known_tools=frozenset(t.strip() for t in lists["known_tools"]),
        anomaly_types=tuple(types),
    )


# ---------------- validator


def _is_text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _str_list(raw: dict[str, Any], key: str, errs: list[str]) -> list[str]:
    v = raw.get(key)
    if not isinstance(v, list) or not all(_is_text(x) for x in v):
        errs.append(f"{key} phải là danh sách chuỗi không rỗng")
        return []
    return v


def _validate_check(c: Any, where: str, settings: LibrarySettings) -> list[str]:
    if not isinstance(c, dict):
        return [f"{where} phải là mapping"]
    errs: list[str] = []
    for k in c:
        if k not in _CHECK_REQUIRED + _CHECK_OPTIONAL:
            errs.append(f"{where}: trường lạ {k!r}")
    for k in _CHECK_REQUIRED:
        if k not in c:
            errs.append(f"{where}: thiếu {k}")
    cid = c.get("id")
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        errs.append(f"{where}.id phải khớp {_ID_RE.pattern}")
    for key, allowed in (
        ("kind", CHECK_KINDS),
        ("importance", CHECK_IMPORTANCE),
        ("polarity", CHECK_POLARITY),
    ):
        if key in c and c[key] not in allowed:
            errs.append(f"{where}.{key} phải thuộc {allowed}, nhận {c[key]!r}")
    kind = c.get("kind")
    if kind == "quant":
        tool = c.get("tool")
        if not _is_text(tool):
            errs.append(f"{where}: check quant cần `tool`")
        elif settings.known_tools and tool not in settings.known_tools:
            errs.append(f"{where}.tool {tool!r} không có trong known_tools")
        if not isinstance(c.get("params"), dict):
            errs.append(f"{where}: check quant cần `params` (mapping, có thể rỗng)")
        pw = c.get("pass_when")
        if not isinstance(pw, dict) or not pw or any(v is None for v in pw.values()):
            errs.append(f"{where}: check quant cần `pass_when` (mapping không rỗng, không null)")
        if c.get("queries") not in (None, []):
            errs.append(f"{where}: check quant không có `queries`")
    elif kind == "text":
        q = c.get("queries")
        if not isinstance(q, list) or not q or not all(_is_text(x) for x in q):
            errs.append(f"{where}: check text cần `queries` (danh sách chuỗi, không rỗng)")
        if any(c.get(k) is not None for k in ("tool", "params", "pass_when")):
            errs.append(f"{where}: check text không có tool/params/pass_when")
    return errs


def _reviewer_problem(name: str, settings: LibrarySettings) -> str | None:
    """Lọc theo danh sách chỉ chặn phần lớn trường hợp; người ký/commit vẫn là kiểm soát cuối."""
    if name.strip().lower() in settings.placeholder_reviewers:
        return "là giá trị giữ chỗ"
    norm = unicodedata.normalize("NFKC", name)
    for ch in norm:
        if ch.isalpha() and unicodedata.name(ch, "").split(" ")[0] in ("CYRILLIC", "GREEK"):
            return "chứa chữ Cyrillic/Hy Lạp (giống chữ Latin)"
    words = [w.lower() for w in _WORD_RE.findall(norm.replace(".", ""))]  # "A.I." → "ai"
    if len(words) < 2:
        return "cần họ và tên (ít nhất 2 từ)"
    compact = _letters(name)
    if any(w in compact for w in settings.non_human_reviewer_words if w):
        return "trông như tên mô hình/công cụ AI"
    if set(words) & settings.non_human_reviewer_tokens:
        return "trông như tên mô hình/công cụ AI"
    return None


def _mentions_company(raw: dict[str, Any], settings: LibrarySettings) -> str | None:
    """Thư viện không gắn công ty: mã công ty không được là một từ trong nội dung giả thuyết."""
    blob: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            blob.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    for k, v in raw.items():
        if k not in ("id", "reviewed_by", "sources"):
            walk(v)
    words = {w.lower() for w in _WORD_RE.findall(" ".join(blob))}
    return next((c for c in settings.companies if c and c in words), None)


def validate_hypothesis(raw: Any, settings: LibrarySettings) -> list[str]:
    """Danh sách lỗi (rỗng = hợp lệ). `draft` chỉ cần đủ cấu trúc; `reviewed` cần cả nội dung."""
    if not isinstance(raw, dict):
        return ["giả thuyết phải là mapping"]
    errs: list[str] = []
    for k in raw:
        if k not in _REQUIRED + _OPTIONAL:
            errs.append(f"trường lạ {k!r}")
    for k in _REQUIRED:
        if k not in raw:
            errs.append(f"thiếu trường bắt buộc {k}")

    hid = raw.get("id")
    if not isinstance(hid, str) or not _ID_RE.match(hid):
        errs.append(f"id phải khớp {_ID_RE.pattern}")
    if raw.get("anomaly_type") not in settings.anomaly_types:
        errs.append(
            f"anomaly_type phải thuộc {settings.anomaly_types}, nhận {raw.get('anomaly_type')!r}"
        )
    for k in _TEXT_FIELDS:
        if k in raw and not isinstance(raw[k], str):
            errs.append(f"{k} phải là chuỗi")
    lists = {k: _str_list(raw, k, errs) for k in _LIST_FIELDS if k in raw}

    status = raw.get("status", "draft")
    if status not in HYPOTHESIS_STATUSES:
        errs.append(f"status phải thuộc {HYPOTHESIS_STATUSES}, nhận {status!r}")
    sources = raw.get("sources", [])
    if not isinstance(sources, list) or not all(_is_text(x) for x in sources):
        errs.append("sources phải là danh sách chuỗi không rỗng")
        sources = []
    if raw.get("code") is not None and not _is_text(raw["code"]):
        errs.append("code phải là chuỗi không rỗng")
    if raw.get("testable_with_annual_data") is not None and (
        raw["testable_with_annual_data"] not in _TESTABLE
    ):
        errs.append("testable_with_annual_data phải là yes | partial | no")
    if raw.get("extension_beyond_midterm") is not None and not isinstance(
        raw["extension_beyond_midterm"], bool
    ):
        errs.append("extension_beyond_midterm phải là true/false")
    if raw.get("direction") is not None and raw["direction"] not in DIRECTIONS:
        errs.append(f"direction phải thuộc {DIRECTIONS}")
    if raw.get("source_strength") is not None and not _is_text(raw["source_strength"]):
        errs.append("source_strength phải là chuỗi không rỗng")
    rb = raw.get("reviewed_by")
    if rb is not None and not isinstance(rb, str):
        errs.append("reviewed_by phải là chuỗi")
    elif _is_text(rb) and (why := _reviewer_problem(rb, settings)):
        errs.append(f"reviewed_by {rb!r} {why}")  # kể cả bản draft: LLM không được điền
    checks = raw.get("checks", [])
    if not isinstance(checks, list):
        errs.append("checks phải là danh sách")
        checks = []
    ids: Counter[str] = Counter()
    for i, c in enumerate(checks):
        errs += _validate_check(c, f"checks[{i}]", settings)
        if isinstance(c, dict) and isinstance(c.get("id"), str):
            ids[c["id"]] += 1
    errs += [f"check id lặp: {i}" for i, n in ids.items() if n > 1]

    if status != "reviewed":
        return errs

    # ---- điều kiện riêng của `reviewed` (spec mục 9, Phase 6)
    if raw.get("direction") is None:
        errs.append("reviewed: thiếu direction (up | down | both)")
    for k in _TEXT_FIELDS:
        if not _is_text(raw.get(k)):
            errs.append(f"reviewed: {k} không được rỗng")
    if not lists.get("expected_quantitative_evidence") and not lists.get(
        "expected_textual_evidence"
    ):
        errs.append("reviewed: cần ít nhất một expected_quantitative/textual_evidence")
    for k in ("contradictory_evidence", "critical_missing_evidence"):
        if not lists.get(k):
            errs.append(f"reviewed: {k} không được rỗng")
    if not sources:
        errs.append("reviewed: thiếu sources (nguồn xây giả thuyết)")
    else:
        for x in (x.strip() for x in sources):
            if settings.source_code_re.fullmatch(x):  # mã nguồn trong danh mục
                if settings.catalog_codes is None:
                    errs.append(f"reviewed: source {x!r} là mã nhưng không có file danh mục nguồn")
                elif x not in settings.catalog_codes:
                    errs.append(f"reviewed: source {x!r} không có trong danh mục nguồn")
            elif len(x) < settings.min_source_chars:
                errs.append(
                    f"reviewed: source {x!r} quá ngắn (< {settings.min_source_chars} ký tự)"
                )
    if not _is_text(rb):
        errs.append("reviewed: thiếu reviewed_by")
    if (hit := _mentions_company(raw, settings)) is not None:
        errs.append(f"reviewed: nội dung nhắc công ty {hit!r}; thư viện không gắn công ty")
    valid_checks = [c for c in checks if isinstance(c, dict)]
    if not any(c.get("importance") == "critical" for c in valid_checks):
        errs.append("reviewed: thiếu check critical")
    elif not any(
        c.get("importance") == "critical" and c.get("polarity") == "support" for c in valid_checks
    ):
        errs.append("reviewed: cần ít nhất một check critical polarity support")
    if not any(c.get("polarity") == "contradict" for c in valid_checks):
        errs.append("reviewed: thiếu check phản bác (polarity contradict)")
    return errs


def build_hypothesis(raw: dict[str, Any]) -> Hypothesis:
    """Dựng `Hypothesis` từ dict ĐÃ qua `validate_hypothesis` (from_dict tự nó không kiểm gì)."""
    return Hypothesis.from_dict(raw)


# ---------------- loader


def _read_yaml(p: Path) -> Any:
    try:
        with p.open(encoding="utf-8") as f:
            return yaml.load(f, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader + khóa duy nhất
    except (OSError, yaml.YAMLError) as e:
        raise HypothesisError(f"{p.name}: không đọc được: {e}") from e


def load_hypothesis_file(path: str | Path, settings: LibrarySettings) -> list[Hypothesis]:
    """Một giả thuyết (tên file = `<id>.yaml`) hoặc `hypotheses: [...]` (nhiều giả thuyết)."""
    p = Path(path)
    raw = _read_yaml(p)
    if isinstance(raw, dict) and set(raw) == {"hypotheses"}:
        items = raw["hypotheses"]
        if not isinstance(items, list) or not items:
            raise HypothesisError(f"{p.name}: `hypotheses` phải là danh sách không rỗng")
        named = True
    else:
        items, named = [raw], False
    out: list[Hypothesis] = []
    for i, item in enumerate(items):
        who = (item.get("id") if isinstance(item, dict) else None) or f"#{i + 1}"
        errs = validate_hypothesis(item, settings)
        if not named and isinstance(item, dict) and item.get("id") != p.stem:
            errs.append(f"tên file phải là <id>.yaml (id={item.get('id')!r})")
        if errs:
            raise HypothesisError(f"{p.name} [{who}]: " + "; ".join(errs))
        out.append(build_hypothesis(item))
    return out


@dataclass(frozen=True)
class Library:
    """Kết quả tải: `reviewed` là phần duy nhất được vào pipeline; `drafts` chỉ để theo dõi."""

    reviewed: tuple[Hypothesis, ...]
    drafts: tuple[Hypothesis, ...]
    min_reviewed_per_type: int
    anomaly_types: tuple[str, ...]

    def for_type(self, anomaly_type: str) -> tuple[Hypothesis, ...]:
        return tuple(h for h in self.reviewed if h.anomaly_type == anomaly_type)

    def coverage(self) -> dict[str, dict[str, int]]:
        """Mỗi loại: số reviewed, số draft, số còn thiếu so với `min_reviewed_per_type`."""
        out: dict[str, dict[str, int]] = {}
        for t in self.anomaly_types:
            n = len(self.for_type(t))
            out[t] = {
                "reviewed": n,
                "draft": sum(1 for h in self.drafts if h.anomaly_type == t),
                "missing": max(0, self.min_reviewed_per_type - n),
            }
        return out


def load_library(
    directory: str | Path | None = None, settings: LibrarySettings | None = None
) -> Library:
    """Tải mọi `*.yaml` trong thư mục thư viện; id phải duy nhất. Sai cấu trúc → HypothesisError."""
    s = load_library_settings() if settings is None else settings
    base = s.library_dir if directory is None else Path(directory)
    if not base.is_dir():
        raise HypothesisError(f"Không tìm thấy thư mục thư viện: {base}")
    files = sorted(p for p in base.iterdir() if p.is_file() and not p.name.startswith("."))
    stray = [p.name for p in files if p.suffix != ".yaml" and p.suffix.lower() in _DATA_SUFFIXES]
    if stray:  # không bỏ sót im lặng một giả thuyết đặt sai đuôi
        raise HypothesisError(f"File thư viện phải có đuôi .yaml (chữ thường): {stray}")
    hyps = [h for p in files if p.suffix == ".yaml" for h in load_hypothesis_file(p, s)]
    dup = [i for i, n in Counter(h.id for h in hyps).items() if n > 1]
    if dup:
        raise HypothesisError(f"id giả thuyết lặp: {sorted(dup)}")
    return Library(
        reviewed=tuple(h for h in hyps if h.status == "reviewed"),
        drafts=tuple(h for h in hyps if h.status != "reviewed"),
        min_reviewed_per_type=s.min_reviewed_per_type,
        anomaly_types=s.anomaly_types,
    )
