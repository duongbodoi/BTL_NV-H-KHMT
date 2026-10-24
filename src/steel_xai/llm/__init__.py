"""Phase 9 — LLM: client mỏng (structured output, cache, log), diễn đạt truy vấn, phân loại
bằng chứng, kiểm trích dẫn nguyên văn bằng code. Mọi test dùng transport giả."""

from steel_xai.llm.client import (
    AnthropicTransport,
    CallRecord,
    GeminiTransport,
    LLMClient,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMRequest,
    LLMResult,
    RawCompletion,
    Transport,
    UsageTotals,
    cache_key,
    make_transport,
)
from steel_xai.llm.evidence import (
    DroppedEvidence,
    TextCheckOutcome,
    classify_passages,
    gather_text_evidence,
    rephrase_queries,
)
from steel_xai.llm.prompts import prompt_fingerprint
from steel_xai.llm.quotes import QuoteCheck, canonical, verify_quote
from steel_xai.llm.settings import EvidenceSettings, LLMSettings, load_llm_settings

__all__ = [
    "AnthropicTransport",
    "CallRecord",
    "GeminiTransport",
    "DroppedEvidence",
    "EvidenceSettings",
    "LLMClient",
    "LLMError",
    "LLMOutputError",
    "LLMRefusalError",
    "LLMRequest",
    "LLMResult",
    "LLMSettings",
    "QuoteCheck",
    "RawCompletion",
    "TextCheckOutcome",
    "Transport",
    "UsageTotals",
    "cache_key",
    "make_transport",
    "canonical",
    "classify_passages",
    "gather_text_evidence",
    "load_llm_settings",
    "prompt_fingerprint",
    "rephrase_queries",
    "verify_quote",
]
