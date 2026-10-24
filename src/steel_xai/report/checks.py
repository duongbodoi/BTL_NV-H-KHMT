"""Bộ kiểm sau khi LLM viết (spec mục 13): đối chiếu số, liên kết khẳng định–nguồn, trạng thái,
nội dung cấm.

Mỗi vi phạm thuộc một loại. `unsupported` (khẳng định không dẫn nguồn) chỉ làm khẳng định bị loại;
mọi loại khác là chặn: bản nháp bị từ chối và sinh lại. Số lỗi của bản nháp ĐẦU TIÊN được giữ làm
metric (numeric mismatch, unsupported claim...).

Đối chiếu số: mỗi số trong khẳng định phải khớp (a) một số N đã dẫn, cùng đơn vị, không thô hơn
chuỗi hiển thị, đúng chiều nếu có "tăng/giảm" ngay trước; hoặc (b) một số viết trong trích dẫn
E / lý do M đã dẫn (cùng đơn vị); hoặc (c) hạng của giả thuyết H đã dẫn. Tỷ số `a/b` phải đúng
cặp critical đạt/tổng của một H đã dẫn. Được bỏ qua: mã nguồn có thật, mã giả thuyết có thật, năm
và ngày có năm thuộc dữ liệu đầu vào, các mẫu trong `numbers.exempt_patterns`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from steel_xai.report.facts import (
    EvidenceItem,
    MissingItem,
    NumberFact,
    ReportInput,
    VerdictItem,
)
from steel_xai.report.numbers import (
    NumberToken,
    display_step,
    extract_numbers,
    matches_literal,
    matches_value,
)
from steel_xai.report.prompts import SECTIONS
from steel_xai.report.settings import STATUS_ORDER, ReportSettings

NON_BLOCKING = frozenset({"unsupported"})
EVIDENCE_ROLES = frozenset({"support", "failed", "contradict"})  # số thuộc riêng một giả thuyết
_IDS = re.compile(r"\b[NEHM]\d+\b")
_CODES = re.compile(r"\b[A-Z]{2,5}-\d+\b")
_DATES = re.compile(r"\b(\d{4})-\d{2}(?:-\d{2})?\b|\b\d{1,2}/\d{1,2}/(\d{4})\b")
_PAIR = re.compile(r"(?<![\w/,.])(\d+)\s*/\s*(\d+)(?![\w/,.])")
_STATUS_EN = re.compile(r"\b(SUPPORTED|PARTIAL|UNVERIFIED|CONTRADICTED)\b", re.IGNORECASE)
_NEGATED = re.compile(r"\b(không|chưa)(\s+\S+){0,3}\s*$")
# ranh giới câu/vế cho cụm thuật lại: sau dấu này là một khẳng định mới
_CLAUSE_SPLIT = re.compile(r"[.;:!?](\s|$)")
_DIRECTION = re.compile(r"\b(tăng(?!\s+trưởng)|giảm)\b")
_NEG_BEFORE = re.compile(r"\b(không|chưa)\s+$")
_LEVEL_BEFORE = re.compile(r"\b(từ|xuống|lên|còn|mức)\s*$")
_YEAR_UNIT = re.compile(r"\s*(usd|vnd|đồng|tấn)\b", re.IGNORECASE)


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
    """Dữ liệu đã qua kiểm schema của client; chuẩn hóa khoảng trắng (không cho chèn dòng mới vào
    Markdown của báo cáo)."""
    return tuple(
        Claim(
            section=c["section"],
            hypothesis_id=c["hypothesis_id"].strip(),
            text=" ".join(c["text"].split()),
            sources=tuple(dict.fromkeys(s.strip() for s in c["sources"] if s.strip())),
        )
        for c in data["claims"]
    )


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).lower()


def _blank_span(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _sub_blank(pattern: re.Pattern[str], text: str, keep=lambda m: True) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)) if keep(m) else m.group(0), text)


def _first_unnegated(
    patterns: tuple[re.Pattern[str], ...],
    low: str,
    attribution: tuple[re.Pattern[str], ...] = (),
) -> str | None:
    """Mẫu đầu tiên khớp mà không có "không/chưa" trong ba từ ngay trước, và (nếu có `attribution`)
    không nằm sau một cụm thuật lại ("giả thuyết", "cho rằng") trong cùng câu/vế."""
    for p in patterns:
        for m in p.finditer(low):
            if _NEGATED.search(low[max(0, m.start() - 40) : m.start()]):
                continue
            clause = _CLAUSE_SPLIT.split(low[: m.start()])[-1]
            if any(a.search(clause) for a in attribution):
                continue
            return p.pattern
    return None


class _Checker:
    def __init__(self, inp: ReportInput, s: ReportSettings) -> None:
        self.inp, self.s = inp, s
        self.by_id = inp.by_id
        self.status_of = {v.hypothesis_id: v.status for v in inp.verdicts}
        self.codes = {v.code for v in (*inp.verdicts, *inp.others) if v.code}
        self.steps: dict[str, Decimal] = {n.id: display_step(n.value, n.unit) for n in inp.numbers}

    # ---- số

    def _strip(self, text: str) -> str:
        """Thay bằng khoảng trắng (giữ vị trí) những gì không phải số liệu."""
        t = _sub_blank(_IDS, text, lambda m: m.group(0) in self.by_id)
        t = _sub_blank(_CODES, t, lambda m: m.group(0) in self.codes)
        t = _sub_blank(_DATES, t, lambda m: int(m.group(1) or m.group(2)) in self.inp.years)
        for p in self.s.exempt_patterns:
            t = _sub_blank(p, t)
        return t

    def _fact_ok(self, tok: NumberToken, n: NumberFact, direction: str | None) -> bool:
        # số đầu vào là MỨC (biên gộp năm X = 11,5%): "giảm xuống 11,5%" đúng dù mức dương;
        # chiều chỉ kiểm cho số là thay đổi (chỉ báo, kết quả check)
        if n.role != "input" and (
            direction == "tăng" and n.value < 0 or direction == "giảm" and n.value > 0
        ):
            return False
        return matches_value(tok, n.value, n.unit, self.s.relative_tolerance, self.steps[n.id])

    def numbers(self, i: int, c: Claim, cited: list[Any]) -> tuple[list[Violation], int]:
        out: list[Violation] = []
        src = ", ".join(c.sources) or "không có nguồn"
        text = self._strip(c.text)
        verdicts = [x for x in cited if isinstance(x, VerdictItem)]
        pairs = list(_PAIR.finditer(text))
        for m in pairs:
            pair = (int(m.group(1)), int(m.group(2)))
            if not any(pair == (v.critical_passed, v.critical_total) for v in verdicts):
                out.append(
                    Violation(
                        "numeric_mismatch",
                        i,
                        f"tỷ số {m.group(0)!r} không khớp số check critical đạt/tổng của mã H "
                        f"đã dẫn ({src})",
                    )
                )
            text = _blank_span(text, m.start(), m.end())

        facts = [x for x in cited if isinstance(x, NumberFact)]
        literals = [
            t
            for x in cited
            if isinstance(x, (EvidenceItem, MissingItem))
            for t in extract_numbers(x.quote if isinstance(x, EvidenceItem) else x.reason)
        ]
        ranks = {Decimal(v.rank) for v in verdicts if v.rank is not None}
        tokens = extract_numbers(text)
        prev_end = 0
        for tok in tokens:
            window = text[max(prev_end, tok.start - 30) : tok.start]
            w_start = max(prev_end, tok.start - 30)
            low_w = _norm(window)
            dirs = list(_DIRECTION.finditer(low_w))
            last = dirs[-1] if dirs else None
            direction = last.group(1) if last is not None else None
            if last is not None:
                at = w_start + last.start()
                # "không giảm"/"chưa tăng": không nêu chiều (nhìn cả trước cửa sổ)
                if _NEG_BEFORE.search(_norm(text[max(0, at - 12) : at])):
                    direction = None
            if _LEVEL_BEFORE.search(low_w):  # "giảm từ 132,7 ngày xuống 63,0": số là mức
                direction = None
            prev_end = tok.end
            shown = c.text[tok.start : tok.end].strip()
            if not tok.candidates:
                out.append(
                    Violation(
                        "numeric_mismatch",
                        i,
                        f"số {shown!r} không theo định dạng Việt (dấu chấm ngăn nghìn, dấu phẩy "
                        "thập phân); chép đúng chuỗi display của mã N",
                    )
                )
                continue
            if (
                tok.is_year_like
                and int(tok.text) in self.inp.years
                and not _YEAR_UNIT.match(text, tok.end)
            ):
                continue
            ok = (
                any(self._fact_ok(tok, n, direction) for n in facts)
                or any(matches_literal(tok, lit, self.s.relative_tolerance) for lit in literals)
                or (
                    tok.kind == "plain"
                    and not tok.negative
                    and any(cand.value in ranks and cand.decimals == 0 for cand in tok.candidates)
                )
            )
            if not ok:
                out.append(
                    Violation(
                        "numeric_mismatch",
                        i,
                        f"số {shown!r} không khớp giá trị/văn bản của nguồn đã dẫn ({src}) "
                        "(đơn vị, độ chi tiết, hoặc chiều tăng/giảm); chép đúng chuỗi display "
                        "của mã N",
                    )
                )
        return out, len(tokens) + len(pairs)

    # ---- trạng thái

    def statuses(self, i: int, c: Claim, cited: list[Any]) -> list[Violation]:
        low = _norm(c.text)
        said: list[str] = [m.group(1).upper() for m in _STATUS_EN.finditer(c.text)]
        for status in STATUS_ORDER:
            for p in self.s.status_phrases.get(status, ()):
                for m in p.finditer(low):
                    if not _NEGATED.search(low[max(0, m.start() - 40) : m.start()]):
                        said.append(status)
                low = p.sub(lambda m: " " * len(m.group(0)), low)
        if not said:
            return []
        if c.hypothesis_id:
            allowed = (
                {self.status_of[c.hypothesis_id]} if c.hypothesis_id in self.status_of else set()
            )
        else:
            allowed = {x.status for x in cited if isinstance(x, VerdictItem)}
        wrong = sorted(set(said) - allowed)
        if not wrong:
            return []
        expected = ", ".join(sorted(allowed)) or "không dẫn mã H nào"
        return [
            Violation(
                "status_mismatch",
                i,
                f"nhắc trạng thái {', '.join(wrong)} nhưng trạng thái do code xác định là "
                f"{expected}",
            )
        ]

    # ---- một khẳng định

    def claim(self, i: int, c: Claim) -> tuple[list[Violation], int]:
        s = self.s
        out: list[Violation] = []
        if c.section not in SECTIONS:
            out.append(Violation("format", i, f"mục lạ {c.section!r}"))
        if not c.text:
            out.append(Violation("format", i, "khẳng định rỗng"))
        if len(c.text) > s.max_claim_chars:
            out.append(Violation("format", i, f"dài quá {s.max_claim_chars} ký tự"))
        if not c.sources:
            out.append(Violation("unsupported", i, "không dẫn nguồn nào; khẳng định bị loại"))
        unknown = [x for x in c.sources if x not in self.by_id]
        if unknown:
            out.append(
                Violation("unknown_source", i, f"mã nguồn không tồn tại: {', '.join(unknown)}")
            )
        if c.hypothesis_id and c.hypothesis_id not in self.status_of:
            out.append(
                Violation(
                    "unknown_hypothesis", i, f"giả thuyết {c.hypothesis_id!r} không thuộc top-k"
                )
            )
        cited = [self.by_id[x] for x in c.sources if x in self.by_id]
        if c.hypothesis_id:
            wrong = [
                x.id
                for x in cited
                if (
                    isinstance(x, (EvidenceItem, MissingItem, VerdictItem))
                    or (isinstance(x, NumberFact) and x.role in EVIDENCE_ROLES)
                )
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
        nums, checked = self.numbers(i, c, cited)
        out += nums
        out += self.statuses(i, c, cited)

        low = _norm(c.text)
        for group, pats in s.forbidden.items():
            hit = _first_unnegated(pats, low)
            if hit:
                out.append(Violation(f"forbidden_{group}", i, f"nội dung cấm (mẫu {hit!r})"))
        hedged = any(p.search(low) for p in s.hedges)
        supported = self.status_of.get(c.hypothesis_id) == "SUPPORTED"
        # mục missing nói về lý do THIẾU DỮ LIỆU ("chưa kiểm được vì thiếu..."), không phải nhân quả
        if not hedged and not supported and c.section != "missing":
            hit = _first_unnegated(s.causal, low, s.attribution)
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
        return out, checked


def check_draft(claims: tuple[Claim, ...], inp: ReportInput, s: ReportSettings) -> DraftCheck:
    chk = _Checker(inp, s)
    violations: list[Violation] = []
    checked = 0
    if not claims:
        violations.append(Violation("format", None, "không có khẳng định nào"))
    if len(claims) > s.max_claims:
        violations.append(Violation("format", None, f"quá {s.max_claims} khẳng định"))
    for i, c in enumerate(claims, start=1):
        vs, n = chk.claim(i, c)
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
