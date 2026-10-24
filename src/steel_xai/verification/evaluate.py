"""Chạy check định lượng của thư viện giả thuyết: tool + điều kiện đạt (`pass_when`) → CheckResult.

`pass_when` là DỮ LIỆU: {op: > | >= | < | <=, value: số} cộng cờ tùy chọn:
  signed_by_anomaly_direction       nhân giá trị với +1 (bất thường tăng) hoặc -1 (giảm) rồi so
  signed_opposite_to_anomaly_direction   ngược lại
  abs                                so giá trị tuyệt đối
  any                                chuỗi giá nhiều thành phần: chỉ cần một thành phần đạt.
                                     Mặc định: MỌI thành phần phải đạt; thiếu thành phần mà chưa có
                                     thành phần nào trượt → NOT_EVALUABLE (không PASS nửa vời)
Thiếu dữ liệu → NOT_EVALUABLE kèm lý do; không bao giờ FAIL và không bao giờ coi là 0.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from steel_xai.contracts import Check, CheckResult, CheckStatus, Hypothesis
from steel_xai.verification.context import CheckContext, NotEvaluable
from steel_xai.verification.settings import VerificationSettings
from steel_xai.verification.tools import NUMERATOR_MODES, PERIODS, TOOLS, ToolOutput

OPS: dict[str, Callable[[Decimal, Decimal], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}
_FLAGS = frozenset(
    {"abs", "any", "signed_by_anomaly_direction", "signed_opposite_to_anomaly_direction"}
)


class UnknownToolError(KeyError):
    """Check dùng tên tool không có trong registry: lỗi cấu hình, không phải NOT_EVALUABLE."""


def _is_number(v: Any) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        return False
    return Decimal(str(v)).is_finite()


def _validate_param(kind: str, key: str, value: Any, cid: str, known: frozenset[str] | None):
    if kind == "metric":
        if not isinstance(value, str) or not value.strip():
            return [f"{cid}: tham số {key} phải là tên metric (chuỗi)"]
        if known is not None and value not in known:
            return [f"{cid}: metric {value!r} không có trong danh sách metric đã biết"]
    elif kind == "series":
        names = [value] if isinstance(value, str) else value
        if (
            not isinstance(names, list)
            or not names
            or not all(isinstance(n, str) and n.strip() for n in names)
        ):
            return [f"{cid}: tham số {key} phải là tên chuỗi hoặc danh sách tên chuỗi"]
    elif kind == "lag":
        if value != "from_config" and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            return [f"{cid}: lag phải là số nguyên >= 0 hoặc 'from_config'"]
    elif kind == "period":
        if value not in PERIODS:
            return [f"{cid}: {key} phải thuộc {PERIODS}"]
    elif kind == "mode":
        if value not in NUMERATOR_MODES:
            return [f"{cid}: {key} phải thuộc {NUMERATOR_MODES}"]
    elif kind == "true" and value is not True:
        return [f"{cid}: {key} chỉ có thể là true"]
    return []


def validate_check(check: Check, settings: VerificationSettings | None = None) -> list[str]:
    """Lỗi cấu trúc của một check quant (tool, tham số và kiểu, điều kiện). Rỗng = hợp lệ.

    Có `settings`: kiểm thêm tên metric (bắt lỗi gõ sai) và tên chuỗi giá.
    """
    cid = str(check.id)
    if check.kind != "quant":
        return [f"{cid}: không phải check quant"]
    if not isinstance(check.tool, str) or check.tool not in TOOLS:
        return [f"{cid}: tool {check.tool!r} không có trong registry"]
    spec = TOOLS[check.tool]
    errs: list[str] = []
    params = check.params
    if not isinstance(params, dict):
        return [f"{cid}: params phải là mapping"]
    for k in params:
        if k not in spec.required | spec.optional:
            errs.append(f"{cid}: tham số lạ {k!r} cho tool {spec.name}")
    for k in spec.required:
        if k not in params:
            errs.append(f"{cid}: thiếu tham số {k!r} cho tool {spec.name}")
    known = None if settings is None else settings.known_metrics
    for k, v in params.items():
        if k in spec.types:
            errs += _validate_param(spec.types[k], k, v, cid, known)
            if settings is not None and spec.types[k] == "series":
                names = [v] if isinstance(v, str) else (v if isinstance(v, list) else [])
                errs += [
                    f"{cid}: chuỗi giá {n!r} không có trong verification.external.series"
                    for n in names
                    if isinstance(n, str) and n not in settings.series
                ]
    pw = check.pass_when
    if not isinstance(pw, dict):
        return [*errs, f"{cid}: pass_when phải là mapping"]
    if not isinstance(pw.get("op"), str) or pw["op"] not in OPS:
        errs.append(f"{cid}: pass_when.op phải thuộc {sorted(OPS)}")
    if not _is_number(pw.get("value")):
        errs.append(f"{cid}: pass_when.value phải là số (không phải chuỗi/bool/NaN/vô cực)")
    for k, val in pw.items():
        if k in ("op", "value"):
            continue
        if k not in _FLAGS:
            errs.append(f"{cid}: pass_when có cờ lạ {k!r}")
        elif val is not True:
            errs.append(f"{cid}: cờ {k} chỉ có thể là true")
    signs = [k for k in pw if str(k).startswith("signed_")]
    if len(signs) > 1 or (signs and pw.get("abs")):
        errs.append(f"{cid}: không kết hợp được các cờ có dấu / abs với nhau")
    return errs


def _sign(pw: dict[str, Any], direction: str | None) -> Decimal | None:
    """Hệ số nhân theo hướng bất thường; None = cần hướng mà không có."""
    if pw.get("signed_by_anomaly_direction") or pw.get("signed_opposite_to_anomaly_direction"):
        if direction not in ("up", "down"):
            return None
        s = Decimal(1) if direction == "up" else Decimal(-1)
        return -s if pw.get("signed_opposite_to_anomaly_direction") else s
    return Decimal(1)


def evaluate_condition(
    values: dict[str, Decimal | None], pw: dict[str, Any], direction: str | None
) -> CheckStatus | None:
    """PASS/FAIL, hoặc None (→ NOT_EVALUABLE) khi không đủ thành phần để kết luận.

    Chế độ mặc định (tất cả): có thành phần trượt → FAIL (chắc chắn dù thiếu phần còn lại); mọi
    thành phần đạt nhưng còn thành phần thiếu → None. Chế độ `any`: có thành phần đạt → PASS; không
    thành phần nào đạt mà còn thành phần thiếu → None (thành phần thiếu có thể đã đạt).
    """
    sign = _sign(pw, direction)
    if sign is None:
        return None
    op, ref = OPS[pw["op"]], Decimal(str(pw["value"]))
    outcomes = []
    missing = False
    for v in values.values():
        if v is None:
            missing = True
            continue
        x = abs(v) if pw.get("abs") else v * sign
        outcomes.append(op(x, ref))
    if not outcomes:
        return None
    if pw.get("any"):
        if any(outcomes):
            return CheckStatus.PASS
        return None if missing else CheckStatus.FAIL
    if not all(outcomes):
        return CheckStatus.FAIL
    return None if missing else CheckStatus.PASS


def run_check(check: Check, ctx: CheckContext) -> CheckResult:
    """Chạy một check quant. Sai cấu hình → lỗi lớn tiếng (không phải NOT_EVALUABLE)."""
    errs = validate_check(check, ctx.settings)
    if errs:
        if check.kind == "quant" and (not isinstance(check.tool, str) or check.tool not in TOOLS):
            raise UnknownToolError(errs[0])
        raise ValueError("; ".join(errs))
    spec = TOOLS[check.tool or ""]
    pw = check.pass_when or {}

    def not_evaluable(reason: str, out: ToolOutput | None = None) -> CheckResult:
        return CheckResult(
            check.id,
            CheckStatus.NOT_EVALUABLE,
            {} if out is None else out.values,
            () if out is None else tuple(out.inputs),
            reason,
        )

    if _sign(pw, ctx.direction) is None:
        return not_evaluable("điều kiện có dấu theo hướng bất thường nhưng ca không có hướng")
    try:
        out = spec.fn(ctx, dict(check.params or {}))
    except NotEvaluable as e:
        return not_evaluable(str(e))
    status = evaluate_condition(out.values, pw, ctx.direction)
    notes = "; ".join(out.notes)
    if status is None:
        return not_evaluable(notes or "không đủ thành phần để kết luận", out)
    return CheckResult(check.id, status, out.values, tuple(out.inputs), notes)


def run_quant_checks(hypothesis: Hypothesis, ctx: CheckContext) -> dict[str, CheckResult]:
    """Chạy mọi check quant của một giả thuyết; check text bỏ qua (thuộc RAG)."""
    return {c.id: run_check(c, ctx) for c in hypothesis.checks if c.kind == "quant"}


def hypothesis_applies(
    hypothesis: Hypothesis, direction: str | None, settings: VerificationSettings
) -> bool:
    """Giả thuyết có áp dụng cho hướng này của bất thường không (`applicable_directions`).

    Thư viện hiện chỉ có nguyên nhân của tồn kho TĂNG; chạy chúng trên ca giảm sẽ biến bằng chứng
    ngược hướng thành bằng chứng ủng hộ. Loại bất thường lạ → không áp dụng.
    """
    allowed = settings.applicable_directions.get(hypothesis.anomaly_type)
    return allowed is not None and direction in allowed
