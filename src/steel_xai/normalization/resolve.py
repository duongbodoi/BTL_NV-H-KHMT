"""Gộp candidate thành `FinancialFact` và tính metric dẫn xuất (spec mục 7.1, 23).

Quy tắc nguồn: dữ liệu có cấu trúc > số trích từ OCR. Hai nguồn OK mà lệch nhau → UNRESOLVED
(`source_conflict`), không tự chọn im lặng. Số lệch giữa hai lần xuất hiện trong cùng một nguồn
(ví dụ báo cáo năm sau trình bày lại số năm trước) cũng là UNRESOLVED.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
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
FLAG_CONFIRMED_BY_OCR = "confirmed_by_ocr"
FLAG_OCR_FALLBACK = "structured_unresolved_ocr_fallback"
FLAG_HAS_UNRESOLVED = "has_unresolved_candidate"
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
) -> tuple[list[FinancialFact], list[Conflict]]:
    groups: dict[tuple[str, str, str], list[Candidate]] = defaultdict(list)
    for c in candidates:
        groups[c.key].append(c)
    facts: list[FinancialFact] = []
    conflicts: list[Conflict] = []
    for key in sorted(groups):
        fact, conflict = _resolve_group(key, groups[key], settings)
        facts.append(fact)
        if conflict is not None:
            conflicts.append(conflict)
    return facts, conflicts


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
    status = ValueStatus.MISSING
    unusable = False
    for metric, fact in ((num_metric, num), (den_metric, den)):
        if fact is None:
            flags.append(f"input_missing:{metric}")
            unusable = True
        elif fact.status is not ValueStatus.OK:
            flags.append(f"input_{fact.status.value.lower()}:{metric}")
            unusable = True
            if fact.status is ValueStatus.UNRESOLVED:
                status = ValueStatus.UNRESOLVED
    if unusable or num is None or den is None or num.value is None or den.value is None:
        return FinancialFact(
            company, period, name, None, UNIT_RATIO, status, "derived", None, tuple(flags), inputs
        )
    if den.value == 0:
        flags.append(FLAG_ZERO_DENOMINATOR)
        return FinancialFact(
            company,
            period,
            name,
            None,
            UNIT_RATIO,
            ValueStatus.UNRESOLVED,
            "derived",
            None,
            tuple(flags),
            inputs,
        )
    return FinancialFact(
        company,
        period,
        name,
        num.value / den.value,
        UNIT_RATIO,
        ValueStatus.OK,
        "derived",
        None,
        tuple(flags),
        inputs,
    )


def _resolve_group(
    key: tuple[str, str, str], cands: list[Candidate], s: NormalizationSettings
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
    flags: set[str] = set()
    for c in cands:
        flags.update(c.flags)
    if ok and any(c.status is ValueStatus.UNRESOLVED for c in cands):
        flags.add(FLAG_HAS_UNRESOLVED)

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
        )
        return fact, None

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
