"""Pull daily OHLC for one 50-symbol batch (market-cap-ranked universe, from
scripts/mtf_classifier/full_universe_batches.csv) and run the Daily-RSI-crosses-
above-Weekly-RSI study. Read-only SELECT against BigQuery.

Usage: python run_batch.py <batch_num>   (1-indexed, 50 symbols per batch)
Appends full raw signal data (entry OHLC, indicator state, MFE/MAE, and both
the % return AND raw OHLC at every checkpoint from 1W to 365d) to
rsi_cross_study/events_all.parquet, replacing any prior rows for the same
symbols so reruns are safe. Kept deliberately quiet per-batch (just row
counts) -- run analyze_all.py separately for the full bucket/RSI/ADX
breakdown across the combined dataset.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from rsi_cross_core import compute_symbol, extract_events

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
skipped_short = []
for sym, g in df.groupby("symbol"):
    d = compute_symbol(g[["timestamp", "open", "high", "low", "close"]])
    if d is None:
        skipped_short.append(sym)
        continue
    ev = extract_events(d, sym)
    if len(ev):
        all_events.append(ev)

if skipped_short:
    print(f"[warn] {len(skipped_short)} symbols skipped (insufficient history <~1.5yr): {skipped_short}")

if not all_events:
    print("no crossover events found in this batch")
    sys.exit(0)

ev_clean = pd.concat(all_events, ignore_index=True)  # whipsaw-deduped inside compute_symbol
ev_clean["batch"] = batch_num
print(f"batch {batch_num}: {len(ev_clean)} independent signals across {ev_clean['symbol'].nunique()} symbols "
      f"(win%@365d={100*(ev_clean['fwd_365d']>0).mean():.1f}%)")

# accumulate into the running combined file, replacing old rows for these symbols
if os.path.exists(EVENTS_PARQUET):
    prior = pd.read_parquet(EVENTS_PARQUET)
    prior = prior[~prior["symbol"].isin(batch_syms)]
    combined = pd.concat([prior, ev_clean], ignore_index=True)
else:
    combined = ev_clean
combined = combined.drop_duplicates(subset=["symbol", "timestamp"]).sort_values(["symbol", "timestamp"])
combined.to_parquet(EVENTS_PARQUET, index=False)
print(f"saved -> {EVENTS_PARQUET} ({len(combined)} total events, {combined['symbol'].nunique()} symbols so far)")
