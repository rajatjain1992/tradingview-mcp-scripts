# -*- coding: utf-8 -*-
"""Driver: computes the Multi-Mode Indicator for NIFTY across 1m/5m/15m/30m/60m
(intraday) and D/W/M, saving ONE file per timeframe (kept separate rather than
concatenated -- the combined intraday MTF file from run_mtf_indicators.py was
856MB/106MB parquet already; Multi-Mode has ~2x the columns).

Validated against live TradingView (Table View CSV export) on 2026-08-12 13:30
5m bar: EMAs match within cross-vendor tick noise, day-variables (dayHigh/
dayLow/day50/high_30/low_30) match exactly or near-exactly, VWAP within 0.18
points despite the volume-data caveat (see multi_mode_calc.py docstring).
"""
import sys
import time
sys.path.insert(0, "scripts/pine")
from mtf_indicators import resample_daily, resample_weekly, resample_monthly
from multi_mode_calc import multi_mode_calc
import pandas as pd

NIFTY_1MIN_VOL = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY_1min_with_volume.csv"
OUT_DIR = r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4"


def resample_intraday_v(df1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    d = df1m.copy()
    d["date"] = d["timestamp"].dt.date
    mins = d["timestamp"].dt.hour * 60 + d["timestamp"].dt.minute
    d["bucket"] = (mins - (9 * 60 + 15)) // minutes
    g = d.groupby(["date", "bucket"], sort=True, as_index=False)
    out = g.agg(timestamp=("timestamp", "first"), open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    return out.sort_values("timestamp").reset_index(drop=True)[
        ["timestamp", "open", "high", "low", "close", "volume"]]


def resample_daily_v(df1m: pd.DataFrame) -> pd.DataFrame:
    d = df1m.copy()
    d["date"] = d["timestamp"].dt.date
    out = d.groupby("date", as_index=False).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))
    out["timestamp"] = pd.to_datetime(out["date"])
    return out[["timestamp", "open", "high", "low", "close", "volume"]]


t0 = time.time()
df1 = pd.read_csv(NIFTY_1MIN_VOL)
df1["timestamp"] = pd.to_datetime(df1["timestamp"])
print(f"loaded {len(df1):,} 1-min bars w/ volume ({time.time()-t0:.1f}s)")

ohlc = {"1m": df1[["timestamp", "open", "high", "low", "close", "volume"]].copy()}
for m, label in [(5, "5m"), (15, "15m"), (30, "30m"), (60, "60m")]:
    ohlc[label] = resample_intraday_v(df1, m)
daily_v = resample_daily_v(df1)
ohlc["D"] = daily_v
# weekly/monthly need volume too -- reuse the OHLC resamplers' grouping via a
# volume-aware daily->weekly/monthly rollup (sum volume same as OHLC agg)
d = daily_v.set_index("timestamp")
ohlc["W"] = d.resample("W-FRI", label="left", closed="left").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
ohlc["M"] = d.resample("MS").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()

for k, v in ohlc.items():
    print(f"  {k:5s}: {len(v):,} bars")

for tf, is_intraday in [("1m", True), ("5m", True), ("15m", True), ("30m", True),
                         ("60m", True), ("D", False), ("W", False), ("M", False)]:
    t = time.time()
    result = multi_mode_calc(ohlc[tf], is_intraday=is_intraday)
    out_csv = f"{OUT_DIR}\\multi_mode_{tf}.csv"
    out_pq = f"{OUT_DIR}\\multi_mode_{tf}.parquet"
    result.to_csv(out_csv, index=False)
    result.to_parquet(out_pq, index=False)
    print(f"  {tf:5s}: {len(result):,} rows, {time.time()-t:.1f}s -> {out_csv}")

print(f"\nTOTAL: {time.time()-t0:.1f}s")
