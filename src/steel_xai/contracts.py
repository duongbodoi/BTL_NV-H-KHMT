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
    `asof_date`: ngày công bố BCTC của kỳ bất thường (ISO) nếu nhóm đã khai, ngược lại `None`.
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
        )
