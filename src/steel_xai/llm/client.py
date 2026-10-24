"""Lớp bọc LLM mỏng (spec mục 16): structured output bắt buộc, cache đĩa, log token/độ trễ.

Không agent framework. Mọi test dùng `Transport` giả; chỉ `AnthropicTransport`/`GeminiTransport` mới
gọi mạng (chọn theo `llm.provider` qua `make_transport`).
Khóa cache gồm `run_id` (3 lần chạy không trùng cache) và cả prompt, schema, model, effort.
Không gửi temperature/top_p/top_k (API từ chối).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from steel_xai.config import ConfigError, get_api_key
from steel_xai.llm.schema import check_schema, validate
from steel_xai.llm.settings import LLMSettings

CACHE_VERSION = 1


class LLMError(Exception):
    """Gọi LLM thất bại (mạng, từ chối, cắt cụt, đầu ra sai schema)."""


class LLMRefusalError(LLMError):
    """Mô hình từ chối trả lời (stop_reason == refusal). Không gọi lại."""


class LLMOutputError(LLMError):
    """Đầu ra không phải JSON hợp lệ theo schema sau mọi lần thử, hoặc bị cắt do max_tokens."""


@dataclass(frozen=True)
class LLMRequest:
    purpose: str  # tên tác vụ, để thống kê (rephrase_queries, classify_passages...)
    run_id: str  # nằm trong khóa cache
    system: str
    user: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class RawCompletion:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None
    request_id: str | None = None


class Transport(Protocol):
    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        effort: str,
        timeout_s: float,
    ) -> RawCompletion: ...


@dataclass(frozen=True)
class CallRecord:
    purpose: str
    run_id: str
    model: str
    cache_key: str
    cached: bool
    attempts: int
    billed_input_tokens: int  # token tính tiền ở lần gọi NÀY (0 nếu cache hit)
    billed_output_tokens: int
    latency_s: float
    status: str  # ok | invalid_output | refusal | truncated | error
    request_id: str | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LLMResult:
    data: dict[str, Any]
    record: CallRecord


@dataclass
class UsageTotals:
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    failures: int = 0
    by_purpose: dict[str, int] = field(default_factory=dict)


def cache_key(settings: LLMSettings, req: LLMRequest) -> str:
    payload = {
        "v": CACHE_VERSION,
        "provider": settings.provider,
        "model": settings.model,
        "effort": settings.effort,
        "max_tokens": settings.max_tokens,
        "purpose": req.purpose,
        "run_id": req.run_id,
        "system": req.system,
        "user": req.user,
        "schema": req.schema,
    }
    # ensure_ascii=True để chuỗi có ký tự đại diện lẻ (surrogate) vẫn băm được thay vì văng lỗi
    blob = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"khóa JSON trùng: {k!r}")
        d[k] = v
    return d


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        transport: Transport,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._clock = clock
        self.records: list[CallRecord] = []
        self.warnings: list[str] = []  # lỗi ghi cache/log (không làm mất kết quả đã trả tiền)

    # ---- cache

    def _cache_path(self, key: str) -> Path | None:
        d = self.settings.cache_dir
        return None if d is None else d / key[:2] / f"{key}.json"

    def _read_cache(self, key: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        p = self._cache_path(key)
        if p is None or not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return data if isinstance(data, dict) and not validate(data, schema) else None

    def _write_cache(self, key: str, data: dict[str, Any], rec: CallRecord) -> None:
        """Ghi cache tốt nhất có thể: lỗi đĩa chỉ là cảnh báo, kết quả đã trả tiền vẫn được trả."""
        p = self._cache_path(key)
        if p is None:
            return
        tmp = p.with_name(
            f"{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )  # không đua giữa tiến trình
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            body = {"data": data, "record": rec.to_dict()}
            tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            self.warnings.append(f"không ghi được cache {p.name}: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _log(self, rec: CallRecord) -> None:
        self.records.append(rec)  # luôn giữ trong bộ nhớ trước, để token đã tiêu không bị mất
        path = self.settings.call_log
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as e:
            self.warnings.append(f"không ghi được call_log: {e}")

    # ---- gọi

    def call(self, req: LLMRequest) -> LLMResult:
        """Gọi LLM với structured output. Trả dữ liệu ĐÃ qua kiểm schema, hoặc raise LLMError."""
        bad = check_schema(req.schema)
        if bad:
            raise ValueError("schema không hợp lệ: " + "; ".join(bad))
        key = cache_key(self.settings, req)
        base = {
            "purpose": req.purpose,
            "run_id": req.run_id,
            "model": self.settings.model,
            "cache_key": key,
        }

        cached = self._read_cache(key, req.schema)
        if cached is not None:
            rec = CallRecord(**base, cached=True, attempts=0, billed_input_tokens=0,
                             billed_output_tokens=0, latency_s=0.0, status="ok")  # fmt: skip
            self._log(rec)
            return LLMResult(cached, rec)

        t_in = t_out = 0
        spent = 0.0
        request_id: str | None = None
        last_error = ""
        status = "invalid_output"
        attempts = 1 + self.settings.max_retries_invalid_output
        for attempt in range(1, attempts + 1):
            start = self._clock()
            try:
                raw = self.transport.complete(
                    model=self.settings.model,
                    system=req.system,
                    user=req.user,
                    schema=req.schema,
                    max_tokens=self.settings.max_tokens,
                    effort=self.settings.effort,
                    timeout_s=self.settings.timeout_s,
                )
            except Exception as e:
                spent += self._clock() - start
                self._fail(base, attempt, t_in, t_out, spent, "error", request_id, repr(e))
                raise LLMError(f"gọi LLM thất bại: {e!r}") from e
            spent += self._clock() - start
            t_in += raw.input_tokens
            t_out += raw.output_tokens
            request_id = raw.request_id or request_id
            if raw.stop_reason == "refusal":
                self._fail(base, attempt, t_in, t_out, spent, "refusal", request_id, "refusal")
                raise LLMRefusalError("mô hình từ chối trả lời")
            if raw.stop_reason == "max_tokens":
                self._fail(base, attempt, t_in, t_out, spent, "truncated", request_id, "max_tokens")
                raise LLMOutputError("đầu ra bị cắt do max_tokens; tăng llm.max_tokens")
            if raw.stop_reason not in (None, "end_turn", "stop_sequence"):
                reason = f"stop_reason không mong đợi: {raw.stop_reason}"
                self._fail(base, attempt, t_in, t_out, spent, "invalid_output", request_id, reason)
                raise LLMOutputError(reason)
            try:
                data = json.loads(raw.text, object_pairs_hook=_no_duplicate_keys)
                errs = (
                    validate(data, req.schema) if isinstance(data, dict) else ["gốc phải là object"]
                )
            except ValueError as e:
                errs = [f"không phải JSON: {e}"]
                data = None
            if not errs:
                rec = CallRecord(**base, cached=False, attempts=attempt, billed_input_tokens=t_in,
                                 billed_output_tokens=t_out, latency_s=spent, status="ok",
                                 request_id=request_id)  # fmt: skip
                self._write_cache(key, data, rec)
                self._log(rec)
                return LLMResult(data, rec)
            last_error = "; ".join(errs[:5])
        self._fail(base, attempts, t_in, t_out, spent, status, request_id, last_error)
        raise LLMOutputError(f"đầu ra sai schema sau {attempts} lần: {last_error}")

    def _fail(self, base, attempts, t_in, t_out, spent, status, request_id, error) -> None:
        self._log(
            CallRecord(
                **base,
                cached=False,
                attempts=attempts,
                billed_input_tokens=t_in,
                billed_output_tokens=t_out,
                latency_s=spent,
                status=status,
                request_id=request_id,
                error=error,
            )  # fmt: skip
        )

    # ---- thống kê

    def totals(self) -> UsageTotals:
        t = UsageTotals()
        for r in self.records:
            t.calls += 1
            t.cache_hits += r.cached
            t.input_tokens += r.billed_input_tokens
            t.output_tokens += r.billed_output_tokens
            t.latency_s += r.latency_s
            t.failures += r.status != "ok"
            t.by_purpose[r.purpose] = t.by_purpose.get(r.purpose, 0) + 1
        return t


class AnthropicTransport:
    """Gọi Anthropic Messages API với structured output (output_config.format).

    Không dùng tool_choice ép buộc (Claude 5.5 trả 400) và không gửi temperature/top_p/top_k.
    CHƯA được thử với API thật trong repo này (test chỉ dùng module `anthropic` giả).
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls, llm_cfg: dict[str, Any]) -> AnthropicTransport:
        try:
            import anthropic
        except ImportError as e:
            raise ConfigError("chưa cài gói 'anthropic' (pip install -e .[llm])") from e
        key = get_api_key(llm_cfg)  # chỉ từ biến môi trường
        return cls(anthropic.Anthropic(api_key=key))

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        effort: str,
        timeout_s: float,
    ) -> RawCompletion:
        resp = self._client.with_options(timeout=timeout_s).messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        return RawCompletion(
            text=text,
            input_tokens=int(resp.usage.input_tokens),
            output_tokens=int(resp.usage.output_tokens),
            stop_reason=getattr(resp, "stop_reason", None),
            request_id=getattr(resp, "_request_id", None),
        )


