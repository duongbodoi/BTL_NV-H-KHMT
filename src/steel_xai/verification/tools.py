"""Registry công cụ kiểm chứng định lượng (spec mục 10.2, Phase 8).

Mọi phép tính do code (Decimal), không LLM. Tên tool phải khớp trường `tool` trong thư viện giả
thuyết (test kiểm điều này). Thiếu dữ liệu → `NotEvaluable`, không bao giờ coi là 0.

Quy ước giá trị: tăng trưởng là tỷ lệ (0,20 = +20%); `*_change` của tỷ lệ là hiệu số tuyệt đối của
tỷ lệ (0,03 = 3 điểm %); `days_inventory_change` tính bằng ngày; z-score là số lần độ lệch chuẩn.
Với metric tài khoản đối ứng (`contra_metrics`, ví dụ dự phòng hàng tồn kho lưu số âm), tăng trưởng
tính trên độ lớn.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from steel_xai.contracts import CheckInput
from steel_xai.verification.context import CheckContext, Ledger, NotEvaluable
from steel_xai.verification.data import Month, add_months

ZERO = Decimal(0)
PERIODS = ("current", "prior_year_end")
NUMERATOR_MODES = ("change", "level")


@dataclass
class ToolOutput:
    values: dict[str, Decimal | None]
    inputs: list[CheckInput]
    notes: list[str] = field(default_factory=list)  # lý do thành phần None / peer bị loại


ToolFn = Callable[[CheckContext, dict[str, Any]], ToolOutput]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    required: frozenset[str]
    optional: frozenset[str]
    fn: ToolFn
    doc: str
    # kiểu từng tham số: metric | series | lag | period | mode | true
    types: dict[str, str]


# ---------------- truy cập fact


def _fv(
    ctx: CheckContext, led: Ledger, metric: str, year: int, company: str | None = None
) -> Decimal:
    who = company or ctx.company
    if ctx.facts.is_conflict(who, year, metric):
        raise NotEvaluable(f"{metric} FY{year} của {who} có các giá trị trùng khóa mâu thuẫn")
    f = ctx.fact(metric, year, company)
    if f is None or f.value is None:
        raise NotEvaluable(f"thiếu {metric} FY{year} của {who}")
    led.fact_input(f)
    return f.value


def _growth(
    ctx: CheckContext, led: Ledger, metric: str, year: int | None = None, company: str | None = None
) -> Decimal:
    y = year or ctx.year
    cur, prev = _fv(ctx, led, metric, y, company), _fv(ctx, led, metric, y - 1, company)
    if metric in ctx.settings.contra_metrics:  # lưu số âm: so độ lớn
        cur, prev = abs(cur), abs(prev)
    if prev <= 0:
        raise NotEvaluable(f"{metric} FY{y - 1} <= 0, không tính được tăng trưởng")
    return cur / prev - 1


def _days(ctx: CheckContext, led: Ledger, year: int) -> int:
    end, start = ctx.fiscal_end(year), ctx.fiscal_end(year - 1)
    if end is None or start is None or end <= start:
        raise NotEvaluable(f"thiếu ngày kết thúc năm tài chính FY{year} hoặc FY{year - 1}")
    days = (end - start).days
    led.add(
        CheckInput(
            kind="derived",
            name="days_in_fiscal_year",
            period=f"{ctx.company}:FY{year}",
            value=Decimal(days),
            source=f"fiscal_period_end {end.isoformat()} - {start.isoformat()}",
        )
    )
    return days


def _one(value: Decimal, led: Ledger, notes: list[str] | None = None) -> ToolOutput:
    return ToolOutput({"value": value}, led.inputs, notes or [])


# ---------------- tool từ fact


def growth_yoy(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Tăng trưởng YoY của một metric (độ lớn nếu là tài khoản đối ứng)."""
    led = Ledger()
    return _one(_growth(ctx, led, p["metric"]), led)


