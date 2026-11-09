"""Excel workbook (openpyxl). Percent cells hold FRACTIONS formatted as % (internal convention 2.5 == 2.5% is
divided by 100 only here). Counts/ratios on Summary are live COUNTIFS/IF formulas over the member sheets."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties

from ..models import (AggregateOwnership, CompanyYearResult, MemberObservation, OutstandingShares, ReportDoc,
                      ReviewTask, Status)

S_HDQT, S_BKS = "HĐQT Members", "BKS Members"
SHEETS = ["Summary", S_HDQT, S_BKS, "Outstanding Shares", "Sources", "Review Queue", "Scoring Rubric"]
FILLS = {"VERIFIED": "C6EFCE", "NEEDS_REVIEW": "FFEB9C", "UNAVAILABLE": "D9D9D9", "INCOMPLETE": "F8CBAD",
         "NOT_APPLICABLE": "DDEBF7", "PENDING": "EDEDED", "PENDING_RUBRIC": "EDEDED", "REJECTED": "FFC7CE"}
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
PCT_FMT = "0.00##%"


def _pct(d):
    return None if d is None else float(Decimal(d) / 100)


def _yn(b):
    return "Yes" if b is True else "No" if b is False else "Unknown"


def _sheet(wb, title, headers, widths=None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 45
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, max(12, min(len(h) + 2, 28)))
    return ws


def _color(ws, col_idx):
    for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
        c = row[0]
        if c.value in FILLS:
            c.fill = PatternFill("solid", fgColor=FILLS[c.value])


def _finish(ws):
    ws.auto_filter.ref = ws.dimensions


def _page_link(doc: ReportDoc | None, page):
    if doc and doc.local_path and page:
        return f"file://{Path(doc.local_path).resolve()}#page={page}"
    return None


def build_workbook(ticker: str, years: list[int], company: dict, docs: dict[int, ReportDoc],
                   missing: dict[int, str], members: dict[int, list[MemberObservation]],
                   aggregates: dict[int, list[AggregateOwnership]], results: dict[int, CompanyYearResult],
                   shares: dict[int, OutstandingShares | None], tasks: list[ReviewTask], scoring: dict) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    ws_sum = _sheet(wb, "Summary", [
        "Ticker", "Company name", "Exchange", "Reporting year", "Total active HĐQT members",
        "Active independent HĐQT members", "INED % in BoD", "INED effective date", "Total active BKS members",
        "Active BKS with confirmed zero ownership", "Zero-ownership BKS %", "BKS ownership effective date",
        "Reported aggregate Board ownership %", "Calculated Board ownership % (sum of individual)",
        "Final Board ownership ratio", "Board ownership effective date", "Historical outstanding shares (supplementary)",
        "Indicator 1 score", "Indicator 2 score", "Indicator 3 score", "Overall governance score",
        "Indicator 1 verification", "Indicator 2 verification", "Indicator 3 verification",
        "Overall data completeness", "Notes"], {2: 30, 26: 80, 8: 18, 12: 18, 16: 18})
    ws_h = _sheet(wb, S_HDQT, [
        "Ticker", "Reporting year", "Full name", "Reported position", "Independence", "Appointment date",
        "Departure date", "Active at measurement date", "Personal shares", "Personal ownership %",
        "Reported ownership category", "Membership effective date", "Ownership effective date", "Source document",
        "PDF page", "Evidence", "Verification status", "Review notes"], {3: 28, 4: 30, 14: 18, 16: 60, 18: 60})
    ws_b = _sheet(wb, S_BKS, [
        "Ticker", "Reporting year", "Full name", "Position", "Appointment date", "Departure date",
        "Active at measurement date", "Personal shares", "Personal ownership %", "Confirmed zero ownership",
        "Zero-ownership basis", "Ownership effective date", "Ownership information status", "Source document",
        "PDF page", "Evidence", "Verification status", "Review notes"], {3: 28, 4: 30, 14: 18, 16: 60, 18: 60})
    ws_o = _sheet(wb, "Outstanding Shares", [
        "Ticker", "Reporting year", "Final trading date", "Historical outstanding shares", "Source", "Effective date",
        "Reported issued shares", "Treasury shares", "Adjustments", "Verification status", "Notes"], {5: 30, 11: 70})
    ws_s = _sheet(wb, "Sources", [
        "Ticker", "Reporting year", "Source category", "Document title", "Original URL", "Publication date",
        "Download date", "Local filename", "SHA-256", "Relevant pages", "Notes"], {4: 30, 5: 50, 9: 30, 11: 70})
    ws_q = _sheet(wb, "Review Queue", [
        "Ticker", "Fiscal year", "Field", "Extracted value", "Source page", "Original evidence", "Ambiguity",
        "Suggested resolution", "Status"], {3: 40, 6: 60, 7: 60, 8: 40})
    ws_r = _sheet(wb, "Scoring Rubric", ["Metric", "Direction", "Weight", "Min", "Max", "Min inclusive",
                                         "Max inclusive", "Points", "Status"], {1: 32, 9: 60})

    hrow = brow = 2
    for y in years:
        for m in sorted(members.get(y, []), key=lambda x: (x.governance_body, x.person_name)):
            doc = docs.get(y)
            link = _page_link(doc, m.evidence.page if m.evidence else None)
            common_src = f"{m.ticker}_{y}.pdf"
            notes = "; ".join(m.review_notes) + ("" if not m.corrected_fields else f" | corrected: {m.corrected_fields}")
            if m.governance_body == "HDQT":
                ws_h.append([m.ticker, y, m.person_name, m.reported_title, m.independence.value,
                             m.appointment_date, m.departure_date, _yn(m.active_at_cutoff), m.personal_shares_owned,
                             _pct(m.personal_ownership_percentage), m.reported_ownership_category,
                             m.membership_effective_date, m.ownership_effective_date, common_src,
                             m.evidence.page if m.evidence else None, m.evidence.quote if m.evidence else None,
                             m.verification_status.value, notes])
                r = ws_h[ws_h.max_row]
                for c, fmt in ((6, "yyyy-mm-dd"), (7, "yyyy-mm-dd"), (9, "#,##0"), (10, PCT_FMT)):
                    r[c - 1].number_format = fmt
                if link:
                    r[14].hyperlink, r[14].style = link, "Hyperlink"
            else:
                ws_b.append([m.ticker, y, m.person_name, m.reported_title, m.appointment_date, m.departure_date,
                             _yn(m.active_at_cutoff), m.personal_shares_owned, _pct(m.personal_ownership_percentage),
                             _yn(m.zero_ownership_confirmed), m.zero_ownership_basis, m.ownership_effective_date,
                             m.ownership_information_status.value, common_src,
                             m.evidence.page if m.evidence else None, m.evidence.quote if m.evidence else None,
                             m.verification_status.value, notes])
                r = ws_b[ws_b.max_row]
                for c, fmt in ((5, "yyyy-mm-dd"), (6, "yyyy-mm-dd"), (8, "#,##0"), (9, PCT_FMT)):
                    r[c - 1].number_format = fmt
                if link:
                    r[14].hyperlink, r[14].style = link, "Hyperlink"

    q_h, q_b = f"'{S_HDQT}'", f"'{S_BKS}'"
    for i, y in enumerate(years, start=2):
        res, doc, sh = results.get(y), docs.get(y), shares.get(y)
        ws_sum.cell(i, 1, ticker)
        ws_sum.cell(i, 2, company.get("name") or (doc.company_name if doc else None))
        ws_sum.cell(i, 3, (doc.exchange if doc and doc.exchange else None) or company.get("exchange"))
        ws_sum.cell(i, 4, y)
        if not res:
            ws_sum.cell(i, 26, f"No result: {missing.get(y, 'not processed')}")
            for c in (22, 23, 24):
                ws_sum.cell(i, c, "UNAVAILABLE")
            ws_sum.cell(i, 25, "INCOMPLETE")
            continue
        i1, i2, i3 = res.ined, res.bks, res.board_own
        if i1.status != Status.UNAVAILABLE:
            ws_sum.cell(i, 5, f'=COUNTIFS({q_h}!$A:$A,$A{i},{q_h}!$B:$B,$D{i},{q_h}!$H:$H,"Yes")')
            ws_sum.cell(i, 6, f'=COUNTIFS({q_h}!$A:$A,$A{i},{q_h}!$B:$B,$D{i},{q_h}!$H:$H,"Yes",{q_h}!$E:$E,"TRUE")')
            ws_sum.cell(i, 7, f'=IF(E{i}>0,F{i}/E{i},"")')
        ws_sum.cell(i, 8, i1.effective_date)
        if i2.status not in (Status.UNAVAILABLE, Status.NOT_APPLICABLE):
            ws_sum.cell(i, 9, f'=COUNTIFS({q_b}!$A:$A,$A{i},{q_b}!$B:$B,$D{i},{q_b}!$G:$G,"Yes")')
            ws_sum.cell(i, 10, f'=COUNTIFS({q_b}!$A:$A,$A{i},{q_b}!$B:$B,$D{i},{q_b}!$G:$G,"Yes",{q_b}!$J:$J,"Yes")')
            ws_sum.cell(i, 11, f'=IF(I{i}>0,J{i}/I{i},"")')
        elif i2.status == Status.NOT_APPLICABLE:
            ws_sum.cell(i, 11, "N/A")
        ws_sum.cell(i, 12, i2.effective_date)
        ws_sum.cell(i, 13, _pct(i3.reported_aggregate))
        if i3.calculated_sum is not None:   # formula only when every active member has a personal %
            ws_sum.cell(i, 14, f'=SUMIFS({q_h}!$J:$J,{q_h}!$A:$A,$A{i},{q_h}!$B:$B,$D{i},{q_h}!$H:$H,"Yes")')
        ws_sum.cell(i, 15, _pct(i3.value))
        ws_sum.cell(i, 16, i3.effective_date)
        ws_sum.cell(i, 17, sh.outstanding_shares if sh and sh.verification_status == Status.VERIFIED else None)
        for k, name in enumerate(("independent_director_ratio", "zero_ownership_bks_ratio", "board_ownership_ratio")):
            sc = res.scores.get(name, {})
            ws_sum.cell(i, 18 + k, sc.get("points") if sc.get("points") is not None else sc.get("state", "PENDING_RUBRIC"))
        ws_sum.cell(i, 21, res.overall_score if res.overall_score is not None else res.score_status)
        ws_sum.cell(i, 22, i1.status.value)
        ws_sum.cell(i, 23, i2.status.value)
        ws_sum.cell(i, 24, i3.status.value)
        ws_sum.cell(i, 25, res.completeness)
        ws_sum.cell(i, 26, " | ".join([f"I1: {'; '.join(i1.notes)}" if i1.notes else "",
                                       f"I2: {'; '.join(i2.notes)}" if i2.notes else "",
                                       f"I3: {'; '.join(i3.notes)}" if i3.notes else ""]).strip(" |"))
        for c in (7, 11, 13, 14, 15):
            ws_sum.cell(i, c).number_format = PCT_FMT
        ws_sum.cell(i, 17).number_format = "#,##0"
        ws_sum.cell(i, 26).alignment = Alignment(wrap_text=True, vertical="top")

    for y in years:
        sh = shares.get(y)
        if sh:
            ws_o.append([ticker, y, sh.final_trading_date, sh.outstanding_shares, sh.source, sh.effective_date,
                         sh.issued_shares, sh.treasury_shares, sh.adjustments, sh.verification_status.value,
                         "; ".join(sh.review_notes)])
            r = ws_o[ws_o.max_row]
            r[2].number_format = r[5].number_format = "yyyy-mm-dd"
            for c in (3, 6, 7):
                r[c].number_format = "#,##0"
        d = docs.get(y)
        if d:
            ws_s.append([ticker, y, d.source_category, f"Annual Report {y}", d.source_url, d.publication_date,
                         d.downloaded_at, Path(d.local_path).name if d.local_path else None, d.sha256, None,
                         f"validation={d.validation_status}; " + "; ".join(d.validation_notes)])
            r = ws_s[ws_s.max_row]
            if d.source_url and d.source_url.startswith("http"):
                r[4].hyperlink, r[4].style = d.source_url, "Hyperlink"
            if d.local_path:
                r[7].hyperlink, r[7].style = f"file://{Path(d.local_path).resolve()}", "Hyperlink"
            pages = sorted({m.evidence.page for m in members.get(y, []) if m.evidence})
            r[9].value = ", ".join(map(str, pages))
        else:
            ws_s.append([ticker, y, None, None, None, None, None, None, None, None,
                         f"REPORT NOT AVAILABLE: {missing.get(y, 'not processed')}"])
    for t in tasks:
        ws_q.append([t.ticker, t.fiscal_year, t.field, t.extracted_value, t.source_page, t.evidence, t.ambiguity,
                     t.suggested_resolution, t.status])
    metrics = scoring.get("metrics", {})
    pending = scoring.get("rubric_status", "PENDING") == "PENDING" or not any(v.get("thresholds") for v in metrics.values())
    for name, cfg in metrics.items():
        ths = cfg.get("thresholds") or []
        if not ths:
            ws_r.append([name, cfg.get("direction"), cfg.get("weight"), None, None, None, None, None,
                         "PENDING_RUBRIC - no thresholds supplied; scores are not computed"])
        for t in ths:
            ws_r.append([name, cfg.get("direction"), cfg.get("weight"), t.get("min"), t.get("max"),
                         t.get("min_inclusive", True), t.get("max_inclusive", True), t.get("points"), "CONFIGURED"])
    ws_r.append([])
    ws_r.append(["Rubric status", "PENDING_RUBRIC" if pending else scoring.get("rubric_status")])
    ws_r.append(["Convention", "Indicator values are percentage values (2.5 = 2.5%); Excel cells store fractions formatted as %."])
    ws_r.append(["Config dump", yaml.safe_dump(scoring, allow_unicode=True)])

    for ws, cols in ((ws_sum, (22, 23, 24, 25)), (ws_h, (17,)), (ws_b, (13, 17)), (ws_o, (10,)), (ws_q, (9,))):
        for c in cols:
            _color(ws, c)
    for ws in wb.worksheets:
        _finish(ws)
    wb.calculation = CalcProperties(fullCalcOnLoad=True)
    return wb


def save(wb: Workbook, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
