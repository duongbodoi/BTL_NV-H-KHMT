"""Bộ kiểm sau khi LLM viết (spec mục 13): đối chiếu số, liên kết khẳng định–nguồn, nội dung cấm.

Mỗi vi phạm thuộc một loại. `unsupported` (khẳng định không dẫn nguồn) chỉ làm khẳng định bị loại;
mọi loại khác là chặn: bản nháp bị từ chối và sinh lại. Số lỗi của bản nháp ĐẦU TIÊN được giữ làm
metric (numeric mismatch, unsupported claim...).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from steel_xai.report.facts import (
    EvidenceItem,
    MissingItem,
    NumberFact,
    ReportInput,
    VerdictItem,
)
from steel_xai.report.numbers import NumberToken, extract_numbers, matches_literal, matches_value
from steel_xai.report.prompts import SECTIONS
from steel_xai.report.settings import ReportSettings

NON_BLOCKING = frozenset({"unsupported"})


@dataclass(frozen=True)
class Claim:
    section: str
    hypothesis_id: str
    text: str
    sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "hypothesis_id": self.hypothesis_id,
            "text": self.text,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class Violation:
    kind: str
    claim_index: int | None  # None: lỗi của cả bản nháp
    detail: str

    @property
    def blocking(self) -> bool:
        return self.kind not in NON_BLOCKING

    def feedback(self) -> str:
        where = "toàn bản nháp" if self.claim_index is None else f"khẳng định #{self.claim_index}"
        return f"{where} [{self.kind}]: {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "claim_index": self.claim_index, "detail": self.detail}


@dataclass(frozen=True)
class DraftCheck:
    claims: tuple[Claim, ...]
    violations: tuple[Violation, ...]
    numbers_checked: int

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(v.kind for v in self.violations))

    @property
    def numeric_mismatches(self) -> int:
        return self.counts.get("numeric_mismatch", 0)

    @property
    def unsupported_claims(self) -> int:
        return self.counts.get("unsupported", 0)

    @property
    def blocking(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.blocking)

    @property
    def accepted(self) -> bool:
        return not self.blocking

    def passing_claims(self) -> tuple[Claim, ...]:
        """Khẳng định không dính vi phạm nào gắn với nó (kể cả unsupported)."""
        bad = {v.claim_index for v in self.violations if v.claim_index is not None}
        return tuple(c for i, c in enumerate(self.claims, start=1) if i not in bad)

    def summary(self) -> dict[str, Any]:
        return {
            "claims": len(self.claims),
            "numbers_checked": self.numbers_checked,
            "violations": self.counts,
        }


def parse_claims(data: dict[str, Any]) -> tuple[Claim, ...]:
    """Dữ liệu đã qua kiểm schema của client; chỉ chuẩn hóa."""
    return tuple(
        Claim(
            section=c["section"],
            hypothesis_id=c["hypothesis_id"].strip(),
            text=c["text"].strip(),
            sources=tuple(dict.fromkeys(s.strip() for s in c["sources"] if s.strip())),
        )
        for c in data["claims"]
    )


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).lower()


def _blank(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    for p in patterns:
        text = p.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _literal_text(item: Any) -> str:
    if isinstance(item, EvidenceItem):
        return item.quote
    if isinstance(item, MissingItem):
        return item.reason
    if isinstance(item, VerdictItem):
        return item.literal_text
    return ""


def _number_ok(
    tok: NumberToken,
    cited: list[Any],
    literals: list[NumberToken],
    inp: ReportInput,
    s: ReportSettings,
) -> bool:
    if tok.is_year_like and any(c.value in inp.years for c in tok.candidates):
        return True
    for item in cited:
        if isinstance(item, NumberFact) and matches_value(
            tok, item.value, item.unit, s.relative_tolerance
        ):
            return True
    return any(matches_literal(tok, lit, s.relative_tolerance) for lit in literals)


def _check_claim(
    i: int, c: Claim, inp: ReportInput, s: ReportSettings, status_of: dict[str, str]
) -> tuple[list[Violation], int]:
    out: list[Violation] = []
    by_id = inp.by_id
    if c.section not in SECTIONS:
        out.append(Violation("format", i, f"mục lạ {c.section!r}"))
    if not c.text:
        out.append(Violation("format", i, "khẳng định rỗng"))
    if len(c.text) > s.max_claim_chars:
        out.append(Violation("format", i, f"dài quá {s.max_claim_chars} ký tự"))
    if not c.sources:
        out.append(Violation("unsupported", i, "không dẫn nguồn nào; khẳng định bị loại"))
    unknown = [x for x in c.sources if x not in by_id]
    if unknown:
        out.append(Violation("unknown_source", i, f"mã nguồn không tồn tại: {', '.join(unknown)}"))
    if c.hypothesis_id and c.hypothesis_id not in status_of:
        out.append(
            Violation("unknown_hypothesis", i, f"giả thuyết {c.hypothesis_id!r} không thuộc top-k")
        )
    cited = [by_id[x] for x in c.sources if x in by_id]
    if c.hypothesis_id:
        wrong = [
            x.id
            for x in cited
            if isinstance(x, (EvidenceItem, MissingItem, VerdictItem))
            and x.hypothesis_id != c.hypothesis_id
        ]
        if wrong:
            out.append(
                Violation(
                    "wrong_hypothesis_source",
                    i,
                    f"{', '.join(wrong)} thuộc giả thuyết khác {c.hypothesis_id!r}",
                )
            )

    # đối chiếu số
    literals = [t for x in cited for t in extract_numbers(_literal_text(x))]
    tokens = extract_numbers(_blank(c.text, s.exempt_patterns))
    for tok in tokens:
        if not _number_ok(tok, cited, literals, inp, s):
            shown = c.text[tok.start : tok.end].strip()
            src = ", ".join(c.sources) or "không có nguồn"
            out.append(
                Violation(
                    "numeric_mismatch",
                    i,
                    f"số {shown!r} không khớp giá trị/văn bản của nguồn đã dẫn ({src}); "
                    "chép đúng chuỗi display của mã N",
                )
            )

    # nội dung cấm
    low = _norm(c.text)
    for group, pats in s.forbidden.items():
        hit = next((p.pattern for p in pats if p.search(low)), None)
        if hit:
            out.append(Violation(f"forbidden_{group}", i, f"nội dung cấm (mẫu {hit!r})"))
    hedged = any(p.search(low) for p in s.hedges)
    if not hedged and status_of.get(c.hypothesis_id) != "SUPPORTED":
        hit = next((p.pattern for p in s.causal if p.search(low)), None)
        if hit:
            out.append(
                Violation(
                    "unhedged_causal",
                    i,
                    f"khẳng định nhân quả (mẫu {hit!r}) khi giả thuyết chưa SUPPORTED; "
                    "dùng cách nói có điều kiện",
                )
            )
    if c.section == "conclusion" and not hedged:
        out.append(Violation("unconditional_conclusion", i, "kết luận phải có điều kiện"))
    return out, len(tokens)


def check_draft(claims: tuple[Claim, ...], inp: ReportInput, s: ReportSettings) -> DraftCheck:
    status_of = {v.hypothesis_id: v.status for v in inp.verdicts}
    violations: list[Violation] = []
    checked = 0
    if not claims:
        violations.append(Violation("format", None, "không có khẳng định nào"))
    if len(claims) > s.max_claims:
        violations.append(Violation("format", None, f"quá {s.max_claims} khẳng định"))
    for i, c in enumerate(claims, start=1):
        vs, n = _check_claim(i, c, inp, s, status_of)
        violations += vs
        checked += n

    # độ phủ: mỗi giả thuyết top-k phải được nhắc; giả thuyết chưa xác thực đầy đủ mà có phần thiếu
    # phải được giải thích ở mục missing (spec mục 13: "nêu rõ giả thuyết chưa xác thực và vì sao")
    mentioned = {c.hypothesis_id for c in claims if c.sources}
    explained = {c.hypothesis_id for c in claims if c.section == "missing" and c.sources}
    has_missing = {m.hypothesis_id for m in inp.missing}
    for v in inp.verdicts:
        if v.hypothesis_id not in mentioned:
            violations.append(
                Violation("coverage", None, f"chưa có khẳng định nào về {v.hypothesis_id!r}")
            )
        elif (
            v.status in ("UNVERIFIED", "PARTIAL")
            and v.hypothesis_id in has_missing
            and v.hypothesis_id not in explained
        ):
            violations.append(
                Violation(
                    "coverage",
                    None,
                    f"{v.hypothesis_id!r} là {v.status} nhưng mục missing chưa nêu phần còn thiếu",
                )
            )
    return DraftCheck(claims, tuple(violations), checked)
