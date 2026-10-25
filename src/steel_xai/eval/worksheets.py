"""Bảng cho người làm: ánh xạ mù giả thuyết của baseline (spec 14.1) và phân loại ca sai (15.5).

Ánh xạ mù: giả thuyết tự do của cấu hình A/B được trộn trong từng ca, gán `item_id` không lộ cấu
hình (băm có `salt` NGẪU NHIÊN, bí mật), thứ tự theo `item_id`. Người gán thấy `sheet` (không có cấu
hình), cột `mapped_id` điền id thư viện hoặc `other`. `key` (item_id → cấu hình/lần chạy/hạng, kèm
salt) để riêng, chỉ mở sau khi ánh xạ xong; tái lập dựa vào file key, không dựa vào salt (biết salt
thì vét cạn được cấu hình vì người gán đã thấy ca và văn bản). Code KHÔNG điền ánh xạ và KHÔNG gán
nguyên nhân ca sai.
"""

from __future__ import annotations

import csv
import hashlib
import secrets
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steel_xai.contracts import CaseRecord
from steel_xai.eval.labels import Kappa, Taxonomy, cohen_kappa
from steel_xai.hypotheses.library import LibrarySettings, person_name_problem

SHEET_FIELDS = (
    "item_id",
    "case_id",
    "anomaly_type",
    "hypothesis_text",
    "mapped_id",
    "annotator",
    "note",
)
KEY_FIELDS = ("item_id", "case_id", "config", "run_id", "rank", "salt")
FAILURE_FIELDS = ("case_id", "anomaly_id", "reference_set", "c_top3", "cause", "annotator", "note")


@dataclass(frozen=True)
class BlindItem:
    case_id: str
    config: str  # A | B (hoặc ablation)
    run_id: str
    rank: int
    text: str


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel đọc đúng tiếng Việt
        w = csv.DictWriter(f, fieldnames=list(fields))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


# ---------------- ánh xạ mù


