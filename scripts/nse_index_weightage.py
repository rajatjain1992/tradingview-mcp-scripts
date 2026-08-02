"""
Monthly NSE index constituent + weightage summary.

Sources:
  - https://www.nseindia.com/market-data/live-market-indices (index universe)
  - https://niftyindices.com/ (NSE Indices Ltd — the actual data publisher)

For each index this pulls two things from niftyindices.com:
  1. Full constituent list (company, symbol, sector, ISIN) from:
       https://niftyindices.com/IndexConstituent/ind_{slug}list.csv
  2. Top-10 constituents by weight% + sector weight% from the monthly factsheet PDF:
       https://www.niftyindices.com/Factsheet/ind_{slug}.pdf
     (niftyindices.com does not publish a full per-stock weight file for free;
     the factsheet's "Top constituents by weightage" table — usually top 10 —
     is the only official per-stock weight NSE Indices makes public each month.)

The factsheet PDF has two side-by-side columns ("Sector Weight(%)" on the left,
"Top constituents by weightage" on the right) that pdfplumber's default text
extraction interleaves. We split words by x-position (left half / right half
of the page) and reassemble each column by y-position instead.

Output: one CSV per index under out_dir, plus a combined
`index_weightage_summary_<YYYYMM>.csv` with sector weights and a combined
`index_top_constituents_<YYYYMM>.csv` with top-constituent weights, plus
`index_constituents_<YYYYMM>.csv` with the full membership list for every index.

Usage:
    python nse_index_weightage.py                       # all indices, this month
    python nse_index_weightage.py --indices NIFTY50,BANKNIFTY
    python nse_index_weightage.py --out-dir data/nse_index_weightage
"""
from __future__ import annotations

import argparse
import io
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

CONSTITUENT_URL = "https://niftyindices.com/IndexConstituent/ind_{slug}list.csv"
FACTSHEET_URL = "https://www.niftyindices.com/Factsheet/ind_{factsheet_slug}.pdf"

# display name -> niftyindices.com constituent-list slug (verified as of Jul 2026)
INDICES: dict[str, str] = {
    "NIFTY50": "nifty50",
    "NIFTYNEXT50": "niftynext50",
    "NIFTY100": "nifty100",
    "NIFTY200": "nifty200",
    "NIFTY500": "nifty500",
    "NIFTYTOTALMARKET": "niftytotalmarket_",
    "NIFTYMIDCAP50": "niftymidcap50",
    "NIFTYMIDCAP100": "niftymidcap100",
    "NIFTYMIDCAP150": "niftymidcap150",
    "NIFTYSMALLCAP50": "niftysmallcap50",
    "NIFTYSMALLCAP100": "niftysmallcap100",
    "NIFTYSMALLCAP250": "niftysmallcap250",
    "NIFTYLARGEMIDCAP250": "niftylargemidcap250",
    "NIFTYMIDSMALLCAP400": "niftymidsmallcap400",
    "BANKNIFTY": "niftybank",
    "NIFTYAUTO": "niftyauto",
    "NIFTYFINSERVICE": "niftyfinance",
    "NIFTYFMCG": "niftyfmcg",
    "NIFTYHEALTHCARE": "niftyhealthcare",
    "NIFTYIT": "niftyit",
    "NIFTYMEDIA": "niftymedia",
    "NIFTYMETAL": "niftymetal",
    "NIFTYPHARMA": "niftypharma",
    "NIFTYPVTBANK": "nifty_privatebank",
    "NIFTYPSUBANK": "niftypsubank",
    "NIFTYREALTY": "niftyrealty",
    "NIFTYCONSRDURBL": "niftyconsumerdurables",
    "NIFTYOILGAS": "niftyoilgas",
    "NIFTYCPSE": "niftycpse",
    "NIFTYCONSUMPTION": "niftyconsumption",
    "NIFTYCOMMODITIES": "niftycommodities",
    "NIFTYINFRA": "niftyinfra",
    "NIFTYPSE": "niftypse",
    "NIFTYMNC": "niftymnc",
    "NIFTYDIVOPPS50": "niftydivopp50",
    "NIFTYSERVICE": "niftyservice",
    # sector indices added 2026-08-02
    "NIFTYCEMENT": "niftycement_",
    "NIFTYCHEMICALS": "niftychemicals_",
    "NIFTYENERGY": "niftyenergy",
    "NIFTYCAPITALMKT": "niftycapitalmarkets_",
    "NIFTYCOREHOUSING": "niftycorehousing_",
    # midcap/smallcap variants added 2026-08-02
    "NIFTYMIDCAP150QLTY50": "niftymidcap150quality50",
    "NIFTYMIDCAP150MOM50": "niftymidcap150momentum50_",
    "NIFTYSMALLCAP500": "niftysmallcap500_",
    "NIFTYMICROCAP250": "niftymicrocap250_",
    "NIFTYMIDSMALLCAP400MOMQLTY100": "niftymidsmallcap400momentumquality100_",
    "NIFTYTOTALMKTMOMQLTY50": "niftytotalmarketmomentumquality50_",
    "NIFTYSMALLCAP250MOMQLTY100": "niftysmallcap250momentumquality100_",
    "NIFTYMIDCAPSELECT": "niftymidcapselect_",
    "NIFTYMIDSMALLCAP400_5050": "niftymidsmallcap4005050_",
}

