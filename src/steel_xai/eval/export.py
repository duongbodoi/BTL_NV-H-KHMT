"""Bảng cho người và bảng kết quả của Phase 12 (spec mục 14.1, 15.3-15.8).

Bảng cho NGƯỜI (code chỉ sinh khung, không điền):
- ánh xạ mù giả thuyết tự do của A/B/no_library về taxonomy (hai người; bất đồng → người thứ ba);
- chấm trích dẫn đã qua kiểm nguyên văn (hai người, mù cấu hình; bất đồng → người thứ ba);
- nguyên nhân thật của các UNVERIFIED của C (chất lượng UNVERIFIED, RQ3);
- nguyên nhân ca sai của C ở tập test (spec 15.5).
Key giải mù để trong thư mục kết quả (không đưa cho người điền).

Bảng KẾT QUẢ: Markdown + CSV + biểu đồ SVG (không cần thư viện vẽ). Mọi bảng kèm câu "khảo sát khả
thi" (spec mục 15).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import secrets
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from steel_xai.contracts import CaseRecord, RunRecord
from steel_xai.eval import metrics as M
from steel_xai.eval.labels import Taxonomy, cohen_kappa, person_key
from steel_xai.eval.runner import FREE_CONFIGS, REPORT_CONFIGS
from steel_xai.eval.settings import RunnerSettings
from steel_xai.eval.worksheets import (
    FAILURE_FIELDS,
    KEY_FIELDS,
    SHEET_FIELDS,
    BlindItem,
    make_blind_sheet,
    mapping_kappa,
    read_csv,
    summarize_failures,
    unblind,
    validate_failures,
    validate_mapping,
    write_csv,
)
from steel_xai.hypotheses.library import LibrarySettings, person_name_problem

TEMPLATE = "TEMPLATE"
ADJUDICATION = "adjudication"
CITATION_FIELDS = (
    "item_id",
    "case_id",
    "claim_text",
    "quote",
    "document",
    "page",
    "rating",
    "annotator",
    "note",
)
CITATION_KEY_FIELDS = (
    "item_id",
    "case_id",
    "config",
    "run_id",
    "claim_index",
    "citation_index",
    "salt",
)
UNVERIFIED_FIELDS = (
    "case_id",
    "hypothesis_id",
    "code",
    "runs_unverified",
    "missing_reasons",
    "cause",
    "annotator",
    "note",
)
ADJ_FIELDS = ("item_id", "value", "adjudicator", "note")
STATUSES = ("SUPPORTED", "PARTIAL", "UNVERIFIED", "CONTRADICTED")


def split_dirs(rs: RunnerSettings, split: str) -> tuple[Path, Path, Path]:
    """(thư mục bảng cho người, thư mục key giải mù, thư mục kết quả)."""
    out = rs.output_dir / split
    return rs.sheets_dir / split, out / "keys", out / "results"


# ---------------- sinh bảng cho người


def blind_items(records: Sequence[RunRecord]) -> list[BlindItem]:
    """Mỗi giả thuyết tự do một mục. Trường `rank` của BlindItem/key là VỊ TRÍ (1, 2, ...) trong
    danh sách xếp hạng, không phải hạng: các mục hòa hạng vẫn có khóa riêng."""
    return [
        BlindItem(rec.case_id, rec.config, rec.run_id, pos, blind_text(r["text"]))
        for rec in records
        if rec.config in FREE_CONFIGS and rec.status == "ok"
        for pos, r in enumerate(rec.outputs.get("ranking") or [], start=1)
    ]


_CODE_TOKENS = re.compile(r"\b[NEHMP]\d+\b")
_STATUS_WORDS = re.compile(r"\b(SUPPORTED|PARTIAL|UNVERIFIED|CONTRADICTED)\b", re.IGNORECASE)


def blind_text(text: str) -> str:
    """Bỏ dấu vết lộ cấu hình khỏi văn bản đưa cho người chấm mù: mã nguồn N/E/H/M/P (chỉ C và B
    có) và từ trạng thái (chỉ C có)."""
    t = _CODE_TOKENS.sub("[mã]", text)
    return " ".join(_STATUS_WORDS.sub("[trạng thái]", t).split())


def citation_items(records: Sequence[RunRecord]) -> list[dict[str, Any]]:
    """Trích dẫn ĐÃ qua kiểm nguyên văn của A/B/C (không qua kiểm tự động = không hỗ trợ)."""
    out = []
    for rec in records:
        rep = rec.outputs.get("report")
        if rec.config not in REPORT_CONFIGS or rec.status != "ok" or not rep:
            continue
        claims = rep.get("claims") or []
        for idx, c in enumerate(rep.get("citations") or []):
            if not c.get("verified"):
                continue
            ci = int(c["claim_index"])
            out.append(
                {
                    "case_id": rec.case_id,
                    "config": rec.config,
                    "run_id": rec.run_id,
                    "claim_index": ci,
                    "citation_index": idx,
                    "claim_text": blind_text(claims[ci - 1]["text"])
                    if 0 < ci <= len(claims)
                    else "",
                    "quote": c.get("quote", ""),
                    "document": c.get("doc_id", ""),
                    "page": c.get("page", ""),
                }
            )
    return out


def make_citation_sheet(
    items: Sequence[Mapping[str, Any]], salt: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    salt = secrets.token_hex(16) if salt is None else salt
    sheet, key = [], []
    for it in items:
        raw = (
            f"{salt}|{it['case_id']}|{it['config']}|{it['run_id']}|{it['claim_index']}|"
            f"{it['citation_index']}"
        )
        iid = "Q" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        sheet.append({k: it.get(k, "") for k in CITATION_FIELDS} | {"item_id": iid})
        key.append({k: it.get(k, "") for k in CITATION_KEY_FIELDS} | {"item_id": iid, "salt": salt})
    sheet.sort(key=lambda r: (r["case_id"], r["item_id"]))
    key.sort(key=lambda r: (r["case_id"], r["item_id"]))
    return sheet, key


def unverified_rows(records: Sequence[RunRecord], codes: Mapping[str, str]) -> list[dict[str, Any]]:
    """Cặp (ca, giả thuyết) UNVERIFIED ở C, gộp các lần chạy, kèm lý do thiếu do code ghi."""
    n_runs: dict[str, set[str]] = defaultdict(set)
    hit: dict[tuple[str, str], set[str]] = defaultdict(set)
    why: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for rec in records:
        if rec.config != "C" or rec.status != "ok":
            continue
        n_runs[rec.case_id].add(rec.run_id)
        for v in rec.outputs.get("verdicts") or []:
            if v["status"] != "UNVERIFIED":
                continue
            k = (rec.case_id, v["hypothesis_id"])
            hit[k].add(rec.run_id)
            for f in v.get("missing") or []:
                if f.get("importance") == "critical":
                    why[k][f"{f['check_id']}: {M.missing_reason(f)}"] += 1
    return [
        {
            "case_id": c,
            "hypothesis_id": h,
            "code": codes.get(h, ""),
            "runs_unverified": f"{len(hit[(c, h)])}/{len(n_runs[c])}",
            "missing_reasons": " | ".join(r for r, _ in why[(c, h)].most_common(4)),
        }
        for c, h in sorted(hit)
    ]


def write_sheets(
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    rs: RunnerSettings,
    split: str,
    codes: Mapping[str, str],
    *,
    overwrite: bool = False,
    salt: str | None = None,
) -> list[str]:
    """Sinh khung bảng cho người. Từ chối ghi đè key đã có (người có thể đã điền theo key cũ)."""
    sheets, keys, _ = split_dirs(rs, split)
    msgs: list[str] = []
    blind_key, cit_key = keys / "blind_key.csv", keys / "citation_key.csv"
    if not overwrite and (blind_key.is_file() or cit_key.is_file()):
        raise FileExistsError(
            f"đã có key giải mù trong {keys}: sinh lại sẽ đổi item_id, bảng đã điền không "
            "dùng được. "
            "Dùng --overwrite nếu chắc chắn chưa ai điền."
        )
    items = blind_items(records)
    if items:
        sheet, key = make_blind_sheet(items, cases, salt)
        write_csv(sheets / f"blind_mapping_{TEMPLATE}.csv", SHEET_FIELDS, sheet)
        write_csv(blind_key, KEY_FIELDS, key)
        msgs.append(
            f"ánh xạ mù: {len(sheet)} giả thuyết tự do → {sheets / f'blind_mapping_{TEMPLATE}.csv'}"
        )
    cits = citation_items(records)
    if cits:
        sheet, key = make_citation_sheet(cits, salt)
        write_csv(sheets / f"citation_rating_{TEMPLATE}.csv", CITATION_FIELDS, sheet)
        write_csv(cit_key, CITATION_KEY_FIELDS, key)
        msgs.append(
            f"chấm trích dẫn: {len(sheet)} trích dẫn → {sheets / f'citation_rating_{TEMPLATE}.csv'}"
        )
    unv = unverified_rows(records, codes)
    if unv:
        write_csv(sheets / f"unverified_{TEMPLATE}.csv", UNVERIFIED_FIELDS, unv)
        msgs.append(f"UNVERIFIED của C: {len(unv)} cặp → {sheets / f'unverified_{TEMPLATE}.csv'}")
    return msgs


def write_failure_sheet(
    records: Sequence[RunRecord], cases: Sequence[CaseRecord], rs: RunnerSettings, other_id: str
) -> tuple[Path, int]:
    rows = M.failure_cases(records, cases, other_id=other_id, other_counts=rs.other_counts_as_hit)
    p = split_dirs(rs, "test")[0] / f"failure_analysis_{TEMPLATE}.csv"
    write_csv(p, (*FAILURE_FIELDS, "missed_runs"), rows)
    return p, len(rows)


# ---------------- đọc bảng người đã điền


def filled_files(folder: Path, prefix: str) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p
        for p in folder.glob(f"{prefix}_*.csv")
        if not p.stem.endswith(TEMPLATE) and not p.stem.endswith(ADJUDICATION)
    )


def combine_two(
    a: Sequence[Mapping[str, str]],
    b: Sequence[Mapping[str, str]],
    field: str,
    adjudication: Sequence[Mapping[str, str]],
    lib_settings: LibrarySettings,
    allowed: Sequence[str] | Callable[[str], Sequence[str]] | None = None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """(giá trị cuối theo item, item còn bất đồng, lỗi). Hai người khớp → dùng; khác → bắt buộc
    người thứ ba (khác hai người), quyết định có lỗi bị bỏ."""
    va = {r["item_id"]: r.get(field, "") for r in a if r.get(field)}
    vb = {r["item_id"]: r.get(field, "") for r in b if r.get(field)}
    names = {person_key(r.get("annotator", "")) for r in (*a, *b) if r.get("annotator")}
    errs: list[str] = []
    adj: dict[str, str] = {}
    for r in adjudication:
        who = r.get("adjudicator", "")
        where = f"adjudication {r.get('item_id')}"
        if (why := person_name_problem(who, lib_settings)) is not None:
            errs.append(f"{where}: adjudicator {who!r} {why}")
        elif person_key(who) in names:
            errs.append(f"{where}: người thứ ba phải khác hai người điền")
        elif not r.get("note"):
            errs.append(f"{where}: thiếu lý do (note)")
        elif allowed is not None and r.get("value") not in (
            ok := (allowed(r["item_id"]) if callable(allowed) else allowed)
        ):
            errs.append(f"{where}: value phải thuộc {sorted(ok)}")
        else:
            adj[r["item_id"]] = r["value"]
    final: dict[str, str] = {}
    open_: list[str] = []
    for iid in sorted(set(va) & set(vb)):
        if va[iid] == vb[iid]:
            final[iid] = va[iid]
        elif iid in adj:
            final[iid] = adj[iid]
        else:
            open_.append(iid)
    return final, open_, errs


def final_mapping(
    rs: RunnerSettings,
    split: str,
    cases: Sequence[CaseRecord],
    taxonomy: Taxonomy,
    other_id: str,
    lib_settings: LibrarySettings,
) -> tuple[dict[tuple[str, str, str, int], str] | None, dict[str, Any]]:
    """Ánh xạ cuối (case, config, run, rank) → id taxonomy. Chưa đủ/còn lỗi → (None, thông tin)."""
    sheets, keys, _ = split_dirs(rs, split)
    info: dict[str, Any] = {"files": [], "errors": [], "open": [], "kappa": None}
    key_path = keys / "blind_key.csv"
    files = filled_files(sheets, "blind_mapping")
    info["files"] = [p.name for p in files]
    if not key_path.is_file():
        info["errors"].append("chưa sinh bảng ánh xạ mù (run_eval.py sheets)")
        return None, info
    key = read_csv(key_path)
    if len(files) != 2:
        info["errors"].append(f"cần đúng 2 file blind_mapping_<tên>.csv, có {len(files)}")
        return None, info
    a, b = (read_csv(p) for p in files)
    for p, rows in zip(files, (a, b), strict=True):
        info["errors"] += [
            f"{p.name}: {e}"
            for e in validate_mapping(
                rows, cases, taxonomy, other_id, lib_settings, [r["item_id"] for r in key]
            )
        ]
    info["kappa"] = mapping_kappa(a, b).to_dict()
    adj_p = sheets / f"blind_mapping_{ADJUDICATION}.csv"
    adj = read_csv(adj_p) if adj_p.is_file() else []
    types = {c.case_id: c.anomaly_type for c in cases}
    case_of = {r["item_id"]: r["case_id"] for r in key}

    def allowed(item_id: str) -> list[str]:  # id hợp lệ cho ca của item: cùng loại hoặc `other`
        t = types.get(case_of.get(item_id, ""), "")
        return [*taxonomy.get(t, {}), other_id]

    final, open_, errs = combine_two(a, b, "mapped_id", adj, lib_settings, allowed)
    info["errors"] += errs
    info["open"] = open_
    if info["errors"] or open_:
        return None, info
    rows = unblind([{"item_id": i, "mapped_id": v} for i, v in final.items()], key)
    return {
        (r["case_id"], r["config"], r["run_id"], int(r["rank"])): r["mapped_id"] for r in rows
    }, info


def final_ratings(
    rs: RunnerSettings, split: str, lib_settings: LibrarySettings
) -> tuple[dict[str, str] | None, dict[tuple[str, str, str, int, int], str] | None, dict[str, Any]]:
    sheets, keys, _ = split_dirs(rs, split)
    info: dict[str, Any] = {"files": [], "errors": [], "open": [], "kappa": None}
    key_path = keys / "citation_key.csv"
    if not key_path.is_file():
        info["errors"].append("chưa sinh bảng chấm trích dẫn (run_eval.py sheets)")
        return None, None, info
    key = read_csv(key_path)
    ckey = {
        (
            r["case_id"],
            r["config"],
            r["run_id"],
            int(r["claim_index"]),
            int(r["citation_index"]),
        ): r["item_id"]
        for r in key
    }
    files = filled_files(sheets, "citation_rating")
    info["files"] = [p.name for p in files]
    if len(files) != 2:
        info["errors"].append(f"cần đúng 2 file citation_rating_<tên>.csv, có {len(files)}")
        return None, ckey, info
    a, b = (read_csv(p) for p in files)
    want = {r["item_id"] for r in key}
    for p, rows in zip(files, (a, b), strict=True):
        got = {r.get("item_id") for r in rows}
        if missing := want - got:
            info["errors"].append(f"{p.name}: thiếu {len(missing)} trích dẫn")
        bad = [r["item_id"] for r in rows if r.get("rating") not in rs.citation_ratings]
        if bad:
            info["errors"].append(
                f"{p.name}: rating phải thuộc {list(rs.citation_ratings)}: {bad[:5]}"
            )
        names = {r.get("annotator", "") for r in rows}
        if len(names) != 1 or (person_name_problem(next(iter(names)), lib_settings) is not None):
            info["errors"].append(f"{p.name}: mỗi file do một người (họ tên thật) điền")
    ka = {r["item_id"]: r.get("rating") for r in a}
    kb = {r["item_id"]: r.get("rating") for r in b}
    common = sorted(set(ka) & set(kb))
    info["kappa"] = cohen_kappa([ka[i] for i in common], [kb[i] for i in common]).to_dict()
    adj_p = sheets / f"citation_rating_{ADJUDICATION}.csv"
    adj = read_csv(adj_p) if adj_p.is_file() else []
    final, open_, errs = combine_two(a, b, "rating", adj, lib_settings, rs.citation_ratings)
    info["errors"] += errs
    info["open"] = open_
    if info["errors"] or open_:
        return None, ckey, info
    return final, ckey, info


def unverified_quality(
    rs: RunnerSettings, split: str, lib_settings: LibrarySettings
) -> dict[str, Any]:
    """Chất lượng UNVERIFIED (spec 15.3): tỷ lệ UNVERIFIED mà nguyên nhân thật là dữ liệu công khai
    không đủ, theo bảng người gán (MỘT file; spec không yêu cầu hai người). Bảng có lỗi, hoặc thiếu
    /thừa cặp so với khung đã sinh → không tính tỷ lệ."""
    sheets = split_dirs(rs, split)[0]
    files = filled_files(sheets, "unverified")
    if not files:
        return {"status": "chưa có bảng UNVERIFIED đã điền"}
    if len(files) > 1:
        return {
            "files": [p.name for p in files],
            "errors": ["chỉ điền MỘT file unverified_<tên>.csv"],
            "public_data_insufficient_rate": None,
        }
    rows = read_csv(files[0])
    errs: list[str] = []
    tpl = sheets / f"unverified_{TEMPLATE}.csv"
    if tpl.is_file():
        want = {(r["case_id"], r["hypothesis_id"]) for r in read_csv(tpl)}
        got = [(r.get("case_id", ""), r.get("hypothesis_id", "")) for r in rows]
        errs += [f"thiếu cặp {c}/{h}" for c, h in sorted(want - set(got))]
        errs += [f"cặp {c}/{h} không có trong khung" for c, h in sorted(set(got) - want)]
        errs += [f"cặp {c}/{h} lặp" for (c, h), k in Counter(got).items() if k > 1]
    errs += [
        f"{r.get('case_id')}/{r.get('hypothesis_id')}: cause phải thuộc "
        f"{list(rs.unverified_causes)}"
        for r in rows
        if r.get("cause") not in rs.unverified_causes
    ]
    errs += [
        f"{r.get('case_id')}/{r.get('hypothesis_id')}: annotator {why}"
        for r in rows
        if (why := person_name_problem(r.get("annotator"), lib_settings)) is not None
    ]
    n = Counter(r.get("cause") for r in rows)
    total = len(rows)
    return {
        "files": [p.name for p in files],
        "errors": errs,
        "pairs": total,
        "by_cause": dict(n),
        "public_data_insufficient_rate": (
            (n["public_data_insufficient"] / total) if total and not errs else None
        ),
    }


def failure_summary(
    rs: RunnerSettings,
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    other_id: str,
    causes: Sequence[str],
    lib_settings: LibrarySettings,
) -> dict[str, Any]:
    p = split_dirs(rs, "test")[0] / "failure_analysis.csv"
    if not p.is_file():
        return {"status": "chưa có failure_analysis.csv đã điền"}
    rows = read_csv(p)
    expected = M.failure_cases(
        records, cases, other_id=other_id, other_counts=rs.other_counts_as_hit
    )
    errs = validate_failures(rows, causes, lib_settings, expected)
    return {
        "errors": errs,
        "by_cause": dict(Counter(r.get("cause") for r in rows)),
        "causes": list(causes),
        "cases": len(rows),
    }


# ---------------- định dạng


def vn(x: float | None, digits: int = 2, pct: bool = False) -> str:
    if x is None:
        return "–"
    v = x * 100 if pct else x
    s = f"{v:,.{digits}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return s + ("%" if pct else "")


def fmt_est(e: Mapping[str, Any] | None, digits: int = 2, pct: bool = False) -> str:
    if not e or e.get("mean") is None:
        return "–"
    s = vn(e["mean"], digits, pct)
    if e.get("ci_low") is not None and e.get("ci_high") is not None and e["ci_low"] != e["ci_high"]:
        s += f" [{vn(e['ci_low'], digits, pct)}–{vn(e['ci_high'], digits, pct)}]"
    if e.get("run_std") is not None:
        s += f" ±{vn(e['run_std'], digits, pct)}"
    return s


def md_table(header: Sequence[str], rows: Sequence[Sequence[Any]], disclaimer: str) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines) + f"\n\n_{disclaimer}_\n"


# ---------------- SVG


def _svg(w: int, h: int, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="sans-serif" font-size="12"><title>{html.escape(title)}</title>'
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>{body}</svg>\n'
    )


def svg_hit_chart(
    ranking: Mapping[str, Mapping[str, Any]],
    configs: Sequence[str],
    top_ks: Sequence[int],
    disclaimer: str,
) -> str:
    """Cột Hit@k (kỳ vọng) kèm CI bootstrap cho từng cấu hình (spec 15.8)."""
    configs = [c for c in configs if c in ranking]
    colors = ["#2a6fdb", "#e07b39", "#3a9d5d", "#8e5cc4"]
    W, H, L, B, T = 120 + 90 * max(1, len(configs)), 320, 50, 60, 30
    ph = H - B - T
    bw = 70 / max(1, len(top_ks))
    parts = [
        f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" stroke="#333"/>',
        f'<line x1="{L}" y1="{H - B}" x2="{W - 10}" y2="{H - B}" stroke="#333"/>',
    ]
    for t in range(0, 11, 2):
        y = H - B - ph * t / 10
        parts.append(
            f'<text x="{L - 6}" y="{y + 4}" text-anchor="end">{t / 10:.1f}</text>'
            f'<line x1="{L}" y1="{y}" x2="{W - 10}" y2="{y}" stroke="#eee"/>'
        )
    for i, cfg in enumerate(configs):
        x0 = L + 20 + 90 * i
        for j, k in enumerate(top_ks):
            e = ranking[cfg].get(f"hit@{k}:expected") or {}
            m = e.get("mean")
            if m is None:
                continue
            x, y = x0 + j * bw, H - B - ph * m
            parts.append(
                f'<rect x="{x}" y="{y}" width="{bw - 4}" height="{ph * m}" '
                f'fill="{colors[j % len(colors)]}"/>'
            )
            if e.get("ci_low") is not None:
                y1, y2 = H - B - ph * e["ci_low"], H - B - ph * e["ci_high"]
                cx = x + (bw - 4) / 2
                parts.append(
                    f'<line x1="{cx}" y1="{y1}" x2="{cx}" y2="{y2}" stroke="#000"/>'
                    f'<line x1="{cx - 4}" y1="{y1}" x2="{cx + 4}" y2="{y1}" stroke="#000"/>'
                    f'<line x1="{cx - 4}" y1="{y2}" x2="{cx + 4}" y2="{y2}" stroke="#000"/>'
                )
        parts.append(
            f'<text x="{x0 + 35}" y="{H - B + 16}" text-anchor="middle">{html.escape(cfg)}</text>'
        )
    for j, k in enumerate(top_ks):
        parts.append(
            f'<rect x="{L + 10 + 80 * j}" y="8" width="10" height="10" '
            f'fill="{colors[j % len(colors)]}"/><text x="{L + 24 + 80 * j}" y="17">Hit@{k}</text>'
        )
    parts.append(f'<text x="{L}" y="{H - 12}" font-size="10">{html.escape(disclaimer)}</text>')
    return _svg(W, H, "".join(parts), "Hit@k theo cấu hình")


def svg_heatmap(counts: Mapping[str, Counter[str]], disclaimer: str) -> str:
    """Heatmap trạng thái theo giả thuyết (spec 15.8): tỷ lệ mỗi trạng thái / số lần được kiểm."""
    rows = sorted(counts)
    cw, rh, L, T = 110, 22, 260, 40
    W, H = L + cw * len(STATUSES) + 20, T + rh * max(1, len(rows)) + 40
    parts = [
        f'<text x="{L + cw * i + cw / 2}" y="{T - 10}" text-anchor="middle">{s}</text>'
        for i, s in enumerate(STATUSES)
    ]
    for r, h in enumerate(rows):
        total = sum(counts[h].values()) or 1
        y = T + rh * r
        parts.append(f'<text x="{L - 6}" y="{y + 15}" text-anchor="end">{html.escape(h)}</text>')
        for i, s in enumerate(STATUSES):
            v = counts[h].get(s, 0) / total
            shade = int(255 - 200 * v)
            parts.append(
                f'<rect x="{L + cw * i}" y="{y}" width="{cw - 2}" height="{rh - 2}" '
                f'fill="rgb({shade},{shade},255)"/><text x="{L + cw * i + cw / 2}" '
                f'y="{y + 15}" text-anchor="middle">{vn(v, 0, pct=True)}</text>'
            )
    parts.append(f'<text x="10" y="{H - 12}" font-size="10">{html.escape(disclaimer)}</text>')
    return _svg(W, H, "".join(parts), "Trạng thái theo giả thuyết")


def series_points(rec: RunRecord) -> dict[str, list[tuple[str, float]]]:
    """Chuỗi giá ngoài mà các check của C đã dùng (đầu vào có kỳ/tháng, do code đọc)."""
    pts: dict[str, dict[str, float]] = defaultdict(dict)
    for v in rec.outputs.get("verdicts") or []:
        for group in ("evidence", "contradictions", "failed", "missing"):
            for f in v.get(group) or []:
                for inp in (f.get("quant") or {}).get("inputs") or []:
                    if inp.get("kind") == "external":
                        try:
                            pts[inp["name"]][str(inp["period"])] = float(inp["value"])
                        except (TypeError, ValueError, KeyError):
                            continue
    return {k: sorted(v.items()) for k, v in pts.items()}


def svg_series(series: Mapping[str, Sequence[tuple[str, float]]], title: str) -> str:
    W, H, L, B, T = 640, 300, 60, 70, 30
    parts: list[str] = []
    colors = ["#2a6fdb", "#e07b39", "#3a9d5d", "#8e5cc4", "#c44e52"]
    for n, (name, pts) in enumerate(sorted(series.items())):
        if len(pts) < 1:
            continue
        lo, hi = min(v for _, v in pts), max(v for _, v in pts)
        span = (hi - lo) or 1.0
        xs = [L + (W - L - 20) * (i / max(1, len(pts) - 1)) for i in range(len(pts))]
        ys = [H - B - (H - B - T) * ((v - lo) / span) for _, v in pts]
        col = colors[n % len(colors)]
        parts.append(
            '<polyline fill="none" stroke="{}" stroke-width="2" points="{}"/>'.format(
                col, " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
            )
        )
        parts.append(
            f'<text x="{L}" y="{T - 12 + 0 * n}" dx="{140 * n}" fill="{col}">'
            f"{html.escape(name)} ({vn(lo)}–{vn(hi)}, chuẩn hóa)</text>"
        )
        for i in (0, len(pts) - 1):
            parts.append(
                f'<text x="{xs[i]}" y="{H - B + 14 + 12 * n}" text-anchor="middle" '
                f'font-size="9">{html.escape(pts[i][0][:20])}</text>'
            )
    if not parts:
        parts.append(f'<text x="{L}" y="{H / 2}">Ca này không dùng chuỗi giá ngoài nào.</text>')
    return _svg(W, H, "".join(parts), title)


def case_illustration(rec: RunRecord, codes: Mapping[str, str], disclaimer: str) -> tuple[str, str]:
    """Ca minh họa (spec 15.8): top-3 của C, trạng thái, bằng chứng trích dẫn, chuỗi giá đã dùng."""
    lines = [f"# Ca minh họa {rec.case_id}: {rec.anomaly_id} ({rec.config}, {rec.run_id})", ""]
    lines.append("| Hạng | Giả thuyết | Trạng thái |")
    lines.append("|---|---|---|")
    for r in rec.outputs.get("ranking") or []:
        if r["rank"] <= 3:
            h = r["hypothesis_id"]
            lines.append(f"| {r['rank']} | {codes.get(h, '')} {h} | {r.get('status') or ''} |")
    lines += ["", "## Trích dẫn đã kiểm nguyên văn", ""]
    rep = rec.outputs.get("report") or {}
    for c in rep.get("citations") or []:
        lines.append(f"- {c.get('doc_id')}, trang {c.get('page')}: “{c.get('quote')}”")
    if not rep.get("citations"):
        lines.append("- (không có)")
    lines += ["", f"![Chuỗi giá đã dùng](case_{rec.case_id}_series.svg)", "", f"_{disclaimer}_", ""]
    return "\n".join(lines), svg_series(series_points(rec), f"Chuỗi giá — {rec.anomaly_id}")


# ---------------- tổng hợp & ghi kết quả


def success_check(results: Mapping[str, Any]) -> list[dict[str, str]]:
    """Chỉ số thành công MẶC ĐỊNH của spec 15.7 (khi nhóm chưa chốt bộ khác). Hit@3 so trên tập ca
    chung của A/B/C."""
    rk, rp = results.get("ranking_common", results["ranking"]), results["report"]

    def mean(d: Mapping[str, Any], cfg: str, name: str) -> float | None:
        return ((d.get(cfg) or {}).get(name) or {}).get("mean")

    out = []
    h = [mean(rk, c, "hit@3:expected") for c in ("C", "B", "A")]
    out.append(
        {
            "criterion": "Hit@3: C > B > A",
            "result": "chưa đủ dữ liệu"
            if None in h
            else ("đạt" if h[0] > h[1] > h[2] else "không đạt"),
        }
    )
    nm = mean(rp, "C", "numeric_mismatch_final")
    out.append(
        {
            "criterion": "Numeric mismatch của C = 0 ở báo cáo cuối",
            "result": "chưa đủ dữ liệu" if nm is None else ("đạt" if nm == 0 else "không đạt"),
        }
    )
    cs = mean(rp, "C", "citation_support_rate")
    out.append(
        {
            "criterion": "Citation support rate của C ≥ 90% (người chấm)",
            "result": "chưa chấm" if cs is None else ("đạt" if cs >= 0.9 else "không đạt"),
        }
    )
    u = [mean(rp, c, "unsupported_rate_first") for c in ("C", "A", "B")]
    out.append(
        {
            "criterion": "Unsupported claim rate của C (bản nháp đầu) thấp hơn A và B",
            "result": "chưa đủ dữ liệu"
            if None in u
            else ("đạt" if u[0] < u[1] and u[0] < u[2] else "không đạt"),
        }
    )
    return out


def write_results(
    results: Mapping[str, Any],
    records: Sequence[RunRecord],
    rs: RunnerSettings,
    split: str,
    codes: Mapping[str, str],
) -> list[Path]:
    """Ghi results.json, summary.md, per_case.csv, rq3.csv và biểu đồ SVG."""
    _, _, out = split_dirs(rs, split)
    out.mkdir(parents=True, exist_ok=True)
    d = rs.disclaimer
    paths = [out / "results.json"]
    paths[0].write_text(
        json.dumps(results, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    configs = [c for c in rs.configs]
    rk, rp, vf, cost = (
        results["ranking"],
        results["report"],
        results["verification"],
        results["cost"],
    )
    md = [f"# Kết quả đánh giá — tập {split}", ""]
    for line in results["header"]:
        md.append(f"- {line}")
    md += [
        "",
        "## RQ1 — Xếp hạng (Hit@k, MRR; kỳ vọng khi phá hòa ngẫu nhiên, [CI bootstrap] "
        "±độ lệch giữa các lần chạy)",
        "",
    ]
    rows = []
    for c in configs:
        e = rk.get(c) or {}
        rows.append(
            [
                c,
                *(fmt_est(e.get(f"hit@{k}:expected")) for k in rs.top_ks),
                fmt_est(e.get("mrr:expected")),
                " / ".join(
                    f"{vn((e.get(f'hit@{k}:pessimistic') or {}).get('mean'))}–"
                    f"{vn((e.get(f'hit@{k}:optimistic') or {}).get('mean'))}"
                    for k in rs.top_ks
                ),
                (e.get(f"hit@{rs.top_ks[0]}:expected") or {}).get("n_cases", 0),
                e.get("unscored_runs", 0),
            ]
        )
    md.append(
        md_table(
            [
                "Cấu hình",
                *(f"Hit@{k}" for k in rs.top_ks),
                "MRR",
                "Cận bi quan–lạc quan (Hit@k)",
                "Số ca",
                "Lần chạy chưa chấm",
            ],
            rows,
            d,
        )
    )
    md += ["## RQ2 — Báo cáo (A, B, C)", ""]
    rows = []
    for c in ("A", "B", "C"):
        e = rp.get(c) or {}
        rows.append(
            [
                c,
                fmt_est(e.get("numeric_mismatch_first")),
                fmt_est(e.get("numeric_mismatch_final")),
                fmt_est(e.get("unsupported_rate_first"), 1, True),
                fmt_est(e.get("unsupported_rate_final"), 1, True),
                fmt_est(e.get("citation_verbatim_rate"), 1, True),
                fmt_est(e.get("citation_support_rate"), 1, True),
                fmt_est(e.get("forbidden_first")),
            ]
        )
    md.append(
        md_table(
            [
                "Cấu hình",
                "Lệch số (nháp đầu)/ca",
                "Lệch số (bản cuối)/ca",
                "Unsupported (nháp đầu)",
                "Unsupported (bản cuối)",
                "Trích dẫn qua kiểm nguyên văn (C: bản cuối, luôn 100% theo cấu trúc)",
                "Trích dẫn hỗ trợ (người chấm; C: bản cuối)",
                "Nội dung cấm (nháp đầu)/ca",
            ],
            rows,
            d,
        )
    )
    md += ["## RQ2/RQ3 — Trạng thái (cặp thuộc top-3 của C có trạng thái chuẩn)", ""]
    rows = [
        [
            c,
            v["pairs"],
            vn(v["verification_accuracy"], 1, True),
            v["supported_pairs"],
            vn(v["false_supported_rate"], 1, True),
        ]
        for c, v in vf.items()
    ]
    md.append(
        md_table(
            ["Cấu hình", "Số cặp", "Verification accuracy", "Cặp SUPPORTED", "False-SUPPORTED"],
            rows,
            d,
        )
    )
    uq = results.get("unverified_quality") or {}
    md += ["## RQ3 — UNVERIFIED theo giả thuyết (C)", ""]
    rows = [
        [
            f"{codes.get(r['hypothesis_id'], '')} {r['hypothesis_id']}",
            r["evaluated"],
            r["unverified"],
            vn(r["unverified_rate"], 0, True),
            "; ".join(r["top_reasons"]),
        ]
        for r in results["rq3"]
    ]
    md.append(
        md_table(
            ["Giả thuyết", "Số lần kiểm", "UNVERIFIED", "Tỷ lệ", "Lý do thiếu hay gặp (code ghi)"],
            rows,
            d,
        )
    )
    uq_text = (
        vn(uq.get("public_data_insufficient_rate"), 1, True)
        if "pairs" in uq
        else uq.get("status", "–")
    )
    md.append(f"Chất lượng UNVERIFIED (người gán): {uq_text}\n")
    md += ["## Độ trễ, token, chi phí mỗi ca", ""]
    rows = []
    for c in configs:
        e = cost.get(c) or {}
        rows.append(
            [
                c,
                fmt_est(e.get("latency_s"), 1),
                fmt_est(e.get("input_tokens"), 0),
                fmt_est(e.get("output_tokens"), 0),
                fmt_est(e.get("cost"), 4) if e.get("cost") else "chưa khai giá",
                e.get("llm_calls", 0),
                e.get("cache_hits", 0),
            ]
        )
    md.append(
        md_table(
            [
                "Cấu hình",
                "Độ trễ (s)",
                "Token vào",
                "Token ra",
                f"Tiền ({rs.currency})",
                "Lời gọi LLM",
                "Cache hit",
            ],
            rows,
            d,
        )
    )
    rc = results.get("ranking_common") or {}
    md += [
        f"## RQ1 trên tập ca chung của A/B/C ({len(results.get('common_cases', []))} ca)",
        "",
    ]
    md.append(
        md_table(
            ["Cấu hình", *(f"Hit@{k}" for k in rs.top_ks), "MRR"],
            [
                [
                    c,
                    *(fmt_est((rc.get(c) or {}).get(f"hit@{k}:expected")) for k in rs.top_ks),
                    fmt_est((rc.get(c) or {}).get("mrr:expected")),
                ]
                for c in ("A", "B", "C")
            ],
            d,
        )
    )
    fs = (results.get("human") or {}).get("failures") or {}
    if fs.get("by_cause") and not fs.get("errors"):
        md += ["## Phân tích ca sai của C (người gán nguyên nhân)", ""]
        rows_f = [{"cause": k} for k, n in fs["by_cause"].items() for _ in range(n)]
        md.append(summarize_failures(rows_f, list(fs.get("causes") or fs["by_cause"]), d))
    md += ["## Chỉ số thành công", ""]
    md.append(
        md_table(
            ["Tiêu chí", "Kết quả"], [[x["criterion"], x["result"]] for x in results["success"]], d
        )
    )
    md += ["## Độ tin cậy của nhãn người", ""]
    rows = [
        [
            name,
            json.dumps(info.get("kappa"), ensure_ascii=False) if info.get("kappa") else "–",
            "; ".join(info.get("errors") or [])
            or ("còn bất đồng: " + str(len(info["open"])) if info.get("open") else "ok"),
        ]
        for name, info in results["human"].items()
        if isinstance(info, dict)
    ]
    md.append(md_table(["Bảng", "Kappa", "Tình trạng"], rows, d))
    md += ["![Hit@k](hit_chart.svg)", "", "![Trạng thái](status_heatmap.svg)", ""]
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    paths.append(out / "summary.md")

    pc = results["per_case"]
    header = ["case_id", "split", "anomaly_id", "reference_set", *configs]
    rows_csv = [
        {
            "case_id": c["case_id"],
            "split": c["split"],
            "anomaly_id": c["anomaly_id"],
            "reference_set": ";".join(c["reference_set"]),
            **{
                cfg: ""
                if pc.get(c["case_id"], {}).get(cfg) is None
                else f"{pc[c['case_id']][cfg]:.3f}"
                for cfg in configs
            },
        }
        for c in results["cases"]
    ]
    write_csv(out / "per_case.csv", header, [*rows_csv, {"case_id": d}])
    (out / "per_case.md").write_text(
        f"# Hit@3 từng ca (trung bình {rs.runs_per_case} lần chạy)\n\n"
        + md_table(header, [[r[h] for h in header] for r in rows_csv], d),
        encoding="utf-8",
    )
    write_csv(
        out / "rq3.csv",
        ["hypothesis_id", "evaluated", "unverified", "unverified_rate", "top_reasons"],
        [
            *({**r, "top_reasons": "; ".join(r["top_reasons"])} for r in results["rq3"]),
            {"hypothesis_id": d},  # câu "khảo sát khả thi" ở dòng cuối mọi bảng CSV (spec 15)
        ],
    )
    paths += [out / "per_case.csv", out / "per_case.md", out / "rq3.csv"]
    # ca minh họa (spec 15.8): mỗi ca một trang từ lần chạy đầu của C; nhóm chọn ca để trình bày
    seen: set[str] = set()
    for rec in sorted(
        (r for r in records if r.config == "C" and r.status == "ok"),
        key=lambda r: (r.case_id, r.run_id),
    ):
        if rec.case_id in seen:
            continue
        seen.add(rec.case_id)
        md_case, svg_case = case_illustration(rec, codes, d)
        (out / f"case_{rec.case_id}.md").write_text(md_case, encoding="utf-8")
        (out / f"case_{rec.case_id}_series.svg").write_text(svg_case, encoding="utf-8")
        paths += [out / f"case_{rec.case_id}.md", out / f"case_{rec.case_id}_series.svg"]
    (out / "hit_chart.svg").write_text(svg_hit_chart(rk, configs, rs.top_ks, d), encoding="utf-8")
    labels = {h: f"{codes.get(h, '')} {h}".strip() for h in codes}
    heat = M.status_counts(records, "C", lambda h: labels.get(h, h))
    (out / "status_heatmap.svg").write_text(svg_heatmap(heat, d), encoding="utf-8")
    paths += [out / "hit_chart.svg", out / "status_heatmap.svg"]
    return paths


def compute_results(
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    rs: RunnerSettings,
    other_id: str,
    *,
    mapping: Mapping[tuple[str, str, str, int], str] | None,
    ratings: Mapping[str, str] | None,
    citation_key: Mapping[tuple[str, str, str, int, int], str] | None,
    human: Mapping[str, Any],
    header: Sequence[str],
) -> dict[str, Any]:
    boot = M.BootCfg(rs.bootstrap_samples, rs.bootstrap_seed, rs.bootstrap_level)
    ok = [r for r in records if r.status == "ok"]
    errors = [
        f"{r.case_id} {r.config} {r.run_id}: {r.error.splitlines()[0]}"
        for r in records
        if r.status != "ok"
    ]
    results: dict[str, Any] = {
        "header": list(header)
        + ([f"{len(errors)} lần chạy lỗi (bị loại khỏi metric)"] if errors else []),
        "errors": errors,
        "disclaimer": rs.disclaimer,
        "cases": [c.to_dict() for c in cases],
        "ranking": M.ranking_metrics(
            ok,
            cases,
            top_ks=rs.top_ks,
            other_id=other_id,
            other_counts=rs.other_counts_as_hit,
            mapping=mapping,
            boot=boot,
        ),
        "per_case": M.per_case_hits(
            ok,
            cases,
            k=max(rs.top_ks),
            other_id=other_id,
            other_counts=rs.other_counts_as_hit,
            mapping=mapping,
        ),
        "report": M.report_metrics(ok, ratings=ratings, citation_key=citation_key, boot=boot),
        "verification": M.verification_metrics(
            ok, cases, configs=[c for c in rs.configs if c in ("C", "no_rag")]
        ),
        "rq3": M.rq3_table(ok, "C"),
        "cost": M.cost_metrics(
            ok, input_per_mtok=rs.input_per_mtok, output_per_mtok=rs.output_per_mtok, boot=boot
        ),
        "human": dict(human),
        "unverified_quality": human.get("unverified"),
    }
    # so sánh A/B/C trên CÙNG tập ca (ca lỗi/chưa ánh xạ ở một cấu hình bị bỏ ở cả ba)
    common = M.common_cases(ok, cases, ("A", "B", "C"), mapping)
    results["common_cases"] = sorted(common)
    results["ranking_common"] = M.ranking_metrics(
        [r for r in ok if r.case_id in common and r.config in ("A", "B", "C")],
        cases,
        top_ks=rs.top_ks,
        other_id=other_id,
        other_counts=rs.other_counts_as_hit,
        mapping=mapping,
        boot=boot,
    )
    other_only = [
        c.case_id
        for c in cases
        if c.reference_set and not M.scoring_refs(c, other_id, rs.other_counts_as_hit)
    ]
    if other_only:
        results["header"].append(
            f"{len(other_only)} ca có đáp án chỉ gồm `{other_id}` (không cấu hình nào trúng được "
            f"khi `other` không tính trúng): {', '.join(other_only)}"
        )
    results["success"] = success_check(results)
    return results
