# NSE Index Constituents & Weightage — monthly summary

Reproduces, every month, a snapshot of what's in each major NSE index and each
constituent's weight, sourced from NSE Indices Ltd's own site (niftyindices.com) —
the publisher behind both `nseindia.com/market-data/live-market-indices` and
`niftyindices.com`.

## Run it

```bash
python scripts/nse_index_weightage.py --out-dir scripts/data/nse_index_weightage
```

Add `--indices NIFTY50,BANKNIFTY` to limit to specific indices (see the script's
`INDICES` dict for the full list of 35 supported names).

## Outputs (timestamped `YYYYMM`, safe to re-run monthly)

- `index_constituents_<YYYYMM>.csv` — every stock in every supported index (company, sector, symbol, ISIN). Always populated.
- `index_top_constituents_<YYYYMM>.csv` — top-10-by-weight stocks per index, with weight %. From the monthly factsheet PDF.
- `index_sector_weights_<YYYYMM>.csv` — sector weight % per index (only published for the broad-market/midcap indices whose factsheet has a "Sector Representation" panel).

## Why only top-10 weights, not all constituents

NSE Indices does not publish a free, full per-stock weight file. The only
official per-stock weight numbers available each month are the "Top
constituents by weightage" table in each index's factsheet PDF
(`https://www.niftyindices.com/Factsheet/ind_<slug>.pdf`), which lists the top
10 stocks by weight. Full membership (all stocks, no weight) comes from
`https://niftyindices.com/IndexConstituent/ind_<slug>list.csv`.

If you need exact weights for every constituent (not just top 10), that
requires a paid NSE Indices data subscription or reverse-engineering
free-float market cap from bhavcopy — out of scope here.

## Gotchas that make this brittle

- The factsheet PDF slug is **not** the same as the constituent-CSV slug, and
  the underscore convention is inconsistent (`ind_nifty50.pdf` but
  `ind_nifty_bank.pdf`, `ind_nifty_100.pdf` but `ind_niftyauto` → `ind_nifty_auto.pdf`).
  `FACTSHEET_SLUGS` in the script only includes slugs that were verified to
  return `content-type: application/pdf`. Indices missing from that map still
  get their constituent list, just no weight breakdown.
- Both niftyindices.com endpoints return HTTP 200 with an HTML error page for
  a bad slug (never a 404), so the script validates content (`Company Name...`
  header, or actual PDF bytes) rather than trusting the status code.
- The factsheet PDF renders two side-by-side columns ("Sector Weight(%)" /
  "Top constituents by weightage") that naive text extraction interleaves.
  The script splits words by x-position (left half vs. right half of the
  page) before reassembling lines — if NSE Indices changes the factsheet
  layout, this parsing will need adjusting.
- If NSE Indices renames/removes an index's factsheet or constituent file,
  that one index will fail and print a warning but won't stop the rest of
  the run.
