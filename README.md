# viet-governance-ai — Vietnamese corporate-governance research pipeline

Turns annual reports (Báo cáo thường niên) of HOSE/HNX-listed companies into three auditable governance
indicators and an Excel workbook. Input: a ticker. Output: `data/output/<TICKER>_Corporate_Governance_<y0>_<y1>.xlsx`.

> **Status — read first.** The code, tests and Excel output are implemented and pass (126 tests), but:
> * **Live Vietstock discovery/download is implemented but has NOT been exercised against the real site.** The
>   development sandbox blocked all outbound access to vietstock.vn, hnx.vn and company sites (HTTP 403 from the proxy).
>   The Vietstock page parser is best-effort and may need adjusting (Vietstock may load its document list through
>   AJAX/authenticated endpoints, which this tool deliberately does not bypass).
> * **No real company report has been processed.** All tests and the sample workbook use *synthetic* fixture PDFs of a
>   fictional company "ABC". The sample is in `docs/sample_output/SYNTHETIC_*.xlsx` and is **not research data**.
> * Extraction is rule-based (tables + text-line fallback). There is no LLM call and no OCR engine in this build
>   (OCR is used automatically only if `tesseract` is installed). Real reports will surface layouts the rules do not
>   cover; those become `NEEDS_REVIEW` / `UNAVAILABLE`, never guessed values.
> * The scoring rubric has not been supplied: all scores are `PENDING_RUBRIC`.
> * Historical outstanding shares have no automatic live source; they come from a researcher-supplied CSV.
> * Streamlit UI (optional phase 2) is not built.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q          # no network, no paid APIs
```
## Usage
```bash
python -m app.main --ticker FPT --start-year 2018 --end-year 2025
python -m app.main --tickers FPT HPG VNM
python -m app.main --ticker FPT --cached-only                 # never touch the network
python -m app.main --ticker FPT --export-only                 # recalculate + export, no extraction
python -m app.main --ticker FPT --reprocess-missing           # only years without a finished extraction
python -m app.main --ticker FPT --retry-downloads             # retry years with no report
python -m app.main --ticker FPT --force                       # redo download + extraction
python -m app.main --ticker FPT --status                      # per-year stage status
python -m app.main --list-unresolved                          # open review tasks
python -m app.main --ticker FPT --membership-date-policy fiscal_year_end --board-source-policy aggregate_preferred
python -m app.main --ticker FPT --scoring-config my_scoring.yaml
# recorded manual correction (raw observation untouched; key = ticker:year:BODY:normalised-name)
python -m app.main --correct "FPT:2024:BKS:nguyễn thị hoa" personal_shares_owned 0 --reviewer me --justification "AR p.57"
```
Secondary sources (used only if Vietstock yields nothing; always labelled in the `Sources` sheet):
`data/reviewed/report_sources.csv` (`ticker,year,url,source_category,publication_date`) and a local drop folder
`data/reports/inbox/TICKER_YEAR.pdf`. Historical shares: `data/reviewed/outstanding_shares.csv`
(`ticker,year,outstanding_shares,issued_shares,treasury_shares,source,source_category,effective_date,adjustments`).

## Architecture
Deterministic state machine (`app/workflow.py`) over five narrow agents with pydantic contracts (`app/models.py`):

```
ticker → Discovery → Document Intelligence → Governance Extraction → Verification ─┐
                                 Outstanding Shares (separate, supplementary) ─────┤
                                                  Calculations → Scoring → Excel ◄─┘
```
| Agent (`app/agents/`) | Responsibility |
|---|---|
| `discovery` | validate ticker, resolve company (rename/exchange history in `config/companies.yaml`), list Vietstock PDFs → fallback sources, resumable download, PDF magic/size/type checks, identity + fiscal-year validation (wrong company/year ⇒ `REJECTED`), SHA-256 dedupe, cache |
| `document_intelligence` | PyMuPDF text per page, scanned detection, keyword page ranking (HĐQT/BKS/ownership/capital), pdfplumber tables only on relevant pages, OCR only if available, cache keyed by file checksum |
| `governance_extraction` | header-mapped table rows → members, titles, independence, appointment/departure dates, personal vs related/representative holdings, "as of" effective dates, former-member sections, duplicate merging, aggregate Board ownership (total rows + narrative), BKS-absent declaration |
| `outstanding_shares` | last trading day (weekday + configured holidays), CSV provider, treasury/issued consistency, effective-date check. **Never** the Indicator 3 denominator |
| `verification` | independent re-read of each cited page with a second parser (pdfplumber), name/figure presence, unit & range checks, zero-ownership basis consistency, dual-role detection, aggregate scope; produces review tasks. Does not trust Agent 3 |

Services: `calculations.py` (pure Python indicators), `scoring.py`, `pdf_parser.py`, `vietstock.py`, `excel_export.py`.

## Methodology implemented
* **Percentages are percentage values internally (2.5 = 2.5 %)**; Excel stores fractions formatted `0.00##%`.
* **Reporting-year attribution**: every observation belongs to the fiscal year of the report that contains it; a
  17/03/2025 composition in the 2024 report stays FY2024 and keeps its real date. Missing date ⇒ `Not explicitly disclosed`
  (never inferred, never the publication date). Dates far from the fiscal year or pages mentioning subsequent events ⇒ review.
