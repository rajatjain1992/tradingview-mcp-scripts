"""Track B (revised): intraday-CLOSE hold, 3-TF gated crossover on NIFTY.
Combos: (fast=1m, mid=5m, slow=15m) and (fast=15m, mid=1h, slow=D).
Signal = fast crosses above mid, gated by slow rsi2>50. Exit forced by same
session's close -- MFE/MAE bounded by (next signal, session end), whichever
first. No overnight hold.
"""
import os
import numpy as np
import pandas as pd
from triple_tf_core import load_1m, build_signal, bucket_by_mfe

OUT_DIR = os.path.dirname(__file__)
COMBOS = [
    ("1m-5m-15m", 1, 5, 15, 5),     # name, fast, mid, slow, min_gap_minutes
    ("15m-1h-D", 15, 60, "D", 30),
]

df1m = load_1m()

for name, fast_tf, mid_tf, slow_tf, min_gap_min in COMBOS:
    print(f"\n{'='*70}\nCOMBO {name}: fast={fast_tf} mid={mid_tf} slow={slow_tf}\n{'='*70}")
    d = build_signal(df1m, fast_tf, mid_tf, slow_tf)
    d["date"] = d["timestamp"].dt.date
    close = d["close"].to_numpy(dtype=float)
    dates = d["timestamp"].to_numpy()
    dayarr = d["date"].to_numpy()
    n = len(d)
    print(f"joined rows: {n}  sessions: {d['date'].nunique()}  raw cross_up: {d['cross_up'].sum()}  gated: {d['signal_raw'].sum()}")

    raw_idx = np.where(d["signal_raw"].to_numpy())[0]
    sig_idx = []
    last_t = None
    for i in raw_idx:
        if last_t is None or (dates[i] - last_t) / np.timedelta64(1, "m") >= min_gap_min:
            sig_idx.append(i)
        last_t = dates[i]
    sig_idx = np.array(sig_idx, dtype=int)
    print(f"independent signals (>={min_gap_min}min gap): {len(sig_idx)} (~{len(sig_idx)/d['date'].nunique():.2f}/day)")

    # session-end index per row (last bar of the same date)
    session_end_idx = pd.Series(d.index, index=d["date"]).groupby(level=0).last()
    row_session_end = d["date"].map(session_end_idx).to_numpy()

    mfe = np.full(n, np.nan); mae = np.full(n, np.nan); final_ret = np.full(n, np.nan); window_bars = np.full(n, np.nan)
    for k, i in enumerate(sig_idx):
        next_i = sig_idx[k + 1] if k + 1 < len(sig_idx) else n
        j = min(row_session_end[i], next_i - 1, n - 1)
        if j <= i:
            continue
        path = close[i : j + 1]
        mfe[i] = path.max() / close[i] - 1.0
        mae[i] = path.min() / close[i] - 1.0
        final_ret[i] = close[j] / close[i] - 1.0
        window_bars[i] = j - i
    d["mfe"] = mfe; d["mae"] = mae; d["final_ret"] = final_ret; d["window_bars"] = window_bars

    ev = d.loc[sig_idx, ["timestamp", "close", "fast_rsi", "mid_rsi", "slow_rsi", "fast_adx",
                          "mfe", "mae", "final_ret", "window_bars"]].copy()
    ev = ev.dropna(subset=["mfe", "mae", "final_ret"])

    def stat(s):
        s = s.dropna() * 100
        return f"n={len(s):4d} win%={100*(s>0).mean():5.1f}% mean={s.mean():+6.3f}% median={s.median():+6.3f}%" if len(s) else "n=0"

    print(f"\nSignal events, same-day final_ret: {stat(ev['final_ret'])}")
    print(f"  avg window length: {ev['window_bars'].mean():.1f} bars of the fast TF ({fast_tf})")
    print(f"  (no formal baseline for intraday mode -- compare win% against 50% coin-flip directly)")

    print(f"\nMFE/MAE:")
    print(f"  MFE    p25={ev.mfe.quantile(.25)*100:+.2f}% median={ev.mfe.median()*100:+.2f}% p75={ev.mfe.quantile(.75)*100:+.2f}%")
    print(f"  MAE    p25={ev.mae.quantile(.25)*100:+.2f}% median={ev.mae.median()*100:+.2f}% p75={ev.mae.quantile(.75)*100:+.2f}%")
    print(f"  final  p25={ev.final_ret.quantile(.25)*100:+.2f}% median={ev.final_ret.median()*100:+.2f}% p75={ev.final_ret.quantile(.75)*100:+.2f}%")

    top = bucket_by_mfe(ev, n_buckets=5, top_n=2)
    fname = os.path.join(OUT_DIR, f"intraday_{name.replace('-','_')}_events.csv")
    ev.to_csv(fname, index=False)
    print(f"\nsaved -> {fname} ({len(ev)} events)")
