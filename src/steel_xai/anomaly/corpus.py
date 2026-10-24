"""Nạp bộ OCR (ViFinQA), chạy Phase 2-4 rồi Phase 5 và xuất bảng ứng viên cho nhóm chọn ca.

Bố cục thư mục: `<ocr_dir>/<mã>/<năm>/<mã>_financial_statements_<năm>_<loại>/*_extracted.txt`, với
`<loại>` là `consolidated` hoặc `separate`. Chỉ nạp báo cáo hợp nhất vì khóa fact chưa
phân biệt hai loại.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from steel_xai.anomaly.detect import DetectionResult, detect_anomalies, write_candidates_csv
from steel_xai.anomaly.settings import AnomalySettings, AsofEntry, load_anomaly_settings
from steel_xai.config import load_config
from steel_xai.extraction import TableExtraction, extract_document
from steel_xai.normalization import (
    DocContext,
    NormalizationSettings,
    build_context,
    load_normalization_settings,
    normalize,
)
from steel_xai.parsing import parse_file
from steel_xai.validation import load_validation_settings, validate_facts

ASOF_SOURCE_AUDIT = "audit_report_date"
STATUS_OK = "ok"
STATUS_UNRESOLVED = "unresolved"
STATUS_CONFLICT = "conflict"
ASOF_COLUMNS = (
    "company",
    "period",
    "asof_date",
    "asof_source",
    "status",
    "evidence",
    "doc_id",
    "note",
)


@dataclass(frozen=True)
class DocRef:
    company: str
    doc_id: str
    path: Path


@dataclass(frozen=True)
class Corpus:
    docs: tuple[tuple[DocRef, DocContext], ...]
    tables: tuple[TableExtraction, ...]


def find_documents(
    ocr_dir: str | Path, companies: Iterable[str], kind: str = "consolidated"
) -> list[DocRef]:
    """Các file OCR của `companies` thuộc loại `kind`, theo thứ tự xác định."""
    root = Path(ocr_dir)
    out = []
    for company in sorted(set(companies)):
        for path in sorted((root / company).glob(f"*/*_{kind}/*_extracted.txt")):
            out.append(DocRef(company, path.parent.name, path))
    return out


def load_corpus(docs: Sequence[DocRef], settings: NormalizationSettings | None = None) -> Corpus:
    ns = load_normalization_settings() if settings is None else settings
    loaded, tables = [], []
    for ref in docs:
        doc = parse_file(ref.path, ref.doc_id)
        loaded.append((ref, build_context(doc, ns)))
        tables += extract_document(doc, company=ref.company)
    return Corpus(tuple(loaded), tuple(tables))


@dataclass(frozen=True)
class AsofRow:
    company: str
    period: str
    asof_date: str
    asof_source: str
    status: str
    evidence: str
    doc_id: str
    note: str


def asof_from_corpus(
    corpus: Corpus, reference_only_from_year: int
) -> tuple[dict[tuple[str, str], AsofEntry], list[AsofRow]]:
    """Ngày ký báo cáo kiểm toán của từng (công ty, năm tài chính) làm mốc as-of TẠM.

    Không trích được, hoặc hai tài liệu cùng năm ra hai ngày khác nhau → không dùng; ghi vào danh
    sách để người tra tay. Năm >= mốc tham chiếu bị bỏ qua (không vào candidates).
    """
    found: dict[tuple[str, str], list[AsofRow]] = {}
    rows: list[AsofRow] = []
    for ref, ctx in corpus.docs:
        if ctx.report_end is None:
            rows.append(
                AsofRow(ref.company, "", "", "", STATUS_UNRESOLVED, "", ref.doc_id,
                        "không đọc được ngày kết thúc năm tài chính của tài liệu")
            )  # fmt: skip
            continue
        period = f"FY{ctx.report_end.year}"
        if ctx.report_end.year >= reference_only_from_year:
            continue
        if ctx.audit_report_date is None:
            note = "không trích được ngày ký báo cáo kiểm toán"
            if ctx.audit_evidence:
                note += " (dòng tìm thấy nằm ngoài khoảng hợp lệ)"
            rows.append(
                AsofRow(ref.company, period, "", "", STATUS_UNRESOLVED, ctx.audit_evidence,
                        ref.doc_id, note)
            )  # fmt: skip
            continue
        row = AsofRow(
            ref.company,
            period,
            ctx.audit_report_date.isoformat(),
            ASOF_SOURCE_AUDIT,
            STATUS_OK,
            ctx.audit_evidence,
            ref.doc_id,
            "",
        )
        found.setdefault((ref.company, period), []).append(row)

    asof: dict[tuple[str, str], AsofEntry] = {}
    for key, group in sorted(found.items()):
        if len({r.asof_date for r in group}) == 1:
            asof[key] = AsofEntry(group[0].asof_date, ASOF_SOURCE_AUDIT)
            rows.extend(group)
        else:
            for r in group:
                rows.append(
                    AsofRow(r.company, r.period, "", "", STATUS_CONFLICT, r.evidence, r.doc_id,
                            f"hai tài liệu cho hai ngày khác nhau (một trong số: {r.asof_date})")
                )  # fmt: skip
    rows.sort(key=lambda r: (r.company, r.period, r.doc_id))
    return asof, rows


def write_asof_csv(rows: Sequence[AsofRow], path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(ASOF_COLUMNS)
        for r in rows:
            w.writerow(
                [
                    r.company,
                    r.period,
                    r.asof_date,
                    r.asof_source,
                    r.status,
                    r.evidence,
                    r.doc_id,
                    r.note,
                ]
            )


@dataclass(frozen=True)
class BuildOutput:
    result: DetectionResult
    asof_rows: tuple[AsofRow, ...]
    candidates_path: Path
    asof_path: Path


def build_candidates(
    ocr_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    settings: AnomalySettings | None = None,
) -> BuildOutput:
    """Toàn bộ chuỗi: OCR → trích → chuẩn hóa → validation → anomaly → hai file CSV."""
    dataset = load_config("dataset")
    s = load_anomaly_settings() if settings is None else settings
    ocr = Path(ocr_dir or dataset["sources"]["ocr_dir"])
    out = Path(out_dir or dataset["paths"]["processed"])
    out.mkdir(parents=True, exist_ok=True)

    ns = load_normalization_settings()
    corpus = load_corpus(find_documents(ocr, s.companies), ns)
    normalized = normalize(corpus.tables, settings=ns, contexts=[c for _, c in corpus.docs])
    validated = validate_facts(normalized.facts, load_validation_settings())
    asof, asof_rows = asof_from_corpus(corpus, s.reference_only_from_year)
    result = detect_anomalies(validated.facts, s, asof_extracted=asof)

    candidates_path, asof_path = out / "anomaly_candidates.csv", out / "asof_dates.csv"
    write_candidates_csv(result, candidates_path)
    write_asof_csv(asof_rows, asof_path)
    return BuildOutput(result, tuple(asof_rows), candidates_path, asof_path)
