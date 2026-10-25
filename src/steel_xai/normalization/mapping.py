"""Gán giá trị thô của Phase 2 vào (công ty, năm tài chính, metric) và quy đổi về VND.

Mỗi giá trị khớp nhãn metric hoặc thành `Candidate` (kể cả khi trạng thái không phải OK), hoặc bị
ghi vào `Rejection` kèm lý do. Không có đường thứ ba: không bỏ im lặng (spec mục 23).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from steel_xai.contracts import Provenance, ValueStatus
from steel_xai.extraction.settings import norm_key
from steel_xai.extraction.structured import RawStructuredValue
from steel_xai.extraction.tables import Period, RawTableValue, TableExtraction
from steel_xai.normalization.context import DocContext
from steel_xai.normalization.settings import (
    MetricDef,
    NormalizationSettings,
    label_key,
    loose_label_key,
    norm_label,
)

SOURCE_STRUCTURED = "structured"
SOURCE_OCR = "ocr"

FLAG_UNIT_UNKNOWN = "unit_unknown"
FLAG_UNIT_FROM_DOCUMENT = "unit_from_document"
FLAG_UNEXPECTED_SIGN = "unexpected_sign"
FLAG_SIGN_FLIPPED = "sign_flipped"
FLAG_SIGN_NORMALIZED = "sign_normalized"
FLAG_SIGN_NOT_CHECKED = "sign_not_checked"
FLAG_PERIOD_FROM_REPORT = "period_from_report_context"
FLAG_FISCAL_END_FROM_REPORT = "fiscal_end_from_report"
FLAG_FISCAL_END_UNKNOWN = "fiscal_end_unknown"

R_COMPANY_MISSING = "company_missing"
R_IS_PERCENT = "is_percent"
R_CODE_MISMATCH = "code_mismatch"
# nhãn chỉ khớp khi bỏ dấu (OCR mất dấu) và mã số trùng mã của metric: fact vẫn dùng, giữ cờ
FLAG_LOOSE_LABEL = "label_matched_without_diacritics"
R_CODE_MISSING = "code_missing"
R_COLUMN_MISMATCH = "column_mismatch"
R_TABLE_CONTEXT = "table_context_mismatch"
R_DATE_NOT_MONTH_END = "date_not_month_end"
R_FLOW_DURATION_UNKNOWN = "flow_duration_unknown"
R_NOT_ANNUAL = "not_annual"
R_PERIOD_UNUSABLE = "period_unusable"
R_REPORT_PERIOD_UNKNOWN = "report_period_unknown"

# "31 tháng 12" là ngày, không phải độ dài kỳ: bỏ trước khi tìm từ khóa kỳ không phải năm.
_DAY_MONTH_RE = re.compile(r"\d{1,2}\s+tháng\s+\d{1,2}")


@dataclass(frozen=True)
class Candidate:
    """Một giá trị đã gán vào (công ty, năm tài chính, metric); `value` đã ở VND khi status OK."""

    company: str
    period: str  # FY<năm>
    metric: str
    value: Decimal | None
    status: ValueStatus
    source: str
    provenance: Provenance
    flags: tuple[str, ...]
    ref: str  # value_id (OCR) hoặc file:dòng:cột (CSV), để truy ngược
    fiscal_end: str | None = None  # ISO
    report_year: int | None = None  # năm tài chính của báo cáo chứa giá trị (None = không biết)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.company, self.period, self.metric)

    @property
    def fiscal_year(self) -> int:
        return int(self.period[2:])

    @property
    def is_comparative(self) -> bool:
        """Số so sánh: nằm trong báo cáo của năm sau năm tài chính của nó."""
        return self.report_year is not None and self.report_year > self.fiscal_year


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
    tables: Sequence[TableExtraction],
    settings: NormalizationSettings,
    contexts: Mapping[str, DocContext] | None = None,
) -> tuple[list[Candidate], list[Rejection]]:
    contexts = contexts or {}
    by_alias = _alias_index(settings, structured=False)
    by_loose = _alias_index(settings, structured=False, key=loose_label_key)
    candidates: list[Candidate] = []
    rejections: list[Rejection] = []
    for table in tables:
        labels = frozenset(norm_label(v.row_label) for v in table.values)
        for v in table.values:
            options = by_alias.get(label_key(v.row_label))
            loose = False
            if options is None and v.row_code:
                # OCR mất dấu: chỉ nhận khi mã số của dòng là mã của metric
                options = [
                    m
                    for m in by_loose.get(loose_label_key(v.row_label), [])
                    if v.row_code in m.codes
                ] or None
                loose = options is not None
            if options is None:
                continue
            metric, pick_reason = _pick_metric(options, v.row_code)
            if metric is None:
                names = "|".join(sorted(m.name for m in options))
                rejections.append(Rejection(SOURCE_OCR, v.value_id, names, (pick_reason,)))
                continue
            ctx = contexts.get(v.doc_id)
            reasons = _table_reasons(v, metric, labels, settings)
            year, end, p_flags, p_reason = _fiscal(v, metric.kind, settings, ctx)
            if p_reason:
                reasons.append(p_reason)
            if reasons or v.company is None or year is None:
                rejections.append(
                    Rejection(SOURCE_OCR, v.value_id, metric.name, tuple(sorted(set(reasons))))
                )
                continue
            unit, u_flags = _unit(v, ctx)
            value, status, flags = _finish(v.value, v.status, unit, metric, settings, True)
            candidates.append(
                Candidate(
                    company=v.company,
                    period=f"FY{year}",
                    metric=metric.name,
                    value=value,
                    status=status,
                    source=SOURCE_OCR,
                    provenance=v.provenance(),
                    flags=tuple(
                        sorted(
                            set(v.flags)
                            | set(p_flags)
                            | set(u_flags)
                            | set(flags)
                            | ({FLAG_LOOSE_LABEL} if loose else set())
                        )
                    ),
                    ref=v.value_id,
                    fiscal_end=None if end is None else end.isoformat(),
                )
            )
    return _with_report_years(candidates, contexts), rejections


def map_structured(
    values: Sequence[RawStructuredValue], settings: NormalizationSettings
) -> tuple[list[Candidate], list[Rejection]]:
    """Nhánh CSV (hiện không dùng): chỉ nhận dữ liệu năm; có quý → loại (`not_annual`)."""
    by_alias = _alias_index(settings, structured=True)
    candidates: list[Candidate] = []
    rejections: list[Rejection] = []
    for v in values:
        options = by_alias.get(label_key(v.item))
        if options is None:
            continue
        metric = options[0]
        ref = f"{v.ref.file}:{v.ref.line}:{v.ref.column}"
        reasons = set(v.flags) & settings.blocking_flags
        if v.company is None:
            reasons.add(R_COMPANY_MISSING)
        if v.year is None:
            reasons.add(R_PERIOD_UNUSABLE)
        elif v.quarter is not None:
            reasons.add(R_NOT_ANNUAL)
        if reasons or v.company is None or v.year is None:
            rejections.append(
                Rejection(SOURCE_STRUCTURED, ref, metric.name, tuple(sorted(reasons)))
            )
            continue
        value, status, flags = _finish(v.value, v.status, v.unit, metric, settings, False)
        candidates.append(
            Candidate(
                company=v.company,
                period=f"FY{v.year}",
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
                flags=tuple(sorted(set(v.flags) | set(flags) | {FLAG_FISCAL_END_UNKNOWN})),
                ref=ref,
            )
        )
    return candidates, rejections


def _with_report_years(
    cands: list[Candidate], contexts: Mapping[str, DocContext]
) -> list[Candidate]:
    """Gắn năm của báo cáo: từ ngày kết thúc đọc trong văn bản; không có thì lấy năm lớn nhất
    trong chính tài liệu (cột "năm nay" luôn có mặt trong bảng của báo cáo năm)."""
    max_fy: dict[str, int] = defaultdict(int)
    for c in cands:
        max_fy[c.provenance.doc_id] = max(max_fy[c.provenance.doc_id], c.fiscal_year)
    out = []
    for c in cands:
        ctx = contexts.get(c.provenance.doc_id)
        year = ctx.report_end.year if ctx and ctx.report_end else max_fy[c.provenance.doc_id]
        out.append(replace(c, report_year=year))
    return out


def _alias_index(
    settings: NormalizationSettings, *, structured: bool, key=label_key
) -> dict[str, list[MetricDef]]:
    out: dict[str, list[MetricDef]] = defaultdict(list)
    to_key = key
    for m in settings.metrics.values():
        for a in m.structured_aliases if structured else m.aliases:
            key = to_key(a)
            if m not in out[key]:
                out[key].append(m)
    return dict(out)


def _pick_metric(options: list[MetricDef], row_code: str | None) -> tuple[MetricDef | None, str]:
    """Nhãn dùng chung giữa nhiều metric (ví dụ mã 140 và 141) được tách bằng mã số."""
    if len(options) == 1:
        return options[0], ""
    if row_code is None:
        return None, R_CODE_MISSING
    hits = [m for m in options if row_code in m.codes]
    return (hits[0], "") if len(hits) == 1 else (None, R_CODE_MISMATCH)


def _table_reasons(
    v: RawTableValue, m: MetricDef, table_labels: frozenset[str], s: NormalizationSettings
) -> list[str]:
    reasons = set(v.flags) & s.blocking_flags
    if "period_relative" in reasons and _relative_kind(v, s) is not None:
        reasons.discard("period_relative")  # giải ở _fiscal bằng ngữ cảnh báo cáo
    if v.company is None:
        reasons.add(R_COMPANY_MISSING)
    if v.is_percent:
        reasons.add(R_IS_PERCENT)
    if m.codes:
        if v.row_code is None:
            reasons.add(R_CODE_MISSING)
        elif v.row_code not in m.codes:
            reasons.add(R_CODE_MISMATCH)
    header = norm_key(" ".join(v.column_header))
    if any(k not in header for k in m.column_includes) or any(
        k in header for k in m.column_excludes
    ):
        reasons.add(R_COLUMN_MISMATCH)
    if not m.requires_table_labels <= table_labels:
        reasons.add(R_TABLE_CONTEXT)
    return sorted(reasons)


def _relative_kind(v: RawTableValue, s: NormalizationSettings) -> str | None:
    """ "current" / "prior" nếu header cột là cụm tương đối nhận ra được, ngược lại None."""
    if v.period is not None:
        return None
    header = norm_key(" ".join(v.column_header))
    cur = any(k in header for k in s.current_period_keywords)
    pri = any(k in header for k in s.prior_period_keywords)
    if cur == pri:
        return None  # không có hoặc mơ hồ
    return "current" if cur else "prior"


def _shift_year(d: dt.date, years: int) -> dt.date:
    y = d.year + years
    return dt.date(y, d.month, min(d.day, calendar.monthrange(y, d.month)[1]))


def _fiscal(
    v: RawTableValue, kind: str, s: NormalizationSettings, ctx: DocContext | None
) -> tuple[int | None, dt.date | None, list[str], str | None]:
    """(năm tài chính, ngày kết thúc, cờ, lý do loại)."""
    rel = _relative_kind(v, s)
    if rel is not None:
        if ctx is None or ctx.report_end is None:
            return None, None, [], R_REPORT_PERIOD_UNKNOWN
        end = ctx.report_end if rel == "current" else _shift_year(ctx.report_end, -1)
        return end.year, end, [FLAG_PERIOD_FROM_REPORT], None
    p: Period | None = v.period
    if p is None:
        return None, None, [], R_PERIOD_UNUSABLE
    if p.quarter is not None:
        return None, None, [], R_NOT_ANNUAL
    # Header hai tầng: "Năm kết thúc ngày" (ô cha) + "30/9/2022 VND" (ô con) → xét cả đường dẫn.
    text = _DAY_MONTH_RE.sub(" ", norm_key(" ".join((*v.column_header, p.text))))
    if p.date is not None:
        try:
            d = dt.date.fromisoformat(p.date)
        except ValueError:
            return None, None, [], R_PERIOD_UNUSABLE
        if kind == "stock":
            if d.day != calendar.monthrange(d.year, d.month)[1]:
                return None, None, [], R_DATE_NOT_MONTH_END
        elif any(k in text for k in s.non_annual_keywords) or not any(
            k in text for k in s.annual_keywords
        ):
            return None, None, [], R_FLOW_DURATION_UNKNOWN
        return d.year, d, [], None
    if p.year is None:
        return None, None, [], R_PERIOD_UNUSABLE
    if kind == "flow" and any(k in text for k in s.non_annual_keywords):
        return None, None, [], R_FLOW_DURATION_UNKNOWN
    if ctx is not None and ctx.report_end is not None:
        end = _shift_year(ctx.report_end, p.year - ctx.report_end.year)
        return p.year, end, [FLAG_FISCAL_END_FROM_REPORT], None
    return p.year, None, [FLAG_FISCAL_END_UNKNOWN], None


def _unit(v: RawTableValue, ctx: DocContext | None) -> tuple[str | None, list[str]]:
    if v.unit is not None:
        return v.unit, []
    if (
        "unit_missing" in v.flags
        and not {"unit_unrecognized", "unit_conflict"}
        & set(v.flags)  # đơn vị lạ/mâu thuẫn: không đoán
        and ctx is not None
        and ctx.unit is not None
    ):
        return ctx.unit, [FLAG_UNIT_FROM_DOCUMENT]
    return None, []


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
    if m.magnitude:
        if value < 0:
            return -value, ValueStatus.OK, [FLAG_SIGN_NORMALIZED]
        return value, ValueStatus.OK, []
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