def make_blind_sheet(
    items: Sequence[BlindItem], cases: Sequence[CaseRecord], salt: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(sheet cho người gán, key giữ riêng). Mặc định salt ngẫu nhiên (`secrets`), chỉ lưu trong
    key; truyền `salt` chỉ để test tái lập."""
    salt = secrets.token_hex(16) if salt is None else salt
    if len(salt) < 8:
        raise ValueError("salt quá ngắn: để trống để sinh ngẫu nhiên")
    types = {c.case_id: c.anomaly_type for c in cases}
    sheet, key = [], []
    seen: set[str] = set()
    for it in items:
        if it.case_id not in types:
            raise ValueError(f"case_id {it.case_id!r} không có trong danh sách ca")
        if not it.text.strip():
            raise ValueError(f"giả thuyết rỗng ({it.case_id}, {it.config}, {it.run_id}, {it.rank})")
        raw = f"{salt}|{it.case_id}|{it.config}|{it.run_id}|{it.rank}|{it.text}"
        iid = "B" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        if iid in seen:
            raise ValueError(f"mục trùng lặp: {it}")
        seen.add(iid)
        sheet.append(
            {
                "item_id": iid,
                "case_id": it.case_id,
                "anomaly_type": types[it.case_id],
                "hypothesis_text": it.text.strip(),
            }
        )
        key.append(
            {"item_id": iid, "case_id": it.case_id, "config": it.config, "run_id": it.run_id,
             "rank": it.rank, "salt": salt}
        )  # fmt: skip
    sheet.sort(key=lambda r: (r["case_id"], r["item_id"]))  # thứ tự theo băm: không lộ cấu hình
    key.sort(key=lambda r: (r["case_id"], r["item_id"]))
    return sheet, key


def validate_mapping(
    rows: Sequence[Mapping[str, str]],
    cases: Sequence[CaseRecord],
    taxonomy: Taxonomy,
    other_id: str,
    lib_settings: LibrarySettings,
    expected_ids: Iterable[str] | None = None,
) -> list[str]:
    """Lỗi của bảng ánh xạ đã điền (một người gán / một file). `expected_ids` (từ key): báo item
    bị thiếu hoặc thừa so với bảng đã sinh."""
    errs: list[str] = []
    types = {c.case_id: c.anomaly_type for c in cases}
    ids = Counter(r.get("item_id", "") for r in rows)
    errs += [f"item_id lặp: {i}" for i, n in ids.items() if n > 1]
    if expected_ids is not None:
        want = set(expected_ids)
        errs += [
            f"thiếu item {i} (đã sinh nhưng không có trong file)" for i in sorted(want - set(ids))
        ]
        errs += [f"item {i} không có trong key" for i in sorted(set(ids) - want)]
    names = {r.get("annotator", "") for r in rows}
    if len(names) != 1:
        errs.append(f"mỗi file ánh xạ do một người điền; thấy annotator {sorted(names)}")
    for r in rows:
        where = f"ánh xạ {r.get('item_id')}"
        t = types.get(r.get("case_id", ""))
        if t is None:
            errs.append(f"{where}: case_id không có trong danh sách ca")
            continue
        m = r.get("mapped_id", "")
        if not m:
            errs.append(f"{where}: chưa điền mapped_id")
        elif m != other_id and m not in taxonomy.get(t, {}):
            errs.append(f"{where}: {m!r} không phải giả thuyết reviewed loại {t} hoặc {other_id!r}")
    for n in names:
        if (why := person_name_problem(n, lib_settings)) is not None:
            errs.append(f"annotator {n!r} {why}")
    return errs


def mapping_kappa(a: Sequence[Mapping[str, str]], b: Sequence[Mapping[str, str]]) -> Kappa:
    """Kappa giữa hai người ánh xạ trên các item cả hai cùng điền."""
    ma = {r["item_id"]: r["mapped_id"] for r in a if r.get("mapped_id")}
    mb = {r["item_id"]: r["mapped_id"] for r in b if r.get("mapped_id")}
    common = sorted(set(ma) & set(mb))
    return cohen_kappa([ma[i] for i in common], [mb[i] for i in common])


def unblind(
    rows: Sequence[Mapping[str, str]], key: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Ghép ánh xạ với key (chỉ dùng SAU khi ánh xạ xong). item không có trong key → lỗi."""
    k = {str(r["item_id"]): r for r in key}
    missing = [r["item_id"] for r in rows if r["item_id"] not in k]
    if missing:
        raise ValueError(f"item_id không có trong key: {missing[:5]}")
    return [{**k[r["item_id"]], "mapped_id": r["mapped_id"]} for r in rows]


# ---------------- phân loại ca sai


def failure_rows(
    cases: Sequence[CaseRecord], c_top3: Mapping[str, Sequence[str]]
) -> list[dict[str, str]]:
    """Ca TEST có đáp án mà không giả thuyết đúng nào nằm trong top-3 của C (spec 15.5).

    `c_top3`: một danh sách cho mỗi ca; với 3 lần chạy, runner (Phase 12) quyết cách gộp và gọi hàm
    này cho từng lần hoặc cho bản gộp. Ca test thiếu đáp án hoặc thiếu kết quả C → ValueError (không
    bỏ qua im lặng).
    """
    out = []
    for c in cases:
        if c.split != "test":
            continue
        if not c.reference_set:
            raise ValueError(f"ca test {c.case_id} chưa có đáp án cuối")
        if c.case_id not in c_top3:
            raise ValueError(f"ca test {c.case_id} không có kết quả top-3 của C")
        top = list(c_top3[c.case_id])[:3]
        if not set(c.reference_set) & set(top):
            out.append(
                {
                    "case_id": c.case_id,
                    "anomaly_id": c.anomaly_id,
                    "reference_set": ";".join(c.reference_set),
                    "c_top3": ";".join(top),
                }
            )
    return out


def validate_failures(
    rows: Sequence[Mapping[str, str]],
    causes: Sequence[str],
    lib_settings: LibrarySettings,
    expected: Sequence[Mapping[str, str]] | None = None,
) -> list[str]:
    """Lỗi của bảng ca sai đã điền. `expected` (từ `failure_rows`): báo ca bị xóa hoặc thêm."""
    errs: list[str] = []
    if expected is not None:
        want, got = {r["case_id"] for r in expected}, Counter(r.get("case_id") for r in rows)
        errs += [
            f"thiếu ca sai {c} (đã sinh nhưng không có trong file)" for c in sorted(want - set(got))
        ]
        errs += [f"ca {c} không phải ca sai đã sinh" for c in sorted(set(got) - want)]
        errs += [f"ca sai {c} lặp" for c, n in got.items() if n > 1]
    for r in rows:
        where = f"ca sai {r.get('case_id')}"
        if r.get("cause") not in causes:
            errs.append(f"{where}: cause phải thuộc {list(causes)}")
        if (why := person_name_problem(r.get("annotator"), lib_settings)) is not None:
            errs.append(f"{where}: annotator {r.get('annotator')!r} {why}")
    return errs


def summarize_failures(
    rows: Sequence[Mapping[str, str]], causes: Sequence[str], disclaimer: str
) -> str:
    """Bảng Markdown: nguyên nhân × số ca. Luôn kèm câu "khảo sát khả thi" (spec 15)."""
    n = Counter(r.get("cause", "") for r in rows)
    total = len(rows)
    lines = ["| Nguyên nhân | Số ca | Tỷ lệ |", "|---|---|---|"]
    for c in causes:
        pct = f"{100 * n[c] / total:.0f}%" if total else "–"
        lines.append(f"| {c} | {n[c]} | {pct} |")
    other = total - sum(n[c] for c in causes)
    if other:
        lines.append(f"| (chưa gán/không hợp lệ) | {other} | {100 * other / total:.0f}% |")
    lines.append(f"| **Tổng** | {total} | |")
    return "\n".join(lines) + f"\n\n_{disclaimer}_\n"
