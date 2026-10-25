"""Đóng băng trước khi chạy tập test (spec mục 15.1, 22).

`configs/eval_freeze.json` lưu hash của: các file theo `eval.freeze_globs` (config, thư viện giả
thuyết, CODE trong src/ và scripts/ vì luật kiểm chứng nằm trong code, dữ liệu đầu vào như giá và
kho RAG, file ca/nhãn), dấu vân tay prompt (Phase 9, 11), danh sách ca và ĐÁP ÁN cuối dev/test.
Runner (Phase 12) gọi `require_frozen` trước khi chạy test: chưa đóng băng → luôn từ chối; lệch hash
→ từ chối, trừ khi có `unfreeze_reason` hợp lệ — lý do và hash phải được ghi vào kết quả.

Hash file tính trên nội dung đã chuẩn hóa xuống dòng (CRLF → LF), để checkout trên Windows/Linux
không làm lệch hash khi nội dung không đổi. Đóng băng lại (`overwrite`) giữ lịch sử các lần trước.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steel_xai.config import CONFIG_DIR_ENV, PROJECT_ROOT
from steel_xai.contracts import CaseRecord
from steel_xai.eval.settings import EvalSettings

FREEZE_VERSION = 2
_META = ("frozen_at", "frozen_by", "history", "manifest_sha256")


class FreezeError(RuntimeError):
    """Freeze lệch, chưa đóng băng, hoặc không đóng băng được (thiếu đáp án...)."""


def file_hash(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _sha(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _cases_part(cases: Sequence[CaseRecord], split: str) -> dict[str, Any]:
    chosen = sorted((c for c in cases if c.split == split), key=lambda c: c.case_id)
    rows = [{"case_id": c.case_id, "anomaly_id": c.anomaly_id} for c in chosen]
    refs = [
        {
            "case_id": c.case_id,
            "reference_set": sorted(c.reference_set),
            "reference_statuses": dict(sorted(c.reference_statuses.items())),
        }
        for c in chosen
    ]
    return {
        "cases": rows,
        "sha256": _sha([[r["case_id"], r["anomaly_id"]] for r in rows]),
        "references": refs,
        "references_sha256": _sha(refs),
    }


def default_prompt_fingerprints() -> dict[str, str]:
    from steel_xai.llm.prompts import prompt_fingerprint
    from steel_xai.report.prompts import report_prompt_fingerprint

    return {"llm_evidence": prompt_fingerprint(), "report": report_prompt_fingerprint()}


def _check_config_dir(root: Path) -> None:
    env = os.environ.get(CONFIG_DIR_ENV)
    if env and Path(env).resolve() != (root / "configs").resolve():
        raise FreezeError(
            f"{CONFIG_DIR_ENV}={env} trỏ ra ngoài configs/ của repo: config khi chạy khác config "
            "được băm. Bỏ biến môi trường này khi đóng băng và khi chạy tập test."
        )


def build_manifest(
    settings: EvalSettings,
    cases: Sequence[CaseRecord],
    *,
    root: Path = PROJECT_ROOT,
    prompts: Callable[[], dict[str, str]] = default_prompt_fingerprints,
) -> dict[str, Any]:
    """Ảnh chụp hiện tại (không gồm thời điểm tạo, để so sánh được)."""
    _check_config_dir(root)
    freeze_file = settings.freeze_file.resolve()
    files: dict[str, str] = {}
    for pattern in settings.freeze_globs:
        for p in sorted(root.glob(pattern)):
            if p.is_file() and p.resolve() != freeze_file and "__pycache__" not in p.parts:
                files[p.relative_to(root).as_posix()] = file_hash(p)
    if not files:
        raise FreezeError(f"freeze_globs {settings.freeze_globs} không khớp file nào")
    return {
        "version": FREEZE_VERSION,
        "files": dict(sorted(files.items())),
        "prompts": dict(sorted(prompts().items())),
        "dev": _cases_part(cases, "dev"),
        "test": _cases_part(cases, "test"),
    }


def manifest_sha(manifest: dict[str, Any]) -> str:
    """Hash của phần nội dung (bỏ thời điểm/người/lịch sử): ghi vào kết quả mỗi lần chạy test."""
    return _sha({k: v for k, v in manifest.items() if k not in _META})


def diff_manifest(frozen: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Danh sách khác biệt dễ đọc; rỗng = khớp."""
    out: list[str] = []
    if frozen.get("version") != current["version"]:
        out.append(f"phiên bản freeze {frozen.get('version')} ≠ {current['version']}")
    fa, fb = frozen.get("files", {}), current["files"]
    out += [f"file đã đổi: {k}" for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]
    out += [f"file mới (chưa đóng băng): {k}" for k in sorted(set(fb) - set(fa))]
    out += [f"file đã xóa: {k}" for k in sorted(set(fa) - set(fb))]
    pa, pb = frozen.get("prompts", {}), current["prompts"]
    out += [f"prompt đã đổi: {k}" for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]
    for sp in ("dev", "test"):
        f, c = frozen.get(sp, {}), current[sp]
        if f.get("sha256") != c["sha256"]:
            out.append(f"danh sách ca {sp} đã đổi")
        if f.get("references_sha256") != c["references_sha256"]:
            out.append(f"đáp án tham chiếu {sp} đã đổi")
    return out


