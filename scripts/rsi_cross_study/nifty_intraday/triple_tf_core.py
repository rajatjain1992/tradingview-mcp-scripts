"""Shared core for 3-timeframe RSI signals on NIFTY: fast crosses mid,
gated by slow's STATE (bullish = slow rsi2 > 50), long-only. Per Rajat's
request 2026-08-21: test multiple TF triplets, both as swing holds (days)
and intraday-close holds (same session), tracking event-bounded MFE/MAE and
percentile-bucketing like the daily/weekly stock study.

TF spec: an int = minutes (resample_intraday), or "D" / "W" for daily/weekly.
Same validated rsi2 = EMA(RSI(close,8),8) variable throughout.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pine"))
import numpy as np
import pandas as pd
from mtf_indicators import mtf_rsi_adx_calc, resample_intraday, resample_daily, resample_weekly

RSI_LEN = 8
DMI_LEN = 8
NIFTY_1M = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv"


def load_1m():
    df1 = pd.read_csv(NIFTY_1M)
    df1["timestamp"] = pd.to_datetime(df1["timestamp"])
    return df1


def get_ohlc(df1m, tf, daily_cache=None):
    if tf == "D":
        return daily_cache if daily_cache is not None else resample_daily(df1m)
    if tf == "W":
        d = daily_cache if daily_cache is not None else resample_daily(df1m)
        return resample_weekly(d)
    return resample_intraday(df1m, tf)


def get_offset(tf):
    if tf == "D":
        return pd.Timedelta(days=1)
    if tf == "W":
        return pd.Timedelta(days=7)
    return pd.Timedelta(minutes=tf)


def tf_label(tf):
    return tf if isinstance(tf, str) else f"{tf}m"


def build_signal(df1m, fast_tf, mid_tf, slow_tf):
    """Returns the fast-TF-grain dataframe with fast_rsi/mid_rsi/slow_rsi,
    fast_adx, cross_up (fast crosses above mid), gate_bull (slow rsi2>50),
    and signal_raw = cross_up & gate_bull. No lookahead: mid/slow values are
    joined using last-CLOSED-bar semantics (each shifted forward by its own
    bar duration before merge_asof)."""
    daily_cache = resample_daily(df1m) if ("D" in (fast_tf, mid_tf, slow_tf) or "W" in (fast_tf, mid_tf, slow_tf)) else None

    fast = get_ohlc(df1m, fast_tf, daily_cache)
    mid = get_ohlc(df1m, mid_tf, daily_cache)
    slow = get_ohlc(df1m, slow_tf, daily_cache)

    fc = mtf_rsi_adx_calc(fast, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
    fast = fast.copy()
    fast["fast_rsi"] = fc["rsi2"].to_numpy()
    fast["fast_adx"] = fc["adx"].to_numpy()

    mc = mtf_rsi_adx_calc(mid, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
    mid = mid.copy()
    mid["mid_rsi"] = mc["rsi2"].to_numpy()
    mid["avail_from"] = mid["timestamp"] + get_offset(mid_tf)

    sc = mtf_rsi_adx_calc(slow, dmi_len=DMI_LEN, rsi_len=RSI_LEN)
    slow = slow.copy()
    slow["slow_rsi"] = sc["rsi2"].to_numpy()
    slow["avail_from"] = slow["timestamp"] + get_offset(slow_tf)

    d = pd.merge_asof(
        fast.sort_values("timestamp"),
        mid[["avail_from", "mid_rsi"]].rename(columns={"avail_from": "timestamp"}).sort_values("timestamp"),
        on="timestamp", direction="backward",
    )
    d = pd.merge_asof(
        d.sort_values("timestamp"),
        slow[["avail_from", "slow_rsi"]].rename(columns={"avail_from": "timestamp"}).sort_values("timestamp"),
        on="timestamp", direction="backward",
    )
    d = d.dropna(subset=["fast_rsi", "mid_rsi", "slow_rsi"]).reset_index(drop=True)

    prev_f = d["fast_rsi"].shift(1)
    prev_m = d["mid_rsi"].shift(1)
    d["cross_up"] = (prev_f <= prev_m) & (d["fast_rsi"] > d["mid_rsi"]) & prev_f.notna() & prev_m.notna()
    d["gate_bull"] = d["slow_rsi"] > 50
    d["signal_raw"] = d["cross_up"] & d["gate_bull"]
    return d


def summarize(sub, label, cols=("f", "eod")):
    print(f"\n=== {label} (n={len(sub)}) ===")
    if len(sub) == 0:
        return
    for c in sub.attrs.get("horizon_cols", []):
        s = sub[c].dropna() * 100
        if len(s) == 0:
            continue
        print(f"  {c:10}: n={len(s):5d}  win%={100*(s>0).mean():5.1f}%  mean={s.mean():+6.2f}%  median={s.median():+6.2f}%")


def bucket_by_mfe(ev, n_buckets=10, top_n=3):
    sub = ev.dropna(subset=["mfe", "mae", "final_ret"]).copy()
    if len(sub) < n_buckets * 5:
        print(f"  [skip bucketing -- only {len(sub)} events, too few for {n_buckets} buckets]")
        return sub
    sub["mfe_bucket"] = pd.qcut(sub["mfe"], n_buckets, labels=False, duplicates="drop") + 1
    print(f"\n  bkt   mfe range              n  avg_mae  avg_final  win%")
    for b in sorted(sub["mfe_bucket"].unique()):
        g = sub[sub["mfe_bucket"] == b]
        print(f"  {int(b):3d}  {g['mfe'].min()*100:+7.2f}%..{g['mfe'].max()*100:+7.2f}%  {len(g):4d}  "
              f"{g['mae'].mean()*100:+6.2f}%  {g['final_ret'].mean()*100:+7.2f}%  {100*(g['final_ret']>0).mean():5.1f}%")
    top_buckets = sorted(sub["mfe_bucket"].unique())[-top_n:]
    return sub[sub["mfe_bucket"].isin(top_buckets)].sort_values("mfe", ascending=False)
