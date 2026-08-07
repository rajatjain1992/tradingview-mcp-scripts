# -*- coding: utf-8 -*-
"""
Download NSE's daily "PR" bhavcopy zip, extract the mcap{DDMMYYYY}.csv member
(market cap of every listed/permitted company), and load it into BigQuery.

Source: https://www.nseindia.com/all-reports#cr_equity_archives
        ("Bhavcopy (PR zip)" -- only the mcap*.csv member is used here)
File:   https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{DDMMYY}.zip
        NOTE the date in the zip filename is DDMMYY (2-digit year), unlike
        sec_bhavdata_full which uses DDMMYYYY.
        Static archive host, no cookies/session needed -- just a browser UA;
        a non-trading day (weekend/holiday) returns a genuine 404 here.

RUN THIS IN GOOGLE COLAB. Authenticates to BigQuery via Colab's built-in auth
(no service-account key needed), writes to Rajat's `rajat-trade` project.

On run it prompts for a start and end date (DD-MM-YYYY), fetches every
trading day in that range, checks which (trade_date, symbol, series) rows
already exist in BigQuery for that range, and appends only the ones that
are new.
"""

# ============================== 0. Setup (Colab) ==============================
# !pip install -q requests pandas db-dtypes
from google.colab import auth
auth.authenticate_user()

PROJECT_ID = "rajat-trade"          # <-- change if needed
DATASET    = "stock_data_set"
TABLE      = "nse_mcap_daily"
TABLE_ID   = f"{PROJECT_ID}.{DATASET}.{TABLE}"

import io
import time
import zipfile
from datetime import datetime, timedelta

import pandas as pd
import requests
from google.cloud import bigquery

client = bigquery.Client(project=PROJECT_ID)

# ============================== 1. Config =====================================
BASE_URL = "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{date}.zip"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

COLUMN_MAP = {
    "Trade Date": "trade_date", "Symbol": "symbol", "Series": "series",
    "Security Name": "security_name", "Category": "category",
    "Last Trade Date": "last_trade_date", "Face Value(Rs.)": "face_value",
    "Issue Size": "issue_size", "Close Price/Paid up value(Rs.)": "close_price",
    "Market Cap(Rs.)": "market_cap",
}
STRING_COLS = ["symbol", "series", "security_name", "category"]
NUMERIC_COLS = ["face_value", "issue_size", "close_price", "market_cap"]


def fetch_one(date, session):
    """Download the PR zip for one date, extract mcap*.csv, and parse it.
    Returns None on 404 (non-trading day) or if the zip/member is missing."""
    url = BASE_URL.format(date=date.strftime("%d%m%y"))
    resp = session.get(url, headers=HEADERS, timeout=20)
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


def fetch_range(start, end, delay=0.5):
    """Fetch + parse mcap data for every calendar day in [start, end].
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
print(f"Fetched {len(df)} rows across {df['trade_date'].nunique() if not df.empty else 0} "
      f"trading day(s) between {start_str} and {end_str}: "
      f"{sorted(df['trade_date'].unique()) if not df.empty else []}")

# ============================== 3. Dedup against existing BigQuery rows =======
client.create_dataset(f"{PROJECT_ID}.{DATASET}", exists_ok=True)

try:
    client.get_table(TABLE_ID)
    table_exists = True
except Exception:
    table_exists = False

if table_exists and not df.empty:
    existing = client.query(
        f"SELECT DISTINCT trade_date, symbol, series FROM `{TABLE_ID}` "
        f"WHERE trade_date BETWEEN '{df['trade_date'].min()}' AND '{df['trade_date'].max()}'"
    ).to_dataframe()
    if not existing.empty:
        df = df.merge(existing, on=["trade_date", "symbol", "series"], how="left", indicator=True)
        df = df[df["_merge"] == "left_only"].drop(columns="_merge")

# ============================== 4. Load =======================================
if df.empty:
    print("Nothing new to load (all rows already in BigQuery).")
else:
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", autodetect=True)
    job = client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config)
    job.result()
    print(f"Loaded {len(df)} new rows into {TABLE_ID}")
