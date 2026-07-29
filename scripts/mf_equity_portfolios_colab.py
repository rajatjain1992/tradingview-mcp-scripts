# -*- coding: utf-8 -*-
"""
Load the mutual fund equity portfolio disclosures CSV (SBI / ICICI / HDFC,
last 3 disclosed months) into BigQuery.

Columns: amc, scheme_name, portfolio_date, isin, instrument_name,
         industry_or_rating, quantity, market_value_lakhs, pct_to_nav

RUN THIS IN GOOGLE COLAB. Authenticates to BigQuery via Colab's built-in auth
(no service-account key needed), writes to Rajat's `rajat-trade` project,
table `mutual_fund_data.equity_portfolio_disclosures` (already created, with
440 rows loaded as an earlier proof-of-concept -- this script dedupes against
those before appending, so it's safe to re-run).

On run it prompts you to upload `mf_equity_portfolios.csv`.
"""

# ============================== 0. Setup (Colab) ==============================
# !pip install -q pandas db-dtypes
from google.colab import auth, files
auth.authenticate_user()

PROJECT_ID = "rajat-trade"          # <-- change if needed
DATASET    = "mutual_fund_data"
TABLE      = "equity_portfolio_disclosures"
TABLE_ID   = f"{PROJECT_ID}.{DATASET}.{TABLE}"

import io
import pandas as pd
from google.cloud import bigquery

client = bigquery.Client(project=PROJECT_ID)

# ============================== 1. Upload CSV ==================================
print("Select mf_equity_portfolios.csv when prompted...")
uploaded = files.upload()
csv_name = next(iter(uploaded))
df = pd.read_csv(io.BytesIO(uploaded[csv_name]))

DTYPES = {
    "amc": str, "scheme_name": str, "isin": str,
    "instrument_name": str, "industry_or_rating": str,
}
for col, t in DTYPES.items():
    df[col] = df[col].astype(t)
df["portfolio_date"] = pd.to_datetime(df["portfolio_date"]).dt.date
for col in ("quantity", "market_value_lakhs", "pct_to_nav"):
    df[col] = pd.to_numeric(df[col], errors="coerce")

print(f"Loaded {len(df)} rows from {csv_name} "
      f"({df['amc'].nunique()} AMCs, {df['scheme_name'].nunique()} schemes, "
      f"dates {sorted(df['portfolio_date'].unique())})")

# ============================== 2. Dedup against existing BigQuery rows =======
client.create_dataset(f"{PROJECT_ID}.{DATASET}", exists_ok=True)

try:
    client.get_table(TABLE_ID)
    table_exists = True
except Exception:
    table_exists = False

if table_exists and not df.empty:
    existing = client.query(
        f"SELECT DISTINCT amc, scheme_name, portfolio_date, isin FROM `{TABLE_ID}` "
        f"WHERE portfolio_date BETWEEN '{df['portfolio_date'].min()}' AND '{df['portfolio_date'].max()}'"
    ).to_dataframe()
    if not existing.empty:
        # isin can be NULL/duplicate for non-instrument rows (TREPS, footnotes,
        # industry-allocation summary rows) -- match those on the full row
        # instead of the key, so we don't spuriously drop distinct rows that
        # happen to share a NULL isin.
        key_cols = ["amc", "scheme_name", "portfolio_date", "isin"]
        has_isin = df["isin"].notna()
        dedup_part = df[has_isin].merge(existing, on=key_cols, how="left", indicator=True)
        dedup_part = dedup_part[dedup_part["_merge"] == "left_only"].drop(columns="_merge")
        df = pd.concat([dedup_part, df[~has_isin]], ignore_index=True)

# ============================== 3. Load =======================================
if df.empty:
    print("Nothing new to load (all rows already in BigQuery).")
else:
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", autodetect=True)
    job = client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config)
    job.result()
    print(f"Loaded {len(df)} new rows into {TABLE_ID}")
