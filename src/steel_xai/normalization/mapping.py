"""Gán giá trị thô của Phase 2 vào (công ty, kỳ, metric) và quy đổi về VND.

Mỗi giá trị khớp nhãn metric hoặc thành `Candidate` (kể cả khi trạng thái không phải OK), hoặc bị
ghi vào `Rejection` kèm lý do. Không có đường thứ ba: không bỏ im lặng (spec mục 23).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from steel_xai.contracts import Provenance, ValueStatus
from steel_xai.extraction.settings import norm_key
from steel_xai.extraction.structured import RawStructuredValue
from steel_xai.extraction.tables import Period, RawTableValue, TableExtraction
from steel_xai.normalization.settings import MetricDef, NormalizationSettings, norm_label

SOURCE_STRUCTURED = "structured"
SOURCE_OCR = "ocr"

FLAG_UNIT_UNKNOWN = "unit_unknown"
FLAG_UNEXPECTED_SIGN = "unexpected_sign"
FLAG_SIGN_FLIPPED = "sign_flipped"
FLAG_SIGN_NOT_CHECKED = "sign_not_checked"

# "31 tháng 12" là ngày, không phải độ dài kỳ: bỏ trước khi tìm từ khóa kỳ không phải năm.
_DAY_MONTH_RE = re.compile(r"\d{1,2}\s+tháng\s+\d{1,2}")

R_COMPANY_MISSING = "company_missing"
R_IS_PERCENT = "is_percent"
R_CODE_MISMATCH = "code_mismatch"
R_CODE_MISSING = "code_missing"
R_COLUMN_MISMATCH = "column_mismatch"
R_TABLE_CONTEXT = "table_context_mismatch"
R_DATE_NOT_QUARTER_END = "date_not_quarter_end"
R_FLOW_DURATION_UNKNOWN = "flow_duration_unknown"
R_PERIOD_UNUSABLE = "period_unusable"


@dataclass(frozen=True)
class Candidate:
    """Một giá trị đã gán vào (công ty, kỳ, metric); `value` đã ở VND khi status OK."""

    company: str
    period: str
    metric: str
    value: Decimal | None
    status: ValueStatus
    source: str
    provenance: Provenance
    flags: tuple[str, ...]
    ref: str  # value_id (OCR) hoặc file:dòng:cột (CSV), để truy ngược

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.company, self.period, self.metric)


@dataclass(frozen=True)
class Rejection:
    source: str
    ref: str
    metric: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "ref": self.ref,
            "metric": self.metric,
            "reasons": list(self.reasons),
        }


def map_tables(
    tables: Sequence[TableExtraction], settings: NormalizationSettings
) -> tuple[list[Candidate], list[Rejection]]:
    by_alias = _alias_index(settings, structured=False)
    candidates: list[Candidate] = []
    rejections: list[Rejection] = []
    for table in tables:
        labels = frozenset(norm_label(v.row_label) for v in table.values)
        for v in table.values:
            metric = by_alias.get(norm_label(v.row_label))
            if metric is None:
                continue
            reasons = _table_reasons(v, metric, labels, settings)
            period, p_reason = _period_key(v.period, metric.kind, settings)
            if p_reason:
                reasons.append(p_reason)
            if reasons or v.company is None or period is None:
                rejections.append(Rejection(SOURCE_OCR, v.value_id, metric.name, tuple(reasons)))
                continue
            value, status, flags = _finish(v.value, v.status, v.unit, metric, settings, True)
            candidates.append(
                Candidate(
                    company=v.company,
                    period=period,
                    metric=metric.name,
                    value=value,
                    status=status,
                    source=SOURCE_OCR,
                    provenance=v.provenance(),
                    flags=tuple(sorted(set(v.flags) | set(flags))),
                    ref=v.value_id,
                )
            )
    return candidates, rejections


def map_structured(
    values: Sequence[RawStructuredValue], settings: NormalizationSettings
) -> tuple[list[Candidate], list[Rejection]]:
    by_alias = _alias_index(settings, structured=True)
    candidates: list[Candidate] = []
    rejections: list[Rejection] = []
    for v in values:
        metric = by_alias.get(norm_label(v.item))
        if metric is None:
            continue
        ref = f"{v.ref.file}:{v.ref.line}:{v.ref.column}"
        reasons = set(v.flags) & settings.blocking_flags
        if v.company is None:
            reasons.add(R_COMPANY_MISSING)
        period = _structured_period(v, metric)
        if period is None:
            reasons.add(R_PERIOD_UNUSABLE)
        if reasons or v.company is None or period is None:
            rejections.append(
                Rejection(SOURCE_STRUCTURED, ref, metric.name, tuple(sorted(reasons)))
            )
            continue
        value, status, flags = _finish(v.value, v.status, v.unit, metric, settings, False)
        candidates.append(
            Candidate(
                company=v.company,
                period=period,
                metric=metric.name,
                value=value,
                status=status,
                source=SOURCE_STRUCTURED,
                provenance=Provenance(
                    doc_id=v.ref.file,
                    page=0,  # CSV không có trang; vị trí nằm ở block_id
                    block_id=f"L{v.ref.line}:{v.ref.column}",
                    source_text=v.raw,
                ),
                flags=tuple(sorted(set(v.flags) | set(flags))),
                ref=ref,
            )
        )
    return candidates, rejections


def _alias_index(settings: NormalizationSettings, *, structured: bool) -> dict[str, MetricDef]:
    out: dict[str, MetricDef] = {}
    for m in settings.metrics.values():
        for a in m.structured_aliases if structured else m.aliases:
            out[a] = m
    return out


def _table_reasons(
    v: RawTableValue, m: MetricDef, table_labels: frozenset[str], s: NormalizationSettings
) -> list[str]:
    reasons = sorted(set(v.flags) & s.blocking_flags)
    if v.company is None:
        reasons.append(R_COMPANY_MISSING)
    if v.is_percent:
        reasons.append(R_IS_PERCENT)
    if m.codes:
        if v.row_code is None:
            reasons.append(R_CODE_MISSING)
        elif v.row_code not in m.codes:
            reasons.append(R_CODE_MISMATCH)
    header = norm_key(" ".join(v.column_header))
    if any(k not in header for k in m.column_includes) or any(
        k in header for k in m.column_excludes
    ):
        reasons.append(R_COLUMN_MISMATCH)
    if not m.requires_table_labels <= table_labels:
        reasons.append(R_TABLE_CONTEXT)
    return reasons


def _period_key(
    p: Period | None, kind: str, s: NormalizationSettings
) -> tuple[str | None, str | None]:
    """(khóa kỳ, lý do loại). Khóa: `2024Q3` hoặc `FY2024`."""
    if p is None:
        return None, R_PERIOD_UNUSABLE
    if kind == "stock":
        if p.date is not None:
            try:
                d = dt.date.fromisoformat(p.date)
            except ValueError:
                return None, R_PERIOD_UNUSABLE
            last_day = calendar.monthrange(d.year, d.month)[1]
            if d.month not in s.quarter_end_months or d.day != last_day:
                return None, R_DATE_NOT_QUARTER_END
            return f"{d.year}Q{(d.month - 1) // 3 + 1}", None
        if p.year is not None and p.quarter is not None:
            return f"{p.year}Q{p.quarter}", None
        return None, R_PERIOD_UNUSABLE
    if p.year is None:
        return None, R_PERIOD_UNUSABLE
    if p.quarter is not None:
        return f"{p.year}Q{p.quarter}", None
    text = _DAY_MONTH_RE.sub(" ", norm_key(p.text))
    if any(k in text for k in s.non_annual_keywords):
        return None, R_FLOW_DURATION_UNKNOWN
    if p.date is not None and not any(k in text for k in s.annual_keywords):
        return None, R_FLOW_DURATION_UNKNOWN
    return f"FY{p.year}", None


def _structured_period(v: RawStructuredValue, m: MetricDef) -> str | None:
    if v.year is None:
        return None
    if v.quarter is not None:
        return f"{v.year}Q{v.quarter}"
    return f"{v.year}Q4" if m.kind == "stock" else f"FY{v.year}"


def _finish(
    value: Decimal | None,
    status: ValueStatus,
    unit: str | None,
    m: MetricDef,
    s: NormalizationSettings,
    sign_convention_known: bool,
) -> tuple[Decimal | None, ValueStatus, list[str]]:
    """Quy đổi VND, xử lý dấu. Số không quy đổi/kiểm được → UNRESOLVED, không đoán."""
    if status is not ValueStatus.OK or value is None:
        return None, status, []
    scale = s.unit_scales.get(unit) if unit is not None else None
    if scale is None:
        return None, ValueStatus.UNRESOLVED, [FLAG_UNIT_UNKNOWN]
    value = value * scale
    flags: list[str] = []
    if m.flip_sign and not sign_convention_known:
        # CSV: quy ước dấu do nguồn quyết định (nhóm chốt sau) → lấy độ lớn, ghi cờ.
        return abs(value), ValueStatus.OK, [FLAG_SIGN_NOT_CHECKED]
    if m.expected_sign is not None:
        wrong = value > 0 if m.expected_sign == "negative" else value < 0
        if wrong:
            return None, ValueStatus.UNRESOLVED, [FLAG_UNEXPECTED_SIGN]
        if m.flip_sign:
            value = -value if value != 0 else value
            flags.append(FLAG_SIGN_FLIPPED)
    return value, ValueStatus.OK, flags
