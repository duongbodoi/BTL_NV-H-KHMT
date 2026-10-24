"""Ngữ cảnh cấp tài liệu: ngày kết thúc năm tài chính của báo cáo và đơn vị trình bày.

Ngày kết thúc năm tài chính = ngày cuối tháng xuất hiện nhiều nhất trong văn bản ("ngày D tháng
M năm Y") và phải nhiều hơn rõ rệt ngày xếp sau (hòa → không đoán, ví dụ ngày ký báo cáo).
Ổn định hơn việc tìm cụm "kết thúc ngày", vốn hay đi kèm năm trước.

Header tương đối ("Năm nay", "Số đầu năm") và bảng không ghi đơn vị chỉ giải được bằng chính văn
bản của tài liệu. Không đọc được thì để `None`; mapping sẽ loại hoặc để UNRESOLVED, không đoán.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from steel_xai.extraction.numbers import normalize_space
from steel_xai.normalization.settings import NormalizationSettings
from steel_xai.parsing import Document

_DATE_TEXT_RE = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.IGNORECASE)
_DATE_SLASH_RE = re.compile(r"ngày\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)

# Ngày ký báo cáo kiểm toán: dòng chữ ký "Hà Nội, ngày 16 tháng 3 năm 2016" hoặc dòng "Ngày ..."
# đứng riêng (kiểu cũ, sau địa danh). Chỉ nhận dòng chứa đúng ngày đó, không nhận ngày trong câu.
_SIGNATURE_RE = re.compile(
    r"^\s*(?:[^\W\d_][^\n,]{1,40},\s*)?ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_AUDIT_HEADING_RE = re.compile(r"^\s*báo cáo kiểm toán độc lập\s*$", re.IGNORECASE | re.MULTILINE)
_MAX_DAYS_AFTER_YEAR_END = 400  # ngày ký quá xa ngày khóa sổ thì không tin


@dataclass(frozen=True)
class DocContext:
    doc_id: str
    report_end: dt.date | None  # ngày kết thúc năm tài chính của chính báo cáo
    unit: str | None  # đơn vị suy từ văn bản tài liệu (None = không khẳng định được)
    audit_report_date: dt.date | None = None  # ngày ký báo cáo kiểm toán (mốc as-of tạm)
    audit_evidence: str = ""  # dòng nguyên văn đã dùng, để người tra


def build_context(doc: Document, settings: NormalizationSettings) -> DocContext:
    texts = [unicodedata.normalize("NFC", b.text) for b in doc.iter_blocks("text")]
    ends: Counter[dt.date] = Counter()
    for t in texts:
        for rx in (_DATE_TEXT_RE, _DATE_SLASH_RE):
            for m in rx.finditer(t):
                day, month, year = (int(g) for g in m.groups())
                try:
                    d = dt.date(year, month, day)
                except ValueError:
                    continue
                if day == calendar.monthrange(year, month)[1]:
                    ends[d] += 1  # ngày khóa sổ là ngày cuối tháng
    report_end = None
    if ends:
        ranked = ends.most_common(2)
        # Phải có ngày chiếm ưu thế rõ ràng; hòa (ví dụ ngày ký báo cáo ngang ngày khóa sổ) → None.
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            report_end = ranked[0][0]

    blob = normalize_space(" ".join(texts)).casefold()
    du = settings.document_unit
    unit = None
    if any(s in blob for s in du.statements) and not any(s in blob for s in du.scaled_statements):
        unit = du.unit
    audit_date, evidence = audit_report_date(doc, report_end)
    return DocContext(doc.doc_id, report_end, unit, audit_date, evidence)


def audit_report_date(doc: Document, report_end: dt.date | None) -> tuple[dt.date | None, str]:
    """Ngày ký báo cáo kiểm toán độc lập, trích từ văn bản trước bảng cân đối kế toán.

    Quy tắc: lấy các dòng chữ ký trước bảng báo cáo tài chính đầu tiên (bảng có cột "Mã số"); nếu
    có tiêu đề "BÁO CÁO KIỂM TOÁN ĐỘC LẬP" thì chỉ xét các dòng sau tiêu đề đó; lấy dòng cuối cùng.
    Phải nằm trong khoảng (ngày khóa sổ, ngày khóa sổ + 400 ngày]; ngược lại hoặc không có → `None`.
    Không đoán: người tra tay phần không trích được.
    """
    if report_end is None:
        return None, ""
    candidates: list[tuple[dt.date, str]] = []
    heading_at = -1
    for page in doc.pages:
        stop = False
        for block in page.blocks:
            text = unicodedata.normalize("NFC", block.text)
            if block.kind == "table":
                if re.search(r"mã số", text, re.IGNORECASE):
                    stop = True
                    break
                continue
            if heading_at < 0 and _AUDIT_HEADING_RE.search(text):
                heading_at = len(candidates)
            for m in _SIGNATURE_RE.finditer(text):
                day, month, year = (int(g) for g in m.groups())
                try:
                    candidates.append((dt.date(year, month, day), m.group(0).strip()))
                except ValueError:
                    continue
        if stop:
            break
    if heading_at >= 0:
        candidates = candidates[heading_at:]
    if not candidates:
        return None, ""
    day, evidence = candidates[-1]
    if not 1 <= (day - report_end).days <= _MAX_DAYS_AFTER_YEAR_END:
        return None, evidence
    return day, evidence
