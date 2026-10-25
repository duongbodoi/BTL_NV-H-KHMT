"""Metric đánh giá (spec mục 15.3, 15.4, 15.6). Hàm thuần trên RunRecord + đáp án; không LLM.

Hòa hạng (spec 12.2: hòa thì chia chung hạng): Hit@k và MRR tính theo KỲ VỌNG khi phá hòa ngẫu
nhiên (đúng cho mọi kích thước nhóm hòa), kèm cận lạc quan (giả thuyết đúng đứng đầu nhóm hòa) và
bi quan (đứng cuối). Một ca: trung bình trên các lần chạy; cả tập: trung bình trên ca, CI bootstrap
theo ca (seed cố định), độ lệch chuẩn giữa các lần chạy.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from steel_xai.contracts import CaseRecord, RunRecord

Entry = tuple[int, frozenset[str]]  # (hạng, các id taxonomy của mục đó)


# ---------------- một bảng xếp hạng


def _groups(ranking: Sequence[Entry]) -> list[list[frozenset[str]]]:
    by: dict[int, list[frozenset[str]]] = defaultdict(list)
    for r, ids in ranking:
        by[r].append(ids)
    return [by[r] for r in sorted(by)]


def hit_at_k(
    ranking: Sequence[Entry], refs: frozenset[str], k: int, mode: str = "expected"
) -> float:
    """Xác suất có ≥1 mục đúng trong k vị trí đầu. mode: expected | optimistic | pessimistic."""
    pos = 0
    for g in _groups(ranking):
        s, c = len(g), sum(1 for ids in g if ids & refs)
        if pos >= k:
            return 0.0
        m = min(s, k - pos)  # số vị trí của nhóm còn nằm trong top-k
        if c:
            if mode == "optimistic":
                return 1.0
            if mode == "pessimistic":
                return 1.0 if m > s - c else 0.0
            return 1.0 - math.comb(s - c, m) / math.comb(s, m)
        pos += s
    return 0.0


def reciprocal_rank(
    ranking: Sequence[Entry], refs: frozenset[str], mode: str = "expected"
) -> float:
    """1/vị trí của mục đúng đầu tiên (0 nếu không có). Kỳ vọng khi phá hòa ngẫu nhiên."""
    pos = 0
    for g in _groups(ranking):
        s, c = len(g), sum(1 for ids in g if ids & refs)
        if c:
            if mode == "optimistic":
                return 1.0 / (pos + 1)
            if mode == "pessimistic":
                return 1.0 / (pos + s - c + 1)
            total = math.comb(s, c)
            # P(mục đúng đầu tiên ở vị trí j trong nhóm) = C(s-j, c-1) / C(s, c)
            return sum(math.comb(s - j, c - 1) / total / (pos + j) for j in range(1, s - c + 2))
        pos += s
    return 0.0


def top_ids(ranking: Sequence[Entry], k: int) -> list[str]:
    """Id có hạng ≤ k (hòa ở hạng k thì lấy cả nhóm), theo thứ tự hạng."""
    return [i for r, ids in sorted(ranking, key=lambda x: x[0]) if r <= k for i in sorted(ids)]


# ---------------- tổng hợp


@dataclass(frozen=True)
class Estimate:
    mean: float | None
    low: float | None
    high: float | None
    run_std: float | None  # độ lệch chuẩn của giá trị cả tập giữa các lần chạy
    n_cases: int
    n_runs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": self.mean,
            "ci_low": self.low,
            "ci_high": self.high,
            "run_std": self.run_std,
            "n_cases": self.n_cases,
            "n_runs": self.n_runs,
        }


def bootstrap_ci(
    values: Sequence[float], samples: int, seed: int, level: float
) -> tuple[float | None, float | None]:
    """CI bootstrap phân vị cho trung bình (lấy mẫu lại theo ca)."""
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(samples))
    lo = means[int(math.floor((1 - level) / 2 * (samples - 1)))]
    hi = means[int(math.ceil((1 + level) / 2 * (samples - 1)))]
    return lo, hi


def aggregate(
    per_case_run: Mapping[str, Mapping[str, float | None]],
    samples: int,
    seed: int,
    level: float,
) -> Estimate:
    """per_case_run[ca][lần] = giá trị (None = không áp dụng). Ca không có giá trị nào bị bỏ."""
    case_means: dict[str, float] = {}
    by_run: dict[str, list[float]] = defaultdict(list)
    for case, runs in per_case_run.items():
        vals = [v for v in runs.values() if v is not None]
        if vals:
            case_means[case] = sum(vals) / len(vals)
        for run, v in runs.items():
            if v is not None:
                by_run[run].append(v)
    if not case_means:
        return Estimate(None, None, None, None, 0, 0)
    vals = [case_means[c] for c in sorted(case_means)]
    lo, hi = bootstrap_ci(vals, samples, seed, level)
    run_means = [sum(v) / len(v) for v in by_run.values() if v]
    std = statistics.stdev(run_means) if len(run_means) > 1 else None
    return Estimate(sum(vals) / len(vals), lo, hi, std, len(vals), len(by_run))


# ---------------- xếp hạng → id taxonomy


def ranking_entries(
    rec: RunRecord, mapping: Mapping[tuple[str, str, str, int], str] | None
) -> list[Entry] | None:
    """Bảng xếp hạng của một lần chạy dưới dạng id taxonomy. Cấu hình tự do cần ánh xạ mù
    (case, config, run, VỊ TRÍ trong danh sách) → id (vị trí, không phải hạng: các mục hòa hạng có
    khóa riêng); thiếu ánh xạ cho bất kỳ mục nào → None (chưa chấm được)."""
    ranking = rec.outputs.get("ranking") or []
    if rec.outputs.get("mode") == "library":
        return [(int(r["rank"]), frozenset({r["hypothesis_id"]})) for r in ranking]
    if mapping is None:
        return None
    out: list[Entry] = []
    for pos, r in enumerate(ranking, start=1):
        key = (rec.case_id, rec.config, rec.run_id, pos)
        if key not in mapping:
            return None
        out.append((int(r["rank"]), frozenset({mapping[key]})))
    return out


def scoring_refs(case: CaseRecord, other_id: str, other_counts: bool) -> frozenset[str]:
    refs = frozenset(case.reference_set)
    return refs if other_counts else refs - {other_id}


@dataclass(frozen=True)
class BootCfg:
    samples: int
    seed: int
    level: float


def _agg(table: Mapping[str, Mapping[str, float | None]], b: BootCfg) -> Estimate:
    return aggregate(table, b.samples, b.seed, b.level)


def ranking_metrics(
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    *,
    top_ks: Sequence[int],
    other_id: str,
    other_counts: bool,
    mapping: Mapping[tuple[str, str, str, int], str] | None,
    boot: BootCfg,
) -> dict[str, dict[str, Any]]:
    """RQ1: Hit@k, MRR (kỳ vọng + cận) theo cấu hình. Ca thiếu đáp án bị bỏ (đếm lại)."""
    by_case = {c.case_id: c for c in cases if c.reference_set}
    tables: dict[str, dict[str, dict[str, dict[str, float | None]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(dict))
    )
    unscored: Counter[str] = Counter()
    for rec in records:
        case = by_case.get(rec.case_id)
        if case is None or rec.status != "ok":
            unscored[rec.config] += 1
            continue
        entries = ranking_entries(rec, mapping)
        if entries is None:
            unscored[rec.config] += 1
            continue
        refs = scoring_refs(case, other_id, other_counts)
        t = tables[rec.config]
        for k in top_ks:
            for mode in ("expected", "optimistic", "pessimistic"):
                t[f"hit@{k}:{mode}"][rec.case_id][rec.run_id] = hit_at_k(entries, refs, k, mode)
        for mode in ("expected", "optimistic", "pessimistic"):
            t[f"mrr:{mode}"][rec.case_id][rec.run_id] = reciprocal_rank(entries, refs, mode)
    out: dict[str, dict[str, Any]] = {}
    for config, t in tables.items():
        out[config] = {name: _agg(v, boot).to_dict() for name, v in t.items()}
        out[config]["unscored_runs"] = unscored.get(config, 0)
    for config, n in unscored.items():
        out.setdefault(config, {"unscored_runs": n})
    return out


def per_case_hits(
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    *,
    k: int,
    other_id: str,
    other_counts: bool,
    mapping: Mapping[tuple[str, str, str, int], str] | None,
) -> dict[str, dict[str, float | None]]:
    """[ca][cấu hình] = Hit@k kỳ vọng trung bình trên các lần chạy (bảng từng ca, spec 15.4)."""
    by_case = {c.case_id: c for c in cases if c.reference_set}
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rec in records:
        case = by_case.get(rec.case_id)
        entries = ranking_entries(rec, mapping) if rec.status == "ok" else None
        if case is None or entries is None:
            continue
        acc[rec.case_id][rec.config].append(
            hit_at_k(entries, scoring_refs(case, other_id, other_counts), k)
        )
    return {
        c: {cfg: (sum(v) / len(v) if v else None) for cfg, v in d.items()} for c, d in acc.items()
    }


# ---------------- RQ2: báo cáo


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def report_metrics(
    records: Sequence[RunRecord],
    *,
    ratings: Mapping[str, str] | None,
    citation_key: Mapping[tuple[str, str, str, int, int], str] | None,
    boot: BootCfg,
) -> dict[str, dict[str, Any]]:
    """Numeric mismatch (bản nháp đầu; C có thêm bản cuối), unsupported claim rate, citation
    (kiểm nguyên văn bằng code; và tỷ lệ được người chấm là hỗ trợ khi đã có bảng chấm)."""
    T = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    unrated: Counter[str] = Counter()
    for rec in records:
        rep = rec.outputs.get("report")
        if rec.status != "ok" or not rep:
            continue
        t, c, r = T[rec.config], rec.case_id, rec.run_id
        if "status" in rep:  # C: báo cáo bị bộ kiểm từ chối hết số lần (chỉ còn phần code dựng)
            t["report_rejected"][c][r] = float(rep["status"] in ("rejected", "llm_error"))
        first = rep.get("first_draft") or {}
        if first:
            t["numeric_mismatch_first"][c][r] = float(first["numeric_mismatches"])
            t["unsupported_rate_first"][c][r] = _ratio(first["unsupported"], first["claims"])
            t["forbidden_first"][c][r] = float(first["forbidden"])
            t["claims_first"][c][r] = float(first["claims"])
        final = rep.get("final")
        if final:
            t["numeric_mismatch_final"][c][r] = float(final["numeric_mismatches"])
            t["unsupported_rate_final"][c][r] = _ratio(final["unsupported"], final["claims"])
            t["claims_final"][c][r] = float(final["claims"])
        cits = rep.get("citations") or []
        t["citation_verbatim_rate"][c][r] = _ratio(
            sum(bool(x["verified"]) for x in cits), len(cits)
        )
        if ratings is not None and citation_key is not None and cits:
            ok = 0
            rated = 0
            for idx, x in enumerate(cits):
                if not x["verified"]:
                    rated += 1  # trích dẫn không qua kiểm nguyên văn: không hỗ trợ
                    continue
                key = (rec.case_id, rec.config, rec.run_id, int(x["claim_index"]), idx)
                iid = citation_key.get(key)
                if iid is None or iid not in ratings:
                    rated = -1
                    break
                rated += 1
                ok += ratings[iid] == "supports"
            t["citation_support_rate"][c][r] = _ratio(ok, rated) if rated >= 0 else None
            unrated[rec.config] += rated < 0
    out: dict[str, dict[str, Any]] = {}
    for cfg, t in T.items():
        out[cfg] = {name: _agg(v, boot).to_dict() for name, v in t.items()}
        out[cfg]["citation_unrated_runs"] = unrated[cfg]  # chạy sau khi sinh bảng chấm / chưa chấm
    return out


# ---------------- RQ2/RQ3: trạng thái


def verification_metrics(
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    *,
    configs: Iterable[str],
    top_k: int = 3,
) -> dict[str, dict[str, Any]]:
    """Verification accuracy và false-SUPPORTED trên cặp (ca, giả thuyết) thuộc top-k của C ở cùng
    lần chạy, chỉ cặp có trạng thái chuẩn (spec 15.3). Cấu hình khác được chấm trên cùng tập cặp."""
    ref = {c.case_id: c.reference_statuses for c in cases if c.reference_statuses}
    c_top: dict[tuple[str, str], set[str]] = {}
    status: dict[tuple[str, str, str], dict[str, str]] = {}
    for rec in records:
        if rec.status != "ok" or rec.case_id not in ref:
            continue
        st = {
            r["hypothesis_id"]: r["status"]
            for r in rec.outputs.get("ranking") or []
            if r.get("status") and r.get("hypothesis_id")
        }
        status[(rec.config, rec.case_id, rec.run_id)] = st
        if rec.config == "C":
            c_top[(rec.case_id, rec.run_id)] = {
                r["hypothesis_id"] for r in rec.outputs["ranking"] if r["rank"] <= top_k
            }
    out: dict[str, dict[str, Any]] = {}
    for cfg in configs:
        pairs = correct = sup = false_sup = 0
        for (case, run), hyps in c_top.items():
            st = status.get((cfg, case, run))
            if st is None:
                continue
            for h in hyps:
                gold = ref[case].get(h)
                got = st.get(h)
                if gold is None or got is None:
                    continue
                pairs += 1
                correct += got == gold
                if got == "SUPPORTED":
                    sup += 1
                    false_sup += gold != "SUPPORTED"
        out[cfg] = {
            "pairs": pairs,
            "verification_accuracy": _ratio(correct, pairs),
            "supported_pairs": sup,
            "false_supported_rate": _ratio(false_sup, sup),
        }
    return out


def status_counts(
    records: Sequence[RunRecord], config: str, label: Callable[[str], str] = lambda h: h
) -> dict[str, Counter[str]]:
    """[giả thuyết][trạng thái] = số lần (mọi ca, mọi lần chạy) — heatmap spec 15.8."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for rec in records:
        if rec.config != config or rec.status != "ok":
            continue
        for r in rec.outputs.get("ranking") or []:
            if r.get("status"):
                out[label(r["hypothesis_id"])][r["status"]] += 1
    return dict(out)


