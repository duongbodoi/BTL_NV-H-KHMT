"""Danh sách ca đánh giá và chia dev/test (spec mục 15, 15.1). NỘI DUNG do nhóm chọn.

File `data/eval/cases.yaml`:

    cases:
      - case_id: C01
        anomaly_id: "HPG|FY2021|inventory_increase|up"   # id của bất thường do engine phát hiện
        split: dev                                        # dev | test
        note: ""

Code chỉ kiểm cấu trúc: id duy nhất, bất thường có thật trong `anomaly_candidates.csv`, công ty/kỳ
nằm trong phạm vi, số ca mỗi phần khớp `eval.split` (bắt buộc khi đóng băng).
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from steel_xai.config import PROJECT_ROOT, load_config
from steel_xai.contracts import SPLITS, CaseRecord
from steel_xai.eval.settings import EvalSettings

_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
_PERIOD = re.compile(r"^FY(\d{4})$")
_CASE_KEYS = {"case_id", "anomaly_id", "split", "note"}


@dataclass(frozen=True)
class CaseContext:
    """Phạm vi hợp lệ của ca, đọc từ config + danh sách bất thường do engine sinh."""

    companies: frozenset[str]
    anomaly_types: frozenset[str]
    start_year: int
    end_year: int
    reference_only_from_year: int
    # anomaly_id → có vượt ngưỡng không (cột `candidate`); None = chưa có file ứng viên
    known: dict[str, bool] | None


@dataclass(frozen=True)
class CaseCheck:
    cases: tuple[CaseRecord, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _Acc:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def anomaly_id_of(row: dict[str, str]) -> str:
    return f"{row['company']}|{row['period']}|{row['type']}|{row['direction']}"


def read_candidates(path: str | Path) -> dict[str, bool]:
    """anomaly_id → candidate (vượt ngưỡng) từ `anomaly_candidates.csv` (Phase 5)."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return {
            anomaly_id_of(r): r["candidate"].strip().lower() == "true" for r in csv.DictReader(f)
        }


def load_case_context(candidates_path: str | Path | None = None) -> CaseContext:
    ds, an = load_config("dataset"), load_config("anomaly")
    win = ds["case_selection_window"]
    if candidates_path is None:
        candidates_path = PROJECT_ROOT / ds["paths"]["processed"] / "anomaly_candidates.csv"
    p = Path(candidates_path)
    return CaseContext(
        companies=frozenset(ds["companies"]),
        anomaly_types=frozenset(an["anomaly_types"]),
        start_year=int(win["start_year"]),
        end_year=int(win["end_year"]),
        reference_only_from_year=int(ds["reference_only_from_year"]),
        known=read_candidates(p) if p.is_file() else None,
    )


def _check_anomaly_id(aid: Any, where: str, ctx: CaseContext, acc: _Acc) -> bool:
    if not isinstance(aid, str) or aid.count("|") != 3:
        acc.errors.append(f"{where}: anomaly_id phải có dạng CÔNG_TY|FYyyyy|loại|hướng")
        return False
    company, period, typ, direction = aid.split("|")
    ok = True
    if company not in ctx.companies:
        acc.errors.append(f"{where}: công ty {company!r} không thuộc dataset.companies")
        ok = False
    m = _PERIOD.match(period)
    if not m:
        acc.errors.append(f"{where}: kỳ {period!r} phải có dạng FYyyyy")
        ok = False
    else:
        y = int(m.group(1))
        if not ctx.start_year <= y <= ctx.end_year:
            acc.errors.append(
                f"{where}: {period} ngoài cửa sổ chọn ca {ctx.start_year}-{ctx.end_year}"
            )
            ok = False
        if y >= ctx.reference_only_from_year:  # spec 3.3: chỉ làm nguồn đáp án
            acc.errors.append(f"{where}: {period} chỉ dùng làm nguồn đáp án, không làm ca")
            ok = False
    if typ not in ctx.anomaly_types:
        acc.errors.append(f"{where}: loại {typ!r} không thuộc anomaly.anomaly_types")
        ok = False
    if direction not in ("up", "down"):
        acc.errors.append(f"{where}: hướng {direction!r} phải là up | down")
        ok = False
    if ok and ctx.known is not None:
        if aid not in ctx.known:
            acc.errors.append(
                f"{where}: {aid!r} không có trong anomaly_candidates.csv "
                "(chạy lại scripts/build_anomaly_candidates.py?)"
            )
            ok = False
        elif not ctx.known[aid]:
            acc.warnings.append(
                f"{where}: {aid!r} không vượt ngưỡng (candidate=false); ca đối chứng?"
            )
    return ok


