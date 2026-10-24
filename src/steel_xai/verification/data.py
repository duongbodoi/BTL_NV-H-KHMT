"""Nguồn dữ liệu cho check định lượng: kho fact (không nhìn tương lai) và chuỗi giá bên ngoài.

Hai kho này là nơi DUY NHẤT chặn rò rỉ thời gian của Phase 8: fact chỉ từ năm <= kỳ đang xét (số so
sánh của chính năm đó hoặc sau đó bị coi là chưa có); giá chỉ gồm tháng đã công bố tại as-of.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path

from steel_xai.contracts import FinancialFact, ValueStatus
from steel_xai.verification.settings import VerificationSettings

Month = tuple[int, int]  # (năm, tháng)
_DATE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
MAX_PRICE = Decimal(10) ** 12  # chặn giá trị vô lý (ví dụ 1e400) do nhập sai


def add_months(m: Month, n: int) -> Month:
    idx = m[0] * 12 + (m[1] - 1) + n
    return idx // 12, idx % 12 + 1


def month_end(m: Month) -> dt.date:
    nxt = add_months(m, 1)
    return dt.date(nxt[0], nxt[1], 1) - dt.timedelta(days=1)


def fy_year(period: str) -> int | None:
    if period.startswith("FY") and period[2:].isdigit():
        return int(period[2:])
    return None


def _is_comparative(f: FinancialFact) -> bool:
    return "comparative_only" in f.validation_flags or "comparative" in f.validation_flags


class FactStore:
    """Fact OK theo (công ty, năm, metric). Khóa trùng khác giá trị bị loại."""

    def __init__(self, facts: Iterable[FinancialFact], reference_only_from_year: int) -> None:
        groups: dict[tuple[str, int, str], list[FinancialFact]] = defaultdict(list)
        for f in facts:
            y = fy_year(f.period)
            if y is None or y >= reference_only_from_year:
                continue
            if f.status is not ValueStatus.OK or f.value is None or not f.value.is_finite():
                continue
            groups[(f.company, y, f.metric)].append(f)
        self._f: dict[tuple[str, int, str], FinancialFact] = {}
        self.conflicts: list[tuple[str, int, str]] = []
        for key, fs in groups.items():
            preferred = [f for f in fs if not _is_comparative(f)] or fs
            if len({f.value for f in preferred}) > 1:
                self.conflicts.append(key)
                continue
            self._f[key] = min(
                preferred,
                key=lambda f: (
                    f.provenance.doc_id if f.provenance else "",
                    f.provenance.page if f.provenance else 0,
                ),
            )
        self.companies = sorted({k[0] for k in self._f})

    def get(
        self, company: str, year: int, metric: str, horizon: int | None = None
    ) -> FinancialFact | None:
        """`horizon` = năm của kỳ bất thường: không có fact năm > horizon, và số so sánh của năm
        >= horizon bị coi là chưa có (nó chỉ xuất hiện trong báo cáo của năm SAU, tức sau as-of)."""
        if horizon is not None:
            if year > horizon:
                return None
        f = self._f.get((company, year, metric))
        if f is None:
            return None
        if horizon is not None and year >= horizon and _is_comparative(f):
            return None
        return f

    def is_conflict(self, company: str, year: int, metric: str) -> bool:
        return (company, year, metric) in self.conflicts

    def metrics(self, company: str, year: int) -> list[str]:
        return sorted(k[2] for k in self._f if k[0] == company and k[1] == year)

    def years(self, company: str) -> list[int]:
        return sorted({k[1] for k in self._f if k[0] == company})


class ExternalStore:
    """Chuỗi giá THÁNG từ CSV `date,value` (YYYY-MM hoặc YYYY-MM-DD). Thiếu file = chưa có chuỗi."""

    def __init__(self, series: dict[str, dict[Month, Decimal]], settings: VerificationSettings):
        self._s = series
        self.settings = settings

    @classmethod
    def load(cls, settings: VerificationSettings) -> ExternalStore:
        out: dict[str, dict[Month, Decimal]] = {}
        for name, s in settings.series.items():
            if s.path.is_file():
                out[name] = read_series_csv(s.path)
        return cls(out, settings)

    def has(self, name: str) -> bool:
        return bool(self._s.get(name))

    def observations(self, name: str, asof: dt.date) -> dict[Month, Decimal]:
        """Chỉ tháng đã công bố tại `asof` (cuối tháng + publication_lag_days <= asof)."""
        lag = dt.timedelta(days=self.settings.publication_lag_days)
        return {m: v for m, v in self._s.get(name, {}).items() if month_end(m) + lag <= asof}


def read_series_csv(path: Path) -> dict[Month, Decimal]:
    out: dict[Month, Decimal] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = csv.DictReader(fh)
        if rows.fieldnames is None or [h.strip().lower() for h in rows.fieldnames][:2] != [
            "date",
            "value",
        ]:
            raise ValueError(f"{path.name}: cần cột 'date,value'")
        names = rows.fieldnames
        for i, row in enumerate(rows, start=2):
            try:
                raw_d, raw_v = row[names[0]].strip(), row[names[1]].strip()
                if not _DATE.fullmatch(raw_d):
                    raise ValueError
                y, mth = int(raw_d[:4]), int(raw_d[5:7])
                dt.date(y, mth, int(raw_d[8:10]) if len(raw_d) == 10 else 1)
                v = Decimal(raw_v)
                if not v.is_finite() or v <= 0 or v > MAX_PRICE:
                    raise InvalidOperation
            except (ValueError, InvalidOperation, AttributeError) as e:
                raise ValueError(f"{path.name} dòng {i}: date/value không hợp lệ") from e
            if (y, mth) in out:
                raise ValueError(f"{path.name} dòng {i}: tháng {raw_d[:7]} bị lặp")
            out[(y, mth)] = v
    return out
