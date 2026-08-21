"""TRACK A: intraday SIGNAL, swing HOLD on NIFTY.

Same core mechanism as the stock study (rsi_cross_core.py) -- fast-TF rsi2
crosses above slow-TF rsi2 -- but shifted down one rung: 240m (fast) crosses
Daily (slow), instead of Daily crosses Weekly. Held for days, not exited
same-day. This is the closest single-symbol analog to the ALREADY-VALIDATED
"1h/4h/D bull alignment, swing horizon" exception in mtf-rsi-derived-rules
memory (PF 1.15-1.55, long-only, ~3 trades/mo) -- not a blind new test.

Same event-bounded MFE/MAE methodology, same rsi2=EMA(RSI(close,8),8)
variable, same last-closed-bar join discipline (240m bar visible on Daily
grid only once actually closed -- shift join key forward by 240min).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pine"))
import numpy as np
import pandas as pd
from mtf_indicators import mtf_rsi_adx_calc, resample_intraday, resample_daily

RSI_LEN = 8
DMI_LEN = 8
FWD_HORIZONS = {"1w": 7, "2w": 14, "30d": 30, "60d": 60, "90d": 90, "180d": 180, "365d": 365}
MFE_MAE_CAP_DAYS = 365
MIN_GAP_DAYS = 5  # shorter than the daily/weekly study -- 240m signals fire more often

NIFTY_1M = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv"

df1 = pd.read_csv(NIFTY_1M)
df1["timestamp"] = pd.to_datetime(df1["timestamp"])

daily = resample_daily(df1)
fast = resample_intraday(df1, 240)

print(f"daily bars: {len(daily)}  240m bars: {len(fast)}")

daily_calc = mtf_rsi_adx_calc(daily, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
daily = daily.copy()
daily["slow_rsi"] = daily_calc["rsi2"].to_numpy()
daily["avail_from"] = daily["timestamp"] + pd.Timedelta(days=1)  # visible from next session

fast_calc = mtf_rsi_adx_calc(fast, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
fast = fast.copy()
fast["fast_rsi"] = fast_calc["rsi2"].to_numpy()
fast["fast_adx"] = fast_calc["adx"].to_numpy()

d = pd.merge_asof(
    fast.sort_values("timestamp"),
    daily[["avail_from", "slow_rsi"]].rename(columns={"avail_from": "timestamp"}).sort_values("timestamp"),
    on="timestamp", direction="backward",
)
d = d.dropna(subset=["fast_rsi", "slow_rsi"]).reset_index(drop=True)
close = d["close"].to_numpy(dtype=float)
dates = d["timestamp"].to_numpy()
n = len(d)
print(f"joined rows (240m bars w/ valid daily context): {n}")

prev_f = d["fast_rsi"].shift(1)
prev_s = d["slow_rsi"].shift(1)
d["cross_up"] = (prev_f <= prev_s) & (d["fast_rsi"] > d["slow_rsi"]) & prev_f.notna() & prev_s.notna()
print(f"raw cross_up count: {d['cross_up'].sum()}")

raw_idx = np.where(d["cross_up"].to_numpy())[0]
sig_idx = []
last_t = None
for i in raw_idx:
    if last_t is None or (dates[i] - last_t) / np.timedelta64(1, "D") >= MIN_GAP_DAYS:
        sig_idx.append(i)
    last_t = dates[i]
sig_idx = np.array(sig_idx, dtype=int)
print(f"independent signals (>= {MIN_GAP_DAYS}d gap): {len(sig_idx)}")

for label, days in FWD_HORIZONS.items():
    target = dates + np.timedelta64(days, "D")
    idx = np.searchsorted(dates, target, side="left")
    fwd = np.full(n, np.nan)
    valid = idx < n
    fwd[valid] = close[idx[valid]] / close[valid] - 1.0
    d[f"fwd_{label}"] = fwd

cap_target = dates + np.timedelta64(MFE_MAE_CAP_DAYS, "D")
cap_idx = np.searchsorted(dates, cap_target, side="left")
mfe = np.full(n, np.nan); mae = np.full(n, np.nan); final_ret = np.full(n, np.nan); window_days = np.full(n, np.nan)
for k, i in enumerate(sig_idx):
    next_i = sig_idx[k + 1] if k + 1 < len(sig_idx) else n
    j = min(cap_idx[i], next_i - 1, n - 1)
    if j <= i:
        continue
    path = close[i : j + 1]
    mfe[i] = path.max() / close[i] - 1.0
    mae[i] = path.min() / close[i] - 1.0
    final_ret[i] = close[j] / close[i] - 1.0
    window_days[i] = (dates[j] - dates[i]) / np.timedelta64(1, "D")
d["mfe"] = mfe; d["mae"] = mae; d["final_ret"] = final_ret; d["window_days"] = window_days

ev = d.loc[sig_idx, ["timestamp", "close", "fast_rsi", "slow_rsi", "fast_adx", "mfe", "mae", "final_ret", "window_days"]
           + [f"fwd_{k}" for k in FWD_HORIZONS]].copy()

# baseline: every 240m bar, unconditional
def summarize(sub, label):
    print(f"\n=== {label} (n={len(sub)}) ===")
    if len(sub) == 0:
        return
    print(f"{'horizon':8} {'n':>5} {'win%':>6} {'mean%':>7} {'median%':>8}")
    for k in FWD_HORIZONS:
        s = sub[f"fwd_{k}"].dropna() * 100
        if len(s) == 0:
            continue
        print(f"{k:8} {len(s):5d} {100*(s>0).mean():5.1f}% {s.mean():+6.1f}% {s.median():+7.1f}%")

summarize(ev, "TRACK A: 240m crosses Daily, signal events")
summarize(d, "TRACK A: baseline (every 240m bar)")

ev.to_csv(os.path.join(os.path.dirname(__file__), "track_a_events.csv"), index=False)
print(f"\nsaved -> track_a_events.csv ({len(ev)} rows)")
print(ev[["timestamp","close","fast_rsi","slow_rsi","fast_adx","mfe","mae","final_ret","window_days"]].tail(20).to_string(index=False))
