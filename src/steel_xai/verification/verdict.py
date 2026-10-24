"""Gộp kết quả check → trạng thái giả thuyết và xếp hạng (spec mục 12). Hàm thuần, không LLM.

Quy về cùng một thang (`CheckFinding.outcome`, theo ngữ nghĩa của từng check):
- quant: PASS → satisfied, FAIL → not_satisfied, NOT_EVALUABLE → unknown.
- text:  FOUND → satisfied; UNVERIFIED / MISSING → unknown. Văn bản KHÔNG BAO GIỜ thành
  not_satisfied: không tìm thấy đoạn nào không chứng minh được điều đó không có.
Check polarity `support`: satisfied = có bằng chứng ủng hộ. Polarity `contradict`: satisfied =
quan sát được bằng chứng chống lại (not_satisfied = đã kiểm mà không thấy).

Luật trạng thái (mục 12.1), theo thứ tự ưu tiên:
1. CONTRADICTED nếu có bằng chứng rõ chống lại: một check `contradict` satisfied; hoặc một đoạn
   văn bản đã qua kiểm trích dẫn có nhãn `contradict`; hoặc (theo `critical_support_fail_policy`)
   một check `support` + `critical` bị not_satisfied (dữ liệu đã tính ra, kỳ vọng quan trọng không
   xảy ra). Nếu cùng lúc có bằng chứng ủng hộ, cả hai được giữ trong kết quả.
2. SUPPORTED nếu có ít nhất một check critical-support và MỌI check critical-support đều
   satisfied, và không có check critical-contradict định lượng nào chưa kiểm được (`VerdictRules`).
3. PARTIAL nếu chưa SUPPORTED nhưng có bằng chứng ủng hộ (satisfied bất kỳ mức quan trọng nào).
4. UNVERIFIED còn lại: không đủ dữ liệu để nói gì. Thiếu dữ liệu không bao giờ dẫn tới SUPPORTED.

Xếp hạng (mục 12.2): SUPPORTED > PARTIAL > UNVERIFIED > CONTRADICTED; cùng trạng thái: tỷ lệ check
critical-support đạt (giảm dần; SUPPORTED luôn có tỷ lệ 1), rồi số check supporting đạt;
hòa thì chia chung hạng (hạng cạnh tranh: 1, 1, 3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

from steel_xai.config import ConfigError, load_config
from steel_xai.contracts import (
    Check,
    CheckFinding,
    CheckResult,
    CheckStatus,
    Hypothesis,
    HypothesisVerdict,
    TextCheckStatus,
    VerdictStatus,
)

FAIL_POLICIES = ("never", "if_none_pass", "always")
STATUS_ORDER = {
    VerdictStatus.SUPPORTED: 0,
    VerdictStatus.PARTIAL: 1,
    VerdictStatus.UNVERIFIED: 2,
    VerdictStatus.CONTRADICTED: 3,
}


@dataclass(frozen=True)
class VerdictRules:
    """Các lựa chọn của luật mà spec để ngỏ (configs/verification.yaml: verdict)."""

    # Check văn bản critical + contradict mà không thấy bằng chứng: có chặn SUPPORTED không?
    # False (mặc định): không (văn bản không chứng minh được sự vắng mặt, nếu chặn thì loại check
    # này không bao giờ SUPPORTED được); chỗ trống vẫn nằm trong `missing`.
    text_critical_contradict_unknown_blocks: bool = False
    # never | if_none_pass | always (xem configs/verification.yaml)
    critical_support_fail_policy: str = "if_none_pass"


def load_verdict_rules(cfg: dict[str, Any] | None = None) -> VerdictRules:
    cfg = load_config("verification") if cfg is None else cfg
    v = cfg.get("verdict")
    if not isinstance(v, dict):
        raise ConfigError("verification.verdict phải là mapping")
    flag = v.get("text_critical_contradict_unknown_blocks")
    if not isinstance(flag, bool):
        raise ConfigError(
            "verification.verdict.text_critical_contradict_unknown_blocks phải là bool"
        )
    policy = v.get("critical_support_fail_policy")
    if policy not in FAIL_POLICIES:
        raise ConfigError(
            f"verification.verdict.critical_support_fail_policy phải thuộc {FAIL_POLICIES}"
        )
    return VerdictRules(
        text_critical_contradict_unknown_blocks=flag, critical_support_fail_policy=policy
    )


# ---------------- quy kết quả check về cùng một thang


def _fmt(v: Any) -> str:
    return "n/a" if v is None else format(v, "f") if hasattr(v, "is_finite") else str(v)


def finding_from_quant(check: Check, result: CheckResult) -> CheckFinding:
    outcome = {
        CheckStatus.PASS: "satisfied",
        CheckStatus.FAIL: "not_satisfied",
        CheckStatus.NOT_EVALUABLE: "unknown",
    }[result.status]
    values = ", ".join(f"{k}={_fmt(v)}" for k, v in result.values.items())
    detail = f"{check.tool}: {values}" if values else f"{check.tool}"
    if result.reason:
        detail += f" ({result.reason})"
    return CheckFinding(
        check.id, "quant", check.importance, check.polarity, outcome, detail, quant=result
    )


def finding_from_text(check: Check, outcome: Any) -> CheckFinding:
    """`outcome`: TextCheckOutcome (không import để verification không phụ thuộc llm)."""
    if outcome.check_id != check.id or any(e.check_id != check.id for e in outcome.evidence):
        raise ValueError(
            f"kết quả văn bản của check {outcome.check_id!r} gán nhầm cho {check.id!r}"
        )
    backed = any(e.polarity == check.polarity for e in outcome.evidence)
    found = outcome.status is TextCheckStatus.FOUND and backed
    detail = outcome.status.value
    if outcome.status is TextCheckStatus.FOUND and not backed:
        detail += "; thiếu bằng chứng đã kiểm đúng chiều của check, coi là chưa kiểm được"
    if outcome.dropped:
        detail += f"; {len(outcome.dropped)} trích dẫn bị loại"
    if outcome.reason:
        detail += f" ({outcome.reason})"
    return CheckFinding(
        check.id,
        "text",
        check.importance,
        check.polarity,
        "satisfied" if found else "unknown",
        detail,
        text=tuple(outcome.evidence),
    )


def unrun_finding(check: Check, why: str) -> CheckFinding:
    return CheckFinding(check.id, check.kind, check.importance, check.polarity, "unknown", why)


def build_findings(
    hypothesis: Hypothesis,
    quant: Mapping[str, CheckResult] | None = None,
    text: Mapping[str, Any] | None = None,
    *,
    quant_missing_reason: str = "chưa chạy check định lượng",
    text_missing_reason: str = "chưa chạy check văn bản",
) -> list[CheckFinding]:
    """Một finding cho MỖI check của thư viện (LLM/bên ngoài không thêm hay bớt check).

    Check không có kết quả → unknown. Kết quả cho check không tồn tại, hoặc sai loại (kết quả định
    lượng cho check văn bản...) là lỗi lớn tiếng.
    """
    quant, text = quant or {}, text or {}
    by_id = {c.id: c for c in hypothesis.checks}
    for k in quant:
        if k not in by_id or by_id[k].kind != "quant":
            raise ValueError(f"kết quả định lượng cho check không tồn tại/không phải quant: {k!r}")
    for k in text:
        if k not in by_id or by_id[k].kind != "text":
            raise ValueError(f"kết quả văn bản cho check không tồn tại/không phải text: {k!r}")
    out = []
    for c in hypothesis.checks:
        if c.kind == "quant":
            if c.id in quant:
                out.append(finding_from_quant(c, quant[c.id]))
            else:
                out.append(unrun_finding(c, quant_missing_reason))
        elif c.id in text:
            out.append(finding_from_text(c, text[c.id]))
        else:
            out.append(unrun_finding(c, text_missing_reason))
    return out


# ---------------- luật trạng thái


def _text_polarities(f: CheckFinding) -> set[str]:
    return {e.polarity for e in f.text}


def is_contradiction(f: CheckFinding) -> bool:
    if f.polarity == "contradict" and f.outcome == "satisfied":
        return True
    return "contradict" in _text_polarities(f)


def is_support(f: CheckFinding) -> bool:
    if f.polarity == "support" and f.outcome == "satisfied":
        return True
    return "support" in _text_polarities(f)


def decide(
    hypothesis_id: str, findings: Iterable[CheckFinding], rules: VerdictRules | None = None
) -> HypothesisVerdict:
    rules = VerdictRules() if rules is None else rules
    fs = list(findings)
    evidence = tuple(f for f in fs if is_support(f))
    crit = [f for f in fs if f.importance == "critical"]
    crit_support = [f for f in crit if f.polarity == "support"]
    crit_passed = sum(f.outcome == "satisfied" for f in crit_support)
    failed = tuple(f for f in fs if f.polarity == "support" and f.outcome == "not_satisfied")
    failed_critical = [f for f in failed if f.importance == "critical"]
    policy = rules.critical_support_fail_policy
    fail_contradicts = bool(failed_critical) and (
        policy == "always" or (policy == "if_none_pass" and crit_passed == 0)
    )
    contradictions = tuple(
        f for f in fs if is_contradiction(f) or (fail_contradicts and f in failed_critical)
    )
    missing = tuple(
        sorted((f for f in fs if f.outcome == "unknown"), key=lambda f: f.importance != "critical")
    )

    supporting_passed = sum(
        f.importance == "supporting" and f.polarity == "support" and f.outcome == "satisfied"
        for f in fs
    )
    blocking_unknown = any(
        f.polarity == "contradict"
        and f.outcome == "unknown"
        and (f.kind == "quant" or rules.text_critical_contradict_unknown_blocks)
        for f in crit
    )

    if contradictions:
        status = VerdictStatus.CONTRADICTED
    elif (
        crit_support
        and all(f.outcome == "satisfied" for f in crit_support)
        and not blocking_unknown
    ):
        status = VerdictStatus.SUPPORTED
    elif evidence:
        status = VerdictStatus.PARTIAL
    else:
        status = VerdictStatus.UNVERIFIED
    return HypothesisVerdict(
        hypothesis_id=hypothesis_id,
        status=status,
        evidence=evidence,
        contradictions=contradictions,
        missing=missing,
        failed=failed,
        critical_total=len(crit_support),
        critical_passed=crit_passed,
        supporting_passed=supporting_passed,
    )


def verify_hypothesis(
    hypothesis: Hypothesis,
    quant: Mapping[str, CheckResult] | None = None,
    text: Mapping[str, Any] | None = None,
    rules: VerdictRules | None = None,
    **kw: str,
) -> HypothesisVerdict:
    return decide(hypothesis.id, build_findings(hypothesis, quant, text, **kw), rules)


# ---------------- xếp hạng


def _score(v: HypothesisVerdict) -> tuple[int, Fraction, int]:
    ratio = Fraction(v.critical_passed, v.critical_total) if v.critical_total else Fraction(0)
    return (STATUS_ORDER[v.status], -ratio, -v.supporting_passed)


def rank_verdicts(verdicts: Iterable[HypothesisVerdict]) -> list[HypothesisVerdict]:
    """Sắp xếp theo mục 12.2 và gán `rank`. Hòa (cùng trạng thái, cùng tỷ lệ critical, cùng số
    supporting) chia chung hạng; hạng sau hạng hòa bị nhảy (1, 1, 3). Thứ tự hiển thị trong nhóm hòa
    theo id để đầu ra xác định, không ảnh hưởng hạng."""
    vs = list(verdicts)
    ids = [v.hypothesis_id for v in vs]
    if len(set(ids)) != len(ids):
        raise ValueError("hypothesis_id trùng khi xếp hạng")
    ordered = sorted(vs, key=lambda v: (*_score(v), v.hypothesis_id))
    out: list[HypothesisVerdict] = []
    for i, v in enumerate(ordered):
        rank = out[-1].rank if out and _score(ordered[i - 1]) == _score(v) else i + 1
        out.append(replace(v, rank=rank))
    return out
