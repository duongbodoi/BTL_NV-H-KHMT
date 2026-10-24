"""Data contract dùng chung giữa các phase (spec mục 18).

Chỉ định nghĩa những gì phase hiện tại cần; các contract khác thêm ở phase tương ứng.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ValueStatus(StrEnum):
    """Trạng thái một giá trị số (spec mục 18, 23). Chỉ `OK` mới có `value`; không ép về 0."""

    OK = "OK"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class Provenance:
    """Nguồn gốc của một mảnh dữ liệu: tài liệu, trang PDF, block/chunk, văn bản gốc.

    `page` luôn là số trang PDF (1-based), không phải số trang in trên tài liệu.
    """

    doc_id: str
    page: int
    block_id: str | None = None
    chunk_id: str | None = None
    source_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Provenance:
        return cls(
            doc_id=d["doc_id"],
            page=d["page"],
            block_id=d.get("block_id"),
            chunk_id=d.get("chunk_id"),
            source_text=d.get("source_text"),
        )


FACT_SOURCES = ("structured", "ocr", "derived")


@dataclass(frozen=True)
class FinancialFact:
    """Một giá trị tài chính đã chuẩn hóa: (công ty, kỳ, metric) → số VND hoặc trạng thái.

    `period`: năm tài chính `FY2024` (dữ liệu là BCTC năm). `fiscal_period_end` là ngày kết thúc
    năm tài chính (ISO) nếu đọc được, vì công ty có năm tài chính lệch lịch (kết thúc 30/9...).
    `value` là `Decimal`, chỉ có khi `status == OK`. `source = "derived"` là số do code tính từ các
    fact khác (`inputs` ghi khóa của chúng), không có provenance riêng.
    """

    company: str
    period: str
    metric: str
    value: Decimal | None
    unit: str | None
    status: ValueStatus
    source: str
    provenance: Provenance | None = None
    validation_flags: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    fiscal_period_end: str | None = None

    def __post_init__(self) -> None:
        if self.source not in FACT_SOURCES:
            raise ValueError(f"source phải thuộc {FACT_SOURCES}, nhận {self.source!r}")
        if (self.value is not None) != (self.status is ValueStatus.OK):
            raise ValueError("value chỉ có khi status == OK")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.company, self.period, self.metric)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "period": self.period,
            "metric": self.metric,
            "value": None if self.value is None else str(self.value),
            "unit": self.unit,
            "status": self.status.value,
            "source": self.source,
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
            "validation_flags": list(self.validation_flags),
            "inputs": list(self.inputs),
            "fiscal_period_end": self.fiscal_period_end,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FinancialFact:
        return cls(
            company=d["company"],
            period=d["period"],
            metric=d["metric"],
            value=None if d.get("value") is None else Decimal(d["value"]),
            unit=d.get("unit"),
            status=ValueStatus(d["status"]),
            source=d["source"],
            provenance=None
            if d.get("provenance") is None
            else Provenance.from_dict(d["provenance"]),
            validation_flags=tuple(d.get("validation_flags", ())),
            inputs=tuple(d.get("inputs", ())),
            fiscal_period_end=d.get("fiscal_period_end"),
        )


@dataclass(frozen=True)
class Anomaly:
    """Một bất thường do engine phát hiện (spec mục 8, 18). Không gán nhãn tốt/xấu.

    `indicators`: giá trị các chỉ báo của loại này (chuỗi Decimal, `None` = không tính được).
    `thresholds_hit`: các luật bị vượt, mỗi luật {indicator, direction, value, threshold}.
    `baseline_used`: các cơ sở so sánh thực sự dùng: yoy, peer, history_z, history_mad.
    `severity`: lớn nhất của |giá trị| / |ngưỡng| trong các luật bị vượt (>= 1).
    `direction`: dấu biến động của CHÍNH công ty so năm trước, không phải kết quả so peer.
    `peer_relative`: so với nhóm ngành, chỉ là thuộc tính phụ: above | below | within | None.
    `asof_date`: ngày công bố BCTC của kỳ bất thường (ISO) nếu đã có, ngược lại `None`;
    `asof_source`: nguồn của ngày đó (manual | audit_report_date).
    """

    company: str
    period: str
    type: str
    direction: str
    indicators: dict[str, str | None]
    thresholds_hit: tuple[dict[str, str], ...]
    baseline_used: tuple[str, ...]
    severity: Decimal
    flags: tuple[str, ...] = ()
    asof_date: str | None = None
    peer_relative: str | None = None
    asof_source: str | None = None

    def __post_init__(self) -> None:
        if self.direction not in ("up", "down"):
            raise ValueError(f"direction phải là up/down, nhận {self.direction!r}")

    @property
    def anomaly_id(self) -> str:
        return f"{self.company}|{self.period}|{self.type}|{self.direction}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "period": self.period,
            "type": self.type,
            "direction": self.direction,
            "indicators": dict(self.indicators),
            "thresholds_hit": [dict(h) for h in self.thresholds_hit],
            "baseline_used": list(self.baseline_used),
            "severity": str(self.severity),
            "flags": list(self.flags),
            "asof_date": self.asof_date,
            "peer_relative": self.peer_relative,
            "asof_source": self.asof_source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Anomaly:
        return cls(
            company=d["company"],
            period=d["period"],
            type=d["type"],
            direction=d["direction"],
            indicators=dict(d["indicators"]),
            thresholds_hit=tuple(dict(h) for h in d["thresholds_hit"]),
            baseline_used=tuple(d["baseline_used"]),
            severity=Decimal(d["severity"]),
            flags=tuple(d.get("flags", ())),
            asof_date=d.get("asof_date"),
            peer_relative=d.get("peer_relative"),
            asof_source=d.get("asof_source"),
        )


CHECK_KINDS = ("quant", "text")
CHECK_IMPORTANCE = ("critical", "supporting")
CHECK_POLARITY = ("support", "contradict")
HYPOTHESIS_STATUSES = ("draft", "reviewed")


@dataclass(frozen=True)
class Check:
    """Một check có cấu trúc của giả thuyết (spec mục 10.1).

    `quant`: `tool` + `params` + `pass_when` (điều kiện đạt, dữ liệu, không phải code).
    `text`: `queries` (truy vấn RAG; LLM chỉ được diễn đạt lại, không thêm/bớt check critical).
    """

    id: str
    kind: str
    importance: str
    polarity: str
    tool: str | None = None
    params: dict[str, Any] | None = None
    pass_when: dict[str, Any] | None = None
    queries: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "importance": self.importance,
            "polarity": self.polarity,
            "tool": self.tool,
            "params": self.params,
            "pass_when": self.pass_when,
            "queries": list(self.queries),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Check:
        return cls(
            id=d["id"],
            kind=d["kind"],
            importance=d["importance"],
            polarity=d["polarity"],
            tool=d.get("tool"),
            params=d.get("params"),
            pass_when=d.get("pass_when"),
            queries=tuple(d.get("queries") or ()),
        )


@dataclass(frozen=True)
class Hypothesis:
    """Giả thuyết nghiệp vụ của thư viện (spec mục 9.1): 9 trường báo cáo + metadata + checks.

    Thư viện dành riêng cho ngành thép, không gắn với công ty cụ thể. Chỉ `status == "reviewed"`
    (có `reviewed_by` là người) mới được vào pipeline.
    """

    id: str
    anomaly_type: str
    claim: str
    business_rationale: str
    expected_quantitative_evidence: tuple[str, ...]
    expected_textual_evidence: tuple[str, ...]
    contradictory_evidence: tuple[str, ...]
    required_external_data: tuple[str, ...]
    critical_missing_evidence: tuple[str, ...]
    sources: tuple[str, ...] = ()
    status: str = "draft"
    reviewed_by: str | None = None
    checks: tuple[Check, ...] = ()
    code: str | None = None  # mã ngắn do nhóm đặt (INV-1, GM-1...)
    testable_with_annual_data: str | None = None  # yes | partial | no
    source_strength: str | None = None
    extension_beyond_midterm: bool = False

    def __post_init__(self) -> None:
        if self.status not in HYPOTHESIS_STATUSES:
            raise ValueError(f"status phải thuộc {HYPOTHESIS_STATUSES}, nhận {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "anomaly_type": self.anomaly_type,
            "claim": self.claim,
            "business_rationale": self.business_rationale,
            "expected_quantitative_evidence": list(self.expected_quantitative_evidence),
            "expected_textual_evidence": list(self.expected_textual_evidence),
            "contradictory_evidence": list(self.contradictory_evidence),
            "required_external_data": list(self.required_external_data),
            "critical_missing_evidence": list(self.critical_missing_evidence),
            "sources": list(self.sources),
            "status": self.status,
            "reviewed_by": self.reviewed_by,
            "checks": [c.to_dict() for c in self.checks],
            "code": self.code,
            "testable_with_annual_data": self.testable_with_annual_data,
            "source_strength": self.source_strength,
            "extension_beyond_midterm": self.extension_beyond_midterm,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Hypothesis:
        def tup(key: str) -> tuple[str, ...]:
            return tuple(d.get(key) or ())

        return cls(
            id=d["id"],
            anomaly_type=d["anomaly_type"],
            claim=d["claim"],
            business_rationale=d["business_rationale"],
            expected_quantitative_evidence=tup("expected_quantitative_evidence"),
            expected_textual_evidence=tup("expected_textual_evidence"),
            contradictory_evidence=tup("contradictory_evidence"),
            required_external_data=tup("required_external_data"),
            critical_missing_evidence=tup("critical_missing_evidence"),
            sources=tup("sources"),
            status=d.get("status", "draft"),
            reviewed_by=d.get("reviewed_by"),
            checks=tuple(Check.from_dict(c) for c in d.get("checks") or ()),
            code=d.get("code"),
            testable_with_annual_data=_testable(d.get("testable_with_annual_data")),
            source_strength=d.get("source_strength"),
            extension_beyond_midterm=bool(d.get("extension_beyond_midterm", False)),
        )


def _testable(v: Any) -> str | None:
    """YAML 1.1 đọc `yes`/`no` thành bool; chuẩn hóa về yes | no | partial."""
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return v


class CheckStatus(StrEnum):
    """Kết quả một check định lượng (spec mục 10.2). Thiếu dữ liệu KHÔNG phải FAIL."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True)