def missing_reason(f: Mapping[str, Any]) -> str:
    q = f.get("quant") or {}
    why = (q.get("reason") if isinstance(q, dict) else "") or f.get("detail") or "không rõ"
    return str(why).split(";")[0].strip()[:90]


def rq3_table(records: Sequence[RunRecord], config: str = "C") -> list[dict[str, Any]]:
    """RQ3: mỗi giả thuyết: số lần được kiểm, tỷ lệ UNVERIFIED, lý do thiếu hay gặp nhất (do code
    ghi ở các check không kiểm được). Nguyên nhân THẬT của UNVERIFIED do người gán (bảng riêng)."""
    seen: Counter[str] = Counter()
    unv: Counter[str] = Counter()
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    for rec in records:
        if rec.config != config or rec.status != "ok":
            continue
        for v in rec.outputs.get("verdicts") or []:
            h = v["hypothesis_id"]
            seen[h] += 1
            if v["status"] == "UNVERIFIED":
                unv[h] += 1
                for f in v.get("missing") or []:
                    if f.get("importance") == "critical":
                        reasons[h][missing_reason(f)] += 1
                        kinds[h][f.get("kind", "?")] += 1
    rows = []
    for h in sorted(seen, key=lambda x: (-unv[x] / seen[x], x)):
        rows.append(
            {
                "hypothesis_id": h,
                "evaluated": seen[h],
                "unverified": unv[h],
                "unverified_rate": unv[h] / seen[h],
                "missing_kinds": dict(kinds[h]),
                "top_reasons": [f"{r} (×{n})" for r, n in reasons[h].most_common(3)],
            }
        )
    return rows


