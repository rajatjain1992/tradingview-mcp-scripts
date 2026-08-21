"""Fuzzy threshold-band search on the INTRADAY spread-exhaustion signedPct across
4 timeframes (v5/v15/v30/v60), given explicit bounds by Rajat directly (matches
his own reading of the live chart's Data Window). Different flavor from 01/02/05's
z-score nearest-neighbor: hard bounds, not distance ranking -- simpler and more
interpretable, closer to how the existing repo convention (day_context.py,
analyze_similar.py in Downloads/Daily Trade Files/MTF_V4/) does it.

Run 2026-08-21 with bounds (v5 in [-2,20], v15 in [-60,-30], v30 in [-40,-20],
v60 in [-20,0]): 21 independent onsets since 2018, notably including the very last
5m bar of 2026-08-20 itself. Finding: same-day continuation tends positive (67% up
to EOD) but historically FADES by the next session (only 35% up next-day close,
mean -49) -- an intraday-bounce-then-reversal pattern, not a clean directional
signal. Edit the `mask = (...)` block to reuse with different bounds/timeframes.
"""
import pandas as pd, numpy as np

df = pd.read_csv(r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\mtf_indicators_intraday.csv")
df = df[df.tf == "5m"].copy()
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.sort_values("timestamp").reset_index(drop=True)
df["day"] = df["timestamp"].dt.date

mask = (
    df.v_5m.between(-2, 20) &
    df.v_15m.between(-60, -30) &
    df.v_30m.between(-40, -20) &
    df.v_60m.between(-20, 0)
)
print(f"matching 5m bars: {mask.sum()} / {len(df)}")

# independent onset = first bar of a same-day contiguous match run
onset = mask & ~(mask.shift(1, fill_value=False) & (df.day == df.day.shift(1)))
ev = df[onset].copy()
print(f"independent onsets: {len(ev)}  across {ev.day.nunique()} distinct days")
print(f"date span: {ev.timestamp.min()} -> {ev.timestamp.max()}\n")

close = df["close"].values
dayarr = df["day"].values
N = len(df)
eod = df.groupby("day").tail(1).set_index("day")["close"]
days_sorted = sorted(df.day.unique())
next_day = {d: (days_sorted[i+1] if i+1 < len(days_sorted) else None) for i,d in enumerate(days_sorted)}
last_close_by_day = df.groupby("day").tail(1).set_index("day")["close"]

def fwd_bars(i, n_bars):  # n_bars of 5m = n_bars*5 minutes
    j = i + n_bars
    return close[j] - close[i] if j < N and dayarr[j] == dayarr[i] else np.nan

rows = []
for i in ev.index:
    d = dayarr[i]
    nd = next_day.get(d)
    ndc = (last_close_by_day[nd] - close[i]) if nd else np.nan
    rows.append(dict(
        t=df.timestamp[i], close=close[i],
        v5=df.v_5m[i], v15=df.v_15m[i], v30=df.v_30m[i], v60=df.v_60m[i],
        rsi5=df.rsi_5m[i], adx5=df.adx_5m[i],
        f15=fwd_bars(i,3), f30=fwd_bars(i,6), f60=fwd_bars(i,12), f120=fwd_bars(i,24),
        toEOD=eod[d]-close[i], nextDayClose=ndc,
    ))
R = pd.DataFrame(rows)

print("=== matching events ===")
print(f"{'time':17} {'close':>9} {'v5':>6} {'v15':>6} {'v30':>6} {'v60':>6} | {'f15':>6} {'f30':>6} {'f60':>6} {'f120':>6} {'toEOD':>7} {'nextD':>7}")
for _,r in R.iterrows():
    print(f"{str(r.t)[:16]:17} {r.close:9.2f} {r.v5:6.1f} {r.v15:6.1f} {r.v30:6.1f} {r.v60:6.1f} | "
          f"{r.f15:+6.0f} {r.f30:+6.0f} {r.f60:+6.0f} {r.f120:+6.0f} {r.toEOD:+7.0f} {r.nextDayClose:+7.0f}")

def stat(col):
    s = R[col].dropna()
    if len(s)==0: return "n=0"
    return f"n={len(s):3d}  mean={s.mean():+7.1f}  median={s.median():+7.1f}  %up={100*(s>0).mean():4.0f}%  std={s.std():6.1f}"

print("\n=== forward outcomes ===")
for c,label in [("f15","+15min"),("f30","+30min"),("f60","+60min"),("f120","+120min"),("toEOD","to EOD"),("nextDayClose","next-day close")]:
    print(f"  {label:15s}: {stat(c)}")
