"""Run the full RSI-bucket / ADX-bucket / MFE-percentile-bucket breakdown on
the COMBINED events_all.parquet (all batches run so far), not just one batch.
This is what to rerun as more batches land, to re-check whether the batch-1
findings (weekly_rsi 20-30, weekly_adx<15, MFE bucket 9 characteristics) hold
up at larger sample size -- per Rajat's "revisit after a few more batches".
"""
import os
import pandas as pd
from rsi_cross_core import summarize, summarize_mfe_mae, bucket_by_mfe, FWD_HORIZONS

OUT_DIR = os.path.dirname(__file__)
EVENTS_PARQUET = os.path.join(OUT_DIR, "events_all.parquet")

ev = pd.read_parquet(EVENTS_PARQUET)
print(f"combined dataset: {len(ev)} events, {ev['symbol'].nunique()} symbols, "
      f"batches {sorted(ev['batch'].unique())}")

summarize(ev, "ALL BATCHES combined")

print(f"\n=== segmented by weekly_rsi level at cross ===")
bins = [0, 20, 30, 40, 50, 60, 100]
labels = ["0-20", "20-30", "30-40", "40-50", "50-60", "60+"]
ev["wrsi_bucket"] = pd.cut(ev["weekly_rsi"], bins=bins, labels=labels)
for b in labels:
    sub = ev[ev["wrsi_bucket"] == b]
    if len(sub):
        summarize(sub, f"weekly_rsi {b} at cross")

adx_bins = [0, 15, 20, 25, 30, 100]
adx_labels = ["<15", "15-20", "20-25", "25-30", "30+"]
print(f"\n=== segmented by WEEKLY ADX at cross ===")
ev["wadx_bucket"] = pd.cut(ev["weekly_adx"], bins=adx_bins, labels=adx_labels)
for b in adx_labels:
    sub = ev[ev["wadx_bucket"] == b]
    if len(sub):
        summarize(sub, f"weekly_adx {b} at cross")

summarize_mfe_mae(ev, "ALL events, all batches")
top = bucket_by_mfe(ev, n_buckets=10, top_n=3)
top.to_csv(os.path.join(OUT_DIR, "top_mfe_events_all.csv"), index=False)
print(f"\ntop-3-bucket events saved -> top_mfe_events_all.csv ({len(top)} rows)")

# window/rate/context re-check for the bucket-9-driver question
sub = ev.dropna(subset=["mfe", "mae", "final_ret", "window_days"]).copy()
sub = sub[sub.window_days > 0]
sub["mfe_bucket"] = pd.qcut(sub["mfe"], 10, labels=False, duplicates="drop") + 1
sub["mfe_per_30d"] = sub["mfe"] / sub["window_days"] * 30
g = sub.groupby("mfe_bucket").agg(
    n=("mfe", "size"), mfe=("mfe", "mean"), window_days=("window_days", "mean"),
    mfe_per_30d=("mfe_per_30d", "mean"), weekly_rsi=("weekly_rsi", "mean"),
    daily_adx=("daily_adx", "mean"), weekly_adx=("weekly_adx", "mean"),
    n_symbols=("symbol", "nunique"),
)
print("\n=== bucket 9 driver re-check (rate of ascent, RSI/ADX context) ===")
print(g.round(2))
