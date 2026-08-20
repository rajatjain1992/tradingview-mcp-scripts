# -*- coding: utf-8 -*-
"""Driver: computes the MTF Spread-Exhaustion / MTF RSI / MTF ADX (+ ADX Bowl
detector) indicator suite for NIFTY across 1m/5m/15m/30m/60m (intraday) and
D/W/M, and saves two output files.

Formulas validated against live TradingView (Table View CSV export) --
see scripts/pine/mtf_indicators.py docstring. ADX matched to 12+ decimal
places on both a 5m and a 60m bar; RSI/signedPct match within cross-vendor
tick-level noise (Dhan vs TradingView's own NSE feed).

Pine's request.security() semantics: a lower-TF bar shows the LAST CLOSED
higher-TF bar's value, not whatever HTF bucket its own timestamp falls in.
That's reproduced here with merge_asof(direction='backward') keyed on each
HTF bar's own timestamp (= that bar's START/open time, so the join is valid
starting from when the HTF bar CLOSES, i.e. one bucket duration after the
timestamp values shown here -- close enough for research use; exact-close
alignment would need shifting each HTF series by one bar, matching Pine's
default non-lookahead behavior even more precisely. See NOTE below.)
"""
import sys
import time
sys.path.insert(0, "scripts/pine")
from mtf_indicators import (
    resample_intraday, resample_daily, resample_weekly, resample_monthly,
    spread_exhaustion_calc, mtf_rsi_adx_calc, adx_bowl_detector,
)
import pandas as pd

NIFTY_1MIN = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv"
OUT_DIR = r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4"

t0 = time.time()
df1 = pd.read_csv(NIFTY_1MIN)
df1["timestamp"] = pd.to_datetime(df1["timestamp"])
print(f"loaded {len(df1):,} 1-min bars ({time.time()-t0:.1f}s)")

# --- native per-TF OHLC ------------------------------------------------
ohlc = {"1m": df1[["timestamp", "open", "high", "low", "close"]].copy()}
for m, label in [(5, "5m"), (15, "15m"), (30, "30m"), (60, "60m"), (120, "120m"), (240, "240m")]:
    ohlc[label] = resample_intraday(df1, m)
ohlc["D"] = resample_daily(df1)
ohlc["W"] = resample_weekly(ohlc["D"])
ohlc["M"] = resample_monthly(ohlc["D"])
for k, v in ohlc.items():
    print(f"  {k:5s}: {len(v):,} bars, {v.timestamp.iloc[0]} -> {v.timestamp.iloc[-1]}")

# --- native per-TF spread-exhaustion / rsi / adx (the 9 request.security TFs) ---
HTF_LIST = ["5m", "15m", "30m", "60m", "120m", "240m", "D", "W", "M"]
native = {}
for tf in HTF_LIST:
    t = time.time()
    se = spread_exhaustion_calc(ohlc[tf])
    native[tf] = se
    print(f"  native calc {tf:5s}: {time.time()-t:.1f}s")

# --- current-TF ADX Bowl detector, for each requested base granularity ---
BASE_TFS = ["1m", "5m", "15m", "30m", "60m", "D", "W", "M"]
bowl = {}
for tf in BASE_TFS:
    t = time.time()
    bowl[tf] = adx_bowl_detector(ohlc[tf])
    print(f"  bowl detector {tf:5s}: {time.time()-t:.1f}s ({len(ohlc[tf]):,} bars)")

print(f"\nall calc done: {time.time()-t0:.1f}s total")

# A lower-TF bar only sees a higher-TF bar's value once that HTF bar has
# actually CLOSED -- i.e. join key = HTF bar's own timestamp + its bar
# duration, not its raw (start) timestamp. Confirmed necessary: joining on
# raw timestamps picked up the still-forming 60m bar instead of the last
# closed one, causing a 3+ point ADX mismatch until fixed.
HTF_OFFSET = {
    "5m": pd.Timedelta(minutes=5), "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30), "60m": pd.Timedelta(minutes=60),
    "120m": pd.Timedelta(minutes=120), "240m": pd.Timedelta(minutes=240),
    "D": pd.Timedelta(days=1), "W": pd.Timedelta(days=7), "M": pd.DateOffset(months=1),
}


# --- assemble one merged table per base TF: own OHLC + own bowl state + ---
# --- as-of (last-closed) values from every native HTF series -------------
def assemble(base_tf: str) -> pd.DataFrame:
    base = ohlc[base_tf][["timestamp", "open", "high", "low", "close"]].copy()
    b = bowl[base_tf]
    for col in ["pDI", "mDI", "dx", "adxCur", "adxSlope", "adxNorm", "bowlActive",
                "bullBowlActive", "bearBowlActive", "bullExpansion", "bearExpansion",
                "bullElephant", "bearElephant"]:
        base[f"curTF_{col}"] = b[col].to_numpy()

    for tf in HTF_LIST:
        src = native[tf].rename(columns={
            "signedPct": f"v_{tf}", "signedFan": f"fan_{tf}",
            "rsi2": f"rsi_{tf}", "adx": f"adx_{tf}",
        }).copy()
        src["avail_from"] = src["timestamp"] + HTF_OFFSET[tf]
        src = src.drop(columns=["timestamp"]).rename(columns={"avail_from": "timestamp"})
        base = pd.merge_asof(base.sort_values("timestamp"), src.sort_values("timestamp"),
                              on="timestamp", direction="backward")
    return base


intraday_frames = []
for tf in ["1m", "5m", "15m", "30m", "60m"]:
    t = time.time()
    m = assemble(tf)
    m.insert(1, "tf", tf)
    intraday_frames.append(m)
    print(f"  assembled {tf:5s}: {len(m):,} rows ({time.time()-t:.1f}s)")

dwm_frames = []
for tf in ["D", "W", "M"]:
    m = assemble(tf)
    m.insert(1, "tf", tf)
    dwm_frames.append(m)
    print(f"  assembled {tf:5s}: {len(m):,} rows")

intraday_out = pd.concat(intraday_frames, ignore_index=True)
dwm_out = pd.concat(dwm_frames, ignore_index=True)

intraday_path = f"{OUT_DIR}\\mtf_indicators_intraday.csv"
dwm_path = f"{OUT_DIR}\\mtf_indicators_dwm.csv"
intraday_out.to_csv(intraday_path, index=False)
dwm_out.to_csv(dwm_path, index=False)

print(f"\nsaved: {intraday_path}  ({len(intraday_out):,} rows)")
print(f"saved: {dwm_path}  ({len(dwm_out):,} rows)")
print(f"TOTAL: {time.time()-t0:.1f}s")