def metric_level(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Giá trị của một metric ở kỳ đang xét, đúng dấu như báo cáo (ví dụ dòng tiền)."""
    led = Ledger()
    return _one(_fv(ctx, led, p["metric"], ctx.year), led)


def growth_gap(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Tăng trưởng YoY của a trừ tăng trưởng YoY của b."""
    led = Ledger()
    return _one(_growth(ctx, led, p["a"]) - _growth(ctx, led, p["b"]), led)


def days_inventory_change(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Thay đổi số ngày tồn kho (tồn kho cuối kỳ x số ngày thực của năm tài chính / giá vốn)."""
    led = Ledger()

    def dio(year: int) -> Decimal:
        inv, cogs = _fv(ctx, led, "inventory_total", year), _fv(ctx, led, "cogs", year)
        if cogs <= 0:
            raise NotEvaluable(f"cogs FY{year} <= 0, không tính được số ngày tồn kho")
        return inv * _days(ctx, led, year) / cogs

    return _one(dio(ctx.year) - dio(ctx.year - 1), led)


def _year_for(ctx: CheckContext, period: str) -> int:
    if period not in PERIODS:
        raise ValueError(f"period phải thuộc {PERIODS}, nhận {period!r}")
    return ctx.year if period == "current" else ctx.year - 1


def ratio(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Tỷ lệ tử/mẫu. `period` áp dụng cho cả hai; hoặc `numerator_period`/`denominator_period`
    riêng (ví dụ luồng trong năm chia số dư đầu kỳ: denominator_period = prior_year_end)."""
    led = Ledger()
    base = p.get("period", "current")
    ny = _year_for(ctx, p.get("numerator_period", base))
    dy = _year_for(ctx, p.get("denominator_period", base))
    den = _fv(ctx, led, p["denominator"], dy)
    if den <= 0:
        raise NotEvaluable(f"{p['denominator']} FY{dy} <= 0, không tính được tỷ lệ")
    return _one(_fv(ctx, led, p["numerator"], ny) / den, led)


def _ratio_at(ctx: CheckContext, led: Ledger, num: str, den: str, year: int) -> Decimal:
    d = _fv(ctx, led, den, year)
    if d <= 0:
        raise NotEvaluable(f"{den} FY{year} <= 0, không tính được tỷ lệ")
    return _fv(ctx, led, num, year) / d


def ratio_change(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Hiệu số tỷ lệ tử/mẫu giữa kỳ đang xét và kỳ trước."""
    led = Ledger()
    n, d = p["numerator"], p["denominator"]
    return _one(_ratio_at(ctx, led, n, d, ctx.year) - _ratio_at(ctx, led, n, d, ctx.year - 1), led)


def _delta(ctx: CheckContext, led: Ledger, metric: str) -> Decimal:
    return _fv(ctx, led, metric, ctx.year) - _fv(ctx, led, metric, ctx.year - 1)


def ratio_of_changes(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """|tử| / |Δ mẫu|. Tử là mức thay đổi YoY (`numerator_mode: change`, mặc định) hoặc mức của kỳ
    đang xét (`level`, cho khoản dòng chảy như chi phí trích lập)."""
    mode = p.get("numerator_mode", "change")
    if mode not in NUMERATOR_MODES:
        raise ValueError(f"numerator_mode phải thuộc {NUMERATOR_MODES}, nhận {mode!r}")
    led = Ledger()
    num = (
        _delta(ctx, led, p["numerator"])
        if mode == "change"
        else _fv(ctx, led, p["numerator"], ctx.year)
    )
    dd = _delta(ctx, led, p["denominator"])
    if dd == 0:
        raise NotEvaluable(f"{p['denominator']} không đổi, không tính được tỷ lệ")
    return _one(abs(num) / abs(dd), led)


def share_of_change(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Δ thành phần / Δ tổng (có dấu): phần của mức thay đổi tổng do thành phần này."""
    led = Ledger()
    dc, dt_ = _delta(ctx, led, p["component"]), _delta(ctx, led, p["total"])
    if dt_ == 0:
        raise NotEvaluable(f"{p['total']} không đổi, không tính được tỷ trọng của mức thay đổi")
    return _one(dc / dt_, led)


def share_shift(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Hiệu số giá trị của một chỉ tiêu dạng tỷ trọng (ví dụ tỷ trọng doanh thu xuất khẩu)."""
    led = Ledger()
    return _one(_delta(ctx, led, p["metric"]), led)


# ---------------- tool so peer / lịch sử


def growth_vs_peer(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Tăng trưởng của công ty trừ trung vị tăng trưởng của các peer ĐÃ công bố tại as-of."""
    if ctx.asof is None or ctx.asof_by is None:
        raise NotEvaluable("thiếu as-of để lọc peer theo ngày công bố")
    led = Ledger()
    own = _growth(ctx, led, p["metric"])
    own_end = ctx.fiscal_end(ctx.year)
    vals: list[Decimal] = []
    used: list[str] = []
    skipped: list[str] = []
    for o in ctx.facts.companies:
        if o == ctx.company:
            continue
        pub = ctx.asof_by.get((o, ctx.year))
        if pub is None or pub > ctx.asof:
            skipped.append(f"{o}: chưa công bố tại as-of")
            continue
        peer_led = Ledger()  # chỉ ghi đầu vào của peer nếu peer được dùng
        try:
            peer_end = ctx.fiscal_end(ctx.year, o)
            if own_end and peer_end and peer_end > own_end:
                skipped.append(f"{o}: năm tài chính kết thúc muộn hơn")
                continue
            vals.append(_growth(ctx, peer_led, p["metric"], company=o))
        except NotEvaluable as e:
            skipped.append(f"{o}: {e}")
            continue
        for item in peer_led.inputs:
            led.add(item)
        used.append(o)
    notes = [f"peer dùng: {', '.join(used) or '-'}"] + [f"loại {s}" for s in skipped]
    if len(vals) < ctx.settings.min_peers:
        raise NotEvaluable(
            f"chỉ có {len(vals)} peer dùng được (< {ctx.settings.min_peers}); " + "; ".join(notes)
        )
    return _one(own - statistics.median(vals), led, notes)


def growth_vs_history(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """z-score (độ lệch chuẩn mẫu) của tăng trưởng kỳ này so với các năm TRƯỚC của chính công ty."""
    led = Ledger()
    x = _growth(ctx, led, p["metric"])
    past: list[Decimal] = []
    for y in ctx.facts.years(ctx.company):
        if y >= ctx.year:
            continue
        try:
            past.append(_growth(ctx, led, p["metric"], y))
        except NotEvaluable:
            continue
    if len(past) < ctx.settings.history_min_changes:
        raise NotEvaluable(
            f"chỉ có {len(past)} lần thay đổi trong lịch sử (< {ctx.settings.history_min_changes})"
        )
    mean = sum(past, ZERO) / len(past)
    var = sum(((v - mean) ** 2 for v in past), ZERO) / (len(past) - 1)
    if var == 0:
        raise NotEvaluable("lịch sử không biến động (độ lệch chuẩn bằng 0)")
    return _one((x - mean) / var.sqrt(), led)


# ---------------- tool chuỗi giá bên ngoài


def _series_names(ctx: CheckContext, p: dict[str, Any]) -> list[str]:
    s = p["series"]
    names = [s] if isinstance(s, str) else list(s)
    unknown = [n for n in names if n not in ctx.settings.series]
    if not names or unknown:
        raise ValueError(f"chuỗi giá không có trong verification.external.series: {unknown or s}")
    return names


def _resolve_lag(ctx: CheckContext, name: str, lag: Any) -> int:
    if lag == "from_config":
        return ctx.settings.series[name].lag_months
    if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
        raise ValueError(f"lag phải là số nguyên >= 0 hoặc 'from_config', nhận {lag!r}")
    return lag


def _fiscal_month(ctx: CheckContext) -> Month:
    end = ctx.fiscal_end(ctx.year)
    if end is None:
        raise NotEvaluable(f"thiếu ngày kết thúc năm tài chính FY{ctx.year}")
    return end.year, end.month


def _require_series(ctx: CheckContext, name: str) -> dict[Month, Decimal]:
    if ctx.asof is None:
        raise NotEvaluable("thiếu as-of, không lọc được giá đã công bố")
    if not ctx.external.has(name):
        path = ctx.settings.series[name].path.name
        raise NotEvaluable(f"chưa có dữ liệu chuỗi giá {name} ({path})")
    obs = ctx.external.observations(name, ctx.asof)
    if not obs:
        raise NotEvaluable(f"chuỗi {name} chưa có tháng nào được công bố tại {ctx.asof}")
    return obs


def _window_mean(
    obs: dict[Month, Decimal], end: Month, ctx: CheckContext, name: str
) -> tuple[Decimal, CheckInput]:
    w = ctx.settings.window_months
    months = [add_months(end, -i) for i in range(w)]
    got = [obs[m] for m in months if m in obs]
    if len(got) < ctx.settings.min_months:
        raise NotEvaluable(
            f"{name}: chỉ có {len(got)}/{w} tháng đến {end[0]}-{end[1]:02d} "
            f"(< {ctx.settings.min_months}) tại as-of"
        )
    mean = sum(got, ZERO) / len(got)
    start = months[-1]
    item = CheckInput(
        kind="external",
        name=name,
        period=f"{start[0]}-{start[1]:02d}..{end[0]}-{end[1]:02d}",
        value=mean,
        source=f"{ctx.settings.series[name].path.name}; {len(got)}/{w} tháng; as-of {ctx.asof}",
        flags=("partial_window",) if len(got) < w else (),
    )
    return mean, item


def _series_yoy(ctx: CheckContext, name: str, lag: int, led: Ledger) -> Decimal:
    """Thay đổi YoY của giá trung bình cửa sổ kết thúc `lag` tháng trước cuối năm tài chính."""
    obs = _require_series(ctx, name)
    end = add_months(_fiscal_month(ctx), -lag)
    cur, cur_item = _window_mean(obs, end, ctx, name)
    prev, prev_item = _window_mean(obs, add_months(end, -12), ctx, name)
    if prev <= 0:
        raise NotEvaluable(f"{name}: giá trung bình kỳ trước <= 0")
    led.add(cur_item)  # chỉ ghi đầu vào khi cả hai cửa sổ đều dùng được
    led.add(prev_item)
    return cur / prev - 1


def _per_series(
    ctx: CheckContext, names: list[str], one: Callable[[str, Ledger], Decimal]
) -> ToolOutput:
    led = Ledger()
    values: dict[str, Decimal | None] = {}
    notes: list[str] = []
    for n in names:
        try:
            values[n] = one(n, led)
        except NotEvaluable as e:
            values[n] = None
            notes.append(str(e))
    return ToolOutput(values, led.inputs, notes)


def lagged_external_series(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Thay đổi YoY của giá trung bình 12 tháng, lùi `lag` tháng so với cuối năm tài chính."""
    names = _series_names(ctx, p)
    return _per_series(
        ctx, names, lambda n, led: _series_yoy(ctx, n, _resolve_lag(ctx, n, p["lag"]), led)
    )


def percentile_vs_history(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Tỷ lệ tháng trong lịch sử ĐÃ công bố có giá <= giá cuối kỳ (kể cả chính nó, nên nằm trong
    (0, 1]; gần 1 = giá cuối kỳ thuộc vùng cao nhất)."""
    if p.get("asof_only", True) is not True:
        raise ValueError("asof_only chỉ có thể là true (spec mục 7.2)")

    def one(name: str, led: Ledger) -> Decimal:
        obs = _require_series(ctx, name)
        end = _fiscal_month(ctx)
        recent = [m for m in obs if m <= end and m >= add_months(end, -2)]
        if not recent:
            raise NotEvaluable(f"{name}: không có giá trong 3 tháng đến cuối năm tài chính")
        last = max(recent)
        oldest = add_months(last, -12 * ctx.settings.percentile_lookback_years)
        hist = [v for m, v in obs.items() if oldest <= m <= last]
        if len(hist) < ctx.settings.percentile_min_months:
            raise NotEvaluable(
                f"{name}: lịch sử chỉ có {len(hist)} tháng (< {ctx.settings.percentile_min_months})"
            )
        pct = Decimal(sum(1 for v in hist if v <= obs[last])) / len(hist)
        led.add(
            CheckInput(
                kind="external",
                name=name,
                period=f"{last[0]}-{last[1]:02d}",
                value=obs[last],
                source=f"{ctx.settings.series[name].path.name}; lịch sử {len(hist)} tháng; "
                f"as-of {ctx.asof}",
            )
        )
        return pct

    return _per_series(ctx, _series_names(ctx, p), one)


def growth_vs_series(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Tăng trưởng của một chỉ tiêu trừ thay đổi YoY của chuỗi giá (độ trễ theo config)."""
    led0 = Ledger()
    g = _growth(ctx, led0, p["metric"])

    def one(name: str, led: Ledger) -> Decimal:
        change = _series_yoy(ctx, name, ctx.settings.series[name].lag_months, led)
        for item in led0.inputs:
            led.add(item)
        return g - change

    return _per_series(ctx, _series_names(ctx, p), one)


# ---------------- registry

_S = frozenset
_M = "metric"


def policy_text_similarity(ctx: CheckContext, p: dict[str, Any]) -> ToolOutput:
    """Mức giống nhau (0..1) giữa đoạn chính sách kế toán của khoản mục `item` trong thuyết minh năm
    đang xét và năm trước (code so văn bản, không LLM). Cả hai tài liệu phải công bố trước as-of."""
    from steel_xai.contracts import CheckInput, Provenance
    from steel_xai.verification.policy_text import similarity

    if ctx.policy_texts is None:
        raise NotEvaluable("không có kho văn bản thuyết minh để so chính sách")
    led = Ledger()
    found = []
    for year in (ctx.year, ctx.year - 1):
        pt = ctx.policy_texts.find(ctx.company, year, p["item"])
        if pt is None:
            raise NotEvaluable(
                f"không tìm thấy đoạn chính sách {p['item']!r} FY{year} của {ctx.company}"
            )
        if ctx.asof is not None and (pt.published is None or pt.published > ctx.asof):
            raise NotEvaluable(f"thuyết minh FY{year} công bố sau mốc as-of hoặc không rõ ngày")
        led.add(
            CheckInput(
                kind="document",
                name=f"chính sách {p['item']}",
                period=f"{ctx.company}:FY{year}",
                value=Decimal(len(pt.text.split())),
                source=Provenance(pt.doc_id, pt.page, chunk_id=pt.chunk_id, source_text=pt.text),
            )
        )
        found.append(pt)
    return _one(similarity(found[0].text, found[1].text), led)


def _spec(
    fn: ToolFn, types: dict[str, str], required: frozenset[str], optional: frozenset[str] = _S()
) -> ToolSpec:
    assert set(types) == set(required | optional), fn.__name__
    return ToolSpec(fn.__name__, required, optional, fn, (fn.__doc__ or "").strip(), types)


TOOLS: dict[str, ToolSpec] = {
    s.name: s
    for s in (
        _spec(growth_yoy, {"metric": _M}, _S({"metric"})),
        _spec(metric_level, {"metric": _M}, _S({"metric"})),
        _spec(growth_gap, {"a": _M, "b": _M}, _S({"a", "b"})),
        _spec(days_inventory_change, {}, _S()),
        _spec(
            ratio,
            {
                "numerator": _M,
                "denominator": _M,
                "period": "period",
                "numerator_period": "period",
                "denominator_period": "period",
            },
            _S({"numerator", "denominator"}),
            _S({"period", "numerator_period", "denominator_period"}),
        ),
        _spec(ratio_change, {"numerator": _M, "denominator": _M}, _S({"numerator", "denominator"})),
        _spec(
            ratio_of_changes,
            {"numerator": _M, "denominator": _M, "numerator_mode": "mode"},
            _S({"numerator", "denominator"}),
            _S({"numerator_mode"}),
        ),
        _spec(share_of_change, {"component": _M, "total": _M}, _S({"component", "total"})),
        _spec(share_shift, {"metric": _M}, _S({"metric"})),
        _spec(growth_vs_peer, {"metric": _M}, _S({"metric"})),
        _spec(growth_vs_history, {"metric": _M}, _S({"metric"})),
        _spec(lagged_external_series, {"series": "series", "lag": "lag"}, _S({"series", "lag"})),
        _spec(
            percentile_vs_history,
            {"series": "series", "asof_only": "true"},
            _S({"series"}),
            _S({"asof_only"}),
        ),
        _spec(growth_vs_series, {"metric": _M, "series": "series"}, _S({"metric", "series"})),
        _spec(policy_text_similarity, {"item": "text"}, _S({"item"})),
    )
}


def tool_names() -> frozenset[str]:
    return frozenset(TOOLS)


__all__ = ["TOOLS", "ToolOutput", "ToolSpec", "tool_names"]
