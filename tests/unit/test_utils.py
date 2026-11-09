from datetime import date
from decimal import Decimal

from app.utils import (is_missing_cell, name_key, parse_dates, parse_int_vn, parse_pct_vn)


def test_share_counts_with_thousands_separators():
    assert parse_int_vn("1.234.567") == 1234567
    assert parse_int_vn("1,234,567") == 1234567
    assert parse_int_vn("12 000") == 12000
    assert parse_int_vn("0") == 0
    assert parse_int_vn("1.234,5") is None      # not an integer count
    assert parse_int_vn("") is None and parse_int_vn("-") is None


def test_large_share_counts_exact():
    assert parse_int_vn("3.500.000.000") == 3_500_000_000


def test_percentages_vietnamese_decimal_comma():
    assert parse_pct_vn("0,05%") == Decimal("0.05")
    assert parse_pct_vn("2.5 %") == Decimal("2.5")
    assert parse_pct_vn("0,00%") == Decimal("0.00") and parse_pct_vn("0,00%") == 0
    assert parse_pct_vn("1.234,56") == Decimal("1234.56")
    assert parse_pct_vn("abc") is None and parse_pct_vn("") is None


def test_missing_cells_are_not_zero():
    for c in ("", "-", "—", "N/A", None):
        assert is_missing_cell(c)
    assert not is_missing_cell("0")


def test_dates_numeric_and_textual():
    assert parse_dates("tại ngày 17/03/2025")[0][0] == date(2025, 3, 17)
    assert parse_dates("ngày 5 tháng 4 năm 2024")[0][0] == date(2024, 4, 5)
    assert parse_dates("31/02/2024") == []


def test_name_key_does_not_fuzzy_merge():
    assert name_key("Ông  Nguyễn Văn An") == name_key("nguyễn văn an")
    assert name_key("Nguyễn Văn An") != name_key("Nguyễn Văn Anh")
