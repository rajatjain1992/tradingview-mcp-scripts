"""TRACK B: intraday SIGNAL, intraday HOLD (exit same day) on NIFTY.

This is the config already tested and REJECTED as "S4" in mtf-rsi-derived-
rules memory (606,971 1m bars, 2020-23 train/2024-26 test, ~190 configs,
coin flip, fires ~16/day, "the move already happened"). Re-verifying with
the CURRENT full dataset (2017-2026) and the validated rsi2=EMA(RSI(close,8),8)
variable, using 5m crosses 15m as the pair (closest simple analog to the
prior 1m/5m/15m/60m composite). Exits are same-day only -- no overnight hold.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pine"))
import numpy as np
import pandas as pd
from mtf_indicators import mtf_rsi_adx_calc, resample_intraday

NIFTY_1M = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv"
RSI_LEN = 8
DMI_LEN = 8
MIN_GAP_MIN = 15  # collapse re-triggers within 15min

df1 = pd.read_csv(NIFTY_1M)
df1["timestamp"] = pd.to_datetime(df1["timestamp"])
df1["date"] = df1["timestamp"].dt.date

fast = resample_intraday(df1, 5)
slow = resample_intraday(df1, 15)
print(f"5m bars: {len(fast)}  15m bars: {len(slow)}")

fast_calc = mtf_rsi_adx_calc(fast, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
fast = fast.copy()
fast["fast_rsi"] = fast_calc["rsi2"].to_numpy()
fast["fast_adx"] = fast_calc["adx"].to_numpy()

slow_calc = mtf_rsi_adx_calc(slow, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
slow = slow.copy()
slow["slow_rsi"] = slow_calc["rsi2"].to_numpy()
slow["avail_from"] = slow["timestamp"] + pd.Timedelta(minutes=15)

d = pd.merge_asof(
    fast.sort_values("timestamp"),
    slow[["avail_from", "slow_rsi"]].rename(columns={"avail_from": "timestamp"}).sort_values("timestamp"),
    on="timestamp", direction="backward",
)
d = d.dropna(subset=["fast_rsi", "slow_rsi"]).reset_index(drop=True)
d["date"] = d["timestamp"].dt.date
close = d["close"].to_numpy(dtype=float)
dates = d["timestamp"].to_numpy()
dayarr = d["date"].to_numpy()
n = len(d)
print(f"joined 5m rows: {n}, spanning {d['date'].nunique()} sessions")

prev_f = d["fast_rsi"].shift(1)
prev_s = d["slow_rsi"].shift(1)
d["cross_up"] = (prev_f <= prev_s) & (d["fast_rsi"] > d["slow_rsi"]) & prev_f.notna() & prev_s.notna()
print(f"raw cross_up: {d['cross_up'].sum()}  (~{d['cross_up'].sum()/d['date'].nunique():.1f}/day)")

raw_idx = np.where(d["cross_up"].to_numpy())[0]
sig_idx = []
last_t = None
for i in raw_idx:
    if last_t is None or (dates[i] - last_t) / np.timedelta64(1, "m") >= MIN_GAP_MIN:
        sig_idx.append(i)
    last_t = dates[i]
sig_idx = np.array(sig_idx, dtype=int)
print(f"independent signals (>={MIN_GAP_MIN}min gap): {len(sig_idx)}  (~{len(sig_idx)/d['date'].nunique():.2f}/day)")

# same-day EOD close per date
eod_close = d.groupby("date")["close"].last()

def fwd_bars(i, n_bars):
    j = i + n_bars
    if j >= n or dayarr[j] != dayarr[i]:
        return np.nan
    return close[j] / close[i] - 1.0

def fwd_eod(i):
    return eod_close[dayarr[i]] / close[i] - 1.0

rows = []
for i in sig_idx:
    rows.append(dict(
        t=dates[i], close=close[i], fast_rsi=d["fast_rsi"].iloc[i], slow_rsi=d["slow_rsi"].iloc[i],
        fast_adx=d["fast_adx"].iloc[i],
        f15=fwd_bars(i, 3), f30=fwd_bars(i, 6), f60=fwd_bars(i, 12), f120=fwd_bars(i, 24),
        eod=fwd_eod(i),
    ))
ev = pd.DataFrame(rows)

# baseline: every 5m bar
rows_b = []
for i in range(n):
    rows_b.append(dict(f15=fwd_bars(i,3), f30=fwd_bars(i,6), f60=fwd_bars(i,12), f120=fwd_bars(i,24), eod=fwd_eod(i)))
base = pd.DataFrame(rows_b)

def stat(s):
    s = s.dropna()*100
    if len(s)==0: return "n=0"
    return f"n={len(s):5d}  win%={100*(s>0).mean():5.1f}%  mean={s.mean():+6.2f}%  median={s.median():+6.2f}%  std={s.std():6.2f}%"

print("\n=== TRACK B signal events (5m crosses 15m, same-day exit) ===")
for c,label in [("f15","+15min"),("f30","+30min"),("f60","+60min"),("f120","+120min"),("eod","to EOD")]:
    print(f"  {label:8}: {stat(ev[c])}")

print("\n=== TRACK B baseline (every 5m bar) ===")
for c,label in [("f15","+15min"),("f30","+30min"),("f60","+60min"),("f120","+120min"),("eod","to EOD")]:
    print(f"  {label:8}: {stat(base[c])}")

ev.to_csv(os.path.join(os.path.dirname(__file__), "track_b_events.csv"), index=False)
print(f"\nsaved -> track_b_events.csv ({len(ev)} rows)")
