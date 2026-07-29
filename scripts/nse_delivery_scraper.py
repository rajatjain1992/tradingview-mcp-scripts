"""
Download NSE's "CM - Security-wise Delivery Position" report (the MTO file)
for a date or date range, and parse it into a clean CSV/DataFrame.

Source page: https://www.nseindia.com/all-reports#cr_equity_archives
File pattern: https://nsearchives.nseindia.com/archives/equities/mto/MTO_DDMMYYYY.DAT

This endpoint lives on nsearchives.nseindia.com (the static archive host), not
api.nseindia.com, so it does NOT require the usual cookie/session dance that
NSE's API endpoints need. A plain browser-like User-Agent is enough.

Usage:
    python nse_delivery_scraper.py --date 28-07-2026
    python nse_delivery_scraper.py --from 21-07-2026 --to 28-07-2026 --out delivery.csv
"""
from __future__ import annotations

import argparse
import io
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{date}.DAT"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

COLUMNS = [
    "record_type",
    "sr_no",
    "symbol",
    "series",
    "traded_qty",
    "deliverable_qty",
    "delivery_pct",
]


def fetch_raw(date: datetime, session: requests.Session, timeout: int = 15) -> str | None:
    """Download the raw .DAT text for one date. Returns None if not published
    (weekends/holidays return 404)."""
    url = BASE_URL.format(date=date.strftime("%d%m%Y"))
    resp = session.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


def parse_mto(raw_text: str, date: datetime) -> pd.DataFrame:
    """Parse the pipe-less CSV body of an MTO file into a tidy DataFrame.

    Layout:
        line 1: report title
        line 2: "10,MTO,DDMMYYYY,<timestamp>,<seq>"
        line 3: "Trade Date <DD-MON-YYYY>,Settlement Type <N>"
        line 4: column header
        lines 5+: "20,<sr_no>,<symbol>,<traded_qty>,<deliverable_qty>,<pct>"
    """
    rows = []
    for line in raw_text.splitlines():
        if not line.startswith("20,"):
            continue
        parts = [p.strip() for p in line.split(",")]
        # Equity rows: 20,sr_no,symbol,series,traded_qty,deliverable_qty,pct  (7 fields)
        # Debt/bond rows omit the series field (6 fields).
        if len(parts) == 7:
            record_type, sr_no, symbol, series, traded_qty, deliverable_qty, delivery_pct = parts
        elif len(parts) == 6:
            record_type, sr_no, symbol, traded_qty, deliverable_qty, delivery_pct = parts
            series = None
        else:
            continue
        rows.append((record_type, sr_no, symbol, series, traded_qty, deliverable_qty, delivery_pct))

    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df

    df["sr_no"] = pd.to_numeric(df["sr_no"], errors="coerce")
    df["traded_qty"] = pd.to_numeric(df["traded_qty"], errors="coerce")
    df["deliverable_qty"] = pd.to_numeric(df["deliverable_qty"], errors="coerce")
    df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")
    df.insert(0, "date", date.strftime("%Y-%m-%d"))
    df = df.drop(columns=["record_type"])
    return df


def fetch_range(start: datetime, end: datetime, delay: float = 0.5) -> pd.DataFrame:
    """Fetch and concatenate delivery data for every calendar day in [start, end].
    Days with no published file (weekends/holidays) are silently skipped."""
    frames = []
    with requests.Session() as session:
        day = start
        while day <= end:
            raw = fetch_raw(day, session)
            if raw:
                frames.append(parse_mto(raw, day))
            day += timedelta(days=1)
            time.sleep(delay)  # be polite to NSE's archive host
    if not frames:
        return pd.DataFrame(columns=["date"] + COLUMNS[1:])
    return pd.concat(frames, ignore_index=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="single date, DD-MM-YYYY")
    ap.add_argument("--from", dest="from_date", help="range start, DD-MM-YYYY")
    ap.add_argument("--to", dest="to_date", help="range end, DD-MM-YYYY")
    ap.add_argument("--out", default=None, help="output CSV path (default: print head)")
    args = ap.parse_args()

    fmt = "%d-%m-%Y"
    if args.date:
        start = end = datetime.strptime(args.date, fmt)
    elif args.from_date and args.to_date:
        start = datetime.strptime(args.from_date, fmt)
        end = datetime.strptime(args.to_date, fmt)
    else:
        ap.error("pass either --date or both --from and --to")
        return

    df = fetch_range(start, end)
    print(f"Fetched {len(df)} rows across {df['date'].nunique() if not df.empty else 0} trading day(s)")

    if args.out:
        out_path = Path(args.out)
        df.to_csv(out_path, index=False)
        print(f"Saved to {out_path}")
    else:
        print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
