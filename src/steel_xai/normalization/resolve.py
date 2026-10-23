"""Gộp candidate thành `FinancialFact` và tính metric dẫn xuất (spec mục 7.1, 23).

Quy tắc nguồn: dữ liệu có cấu trúc > số trích từ OCR. Hai nguồn OK mà lệch nhau → UNRESOLVED
(`source_conflict`), không tự chọn im lặng. Hai tài liệu cùng nguồn lệch nhau cũng là UNRESOLVED.

Số đã được điều chỉnh ở năm sau: giá trị chính của năm N lấy từ báo cáo năm N. Số so sánh về năm N
trong báo cáo năm N+1 được lưu riêng (`comparatives`); nếu khác thì fact chính gắn cờ
`restated_later`, không đổi thành UNRESOLVED. Chỉ khi không có báo cáo năm N thì dùng số so sánh
(cờ `comparative_only`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from steel_xai.contracts import FinancialFact, Provenance, ValueStatus
from steel_xai.normalization.mapping import SOURCE_OCR, SOURCE_STRUCTURED, Candidate
from steel_xai.normalization.settings import NormalizationSettings

UNIT_VND = "VND"
UNIT_RATIO = "ratio"

FLAG_SOURCE_CONFLICT = "source_conflict"
FLAG_NA_CONFLICT = "not_applicable_vs_value"
FLAG_STRUCTURED_CONFLICT = "structured_conflict"
FLAG_OCR_CONFLICT = "ocr_conflict"
FLAG_FISCAL_END_MISMATCH = "fiscal_end_mismatch"
FLAG_CONFIRMED_BY_OCR = "confirmed_by_ocr"
FLAG_OCR_FALLBACK = "structured_unresolved_ocr_fallback"
FLAG_HAS_UNRESOLVED = "has_unresolved_candidate"
FLAG_RESTATED_LATER = "restated_later"
FLAG_COMPARATIVE_ONLY = "comparative_only"
FLAG_COMPARATIVE = "comparative"
FLAG_DERIVED = "derived"
FLAG_ZERO_DENOMINATOR = "zero_denominator"


@dataclass(frozen=True)
class Conflict:
    """Các giá trị lệch nhau của cùng một khóa, giữ lại để người đối chiếu."""

    company: str
    period: str
    metric: str
    reason: str
    candidates: tuple[tuple[str, str, Provenance], ...]  # (nguồn, giá trị, provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "company": self.company,
            "period": self.period,
            "metric": self.metric,
            "reason": self.reason,
            "candidates": [
                {"source": s, "value": v, "provenance": p.to_dict()} for s, v, p in self.candidates
            ],
        }


def resolve_facts(
    candidates: Sequence[Candidate], settings: NormalizationSettings
) -> tuple[list[FinancialFact], list[FinancialFact], list[Conflict]]:
    """(fact chính, fact so sánh lưu riêng, conflict)."""
    groups: dict[tuple[str, str, str], list[Candidate]] = defaultdict(list)
    for c in candidates:
        groups[c.key].append(c)
    facts: list[FinancialFact] = []
    comparatives: list[FinancialFact] = []
    conflicts: list[Conflict] = []
    for key in sorted(groups):
        primary = [c for c in groups[key] if not c.is_comparative]
        comp = [c for c in groups[key] if c.is_comparative]
        if not primary:
            fact, conflict = _resolve_group(key, comp, settings, extra={FLAG_COMPARATIVE_ONLY})
            facts.append(fact)
            if conflict is not None:
                conflicts.append(conflict)
            continue
        fact, conflict = _resolve_group(key, primary, settings)
        if conflict is not None:
            conflicts.append(conflict)
        if comp:
            years = sorted({str(c.report_year) for c in comp})
            c_fact, c_conflict = _resolve_group(
                key,
                comp,
                settings,
                extra={FLAG_COMPARATIVE} | {f"report_year:{y}" for y in years},
            )
            comparatives.append(c_fact)
            if c_conflict is not None:
                conflicts.append(c_conflict)
            if _restated(fact, c_fact, settings):
                fact = replace(
                    fact,
                    validation_flags=tuple(sorted({*fact.validation_flags, FLAG_RESTATED_LATER})),
                )
        facts.append(fact)
    return facts, comparatives, conflicts


def _restated(main: FinancialFact, comp: FinancialFact, s: NormalizationSettings) -> bool:
    """Số so sánh ở báo cáo sau khác số chính: giá trị khác, hoặc một bên "-" còn bên kia có số."""
    known = (ValueStatus.OK, ValueStatus.NOT_APPLICABLE)
    if main.status not in known or comp.status not in known:
        return False
    if main.status is not comp.status:
        return True
    if main.value is None or comp.value is None:
        return False
    return not _close(main.value, comp.value, s.relative_tolerance)


def derive_facts(
    facts: Sequence[FinancialFact], settings: NormalizationSettings
) -> list[FinancialFact]:
    """Tỷ số do code tính từ các fact OK; thiếu/không giải quyết được đầu vào → không có số."""
    index = {f.key: f for f in facts}
    periods = sorted({(f.company, f.period) for f in facts})
    out: list[FinancialFact] = []
    for d in settings.derived.values():
        for company, period in periods:
            num = index.get((company, period, d.numerator))
            den = index.get((company, period, d.denominator))
            if num is None and den is None:
                continue
            out.append(_derive_ratio(company, period, d.name, d.numerator, d.denominator, num, den))
    return out


def _derive_ratio(
    company: str,
    period: str,
    name: str,
    num_metric: str,
    den_metric: str,
    num: FinancialFact | None,
    den: FinancialFact | None,
) -> FinancialFact:
    inputs = tuple(f"{company}|{period}|{m}" for m in (num_metric, den_metric))
    flags = [FLAG_DERIVED]
    for f in (num, den):  # dấu vết nguồn của đầu vào đi theo số dẫn xuất
        if f is not None:
            flags += [
                x for x in (FLAG_COMPARATIVE_ONLY, FLAG_RESTATED_LATER) if x in f.validation_flags
            ]
    flags = sorted(set(flags))
    status = ValueStatus.MISSING
    unusable = False
    ends = {f.fiscal_period_end for f in (num, den) if f is not None and f.fiscal_period_end}
    if len(ends) > 1:
        flags.append(FLAG_FISCAL_END_MISMATCH)
        status = ValueStatus.UNRESOLVED
        unusable = True
    end = next(iter(ends)) if len(ends) == 1 else None
    for metric, fact in ((num_metric, num), (den_metric, den)):
        if fact is None:
            flags.append(f"input_missing:{metric}")
            unusable = True
        elif fact.status is not ValueStatus.OK:
            flags.append(f"input_{fact.status.value.lower()}:{metric}")
            unusable = True
            if fact.status is ValueStatus.UNRESOLVED:
                status = ValueStatus.UNRESOLVED

    def make(value: Decimal | None, st: ValueStatus, extra: str | None = None) -> FinancialFact:
        fl = tuple(flags + ([extra] if extra else []))
        return FinancialFact(
            company, period, name, value, UNIT_RATIO, st, "derived", None, fl, inputs, end
        )

    if unusable or num is None or den is None or num.value is None or den.value is None:
        return make(None, status)
    if den.value == 0:
        return make(None, ValueStatus.UNRESOLVED, FLAG_ZERO_DENOMINATOR)
    return make(num.value / den.value, ValueStatus.OK)


def _resolve_group(
    key: tuple[str, str, str],
    cands: list[Candidate],
    s: NormalizationSettings,
    extra: set[str] | None = None,
) -> tuple[FinancialFact, Conflict | None]:
    company, period, metric = key
    cands = sorted(
        cands,
        key=lambda c: (
            c.source != SOURCE_STRUCTURED,
            c.provenance.doc_id,
            c.provenance.page,
            c.provenance.block_id or "",
            c.ref,
        ),
    )
    ok = [c for c in cands if c.status is ValueStatus.OK]
    flags: set[str] = set(extra or ())
    for c in cands:
        flags.update(c.flags)
    if ok and any(c.status is ValueStatus.UNRESOLVED for c in cands):
        flags.add(FLAG_HAS_UNRESOLVED)
    ends = sorted({c.fiscal_end for c in (ok or cands) if c.fiscal_end})
    end = ends[0] if len(ends) == 1 else None

    def unresolved(reason: str) -> tuple[FinancialFact, Conflict]:
        conflict = Conflict(
            company,
            period,
            metric,
            reason,
            tuple((c.source, str(c.value), c.provenance) for c in ok),
        )
        fact = FinancialFact(
            company,
            period,
            metric,
            None,
            UNIT_VND,
            ValueStatus.UNRESOLVED,
            ok[0].source,
            ok[0].provenance,
            tuple(sorted(flags | {reason})),
            (),
            end,
        )
        return fact, conflict

    if not ok:
        first = cands[0]
        fact = FinancialFact(
            company,
            period,
            metric,
            None,
            None,
            _fallback_status([c.status for c in cands]),
            first.source,
            first.provenance,
            tuple(sorted(flags)),
            (),
            end,
        )
        return fact, None

    if len(ends) > 1:
        return unresolved(FLAG_FISCAL_END_MISMATCH)
    if any(c.status is ValueStatus.NOT_APPLICABLE for c in cands):
        # một nguồn ghi "-" (không phát sinh) còn nguồn khác có số: mâu thuẫn, không tự chọn
        return unresolved(FLAG_NA_CONFLICT)
    struct = [c for c in ok if c.source == SOURCE_STRUCTURED]
    ocr = [c for c in ok if c.source == SOURCE_OCR]
    if struct and not _agree(struct, s):
        return unresolved(FLAG_STRUCTURED_CONFLICT)
    if ocr and not _agree(ocr, s):
        return unresolved(FLAG_OCR_CONFLICT)
    if struct and ocr:
        if not _agree([struct[0], ocr[0]], s):
            return unresolved(FLAG_SOURCE_CONFLICT)
        flags.add(FLAG_CONFIRMED_BY_OCR)
    chosen = struct[0] if struct else ocr[0]
    if not struct and any(
        c.source == SOURCE_STRUCTURED and c.status is ValueStatus.UNRESOLVED for c in cands
    ):
        flags.add(FLAG_OCR_FALLBACK)
    fact = FinancialFact(
        company,
        period,
        metric,
        chosen.value,
        UNIT_VND,
        ValueStatus.OK,
        chosen.source,
        chosen.provenance,
        tuple(sorted(flags)),
        (),
        end,
    )
    return fact, None


def _agree(cands: list[Candidate], s: NormalizationSettings) -> bool:
    ref = cands[0].value
    if ref is None:
        return False
    return all(
        c.value is not None and _close(ref, c.value, s.relative_tolerance) for c in cands[1:]
    )


def _close(a: Decimal, b: Decimal, tol: Decimal) -> bool:
    if a == b:
        return True
    return abs(a - b) <= tol * max(abs(a), abs(b))


def _fallback_status(statuses: list[ValueStatus]) -> ValueStatus:
    """Không có giá trị OK: UNRESOLVED nếu có ô hỏng; NOT_APPLICABLE chỉ khi mọi nguồn ghi "-"."""
    if ValueStatus.UNRESOLVED in statuses:
        return ValueStatus.UNRESOLVED
    if all(st is ValueStatus.NOT_APPLICABLE for st in statuses):
        return ValueStatus.NOT_APPLICABLE
    if ValueStatus.UNKNOWN in statuses and ValueStatus.MISSING not in statuses:
        return ValueStatus.UNKNOWN
    return ValueStatus.MISSING
