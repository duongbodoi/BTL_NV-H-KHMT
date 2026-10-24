"""Ngữ cảnh một ca cho check định lượng: công ty, năm, hướng bất thường, as-of, dữ liệu."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from steel_xai.contracts import Anomaly, CheckInput, FinancialFact
from steel_xai.verification.data import ExternalStore, FactStore, fy_year
from steel_xai.verification.settings import VerificationSettings


class NotEvaluable(Exception):
    """Thiếu dữ liệu hoặc không tính được → check ra NOT_EVALUABLE (không phải FAIL)."""


@dataclass(frozen=True)
class CheckContext:
    company: str
    year: int
    direction: str | None  # up | down; cần cho điều kiện có dấu theo hướng bất thường
    asof: dt.date | None
    facts: FactStore
    external: ExternalStore
    settings: VerificationSettings
    # (công ty, năm) → ngày công bố, để lọc peer theo as-of. None → check peer NOT_EVALUABLE
    asof_by: Mapping[tuple[str, int], dt.date] | None = None

    def fact(self, metric: str, year: int | None = None, company: str | None = None):
        return self.facts.get(company or self.company, year or self.year, metric, self.year)

    def fiscal_end(self, year: int, company: str | None = None) -> dt.date | None:
        """Ngày kết thúc năm tài chính từ MỌI fact của năm đó; các fact lệch nhau → NotEvaluable."""
        c = company or self.company
        ends: set[dt.date] = set()
        for metric in self.facts.metrics(c, year):
            f = self.facts.get(c, year, metric, self.year)
            if f is not None and f.fiscal_period_end:
                try:
                    ends.add(dt.date.fromisoformat(f.fiscal_period_end))
                except ValueError:
                    continue
        if len(ends) > 1:
            raise NotEvaluable(
                f"ngày kết thúc năm tài chính FY{year} của {c} không nhất quán giữa các fact: "
                + ", ".join(sorted(d.isoformat() for d in ends))
            )
        return next(iter(ends), None)


class Ledger:
    """Ghi lại mọi đầu vào đã dùng, để kết quả luôn có nguồn (spec mục 10.2)."""

    def __init__(self) -> None:
        self.inputs: list[CheckInput] = []
        self._seen: set[tuple[str, str, str]] = set()

    def add(self, item: CheckInput) -> None:
        key = (item.kind, item.name, item.period)
        if key not in self._seen:
            self._seen.add(key)
            self.inputs.append(item)

    def fact_input(self, f: FinancialFact) -> None:
        self.add(
            CheckInput(
                kind="fact",
                name=f.metric,
                period=f"{f.company}:{f.period}",
                value=f.value if f.value is not None else Decimal(0),
                source=f.provenance or f"{f.source}:{','.join(f.inputs)}",
                flags=f.validation_flags,
            )
        )


def context_from_anomaly(
    anomaly: Anomaly,
    facts: FactStore,
    external: ExternalStore,
    settings: VerificationSettings,
    asof_by: Mapping[tuple[str, int], dt.date] | None = None,
) -> CheckContext:
    year = fy_year(anomaly.period)
    if year is None:
        raise ValueError(f"kỳ không phải năm tài chính: {anomaly.period!r}")
    asof = None
    if anomaly.asof_date:
        asof = dt.date.fromisoformat(anomaly.asof_date)
    return CheckContext(
        company=anomaly.company,
        year=year,
        direction=anomaly.direction,
        asof=asof,
        facts=facts,
        external=external,
        settings=settings,
        asof_by=asof_by,
    )
