"""TRACK C: ADX-gated oversold bounce on NIFTY 60m -- the angle already
validated in mtf-rsi-cycle-research memory (finding #4: "ADX flips oversold
from BOUNCE to CONTINUATION"). Re-running on the current full dataset
(2017-2026, vs the prior 2019-2026 panel) with the validated rsi2/adx
variable to confirm the effect still holds and get a current read.

Rule: 60m rsi2 < 30 (oversold), segmented by 60m ADX tercile at that moment.
Prior finding: low/mid ADX oversold bounces (+9 to +13pt), high ADX oversold
does NOT bounce (continues falling).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pine"))
import numpy as np
import pandas as pd
from mtf_indicators import mtf_rsi_adx_calc, resample_intraday

NIFTY_1M = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv"
RSI_LEN = 8
DMI_LEN = 8

df1 = pd.read_csv(NIFTY_1M)
df1["timestamp"] = pd.to_datetime(df1["timestamp"])
df1["date"] = df1["timestamp"].dt.date

tf60 = resample_intraday(df1, 60)
calc = mtf_rsi_adx_calc(tf60, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
tf60 = tf60.copy()
tf60["rsi2"] = calc["rsi2"].to_numpy()
tf60["adx"] = calc["adx"].to_numpy()
tf60 = tf60.dropna(subset=["rsi2", "adx"]).reset_index(drop=True)
tf60["date"] = tf60["timestamp"].dt.date
close = tf60["close"].to_numpy(dtype=float)
dates = tf60["timestamp"].to_numpy()
dayarr = tf60["date"].to_numpy()
n = len(tf60)
print(f"60m bars: {n}")

# ADX terciles computed on the whole sample (fixed cutpoints, not rolling --
# simple and matches the prior median-split methodology closely enough for a
# first check)
adx_low, adx_high = tf60["adx"].quantile([1/3, 2/3])
print(f"ADX tercile cutpoints: low<{adx_low:.1f}  mid  high>{adx_high:.1f}")

oversold = tf60["rsi2"] < 30
# only count the FIRST bar of a contiguous oversold run as the event (avoid
# re-counting the same episode every bar it stays under 30)
onset = oversold & ~(oversold.shift(1, fill_value=False))
print(f"oversold onsets: {onset.sum()}")

eod_close = tf60.groupby("date")["close"].last()

def fwd_bars(i, n_bars):
    j = i + n_bars
    return close[j] / close[i] - 1.0 if j < n else np.nan

def fwd_eod(i):
    return eod_close[dayarr[i]] / close[i] - 1.0

rows = []
for i in np.where(onset.to_numpy())[0]:
    adx_i = tf60["adx"].iloc[i]
    regime = "low" if adx_i < adx_low else ("high" if adx_i > adx_high else "mid")
    rows.append(dict(t=dates[i], close=close[i], rsi2=tf60["rsi2"].iloc[i], adx=adx_i, regime=regime,
                      f1=fwd_bars(i,1), f2=fwd_bars(i,2), f4=fwd_bars(i,4), f8=fwd_bars(i,8), eod=fwd_eod(i)))
ev = pd.DataFrame(rows)

def stat(s):
    s = s.dropna()*100
    if len(s)==0: return "n=0"
    return f"n={len(s):4d}  win%={100*(s>0).mean():5.1f}%  mean={s.mean():+6.2f}%  median={s.median():+6.2f}%"

print("\n=== TRACK C: 60m RSI<30 onset, forward return by ADX regime ===")
for regime in ["low", "mid", "high"]:
    sub = ev[ev.regime==regime]
    print(f"\n-- {regime} ADX (n={len(sub)}) --")
    for c,label in [("f1","+1 bar (60m)"),("f2","+2 bars (2h)"),("f4","+4 bars (4h)"),("f8","+8 bars (~1.5d)"),("eod","to EOD")]:
        print(f"  {label:16}: {stat(sub[c])}")

ev.to_csv(os.path.join(os.path.dirname(__file__), "track_c_events.csv"), index=False)
print(f"\nsaved -> track_c_events.csv ({len(ev)} rows)")
