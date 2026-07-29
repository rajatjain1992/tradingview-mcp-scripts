# Mutual Fund Equity Portfolio Disclosures → BigQuery

Scrapes the monthly portfolio disclosures (SEBI-mandated) that SBI, ICICI
Prudential, and HDFC mutual funds publish for each of their schemes, keeps
only **equity-category schemes**, normalizes every scheme's holdings into one
flat table, and loads it into BigQuery. Built from AMFI's public portfolio
disclosure directory: https://www.amfiindia.com/online-center/portfolio-disclosure

## Why this exists

Every AMC (Asset Management Company) publishes a monthly "portfolio
disclosure" — a spreadsheet listing every stock/bond a scheme holds, with
ISIN, quantity, market value, and % of NAV. It's the same regulatory format
across all AMCs, but every AMC's website hosts and structures the files
completely differently. This pipeline handles all three quirks and produces
one clean, queryable table instead of clicking through 80+ spreadsheets by
hand every month.

## Files

| File | What it is |
|---|---|
| `mf_equity_portfolios_to_bigquery.py` | The scraper/parser/loader — run this locally or in CI. Downloads (SBI, HDFC), parses all three AMCs, writes SQLite, optionally loads BigQuery directly. |
| `mf_equity_portfolios_colab.py` | A Colab-only loader — no scraping, just uploads a pre-built CSV and pushes it into BigQuery via Colab's built-in Google auth (use this if you don't have BigQuery credentials configured on your own machine). |
| `mf_equity_portfolios.db` | SQLite database, table `equity_portfolio_holdings` — the output of a full run. |
| `mf_equity_portfolios.csv` | Same data as the SQLite table, flat CSV — this is what you upload in the Colab script. |
| `cache/mf_portfolios/` | Raw downloaded `.xlsx` files (gitignored — regenerate by re-running the script). |

## How the scraper works, per AMC

All three AMCs publish the *same* SEBI-mandated table format inside their
spreadsheets (Name of Instrument, ISIN, Industry/Rating, Quantity, Market
Value, % to NAV) — but wildly different file layouts and hosting:

**SBI** (`https://www.sbimf.com/portfolios`)
- One combined "All Schemes Monthly Portfolio" `.xlsx` per month — a
  multi-sheet workbook with one sheet per scheme, plus an `Index` sheet
  mapping short scheme codes (e.g. `SFLEXI`) to full scheme names.
- Direct download URL, no auth needed, pattern:
  `https://www.sbimf.com/docs/default-source/scheme-portfolios/all-schemes-monthly-portfolio---as-on-{Dth}-{month}-{year}.xlsx`
- The script downloads this once per month, opens the `Index` sheet to
  resolve names, then parses only the sheets whose scheme code is in the
  curated `SBI_EQUITY_SHEET_CODES` set (large/mid/small/flexi/multi/focused/
  value/contra/dividend-yield/ELSS/sectoral — debt, liquid, gilt, hybrid,
  arbitrage, multi-asset, FoF, ETF and index schemes are excluded).

**ICICI Prudential** (`icicipruamc.com/media-center/downloads`)
- Also publishes per-scheme files, but the "Download" buttons are
  JavaScript-triggered — there's no static, guessable URL to hit directly.
  **This script does not auto-download ICICI.** You need to visit that page
  in a browser once per month, click Download, and drop the resulting
  per-scheme `.xlsx` files (or unzipped folder) into
  `cache/mf_portfolios/icici_zip_{apr,may,jun}/`.
- Once those files are there, the script fuzzy-matches each file's name
  against the curated `ICICI_EQUITY_SCHEMES` list (sourced from ICICI's own
  "Equity Funds" explorer page) and parses the first non-derivatives sheet
  in each matched workbook.

**HDFC** (`https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio`)
- One `.xlsx` per individual scheme (no combined workbook), hosted directly
  at `https://files.hdfcfund.com/s3fs-public/{upload-month}/Monthly HDFC
  {Scheme Name} - {D Month YYYY}.xlsx` — no auth needed.
- The upload-month folder is (empirically) the calendar month *after* the
  portfolio month; the script tries that offset first and falls back to a
  couple of neighboring months if it 404s.
