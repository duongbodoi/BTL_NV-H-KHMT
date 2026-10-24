"""Điều phối một bất thường: chạy check của các giả thuyết áp dụng → gộp trạng thái → xếp hạng.

Cho phép tắt từng phần để làm ablation (spec mục 14.2): không có `quant_ctx` thì check định lượng
là unknown; không có `text_fn` thì mọi check văn bản là unknown ("bỏ RAG"). Thiếu dữ liệu nào cũng
chỉ làm trạng thái đi xuống, không bao giờ lên SUPPORTED.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from steel_xai.contracts import Anomaly, Hypothesis, HypothesisVerdict
from steel_xai.verification.context import CheckContext
from steel_xai.verification.evaluate import hypothesis_applies, run_quant_checks
from steel_xai.verification.verdict import VerdictRules, rank_verdicts, verify_hypothesis

TextFn = Callable[[Hypothesis, Anomaly], Mapping[str, Any]]


@dataclass(frozen=True)
class AnomalyAnalysis:
    anomaly_id: str
    verdicts: tuple[HypothesisVerdict, ...]  # đã xếp hạng
    skipped: tuple[str, ...]  # giả thuyết không áp dụng (khác loại hoặc khác hướng)
    quant_enabled: bool
    text_enabled: bool
    used_drafts: bool = False  # True: KHÔNG dùng kết quả này để tính metric đánh giá

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "skipped": list(self.skipped),
            "quant_enabled": self.quant_enabled,
            "text_enabled": self.text_enabled,
            "used_drafts": self.used_drafts,
        }


def analyze_anomaly(
    anomaly: Anomaly,
    hypotheses: Iterable[Hypothesis],
    *,
    quant_ctx: CheckContext | None,
    text_fn: TextFn | None,
    rules: VerdictRules | None = None,
    allow_draft: bool = False,
) -> AnomalyAnalysis:
    """Chạy toàn bộ bước 3 cho một bất thường.

    Chỉ giả thuyết `reviewed` được phép (spec mục 9); `allow_draft=True` chỉ để chạy thử.
    """
    verdicts: list[HypothesisVerdict] = []
    skipped: list[str] = []
    used_drafts = False
    for h in hypotheses:
        if h.anomaly_type != anomaly.type or not hypothesis_applies(h, anomaly.direction):
            skipped.append(h.id)
            continue
        if h.status != "reviewed":
            if not allow_draft:  # chỉ giả thuyết thật sự được phân tích mới bị guard chặn
                raise ValueError(f"giả thuyết chưa reviewed không được vào pipeline: {h.id}")
            used_drafts = True
        quant = run_quant_checks(h, quant_ctx) if quant_ctx is not None else None
        text = text_fn(h, anomaly) if text_fn is not None else None
        verdicts.append(
            verify_hypothesis(
                h,
                quant,
                text,
                rules,
                quant_missing_reason="chưa chạy check định lượng (đã tắt)",
                text_missing_reason="chưa chạy check văn bản (đã tắt)",
            )
        )
    return AnomalyAnalysis(
        anomaly_id=anomaly.anomaly_id,
        verdicts=tuple(rank_verdicts(verdicts)),
        skipped=tuple(skipped),
        quant_enabled=quant_ctx is not None,
        text_enabled=text_fn is not None,
        used_drafts=used_drafts,
    )
