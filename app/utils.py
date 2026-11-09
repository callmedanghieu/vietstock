"""Text, number and date helpers for Vietnamese annual reports."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

NOT_DISCLOSED = "Not explicitly disclosed"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", nfc(s)).strip()


def name_key(name: str) -> str:
    """Identity key for a person: NFC + case-fold + whitespace only. NO fuzzy merging."""
    n = collapse(name)
    n = re.sub(r"^(ông|bà|mr\.?|mrs\.?|ms\.?)\s+", "", n, flags=re.I)
    return n.casefold()


def strip_diacritics(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def fold(s: str) -> str:
    return re.sub(r"\s+", " ", strip_diacritics(s).casefold()).strip()


_MISSING = {"", "-", "–", "—", "n/a", "na", "nil", "...", "x"}


def is_missing_cell(s: str | None) -> bool:
    return s is None or collapse(s).casefold() in _MISSING


def parse_int_vn(s: str | None) -> int | None:
    """Share counts: '1.234.567' / '1,234,567' / '1 234 567' -> int. Returns None if not a clean integer."""
    if s is None:
        return None
    t = collapse(s).replace(" ", " ")
    t = re.sub(r"\s*(cp|cổ phiếu|cổ phần)\s*$", "", t, flags=re.I)
    if not re.fullmatch(r"\d{1,3}(?:[.,\s]\d{3})+|\d+", t):
        return None
    return int(re.sub(r"[.,\s]", "", t))


def parse_pct_vn(s: str | None) -> Decimal | None:
    """Percentages as percentage values (2.5 == 2.5%). Handles '0,05%', '0.05 %', '1.234,56'."""
    if s is None:
        return None
    t = collapse(s).replace("%", "").strip()
    if not t or not re.fullmatch(r"[\d.,\s]+", t):
        return None
    t = t.replace(" ", "")
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    else:
        t = t.replace(",", ".")
        if t.count(".") > 1:
            return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


_DATE_RE = re.compile(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4})")
_DATE_TXT = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.I)


def parse_dates(text: str) -> list[tuple[date, int, int]]:
    """All valid dates in text with their (start, end) offsets."""
    out = []
    for rx in (_DATE_RE, _DATE_TXT):
        for m in rx.finditer(text or ""):
            d, mo, y = (int(g) for g in m.groups())
            try:
                out.append((date(y, mo, d), m.start(), m.end()))
            except ValueError:
                continue
    return sorted(out, key=lambda x: x[1])


def parse_one_date(s: str | None) -> date | None:
    r = parse_dates(s or "")
    return r[0][0] if r else None


def pct_str(d: Decimal | None) -> str:
    return "" if d is None else format(d.normalize(), "f")
