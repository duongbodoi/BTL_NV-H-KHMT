"""Định dạng và đọc số trong báo cáo tiếng Việt (spec mục 13, 16).

Chỉ nhận định dạng Việt: dấu chấm ngăn nghìn (`1.234.567`), dấu phẩy thập phân (`12,3`), số âm có
dấu trừ hoặc trong ngoặc. Cách viết kiểu Anh (`12.3`, `1,234.5`) không có cách đọc hợp lệ → bị
coi là sai, LLM được yêu cầu viết lại. `1,250` đọc là 1,25 (không phải 1250).

So khớp với một số do code tính: phải cùng loại đơn vị (`%` với tỷ lệ, `điểm %` với hiệu tỷ lệ/điểm
phần trăm, `ngày`, `tỷ/triệu/nghìn` với tiền), lệch không quá nửa đơn vị chữ số cuối, và không được
viết THÔ hơn chuỗi hiển thị do code định dạng (viết "1 nghìn tỷ" cho 1.400,0 tỷ là sai). Dấu được
kiểm khi số có dấu trừ/ngoặc; từ "tăng/giảm" đứng trước số do `checks` kiểm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_NUM = re.compile(
    r"(?<![\w])(?P<open>\()?(?P<sign>[-+−–])?"
    r"(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
    r"(?P<close>\))?(?!\d)"
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
_KINDS = {
    "%": "pct",
    "phần trăm": "pct",
    "điểm phần trăm": "pp",
    "điểm": "pp",
    "ngày": "days",
    "lần": "times",
}
BILLION = Decimal(10) ** 9
MILLION = Decimal(10) ** 6
HUNDREDTH = Decimal("0.01")


@dataclass(frozen=True)
class Candidate:
    value: Decimal  # giá trị như người viết (chưa nhân thang tỷ/triệu)
    decimals: int  # số chữ số thập phân đã viết


@dataclass(frozen=True)
class NumberToken:
    text: str
    start: int
    end: int
    candidates: tuple[Candidate, ...]  # rỗng = không phải định dạng Việt hợp lệ
    kind: str  # pct | pp | days | times | scaled | plain
    scale: Decimal  # 1 nếu không có tỷ/triệu/nghìn
    negative: bool

    @property
    def is_year_like(self) -> bool:
        """Số nguyên 4 chữ số 1900–2199, không hậu tố, không dấu: có thể là năm."""
        return (
            self.kind == "plain"
            and not self.negative
            and re.fullmatch(r"(19|2[01])\d{2}", self.text) is not None
        )


def _candidates(s: str) -> tuple[Candidate, ...]:
    """Cách đọc kiểu Việt; rỗng nếu chuỗi chỉ hợp lệ theo kiểu Anh."""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):  # 1.234.567,8
        whole, _, frac = s.partition(",")
        digits = whole.replace(".", "") + ("." + frac if frac else "")
        return (Candidate(Decimal(digits), len(frac)),)
    if re.fullmatch(r"\d+,\d+", s):  # 12,3 và cả 1,250 (= 1,25)
        whole, frac = s.split(",")
        return (Candidate(Decimal(f"{whole}.{frac}"), len(frac)),)
    if re.fullmatch(r"\d+", s):
        return (Candidate(Decimal(s), 0),)
    return ()  # 12.3, 1,234,567, 1,234.5: kiểu Anh


def extract_numbers(text: str) -> list[NumberToken]:
    tokens: list[NumberToken] = []
    for m in _NUM.finditer(text):
        sign = m.group("sign") or ""
        if bool(m.group("open")) != bool(m.group("close")):
            # "(12" hoặc "12)": dấu ngoặc của câu, không phải số âm kiểu kế toán
            negative = sign not in ("", "+")
            start = m.start("sign") if sign else m.start("num")
            num_end = m.end("num")
        else:
            negative = bool(m.group("open")) or sign not in ("", "+")
            start, num_end = m.start(), m.end()
        kind, scale, end = "plain", Decimal(1), num_end
        suf = _SUFFIX.match(text, num_end)
        if suf:
            word = re.sub(r"\s+", " ", suf.group("suf").lower())
            if word.startswith("điểm") and "%" in word:
                word = "điểm phần trăm"
            if word in _SCALES:
                kind, scale = "scaled", _SCALES[word]
            else:
                kind = _KINDS[word]
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
        if abs(value) >= MILLION:
            return format_vn(value / MILLION, 1) + " triệu đồng"
        return format_vn(value, 0) + " đồng"
    return format_vn(value, 2)


# ---------------- so khớp


def _multipliers(tok: NumberToken, unit: str) -> list[Decimal]:
    """Các hệ số m sao cho giá trị của fact ≈ số người viết × m (cùng đơn vị)."""
    k = tok.kind
    if unit == "ratio":
        return [HUNDREDTH] if k == "pct" else [Decimal(1), HUNDREDTH] if k == "plain" else []
    if unit == "ratio_diff":
        return [HUNDREDTH] if k == "pp" else [Decimal(1), HUNDREDTH] if k == "plain" else []
    if unit == "pp":
        return [Decimal(1)] if k in ("pp", "plain") else []
    if unit == "days":
        return [Decimal(1)] if k in ("days", "plain") else []
    if unit == "vnd":
        return [tok.scale] if k in ("scaled", "plain") else []
    return [Decimal(1)] if k == "plain" else []


def _steps(tok: NumberToken, value: Decimal, unit: str, rel_tol: Decimal) -> list[Decimal]:
    """Bước làm tròn (theo đơn vị của fact) của mọi cách đọc token khớp `value`."""
    out = []
    v = abs(value)
    for c in tok.candidates:
        for m in _multipliers(tok, unit):
            step = Decimal(1).scaleb(-c.decimals) * m
            if abs(c.value * m - v) <= step / 2 + rel_tol * v:
                out.append(step)
    return out


def display_step(value: Decimal, unit: str) -> Decimal:
    """Bước làm tròn của chuỗi hiển thị do code định dạng (mức chi tiết tối thiểu phải viết)."""
    (tok,) = extract_numbers(display(value, unit))
    return min(_steps(tok, value, unit, Decimal(0)))


def matches_value(
    tok: NumberToken,
    value: Decimal,
    unit: str,
    rel_tol: Decimal,
    max_step: Decimal | None = None,
) -> bool:
    """Token khớp giá trị do code tính (độ lớn; dấu kiểm khi token có dấu trừ/ngoặc)."""
    if tok.negative and value >= 0:
        return False
    steps = _steps(tok, value, unit, rel_tol)
    return any(max_step is None or s <= max_step for s in steps)


def matches_literal(tok: NumberToken, other: NumberToken, rel_tol: Decimal) -> bool:
    """Số trong khẳng định trùng một số viết trong văn bản nguồn (trích dẫn, lý do thiếu): phải
    cùng loại đơn vị và cùng thang, không thô hơn; "lần" không bao giờ khớp."""
    if tok.kind != other.kind or tok.kind == "times" or tok.scale != other.scale:
        return False
    return any(
        c.decimals >= o.decimals
        and abs(c.value - o.value) <= Decimal(5).scaleb(-(c.decimals + 1)) + rel_tol * abs(o.value)
        for c in tok.candidates
        for o in other.candidates
    )
