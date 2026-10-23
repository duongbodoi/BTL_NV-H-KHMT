"""Phát hiện bất thường tồn kho và biên lợi nhuận gộp (spec mục 8).

Deterministic, không LLM. Chỉ so YoY theo năm tài chính. Nền lịch sử và peer chỉ dùng thông tin có
sẵn tại kỳ đang xét: năm sau kỳ đó và mọi năm từ `reference_only_from_year` trở đi bị loại ngay từ
đầu vào, và peer có năm tài chính kết thúc SAU công ty đang xét bị loại (số của họ chưa tồn tại ở
mốc as-of của công ty) (spec mục 3.3, 7.2). Bước này không gán nhãn tốt/xấu.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from steel_xai.anomaly.settings import (
    INVENTORY,
    AnomalySettings,
    TypeSettings,
    load_anomaly_settings,
)
from steel_xai.contracts import Anomaly, FinancialFact, ValueStatus

FLAG_WEAK_BASELINE = "weak_baseline"
FLAG_PEER_MISSING = "peer_missing"
FLAG_PEER_FISCAL_END = "peer_fiscal_end_differs"
FLAG_PEER_LATER_END = "peer_excluded_later_fiscal_end"
FLAG_FISCAL_END_UNKNOWN = "fiscal_end_unknown"
FLAG_PROVISIONAL = "provisional_thresholds"
FLAG_ASOF_UNKNOWN = "asof_date_unknown"
FLAG_MIXED_DIRECTION = "mixed_direction"
FLAG_ZERO_VARIANCE = "zero_variance"
FLAG_DAYS_MISSING = "days_in_period_missing"

REASON_NO_DATA = "no_data"

_ROBUST = Decimal("0.6745")  # hệ số đưa MAD về thang độ lệch chuẩn của phân phối chuẩn
_Q = Decimal("0.000001")
_ALLOWED_UNITS = ("VND", "ratio")

_INVENTORY_INPUTS = (
    "inventory_total",
    "inventory_gross",
    "net_revenue",
    "cogs",
    "raw_materials",
    "finished_goods",
    "work_in_progress",
)
_GM_INPUTS = ("gross_margin", "gross_profit", "net_revenue", "cogs")


@dataclass(frozen=True)
class Evaluation:
    """Một (công ty, năm, loại) đã được xét — dù có bị gắn cờ hay không (mẫu số của tỷ lệ)."""

    company: str
    period: str
    type: str
    evaluable: bool
    reason: str  # "" nếu xét được; ngược lại "no_data" hoặc "missing:<chỉ báo bắt buộc>"
    flagged: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "company": self.company,
            "period": self.period,
            "type": self.type,
            "evaluable": self.evaluable,
            "reason": self.reason,
            "flagged": self.flagged,
        }


@dataclass(frozen=True)
class DetectionResult:
    anomalies: tuple[Anomaly, ...]
    evaluations: tuple[Evaluation, ...]
    notes: tuple[str, ...] = field(default=())  # fact bị loại khỏi đầu vào (trùng khóa, sai đơn vị)

    def summary(self) -> dict[str, dict[str, object]]:
        """Số ca bị gắn cờ trên tổng số kỳ đã xét được, theo loại (spec mục 8.4)."""
        out: dict[str, dict[str, object]] = {}
        for t in sorted({e.type for e in self.evaluations}):
            ev = [e for e in self.evaluations if e.type == t]
            ok = [e for e in ev if e.evaluable]
            flagged = [e for e in ok if e.flagged]
            out[t] = {
                "periods_total": len(ev),
                "periods_evaluated": len(ok),
                "periods_flagged": len(flagged),
                "anomaly_rate": None if not ok else str(Decimal(len(flagged)) / len(ok)),
                "not_evaluable": len(ev) - len(ok),
            }
        return out

    def to_dict(self) -> dict[str, object]:
        return {
            "anomalies": [a.to_dict() for a in self.anomalies],
            "evaluations": [e.to_dict() for e in self.evaluations],
            "notes": list(self.notes),
            "summary": self.summary(),
        }


# ---------------- thống kê (Decimal)


def _mean(xs: list[Decimal]) -> Decimal:
    return sum(xs, Decimal(0)) / len(xs)


def _std(xs: list[Decimal]) -> Decimal:
    m = _mean(xs)
    return (sum(((x - m) ** 2 for x in xs), Decimal(0)) / (len(xs) - 1)).sqrt()


def _median(xs: list[Decimal]) -> Decimal:
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _fmt(x: Decimal | None) -> str | None:
    if x is None:
        return None
    try:
        return format(x.quantize(_Q), "f")
    except InvalidOperation:  # số quá lớn so với độ chính xác: giữ nguyên dạng đầy đủ
        return format(x, "f")


def _year(period: str) -> int | None:
    if period.startswith("FY") and period[2:].isdigit():
        return int(period[2:])
    return None


def _end_key(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    last = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return f"{d.month:02d}-end" if d.day == last.day else f"{d.month:02d}-{d.day:02d}"


# ---------------- bảng dữ liệu (không nhìn tương lai)


def _is_comparative(f: FinancialFact) -> bool:
    return "comparative_only" in f.validation_flags or "comparative" in f.validation_flags


class _Panel:
    """Fact OK theo (công ty, năm, metric); loại mọi năm >= `cutoff` (chỉ là đáp án tham chiếu).

    Khóa trùng: ưu tiên fact không phải số so sánh; nếu các giá trị còn lại khác nhau thì KHÔNG dùng
    khóa đó (không chọn theo thứ tự đầu vào). Đơn vị lạ cũng bị loại. Cả hai được ghi vào `notes`.
    """

    def __init__(self, facts: Iterable[FinancialFact], cutoff: int) -> None:
        groups: dict[tuple[str, int, str], list[FinancialFact]] = defaultdict(list)
        notes: set[str] = set()
        for f in facts:
            y = _year(f.period)
            if y is None or y >= cutoff or f.status is not ValueStatus.OK or f.value is None:
                continue
            if f.unit not in _ALLOWED_UNITS:
                notes.add(f"unit_rejected:{f.company}|FY{y}|{f.metric}|{f.unit}")
                continue
            groups[(f.company, y, f.metric)].append(f)
        self._f: dict[tuple[str, int, str], FinancialFact] = {}
        for key, fs in groups.items():
            preferred = [f for f in fs if not _is_comparative(f)] or fs
            if len({f.value for f in preferred}) > 1:
                notes.add(f"duplicate_conflict:{key[0]}|FY{key[1]}|{key[2]}")
                continue
            self._f[key] = min(
                preferred,
                key=lambda f: (
                    f.provenance.doc_id if f.provenance else "",
                    f.provenance.page if f.provenance else 0,
                    f.provenance.block_id or "" if f.provenance else "",
                ),
            )
        self.companies = sorted({k[0] for k in self._f})
        self.notes = tuple(sorted(notes))

    def fact(self, c: str, y: int, m: str) -> FinancialFact | None:
        return self._f.get((c, y, m))

    def val(self, c: str, y: int, m: str) -> Decimal | None:
        f = self._f.get((c, y, m))
        return None if f is None else f.value

    def years(self, c: str) -> list[int]:
        return sorted({k[1] for k in self._f if k[0] == c})


Indicator = Callable[[str, int], "Decimal | None"]  # (công ty, năm) → giá trị


def _growth(p: _Panel, metric: str) -> Indicator:
    def fn(c: str, y: int) -> Decimal | None:
        cur, prev = p.val(c, y, metric), p.val(c, y - 1, metric)
        if cur is None or prev is None or prev <= 0:
            return None
        return cur / prev - 1

    return fn


def _gm_change(p: _Panel) -> Indicator:
    def fn(c: str, y: int) -> Decimal | None:
        cur, prev = p.val(c, y, "gross_margin"), p.val(c, y - 1, "gross_margin")
        return None if cur is None or prev is None else (cur - prev) * 100

    return fn


def _history(fn: Indicator, p: _Panel, c: str, y: int) -> list[Decimal]:
    """Giá trị của cùng chỉ báo ở các năm TRƯỚC `y` (không dùng năm sau)."""
    obs = (fn(c, yy) for yy in p.years(c) if yy < y)
    return [v for v in obs if v is not None]


def _scores(
    x: Decimal | None, obs: list[Decimal], s: AnomalySettings
) -> tuple[Decimal | None, Decimal | None, list[str], list[str]]:
    """(z, robust z, cơ sở đã dùng, cờ). Đủ số lần cho MAD thì dùng MAD, không dùng cả hai."""
    flags: list[str] = []
    if x is None:
        return None, None, [], []
    if len(obs) < s.min_changes_z:
        return None, None, [], [FLAG_WEAK_BASELINE]
    if len(obs) >= s.min_changes_mad:
        med = _median(obs)
        mad = _median([abs(v - med) for v in obs])
        if mad > 0:
            return None, _ROBUST * (x - med) / mad, ["history_mad"], flags
        flags.append(FLAG_ZERO_VARIANCE)
        return None, None, [], flags
    sd = _std(obs)
    if sd > 0:
        return (x - _mean(obs)) / sd, None, ["history_z"], flags
    return None, None, [], [FLAG_ZERO_VARIANCE]


@dataclass
class _Peers:
    median: Decimal | None
    enough: bool
    used: list[str]
    flags: set[str]


def _peers(p: _Panel, fn: Indicator, c: str, y: int, end_metric: str, s: AnomalySettings) -> _Peers:
    """Trung vị chỉ báo của các công ty còn lại cùng năm tài chính.

    Peer có `fiscal_period_end` muộn hơn của công ty đang xét bị loại (cờ riêng): số của họ chưa
    có ở thời điểm công ty đó khóa sổ. Ngày kết thúc thiếu thì giữ peer nhưng gắn cờ.
    """
    own = p.fact(c, y, end_metric)
    own_end = own.fiscal_period_end if own is not None else None
    flags: set[str] = set()
    vals: list[Decimal] = []
    used: list[str] = []
    for o in p.companies:
        if o == c or (v := fn(o, y)) is None:
            continue
        pf = p.fact(o, y, end_metric)
        pe = pf.fiscal_period_end if pf is not None else None
        if own_end and pe and pe > own_end:
            flags.add(FLAG_PEER_LATER_END)
            continue
        if not own_end or not pe:
            flags.add(FLAG_FISCAL_END_UNKNOWN)
        elif _end_key(pe) != _end_key(own_end):
            flags.add(FLAG_PEER_FISCAL_END)
        vals.append(v)
        used.append(o)
    if len(vals) < s.min_peers:
        flags.add(FLAG_PEER_MISSING)
        return _Peers(None, False, [], flags)
    return _Peers(_median(vals), True, used, flags)


# ---------------- chỉ báo theo loại


@dataclass
class _Computed:
    indicators: dict[str, Decimal | None]
    baseline: list[str]
    flags: set[str]
    peers_used: list[str] = field(default_factory=list)


def _inventory(p: _Panel, c: str, y: int, s: AnomalySettings) -> _Computed:
    g_inv, g_rev = _growth(p, "inventory_total"), _growth(p, "net_revenue")
    ind: dict[str, Decimal | None] = {}
    baseline: list[str] = []
    flags: set[str] = set()

    inv_g, rev_g = g_inv(c, y), g_rev(c, y)
    ind["inv_growth_yoy"] = inv_g
    ind["rev_growth_yoy"] = rev_g
    ind["inv_minus_rev_growth"] = None if inv_g is None or rev_g is None else inv_g - rev_g
    if inv_g is not None:
        baseline.append("yoy")

    inv, cogs, rev = (p.val(c, y, m) for m in ("inventory_total", "cogs", "net_revenue"))
    inv0, cogs0, rev0 = (p.val(c, y - 1, m) for m in ("inventory_total", "cogs", "net_revenue"))
    end_missing = False

    def days(year: int) -> int | None:
        nonlocal end_missing
        a, b = p.fact(c, year, "inventory_total"), p.fact(c, year - 1, "inventory_total")
        if a is None or b is None:
            return None  # không có năm liền trước: không có số ngày, không phải lỗi ngày kết thúc
        if not a.fiscal_period_end or not b.fiscal_period_end:
            end_missing = True
            return None
        end, start = a.fiscal_period_end, b.fiscal_period_end
        return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days

    def dio(i: Decimal | None, k: Decimal | None, d: int | None) -> Decimal | None:
        if i is None or k is None or k <= 0 or d is None or d <= 0:
            return None
        return i / k * d

    d_now, d_prev = dio(inv, cogs, days(y)), dio(inv0, cogs0, days(y - 1))
    ind["dio"] = d_now
    ind["dio_prev"] = d_prev
    ind["dio_change_days"] = None if d_now is None or d_prev is None else d_now - d_prev
    if end_missing:
        flags.add(FLAG_DAYS_MISSING)

    def ratio(a: Decimal | None, b: Decimal | None) -> Decimal | None:
        return None if a is None or b is None or b <= 0 else a / b

    r_now, r_prev = ratio(inv, rev), ratio(inv0, rev0)
    ind["inv_to_revenue"] = r_now
    ind["inv_to_revenue_change"] = None if r_now is None or r_prev is None else r_now - r_prev

    gross = p.val(c, y, "inventory_gross")
    for name, metric in (
        ("raw", "raw_materials"),
        ("fg", "finished_goods"),
        ("wip", "work_in_progress"),
    ):
        ind[f"{name}_share"] = ratio(p.val(c, y, metric), gross)

    peers = _peers(p, g_inv, c, y, "inventory_total", s)
    ind["inv_growth_vs_peer"] = (
        None if inv_g is None or peers.median is None else inv_g - peers.median
    )
    if inv_g is not None:
        flags |= peers.flags
        if peers.enough:
            baseline.append("peer")

    z, mz, used, hflags = _scores(inv_g, _history(g_inv, p, c, y), s)
    ind["inv_growth_z"], ind["inv_growth_mad_z"] = z, mz
    baseline += used
    flags.update(hflags)
    return _Computed(ind, baseline, flags, peers.used)


def _gross_margin(p: _Panel, c: str, y: int, s: AnomalySettings) -> _Computed:
    change = _gm_change(p)
    ind: dict[str, Decimal | None] = {}
    baseline: list[str] = []
    flags: set[str] = set()

    gm, gm0 = p.val(c, y, "gross_margin"), p.val(c, y - 1, "gross_margin")
    ch = change(c, y)
    ind["gross_margin"], ind["gross_margin_prev"], ind["gm_change_pp"] = gm, gm0, ch
    if ch is not None:
        baseline.append("yoy")

    def level(o: str, yy: int) -> Decimal | None:
        return p.val(o, yy, "gross_margin")

    peer_level = _peers(p, level, c, y, "gross_margin", s)
    ind["gm_vs_peer_pp"] = (
        None if gm is None or peer_level.median is None else (gm - peer_level.median) * 100
    )

    peers = _peers(p, change, c, y, "gross_margin", s)
    ind["gm_change_vs_peer_pp"] = None if ch is None or peers.median is None else ch - peers.median
    if ch is not None:
        flags |= peers.flags
        if peers.enough:
            baseline.append("peer")

    z, mz, used, hflags = _scores(ch, _history(change, p, c, y), s)
    ind["gm_change_z"], ind["gm_change_mad_z"] = z, mz
    baseline += used
    flags.update(hflags)
    return _Computed(ind, baseline, flags, peers.used)


# ---------------- engine


def detect_anomalies(
    facts: Iterable[FinancialFact],
    settings: AnomalySettings | None = None,
    years: Iterable[int] | None = None,
) -> DetectionResult:
    """Phát hiện bất thường cho mọi (công ty, năm trong giai đoạn gốc).

    Công ty = công ty trong `dataset.companies` hợp với công ty có trong `facts`. Kỳ không có dữ
    liệu vẫn được liệt kê (không xét được, `no_data`) để mẫu số của tỷ lệ bất thường đầy đủ.

    `facts`: fact chính của Phase 3 (nên là đầu ra `validate_facts` để mang cờ validation).
    `years`: ghi đè giai đoạn xét (mặc định `base_period` trong dataset.yaml); năm >= mốc tham chiếu
    vẫn không bao giờ được dùng.
    """
    s = load_anomaly_settings() if settings is None else settings
    panel = _Panel(facts, s.reference_only_from_year)
    wanted = range(s.target_years[0], s.target_years[1] + 1) if years is None else years
    targets = sorted({y for y in wanted if y < s.reference_only_from_year})
    anomalies: list[Anomaly] = []
    evals: list[Evaluation] = []

    for c in sorted(set(panel.companies) | set(s.companies)):
        for y in targets:
            has_data = y in panel.years(c)
            for ts in s.types:
                if not has_data:
                    evals.append(Evaluation(c, f"FY{y}", ts.name, False, REASON_NO_DATA, False))
                    continue
                compute = _inventory if ts.name == INVENTORY else _gross_margin
                computed = compute(panel, c, y, s)
                found = _anomalies_for(panel, s, ts, c, y, computed)
                evaluable = computed.indicators.get(ts.requires) is not None
                reason = "" if evaluable else f"missing:{ts.requires}"
                evals.append(Evaluation(c, f"FY{y}", ts.name, evaluable, reason, bool(found)))
                anomalies += found
    anomalies.sort(key=lambda a: a.anomaly_id)
    evals.sort(key=lambda e: (e.company, e.period, e.type))
    return DetectionResult(tuple(anomalies), tuple(evals), panel.notes)


def _quality_flags(
    panel: _Panel, s: AnomalySettings, c: str, years: Iterable[int], inputs: Iterable[str], tag: str
) -> set[str]:
    out: set[str] = set()
    for yy in years:
        for m in inputs:
            f = panel.fact(c, yy, m)
            if f is None:
                continue
            for fl in f.validation_flags:
                if fl in s.propagate_flags or fl.startswith(s.propagate_prefixes):
                    out.add(f"{tag}:{fl}")
    return out


def _anomalies_for(
    panel: _Panel, s: AnomalySettings, ts: TypeSettings, c: str, y: int, comp: _Computed
) -> list[Anomaly]:
    if comp.indicators.get(ts.requires) is None:
        return []
    hits: dict[str, list[dict[str, str]]] = {"up": [], "down": []}
    ratios: dict[str, list[Decimal]] = {"up": [], "down": []}
    for rule in ts.rules:
        v = comp.indicators.get(rule.indicator)
        if v is None:
            continue
        for direction, thr, hit in (
            ("up", rule.up, rule.up is not None and v >= rule.up),
            ("down", rule.down, rule.down is not None and v <= rule.down),
        ):
            if hit and thr is not None:
                hits[direction].append(
                    {
                        "indicator": rule.indicator,
                        "direction": direction,
                        "value": _fmt(v) or "",
                        "threshold": format(thr, "f"),
                    }
                )
                ratios[direction].append(abs(v) / abs(thr))

    inputs = _INVENTORY_INPUTS if ts.name == INVENTORY else _GM_INPUTS
    input_flags = _quality_flags(panel, s, c, (y, y - 1), inputs, "input")
    earlier = [yy for yy in panel.years(c) if yy < y - 1]
    input_flags |= _quality_flags(panel, s, c, earlier, inputs, "input_history")
    for o in comp.peers_used:
        input_flags |= _quality_flags(panel, s, o, (y, y - 1), inputs, "input_peer")

    both = bool(hits["up"]) and bool(hits["down"])
    asof = s.asof_dates.get(c, {}).get(f"FY{y}")
    out = []
    for direction in ("up", "down"):
        if not hits[direction]:
            continue
        flags = set(comp.flags) | input_flags
        if s.provisional_thresholds:
            flags.add(FLAG_PROVISIONAL)
        if asof is None:
            flags.add(FLAG_ASOF_UNKNOWN)
        if both:
            flags.add(FLAG_MIXED_DIRECTION)
        out.append(
            Anomaly(
                company=c,
                period=f"FY{y}",
                type=ts.name,
                direction=direction,
                indicators={k: _fmt(v) for k, v in sorted(comp.indicators.items())},
                thresholds_hit=tuple(hits[direction]),
                baseline_used=tuple(dict.fromkeys(comp.baseline)),
                severity=max(ratios[direction]),
                flags=tuple(sorted(flags)),
                asof_date=asof,
            )
        )
    return out


# ---------------- danh sách ứng viên cho người chọn ca

CANDIDATE_COLUMNS = (
    "anomaly_id",
    "company",
    "period",
    "type",
    "direction",
    "severity",
    "rules_hit",
    "key_indicators",
    "baseline_used",
    "flags",
    "asof_date",
)


def candidate_rows(result: DetectionResult) -> list[dict[str, str]]:
    """Danh sách bất thường ứng viên, sắp theo mức độ giảm dần, để NGƯỜI chọn ca đánh giá."""
    rows = []
    for a in sorted(result.anomalies, key=lambda a: (-a.severity, a.anomaly_id)):
        keys = [h["indicator"] for h in a.thresholds_hit]
        rows.append(
            {
                "anomaly_id": a.anomaly_id,
                "company": a.company,
                "period": a.period,
                "type": a.type,
                "direction": a.direction,
                "severity": format(a.severity.quantize(Decimal("0.01")), "f"),
                "rules_hit": ";".join(keys),
                "key_indicators": ";".join(f"{k}={a.indicators.get(k)}" for k in keys),
                "baseline_used": ";".join(a.baseline_used),
                "flags": ";".join(a.flags),
                "asof_date": a.asof_date or "",
            }
        )
    return rows


def write_candidates_csv(result: DetectionResult, path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CANDIDATE_COLUMNS)
        w.writeheader()
        w.writerows(candidate_rows(result))
