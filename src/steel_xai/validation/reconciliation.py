"""Đối chiếu ngẫu nhiên với BCTC gốc (spec mục 7.3, Phase 4).

Code chỉ bốc mẫu xác định và so sánh. Giá trị tham chiếu (số đọc từ BCTC gốc) do NGƯỜI nhập; Claude
không tự điền. Tỷ lệ lệch là số đo chất lượng OCR/trích xuất, cần đưa vào DATA_CARD.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from steel_xai.contracts import FinancialFact, ValueStatus
from steel_xai.extraction.numbers import NOTE_AMBIGUOUS_SEPARATOR, parse_number
from steel_xai.validation.settings import ValidationSettings

MATCH = "MATCH"
MISMATCH = "MISMATCH"
NOT_FOUND = "NOT_FOUND"  # không có fact OK cho khóa này
PENDING = "REFERENCE_PENDING"  # người chưa điền giá trị tham chiếu
UNPARSEABLE = "REFERENCE_UNPARSEABLE"
DUPLICATE = "REFERENCE_DUPLICATE"  # trùng khóa với một dòng tham chiếu trước đó
FACT_NOT_OK = "FACT_NOT_OK"  # có fact nhưng chưa OK (UNRESOLVED/MISSING...)

SAMPLE_COLUMNS = (
    "company",
    "period",
    "metric",
    "extracted_value",
    "doc_id",
    "page",
    "source_text",
    "reference_value",
    "reference_note",
)


@dataclass(frozen=True)
class SampleRow:
    company: str
    period: str
    metric: str
    extracted_value: str
    doc_id: str
    page: int
    source_text: str


def draw_sample(facts: Iterable[FinancialFact], n: int, seed: int) -> list[SampleRow]:
    """Bốc `n` fact OK (không phải số dẫn xuất) ngẫu nhiên nhưng tái lập được theo `seed`.

    Ít fact hơn `n` thì trả về tất cả (báo cáo đối chiếu sẽ nói rõ cỡ mẫu thiếu).
    """
    if n < 0:
        raise ValueError("n phải >= 0")
    eligible = sorted(
        (
            f
            for f in facts
            if f.status is ValueStatus.OK
            and f.source != "derived"
            and f.value is not None
            and f.provenance is not None
        ),
        key=lambda f: f.key,
    )
    picked = sorted(random.Random(seed).sample(range(len(eligible)), min(n, len(eligible))))
    rows = []
    for i in picked:
        f = eligible[i]
        assert f.provenance is not None and f.value is not None
        rows.append(
            SampleRow(
                f.company,
                f.period,
                f.metric,
                str(f.value),
                f.provenance.doc_id,
                f.provenance.page,
                f.provenance.source_text or "",
            )
        )
    return rows


def write_sample_template(rows: Sequence[SampleRow], path: str | Path) -> None:
    """CSV để người điền `reference_value` (số trên BCTC gốc, theo VND) và `reference_note`."""
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SAMPLE_COLUMNS)
        for r in rows:
            w.writerow(
                [
                    r.company,
                    r.period,
                    r.metric,
                    r.extracted_value,
                    r.doc_id,
                    r.page,
                    r.source_text,
                    "",
                    "",
                ]
            )


@dataclass(frozen=True)
class ReferenceRow:
    company: str
    period: str
    metric: str
    raw: str  # giá trị tham chiếu nguyên văn người nhập
    note: str


def read_reference_csv(path: str | Path) -> list[ReferenceRow]:
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"company", "period", "metric", "reference_value"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV tham chiếu thiếu cột: {sorted(missing)}")
        return [
            ReferenceRow(
                (r.get("company") or "").strip(),
                (r.get("period") or "").strip(),
                (r.get("metric") or "").strip(),
                (r.get("reference_value") or "").strip(),
                (r.get("reference_note") or "").strip(),
            )
            for r in reader
        ]


def magnitude_metrics() -> frozenset[str]:
    """Metric mà chuẩn hóa lưu độ lớn dương (giá vốn, capex): BCTC ghi trong ngoặc vẫn là khớp."""
    from steel_xai.normalization.settings import load_normalization_settings

    return frozenset(
        m.name for m in load_normalization_settings().metrics.values() if m.magnitude or m.flip_sign
    )


def reconciliation_report(
    facts: Iterable[FinancialFact],
    references: Sequence[ReferenceRow],
    settings: ValidationSettings,
    expected_n: int | None,
    abs_metrics: frozenset[str] | None = None,
) -> dict[str, object]:
    """So fact với giá trị tham chiếu của người (theo VND, định dạng VN: `1.234.567`, `(1.234)`).

    Số tham chiếu mơ hồ (`1,000`) hoặc phần trăm bị từ chối (`REFERENCE_UNPARSEABLE`) thay vì đoán.
    Dòng trùng khóa chỉ tính một lần. Với `abs_metrics` so theo trị tuyệt đối của tham chiếu.
    """
    abs_metrics = magnitude_metrics() if abs_metrics is None else abs_metrics
    by_key = {f.key: f for f in facts if f.source != "derived"}
    seen: set[tuple[str, str, str]] = set()
    entries: list[dict[str, object]] = []
    for ref in references:
        key = (ref.company, ref.period, ref.metric)
        entry: dict[str, object] = {
            "company": ref.company,
            "period": ref.period,
            "metric": ref.metric,
            "reference_raw": ref.raw,
            "note": ref.note,
            "extracted": None,
            "reference": None,
            "deviation_ratio": None,
        }
        fact = by_key.get(key)
        if fact is not None and fact.value is not None:
            entry["extracted"] = str(fact.value)
        parsed = parse_number(ref.raw) if ref.raw else None
        if key in seen:
            entry["status"] = DUPLICATE
        elif parsed is None:
            entry["status"] = PENDING
        elif (
            parsed.status is not ValueStatus.OK
            or parsed.value is None
            or parsed.is_percent
            or parsed.reason == NOTE_AMBIGUOUS_SEPARATOR
        ):
            entry["status"] = UNPARSEABLE
        else:
            ref_value = abs(parsed.value) if ref.metric in abs_metrics else parsed.value
            entry["reference"] = str(ref_value)
            if fact is None:
                entry["status"] = NOT_FOUND
            elif fact.status is not ValueStatus.OK or fact.value is None:
                entry["status"] = FACT_NOT_OK
            else:
                dev = _deviation(fact.value, ref_value)
                entry["deviation_ratio"] = str(dev)
                entry["status"] = MATCH if dev <= settings.match_tolerance else MISMATCH
        seen.add(key)
        entries.append(entry)

    statuses = (MATCH, MISMATCH, NOT_FOUND, FACT_NOT_OK, PENDING, UNPARSEABLE, DUPLICATE)
    counts = {k: sum(1 for e in entries if e["status"] == k) for k in statuses}
    compared = counts[MATCH] + counts[MISMATCH]
    return {
        "expected_n": expected_n,
        "n_rows": len(entries),
        "counts": counts,
        "n_compared": compared,
        "mismatch_rate": None if compared == 0 else str(Decimal(counts[MISMATCH]) / compared),
        "sample_size_ok": expected_n is None or compared >= expected_n,
        "match_tolerance": str(settings.match_tolerance),
        "entries": entries,
    }


def _deviation(extracted: Decimal, reference: Decimal) -> Decimal:
    if extracted == reference:
        return Decimal(0)
    base = abs(reference) if reference != 0 else abs(extracted)
    return abs(extracted - reference) / base


def render_markdown(report: dict[str, object]) -> str:
    counts = report["counts"]
    assert isinstance(counts, dict)
    lines = [
        "# Báo cáo đối chiếu với BCTC gốc",
        "",
        f"- Cỡ mẫu yêu cầu: {report['expected_n']}; số dòng: {report['n_rows']}; "
        f"đã so sánh: {report['n_compared']}"
        + ("" if report["sample_size_ok"] else " (**chưa đủ cỡ mẫu**)"),
        f"- Khớp: {counts[MATCH]}; lệch: {counts[MISMATCH]}; không tìm thấy fact: "
        f"{counts[NOT_FOUND]}; fact chưa OK: {counts[FACT_NOT_OK]}; "
        f"chưa điền tham chiếu: {counts[PENDING]}; tham chiếu không đọc được: "
        f"{counts[UNPARSEABLE]}; trùng khóa: {counts[DUPLICATE]}",
        "- Tỷ lệ lệch (chỉ trên các dòng đã so sánh): "
        + ("chưa có" if report["mismatch_rate"] is None else str(report["mismatch_rate"])),
        "",
        "| Công ty | Kỳ | Metric | Trích xuất | Tham chiếu | Lệch tương đối | Kết quả |",
        "|---|---|---|---|---|---|---|",
    ]
    entries = report["entries"]
    assert isinstance(entries, list)
    for e in entries:
        lines.append(
            f"| {e['company']} | {e['period']} | {e['metric']} | {e['extracted']} | "
            f"{e['reference']} | {e['deviation_ratio']} | {e['status']} |"
        )
    lines += ["", "Khảo sát khả thi trên mẫu nhỏ, không phải kết luận thống kê."]
    return "\n".join(lines) + "\n"
