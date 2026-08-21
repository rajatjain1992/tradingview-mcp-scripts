"""Track A (revised): swing-hold, 3-TF gated crossover on NIFTY.
Combos: (fast=15m, mid=1h, slow=D) and (fast=1h, mid=D, slow=W).
Signal = fast crosses above mid, gated by slow rsi2>50 (bullish HTF state).
Held for days -- event-bounded MFE/MAE (next-signal-or-365d-cap), same
methodology as the stock study.
"""
import os
import numpy as np
import pandas as pd
from triple_tf_core import load_1m, build_signal, bucket_by_mfe

FWD_HORIZONS = {"1w": 7, "2w": 14, "30d": 30, "60d": 60, "90d": 90, "180d": 180, "365d": 365}
MFE_MAE_CAP_DAYS = 365
OUT_DIR = os.path.dirname(__file__)

COMBOS = [
    ("15m-1h-D", 15, 60, "D"),
    ("1h-D-W", 60, "D", "W"),
]

df1m = load_1m()

for name, fast_tf, mid_tf, slow_tf in COMBOS:
    print(f"\n{'='*70}\nCOMBO {name}: fast={fast_tf} mid={mid_tf} slow={slow_tf}\n{'='*70}")
    d = build_signal(df1m, fast_tf, mid_tf, slow_tf)
    close = d["close"].to_numpy(dtype=float)
    dates = d["timestamp"].to_numpy()
    n = len(d)
    print(f"joined rows: {n}  raw cross_up: {d['cross_up'].sum()}  gated signal_raw: {d['signal_raw'].sum()}")

    min_gap_days = 5 if isinstance(fast_tf, int) and fast_tf <= 60 else 10
    raw_idx = np.where(d["signal_raw"].to_numpy())[0]
    sig_idx = []
    last_t = None
    for i in raw_idx:
        if last_t is None or (dates[i] - last_t) / np.timedelta64(1, "D") >= min_gap_days:
            sig_idx.append(i)
        last_t = dates[i]
    sig_idx = np.array(sig_idx, dtype=int)
    print(f"independent signals (>={min_gap_days}d gap): {len(sig_idx)}")

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

    ev = d.loc[sig_idx, ["timestamp", "close", "fast_rsi", "mid_rsi", "slow_rsi", "fast_adx",
                          "mfe", "mae", "final_ret", "window_days"] + [f"fwd_{k}" for k in FWD_HORIZONS]].copy()

    def stat(s):
        s = s.dropna() * 100
        return f"n={len(s):4d} win%={100*(s>0).mean():5.1f}% mean={s.mean():+6.2f}% median={s.median():+6.2f}%" if len(s) else "n=0"

    print("\nSignal events:")
    for k in FWD_HORIZONS:
        print(f"  {k:6}: {stat(ev[f'fwd_{k}'])}")
    print("\nBaseline (every fast-TF bar, gate_bull only, same universe):")
    base = d[d["gate_bull"]]
    for k in FWD_HORIZONS:
        print(f"  {k:6}: {stat(base[f'fwd_{k}'])}")

    print(f"\nMFE/MAE (n={ev['mfe'].notna().sum()}):")
    if ev["mfe"].notna().sum() > 0:
        m = ev.dropna(subset=["mfe", "mae", "final_ret"])
        print(f"  MFE    p25={m.mfe.quantile(.25)*100:+.1f}% median={m.mfe.median()*100:+.1f}% p75={m.mfe.quantile(.75)*100:+.1f}%")
        print(f"  MAE    p25={m.mae.quantile(.25)*100:+.1f}% median={m.mae.median()*100:+.1f}% p75={m.mae.quantile(.75)*100:+.1f}%")
        print(f"  final  p25={m.final_ret.quantile(.25)*100:+.1f}% median={m.final_ret.median()*100:+.1f}% p75={m.final_ret.quantile(.75)*100:+.1f}%")

    top = bucket_by_mfe(ev, n_buckets=5, top_n=2)
    fname = os.path.join(OUT_DIR, f"swing_{name.replace('-','_')}_events.csv")
    ev.to_csv(fname, index=False)
    print(f"\nsaved -> {fname} ({len(ev)} events)")
