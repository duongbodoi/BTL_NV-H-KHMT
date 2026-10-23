"""Kiểm tra tính hợp lệ của fact: quan hệ kế toán, dấu, nhất quán năm tài chính (spec mục 7.3).

Kết quả mỗi check là PASS / FAIL / NOT_EVALUABLE (spec mục 10.2). Thiếu dữ liệu không bao giờ thành
0 (trừ "-" khi cấu hình `nil_as_zero`, có gắn cờ). Check FAIL chỉ gắn cờ vào fact, không đổi
trạng thái hay giá trị: người xem xét, không tự "sửa".
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from steel_xai.contracts import FinancialFact, ValueStatus
from steel_xai.validation.settings import Relation, ValidationSettings

PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUABLE = "NOT_EVALUABLE"

FLAG_NIL_AS_ZERO = "nil_as_zero"
FLAG_FAILED_PREFIX = "validation_failed:"
FLAG_FISCAL_END_CHANGED = "fiscal_end_changed"
FLAG_PEER_FISCAL_END_DIFFERS = "peer_fiscal_end_differs"

NO_FACT = "MISSING"  # không có fact nào cho khóa này


@dataclass(frozen=True)
class CheckResult:
    company: str  # "*" cho check giữa các công ty
    period: str
    rule: str
    status: str
    inputs: tuple[str, ...]  # khóa fact đã dùng: "công ty|kỳ|metric"
    values: dict[str, str]  # tên → giá trị (chuỗi Decimal)
    missing: dict[str, str]  # metric thiếu → trạng thái ("MISSING", "UNRESOLVED", ...)
    tolerance: str
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "company": self.company,
            "period": self.period,
            "rule": self.rule,
            "status": self.status,
            "inputs": list(self.inputs),
            "values": dict(self.values),
            "missing": dict(self.missing),
            "tolerance": self.tolerance,
            "flags": list(self.flags),
        }


@dataclass(frozen=True)
class ValidationResult:
    facts: tuple[FinancialFact, ...]  # fact đã gắn cờ validation
    checks: tuple[CheckResult, ...]

    def summary(self) -> dict[str, dict[str, int]]:
        out: dict[str, Counter[str]] = defaultdict(Counter)
        for c in self.checks:
            out[c.rule.split(":")[0]][c.status] += 1
        return {rule: dict(cnt) for rule, cnt in sorted(out.items())}

    def to_dict(self) -> dict[str, object]:
        return {
            "facts": [f.to_dict() for f in self.facts],
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary(),
        }


def validate_facts(
    facts: Iterable[FinancialFact], settings: ValidationSettings
) -> ValidationResult:
    """Deterministic. Dùng cho fact chính (không gồm số so sánh)."""
    base = sorted(facts, key=lambda f: f.key)
    index = {f.key: f for f in base}
    flags: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    checks: list[CheckResult] = []

    pairs = sorted({(f.company, f.period) for f in base if f.source != "derived"})
    for company, period in pairs:
        for rel in settings.relations:
            result = _check_relation(company, period, rel, index, settings, flags)
            if result is not None:
                checks.append(result)
    checks += _check_non_negative(base, flags, settings.non_negative)
    checks += _check_fiscal_ends(base, flags)

    out = [
        replace(f, validation_flags=tuple(sorted({*f.validation_flags, *flags[f.key]})))
        if flags.get(f.key)
        else f
        for f in base
    ]
    return ValidationResult(tuple(out), tuple(checks))


def _fact_key(company: str, period: str, metric: str) -> str:
    return f"{company}|{period}|{metric}"


def _check_relation(
    company: str,
    period: str,
    rel: Relation,
    index: dict[tuple[str, str, str], FinancialFact],
    s: ValidationSettings,
    flags: dict[tuple[str, str, str], set[str]],
) -> CheckResult | None:
    metrics = [rel.target, *(t.metric for t in rel.terms)]
    if not any((company, period, m) in index for m in metrics):
        return None  # công ty không có dữ liệu nào cho quan hệ này ở kỳ đó
    values: dict[str, Decimal] = {}
    missing: dict[str, str] = {}
    nil_used: list[str] = []
    for m in metrics:
        f = index.get((company, period, m))
        if f is None:
            missing[m] = NO_FACT
        elif f.status is ValueStatus.OK and f.value is not None:
            values[m] = f.value
        elif f.status is ValueStatus.NOT_APPLICABLE and s.nil_as_zero:
            values[m] = Decimal(0)
            nil_used.append(m)
        else:
            missing[m] = f.status.value
    inputs = tuple(_fact_key(company, period, m) for m in metrics if (company, period, m) in index)
    tol = str(s.relative_tolerance)
    ends = sorted(
        {
            _month_day(f.fiscal_period_end)
            for m in metrics
            if (f := index.get((company, period, m))) is not None and f.fiscal_period_end
        }
    )
    if len(ends) > 1:  # các đầu vào không cùng thời điểm khóa sổ: không so được
        missing["fiscal_period_end"] = "MISMATCH:" + ",".join(ends)
        return CheckResult(company, period, rel.id, NOT_EVALUABLE, inputs, {}, missing, tol)
    if missing:
        partial = _partial_bound_violation(rel, values, s)
        if partial is not None:
            return CheckResult(
                company, period, rel.id, FAIL, inputs, partial, missing, tol, ("partial_sum",)
            )
        return CheckResult(company, period, rel.id, NOT_EVALUABLE, inputs, {}, missing, tol)

    rhs = sum((t.coef * values[t.metric] for t in rel.terms), Decimal(0))
    lhs = values[rel.target]
    if rel.kind == "identity":
        ok = abs(lhs - rhs) <= s.relative_tolerance * max(abs(lhs), abs(rhs))
    else:
        ok = rhs <= lhs * (1 + s.relative_tolerance)
    for m in nil_used:
        flags[(company, period, m)].add(FLAG_NIL_AS_ZERO)
    if not ok:
        for m in metrics:
            flags[(company, period, m)].add(FLAG_FAILED_PREFIX + rel.id)
    vals = {"target": str(lhs), "terms_sum": str(rhs)}
    return CheckResult(
        company,
        period,
        rel.id,
        PASS if ok else FAIL,
        inputs,
        vals,
        {},
        tol,
        (FLAG_NIL_AS_ZERO,) if nil_used else (),
    )


def _partial_bound_violation(
    rel: Relation, values: dict[str, Decimal], s: ValidationSettings
) -> dict[str, str] | None:
    """Chặn trên: các thành phần đã biết (hệ số dương) mà đã vượt tổng thì vi phạm dù còn thiếu
    thành phần khác (thiếu chỉ làm tổng lớn thêm, không bao giờ nhỏ đi)."""
    if rel.kind != "upper_bound" or rel.target not in values:
        return None
    if any(t.coef < 0 for t in rel.terms):
        return None
    known = sum((values[t.metric] for t in rel.terms if t.metric in values), Decimal(0))
    if known > values[rel.target] * (1 + s.relative_tolerance):
        return {"target": str(values[rel.target]), "terms_sum": str(known)}
    return None


def _check_non_negative(
    facts: Sequence[FinancialFact],
    flags: dict[tuple[str, str, str], set[str]],
    metrics: frozenset[str],
) -> list[CheckResult]:
    out = []
    for f in facts:
        if f.metric not in metrics or f.status is not ValueStatus.OK or f.value is None:
            continue
        bad = f.value < 0
        if bad:
            flags[f.key].add(FLAG_FAILED_PREFIX + "non_negative")
        out.append(
            CheckResult(
                f.company,
                f.period,
                f"non_negative:{f.metric}",
                FAIL if bad else PASS,
                (_fact_key(*f.key),),
                {"value": str(f.value)},
                {},
                "0",
            )
        )
    return out


def _month_day(iso: str) -> str:
    """Khóa so sánh ngày khóa sổ: "09-end" cho cuối tháng 9 (28/29/30/31 đều là cuối tháng)."""
    d = dt.date.fromisoformat(iso)
    if d.day == calendar.monthrange(d.year, d.month)[1]:
        return f"{d.month:02d}-end"
    return f"{d.month:02d}-{d.day:02d}"


def _check_fiscal_ends(
    facts: Sequence[FinancialFact], flags: dict[tuple[str, str, str], set[str]]
) -> list[CheckResult]:
    """Ngày kết thúc năm tài chính: ổn định theo từng công ty, và cùng nhau giữa các công ty.

    Khác nhau giữa các công ty không phải lỗi dữ liệu mà là cờ cho so sánh nhóm ngành (ví dụ một
    công ty kết thúc năm 30/9, công ty khác 31/12): kỳ `FY` giống nhau nhưng không cùng thời điểm.
    """
    with_end = [f for f in facts if f.fiscal_period_end and f.source != "derived"]
    out: list[CheckResult] = []

    by_company: dict[str, list[FinancialFact]] = defaultdict(list)
    for f in with_end:
        by_company[f.company].append(f)
    for company, fs in sorted(by_company.items()):
        counts = Counter(_month_day(f.fiscal_period_end or "") for f in fs)
        modal = min(counts, key=lambda k: (-counts[k], k))
        for period in sorted({f.period for f in fs}):
            here = [f for f in fs if f.period == period]
            ends = sorted({_month_day(f.fiscal_period_end or "") for f in here})
            ok = ends == [modal]
            if not ok:
                for f in here:
                    flags[f.key].add(FLAG_FISCAL_END_CHANGED)
            out.append(
                CheckResult(
                    company,
                    period,
                    "fiscal_end_consistent",
                    PASS if ok else FAIL,
                    tuple(sorted(_fact_key(*f.key) for f in here)),
                    {"month_day": ",".join(ends), "company_modal": modal},
                    {},
                    "exact",
                )
            )

    by_period: dict[str, list[FinancialFact]] = defaultdict(list)
    for f in with_end:
        by_period[f.period].append(f)
    for period, fs in sorted(by_period.items()):
        companies = sorted({f.company for f in fs})
        if len(companies) < 2:
            continue
        ends = sorted({_month_day(f.fiscal_period_end or "") for f in fs})
        ok = len(ends) == 1
        if not ok:
            for f in fs:
                flags[f.key].add(FLAG_PEER_FISCAL_END_DIFFERS)
        out.append(
            CheckResult(
                "*",
                period,
                "peer_fiscal_end_aligned",
                PASS if ok else FAIL,
                tuple(sorted({_fact_key(*f.key) for f in fs})),
                {"companies": ",".join(companies), "month_day": ",".join(ends)},
                {},
                "exact",
            )
        )
    return out