class CheckInput:
    """Một đầu vào của phép tính, kèm nguồn: fact (có provenance) hoặc chuỗi giá bên ngoài."""

    kind: str  # fact | external
    name: str  # metric hoặc tên chuỗi giá
    period: str  # FY2022, hoặc khoảng tháng của chuỗi ngoài
    value: Decimal
    source: Provenance | str | None = None  # Provenance của fact; hoặc mô tả nguồn chuỗi ngoài
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        src = self.source.to_dict() if isinstance(self.source, Provenance) else self.source
        return {
            "kind": self.kind,
            "name": self.name,
            "period": self.period,
            "value": str(self.value),
            "source": src,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckInput:
        src = d.get("source")
        return cls(
            kind=d["kind"],
            name=d["name"],
            period=d["period"],
            value=Decimal(d["value"]),
            source=Provenance.from_dict(src) if isinstance(src, dict) else src,
            flags=tuple(d.get("flags", ())),
        )


@dataclass(frozen=True)
class CheckResult:
    """Kết quả chạy một `Check` định lượng: trạng thái, giá trị tính được, đầu vào có nguồn.

    `values`: giá trị mỗi thành phần (một giá trị `value`, hoặc mỗi chuỗi giá một giá trị);
    `None` = không tính được. `reason` giải thích NOT_EVALUABLE.
    """

    check_id: str
    status: CheckStatus
    values: dict[str, Decimal | None]
    inputs: tuple[CheckInput, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status is CheckStatus.NOT_EVALUABLE and not self.reason:
            raise ValueError("NOT_EVALUABLE phải có reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "values": {k: None if v is None else str(v) for k, v in self.values.items()},
            "inputs": [i.to_dict() for i in self.inputs],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckResult:
        return cls(
            check_id=d["check_id"],
            status=CheckStatus(d["status"]),
            values={k: None if v is None else Decimal(v) for k, v in d["values"].items()},
            inputs=tuple(CheckInput.from_dict(i) for i in d.get("inputs", ())),
            reason=d.get("reason", ""),
        )