def write_freeze(
    path: Path, manifest: dict[str, Any], frozen_by: str, *, overwrite: bool = False
) -> dict[str, Any]:
    """Ghi freeze. Mọi ca phải có đáp án cuối. Ghi đè chỉ khi `overwrite`, giữ lịch sử bản cũ."""
    missing = [
        r["case_id"]
        for sp in ("dev", "test")
        for r in manifest[sp]["references"]
        if not r["reference_set"]
    ]
    if missing:
        raise FreezeError(f"ca chưa có đáp án cuối, không đóng băng được: {', '.join(missing)}")
    old = read_freeze(path)
    if old is not None and not overwrite:
        raise FreezeError(f"{path} đã có; chỉ đóng băng lại khi CHƯA chạy tập test (overwrite)")
    history = [] if old is None else list(old.get("history", []))
    if old is not None:
        history.append(
            {
                "frozen_at": old.get("frozen_at"),
                "frozen_by": old.get("frozen_by"),
                "manifest_sha256": old.get("manifest_sha256") or manifest_sha(old),
            }
        )
    body = {
        **manifest,
        "manifest_sha256": manifest_sha(manifest),
        "frozen_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "frozen_by": frozen_by,
        "history": history,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body


def read_freeze(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class FreezeCheck:
    differences: tuple[str, ...]
    frozen_sha256: str
    current_sha256: str
    unfreeze_reason: str | None = None  # có giá trị: chạy dù lệch, PHẢI ghi vào kết quả

    @property
    def ok(self) -> bool:
        return not self.differences

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "differences": list(self.differences),
            "frozen_sha256": self.frozen_sha256,
            "current_sha256": self.current_sha256,
            "unfreeze_reason": self.unfreeze_reason,
        }


def _reason_problem(reason: str, min_chars: int) -> str | None:
    if len(reason) < min_chars:
        return f">= {min_chars} ký tự"
    if len({w.casefold() for w in re.findall(r"[^\W\d_]{2,}", reason)}) < 3:
        return "ít nhất 3 từ khác nhau mô tả thay đổi và lý do"
    return None


def require_frozen(
    settings: EvalSettings,
    cases: Sequence[CaseRecord],
    unfreeze_reason: str | None = None,
    **manifest_kw: Any,
) -> FreezeCheck:
    """Gọi trước khi chạy tập test. Chưa đóng băng → luôn FreezeError. Lệch → FreezeError, trừ khi
    `unfreeze_reason` hợp lệ; runner phải ghi `FreezeCheck.to_dict()` vào kết quả."""
    frozen = read_freeze(settings.freeze_file)
    if frozen is None:
        raise FreezeError("chưa đóng băng (chưa có eval_freeze.json): không chạy tập test")
    current = build_manifest(settings, cases, **manifest_kw)
    diffs = tuple(diff_manifest(frozen, current))
    shas = (frozen.get("manifest_sha256") or manifest_sha(frozen), manifest_sha(current))
    if not diffs:
        return FreezeCheck((), *shas)
    reason = (unfreeze_reason or "").strip()
    if (why := _reason_problem(reason, settings.min_unfreeze_reason_chars)) is not None:
        raise FreezeError(
            "từ chối chạy tập test: " + "; ".join(diffs) + f". Dùng --unfreeze-reason ({why}) "
            "nếu thật sự cần; lý do sẽ ghi vào kết quả."
        )
    return FreezeCheck(diffs, *shas, reason)
