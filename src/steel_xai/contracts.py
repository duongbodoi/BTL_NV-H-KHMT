"""Data contract dùng chung giữa các phase (spec mục 18).

Chỉ định nghĩa những gì phase hiện tại cần; các contract khác thêm ở phase tương ứng.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    direction: str | None = None  # up | down | both: hướng bất thường mà giả thuyết giải thích

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
            "direction": self.direction,
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
            direction=d.get("direction"),
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


EVIDENCE_KINDS = ("quant", "text")
EVIDENCE_POLARITIES = ("support", "contradict")


@dataclass(frozen=True)
class Evidence:
    """Một bằng chứng đã qua kiểm của code (spec mục 18).

    Bằng chứng văn bản: `quote` đã được code kiểm là chuỗi con nguyên văn (sau chuẩn hóa khoảng
    trắng/Unicode) của đoạn gốc trong `provenance`.
    """

    kind: str
    polarity: str
    check_id: str
    provenance: Provenance
    quote: str
    reason: str = ""  # lý do ngắn do LLM nêu (chỉ để người đọc; không dùng để kết luận)

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"kind phải thuộc {EVIDENCE_KINDS}, nhận {self.kind!r}")
        if self.polarity not in EVIDENCE_POLARITIES:
            raise ValueError(f"polarity phải thuộc {EVIDENCE_POLARITIES}, nhận {self.polarity!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "polarity": self.polarity,
            "check_id": self.check_id,
            "provenance": self.provenance.to_dict(),
            "quote": self.quote,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        return cls(
            kind=d["kind"],
            polarity=d["polarity"],
            check_id=d["check_id"],
            provenance=Provenance.from_dict(d["provenance"]),
            quote=d["quote"],
            reason=d.get("reason", ""),
        )


class TextCheckStatus(StrEnum):
    """Kết quả một check văn bản (spec mục 10.3, 10.4)."""

    FOUND = "FOUND"  # có bằng chứng đã kiểm, đúng chiều của check
    MISSING = "MISSING"  # LLM nêu bằng chứng nhưng trích dẫn không qua kiểm nguyên văn → bị loại
    UNVERIFIED = "UNVERIFIED"  # không truy xuất/không có đoạn phù hợp, hoặc không đánh giá được


class VerdictStatus(StrEnum):
    """Trạng thái kiểm chứng của một giả thuyết (spec mục 12.1). Do luật trong code quyết định."""

    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"


FINDING_OUTCOMES = ("satisfied", "not_satisfied", "unknown")


@dataclass(frozen=True)
class CheckFinding:
    """Kết quả của một check đã quy về cùng một thang, kèm bằng chứng gốc.

    `outcome` theo ngữ nghĩa của check: với check polarity `support`, satisfied = có bằng chứng ủng
    hộ; với polarity `contradict`, satisfied = quan sát được bằng chứng chống lại. `unknown` = thiếu
    dữ liệu/không kiểm được (không phải bằng chứng gì cả). `text` là bằng chứng văn bản đã qua kiểm
    trích dẫn (có thể có cả chiều ngược với polarity của check).
    """

    check_id: str
    kind: str
    importance: str
    polarity: str
    outcome: str
    detail: str = ""
    quant: CheckResult | None = None
    text: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in FINDING_OUTCOMES:
            raise ValueError(f"outcome phải thuộc {FINDING_OUTCOMES}, nhận {self.outcome!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "kind": self.kind,
            "importance": self.importance,
            "polarity": self.polarity,
            "outcome": self.outcome,
            "detail": self.detail,
            "quant": None if self.quant is None else self.quant.to_dict(),
            "text": [e.to_dict() for e in self.text],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckFinding:
        return cls(
            check_id=d["check_id"],
            kind=d["kind"],
            importance=d["importance"],
            polarity=d["polarity"],
            outcome=d["outcome"],
            detail=d.get("detail", ""),
            quant=None if d.get("quant") is None else CheckResult.from_dict(d["quant"]),
            text=tuple(Evidence.from_dict(e) for e in d.get("text", ())),
        )


@dataclass(frozen=True)
class HypothesisVerdict:
    """Kết luận kiểm chứng cho một giả thuyết (spec mục 18).

    `evidence`: các check có bằng chứng ỦNG HỘ; `contradictions`: bằng chứng CHỐNG LẠI; `missing`:
    check không kiểm được. Một check có thể xuất hiện ở hai danh sách khi bằng chứng văn bản của nó
    có cả hai chiều. `rank` do hàm xếp hạng gán (hòa thì chia chung hạng); None khi chưa xếp hạng.
    """

    hypothesis_id: str
    status: VerdictStatus
    rank: int | None = None
    evidence: tuple[CheckFinding, ...] = ()
    contradictions: tuple[CheckFinding, ...] = ()
    missing: tuple[CheckFinding, ...] = ()
    failed: tuple[CheckFinding, ...] = ()
    critical_total: int = 0
    critical_passed: int = 0
    supporting_passed: int = 0

    @property
    def has_verified_text(self) -> bool:
        """Có ít nhất một đoạn văn bản đã qua kiểm trích dẫn trong phần ủng hộ."""
        return any(e.polarity == "support" for f in self.evidence for e in f.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "status": self.status.value,
            "rank": self.rank,
            "evidence": [f.to_dict() for f in self.evidence],
            "contradictions": [f.to_dict() for f in self.contradictions],
            "missing": [f.to_dict() for f in self.missing],
            "failed": [f.to_dict() for f in self.failed],
            "has_verified_text": self.has_verified_text,
            "critical_total": self.critical_total,
            "critical_passed": self.critical_passed,
            "supporting_passed": self.supporting_passed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HypothesisVerdict:
        return cls(
            hypothesis_id=d["hypothesis_id"],
            status=VerdictStatus(d["status"]),
            rank=d.get("rank"),
            evidence=tuple(CheckFinding.from_dict(f) for f in d.get("evidence", ())),
            contradictions=tuple(CheckFinding.from_dict(f) for f in d.get("contradictions", ())),
            missing=tuple(CheckFinding.from_dict(f) for f in d.get("missing", ())),
            failed=tuple(CheckFinding.from_dict(f) for f in d.get("failed", ())),
            critical_total=d.get("critical_total", 0),
            critical_passed=d.get("critical_passed", 0),
            supporting_passed=d.get("supporting_passed", 0),
        )


SPLITS = ("dev", "test")


@dataclass(frozen=True)
class CaseRecord:
    """Một ca đánh giá (spec mục 15, 18). Ca và chia dev/test do NHÓM chọn; Claude không chọn.

    `reference_set`: tập id giả thuyết đúng (kể cả `other`) SAU khi hai người gán và người thứ ba
    giải quyết bất đồng; rỗng khi chưa có nhãn cuối. `reference_sources`: nguồn độc lập với kho RAG.
    `reference_statuses`: trạng thái chuẩn cho từng cặp (ca, giả thuyết), dùng cho verification
    accuracy (spec 15.3); có thể rỗng.
    """

    case_id: str
    split: str
    anomaly_id: str
    reference_set: tuple[str, ...] = ()
    reference_sources: tuple[dict[str, str], ...] = ()
    reference_statuses: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        if self.split not in SPLITS:
            raise ValueError(f"split phải thuộc {SPLITS}, nhận {self.split!r}")

    @property
    def anomaly_type(self) -> str:
        return self.anomaly_id.split("|")[2]

    @property
    def direction(self) -> str:
        return self.anomaly_id.split("|")[3]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "anomaly_id": self.anomaly_id,
            "reference_set": list(self.reference_set),
            "reference_sources": [dict(s) for s in self.reference_sources],
            "reference_statuses": dict(self.reference_statuses),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CaseRecord:
        return cls(
            case_id=d["case_id"],
            split=d["split"],
            anomaly_id=d["anomaly_id"],
            reference_set=tuple(d.get("reference_set", ())),
            reference_sources=tuple(dict(s) for s in d.get("reference_sources", ())),
            reference_statuses=dict(d.get("reference_statuses") or {}),
            note=d.get("note", ""),
        )


RUN_STATUSES = ("ok", "error")


@dataclass(frozen=True)
class RunRecord:
    """Kết quả một lần chạy (ca × cấu hình × lần) của Phase 12 (spec mục 14, 18).

    `outputs` theo cùng khung cho mọi cấu hình: `mode` (`free` | `library`), `ranking` (danh sách
    {rank, hypothesis_id, text, status}), `report` (khẳng định + metric bản nháp, chỉ A/B/C),
    `verdicts` (cấu hình có kiểm chứng). `tokens`/`latency_s` đo thật từ nhật ký LLM. `freeze`:
    `FreezeCheck.to_dict()` khi chạy tập test (bắt buộc ghi, kể cả lý do bỏ qua freeze).
    """

    case_id: str
    split: str
    anomaly_id: str
    config: str
    run_id: str
    status: str
    outputs: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)  # input, output (token tính tiền)
    latency_s: float = 0.0  # thời gian chạy thật của cả lần chạy (giây)
    llm: dict[str, Any] = field(default_factory=dict)  # calls, cache_hits, failures, model...
    error: str = ""
    freeze: dict[str, Any] | None = None
    # băm code + config + dữ liệu + prompt lúc chạy (không gồm nhãn): resume chỉ giữ bản cùng băm
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.split not in SPLITS:
            raise ValueError(f"split phải thuộc {SPLITS}, nhận {self.split!r}")
        if self.status not in RUN_STATUSES:
            raise ValueError(f"status phải thuộc {RUN_STATUSES}, nhận {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "anomaly_id": self.anomaly_id,
            "config": self.config,
            "run_id": self.run_id,
            "status": self.status,
            "outputs": self.outputs,
            "tokens": dict(self.tokens),
            "latency_s": self.latency_s,
            "llm": dict(self.llm),
            "error": self.error,
            "freeze": self.freeze,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRecord:
        return cls(
            case_id=d["case_id"],
            split=d["split"],
            anomaly_id=d["anomaly_id"],
            config=d["config"],
            run_id=d["run_id"],
            status=d["status"],
            outputs=dict(d.get("outputs") or {}),
            tokens=dict(d.get("tokens") or {}),
            latency_s=float(d.get("latency_s") or 0.0),
            llm=dict(d.get("llm") or {}),
            error=d.get("error", ""),
            freeze=d.get("freeze"),
            fingerprint=d.get("fingerprint", ""),
        )
