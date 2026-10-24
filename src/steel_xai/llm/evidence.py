"""Bước 3 phần văn bản: truy xuất (BM25, lọc as-of) → LLM phân loại → code kiểm trích dẫn.

Ranh giới trách nhiệm (spec mục 11):
- LLM chỉ diễn đạt lại truy vấn và gán nhãn support | contradict | irrelevant kèm trích dẫn.
- CODE quyết định: check nào chạy (danh sách check lấy từ thư viện, LLM không thêm/bớt),
  đoạn nào được xem xét (chỉ đoạn retriever trả về, đã lọc as-of), trích dẫn nào được giữ
  (phải là chuỗi con nguyên văn của ĐÚNG đoạn đó) và trạng thái FOUND | MISSING | UNVERIFIED.
- Không tìm thấy → UNVERIFIED; trích dẫn không khớp → bị loại, check ghi MISSING (spec 10.3, 10.4).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from steel_xai.config import load_config
from steel_xai.contracts import Anomaly, Check, Evidence, Hypothesis, TextCheckStatus
from steel_xai.llm.client import LLMClient, LLMError, LLMRequest
from steel_xai.llm.prompts import (
    CLASSIFY_SCHEMA,
    CLASSIFY_SYSTEM,
    REPHRASE_SCHEMA,
    REPHRASE_SYSTEM,
    classify_user,
    rephrase_user,
)
from steel_xai.llm.quotes import canonical, verify_quote
from steel_xai.retrieval import BM25Retriever, RetrievedEvidence


@dataclass(frozen=True)
class DroppedEvidence:
    """Bằng chứng LLM nêu nhưng code loại (để đếm lỗi và để người xem)."""

    check_id: str
    chunk_id: str
    label: str
    quote: str
    reason: str  # not_found | too_short | too_long | empty | unknown_chunk | duplicate

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "chunk_id": self.chunk_id,
            "label": self.label,
            "quote": self.quote,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TextCheckOutcome:
    check_id: str
    status: TextCheckStatus
    evidence: tuple[Evidence, ...] = ()
    dropped: tuple[DroppedEvidence, ...] = ()
    queries: tuple[str, ...] = ()
    passage_ids: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "dropped": [d.to_dict() for d in self.dropped],
            "queries": list(self.queries),
            "passage_ids": list(self.passage_ids),
            "reason": self.reason,
        }


@dataclass
class _Classified:
    evidence: list[Evidence] = field(default_factory=list)
    dropped: list[DroppedEvidence] = field(default_factory=list)
    judged: int = 0
    unjudged: list[str] = field(default_factory=list)  # đoạn LLM không trả judgement nào


# ---------------- diễn đạt lại truy vấn

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _query_is_clean(q: str, forbidden_words: set[str]) -> bool:
    """Truy vấn diễn đạt lại không được có chữ số (số liệu bịa) hay mã công ty (lộ thông tin ca)."""
    if any(ch.isdigit() for ch in q):
        return False
    return not ({w.lower() for w in _WORD.findall(q)} & forbidden_words)


def rephrase_queries(
    client: LLMClient,
    *,
    run_id: str,
    hypothesis: Hypothesis,
    anomaly_type: str,
    direction: str,
    checks: list[Check],
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    """Truy vấn bổ sung do LLM diễn đạt lại: {check_id: (truy vấn mới, ...)} và danh sách bị bỏ qua.

    Chỉ nhận check_id có trong `checks` (LLM không thêm check); truy vấn rỗng, trùng, quá dài hoặc
    trùng truy vấn gốc bị bỏ; tối đa `max_extra_queries` mỗi check.
    """
    ev = client.settings.evidence
    forbidden = {str(c).lower() for c in load_config("dataset").get("companies", [])}
    known = {c.id: c for c in checks}
    payload = [{"check_id": c.id, "queries": list(c.queries)} for c in checks]
    res = client.call(
        LLMRequest(
            purpose="rephrase_queries",
            run_id=run_id,
            system=REPHRASE_SYSTEM,
            user=rephrase_user(hypothesis.claim, anomaly_type, direction, payload),
            schema=REPHRASE_SCHEMA,
        )
    )
    out: dict[str, list[str]] = {}
    ignored: list[str] = []
    for item in res.data["checks"]:
        cid = item["check_id"]
        if cid not in known:
            ignored.append(f"check_id lạ: {cid!r}")
            continue
        have = {canonical(q).casefold() for q in known[cid].queries}
        bucket = out.setdefault(cid, [])
        for q in item["queries"]:
            q = canonical(q)
            key = q.casefold()
            if not q or len(q) > ev.max_query_chars or key in have:
                continue
            if not _query_is_clean(q, forbidden):
                ignored.append(f"truy vấn có số liệu/tên công ty: {q!r}")
                continue
            have.add(key)
            if len(bucket) < ev.max_extra_queries:
                bucket.append(q)
    return {k: tuple(v) for k, v in out.items()}, ignored


# ---------------- phân loại đoạn + kiểm trích dẫn


def classify_passages(
    client: LLMClient,
    *,
    run_id: str,
    hypothesis: Hypothesis,
    check: Check,
    passages: list[RetrievedEvidence],
) -> _Classified:
    """LLM gán nhãn từng đoạn, CODE kiểm trích dẫn; chỉ bằng chứng qua kiểm mới được giữ."""
    if check.polarity not in ("support", "contradict"):
        raise ValueError(f"polarity của check {check.id!r} phải là support | contradict")
    ev = client.settings.evidence
    by_id = {p.chunk_id: p for p in passages}
    res = client.call(
        LLMRequest(
            purpose="classify_passages",
            run_id=run_id,
            system=CLASSIFY_SYSTEM,
            user=classify_user(
                hypothesis.claim,
                check.polarity,
                [
                    {"chunk_id": p.chunk_id, "section": p.section, "page": p.page, "text": p.text}
                    for p in passages
                ],
            ),
            schema=CLASSIFY_SCHEMA,
        )
    )
    out = _Classified()
    groups: dict[str, list[dict[str, str]]] = {}
    for j in res.data["judgements"]:
        if j["chunk_id"] not in by_id:  # LLM nêu một đoạn mà code không đưa cho nó
            out.dropped.append(
                DroppedEvidence(check.id, j["chunk_id"], j["label"], j["quote"], "unknown_chunk")
            )
            continue
        groups.setdefault(j["chunk_id"], []).append(j)
    out.unjudged = [cid for cid in by_id if cid not in groups]
    for cid, js in groups.items():
        out.judged += 1
        claims = [j for j in js if j["label"] != "irrelevant"]
        if not claims:
            continue
        # Mỗi đoạn chỉ cho tối đa MỘT bằng chứng: judgement đầu tiên có trích dẫn qua kiểm.
        kept: Evidence | None = None
        first_failure: DroppedEvidence | None = None
        for j in claims:
            if kept is not None:
                out.dropped.append(
                    DroppedEvidence(check.id, cid, j["label"], j["quote"], "duplicate")
                )
                continue
            verdict = verify_quote(
                j["quote"],
                by_id[cid].text,
                min_chars=ev.min_quote_chars,
                max_chars=ev.max_quote_chars,
                min_alpha_ratio=ev.min_alpha_ratio,
            )
            if verdict.ok:
                kept = Evidence(
                    kind="text",
                    polarity=j["label"],
                    check_id=check.id,
                    provenance=by_id[cid].provenance(),
                    quote=verdict.quote,
                    reason=" ".join(j["reason"].split())[: ev.max_reason_chars],
                )
            elif first_failure is None:
                first_failure = DroppedEvidence(
                    check.id, cid, j["label"], j["quote"], verdict.reason
                )
            else:
                out.dropped.append(
                    DroppedEvidence(check.id, cid, j["label"], j["quote"], "duplicate")
                )
        if kept is not None:
            out.evidence.append(kept)
        elif first_failure is not None:
            out.dropped.append(first_failure)
    return out


_QUOTE_FAILURES = frozenset({"not_found", "too_short", "too_long", "low_text", "partial_word"})


def _status(check: Check, got: _Classified) -> TextCheckStatus:
    if any(e.polarity == check.polarity for e in got.evidence):
        return TextCheckStatus.FOUND
    # MISSING chỉ khi LLM nêu bằng chứng đúng chiều mà trích dẫn KHÔNG khớp nguyên văn (spec 10.4).
    # Đoạn LLM bịa (unknown_chunk), trích dẫn rỗng hay judgement trùng không phải "trích dẫn sai".
    if any(d.label == check.polarity and d.reason in _QUOTE_FAILURES for d in got.dropped):
        return TextCheckStatus.MISSING
    return TextCheckStatus.UNVERIFIED


# ---------------- điều phối cho một giả thuyết


def gather_text_evidence(
    *,
    client: LLMClient,
    retriever: BM25Retriever,
    hypothesis: Hypothesis,
    anomaly: Anomaly,
    run_id: str,
    rephrase: bool = True,
) -> dict[str, TextCheckOutcome]:
    """Chạy mọi check `text` của giả thuyết cho một bất thường (danh sách check do thư viện)."""
    checks = [c for c in hypothesis.checks if c.kind == "text"]
    if not checks:
        return {}
    if not anomaly.asof_date:
        return {
            c.id: TextCheckOutcome(
                c.id,
                TextCheckStatus.UNVERIFIED,
                queries=c.queries,
                reason="bất thường thiếu mốc as-of, không truy xuất được tài liệu",
            )  # fmt: skip
            for c in checks
        }
    asof = dt.date.fromisoformat(anomaly.asof_date)
    extra: dict[str, tuple[str, ...]] = {}
    note = ""
    if rephrase:
        try:
            extra, ignored = rephrase_queries(
                client, run_id=run_id, hypothesis=hypothesis, anomaly_type=anomaly.type,
                direction=anomaly.direction, checks=checks,
            )  # fmt: skip
            note = "; ".join(ignored)
        except LLMError as e:
            note = f"không diễn đạt lại được truy vấn ({e}); dùng truy vấn gốc"

    outcomes: dict[str, TextCheckOutcome] = {}
    limit = client.settings.evidence.max_passages_per_check
    for c in checks:
        queries = tuple(dict.fromkeys([*c.queries, *extra.get(c.id, ())]))
        passages = retriever.retrieve_many(queries, company=anomaly.company, asof=asof)[:limit]
        for p in passages:  # phòng thủ lớp hai: retriever đã lọc, đây chỉ là chốt chặn cuối
            if p.company != anomaly.company or dt.date.fromisoformat(p.doc_published) > asof:
                raise ValueError(f"rò rỉ as-of: {p.chunk_id} công bố {p.doc_published} > {asof}")
        if not passages:
            outcomes[c.id] = TextCheckOutcome(
                c.id, TextCheckStatus.UNVERIFIED, queries=queries,
                reason=("; ".join(filter(None, ["không truy xuất được đoạn nào", note]))),
            )  # fmt: skip
            continue
        try:
            got = classify_passages(
                client, run_id=run_id, hypothesis=hypothesis, check=c, passages=passages
            )
        except LLMError as e:
            outcomes[c.id] = TextCheckOutcome(
                c.id, TextCheckStatus.UNVERIFIED, queries=queries,
                passage_ids=tuple(p.chunk_id for p in passages), reason=f"LLM lỗi: {e}",
            )  # fmt: skip
            continue
        notes = "; ".join(
            n for n in (note, f"LLM bỏ sót {len(got.unjudged)} đoạn" if got.unjudged else "") if n
        )
        outcomes[c.id] = TextCheckOutcome(
            c.id,
            _status(c, got),
            tuple(got.evidence),
            tuple(got.dropped),
            queries,
            tuple(p.chunk_id for p in passages),
            notes,
        )
    return outcomes
