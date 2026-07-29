# -*- coding: utf-8 -*-
"""
Download NSE's daily "Full Bhavcopy" (sec_bhavdata_full) -- symbol, series,
OHLC, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY,
DELIV_PER in one row per symbol -- and load it into BigQuery.

Source: https://www.nseindia.com/all-reports#cr_equity_archives
        ("Full Bhavcopy and Security Deliverable data")
File:   https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
        (static archive host, no cookies/session needed -- just a browser UA;
        404 on a date means no trading that day, e.g. weekend/holiday)

RUN THIS IN GOOGLE COLAB. Authenticates to BigQuery via Colab's built-in auth
(no service-account key needed), writes to Rajat's `rajat-trade` project.

On run it prompts for a start and end date (DD-MM-YYYY), fetches every
trading day in that range, checks which (date, symbol, series) rows already
exist in BigQuery for that range, and appends only the ones that are new.
"""

# ============================== 0. Setup (Colab) ==============================
# !pip install -q requests pandas db-dtypes
from google.colab import auth
auth.authenticate_user()

PROJECT_ID = "rajat-trade"          # <-- change if needed
DATASET    = "stock_data_set"
TABLE      = "nse_bhavdata_full"
TABLE_ID   = f"{PROJECT_ID}.{DATASET}.{TABLE}"

import io
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
from google.cloud import bigquery

client = bigquery.Client(project=PROJECT_ID)

BASE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

COLUMN_MAP = {
    "SYMBOL": "symbol", "SERIES": "series", "DATE1": "date",
    "PREV_CLOSE": "prev_close", "OPEN_PRICE": "open_price", "HIGH_PRICE": "high_price",
    "LOW_PRICE": "low_price", "LAST_PRICE": "last_price", "CLOSE_PRICE": "close_price",
    "AVG_PRICE": "avg_price", "TTL_TRD_QNTY": "ttl_trd_qnty", "TURNOVER_LACS": "turnover_lacs",
    "NO_OF_TRADES": "no_of_trades", "DELIV_QTY": "deliv_qty", "DELIV_PER": "deliv_per",
}
NUMERIC_COLS = [c for c in COLUMN_MAP.values() if c not in ("symbol", "series", "date")]


def fetch_one(date, session):
    """Download + parse one day's bhavcopy. Returns None if there's no genuine
    data for this exact date (404, or a non-trading day/holiday where NSE's
    archive quirkily serves the PREVIOUS trading day's file with HTTP 200
    instead of 404 -- we detect that by checking the file's own DATE1 column
    against the date we requested, and reject on mismatch)."""
    url = BASE_URL.format(date=date.strftime("%d%m%Y"))
    resp = session.get(url, headers=HEADERS, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns=COLUMN_MAP)
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]

    for col in ("symbol", "series"):
        df[col] = df[col].astype(str).str.strip()
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%d-%b-%Y").dt.strftime("%Y-%m-%d")

    requested = date.strftime("%Y-%m-%d")
    if (df["date"] != requested).any():
        return None  # stale/holiday file served under this date's URL -- discard
    return df


def fetch_range(start, end, delay=0.5):
    """Fetch + parse bhavcopy for every calendar day in [start, end].
    Non-trading days (weekends/holidays) 404 and are silently skipped."""
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


# ============================== 2. Ask for date range + fetch ================
start_str = input("Start date (DD-MM-YYYY): ").strip()
end_str = input("End date (DD-MM-YYYY): ").strip()
START_DATE = datetime.strptime(start_str, "%d-%m-%Y")
END_DATE = datetime.strptime(end_str, "%d-%m-%Y")
if END_DATE < START_DATE:
    raise ValueError("End date must be on/after start date")

df = fetch_range(START_DATE, END_DATE)
print(f"Fetched {len(df)} rows across {df['date'].nunique() if not df.empty else 0} "
      f"trading day(s) between {start_str} and {end_str}: "
      f"{sorted(df['date'].unique()) if not df.empty else []}")

# ============================== 3. Dedup against existing BigQuery rows =======
client.create_dataset(f"{PROJECT_ID}.{DATASET}", exists_ok=True)

try:
    client.get_table(TABLE_ID)
    table_exists = True
except Exception:
    table_exists = False

if table_exists and not df.empty:
    existing = client.query(
        f"SELECT DISTINCT date, symbol, series FROM `{TABLE_ID}` "
        f"WHERE date BETWEEN '{df['date'].min()}' AND '{df['date'].max()}'"
    ).to_dataframe()
    if not existing.empty:
        df = df.merge(existing, on=["date", "symbol", "series"], how="left", indicator=True)
        df = df[df["_merge"] == "left_only"].drop(columns="_merge")

# ============================== 4. Load =======================================
if df.empty:
    print("Nothing new to load (all rows already in BigQuery).")
else:
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", autodetect=True)
    job = client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config)
    job.result()
    print(f"Loaded {len(df)} new rows into {TABLE_ID}")
