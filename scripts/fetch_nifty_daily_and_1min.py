# -*- coding: utf-8 -*-
"""Local pull: NIFTY daily (full available history) + today's 1-min bars from
BigQuery (rajat-trade project). Read-only SELECTs, single scrip ('NIFTY'),
free-tier scale.

IMPORTANT — stock_intraday_prices_dhan is a rolling/live table: it only ever
holds the CURRENT trading day's 1-min bars (confirmed 2026-08-20: MIN date ==
MAX date == today). It is NOT a historical backfill source. The canonical
multi-year 1-min file is built/maintained separately via Kite's historical API
(see MTF_V4/99_update_from_kite.py) -- this script only APPENDS today's bars
to that file, it never overwrites it wholesale. If the canonical file has a
gap (e.g. you skipped running this for a few days), close it with the Kite
refresh script, not this one.

Outputs (Downloads/Daily Trade Files/):
  - NIFTY Daily Data.csv      -- overwritten each run, full history the table has
  - NIFTY 2020-2026 Data.csv  -- today's bars appended + deduped, never overwritten wholesale
"""
import time
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT = "rajat-trade"
KEY = r"C:\Users\Rajat\Downloads\Daily Trade Files\rajat-trade-c411eaec7c51.json"
OUT_DIR = r"C:\Users\Rajat\Downloads\Daily Trade Files"

creds = service_account.Credentials.from_service_account_file(KEY)
client = bigquery.Client(project=PROJECT, credentials=creds)

# --- 1. lifetime daily ------------------------------------------------------
daily_sql = """
SELECT scrip, exchange, security_id, trade_date, open, high, low, close, volume
FROM `rajat-trade.stock_data_set.stock_daily_prices_dhan`
WHERE scrip = 'NIFTY'
ORDER BY trade_date
"""
t = time.time()
job = client.query(daily_sql)
daily = job.result().to_dataframe(create_bqstorage_client=True)
print(f"[daily] {len(daily):,} rows | {job.total_bytes_processed/1e6:.1f} MB scanned | {time.time()-t:.1f}s")
print(f"[daily] range: {daily.trade_date.min()} -> {daily.trade_date.max()}")

daily_out = f"{OUT_DIR}\\NIFTY Daily Data.csv"
daily = daily.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
daily.to_csv(daily_out, index=False)
print(f"[daily] saved: {daily_out}")

# --- 2. today's 1-min bars, appended to the existing canonical file --------
min_sql = """
SELECT scrip,
       DATETIME(TIMESTAMP_SECONDS(timestamp), "Asia/Kolkata") AS timestamp,
       open, high, low, close
FROM `rajat-trade.stock_data_set.stock_intraday_prices_dhan`
WHERE interval_m = 1
  AND scrip = 'NIFTY'
  AND DATE(TIMESTAMP_SECONDS(timestamp), "Asia/Kolkata") = CURRENT_DATE("Asia/Kolkata")
ORDER BY timestamp
"""
t = time.time()
job = client.query(min_sql)
minute = job.result().to_dataframe(create_bqstorage_client=True)
print(f"[1min] {len(minute):,} rows fetched (today only -- this table has no history) "
      f"| {job.total_bytes_processed/1e6:.1f} MB scanned | {time.time()-t:.1f}s")

minute_out = f"{OUT_DIR}\\NIFTY 2020-2026 Data.csv"
minute["timestamp"] = pd.to_datetime(minute["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")

existing = pd.read_csv(minute_out)
combined = pd.concat([existing, minute], ignore_index=True)
combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
combined.to_csv(minute_out, index=False)
print(f"[1min] appended -> {len(combined):,} rows | range: {combined.timestamp.min()} -> {combined.timestamp.max()}")
print(f"[1min] saved: {minute_out}")