- Only schemes in the curated `HDFC_EQUITY_SCHEMES` list are downloaded —
  sourced from HDFC's own "Equity" category filter on their fund explorer
  (https://www.hdfcfund.com/explore/mutual-funds/equity, 21 schemes) plus
  "HDFC ELSS Tax Saver Fund" added by hand, since HDFC's own site buckets
  ELSS separately even though SEBI classifies it as an equity category.
  Pass `--no-elss` to exclude it if you disagree with that call.

## The generic holdings-table parser

Rather than writing one parser per AMC, `parse_holdings_sheet()` works off
the one thing all three formats share: a header row containing the word
"instrument". It:
1. Scans every row for a header containing "instrument" + at least one of
   ISIN / Industry / Quantity / Market Value / % to NAV, and builds a column
   map from whichever of those are present (column order and exact naming
   differ per AMC — this doesn't care).
2. Treats every following row as a holding until it hits a narrative marker
   (NAV tables, notes, dividend declarations, hedging disclosures, etc.) or
   a "Total"/"Grand Total" row, at which point it resets and looks for the
   next header (a sheet can have multiple sub-tables, e.g. equity then debt
   then derivatives).
3. Skips rows with no ISIN, quantity, or market value (section headers,
   blank rows).

This means adding a 4th AMC mostly requires curating its equity-scheme list
and its file-naming/hosting pattern — not writing a new table parser.

## Output schema

Table `equity_portfolio_holdings` (SQLite) / `equity_portfolio_disclosures`
(BigQuery: `rajat-trade.mutual_fund_data.equity_portfolio_disclosures`):

| Column | Type | Notes |
|---|---|---|
| `amc` | STRING | `SBI` / `ICICI` / `HDFC` |
| `scheme_name` | STRING | Full scheme name |
| `portfolio_date` | DATE | "As on" date from the disclosure (month-end) |
| `isin` | STRING | NULL for cash/TREPS/footnote rows |
| `instrument_name` | STRING | Stock/instrument name, or a footnote/label for non-holding rows |
| `industry_or_rating` | STRING | Sector classification (equities) or credit rating (rare debt sleeve inside an equity scheme, e.g. cash-equivalent G-Secs) |
| `quantity` | FLOAT | Shares/units held |
| `market_value_lakhs` | FLOAT | Market value, ₹ lakhs |
| `pct_to_nav` | FLOAT | % of scheme NAV |

Dedup key used everywhere (SQLite insert, BigQuery append, Colab loader):
`(amc, scheme_name, portfolio_date, isin)`.

## Running it

```bash
# Full run: download SBI + HDFC, expect ICICI already cached (see above),
# parse everything, write SQLite, and load straight into BigQuery
python scripts/mf_equity_portfolios_to_bigquery.py --months 3 --to-bigquery

# Just re-parse what's already downloaded/cached (no network calls)
python scripts/mf_equity_portfolios_to_bigquery.py --months 3 --no-download --out mf_equity_portfolios.db
```

`--to-bigquery` requires local Google Application Default Credentials
(`gcloud auth application-default login`). If you don't have that set up,
run the script *without* `--to-bigquery` to get the CSV/SQLite, then use
`mf_equity_portfolios_colab.py` in Google Colab instead — it authenticates
via Colab's own built-in Google login, so no local credentials are needed.

## Known gaps / judgment calls

- **SBI `SBIRIOS`** (Resurgent India Opportunities Scheme) has no April 2026
  sheet — it launched between April and May 2026. Not a bug.
- **HDFC ELSS Tax Saver Fund** is included as equity by default (SEBI
  classification) — pass `--no-elss` to exclude it if you'd rather match
  HDFC's own site categorization instead.
- **SBI's `SLTAF-IV/V/VI`** (Long Term Advantage Fund series, closed-ended
  ELSS-like funds) are included as equity.
- ICICI is **not** auto-downloaded (see above) — the curated equity-scheme
  list and column parser will work on whatever files you drop in, but you
  have to fetch them yourself each month due to the JS-triggered download.