def validate_cases(
    raw: Any, settings: EvalSettings, ctx: CaseContext, *, strict_counts: bool = False
) -> CaseCheck:
    """Kiểm danh sách ca. `strict_counts=True` (khi đóng băng): số ca dev/test phải khớp config."""
    acc = _Acc()
    if ctx.known is None:
        acc.warnings.append("chưa có anomaly_candidates.csv: không kiểm được bất thường có thật")
    if not isinstance(raw, dict) or set(raw) != {"cases"} or not isinstance(raw["cases"], list):
        return CaseCheck((), ("file ca phải có đúng một khóa `cases: [...]`",))
    if not raw["cases"]:
        return CaseCheck((), ("chưa có ca nào (nhóm chọn ca, Claude không chọn)",))
    out: list[CaseRecord] = []
    ids: Counter[str] = Counter()
    aids: Counter[str] = Counter()
    for i, item in enumerate(raw["cases"], start=1):
        where = f"ca #{i}"
        if not isinstance(item, dict):
            acc.errors.append(f"{where}: phải là mapping")
            continue
        where = f"ca #{i} ({item.get('case_id', '?')})"
        extra = set(item) - _CASE_KEYS
        if extra:
            acc.errors.append(f"{where}: trường lạ {sorted(extra)}")
        cid, split, note = item.get("case_id"), item.get("split"), item.get("note") or ""
        good = True
        if not isinstance(cid, str) or not _CASE_ID.match(cid):
            acc.errors.append(f"{where}: case_id phải khớp {_CASE_ID.pattern}")
            good = False
        else:
            ids[cid] += 1
        if split not in SPLITS:
            acc.errors.append(f"{where}: split phải thuộc {SPLITS}")
            good = False
        if not isinstance(note, str):
            acc.errors.append(f"{where}: note phải là chuỗi")
            good = False
        if not _check_anomaly_id(item.get("anomaly_id"), where, ctx, acc):
            good = False
        else:
            aids[item["anomaly_id"]] += 1
        if good:
            out.append(CaseRecord(cid, split, item["anomaly_id"], note=note))
    acc.errors += [f"case_id lặp: {c}" for c, n in ids.items() if n > 1]
    acc.errors += [
        f"anomaly_id lặp (một bất thường chỉ là một ca): {a}" for a, n in aids.items() if n > 1
    ]
    counts = Counter(c.split for c in out)
    for sp in SPLITS:
        want = settings.split[sp]
        if counts.get(sp, 0) != want:
            msg = f"số ca {sp} = {counts.get(sp, 0)}, eval.split.{sp} = {want}"
            (acc.errors if strict_counts else acc.warnings).append(msg)
    return CaseCheck(tuple(out), tuple(acc.errors), tuple(acc.warnings))


def read_yaml(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_cases(
    settings: EvalSettings, ctx: CaseContext | None = None, *, strict_counts: bool = False
) -> CaseCheck:
    p = settings.cases_path
    if not p.is_file():
        return CaseCheck((), (f"chưa có file ca {p} (mẫu: data/eval/templates/cases.yaml)",))
    return validate_cases(
        read_yaml(p), settings, ctx or load_case_context(), strict_counts=strict_counts
    )