# (status HTTP, thân phản hồi). Tách ra để test thay bằng hàm giả, không gọi mạng.
HttpPost = Callable[[str, dict[str, str], bytes, float], tuple[int, str]]

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# finishReason của Gemini → stop_reason mà LLMClient hiểu; lý do khác giữ nguyên (client báo lỗi).
_GEMINI_STOP = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "BLOCKLIST": "refusal",
    "SPII": "refusal",
}
_GEMINI_RETRY_STATUS = (429, 500, 503)


def _urllib_post(
    url: str, headers: dict[str, str], body: bytes, timeout_s: float
) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - URL cố định
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


class GeminiTransport:
    """Gọi Gemini API (REST `generateContent`, chỉ dùng thư viện chuẩn) với structured output
    (`responseMimeType: application/json` + `responseJsonSchema`).

    Dùng để chạy thử bằng hạn mức miễn phí. `effort` KHÔNG được gửi (Gemini dùng cấu hình
    thinking riêng, để mặc định của model). Không gửi temperature/top_p/top_k. Lỗi 429/500/503 và
    timeout/lỗi mạng (gói free hay quá tải) được gọi lại sau `backoff_s` × (lần thử) giây, tối đa
    `rate_limit_retries` lần. Đã thử với API thật 2026-10-25 (gemini-3.5-flash-lite); test dùng
    hàm HTTP giả.
    """

    def __init__(
        self,
        api_key: str,
        *,
        rate_limit_retries: int = 0,
        backoff_s: float = 0.0,
        post: HttpPost = _urllib_post,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._retries = rate_limit_retries
        self._backoff = backoff_s
        self._post = post
        self._sleep = sleep

    @classmethod
    def from_env(cls, llm_cfg: dict[str, Any]) -> GeminiTransport:
        g = llm_cfg.get("gemini") or {}
        if not isinstance(g, dict):
            raise ConfigError("llm.gemini phải là mapping")
        retries, backoff = g.get("rate_limit_retries", 0), g.get("backoff_s", 0)
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ConfigError("llm.gemini.rate_limit_retries phải là số nguyên >= 0")
        if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or backoff < 0:
            raise ConfigError("llm.gemini.backoff_s phải là số >= 0")
        key = get_api_key(llm_cfg)  # chỉ từ biến môi trường
        return cls(key, rate_limit_retries=retries, backoff_s=float(backoff))

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int,
        effort: str,
        timeout_s: float,
    ) -> RawCompletion:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "maxOutputTokens": max_tokens,
            },
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._key}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = GEMINI_URL.format(model=model)
        for attempt in range(self._retries + 1):
            try:
                status, text = self._post(url, headers, data, timeout_s)
            except OSError:  # timeout/mất mạng (gói free hay treo): gọi lại như 429/503
                if attempt == self._retries:
                    raise
                self._sleep(self._backoff * (attempt + 1))
                continue
            if status not in _GEMINI_RETRY_STATUS or attempt == self._retries:
                break
            self._sleep(self._backoff * (attempt + 1))
        if status != 200:
            raise LLMError(f"Gemini HTTP {status}: {text[:300]}")
        resp = json.loads(text)
        usage = resp.get("usageMetadata") or {}
        # token suy luận (thoughts) cũng tính tiền đầu ra, như token suy luận của Claude
        t_in = int(usage.get("promptTokenCount", 0))
        t_out = int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0))
        rid = resp.get("responseId")
        cands = resp.get("candidates") or []
        if not cands:  # prompt bị chặn: không có ứng viên, chỉ có promptFeedback.blockReason
            return RawCompletion("", t_in, t_out, "refusal", rid)
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        out = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        reason = cand.get("finishReason")
        stop = _GEMINI_STOP.get(reason, str(reason).lower() if reason else None)
        return RawCompletion(out, t_in, t_out, stop, rid)


def make_transport(llm_cfg: dict[str, Any]) -> Transport:
    """Transport thật theo `llm.provider` (anthropic | gemini). Key chỉ đọc từ biến môi trường."""
    provider = llm_cfg.get("provider")
    if provider == "anthropic":
        return AnthropicTransport.from_env(llm_cfg)
    if provider == "gemini":
        return GeminiTransport.from_env(llm_cfg)
    raise ConfigError(f"llm.provider không hỗ trợ: {provider!r}")