# ---------------- chi phí


def cost_metrics(
    records: Sequence[RunRecord],
    *,
    input_per_mtok: float | None,
    output_per_mtok: float | None,
    boot: BootCfg,
) -> dict[str, dict[str, Any]]:
    """Độ trễ, token, tiền mỗi ca (đo thật; spec 15.3). Giá chưa khai → không tính tiền. Lần chạy có
    lời gọi lấy từ cache (token tính tiền = 0, độ trễ gần 0) bị loại khỏi số đo và được đếm."""
    T = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    calls: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    cached: Counter[str] = Counter()
    for rec in records:
        if rec.status != "ok":
            continue
        calls[rec.config] += rec.llm.get("calls", 0)
        hits[rec.config] += rec.llm.get("cache_hits", 0)
        if rec.llm.get("cache_hits", 0):
            cached[rec.config] += 1
            continue
        t, c, r = T[rec.config], rec.case_id, rec.run_id
        tin, tout = rec.tokens.get("input", 0), rec.tokens.get("output", 0)
        t["latency_s"][c][r] = rec.latency_s
        t["input_tokens"][c][r] = float(tin)
        t["output_tokens"][c][r] = float(tout)
        if input_per_mtok is not None and output_per_mtok is not None:
            t["cost"][c][r] = (tin * input_per_mtok + tout * output_per_mtok) / 1e6
    out = {}
    for cfg in set(T) | set(calls):
        out[cfg] = {name: _agg(v, boot).to_dict() for name, v in T.get(cfg, {}).items()}
        out[cfg]["llm_calls"] = calls[cfg]
        out[cfg]["cache_hits"] = hits[cfg]
        out[cfg]["cached_runs_excluded"] = cached[cfg]
    return out


