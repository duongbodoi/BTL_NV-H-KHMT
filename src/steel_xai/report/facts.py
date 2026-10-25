"""Đầu vào của báo cáo (spec mục 13): CHỈ dữ liệu đã xử lý, mỗi mảnh có một mã nguồn.

- `N…` số do code tính (chỉ báo bất thường, ngưỡng, giá trị check, đầu vào của check), kèm chuỗi
  hiển thị do code định dạng;
- `E…` trích dẫn văn bản đã qua kiểm nguyên văn (Phase 9), kèm provenance;
- `M…` phần dữ liệu/bằng chứng còn thiếu (check không kiểm được và lý do);
- `H…` kết luận kiểm chứng của một giả thuyết (trạng thái, hạng, số check critical đạt).
LLM chỉ được dẫn các mã này; không có BCTC gốc trong đầu vào.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from steel_xai.contracts import (
    Anomaly,
    CheckFinding,
    CheckInput,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    Provenance,
)
from steel_xai.report.numbers import display
from steel_xai.report.settings import ReportSettings
from steel_xai.verification.analyze import AnomalyAnalysis


@dataclass(frozen=True)
class NumberFact:
    id: str
    label: str
    value: Decimal
    unit: str
    display: str
    source: str  # mô tả nguồn cho mục "Nguồn" (tài liệu/trang, chuỗi giá, hay "do code tính")
    hypothesis_id: str | None = None
    check_id: str | None = None
    role: str = "indicator"  # indicator | support | contradict | failed | input


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    hypothesis_id: str
    check_id: str
    polarity: str
    quote: str
    provenance: Provenance


@dataclass(frozen=True)
class MissingItem:
    id: str
    hypothesis_id: str
    check_id: str
    kind: str
    importance: str
    reason: str


@dataclass(frozen=True)
class VerdictItem:
    id: str
    hypothesis_id: str
    code: str | None
    claim: str
    status: str
    rank: int | None
    critical_passed: int
    critical_total: int
    supporting_passed: int
    has_verified_text: bool


@dataclass(frozen=True)
class ReportInput:
    anomaly: Anomaly
    numbers: tuple[NumberFact, ...]
    verdicts: tuple[VerdictItem, ...]  # top-k, theo hạng
    evidence: tuple[EvidenceItem, ...]
    missing: tuple[MissingItem, ...]
    others: tuple[VerdictItem, ...]  # các giả thuyết ngoài top-k (chỉ để code hiển thị bảng)
    used_drafts: bool
    years: frozenset[int] = field(default_factory=frozenset)  # năm được phép nhắc trong văn bản

    def __post_init__(self) -> None:
        ids = [x.id for x in (*self.numbers, *self.verdicts, *self.evidence, *self.missing)]
        if len(set(ids)) != len(ids):
            raise ValueError("mã nguồn trùng trong ReportInput")

    @property
    def by_id(self) -> dict[str, NumberFact | VerdictItem | EvidenceItem | MissingItem]:
        return {x.id: x for x in (*self.numbers, *self.verdicts, *self.evidence, *self.missing)}

    @property
    def hypothesis_ids(self) -> frozenset[str]:
        return frozenset(v.hypothesis_id for v in self.verdicts)

    def to_prompt(self) -> str:
        """JSON gửi LLM: không có giá trị thô, chỉ chuỗi hiển thị do code định dạng."""
        a = self.anomaly
        payload: dict[str, Any] = {
            "anomaly": {
                "company": a.company,
                "period": a.period,
                "type": a.type,
                "direction": a.direction,
                "asof_date": a.asof_date,
                "baseline_used": list(a.baseline_used),
                "flags": list(a.flags),
            },
            "numbers": [
                {
                    "id": n.id,
                    "label": n.label,
                    "display": n.display,
                    "role": n.role,
                    "hypothesis_id": n.hypothesis_id,
                    "check_id": n.check_id,
                }
                for n in self.numbers
            ],
            "hypotheses": [
                {
                    "id": v.id,
                    "hypothesis_id": v.hypothesis_id,
                    "code": v.code,
                    "claim": v.claim,
                    "status": v.status,
                    "rank": v.rank,
                    "critical_passed": f"{v.critical_passed}/{v.critical_total}",
                    "supporting_passed": v.supporting_passed,
                    "has_verified_text": v.has_verified_text,
                }
                for v in self.verdicts
            ],
            "evidence": [
                {
                    "id": e.id,
                    "hypothesis_id": e.hypothesis_id,
                    "check_id": e.check_id,
                    "polarity": e.polarity,
                    "quote": e.quote,
                    "document": e.provenance.doc_id,
                    "page": e.provenance.page,
                }
                for e in self.evidence
            ],
            "missing": [
                {
                    "id": m.id,
                    "hypothesis_id": m.hypothesis_id,
                    "check_id": m.check_id,
                    "importance": m.importance,
                    "reason": m.reason,
                }
                for m in self.missing
            ],
            "used_drafts": self.used_drafts,
        }
        return json.dumps(payload, ensure_ascii=False, indent=1)


# ---------------- dựng đầu vào


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _years(text: str) -> set[int]:
    return {int(y) for y in re.findall(r"(?<!\d)(19\d{2}|2[01]\d{2})(?!\d)", text)}


def _input_source(inp: CheckInput) -> str:
    if isinstance(inp.source, Provenance):
        return f"{inp.source.doc_id}, trang {inp.source.page}"
    return str(inp.source) if inp.source else "không rõ nguồn"


class _Builder:
    def __init__(self, settings: ReportSettings) -> None:
        self.s = settings
        self.numbers: list[NumberFact] = []
        self.evidence: list[EvidenceItem] = []
        self.missing: list[MissingItem] = []
        self.years: set[int] = set()
        self._input_ids: dict[tuple[str, str, str, str], str] = {}

    def number(self, label: str, value: Decimal, unit: str, source: str, **kw: Any) -> str:
        nid = f"N{len(self.numbers) + 1}"
        self.numbers.append(NumberFact(nid, label, value, unit, display(value, unit), source, **kw))
        return nid

    def check_input(self, inp: CheckInput, hypothesis_id: str, check_id: str) -> None:
        if inp.kind == "document":  # đoạn văn bản đem so (không phải số liệu)
            return
        key = (inp.kind, inp.name, inp.period, str(inp.value))
        if key in self._input_ids:
            return
        if inp.kind == "fact":
            unit = self.s.fact_units.get(inp.name, "vnd")
            label = f"{inp.name} ({inp.period})"
        else:
            unit = "number"
            series_unit = self.s.series_units.get(inp.name)
            label = f"{inp.name} ({inp.period}" + (f", {series_unit})" if series_unit else ")")
        self.years |= _years(inp.period)
        self._input_ids[key] = self.number(
            label,
            inp.value,
            unit,
            _input_source(inp),
            hypothesis_id=hypothesis_id,
            check_id=check_id,
            role="input",
        )

    def quant(self, h: Hypothesis, f: CheckFinding, role: str) -> None:
        if f.quant is None:
            return
        tool = next((c.tool for c in h.checks if c.id == f.check_id), None)
        unit = self.s.tool_units.get(tool or "", "number")
        for name, v in f.quant.values.items():
            d = _dec(v)
            if d is not None:
                self.number(
                    f"{h.id}/{f.check_id}: {name}",
                    d,
                    unit,
                    f"tính bằng code ({tool})",
                    hypothesis_id=h.id,
                    check_id=f.check_id,
                    role=role,
                )
        for inp in f.quant.inputs:
            self.check_input(inp, h.id, f.check_id)

    def text(self, h: Hypothesis, f: CheckFinding) -> None:
        for e in f.text:
            if any(
                x.hypothesis_id == h.id and x.check_id == e.check_id and x.quote == e.quote
                for x in self.evidence
            ):
                continue
            self.evidence.append(_evidence(f"E{len(self.evidence) + 1}", h.id, e))


def _evidence(eid: str, hypothesis_id: str, e: Evidence) -> EvidenceItem:
    return EvidenceItem(eid, hypothesis_id, e.check_id, e.polarity, e.quote, e.provenance)


def _verdict_item(vid: str, v: HypothesisVerdict, h: Hypothesis) -> VerdictItem:
    return VerdictItem(
        id=vid,
        hypothesis_id=v.hypothesis_id,
        code=h.code,
        claim=h.claim,
        status=v.status.value,
        rank=v.rank,
        critical_passed=v.critical_passed,
        critical_total=v.critical_total,
        supporting_passed=v.supporting_passed,
        has_verified_text=v.has_verified_text,
    )


def build_report_input(
    anomaly: Anomaly,
    analysis: AnomalyAnalysis,
    hypotheses: Iterable[Hypothesis],
    settings: ReportSettings,
) -> ReportInput:
    if analysis.anomaly_id != anomaly.anomaly_id:
        raise ValueError(
            f"kết quả phân tích {analysis.anomaly_id!r} không thuộc {anomaly.anomaly_id!r}"
        )
    lib = {h.id: h for h in hypotheses}
    unknown = [v.hypothesis_id for v in analysis.verdicts if v.hypothesis_id not in lib]
    if unknown:
        raise ValueError(f"verdict của giả thuyết không có trong thư viện: {unknown}")
    if any(v.rank is None for v in analysis.verdicts):
        raise ValueError("verdict chưa được xếp hạng")

    b = _Builder(settings)
    b.years |= _years(anomaly.period) | _years(anomaly.asof_date or "")
    b.years |= {y - 1 for y in _years(anomaly.period)}  # năm so sánh (YoY)
    hit = {h.get("indicator") for h in anomaly.thresholds_hit}
    for name, raw in anomaly.indicators.items():
        d = _dec(raw)
        if d is not None:
            label = settings.indicator_labels.get(name, name)
            b.number(
                f"{label} (vượt ngưỡng)" if name in hit else label,
                d,
                settings.indicator_units.get(name, "number"),
                "chỉ báo do anomaly engine tính (code)",
            )

    top = [v for v in analysis.verdicts if v.rank is not None and v.rank <= settings.top_k]
    others = [v for v in analysis.verdicts if v not in top]
    verdict_items: list[VerdictItem] = []
    for v in top:
        h = lib[v.hypothesis_id]
        verdict_items.append(_verdict_item(f"H{len(verdict_items) + 1}", v, h))
        roles: dict[str, tuple[CheckFinding, str]] = {}
        for role, fs in (
            ("support", v.evidence),
            ("failed", v.failed),
            ("contradict", v.contradictions),
        ):
            for f in fs:
                roles[f.check_id] = (f, role)  # contradict ghi đè failed ghi đè support
        for f, role in roles.values():
            b.quant(h, f, role)
        for f in (*v.evidence, *v.contradictions):
            b.text(h, f)
        for f in v.missing:
            why = f.quant.reason if f.quant is not None and f.quant.reason else f.detail
            b.missing.append(
                MissingItem(f"M{len(b.missing) + 1}", h.id, f.check_id, f.kind, f.importance, why)
            )
    other_items = [
        _verdict_item(f"_other{i}", v, lib[v.hypothesis_id]) for i, v in enumerate(others)
    ]
    return ReportInput(
        anomaly=anomaly,
        numbers=tuple(b.numbers),
        verdicts=tuple(verdict_items),
        evidence=tuple(b.evidence),
        missing=tuple(b.missing),
        others=tuple(other_items),
        used_drafts=analysis.used_drafts,
        years=frozenset(b.years),
    )
