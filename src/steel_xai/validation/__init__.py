"""Phase 4 — Validation: quan hệ kế toán, dấu, năm tài chính, đối chiếu mẫu với BCTC gốc."""

from steel_xai.validation.checks import (
    FAIL,
    NOT_EVALUABLE,
    PASS,
    CheckResult,
    ValidationResult,
    validate_facts,
)
from steel_xai.validation.reconciliation import (
    ReferenceRow,
    SampleRow,
    draw_sample,
    magnitude_metrics,
    read_reference_csv,
    reconciliation_report,
    render_markdown,
    write_sample_template,
)
from steel_xai.validation.settings import ValidationSettings, load_validation_settings

__all__ = [
    "FAIL",
    "NOT_EVALUABLE",
    "PASS",
    "CheckResult",
    "ReferenceRow",
    "SampleRow",
    "ValidationResult",
    "ValidationSettings",
    "draw_sample",
    "load_validation_settings",
    "magnitude_metrics",
    "read_reference_csv",
    "reconciliation_report",
    "render_markdown",
    "validate_facts",
    "write_sample_template",
]