# Live NSE indices NOT covered above (checked 2026-08-02) -- no discoverable
# constituent CSV at niftyindices.com's /IndexConstituent/ path under any
# guessed slug, so they're left out rather than silently wrong:
#   India-theme: NIFTY INDIA DEFENCE, DIGITAL, INTERNET, MANUFACTURING,
#     NEW AGE CONSUMPTION, RAILWAYS PSU, TOURISM, INFRASTRUCTURE & LOGISTICS,
#     FPI 150, NON-CYCLICAL CONSUMER
#   Sector: NIFTY HOUSING, NIFTY EV & NEW AGE AUTOMOTIVE
#   Midcap/smallcap: NIFTY MIDCAP LIQUID 15, NIFTY SMALLCAP250 QUALITY 50
#   Niche: NIFTY SME EMERGE, NIFTY IPO, NIFTY MOBILITY,
#     NIFTY TRANSPORTATION & LOGISTICS, NIFTY RURAL, NIFTY WAVES,
#     NIFTY MIDSMALL FINANCIAL SERVICES/HEALTHCARE/INDIA CONSUMPTION/IT & TELECOM
#   Non-equity: INDIA VIX, all G-Sec/Bharat Bond indices
#   Strategy/factor/smart-beta (~50): Alpha/Quality/Low-Vol/Momentum/Equal
#     Weight/ESG variants of Nifty50/100/200/500, PR/TR leverage-inverse,
#     USD/Shariah, Top-N Equal Weight, etc. -- out of scope, separate ask.

# niftyindices.com uses a DIFFERENT (inconsistently underscored) slug for the
# factsheet PDF than for the constituent CSV. Only include an entry here once
# verified to actually return a PDF (content-type application/pdf) - indices
# missing from this map still get their full constituent list, just no
# top-10-weight / sector-weight breakdown (the factsheet URL couldn't be
# reliably guessed for them).
FACTSHEET_SLUGS: dict[str, str] = {
    "NIFTY50": "nifty50",
    "NIFTY100": "nifty_100",
    "NIFTY200": "nifty_200",
    "NIFTY500": "nifty_500",
    "NIFTYMIDCAP50": "nifty_midcap50",
    "BANKNIFTY": "nifty_bank",
    "NIFTYAUTO": "nifty_auto",
    "NIFTYFINSERVICE": "nifty_financial_services",
    "NIFTYFMCG": "nifty_fmcg",
    "NIFTYIT": "nifty_it",
    "NIFTYMEDIA": "nifty_media",
    "NIFTYMETAL": "nifty_metal",
    "NIFTYPHARMA": "nifty_pharma",
    "NIFTYPVTBANK": "nifty_private_bank",
    "NIFTYPSUBANK": "nifty_psu_bank",
    "NIFTYREALTY": "nifty_realty",
    "NIFTYCPSE": "nifty_cpse",
    "NIFTYCOMMODITIES": "nifty_commodities",
    "NIFTYINFRA": "nifty_infra",
    "NIFTYPSE": "nifty_pse",
    "NIFTYMNC": "nifty_mnc",
    "NIFTYSERVICE": "nifty_services_sector",
    "NIFTYCONSUMPTION": "nifty_india_consumption",
}


def fetch_constituents(slug: str, session: requests.Session) -> pd.DataFrame:
    url = CONSTITUENT_URL.format(slug=slug)
    resp = session.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    if not resp.text.lstrip().startswith("Company Name"):
        raise ValueError(f"unexpected constituent CSV for slug={slug} (bad slug?)")
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _column_lines(page, left: bool) -> list[str]:
    """Reassemble one visual column of a 2-column factsheet page into text lines,
    splitting words by x-position (left half vs right half of the page)."""
    mid = page.width * 0.5
    words = page.extract_words()
    col_words = [w for w in words if (w["x0"] < mid) == left]
    rows: dict[int, list] = {}
    for w in col_words:
        key = round(w["top"])
        rows.setdefault(key, []).append(w)
    lines = []
    for key in sorted(rows):
        line = " ".join(w["text"] for w in sorted(rows[key], key=lambda w: w["x0"]))
        lines.append(line)
    return lines


NUM = r"(-?\d+(?:\.\d+)?)"


