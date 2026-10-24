"""Định dạng và đọc số trong báo cáo tiếng Việt (spec mục 13, 16).

Định dạng Việt: dấu chấm ngăn nghìn, dấu phẩy thập phân, số âm trong ngoặc. Khi đọc, cách viết mơ
hồ (`1.250` có thể là 1250 hoặc 1,25 kiểu Anh) sinh nhiều ứng viên; số khớp nếu MỘT ứng viên khớp.
Dung sai = nửa đơn vị chữ số cuối mà người viết dùng (viết `12,3%` thì chấp nhận 12,25–12,35) cộng
dung sai tương đối trong config. Chỉ so độ lớn (bỏ dấu): từ "tăng/giảm" không được kiểm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_NUM = re.compile(
    r"(?<![\w.,])(?P<open>\()?(?P<sign>[-+−–])?"
    r"(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
    r"(?P<close>\))?(?![\w])"
)
_SUFFIX = re.compile(
    r"\s*(?P<suf>%|phần trăm|điểm\s*%|điểm phần trăm|điểm|nghìn tỷ|ngàn tỷ|tỷ|triệu|nghìn|ngàn"
    r"|ngày|lần)(?![\w])",
    re.IGNORECASE,
)
_SCALES = {
    "nghìn tỷ": Decimal(10) ** 12,
    "ngàn tỷ": Decimal(10) ** 12,
    "tỷ": Decimal(10) ** 9,
    "triệu": Decimal(10) ** 6,
    "nghìn": Decimal(10) ** 3,
    "ngàn": Decimal(10) ** 3,
}
_PCT = ("%", "phần trăm", "điểm phần trăm", "điểm")
BILLION = Decimal(10) ** 9
MILLION = Decimal(10) ** 6


@dataclass(frozen=True)
class Candidate:
    value: Decimal  # giá trị như người viết (chưa nhân thang tỷ/triệu)
    decimals: int  # số chữ số thập phân đã viết


@dataclass(frozen=True)
class NumberToken:
    text: str
    start: int
    end: int
    candidates: tuple[Candidate, ...]
    kind: str  # pct | days | times | scaled | plain
    scale: Decimal  # 1 nếu không có tỷ/triệu/nghìn
    negative: bool

    @property
    def is_year_like(self) -> bool:
        """Số nguyên 4 chữ số 1900–2199, không hậu tố: có thể là năm."""
        return (
            self.kind == "plain"
            and not self.negative
            and any(c.decimals == 0 and 1900 <= c.value <= 2199 for c in self.candidates)
            and re.fullmatch(r"\d{4}", self.text) is not None
        )


def _candidates(s: str) -> tuple[Candidate, ...]:
    out: list[Candidate] = []

    def add(digits: str, dec: int) -> None:
        c = Candidate(Decimal(digits), dec)
        if c not in out:
            out.append(c)

    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):  # Việt: 1.234.567,8
        whole, _, frac = s.partition(",")
        add(whole.replace(".", "") + ("." + frac if frac else ""), len(frac))
        if "," not in s and s.count(".") == 1:  # 1.250 kiểu Anh = 1,25
            add(s, len(s.split(".")[1]))
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):  # Anh: 1,234,567.8
        whole, _, frac = s.partition(".")
        add(whole.replace(",", "") + ("." + frac if frac else ""), len(frac))
        if "." not in s and s.count(",") == 1:  # 1,250 kiểu Việt = 1,25
            add(s.replace(",", "."), len(s.split(",")[1]))
    elif "," in s or "." in s:  # 12,3 hoặc 12.3
        sep = "," if "," in s else "."
        whole, frac = s.split(sep)
        add(f"{whole}.{frac}", len(frac))
    else:
        add(s, 0)
    return tuple(out)


def extract_numbers(text: str) -> list[NumberToken]:
    tokens: list[NumberToken] = []
    for m in _NUM.finditer(text):
        if bool(m.group("open")) != bool(m.group("close")):
            # "(12" hoặc "12)": dấu ngoặc của câu, không phải số âm kiểu kế toán
            negative = bool(m.group("sign")) and m.group("sign") not in "+"
            start = m.start("sign") if m.group("sign") else m.start("num")
            end = m.end("num")
        else:
            negative = bool(m.group("open")) or (bool(m.group("sign")) and m.group("sign") != "+")
            start, end = m.start(), m.end()
        kind, scale = "plain", Decimal(1)
        suf = _SUFFIX.match(text, m.end("num") if not m.group("close") else m.end())
        if suf:
            word = re.sub(r"\s+", " ", suf.group("suf").lower())
            if word.startswith("điểm") and "%" in word:
                word = "điểm phần trăm"
            if word in _PCT:
                kind = "pct"
            elif word in _SCALES:
                kind, scale = "scaled", _SCALES[word]
            elif word == "ngày":
                kind = "days"
            elif word == "lần":
                kind = "times"
            end = suf.end()
        tokens.append(
            NumberToken(
                text=m.group("num"),
                start=start,
                end=end,
                candidates=_candidates(m.group("num")),
                kind=kind,
                scale=scale,
                negative=negative,
            )
        )
    return tokens


# ---------------- định dạng


def format_vn(value: Decimal, decimals: int) -> str:
    q = Decimal(1).scaleb(-decimals)
    v = value.quantize(q, rounding=ROUND_HALF_UP)
    sign = "-" if v < 0 else ""
    whole, _, frac = format(abs(v), "f").partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return sign + grouped + ("," + frac if decimals > 0 else "")


def display(value: Decimal, unit: str) -> str:
    """Chuỗi hiển thị do code định dạng; LLM được yêu cầu chép nguyên chuỗi này."""
    if unit == "ratio":
        return format_vn(value * 100, 1) + "%"
    if unit == "ratio_diff":  # hiệu của hai tỷ lệ: 0,055 → 5,5 điểm %
        return format_vn(value * 100, 1) + " điểm %"
    if unit == "pp":
        return format_vn(value, 1) + " điểm %"
    if unit == "days":
        return format_vn(value, 1) + " ngày"
    if unit == "z":
        return format_vn(value, 2)
    if unit == "vnd":
        if abs(value) >= BILLION:
            return format_vn(value / BILLION, 1) + " tỷ đồng"
        return format_vn(value / MILLION, 1) + " triệu đồng"
    return format_vn(value, 2)


# ---------------- so khớp


def _targets(value: Decimal, unit: str, tok: NumberToken) -> list[Decimal]:
    """Giá trị của fact quy về không gian số của token (cùng thang với chữ người viết)."""
    v = abs(value)
    if tok.kind == "times":
        return []
    if tok.kind == "scaled":
        return [v / tok.scale] if unit == "vnd" else []
    if unit in ("ratio", "ratio_diff"):
        return [v * 100] if tok.kind == "pct" else [v, v * 100] if tok.kind == "plain" else []
    if unit == "pp":
        return [v] if tok.kind in ("pct", "plain") else []
    if unit == "days":
        return [v] if tok.kind in ("days", "plain") else []
    return [v] if tok.kind == "plain" else []


def _close(n: Decimal, dec: int, t: Decimal, rel_tol: Decimal) -> bool:
    half_unit = Decimal(5).scaleb(-(dec + 1))
    return abs(n - t) <= half_unit + rel_tol * abs(t)


def matches_value(tok: NumberToken, value: Decimal, unit: str, rel_tol: Decimal) -> bool:
    return any(
        _close(c.value, c.decimals, t, rel_tol)
        for t in _targets(value, unit, tok)
        for c in tok.candidates
    )


def matches_literal(tok: NumberToken, other: NumberToken, rel_tol: Decimal) -> bool:
    """Số trong khẳng định trùng một số viết trong văn bản nguồn (trích dẫn, lý do thiếu)."""
    if (tok.kind == "pct") != (other.kind == "pct"):
        return False
    return any(
        _close(c.value, c.decimals, o.value * other.scale / tok.scale, rel_tol)
        for c in tok.candidates
        for o in other.candidates
    )