* **Indicator 1** = active independent HĐQT / active HĐQT × 100. Independent only if the title says *độc lập* (member
  record stays `UNKNOWN` otherwise). Config `independence.unstated_as_non_independent_when_titles_listed` (default true)
  lets such members count in the denominator without forcing review; set false to require review.
* **Indicator 2** = active BKS with confirmed `0` shares **or** exactly `0.00 %` personal ownership / active BKS × 100.
  Blank or `-` is *missing*, not zero (`ownership.dash_means_missing`); related-person holdings never count; any active
  member with unknown ownership ⇒ `INCOMPLETE` (value shown only as a lower bound); declared absence of BKS ⇒ `NOT_APPLICABLE`.
* **Indicator 3** = Σ personal ownership % of active HĐQT members. Reported aggregate kept separately
  (`reported_board_ownership_ratio` vs `calculated_…`); never added together; conflicts beyond `aggregate_tolerance_pp` ⇒ `NEEDS_REVIEW`
  with neither value overwritten; missing individual % ⇒ calculated sum `INCOMPLETE` unless a verified aggregate covering all
  active members resolves it. Shares are **not** converted to percentages (`derive_pct_from_shares` must stay false).
  Source choice: `ownership.board_source_policy`.
* **Scoring** (`config/scoring.yaml`): thresholds with inclusive/exclusive bounds, weights, direction. Empty ⇒ `PENDING_RUBRIC`.
  Only `VERIFIED` indicators are scored; missing data is never scored as the lowest band; partial scores are not summed.

## Storage and audit trail (SQLite `data/governance.sqlite`)
`companies, report_documents, document_pages, member_observations, aggregate_observations, outstanding_share_observations,
verification_decisions, manual_corrections, review_tasks, scoring_configs, research_runs, stage_status, final_results`.
Raw observations are regenerated from immutable PDFs (idempotent per ticker-year, keyed `ticker:year:BODY:name`);
corrections live in `manual_corrections` (reviewer + justification + timestamp required) and are layered on top at
calculation time, so raw evidence is never overwritten. Names are normalised by NFC/case/whitespace only — no fuzzy merging.
Stage status + `sha256:methodology-hash` make runs resumable; a failure in one year never blocks other years.

## Excel workbook sheets
`Summary` (live `COUNTIFS`/`IF`/`SUMIFS` formulas over the member sheets, `fullCalcOnLoad`), `HĐQT Members`, `BKS Members`,
`Outstanding Shares`, `Sources` (URL, SHA-256, hyperlinks), `Review Queue`, `Scoring Rubric`. Frozen headers, filters,
colour-coded statuses. Years without data show `UNAVAILABLE`, never zero. Formulas were verified by recalculating a
generated workbook in LibreOffice.

## Tests (`python -m pytest`)
Unit: parsing of Vietnamese numbers/dates, calculations (all three indicators incl. conflicts/incomplete/duplicates/
different dates), scoring boundaries (test-only rubric), extraction rules (independence, mid-year/after-cutoff/resigned,
former sections, BKS zero cases, related-person holdings, aggregates, spanning tables, text fallback), PDF parsing
(searchable/scanned/cache), discovery (fake HTTP: unavailable source, wrong company/year, duplicates, resumable download,
403 not retried, cache, offline), outstanding shares, DB corrections/idempotency, Excel. Integration: one year, eight years
with missing/corrupt/scanned/no-BKS years, reprocessing, methodology change, resume after crash, manual correction →
recalculation, multiple companies, CLI.

## Known limitations / next steps
1. Validate Vietstock discovery on a machine with access; adapt `parse_document_links` to the real page/API.
2. Run real reports for one company-year, inspect against the PDF, and extend the table/text rules to real layouts
   (multi-line names, merged cells, ownership tables in different sections, scanned reports).
3. Optional LLM/vision interpreter for hard pages (must only *propose* candidates that pass the same verification), OCR setup.
4. Historical outstanding shares: add a verified provider; exchange holiday calendar for exact last trading days.
5. Provide the scoring rubric; thresholds + boundary tests then replace the pending state.
6. Streamlit review UI.
