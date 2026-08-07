#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Download NSE's daily "PR" bhavcopy zip and pull out the mcap{DDMMYYYY}.csv
member -- market cap of every listed/permitted company -- then load it into
BigQuery.

Source: https://www.nseindia.com/all-reports#cr_equity_archives
        ("Bhavcopy (PR zip)" -- the same zip also has bc/bh/gl/hl/pd/tt/etf/
        corpbond/sme files, but this script only extracts mcap*.csv)
File:   https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{DDMMYY}.zip
        NOTE the date in the zip filename is DDMMYY (2-digit year), unlike
        sec_bhavdata_full which uses DDMMYYYY.

This lives on the static nsearchives.nseindia.com host, so a plain browser
User-Agent is enough -- no cookie/session handshake needed. A date with no
trading (weekend/holiday) returns a genuine 404 here (unlike sec_bhavdata_full,
which serves a stale previous-day file on holidays -- that quirk does NOT
appear to affect this endpoint, but we still cross-check Trade Date against
the requested date as a safety net).

Usage:
    # last 15 trading sessions, append into BigQuery
    python nse_mcap_to_bigquery.py --last 15 --to-bigquery

    # explicit range, write CSV only (no BigQuery)
    python nse_mcap_to_bigquery.py --from 08-07-2026 --to 28-07-2026 --out mcap.csv

    # explicit range + load to BigQuery
    python nse_mcap_to_bigquery.py --from 08-07-2026 --to 28-07-2026 --to-bigquery
"""
from __future__ import annotations

import argparse
import io
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT = "rajat-trade"
DATASET = "stock_data_set"
TABLE = "nse_mcap_daily"
TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"

BASE_URL = "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{date}.zip"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# raw NSE header (post-strip) -> clean BigQuery-friendly column name
COLUMN_MAP = {
    "Trade Date": "trade_date",
    "Symbol": "symbol",
    "Series": "series",
    "Security Name": "security_name",
    "Category": "category",
    "Last Trade Date": "last_trade_date",
    "Face Value(Rs.)": "face_value",
    "Issue Size": "issue_size",
    "Close Price/Paid up value(Rs.)": "close_price",
    "Market Cap(Rs.)": "market_cap",
}

STRING_COLS = ["symbol", "series", "security_name", "category"]
NUMERIC_COLS = ["face_value", "issue_size", "close_price", "market_cap"]


def fetch_one(date: datetime, session: requests.Session, timeout: int = 20) -> pd.DataFrame | None:
    """Download the PR zip for one date, extract mcap*.csv, and parse it.
    Returns None on 404 (non-trading day) or if the zip/member is missing."""
    url = BASE_URL.format(date=date.strftime("%d%m%y"))
    resp = session.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile:
        return None

    mcap_name = next((n for n in zf.namelist() if n.lower().startswith("mcap")), None)
    if mcap_name is None:
        return None

    with zf.open(mcap_name) as f:
        df = pd.read_csv(f)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns=COLUMN_MAP)
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]

    for col in STRING_COLS + ["trade_date", "last_trade_date"]:
        df[col] = df[col].astype(str).str.strip()
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%d %b %Y").dt.strftime("%Y-%m-%d")
    df["last_trade_date"] = pd.to_datetime(df["last_trade_date"], format="%d %b %Y", errors="coerce").dt.strftime("%Y-%m-%d")

    requested = date.strftime("%Y-%m-%d")
    if (df["trade_date"] != requested).any():
        return None  # safety net, in case NSE ever serves a stale file here too
    return df


def fetch_range(start: datetime, end: datetime, delay: float = 0.5) -> pd.DataFrame:
    """Fetch mcap data for every calendar day in [start, end]; skips non-trading days."""
    frames = []
    with requests.Session() as session:
        day = start
        while day <= end:
            df = fetch_one(day, session)
            if df is not None:
                frames.append(df)
            day += timedelta(days=1)
            time.sleep(delay)  # be polite to NSE's archive host
    if not frames:
        return pd.DataFrame(columns=list(COLUMN_MAP.values()))
    return pd.concat(frames, ignore_index=True)


def fetch_last_n_sessions(n: int, as_of: datetime | None = None, lookback_buffer: int = 3) -> pd.DataFrame:
    """Walk backwards from `as_of` (default today) until `n` distinct trading
    sessions have been collected."""
    as_of = as_of or datetime.today()
    calendar_days = n + (n // 5 + 1) * 2 + lookback_buffer  # weekends + a few holidays
    start = as_of - timedelta(days=calendar_days)
    df = fetch_range(start, as_of)
    if df.empty:
        return df
    last_dates = sorted(df["trade_date"].unique())[-n:]
    return df[df["trade_date"].isin(last_dates)].reset_index(drop=True)


def load_to_bigquery(df: pd.DataFrame, write_disposition: str = "WRITE_APPEND"):
    """Load the DataFrame into BigQuery, deduping by (trade_date, symbol, series)
    against what's already in the table before appending."""
    from google.cloud import bigquery

    client = bigquery.Client(project=PROJECT)
    client.create_dataset(f"{PROJECT}.{DATASET}", exists_ok=True)

    table_exists = True
    try:
        client.get_table(TABLE_ID)
    except Exception:
        table_exists = False

    if table_exists and write_disposition == "WRITE_APPEND":
        existing = client.query(
            f"SELECT DISTINCT trade_date, symbol, series FROM `{TABLE_ID}` "
            f"WHERE trade_date BETWEEN '{df['trade_date'].min()}' AND '{df['trade_date'].max()}'"
        ).to_dataframe()
        if not existing.empty:
            key_cols = ["trade_date", "symbol", "series"]
            df = df.merge(existing, on=key_cols, how="left", indicator=True)
            df = df[df["_merge"] == "left_only"].drop(columns="_merge")

    if df.empty:
        print("Nothing new to load (all rows already in BigQuery).")
        return

    job_config = bigquery.LoadJobConfig(
        write_disposition=write_disposition,
        autodetect=True,
    )
    job = client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config)
    job.result()
    print(f"Loaded {len(df)} rows into {TABLE_ID}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="single date, DD-MM-YYYY")
    ap.add_argument("--from", dest="from_date", help="range start, DD-MM-YYYY")
    ap.add_argument("--to", dest="to_date", help="range end, DD-MM-YYYY")
    ap.add_argument("--last", type=int, help="fetch the last N trading sessions up to today")
    ap.add_argument("--out", default=None, help="also save to this CSV path")
    ap.add_argument("--to-bigquery", action="store_true", help=f"load into {TABLE_ID}")
    ap.add_argument("--replace", action="store_true", help="WRITE_TRUNCATE instead of WRITE_APPEND")
    args = ap.parse_args()

    fmt = "%d-%m-%Y"
    if args.last:
        df = fetch_last_n_sessions(args.last)
    elif args.date:
        d = datetime.strptime(args.date, fmt)
        df = fetch_range(d, d)
    elif args.from_date and args.to_date:
        df = fetch_range(datetime.strptime(args.from_date, fmt), datetime.strptime(args.to_date, fmt))
    else:
        ap.error("pass --last N, --date, or both --from and --to")
        return

    n_days = df["trade_date"].nunique() if not df.empty else 0
    print(f"Fetched {len(df)} rows across {n_days} trading day(s)")

    if args.out:
        Path(args.out).write_text(df.to_csv(index=False))
        print(f"Saved to {args.out}")

    if args.to_bigquery and not df.empty:
        load_to_bigquery(df, write_disposition="WRITE_TRUNCATE" if args.replace else "WRITE_APPEND")
    elif not df.empty:
        print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
