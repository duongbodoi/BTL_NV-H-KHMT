"""Sinh báo cáo grounded (spec mục 13, Phase 11): LLM viết → code kiểm → lệch thì sinh lại.

Số vi phạm của bản nháp ĐẦU TIÊN được giữ làm metric (numeric mismatch, unsupported claim...).
Hết số lần mà vẫn lệch: `skeleton_only` chỉ giữ phần code dựng; `drop_failing` giữ khẳng định qua
mọi kiểm. Khẳng định không có nguồn luôn bị loại khỏi bản cuối. Lỗi gọi LLM không bao giờ làm mất
phần code dựng (trạng thái, phần thiếu, nguồn).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from steel_xai.llm.client import LLMClient, LLMError, LLMRequest
from steel_xai.report.checks import Claim, DraftCheck, check_draft, parse_claims
from steel_xai.report.facts import ReportInput
from steel_xai.report.prompts import REPORT_SCHEMA, REPORT_SYSTEM, report_user
from steel_xai.report.render import render_report
from steel_xai.report.settings import ReportSettings

PURPOSE = "grounded_report"
MAX_FEEDBACK = 30
STATUSES = ("accepted", "accepted_after_retry", "rejected", "llm_error")


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    check: DraftCheck | None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "error": self.error,
            "check": None if self.check is None else self.check.summary(),
            "violations": []
            if self.check is None
            else [v.to_dict() for v in self.check.violations],
            "claims": [] if self.check is None else [c.to_dict() for c in self.check.claims],
        }


@dataclass(frozen=True)
class ReportResult:
    anomaly_id: str
    status: str
    markdown: str
    claims: tuple[Claim, ...]  # khẳng định của LLM có trong bản cuối
    attempts: tuple[AttemptRecord, ...]
    final_check: DraftCheck | None
    used_drafts: bool

    @property
    def first_draft(self) -> DraftCheck | None:
        return self.attempts[0].check if self.attempts else None

    def metrics(self) -> dict[str, Any]:
        """Số liệu cho đánh giá (spec mục 15.3): đếm trên bản nháp đầu và trên bản cuối."""
        first, final = self.first_draft, self.final_check
        return {
            "status": self.status,
            "attempts": len(self.attempts),
            "first_draft_claims": None if first is None else len(first.claims),
            "first_draft_numbers_checked": None if first is None else first.numbers_checked,
            "first_draft_numeric_mismatches": None if first is None else first.numeric_mismatches,
            "first_draft_unsupported_claims": None if first is None else first.unsupported_claims,
            "first_draft_violations": None if first is None else first.counts,
            "final_claims": len(self.claims),
            "final_numeric_mismatches": 0 if final is None else final.numeric_mismatches,
            "final_violations": {} if final is None else final.counts,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "status": self.status,
            "used_drafts": self.used_drafts,
            "metrics": self.metrics(),
            "claims": [c.to_dict() for c in self.claims],
            "attempts": [a.to_dict() for a in self.attempts],
            "markdown": self.markdown,
        }


def skeleton_report(inp: ReportInput, settings: ReportSettings, note: str | None = None) -> str:
    """Báo cáo chỉ gồm phần code dựng (không gọi LLM)."""
    return render_report(inp, (), settings, note)


def generate_report(
    inp: ReportInput, client: LLMClient, settings: ReportSettings, run_id: str
) -> ReportResult:
    attempts: list[AttemptRecord] = []
    feedback: list[str] | None = None
    payload = inp.to_prompt()
    accepted: DraftCheck | None = None
    error = False
    for n in range(1, settings.max_attempts + 1):
        req = LLMRequest(
            purpose=PURPOSE,
            run_id=run_id,
            system=REPORT_SYSTEM,
            user=report_user(payload, feedback, n),
            schema=REPORT_SCHEMA,
        )
        try:
            data = client.call(req).data
        except LLMError as e:
            attempts.append(AttemptRecord(n, None, f"{type(e).__name__}: {e}"))
            error = True
            break
        chk = check_draft(parse_claims(data), inp, settings)
        attempts.append(AttemptRecord(n, chk))
        if chk.accepted:
            accepted = chk
            break
        feedback = [v.feedback() for v in chk.blocking][:MAX_FEEDBACK]

    last = next((a.check for a in reversed(attempts) if a.check is not None), None)
    note: str | None = None
    if accepted is not None:
        status = "accepted" if len(attempts) == 1 else "accepted_after_retry"
        claims = accepted.passing_claims()
    else:
        status = "llm_error" if error else "rejected"
        if settings.on_exhausted == "drop_failing" and last is not None:
            claims = last.passing_claims()[: settings.max_claims]
            note = (
                f"Bản nháp của LLM không qua bộ kiểm sau {len(attempts)} lần; chỉ giữ các "
                "khẳng định đã qua mọi kiểm."
            )
        else:
            claims = ()
            note = (
                f"Phần diễn giải của LLM không được chấp nhận ({status}, {len(attempts)} lần); "
                "báo cáo chỉ gồm phần do code dựng."
            )
    final = check_draft(claims, inp, settings) if claims else None
    if final is not None and any(
        v.claim_index is not None and v.blocking for v in final.violations
    ):
        raise AssertionError("khẳng định lỗi lọt vào bản cuối")  # bất biến: không bao giờ xảy ra
    return ReportResult(
        anomaly_id=inp.anomaly.anomaly_id,
        status=status,
        markdown=render_report(inp, claims, settings, note),
        claims=claims,
        attempts=tuple(attempts),
        final_check=final,
        used_drafts=inp.used_drafts,
    )
