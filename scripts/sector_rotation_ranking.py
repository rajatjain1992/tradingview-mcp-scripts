"""
Daily sector / industry rotation ranking (Tier 2 + 3 of the smart-chart-reading funnel).
Local (non-Colab) dev/quick-check version — sector + industry return ranking only.
For the full pipeline (adds stock-level RSI/ADX momentum pattern + relative-RSI vs
NIFTY), see sector_rotation_ranking_colab.py, which is the canonical/final script.

Ranks all NSE indices (and, within them, my_list's basic_industry groups) by
relative-strength return vs NIFTY, so the daily chart-reading effort goes to
the sectors/industries actually rotating up, not all 3,400 stocks equally.

Self-contained: fetches index constituents live from BigQuery each run (like
the Colab script) rather than depending on a local CSV cache.

Data sources:
- scripts/data/close_prices_all.parquet — local cache (scrip, trade_date, close),
  refreshed incrementally from rajat-trade.stock_data_set.stock_daily_prices_dhan
- rajat-trade.stock_data_set.nse_index_constituents — index_name -> member symbols
  (latest snapshot), used for industry aggregation and to synthesize a return
  series for indices with no direct BQ series (mcap-weighted from constituents)
- my_list Google Sheet (via gspread) — scrip -> macro/sector/industry/basic_industry
  + market_cap_cr (weights) + avgvolume/avgclose/tradingdays (liquidity filter)

Usage:
    python sector_rotation_ranking.py --refresh          # re-pull latest day from BQ first
    python sector_rotation_ranking.py                    # use cached parquet only
    python sector_rotation_ranking.py --min-avgvolume 50000 --min-avgclose 20
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
CLOSE_PARQUET = DATA_DIR / "close_prices_all.parquet"
SHEET_KEY = "1aoEgOhQkAAv8b2NqAWtZUYXG41rOal77i0XasevyNtE"
SHEET_WORKSHEET = "my_list"
SERVICE_ACCOUNT_JSON = r"C:\Users\Rajat\Downloads\Daily Trade Files\rajat-trade-c411eaec7c51.json"
BQ_TABLE = "rajat-trade.stock_data_set.stock_daily_prices_dhan"
INDEX_CONSTITUENTS_TABLE = "rajat-trade.stock_data_set.nse_index_constituents"
BQ_PROJECT = "rajat-trade"

LOOKBACKS = {"1w": 5, "1m": 21, "3m": 63}
RANK_ON = "1m"  # which lookback drives the primary sort

# index_name (nse_index_constituents) -> scrip name in stock_daily_prices_dhan,
# for the ~33 indices that have a direct daily series. The rest get a synthetic
# mcap-weighted series built from their constituents instead. Kept identical to
# sector_rotation_ranking_colab.py's INDEX_BQ_MAP.
INDEX_BQ_MAP = {
    "BANKNIFTY": "BANKNIFTY", "NIFTY100": "NIFTY 100", "NIFTY200": "NIFTY 200",
    "NIFTY50": "NIFTY", "NIFTY500": "NIFTY 500", "NIFTYAUTO": "NIFTY AUTO",
    "NIFTYCOMMODITIES": "NIFTY COMMODITIES", "NIFTYCONSRDURBL": "NIFTY CONSR DURBL",
    "NIFTYCONSUMPTION": "NIFTY CONSUMPTION", "NIFTYCPSE": "NIFTYCPSE",
    "NIFTYENERGY": "NIFTY ENERGY", "NIFTYFMCG": "NIFTY FMCG",
    "NIFTYHEALTHCARE": "NIFTY HEALTHCARE", "NIFTYINFRA": "NIFTYINFRA",
    "NIFTYIT": "NIFTYIT", "NIFTYLARGEMIDCAP250": "NIFTY LARGEMID250",
    "NIFTYMEDIA": "NIFTY MEDIA", "NIFTYMETAL": "NIFTY METAL",
    "NIFTYMICROCAP250": "NIFTY MICROCAP250", "NIFTYMIDCAP150": "NIFTY MIDCAP 150",
    "NIFTYMIDCAP50": "NIFTYMCAP50", "NIFTYMIDSMALLCAP400": "NIFTY MIDSMALLCAP 400",
    "NIFTYMNC": "NIFTY MNC", "NIFTYNEXT50": "NIFTYNXT50",
    "NIFTYPHARMA": "NIFTY PHARMA", "NIFTYPSE": "NIFTYPSE",
    "NIFTYPSUBANK": "NIFTY PSU BANK", "NIFTYPVTBANK": "NIFTY PVT BANK",
    "NIFTYREALTY": "NIFTY REALTY", "NIFTYSERVICE": "NIFTY SERV SECTOR",
    "NIFTYSMALLCAP100": "NIFTY SMALLCAP 100", "NIFTYSMALLCAP250": "NIFTY SMALLCAP 250",
    "NIFTYTOTALMARKET": "NIFTY TOTAL MKT",
}


def fetch_index_constituents(client) -> pd.DataFrame:
    q = f"""
        SELECT index_name, symbol
        FROM `{INDEX_CONSTITUENTS_TABLE}`
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `{INDEX_CONSTITUENTS_TABLE}`)
    """
    return client.query(q).to_arrow().to_pandas()


def _wanted_scrips(constituents: pd.DataFrame) -> list:
    """Union of my_list scrips + all index constituent symbols + mapped index
    BQ names — the fetch filters to exactly this set instead of pulling
    unrelated rows (F&O/options rows etc.) from the table."""
    my_list = load_my_list()
    scrips = set(my_list["scrip"]) | set(constituents["symbol"]) | set(INDEX_BQ_MAP.values())
    return sorted(scrips)


def refresh_latest_day(client, constituents: pd.DataFrame):
    """Fetch via .to_arrow().to_pandas() with no server-side ORDER BY (sort
    locally instead) and a scrip filter, per the fast-python-calc skill's
    proven BigQuery fetch pattern."""
    import time

    scrips = _wanted_scrips(constituents)
    scrip_list_sql = ",".join(f"'{s}'" for s in scrips)
    print(f"Scrips: {len(scrips)}", file=sys.stderr)

    if CLOSE_PARQUET.exists():
        cached = pd.read_parquet(CLOSE_PARQUET)
        last_date = cached["trade_date"].max()
        t = time.time()
        q = f"""
            SELECT scrip, trade_date, close FROM `{BQ_TABLE}`
            WHERE scrip IN ({scrip_list_sql}) AND trade_date > DATE('{last_date}')
        """
        new_rows = client.query(q).to_arrow().to_pandas()
        print(f"Fetch (incremental): {time.time()-t:.1f}s | {len(new_rows):,} rows", file=sys.stderr)
        if new_rows.empty:
            return cached
        combined = pd.concat([cached, new_rows], ignore_index=True)
    else:
        t = time.time()
        q = f"""
            SELECT scrip, trade_date, close FROM `{BQ_TABLE}`
            WHERE scrip IN ({scrip_list_sql})
        """
        combined = client.query(q).to_arrow().to_pandas()
        print(f"Fetch (full): {time.time()-t:.1f}s | {len(combined):,} rows", file=sys.stderr)

    t = time.time()
    combined["trade_date"] = pd.to_datetime(combined["trade_date"])
    combined["close"] = combined["close"].astype("float32")
    combined.sort_values(["scrip", "trade_date"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    print(f"Cast+Sort: {time.time()-t:.1f}s", file=sys.stderr)

    combined.to_parquet(CLOSE_PARQUET, index=False)
    print(f"Cached through {combined['trade_date'].max().date()}", file=sys.stderr)
    return combined


def load_my_list():
    import gspread

    gc = gspread.service_account(filename=SERVICE_ACCOUNT_JSON)
    sh = gc.open_by_key(SHEET_KEY)
    ws = sh.worksheet(SHEET_WORKSHEET)
    rows = ws.get_all_values()
    header = rows[0]
    df = pd.DataFrame(rows[1:], columns=header)
    df = df[df["scrip"] != ""]
    for col in ["market_cap_cr", "avgclose", "avgvolume", "tradingdays"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_returns(close_series: pd.Series) -> dict:
    """close_series indexed by trade_date, ascending, for one instrument."""
    close_series = close_series.dropna().sort_index()
    if len(close_series) < max(LOOKBACKS.values()) + 1:
        return {k: np.nan for k in LOOKBACKS}
    last = close_series.iloc[-1]
    out = {}
    for label, n in LOOKBACKS.items():
        prior = close_series.iloc[-1 - n]
        out[label] = round(100 * (last / prior - 1), 2)
    return out


def mcap_weighted_series(symbols, weights, price_wide: pd.DataFrame) -> pd.Series:
    """Build a synthetic index level series as the mcap-weighted average of
    normalized (rebased to 100 at the first common date) constituent closes."""
    cols = [s for s in symbols if s in price_wide.columns]
    if not cols:
        return pd.Series(dtype=float)
    sub = price_wide[cols].dropna(how="all")
    rebased = sub / sub.bfill().iloc[0] * 100
    w = pd.Series({s: weights.get(s, 0) for s in cols})
    if w.sum() == 0:
        w = pd.Series({s: 1 for s in cols})  # equal-weight fallback
    w = w / w.sum()
    return (rebased * w).sum(axis=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Pull latest rows from BigQuery first")
    parser.add_argument("--min-avgvolume", type=float, default=25000)
    parser.add_argument("--min-avgclose", type=float, default=10)
    parser.add_argument("--min-tradingdays", type=float, default=60)
    parser.add_argument("--out-sector", default=str(DATA_DIR / "sector_ranking_latest.csv"))
    parser.add_argument("--out-industry", default=str(DATA_DIR / "industry_ranking_latest.csv"))
    args = parser.parse_args()

    import os
    from google.cloud import bigquery
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_JSON
    client = bigquery.Client(project=BQ_PROJECT)
    constituents = fetch_index_constituents(client)
    index_members = constituents.groupby("index_name")["symbol"].apply(list).to_dict()

    if args.refresh:
        prices = refresh_latest_day(client, constituents)
    else:
        if not CLOSE_PARQUET.exists():
            print("No cached parquet found. Run with --refresh first.", file=sys.stderr)
            sys.exit(1)
        prices = pd.read_parquet(CLOSE_PARQUET)

    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    price_wide = prices.pivot_table(index="trade_date", columns="scrip", values="close")

    my_list = load_my_list()
    liquid = my_list[
        (my_list["avgvolume"] >= args.min_avgvolume)
        & (my_list["avgclose"] >= args.min_avgclose)
        & (my_list["tradingdays"] >= args.min_tradingdays)
    ].copy()
    weights = dict(zip(my_list["scrip"], my_list["market_cap_cr"].fillna(0)))
    print(f"Liquid universe: {len(liquid)} / {len(my_list)} stocks", file=sys.stderr)

    # --- Tier 2: sector/index ranking ---
    sector_rows = []
    for index_name, members in index_members.items():
        if index_name in INDEX_BQ_MAP and INDEX_BQ_MAP[index_name] in price_wide.columns:
            series = price_wide[INDEX_BQ_MAP[index_name]]
            source = "direct"
        else:
            series = mcap_weighted_series(members, weights, price_wide)
            source = "synthetic"
        if series.empty:
            continue
        rets = compute_returns(series)
        sector_rows.append({"index_name": index_name, "source": source, "n_members": len(members), **rets})

    sector_df = pd.DataFrame(sector_rows).sort_values(RANK_ON, ascending=False)
    sector_df.insert(0, "rank", range(1, len(sector_df) + 1))
    sector_df.to_csv(args.out_sector, index=False)
    print(f"Sector ranking -> {args.out_sector} ({len(sector_df)} indices)", file=sys.stderr)

    # --- Tier 3: basic_industry ranking (liquid universe only) ---
    industry_rows = []
    for basic_industry, grp in liquid.groupby("basic_industry"):
        if not basic_industry:
            continue
        series = mcap_weighted_series(list(grp["scrip"]), weights, price_wide)
        if series.empty:
            continue
        rets = compute_returns(series)
        industry_rows.append({
            "basic_industry": basic_industry,
            "sector": grp["sector"].mode().iat[0] if not grp["sector"].mode().empty else "",
            "macro_economic_sector": grp["macro_economic_sector"].mode().iat[0] if not grp["macro_economic_sector"].mode().empty else "",
            "n_stocks": len(grp),
            **rets,
        })

    industry_df = pd.DataFrame(industry_rows).sort_values(RANK_ON, ascending=False)
    industry_df.insert(0, "rank", range(1, len(industry_df) + 1))
    industry_df.to_csv(args.out_industry, index=False)
    print(f"Industry ranking -> {args.out_industry} ({len(industry_df)} basic_industry groups)", file=sys.stderr)

    print("\nTop 10 sectors by 1m return:")
    print(sector_df.head(10).to_string(index=False))
    print("\nTop 10 basic_industry groups by 1m return:")
    print(industry_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
