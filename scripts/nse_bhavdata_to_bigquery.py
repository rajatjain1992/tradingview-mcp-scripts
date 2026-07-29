#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Download NSE's daily "Full Bhavcopy" (sec_bhavdata_full) — the file that has
OHLC + AVG_PRICE + TTL_TRD_QNTY + TURNOVER_LACS + NO_OF_TRADES + DELIV_QTY +
DELIV_PER all in one row per symbol — and load it into BigQuery.

Source: https://www.nseindia.com/all-reports#cr_equity_archives
        ("Full Bhavcopy and Security Deliverable data")
File:   https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv

This lives on the static nsearchives.nseindia.com host (not api.nseindia.com),
so a plain browser User-Agent is enough — no cookie/session handshake needed.
A date with no trading (weekend/holiday) returns 404 and is skipped.

Usage:
    # last 15 trading sessions, append into BigQuery
    python nse_bhavdata_to_bigquery.py --last 15 --to-bigquery

    # explicit range, write CSV only (no BigQuery)
    python nse_bhavdata_to_bigquery.py --from 08-07-2026 --to 28-07-2026 --out bhav.csv

    # explicit range + load to BigQuery
    python nse_bhavdata_to_bigquery.py --from 08-07-2026 --to 28-07-2026 --to-bigquery
"""
from __future__ import annotations

import argparse
import io
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT = "rajat-trade"
DATASET = "stock_data_set"
TABLE = "nse_bhavdata_full"
TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"

BASE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# raw NSE header -> clean BigQuery-friendly column name
COLUMN_MAP = {
    "SYMBOL": "symbol",
    "SERIES": "series",
    "DATE1": "date",
    "PREV_CLOSE": "prev_close",
    "OPEN_PRICE": "open_price",
    "HIGH_PRICE": "high_price",
    "LOW_PRICE": "low_price",
    "LAST_PRICE": "last_price",
    "CLOSE_PRICE": "close_price",
    "AVG_PRICE": "avg_price",
    "TTL_TRD_QNTY": "ttl_trd_qnty",
    "TURNOVER_LACS": "turnover_lacs",
    "NO_OF_TRADES": "no_of_trades",
    "DELIV_QTY": "deliv_qty",
    "DELIV_PER": "deliv_per",
}

NUMERIC_COLS = [
    "prev_close", "open_price", "high_price", "low_price", "last_price",
    "close_price", "avg_price", "ttl_trd_qnty", "turnover_lacs",
    "no_of_trades", "deliv_qty", "deliv_per",
]


def fetch_one(date: datetime, session: requests.Session, timeout: int = 15) -> pd.DataFrame | None:
    """Download + parse one day's bhavcopy. Returns None if there's no genuine
    data for this exact date (404, or a non-trading day/holiday where NSE's
    archive quirkily serves the PREVIOUS trading day's file with HTTP 200
    instead of 404 -- detected by checking the file's own DATE1 column
    against the date we requested, and rejecting on mismatch)."""
    url = BASE_URL.format(date=date.strftime("%d%m%Y"))
    resp = session.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns=COLUMN_MAP)
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]

    for col in ["symbol", "series"]:
        df[col] = df[col].astype(str).str.strip()
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%d-%b-%Y").dt.strftime("%Y-%m-%d")

    requested = date.strftime("%Y-%m-%d")
    if (df["date"] != requested).any():
        return None  # stale/holiday file served under this date's URL -- discard
    return df


def fetch_range(start: datetime, end: datetime, delay: float = 0.5) -> pd.DataFrame:
    """Fetch bhavcopy for every calendar day in [start, end]; skips non-trading days."""
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
    sessions have been collected. `lookback_buffer` extra calendar days are
    added per week to absorb holidays without needing a trading calendar."""
    as_of = as_of or datetime.today()
    calendar_days = n + (n // 5 + 1) * 2 + lookback_buffer  # weekends + a few holidays
    start = as_of - timedelta(days=calendar_days)
    df = fetch_range(start, as_of)
    if df.empty:
        return df
    last_dates = sorted(df["date"].unique())[-n:]
    return df[df["date"].isin(last_dates)].reset_index(drop=True)


def load_to_bigquery(df: pd.DataFrame, write_disposition: str = "WRITE_APPEND"):
    """Load the DataFrame into BigQuery, deduping by (date, symbol, series)
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
            f"SELECT DISTINCT date, symbol, series FROM `{TABLE_ID}` "
            f"WHERE date BETWEEN '{df['date'].min()}' AND '{df['date'].max()}'"
        ).to_dataframe()
        if not existing.empty:
            key_cols = ["date", "symbol", "series"]
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

    n_days = df["date"].nunique() if not df.empty else 0
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