def common_cases(
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    configs: Sequence[str],
    mapping: Mapping[tuple[str, str, str, int], str] | None,
) -> set[str]:
    """Ca có đáp án mà MỌI cấu hình trong `configs` đều có ít nhất một lần chạy chấm được: so sánh
    giữa cấu hình phải trên cùng tập ca (ca lỗi ở một cấu hình không làm lệch so sánh)."""
    ref = {c.case_id for c in cases if c.reference_set}
    ok: dict[str, set[str]] = defaultdict(set)
    for rec in records:
        if rec.status == "ok" and rec.case_id in ref and ranking_entries(rec, mapping) is not None:
            ok[rec.config].add(rec.case_id)
    sets = [ok.get(c, set()) for c in configs]
    return set.intersection(*sets) if sets else set()


# ---------------- ca sai (spec 15.5)


def failure_cases(
    records: Sequence[RunRecord],
    cases: Sequence[CaseRecord],
    *,
    other_id: str,
    other_counts: bool,
    k: int = 3,
) -> list[dict[str, str]]:
    """Ca TEST mà giả thuyết đúng không nằm trong top-k của C ở QUÁ NỬA số lần chạy (một lần chạy
    tính là trượt khi Hit@k kỳ vọng < 0,5). Thiếu đáp án/kết quả C → ValueError, không bỏ qua."""
    runs: dict[str, list[RunRecord]] = defaultdict(list)
    for rec in records:
        if rec.config == "C" and rec.status == "ok":
            runs[rec.case_id].append(rec)
    out = []
    for c in cases:
        if c.split != "test":
            continue
        if not c.reference_set:
            raise ValueError(f"ca test {c.case_id} chưa có đáp án cuối")
        rs = sorted(runs.get(c.case_id, []), key=lambda r: r.run_id)
        if not rs:
            raise ValueError(f"ca test {c.case_id} không có kết quả C")
        refs = scoring_refs(c, other_id, other_counts)
        misses = []
        for rec in rs:
            entries = ranking_entries(rec, None) or []
            if hit_at_k(entries, refs, k) < 0.5:
                misses.append(rec)
        if len(misses) * 2 > len(rs):
            out.append(
                {
                    "case_id": c.case_id,
                    "anomaly_id": c.anomaly_id,
                    "reference_set": ";".join(c.reference_set),
                    "c_top3": ";".join(top_ids(ranking_entries(misses[0], None) or [], k)),
                    "missed_runs": f"{len(misses)}/{len(rs)}",
                }
            )
    return out
