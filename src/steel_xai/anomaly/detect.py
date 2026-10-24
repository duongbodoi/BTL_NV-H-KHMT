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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from steel_xai.anomaly.settings import (
    INVENTORY,
    AnomalySettings,
    AsofEntry,
    TypeSettings,
    is_peer_indicator,
    load_anomaly_settings,
)
from steel_xai.contracts import Anomaly, FinancialFact, ValueStatus

FLAG_WEAK_BASELINE = "weak_baseline"
FLAG_PEER_MISSING = "peer_missing"
FLAG_PEER_FISCAL_END = "peer_fiscal_end_differs"
FLAG_PEER_LATER_END = "peer_excluded_later_fiscal_end"
FLAG_PEER_LATER_ASOF = "peer_excluded_later_asof"
FLAG_PEER_ONLY_HIT = "peer_only_hit"
FLAG_FISCAL_END_UNKNOWN = "fiscal_end_unknown"
FLAG_PROVISIONAL = "provisional_thresholds"
FLAG_ASOF_UNKNOWN = "asof_date_unknown"
FLAG_MIXED_DIRECTION = "mixed_direction"
FLAG_OPPOSITE_ONLY = "opposite_side_hit_only"
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
class RuleScore:
    """Điểm một luật: `score` = |giá trị| / |ngưỡng| ở phía của dấu giá trị; `hit` = score >= 1."""

    indicator: str
    value: Decimal | None
    score: Decimal | None
    side: str | None  # "up" nếu giá trị > 0, "down" nếu < 0
    hit: bool


@dataclass(frozen=True)
class CandidateRow:
    """Một cặp (công ty, năm tài chính, loại) xét được — kể cả khi chưa vượt ngưỡng."""

    company: str
    period: str
    type: str
    direction: str | None  # dấu biến động của chính công ty; None nếu không đổi
    exceeded: bool  # có luật CÙNG PHÍA với hướng vượt ngưỡng
    candidate: bool  # exceeded, không mixed_direction, không chỉ do luật peer
    mixed_direction: bool
    deviation: Decimal  # điểm lớn nhất của các luật cùng phía với hướng (thang để xếp hạng)
    severity: Decimal  # điểm lớn nhất của các luật cùng phía ĐÃ vượt ngưỡng (0 nếu chưa)
    rules: tuple[RuleScore, ...]
    rules_hit: tuple[str, ...]
    rules_hit_opposite: tuple[str, ...]
    peer_relative: str | None  # above | below | within | None (thuộc tính phụ)
    peer_value: str | None
    baseline_used: tuple[str, ...]
    flags: tuple[str, ...]
    asof_date: str | None
    asof_source: str | None
    indicators: dict[str, str | None]


@dataclass(frozen=True)
class DetectionResult:
    anomalies: tuple[Anomaly, ...]
    evaluations: tuple[Evaluation, ...]
    notes: tuple[str, ...] = field(default=())  # fact bị loại khỏi đầu vào (trùng khóa, sai đơn vị)
    rows: tuple[CandidateRow, ...] = field(default=())  # mọi cặp xét được, xếp theo mức lệch
    rule_order: tuple[str, ...] = field(default=())  # thứ tự cột luật trong bảng ứng viên

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
                "periods_candidate": sum(1 for r in self.rows if r.type == t and r.candidate),
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
        # Kỳ đang xét: số so sánh (từ BCTC năm SAU) của năm >= horizon bị coi là chưa có.
        self.horizon = cutoff
        self.asof: dict[tuple[str, int], str] = {}

    def _usable(self, key: tuple[str, int, str]) -> FinancialFact | None:
        f = self._f.get(key)
        if f is None or (_is_comparative(f) and key[1] >= self.horizon):
            return None
        return f

    def fact(self, c: str, y: int, m: str) -> FinancialFact | None:
        return self._usable((c, y, m))

    def val(self, c: str, y: int, m: str) -> Decimal | None:
        f = self._usable((c, y, m))
        return None if f is None else f.value

    def years(self, c: str) -> list[int]:
        return sorted({k[1] for k in self._f if k[0] == c and self._usable(k) is not None})


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
        own_asof, peer_asof = p.asof.get((c, y)), p.asof.get((o, y))
        if own_asof and peer_asof and peer_asof > own_asof:
            flags.add(FLAG_PEER_LATER_ASOF)  # peer công bố sau as-of của công ty đang xét
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


