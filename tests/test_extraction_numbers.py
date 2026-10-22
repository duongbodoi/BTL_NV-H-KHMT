from decimal import Decimal

import pytest

from steel_xai.contracts import ValueStatus
from steel_xai.extraction.numbers import (
    REASON_BAD_FORMAT,
    REASON_DASH,
    REASON_MIXED_TEXT,
    REASON_MULTIPLE_NUMBERS,
    REASON_NO_DIGITS,
    parse_number,
)


@pytest.mark.parametrize(
    ("raw", "expected", "pct"),
    [
        ("1.234.567", Decimal("1234567"), False),
        ("46.192.292.081.813", Decimal("46192292081813"), False),
        ("(17.265.822.207)", Decimal("-17265822207"), False),
        ("-1.234", Decimal("-1234"), False),
        ("−5", Decimal("-5"), False),  # dấu trừ Unicode
        ("0", Decimal("0"), False),
        ("0,5", Decimal("0.5"), False),
        ("1.234,56", Decimal("1234.56"), False),
        ("1.751", Decimal("1751"), False),  # quy ước VN: dấu chấm là ngăn nghìn
        ("1234567", Decimal("1234567"), False),
        ("12,5%", Decimal("12.5"), True),
        ("(3,4%)", Decimal("-3.4"), True),
        ("(3,4)%", Decimal("-3.4"), True),
        ("  1.234 ", Decimal("1234"), False),
    ],
)
def test_vn_numbers_ok(raw, expected, pct):
    p = parse_number(raw)
    assert p.status == ValueStatus.OK
    assert p.value == expected
    assert p.is_percent is pct
    assert p.raw == raw  # nguyên văn
    assert p.reason is None


@pytest.mark.parametrize("raw", ["-", "–", "—", "−", " - "])
def test_dash_is_not_applicable_never_zero(raw):
    p = parse_number(raw)
    assert p.value is None
    assert p.status == ValueStatus.NOT_APPLICABLE
    assert p.reason == REASON_DASH


def test_dash_status_configurable_but_never_ok():
    assert parse_number("-", dash_status=ValueStatus.UNKNOWN).status == ValueStatus.UNKNOWN
    with pytest.raises(ValueError, match="OK"):
        parse_number("-", dash_status=ValueStatus.OK)


def test_custom_dash_tokens():
    assert parse_number("x", dash_tokens=["x"]).reason == REASON_DASH
    # "-" không còn là dấu gạch → không parse được, không thành 0
    p = parse_number("-", dash_tokens=["x"])
    assert p.status == ValueStatus.UNRESOLVED and p.value is None


@pytest.mark.parametrize("raw", ["", "   ", " "])
def test_empty_is_missing(raw):
    p = parse_number(raw)
    assert p.status == ValueStatus.MISSING
    assert p.value is None


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("9.1", REASON_BAD_FORMAT),  # số thuyết minh, nhóm nghìn sai
        ("1.23.456", REASON_BAD_FORMAT),
        ("1.2345", REASON_BAD_FORMAT),
        ("1,234,567", REASON_BAD_FORMAT),  # định dạng Anh: không đoán
        ("1,234.5", REASON_BAD_FORMAT),
        ("(1.234", REASON_BAD_FORMAT),
        ("1.234)", REASON_BAD_FORMAT),
        ("(-1.234)", REASON_BAD_FORMAT),
        ("--5", REASON_BAD_FORMAT),
        ("5%%", REASON_BAD_FORMAT),
        ("+5", REASON_BAD_FORMAT),
        ("12.021.443.836.074(1.420.214.803)", REASON_MULTIPLE_NUMBERS),  # OCR dính hai số
        ("(101.072.060.041)(120.214.412.008)", REASON_MULTIPLE_NUMBERS),
        ("1 234 567", REASON_MULTIPLE_NUMBERS),  # khoảng trắng: không tự ghép
        ("5 – 50 năm", REASON_MULTIPLE_NUMBERS),
        ("Đã điều chỉnh lại1.005", REASON_MIXED_TEXT),
        ("1.234 (i)", REASON_MIXED_TEXT),
        ("31/12/2024", REASON_MULTIPLE_NUMBERS),
        ("abc", REASON_NO_DIGITS),
        ("Giá gốc VND", REASON_NO_DIGITS),
    ],
)
def test_unparseable_is_unresolved(raw, reason):
    p = parse_number(raw)
    assert p.status == ValueStatus.UNRESOLVED
    assert p.value is None
    assert p.reason == reason
    assert p.raw == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234567.89", Decimal("1234567.89")),
        ("-12", Decimal("-12")),
        ("+7", Decimal("7")),
        ("1e3", Decimal("1E+3")),
        (".5", Decimal("0.5")),
        ("(12)", Decimal("-12")),
    ],
)
def test_plain_numbers_ok(raw, expected):
    p = parse_number(raw, "plain")
    assert p.status == ValueStatus.OK
    assert p.value == expected


@pytest.mark.parametrize("raw", ["1.234.567", "1 234", "1,5", "abc", "inf", "nan"])
def test_plain_unparseable(raw):
    p = parse_number(raw, "plain")
    assert p.status == ValueStatus.UNRESOLVED and p.value is None


def test_plain_missing_tokens():
    p = parse_number("NaN", "plain", missing_tokens=["nan"])
    assert p.status == ValueStatus.MISSING and p.value is None


def test_invalid_format_raises():
    with pytest.raises(ValueError):
        parse_number("1", "en")  # type: ignore[arg-type]


def test_to_dict_serializes_decimal_as_string():
    d = parse_number("(1.234,5)").to_dict()
    assert d == {
        "raw": "(1.234,5)",
        "value": "-1234.5",
        "status": "OK",
        "is_percent": False,
        "reason": None,
    }


@pytest.mark.parametrize("raw", ["0.500", "00.123", "01.234"])
def test_leading_zero_thousand_group_unresolved(raw):
    p = parse_number(raw)
    assert p.status == ValueStatus.UNRESOLVED and p.value is None


def test_three_decimal_digits_flagged_ambiguous():
    p = parse_number("1,234")
    assert p.value == Decimal("1.234") and p.reason == "ambiguous_separator"
    assert parse_number("99,999%").reason is None  # tỷ lệ sở hữu: không mơ hồ
    assert parse_number("1.234,567").reason is None  # có nhóm nghìn: rõ ràng


@pytest.mark.parametrize("raw", ["١٢٣", "１２３"])
def test_non_ascii_digits_unresolved(raw):
    assert parse_number(raw).status == ValueStatus.UNRESOLVED


@pytest.mark.parametrize("raw", ["‐", "‑"])
def test_unicode_hyphens_are_dashes(raw):
    p = parse_number(raw)
    assert p.status == ValueStatus.NOT_APPLICABLE and p.value is None