def parse_factsheet(pdf_bytes: bytes) -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    """Returns (sector_weights_df, top_constituents_df, as_of_date_str)."""
    sector_rows, top_rows, as_of = [], [], None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        left_lines = _column_lines(page, left=True)
        right_lines = _column_lines(page, left=False)

    m = re.match(r"^\s*([A-Za-z]+ \d{1,2},? \d{4})\s*$", left_lines[0]) if left_lines else None
    if m:
        as_of = m.group(1)

    in_sector = False
    for line in left_lines:
        if line.strip() == "Sector Weight(%)":
            in_sector = True
            continue
        if in_sector:
            mm = re.match(rf"^(.*?)\s+{NUM}$", line.strip())
            if mm:
                sector_rows.append((mm.group(1).strip(), float(mm.group(2))))
            else:
                in_sector = False  # hit "Fundamentals" or similar, table ended

    in_top = False
    for line in right_lines:
        if "Company" in line and "Weight" in line:
            in_top = True
            continue
        if line.strip() == "Top constituents by weightage":
            continue
        if in_top:
            mm = re.match(rf"^(.*?)\s+{NUM}$", line.strip())
            if mm:
                top_rows.append((mm.group(1).strip(), float(mm.group(2))))
            else:
                break  # first non-matching line ends the table

    sector_df = pd.DataFrame(sector_rows, columns=["sector", "weight_pct"])
    top_df = pd.DataFrame(top_rows, columns=["company_name", "weight_pct"])
    return sector_df, top_df, as_of


def fetch_index(name: str, slug: str, session: requests.Session) -> dict:
    constituents = fetch_constituents(slug, session)
    sector_df = pd.DataFrame(columns=["sector", "weight_pct"])
    top_df = pd.DataFrame(columns=["company_name", "weight_pct"])
    as_of = None

    factsheet_slug = FACTSHEET_SLUGS.get(name)
    if factsheet_slug:
        try:
            resp = session.get(
                FACTSHEET_URL.format(factsheet_slug=factsheet_slug), headers=HEADERS, timeout=20
            )
            resp.raise_for_status()
            sector_df, top_df, as_of = parse_factsheet(resp.content)
            top_df = top_df.merge(
                constituents[["company_name", "symbol", "industry"]],
                on="company_name",
                how="left",
            )
        except Exception as exc:  # noqa: BLE001 - weight data is best-effort
            print(f"    (no weight data for {name}: {exc})")

    for df in (constituents, sector_df, top_df):
        df.insert(0, "index", name)
    if as_of:
        for df in (constituents, sector_df, top_df):
            df.insert(1, "as_of", as_of)

    return {"constituents": constituents, "sectors": sector_df, "top": top_df}


def run(index_names: list[str], out_dir: Path, delay: float = 1.0) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = date.today().strftime("%Y%m")

    all_constituents, all_sectors, all_top = [], [], []
    with requests.Session() as session:
        for i, name in enumerate(index_names):
            slug = INDICES[name]
            try:
                result = fetch_index(name, slug, session)
            except Exception as exc:  # noqa: BLE001 - keep going across indices
                print(f"  ! {name} ({slug}) failed: {exc}")
                continue
            print(
                f"  - {name}: {len(result['constituents'])} constituents, "
                f"{len(result['sectors'])} sectors, {len(result['top'])} top-weighted"
            )
            all_constituents.append(result["constituents"])
            all_sectors.append(result["sectors"])
            all_top.append(result["top"])
            if i < len(index_names) - 1:
                time.sleep(delay)  # be polite to niftyindices.com

    if all_constituents:
        pd.concat(all_constituents, ignore_index=True).to_csv(
            out_dir / f"index_constituents_{tag}.csv", index=False
        )
    non_empty_sectors = [df for df in all_sectors if not df.empty]
    if non_empty_sectors:
        pd.concat(non_empty_sectors, ignore_index=True).to_csv(
            out_dir / f"index_sector_weights_{tag}.csv", index=False
        )
    non_empty_top = [df for df in all_top if not df.empty]
    if non_empty_top:
        pd.concat(non_empty_top, ignore_index=True).to_csv(
            out_dir / f"index_top_constituents_{tag}.csv", index=False
        )
    print(f"Saved outputs under {out_dir} (tag={tag})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--indices",
        default=None,
        help=f"comma-separated index names (default: all {len(INDICES)} known indices). "
        f"Choices: {', '.join(INDICES)}",
    )
    ap.add_argument("--out-dir", default="data/nse_index_weightage", help="output directory")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = ap.parse_args()

    if args.indices:
        names = [n.strip().upper() for n in args.indices.split(",")]
        unknown = [n for n in names if n not in INDICES]
        if unknown:
            ap.error(f"unknown index name(s): {unknown}. Choices: {list(INDICES)}")
    else:
        names = list(INDICES)

    print(f"Fetching {len(names)} index(es)...")
    run(names, Path(args.out_dir), delay=args.delay)


if __name__ == "__main__":
    main()
