"""Track B (v3): intraday-CLOSE hold, 3-TF gated crossover on NIFTY.
Combos per Rajat 2026-08-21: (fast=5m, mid=15m, slow=1h) and
(fast=15m, mid=1h, slow=D). 1m dropped ("too fast").

TRADE DEFINITION CHANGED (temporary, per Rajat): entry = fast crosses above
mid (gated by slow rsi2>50); exit = fast crosses back BELOW mid, capped by
same session's close (no overnight hold) if the cross-down hasn't happened
yet. This is a natural indicator-defined trade lifecycle, not an arbitrary
next-signal/365d-style bound -- explicitly a placeholder ahead of real
SL/Target design, not the final exit rule.

Full entry AND exit bar OHLC now saved per trade (not just close/points),
per Rajat's request -- lets him eyeball every trade against the live chart.

MFE/MAE measured from bar HIGH/LOW (not close) across the whole entry-to-
exit path -- the bug caught 2026-08-21 on the 2026-08-18 NIFTY trade (close-
only missed a +31.65pt intrabar spike that a high-based path.max() catches).
Output in POINTS, not %, per Rajat's feedback (NIFTY option P&L scales with
points, not the index's own tiny % moves).
"""
import os
import numpy as np
import pandas as pd
from triple_tf_core import load_1m, build_signal

OUT_DIR = os.path.dirname(__file__)
COMBOS = [
    ("5m-15m-1h", 5, 15, 60),
    ("15m-1h-D", 15, 60, "D"),
]

df1m = load_1m()

for name, fast_tf, mid_tf, slow_tf in COMBOS:
    print(f"\n{'='*70}\nCOMBO {name}: fast={fast_tf} mid={mid_tf} slow={slow_tf}\n{'='*70}")
    d = build_signal(df1m, fast_tf, mid_tf, slow_tf)
    d["date"] = d["timestamp"].dt.date
    o = d["open"].to_numpy(dtype=float)
    h = d["high"].to_numpy(dtype=float)
    l = d["low"].to_numpy(dtype=float)
    c = d["close"].to_numpy(dtype=float)
    fast_rsi = d["fast_rsi"].to_numpy(dtype=float)
    mid_rsi = d["mid_rsi"].to_numpy(dtype=float)
    dates = d["timestamp"].to_numpy()
    dayarr = d["date"].to_numpy()
    n = len(d)
    print(f"joined rows: {n}  sessions: {d['date'].nunique()}  raw cross_up: {d['cross_up'].sum()}  gated: {d['signal_raw'].sum()}")

    session_end_idx = pd.Series(d.index, index=d["date"]).groupby(level=0).last()
    row_session_end = d["date"].map(session_end_idx).to_numpy()

    below = fast_rsi < mid_rsi  # cross-down condition (state, not just the transition bar)

    signal_raw = d["signal_raw"].to_numpy()
    rows = []
    i = 0
    last_exit = -1
    while i < n:
        if signal_raw[i] and i > last_exit:
            entry_i = i
            sess_end = row_session_end[entry_i]
            # first bar AFTER entry where fast has crossed back below mid
            exit_i = None
            for j in range(entry_i + 1, sess_end + 1):
                if below[j]:
                    exit_i = j
                    break
            forced_exit = exit_i is None
            if exit_i is None:
                exit_i = sess_end
            if exit_i > entry_i:
                path_h = h[entry_i : exit_i + 1]
                path_l = l[entry_i : exit_i + 1]
                rows.append(dict(
                    entry_time=dates[entry_i], entry_open=o[entry_i], entry_high=h[entry_i],
                    entry_low=l[entry_i], entry_close=c[entry_i],
                    exit_time=dates[exit_i], exit_open=o[exit_i], exit_high=h[exit_i],
                    exit_low=l[exit_i], exit_close=c[exit_i],
                    exit_reason=("session_end_forced" if forced_exit else "cross_down"),
                    fast_rsi_entry=fast_rsi[entry_i], mid_rsi_entry=mid_rsi[entry_i],
                    slow_rsi_entry=d["slow_rsi"].iloc[entry_i], fast_adx_entry=d["fast_adx"].iloc[entry_i],
                    mfe_pts=path_h.max() - c[entry_i], mae_pts=path_l.min() - c[entry_i],
                    final_pts=c[exit_i] - c[entry_i], window_bars=exit_i - entry_i,
                ))
            last_exit = exit_i
            i = exit_i + 1
        else:
            i += 1

    ev = pd.DataFrame(rows)
    print(f"trades (entry=cross up & gate, exit=cross down or session end): {len(ev)}  (~{len(ev)/d['date'].nunique():.2f}/day)")
    if len(ev):
        print(f"  exit reason breakdown: {ev['exit_reason'].value_counts().to_dict()}")

    def stat(s):
        s = s.dropna()
        return f"n={len(s):4d} win%={100*(s>0).mean():5.1f}% mean={s.mean():+6.2f}pts median={s.median():+6.2f}pts" if len(s) else "n=0"

    print(f"\nfinal_pts: {stat(ev['final_pts'])}")
    print(f"MFE/MAE (points, high/low-based):")
    print(f"  MFE    p25={ev.mfe_pts.quantile(.25):+.1f} median={ev.mfe_pts.median():+.1f} p75={ev.mfe_pts.quantile(.75):+.1f}")
    print(f"  MAE    p25={ev.mae_pts.quantile(.25):+.1f} median={ev.mae_pts.median():+.1f} p75={ev.mae_pts.quantile(.75):+.1f}")
    print(f"  window (bars): median={ev.window_bars.median():.0f} mean={ev.window_bars.mean():.1f} p90={ev.window_bars.quantile(.9):.0f}")

    if len(ev) >= 50:
        ev["mfe_bucket"] = pd.qcut(ev["mfe_pts"], 10, labels=False, duplicates="drop") + 1
        bucket_ranges = ev.groupby("mfe_bucket")["mfe_pts"].agg(["min", "max"])
        ev["mfe_bucket_label"] = ev["mfe_bucket"].map(
            lambda b: f"{int(b)} ({bucket_ranges.loc[b,'min']:+.0f} to {bucket_ranges.loc[b,'max']:+.0f} pts)")
        print(f"\n  bkt   mfe_pts range         n  avg_mae_pts  avg_final_pts  win%")
        for b in sorted(ev["mfe_bucket"].unique()):
            g = ev[ev["mfe_bucket"] == b]
            print(f"  {int(b):3d}  {g['mfe_pts'].min():+7.1f}..{g['mfe_pts'].max():+7.1f}  {len(g):4d}  "
                  f"{g['mae_pts'].mean():+9.1f}  {g['final_pts'].mean():+11.1f}  {100*(g['final_pts']>0).mean():5.1f}%")
        # put the trade-outcome summary columns up front, right after entry/exit times,
        # so they're visible without scrolling past all the OHLC columns
        front = ["entry_time", "exit_time", "exit_reason", "mfe_bucket", "mfe_bucket_label",
                 "mfe_pts", "mae_pts", "final_pts", "window_bars"]
        ev = ev[front + [c for c in ev.columns if c not in front]]

    fname = os.path.join(OUT_DIR, f"intraday_{name.replace('-','_')}_events_v3.csv")
    ev.to_csv(fname, index=False)
    print(f"\nsaved -> {fname} ({len(ev)} trades)")
