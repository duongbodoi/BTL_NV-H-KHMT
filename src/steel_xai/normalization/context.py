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


@dataclass(frozen=True)
class DocContext:
    doc_id: str
    report_end: dt.date | None  # ngày kết thúc năm tài chính của chính báo cáo
    unit: str | None  # đơn vị suy từ văn bản tài liệu (None = không khẳng định được)


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
    return DocContext(doc.doc_id, report_end, unit)