def _score_rules(ts: TypeSettings, indicators: dict[str, Decimal | None]) -> tuple[RuleScore, ...]:
    """Điểm từng luật: |giá trị| / |ngưỡng| ở phía của dấu giá trị (>= 1: vượt ngưỡng phía đó)."""
    out = []
    for rule in ts.rules:
        v = indicators.get(rule.indicator)
        if v is None or v == 0:
            out.append(RuleScore(rule.indicator, v, None, None, False))
            continue
        side = "up" if v > 0 else "down"
        thr = rule.up if side == "up" else rule.down
        if thr is None:
            out.append(RuleScore(rule.indicator, v, None, side, False))
            continue
        score = abs(v) / abs(thr)
        out.append(RuleScore(rule.indicator, v, score, side, score >= 1))
    return tuple(out)


def _peer_position(
    ts: TypeSettings, scores: tuple[RuleScore, ...]
) -> tuple[str | None, str | None]:
    """(vị trí so với nhóm ngành, giá trị chỉ báo peer). Chỉ là thuộc tính phụ."""
    for r in scores:
        if is_peer_indicator(r.indicator):
            if r.value is None:
                return None, None
            if r.score is not None and r.score >= 1:
                return ("above" if r.side == "up" else "below"), _fmt(r.value)
            return "within", _fmt(r.value)
    return None, None


def detect_anomalies(
    facts: Iterable[FinancialFact],
    settings: AnomalySettings | None = None,
    years: Iterable[int] | None = None,
    asof_extracted: Mapping[tuple[str, str], AsofEntry] | None = None,
) -> DetectionResult:
    """Chấm điểm mọi (công ty, năm tài chính trong cửa sổ chọn ca, loại) và phát hiện bất thường.

    Công ty = công ty trong `dataset.companies` hợp với công ty có trong `facts`. Kỳ không có dữ
    liệu vẫn được liệt kê (không xét được) để mẫu số của tỷ lệ bất thường đầy đủ. Mọi kỳ xét được
    đều có một `CandidateRow` (kể cả chưa vượt ngưỡng) để nhóm chọn ngưỡng và chọn ca.

    `facts`: fact chính của Phase 3 (nên là đầu ra `validate_facts` để mang cờ validation).
    `years`: ghi đè cửa sổ cho mọi công ty; năm >= mốc tham chiếu vẫn không bao giờ được dùng.
    `asof_extracted`: (công ty, kỳ) → ngày công bố trích tự động (vd. ngày ký báo cáo kiểm toán);
    khai tay trong config (`manual`) được ưu tiên hơn.
    """
    s = load_anomaly_settings() if settings is None else settings
    panel = _Panel(facts, s.reference_only_from_year)
    asof_auto = dict(asof_extracted or {})
    for o in set(panel.companies) | set(s.companies):
        for yy in range(1990, s.reference_only_from_year):
            a = s.asof_dates.get(o, {}).get(f"FY{yy}") or asof_auto.get((o, f"FY{yy}"))
            if a is not None and a.date:
                panel.asof[(o, yy)] = a.date
    anomalies: list[Anomaly] = []
    evals: list[Evaluation] = []
    rows: list[CandidateRow] = []

    for c in sorted(set(panel.companies) | set(s.companies)):
        if years is None:
            first = max(s.target_years[0], s.company_start_year.get(c, s.target_years[0]))
            wanted: Iterable[int] = range(first, s.target_years[1] + 1)
        else:
            wanted = years
        for y in sorted({y for y in wanted if y < s.reference_only_from_year}):
            panel.horizon = y
            has_data = y in panel.years(c)
            asof = s.asof_dates.get(c, {}).get(f"FY{y}") or asof_auto.get((c, f"FY{y}"))
            for ts in s.types:
                if not has_data:
                    evals.append(Evaluation(c, f"FY{y}", ts.name, False, REASON_NO_DATA, False))
                    continue
                compute = _inventory if ts.name == INVENTORY else _gross_margin
                comp = compute(panel, c, y, s)
                evaluable = comp.indicators.get(ts.requires) is not None
                reason = "" if evaluable else f"missing:{ts.requires}"
                row = _row_for(panel, s, ts, c, y, comp, asof) if evaluable else None
                evals.append(
                    Evaluation(c, f"FY{y}", ts.name, evaluable, reason, bool(row and row.exceeded))
                )
                if row is not None:
                    rows.append(row)
                    if row.exceeded:
                        anomalies.append(_to_anomaly(row))
    anomalies.sort(key=lambda a: a.anomaly_id)
    evals.sort(key=lambda e: (e.company, e.period, e.type))
    rows.sort(key=lambda r: (-r.deviation, r.company, r.period, r.type))
    order = tuple(dict.fromkeys(rule.indicator for ts in s.types for rule in ts.rules))
    return DetectionResult(tuple(anomalies), tuple(evals), panel.notes, tuple(rows), order)


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


