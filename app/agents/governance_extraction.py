"""Agent 3 - Governance Extraction (deterministic rules; an LLM could only *propose* candidates upstream).

Never invents dates or ownership: unknown => None with an explanatory note. Missing is never zero.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from ..config import Settings
from ..models import (AggregateOwnership, BodyStructure, Evidence, ExtractionResult, Independence,
                      MemberObservation, ParsedDocument, Status)
from ..services.calculations import classify_zero_ownership
from ..utils import (NOT_DISCLOSED, collapse, fold, is_missing_cell, name_key, parse_dates, parse_int_vn,
                     parse_pct_vn)

BODY_KW = {"HDQT": ["hoi dong quan tri", "hdqt"], "BKS": ["ban kiem soat", "bks"]}
FORMER_KW = ["da mien nhiem", "da thoi", "da tu nhiem", "thanh vien cu", "former", "khong con la", "da het nhiem",
             "mien nhiem trong nam", "thoi giu chuc vu"]
BKS_ABSENT = ["khong co ban kiem soat", "khong to chuc ban kiem soat", "khong thanh lap ban kiem soat",
              "khong thanh lap ban kiem soat", "thay the ban kiem soat", "mo hinh khong co ban kiem soat"]
APPT_KW = ["bo nhiem", "duoc bau", "bau lam", "tai cu"]
DEP_KW = ["mien nhiem", "bai nhiem", "tu nhiem", "thoi giu", "het nhiem ky", "thoi nhiem", "ket thuc nhiem ky"]
TITLE_RX = (r"(?:Chủ tịch HĐQT|Phó Chủ tịch HĐQT|Chủ tịch Hội đồng quản trị|Thành viên HĐQT độc lập|"
            r"Thành viên độc lập Hội đồng quản trị|Thành viên HĐQT|Thành viên Hội đồng quản trị|"
            r"Trưởng Ban [Kk]iểm soát|Trưởng BKS|Thành viên (?:Ban [Kk]iểm soát|BKS)|Kiểm soát viên)")


# ---------------------------------------------------------------- headers
def _classify_header(cell: str) -> str | None:
    f = fold(cell)
    if not f:
        return None
    if any(k in f for k in ("bo nhiem", "appoint")) and "mien" not in f:
        return "appt"
    if any(k in f for k in DEP_KW + ["resign"]):
        return "depart"
    if "ty le" in f or "%" in f or "percent" in f:
        return "pct"
    if any(k in f for k in ("so co phieu", "so luong co phieu", "so cp", "so co phan", "shares", "so luong")):
        return "shares"
    if "chuc vu" in f or "chuc danh" in f or f in ("position", "title"):
        return "title"
    if f in ("stt", "no.", "no", "tt"):
        return "stt"
    if f.startswith(("ho va ten", "ho ten", "ten", "full name", "name")):
        return "name"
    return None


def _category(header: str) -> str:
    f = fold(header)
    if "lien quan" in f or "related" in f:
        return "related_person"
    if "dai dien" in f or "representative" in f:
        return "representative"
    return "personal"


def _column_categories(cmap: dict, hdr: list[str]) -> dict[int, str]:
    """Ownership columns inherit the category of the nearest preceding explicitly-labelled ownership column."""
    cats, cur = {}, "personal"
    for ci in sorted(i for i, k in cmap.items() if k in ("shares", "pct")):
        f = fold(hdr[ci])
        explicit = ("lien quan" in f or "related" in f or "dai dien" in f or "representative" in f or "ca nhan" in f)
        if explicit:
            cur = _category(hdr[ci])
        cats[ci] = cur
    return cats


def _merge_header_rows(r1: list[str], r2: list[str]) -> list[str] | None:
    if any(re.search(r"\d", c) for c in r2) or not any(r2):
        return None
    sub = ("ca nhan", "dai dien", "lien quan", "so luong", "ty le", "%", "co phieu")
    if not any(any(s in fold(c) for s in sub) for c in r2):
        return None
    out, last = [], ""
    for a, b in zip(r1, r2):
        last = a or last if (a or b) else last
        out.append(collapse(f"{a or (last if b else '')} {b}"))
    return out


def find_header(rows: list[list[str]]):
    """-> (col_map, header_cells, first_data_row_index) or None."""
    for i, row in enumerate(rows[:4]):
        cells = row
        used = 1
        if i + 1 < len(rows):
            m = _merge_header_rows(row, rows[i + 1])
            if m:
                cells, used = m, 2
        cmap: dict[int, str] = {}
        for ci, c in enumerate(cells):
            k = _classify_header(c)
            if k:
                cmap[ci] = k
        if "name" in cmap.values() and (("title" in cmap.values()) or ("shares" in cmap.values())
                                       or ("pct" in cmap.values())):
            return cmap, cells, i + used
    return None


# ---------------------------------------------------------------- helpers
def _is_person_name(s: str) -> bool:
    s = collapse(s)
    s = re.sub(r"^(Ông|Bà|Mr\.?|Mrs\.?|Ms\.?)\s+", "", s, flags=re.I)
    if len(s) < 4 or re.search(r"\d", s):
        return False
    words = s.split()
    if len(words) < 2 or len(words) > 7:
        return False
    if any(w in fold(s) for w in ("tong cong", "tong so", "total", "ho va ten", "cong ty", "stt")):
        return False
    return bool(re.fullmatch(r"[^\W\d_]+(?:[ .'\-][^\W\d_]+)*\.?", s))


def _clean_name(s: str) -> str:
    return collapse(re.sub(r"^(Ông|Bà|Mr\.?|Mrs\.?|Ms\.?)\s+", "", collapse(s), flags=re.I))


def _date_after_kw(text: str, kws: list[str]) -> date | None:
    f = fold(text)
    # fold() only removes diacritics (1:1 length for NFC input except đ->d which is also 1:1)
    for kw in kws:
        for m in re.finditer(re.escape(kw), f):
            window = text[m.end(): m.end() + 45]
            ds = parse_dates(window)
            if ds:
                return ds[0][0]
    return None


def _independence(title: str | None) -> Independence:
    f = fold(title or "")
    if not f:
        return Independence.UNKNOWN
    if "khong doc lap" in f or "non-independent" in f:
        return Independence.FALSE
    if "doc lap" in f or "independent" in f:
        return Independence.TRUE
    return Independence.UNKNOWN


def _body_from_title(title: str) -> str | None:
    f = fold(title)
    if "kiem soat" in f or "bks" in f:
        return "BKS"
    if "hdqt" in f or "hoi dong quan tri" in f or "chu tich" in f or "doc lap" in f:
        return "HDQT"
    return None


def _anchor(text: str, name: str) -> int:
    i = text.find(name)
    return i if i >= 0 else fold(text).find(fold(name)) if name else -1


def _context_body(text: str, anchor: int) -> str | None:
    f = fold(text[: anchor if anchor >= 0 else len(text)])
    best, pos = None, -1
    for b, kws in BODY_KW.items():
        for k in kws:
            p = f.rfind(k)
            if p > pos:
                best, pos = b, p
    return best


def _former_context(text: str, anchor: int) -> bool:
    if anchor < 0:
        return False
    win = fold(text[max(0, anchor - 220): anchor])
    last_former = max((win.rfind(k) for k in FORMER_KW), default=-1)
    return last_former >= 0


DATE_CUES = ("ngay", "tai", "den", "as of", "tinh den", "thoi diem", "tinh toi", "cap nhat", "at ")


def find_effective_date(text: str, anchor: int) -> tuple[str, str] | None:
    """Closest 'as of' date at/just before the table; else first after. -> (iso, context)"""
    cands = []
    for d, s, e in parse_dates(text):
        ctx = fold(text[max(0, s - 45): s])
        if any(c in ctx for c in DATE_CUES):
            cands.append((d, s, e))
    if not cands:
        return None
    anchor = anchor if anchor >= 0 else len(text)
    before = [c for c in cands if c[2] <= anchor]
    pick = before[-1] if before else cands[0]
    return pick[0].isoformat(), text[max(0, pick[1] - 40): pick[2]]


# ---------------------------------------------------------------- extraction core
class _Ctx:
    def __init__(self, settings: Settings, ticker: str, year: int, doc: ParsedDocument):
        self.s, self.ticker, self.year, self.doc = settings, ticker, year, doc
        self.members: list[MemberObservation] = []
        self.aggs: list[AggregateOwnership] = []
        self.warnings: list[str] = []
        self.dash_missing = settings.get("ownership.dash_means_missing", True)

    def ev(self, page: int, quote: str, ref: str | None = None) -> Evidence:
        return Evidence(document_id=self.doc.document_id, page=page, quote=collapse(quote)[:400], table_ref=ref)


def _row_to_member(cx: _Ctx, page, row: list[str], cmap: dict, hdr: list[str], body: str,
                   former: bool, eff: str | None, tref: str):
    def col(kind):
        return [i for i, k in cmap.items() if k == kind]
    cells = row + [""] * (len(hdr) - len(row))
    nm = next((cells[i] for i in col("name") if i < len(cells) and cells[i]), "")
    if not _is_person_name(nm):
        return None
    title = next((cells[i] for i in col("title") if i < len(cells) and cells[i]), None)
    m = MemberObservation(ticker=cx.ticker, reporting_year=cx.year, person_name=_clean_name(nm),
                          governance_body=body, reported_title=title or None, in_former_section=former)
    m.independence = _independence(title) if body == "HDQT" else Independence.UNKNOWN
    rowtxt = " | ".join(c for c in cells if c)
    m.evidence = cx.ev(page.page, rowtxt, tref)

    # ownership columns (possibly several categories)
    sh_cols = col("shares")
    pc_cols = col("pct")
    seen_personal = False
    cats = _column_categories(cmap, hdr)
    for kind, cols in (("shares", sh_cols), ("pct", pc_cols)):
        for ci in cols:
            raw = cells[ci] if ci < len(cells) else ""
            cat = cats[ci]
            if is_missing_cell(raw):
                if raw.strip() in ("-", "–", "—") and cat == "personal":
                    m.review_notes.append(f"INFO: '-' in ownership cell treated as MISSING, not zero ({kind})")
                continue
            if cat != "personal":
                m.review_notes.append(f"INFO: {cat} holding '{raw}' ({kind}) kept separate, not personal")
                if not seen_personal and m.reported_ownership_category == "personal" and not (
                        any(cats[c] == "personal" for c in sh_cols + pc_cols)):
                    m.reported_ownership_category = cat
                continue
            seen_personal = True
            if kind == "shares":
                v = parse_int_vn(raw)
                m.ownership_raw_shares = raw
                if v is None:
                    m.review_notes.append(f"REVIEW: share cell '{raw}' not a clean integer")
                else:
                    m.personal_shares_owned = v
            else:
                v = parse_pct_vn(raw)
                m.ownership_raw_pct = raw
                if v is None:
                    m.review_notes.append(f"REVIEW: percentage cell '{raw}' not parseable")
                else:
                    m.personal_ownership_percentage = v

    # dates
    for ci in col("appt"):
        if ci < len(cells) and cells[ci]:
            ds = parse_dates(cells[ci])
            m.appointment_date = ds[0][0] if ds else m.appointment_date
    for ci in col("depart"):
        if ci < len(cells) and cells[ci]:
            ds = parse_dates(cells[ci])
            m.departure_date = ds[0][0] if ds else m.departure_date
    txt = " ".join(cells)
    m.appointment_date = m.appointment_date or _date_after_kw(txt, APPT_KW)
    m.departure_date = m.departure_date or _date_after_kw(txt, DEP_KW)
    m.membership_effective_date = eff or NOT_DISCLOSED
    m.ownership_effective_date = eff or NOT_DISCLOSED
    return m


def _process_tables(cx: _Ctx, pages) -> tuple[set, dict]:
    pages_with_rows: dict[str, set] = defaultdict(set)
    last_hdr = None
    for page in pages:
        for ti, tbl in enumerate(page.tables, start=1):
            h = find_header(tbl)
            if h:
                cmap, hdr, start = h
                last_hdr = (cmap, hdr, len(tbl[0]))
            elif last_hdr and len(tbl[0]) == last_hdr[2] and any(_is_person_name(c) for c in tbl[0]):
                cmap, hdr, start = last_hdr[0], last_hdr[1], 0   # table continued from previous page
            else:
                continue
            name_idx = next(i for i, k in cmap.items() if k == "name")
            rows = tbl[start:]
            first_name = next((r[name_idx] for r in rows if name_idx < len(r) and _is_person_name(r[name_idx])), "")
            anchor = _anchor(page.text, _clean_name(first_name)) if first_name else -1
            body = _context_body(page.text, anchor)
            titles = [r[i] for r in rows for i, k in cmap.items() if k == "title" and i < len(r) and r[i]]
            tb = Counter(b for b in map(_body_from_title, titles) if b)
            if tb:
                body = tb.most_common(1)[0][0] if not body or len(tb) == 1 else body
            if body not in ("HDQT", "BKS"):
                cx.warnings.append(f"p{page.page} table {ti}: governance body undetermined, skipped")
                continue
            former = _former_context(page.text, anchor)
            eff = find_effective_date(page.text, anchor)
            for r in rows:
                label = fold(" ".join(r))
                if label.startswith(("tong", "total")) or " tong cong" in " " + label[:25]:
                    pct_cells = [r[i] for i, k in cmap.items() if k == "pct" and i < len(r)]
                    pv = next((parse_pct_vn(c) for c in pct_cells if parse_pct_vn(c) is not None), None)
                    if pv is not None and body == "HDQT":
                        cx.aggs.append(AggregateOwnership(
                            ticker=cx.ticker, reporting_year=cx.year, reported_pct=pv, raw_text=" | ".join(r),
                            covers_all_active_board=not former, effective_date=eff[0] if eff else NOT_DISCLOSED,
                            evidence=cx.ev(page.page, " | ".join(r), f"table {ti} total row")))
                    continue
                m = _row_to_member(cx, page, r, cmap, hdr, body, former, eff[0] if eff else None, f"table {ti}")
                if m:
                    cx.members.append(m)
                    pages_with_rows[body].add(page.page)
    return set(), pages_with_rows


_LINE_RX = re.compile(
    r"^\s*(?:\d{1,2}[.)]?\s+)?(?P<name>(?:Ông|Bà)?\s*[^\W\d_][^\d|]{2,40}?)\s+(?P<title>" + TITLE_RX +
    r")\s+(?P<shares>[\d.,]+|-)\s+(?P<pct>[\d.,]+\s*%?|-)\s*$")


def _process_text_fallback(cx: _Ctx, pages, done: dict) -> None:
    for page in pages:
        for ln in page.text.splitlines():
            m = _LINE_RX.match(collapse(ln))
            if not m:
                continue
            title = collapse(m["title"])
            body = _body_from_title(title)
            if not body or page.page in done.get(body, set()):
                continue
            anchor = page.text.find(ln.strip())
            name = _clean_name(m["name"])
            if not _is_person_name(name):
                continue
            eff = find_effective_date(page.text, anchor)
            mo = MemberObservation(ticker=cx.ticker, reporting_year=cx.year, person_name=name,
                                   governance_body=body, reported_title=title,
                                   in_former_section=_former_context(page.text, anchor))
            mo.independence = _independence(title) if body == "HDQT" else Independence.UNKNOWN
            for kind, raw in (("shares", m["shares"]), ("pct", m["pct"])):
                if is_missing_cell(raw):
                    continue
                if kind == "shares":
                    mo.ownership_raw_shares, mo.personal_shares_owned = raw, parse_int_vn(raw)
                else:
                    mo.ownership_raw_pct, mo.personal_ownership_percentage = raw, parse_pct_vn(raw)
            mo.membership_effective_date = mo.ownership_effective_date = eff[0] if eff else NOT_DISCLOSED
            mo.evidence = cx.ev(page.page, ln, "text line (no table detected)")
            mo.review_notes.append("REVIEW: extracted from free text layout, not a ruled table")
            cx.members.append(mo)


_AGG_RX = re.compile(
    r"(?P<span>(?:các\s+)?(?:thành viên\s+)?(?:HĐQT|Hội đồng quản trị)[^.\n]{0,90}?(?:nắm giữ|sở hữu)[^.\n]{0,70}?"
    r"(?P<pct>\d[\d.,]*)\s*%)", re.I)


_AGG_RX2 = re.compile(
    r"(?P<span>(?:tổng\s+)?tỷ lệ\s+(?:sở hữu|nắm giữ)[^.\n\d]{0,40}?(?:các\s+thành\s+viên\s+)?"
    r"(?:HĐQT|Hội đồng quản trị)[^.\n\d]{0,30}?(?P<pct>\d[\d.,]*)\s*%)", re.I)


def _process_aggregates_text(cx: _Ctx, pages) -> None:
    for page in pages:
        lines = [collapse(l) for l in page.text.splitlines() if l.strip()]
        units = lines + [f"{a} {b}" for a, b in zip(lines, lines[1:])]   # sentences may wrap onto the next line
        seen = set()
        for u in units:
            for m in list(_AGG_RX.finditer(u)) + list(_AGG_RX2.finditer(u)):
                span = m["span"]
                if span in seen or "kiểm soát" in span.lower() or "ban giám đốc" in span.lower():
                    continue
                seen.add(span)
                pv = parse_pct_vn(m["pct"])
                if pv is None:
                    continue
                covers = bool(re.search(r"\btổng\b|toàn bộ|\bcác thành viên\b|cả hội đồng", span, re.I))
                eff = find_effective_date(u, m.start())
                cx.aggs.append(AggregateOwnership(
                    ticker=cx.ticker, reporting_year=cx.year, reported_pct=pv, raw_text=span,
                    covers_all_active_board=covers, effective_date=eff[0] if eff else NOT_DISCLOSED,
                    evidence=cx.ev(page.page, span, "narrative"),
                    review_notes=[] if covers else ["INFO: narrative figure does not clearly cover all Board members"]))


# ---------------------------------------------------------------- membership / dedupe
def _cutoff(settings: Settings, year: int, disclosed: str | None, policy_key: str) -> date | None:
    pol = settings.get(policy_key, "report_disclosed")
    if pol == "fiscal_year_end":
        return date(year, 12, 31)
    if disclosed and disclosed != NOT_DISCLOSED:
        return date.fromisoformat(disclosed)
    return None


def determine_activity(settings: Settings, m: MemberObservation) -> None:
    cutoff = _cutoff(settings, m.reporting_year, m.membership_effective_date, "measurement.membership_date_policy")
    if settings.get("measurement.membership_date_policy") == "fiscal_year_end" and \
            m.membership_effective_date not in (None, NOT_DISCLOSED, f"{m.reporting_year}-12-31"):
        m.review_notes.append(f"REVIEW: report discloses composition as of {m.membership_effective_date} but "
                              "configured cutoff is fiscal-year end")
    if m.in_former_section:
        m.active_at_cutoff = False
        m.review_notes.append("INFO: listed in a former/resigned-members section; excluded")
        return
    if cutoff is None:
        if m.departure_date or m.appointment_date:
            m.active_at_cutoff = None
            m.review_notes.append("REVIEW: appointment/departure dates present but no composition date is disclosed")
        else:
            m.active_at_cutoff = True
            m.review_notes.append("INFO: listed in current composition; composition effective date not explicitly disclosed")
        return
    act = True
    if m.departure_date and m.departure_date <= cutoff:
        act = False
    if m.appointment_date and m.appointment_date > cutoff:
        act = False
    m.active_at_cutoff = act
    if act and m.departure_date and m.departure_date > cutoff:
        m.review_notes.append(f"INFO: departure {m.departure_date} is after cutoff {cutoff}; active at cutoff")
    if not act:
        m.review_notes.append(f"INFO: not active at cutoff {cutoff} (appointed {m.appointment_date}, departed {m.departure_date})")
    # attribution checks (spec section 4 rules 2,5,6)
    fy = m.reporting_year
    if cutoff.year > fy + settings.get("measurement.max_year_gap", 1) or cutoff.year < fy:
        m.review_notes.append(f"REVIEW: composition date {cutoff} is far from fiscal year {fy}; different reporting period?")


def _merge(cx: _Ctx) -> list[MemberObservation]:
    groups: dict[str, list[MemberObservation]] = defaultdict(list)
    for m in cx.members:
        groups[m.key].append(m)
    out = []
    for key, g in groups.items():
        g.sort(key=lambda x: (x.in_former_section, x.evidence.page if x.evidence else 0))
        p = g[0]
        for o in g[1:]:
            where = f"p{o.evidence.page}" if o.evidence else "?"
            if o.in_former_section != p.in_former_section:
                p.review_notes.append(f"REVIEW: also listed as former member ({where}); conflicting status")
            if (p.personal_shares_owned is not None and o.personal_shares_owned is not None
                    and p.personal_shares_owned != o.personal_shares_owned) or \
               (p.personal_ownership_percentage is not None and o.personal_ownership_percentage is not None
                    and p.personal_ownership_percentage != o.personal_ownership_percentage):
                p.review_notes.append(f"REVIEW: conflicting ownership between tables (p{p.evidence.page if p.evidence else '?'} "
                                      f"vs {where}); first kept, none overwritten")
            else:
                for f in ("personal_shares_owned", "personal_ownership_percentage"):
                    if getattr(p, f) is None and getattr(o, f) is not None and o.reported_ownership_category == "personal":
                        setattr(p, f, getattr(o, f))
                        p.review_notes.append(f"INFO: {f} filled from duplicate listing at {where}")
            p.review_notes.append(f"INFO: duplicate listing at {where} merged (counted once)")
        out.append(p)
    return out


def extract(settings: Settings, ticker: str, year: int, doc: ParsedDocument) -> ExtractionResult:
    cx = _Ctx(settings, ticker, year, doc)
    st = BodyStructure()
    if doc.is_scanned and not doc.ocr_used:
        return ExtractionResult(ticker=ticker, year=year, status="UNAVAILABLE", warnings=doc.warnings + [
            "Scanned PDF without OCR: no governance data extracted (not zero, not missing-as-zero)"])
    pages = [p for p in doc.pages if p.score > 0]
    # structure: BKS absent?
    for p in doc.pages:
        f = fold(collapse(p.text))
        for k in BKS_ABSENT:
            i = f.find(k)
            if i >= 0:
                st.bks_absent_declared, st.bks_absent_quote = True, collapse(p.text)[max(0, i - 40): i + 120]
                break
        if st.bks_absent_declared:
            break
    _, done = _process_tables(cx, pages)
    _process_text_fallback(cx, pages, done)
    _process_aggregates_text(cx, pages)
    members = _merge(cx)
    # composition date per body = most common disclosed date
    for body in ("HDQT", "BKS"):
        ds = Counter(m.membership_effective_date for m in members
                     if m.governance_body == body and m.membership_effective_date != NOT_DISCLOSED)
        if body == "HDQT" and ds:
            st.membership_date = ds.most_common(1)[0][0]
    for m in members:
        if m.membership_effective_date == NOT_DISCLOSED and m.governance_body == "HDQT" and st.membership_date:
            pass  # never infer a date for a row whose table disclosed none
        determine_activity(settings, m)
        if m.governance_body == "BKS":
            conf, basis, osts, notes = classify_zero_ownership(
                m.personal_shares_owned, m.personal_ownership_percentage, m.reported_ownership_category)
            m.zero_ownership_confirmed, m.zero_ownership_basis, m.ownership_information_status = conf, basis, osts
            m.review_notes += [("REVIEW: " if osts == Status.NEEDS_REVIEW else "INFO: ") + n for n in notes]
        else:
            m.ownership_information_status = (Status.UNAVAILABLE if m.personal_shares_owned is None
                                             and m.personal_ownership_percentage is None else Status.PENDING)
    # subsequent-event keyword check
    kws = [fold(k) for k in settings.get("measurement.subsequent_event_keywords", [])]
    for m in members:
        if m.evidence:
            pg = next((p for p in doc.pages if p.page == m.evidence.page), None)
            if pg and any(k in fold(pg.text) for k in kws):
                m.review_notes.append("REVIEW: page mentions subsequent events; confirm figures apply to this fiscal year")
    st.has_hdqt_table = any(m.governance_body == "HDQT" for m in members)
    st.has_bks_table = any(m.governance_body == "BKS" for m in members)
    # attribute aggregates: dedupe identical
    seen, aggs = set(), []
    for a in cx.aggs:
        k = (a.reported_pct, a.evidence.page if a.evidence else 0)
        if k not in seen:
            seen.add(k)
            aggs.append(a)
    return ExtractionResult(ticker=ticker, year=year, members=members, aggregates=aggs, structure=st,
                            warnings=doc.warnings + cx.warnings, status="OK" if members else "NO_DATA")
