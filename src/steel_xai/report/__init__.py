"""Phase 11 — Báo cáo grounded: LLM chỉ viết lại kết quả đã kiểm chứng; code đối chiếu số (định
dạng Việt), liên kết khẳng định–nguồn, lọc nội dung cấm, sinh lại khi lệch. Test dùng LLM giả."""

from steel_xai.report.checks import Claim, DraftCheck, Violation, check_draft, parse_claims
from steel_xai.report.facts import (
    EvidenceItem,
    MissingItem,
    NumberFact,
    ReportInput,
    VerdictItem,
    build_report_input,
)
from steel_xai.report.generate import (
    AttemptRecord,
    ReportResult,
    generate_report,
    skeleton_report,
)
from steel_xai.report.numbers import NumberToken, display, extract_numbers, format_vn
from steel_xai.report.prompts import report_prompt_fingerprint
from steel_xai.report.render import render_report
from steel_xai.report.settings import ReportSettings, load_report_settings

__all__ = [
    "AttemptRecord",
    "Claim",
    "DraftCheck",
    "EvidenceItem",
    "MissingItem",
    "NumberFact",
    "NumberToken",
    "ReportInput",
    "ReportResult",
    "ReportSettings",
    "VerdictItem",
    "Violation",
    "build_report_input",
    "check_draft",
    "display",
    "extract_numbers",
    "format_vn",
    "generate_report",
    "load_report_settings",
    "parse_claims",
    "render_report",
    "report_prompt_fingerprint",
    "skeleton_report",
]