def _row_for(
    panel: _Panel,
    s: AnomalySettings,
    ts: TypeSettings,
    c: str,
    y: int,
    comp: _Computed,
    asof: AsofEntry | None,
) -> CandidateRow:
    """Hướng = dấu biến động của chính công ty (chỉ báo `requires`); luật chỉ tính khi cùng phía.

    Luật vượt ngưỡng ở phía NGƯỢC hướng không quyết định gì: chúng được liệt kê và, nếu đồng thời có
    luật vượt cùng phía, ca bị gắn `mixed_direction` và không tự coi là ứng viên.
    """
    own = comp.indicators[ts.requires]
    assert own is not None
    direction = "up" if own > 0 else "down" if own < 0 else None
    scores = _score_rules(ts, comp.indicators)
    same = tuple(r for r in scores if r.hit and r.side == direction)
    opposite = tuple(r for r in scores if r.hit and r.side != direction)
    exceeded = direction is not None and bool(same)
    mixed = exceeded and bool(opposite)
    peer_only = exceeded and all("peer" in r.indicator for r in same)

    flags = set(comp.flags)
    inputs = _INVENTORY_INPUTS if ts.name == INVENTORY else _GM_INPUTS
    flags |= _quality_flags(panel, s, c, (y, y - 1), inputs, "input")
    flags |= _quality_flags(
        panel, s, c, [yy for yy in panel.years(c) if yy < y - 1], inputs, "input_history"
    )
    for o in comp.peers_used:
        flags |= _quality_flags(panel, s, o, (y, y - 1), inputs, "input_peer")
    if s.provisional_thresholds:
        flags.add(FLAG_PROVISIONAL)
    if asof is None:
        flags.add(FLAG_ASOF_UNKNOWN)
    if peer_only:
        flags.add(FLAG_PEER_ONLY_HIT)
    if mixed:
        flags.add(FLAG_MIXED_DIRECTION)
    elif opposite and not same:
        flags.add(FLAG_OPPOSITE_ONLY)

    same_scores = [r.score for r in same if r.score is not None]
    all_same_side = [r.score for r in scores if r.side == direction and r.score is not None]
    deviation = max(all_same_side, default=Decimal(0)) if direction is not None else Decimal(0)
    position, peer_value = _peer_position(ts, scores)
    return CandidateRow(
        company=c,
        period=f"FY{y}",
        type=ts.name,
        direction=direction,
        exceeded=exceeded,
        candidate=exceeded and not mixed and not peer_only,
        mixed_direction=mixed,
        deviation=deviation,
        severity=max(same_scores, default=Decimal(0)),
        rules=scores,
        rules_hit=tuple(r.indicator for r in same),
        rules_hit_opposite=tuple(r.indicator for r in opposite),
        peer_relative=position,
        peer_value=peer_value,
        baseline_used=tuple(dict.fromkeys(comp.baseline)),
        flags=tuple(sorted(flags)),
        asof_date=None if asof is None else asof.date,
        asof_source=None if asof is None else asof.source,
        indicators={k: _fmt(v) for k, v in sorted(comp.indicators.items())},
    )


