"""Phase 5 — Anomaly engine: tồn kho và biên lợi nhuận gộp, YoY theo năm tài chính, hai phía."""

from steel_xai.anomaly.detect import (
    CandidateRow,
    DetectionResult,
    Evaluation,
    RuleScore,
    candidate_columns,
    candidate_rows,
    detect_anomalies,
    write_candidates_csv,
)
from steel_xai.anomaly.settings import AnomalySettings, AsofEntry, load_anomaly_settings

__all__ = [
    "AnomalySettings",
    "AsofEntry",
    "CandidateRow",
    "DetectionResult",
    "Evaluation",
    "RuleScore",
    "candidate_columns",
    "candidate_rows",
    "detect_anomalies",
    "load_anomaly_settings",
    "write_candidates_csv",
]
