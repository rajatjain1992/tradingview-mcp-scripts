"""Pull daily OHLC for one 50-symbol batch (market-cap-ranked universe, from
scripts/mtf_classifier/full_universe_batches.csv) and run the Daily-RSI-crosses-
above-Weekly-RSI study. Read-only SELECT against BigQuery.

Usage: python run_batch.py <batch_num>   (1-indexed, 50 symbols per batch)
Appends events to rsi_cross_study/events_all.parquet (deduped by symbol+timestamp
on rerun) so batches accumulate into one growing dataset.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from rsi_cross_core import (compute_symbol, extract_events, extract_raw_events, summarize,
                             summarize_mfe_mae, bucket_by_mfe)

PROJECT = "rajat-trade"
KEY = r"C:\Users\Rajat\Downloads\Daily Trade Files\rajat-trade-c411eaec7c51.json"

BATCH_SIZE = 50
OUT_DIR = os.path.dirname(__file__)
UNIVERSE_CSV = os.path.join(OUT_DIR, "..", "mtf_classifier", "full_universe_batches.csv")
EVENTS_PARQUET = os.path.join(OUT_DIR, "events_all.parquet")

batch_num = int(sys.argv[1]) if len(sys.argv) > 1 else 1

uni = pd.read_csv(UNIVERSE_CSV).drop_duplicates(subset=["scrip"]).reset_index(drop=True)
lo, hi = (batch_num - 1) * BATCH_SIZE, batch_num * BATCH_SIZE
batch_syms = uni.iloc[lo:hi]["scrip"].tolist()
if not batch_syms:
    print(f"batch {batch_num}: empty (universe has {len(uni)} symbols, {-(-len(uni)//BATCH_SIZE)} batches total)")
    sys.exit(0)
print(f"batch {batch_num}: {len(batch_syms)} symbols, mcap rank {lo+1}-{lo+len(batch_syms)}")
print(batch_syms)

creds = service_account.Credentials.from_service_account_file(KEY)
c = bigquery.Client(project=PROJECT, credentials=creds)
sl = ",".join(f"'{s}'" for s in batch_syms)
t0 = time.time()
job = c.query(f"""SELECT scrip, trade_date, open, high, low, close
    FROM `rajat-trade.stock_data_set.stock_daily_prices_dhan`
    WHERE scrip IN ({sl}) AND trade_date >= '2005-01-01'
    ORDER BY scrip, trade_date""")
df = job.result().to_dataframe(create_bqstorage_client=True)
print(f"[bq] {len(df):,} rows | {job.total_bytes_processed/1e6:.2f} MB scanned | {time.time()-t0:.1f}s")
df["timestamp"] = pd.to_datetime(df["trade_date"])
df = df.rename(columns={"scrip": "symbol"})

found_syms = set(df["symbol"].unique())
missing = [s for s in batch_syms if s not in found_syms]
if missing:
    print(f"[warn] {len(missing)} symbols with no daily rows in BQ: {missing}")

all_events = []
all_raw_events = []
all_days = []
skipped_short = []
for sym, g in df.groupby("symbol"):
    d = compute_symbol(g[["timestamp", "open", "high", "low", "close"]])
    if d is None:
        skipped_short.append(sym)
        continue
    ev = extract_events(d, sym)
    if len(ev):
        all_events.append(ev)
    ev_raw_sym = extract_raw_events(d, sym)
    if len(ev_raw_sym):
        all_raw_events.append(ev_raw_sym)
    dd = d.dropna(subset=["weekly_rsi"]).copy()
    dd.insert(0, "symbol", sym)
    all_days.append(dd)

if skipped_short:
    print(f"[warn] {len(skipped_short)} symbols skipped (insufficient history <~1.5yr): {skipped_short}")

if not all_events:
    print("no crossover events found in this batch")
    sys.exit(0)

ev_raw = pd.concat(all_raw_events, ignore_index=True)
ev_clean = pd.concat(all_events, ignore_index=True)  # already whipsaw-deduped inside compute_symbol

summarize(ev_raw, f"batch {batch_num} RAW (incl. whipsaw)")
summarize(ev_clean, f"batch {batch_num} DEDUPED (>={10}d gap per symbol, independent signals only)")

baseline = pd.concat(all_days, ignore_index=True)
summarize(baseline.assign(symbol=baseline["symbol"]), f"batch {batch_num} BASELINE (every day, unconditional buy-and-hold)")

print(f"\n=== batch {batch_num} DEDUPED events segmented by weekly_rsi level at cross ===")
bins = [0, 20, 30, 40, 50, 60, 100]
labels = ["0-20", "20-30", "30-40", "40-50", "50-60", "60+"]
ev_clean["wrsi_bucket"] = pd.cut(ev_clean["weekly_rsi"], bins=bins, labels=labels)
for b in labels:
    sub = ev_clean[ev_clean["wrsi_bucket"] == b]
    if len(sub) == 0:
        continue
    summarize(sub, f"weekly_rsi {b} at cross")

# ADX flavour -- trend-strength context on both timeframes at the moment of
# the cross, kept as its OWN dimension (not crossed with the RSI buckets yet
# -- 50 stocks isn't enough sample to slice both at once without the cells
# going empty; do that once more batches are in).
adx_bins = [0, 15, 20, 25, 30, 100]
adx_labels = ["<15", "15-20", "20-25", "25-30", "30+"]
print(f"\n=== batch {batch_num} DEDUPED events segmented by DAILY ADX at cross ===")
ev_clean["dadx_bucket"] = pd.cut(ev_clean["daily_adx"], bins=adx_bins, labels=adx_labels)
for b in adx_labels:
    sub = ev_clean[ev_clean["dadx_bucket"] == b]
    if len(sub) == 0:
        continue
    summarize(sub, f"daily_adx {b} at cross")

print(f"\n=== batch {batch_num} DEDUPED events segmented by WEEKLY ADX at cross ===")
ev_clean["wadx_bucket"] = pd.cut(ev_clean["weekly_adx"], bins=adx_bins, labels=adx_labels)
for b in adx_labels:
    sub = ev_clean[ev_clean["wadx_bucket"] == b]
    if len(sub) == 0:
        continue
    summarize(sub, f"weekly_adx {b} at cross")

# MFE/MAE, event-bounded windows (no more blind 1y -- see compute_symbol docstring)
summarize_mfe_mae(ev_clean, "ALL deduped events (any weekly_rsi/adx level)")

# Step 1+2: percentile-bucket every event by MFE, surface the top-3 buckets
top_events = bucket_by_mfe(ev_clean, n_buckets=10, top_n=3)
top_events_out = os.path.join(OUT_DIR, f"top_mfe_events_batch{batch_num}.csv")
top_events.to_csv(top_events_out, index=False)
print(f"\ntop-bucket events saved -> {top_events_out}")
print(top_events[["symbol", "timestamp", "close", "weekly_rsi", "weekly_adx", "mfe", "mae", "final_ret", "window_days"]].to_string(index=False))

# accumulate into the running combined file (dedupe by symbol+timestamp so reruns are safe)
ev_clean["batch"] = batch_num
if os.path.exists(EVENTS_PARQUET):
    prior = pd.read_parquet(EVENTS_PARQUET)
    prior = prior[~prior["symbol"].isin(batch_syms)]  # drop old rows for this batch's symbols, replace fresh
    combined = pd.concat([prior, ev_clean], ignore_index=True)
else:
    combined = ev_clean
combined = combined.drop_duplicates(subset=["symbol", "timestamp"]).sort_values(["symbol", "timestamp"])
combined.to_parquet(EVENTS_PARQUET, index=False)
print(f"\nsaved -> {EVENTS_PARQUET} ({len(combined)} total events across all batches run so far)")