def _to_anomaly(row: CandidateRow) -> Anomaly:
    assert row.direction is not None
    thresholds = {r.indicator: r for r in row.rules}
    hits = tuple(
        {
            "indicator": name,
            "direction": row.direction,
            "value": _fmt(thresholds[name].value) or "",
            "score": _fmt(thresholds[name].score) or "",
        }
        for name in row.rules_hit
    )
    return Anomaly(
        company=row.company,
        period=row.period,
        type=row.type,
        direction=row.direction,
        indicators=dict(row.indicators),
        thresholds_hit=hits,
        baseline_used=row.baseline_used,
        severity=row.severity,
        flags=row.flags,
        asof_date=row.asof_date,
        peer_relative=row.peer_relative,
        asof_source=row.asof_source,
    )


# ---------------- bảng toàn bộ cặp (công ty, năm, loại) cho người chọn ngưỡng và chọn ca

BASE_COLUMNS = (
    "rank",
    "company",
    "period",
    "type",
    "direction",
    "exceeded_threshold",
    "candidate",
    "mixed_direction",
    "deviation",
    "rules_hit",
    "rules_hit_opposite",
    "peer_relative",
    "peer_value",
    "baseline_used",
    "flags",
    "asof_date",
    "asof_source",
)


def candidate_columns(result: DetectionResult) -> list[str]:
    cols = list(BASE_COLUMNS)
    for name in result.rule_order:
        cols += [name, f"{name}__score"]
    return cols


def candidate_rows(result: DetectionResult) -> list[dict[str, str]]:
    """MỌI cặp xét được, xếp theo mức lệch (deviation) giảm dần.

    `deviation`: điểm lớn nhất trong các luật cùng phía với hướng (1,0 = đúng ngưỡng). Mỗi luật có
    cột giá trị và cột `__score` (giá trị / ngưỡng cùng phía). `exceeded_threshold` = có luật cùng
    phía vượt ngưỡng; `candidate` = vượt ngưỡng và không `mixed_direction`.
    """
    out = []
    for rank, r in enumerate(result.rows, start=1):
        row = {
            "rank": str(rank),
            "company": r.company,
            "period": r.period,
            "type": r.type,
            "direction": r.direction or "",
            "exceeded_threshold": str(r.exceeded).lower(),
            "candidate": str(r.candidate).lower(),
            "mixed_direction": str(r.mixed_direction).lower(),
            "deviation": _fmt(r.deviation) or "",
            "rules_hit": ";".join(r.rules_hit),
            "rules_hit_opposite": ";".join(r.rules_hit_opposite),
            "peer_relative": r.peer_relative or "",
            "peer_value": r.peer_value or "",
            "baseline_used": ";".join(r.baseline_used),
            "flags": ";".join(r.flags),
            "asof_date": r.asof_date or "",
            "asof_source": r.asof_source or "",
        }
        for name in result.rule_order:
            row[name] = ""
            row[f"{name}__score"] = ""
        for rule in r.rules:
            row[rule.indicator] = _fmt(rule.value) or ""
            row[f"{rule.indicator}__score"] = _fmt(rule.score) or ""
        out.append(row)
    return out


def write_candidates_csv(result: DetectionResult, path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=candidate_columns(result))
        w.writeheader()
        w.writerows(candidate_rows(result))
