# -*- coding: utf-8 -*-
"""
COLAB SCRIPT — Daily sector / industry / stock rotation ranking + momentum pattern.

WHAT THIS DOES (Tier 2/3/4 of the "smart chart reading" funnel, tradingview-mcp
repo, 2026-08-11): instead of chart-reading all ~3,400 stocks in `my_list` with
equal priority, rank them top-down so daily chart-reading effort goes to what's
actually rotating AND showing improving momentum, not just raw price movers:

  1. SECTOR   — every NSE index (from stock_data_set.nse_index_constituents),
     ranked by 1w/1m/3m % return. Direct BigQuery daily series where the index
     itself is a scrip in stock_daily_prices_dhan; otherwise a market-cap-
     weighted synthetic series built from its constituents.
  2. INDUSTRY — my_list's `basic_industry` groups, ranked the same way
     (mcap-weighted composite of member stocks), liquidity-filtered, dropping
     groups with too few stocks to mean anything.
  3. STOCK    — every liquid stock ranked by its own 1w/1m/3m return, PLUS a
     momentum-pattern read on Daily/Weekly/Monthly: is 8-period RSI (EMA8-
     smoothed, same formula as the MTF pine scripts) improving, and is ADX(8)
     rising or falling. Classified per TF as:
       strengthening  = RSI improving + ADX rising   (trend gaining force)
       supported_turn = RSI improving + ADX falling   (Rajat's validated
                         reversal read: momentum turning while trend force fades)
       weakening      = RSI not improving + ADX rising (against-you trend gaining force)
       fading         = RSI not improving + ADX falling (momentum stalling)
     RSI/ADX code reused verbatim from mtf_lifetime_ema_colab.py (same repo) —
     do not re-derive; that implementation is already TV-validated.

RUN ORDER: paste this whole file into one Colab cell, or split at the "# ---"
markers into separate cells. Needs the service-account JSON for project
`rajat-trade` uploaded to Drive first (same one used by the other BigQuery/
Dhan pipeline notebooks) — it must also have viewer access to the `my_list`
Google Sheet (already granted, used by other scripts against this sheet).

1w/1m/3m = trailing return over 5 / 21 / 63 trading sessions (~1 week / 1
month / 3 months of trading days), not calendar time.

OUTPUT: three CSVs written to Google Drive under
`My Drive/Daily Trade Files/sector_rotation_ranking/`, dated by run date.
"""

# --- 0. setup -------------------------------------------------------------
!pip install -q google-cloud-bigquery pyarrow db-dtypes gspread tqdm

import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
import gspread
from google.cloud import bigquery
from google.oauth2 import service_account
from google.colab import drive
from tqdm.auto import tqdm

drive.mount("/content/drive")

PROJECT = "rajat-trade"
DAILY_TABLE = "rajat-trade.stock_data_set.stock_daily_prices_dhan"
INDEX_CONSTITUENTS_TABLE = "rajat-trade.stock_data_set.nse_index_constituents"

SHEET_KEY = "1aoEgOhQkAAv8b2NqAWtZUYXG41rOal77i0XasevyNtE"
SHEET_WORKSHEET = "my_list"

# same service-account JSON used by the other Dhan/BigQuery pipeline notebooks
SERVICE_ACCOUNT_FILE = "/content/drive/MyDrive/Colab Notebooks/rajat-trade-c411eaec7c51.json"

OUT_DIR = "/content/drive/MyDrive/Daily Trade Files/sector_rotation_ranking"
os.makedirs(OUT_DIR, exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/bigquery",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]
creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
bq = bigquery.Client(project=PROJECT, credentials=creds)
gc = gspread.authorize(creds)

LOOKBACKS = {"1w": 5, "1m": 21, "3m": 63}   # trading sessions
RANK_ON = "1m"
MIN_STOCKS_PER_INDUSTRY = 5                 # drop 1-2 stock "industries" from ranking noise
MIN_AVGVOLUME = 25000
MIN_AVGCLOSE = 10
MIN_TRADINGDAYS = 60
SLOPE_LOOKBACK = 3                          # bars back for "improving/rising" checks (per TF, in that TF's own bars)

# index_name (nse_index_constituents) -> scrip name in stock_daily_prices_dhan,
# for the ~33 indices that have a direct daily series. The rest (mostly
# factor/strategy sub-indices: Capital Markets, Cement, Chemicals, midcap
# momentum/quality variants, etc.) get a synthetic mcap-weighted series built
# from their constituents instead.
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

# --- 1. fetch: my_list (classification + liquidity) ------------------------
t = time.time()
sh = gc.open_by_key(SHEET_KEY)
ws = sh.worksheet(SHEET_WORKSHEET)
rows = ws.get_all_values()
my_list = pd.DataFrame(rows[1:], columns=rows[0])
my_list = my_list[my_list["scrip"] != ""].copy()
for col in ["market_cap_cr", "avgclose", "avgvolume", "tradingdays"]:
    my_list[col] = pd.to_numeric(my_list[col], errors="coerce")
print(f"my_list: {time.time()-t:.1f}s | {len(my_list):,} scrips")

liquid = my_list[
    (my_list["avgvolume"] >= MIN_AVGVOLUME)
    & (my_list["avgclose"] >= MIN_AVGCLOSE)
    & (my_list["tradingdays"] >= MIN_TRADINGDAYS)
].copy()
weights = dict(zip(my_list["scrip"], my_list["market_cap_cr"].fillna(0)))
print(f"Liquid universe: {len(liquid):,} / {len(my_list):,} stocks")

# --- 2. fetch: index constituents (latest snapshot) -------------------------
t = time.time()
q = f"""
    SELECT index_name, symbol
    FROM `{INDEX_CONSTITUENTS_TABLE}`
    WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM `{INDEX_CONSTITUENTS_TABLE}`)
"""
constituents = bq.query(q).to_arrow().to_pandas()
index_members = constituents.groupby("index_name")["symbol"].apply(list).to_dict()
print(f"Index constituents: {time.time()-t:.1f}s | {len(index_members)} indices, {len(constituents):,} rows")

# --- 3. fetch: daily OHLC (Arrow path, no server-side ORDER BY, scrip-filtered) ---
scrips_needed = sorted(set(my_list["scrip"]) | set(constituents["symbol"]) | set(INDEX_BQ_MAP.values()))
scrip_list_sql = ",".join(f"'{s}'" for s in scrips_needed)

t = time.time()
q = f"""
    SELECT scrip, trade_date AS dt, open, high, low, close
    FROM `{DAILY_TABLE}`
    WHERE scrip IN ({scrip_list_sql})
"""
daily_all = bq.query(q).to_arrow().to_pandas()
print(f"Fetch OHLC: {time.time()-t:.1f}s | {len(daily_all):,} rows")

t = time.time()
daily_all["dt"] = pd.to_datetime(daily_all["dt"])
for col in ("open", "high", "low", "close"):
    daily_all[col] = daily_all[col].astype("float32")
daily_all = daily_all.drop_duplicates(subset=["scrip", "dt"])
daily_all.sort_values(["scrip", "dt"], inplace=True)
daily_all.reset_index(drop=True, inplace=True)
price_wide = daily_all.pivot_table(index="dt", columns="scrip", values="close")
print(f"Cast+sort+pivot: {time.time()-t:.1f}s")

# --- 4. helpers: return ranking ---------------------------------------------
def compute_returns(close_series: pd.Series) -> dict:
    close_series = close_series.dropna().sort_index()
    if len(close_series) < max(LOOKBACKS.values()) + 1:
        return {k: np.nan for k in LOOKBACKS}
    last = close_series.iloc[-1]
    return {label: round(100 * (last / close_series.iloc[-1 - n] - 1), 2) for label, n in LOOKBACKS.items()}


def mcap_weighted_series(symbols, weights, price_wide: pd.DataFrame) -> pd.Series:
    cols = [s for s in symbols if s in price_wide.columns]
    if not cols:
        return pd.Series(dtype=float)
    sub = price_wide[cols].dropna(how="all")
    rebased = sub / sub.bfill().iloc[0] * 100
    w = pd.Series({s: weights.get(s, 0) for s in cols})
    if w.sum() == 0:
        w = pd.Series({s: 1 for s in cols})
    w = w / w.sum()
    return (rebased * w).sum(axis=1)


# --- 5. helpers: RSI(8)+EMA8 / ADX(8) — reused verbatim from
#        mtf_lifetime_ema_colab.py, same repo (TV-validated formulas) ------
def _raw_recursive(a: np.ndarray, alpha: float) -> np.ndarray:
    n = len(a)
    o = np.empty(n, dtype=np.float64)
    o[0] = a[0]
    for i in range(1, n):
        o[i] = alpha * a[i] + (1.0 - alpha) * o[i - 1]
    return o

def _matured_recursive(a, length: int, alpha: float) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    n = len(a)
    out = np.full(n, np.nan, dtype=np.float64)
    valid = ~np.isnan(a)
    if not valid.any():
        return out
    start = int(np.argmax(valid))
    out[start:] = _raw_recursive(a[start:], alpha)
    mask_len = min(length - 1, n - start)
    out[start:start + mask_len] = np.nan
    return out

def ema(a, length: int) -> np.ndarray:
    return _matured_recursive(a, length, 2.0 / (length + 1))

def wilder_rma(a, length: int) -> np.ndarray:
    return _matured_recursive(a, length, 1.0 / length)

def rsi_wilder_then_ema(close, rsi_len: int = 8, smooth_len: int = 8) -> np.ndarray:
    close = np.asarray(close, dtype=np.float64)
    delta = np.diff(close, prepend=close[0])
    up = np.clip(delta, 0, None)
    down = np.clip(-delta, 0, None)
    avg_up = wilder_rma(up, rsi_len)
    avg_down = wilder_rma(down, rsi_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_up / avg_down
        rsi_raw = 100.0 - 100.0 / (1.0 + rs)
    rsi_raw = np.where(avg_down == 0, 100.0, rsi_raw)
    return ema(rsi_raw, smooth_len)

def adx_wilder(high, low, close, length: int = 8) -> np.ndarray:
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    up_move = np.diff(high, prepend=high[0])
    down_move = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    sm_tr = wilder_rma(tr, length)
    sm_plus_dm = wilder_rma(plus_dm, length)
    sm_minus_dm = wilder_rma(minus_dm, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100 * sm_plus_dm / sm_tr
        minus_di = 100 * sm_minus_dm / sm_tr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    return wilder_rma(dx, length)


def last_and_slope(arr: np.ndarray, lookback: int):
    """Latest value + whether it's higher than `lookback` bars ago. NaN-safe."""
    valid_idx = np.where(~np.isnan(arr))[0]
    if len(valid_idx) == 0:
        return np.nan, None
    last_i = valid_idx[-1]
    last_val = arr[last_i]
    prior_i = last_i - lookback
    if prior_i < 0 or np.isnan(arr[prior_i]):
        return round(float(last_val), 2), None
    return round(float(last_val), 2), bool(last_val > arr[prior_i])


def classify_pattern(rsi_improving, adx_rising) -> str:
    if rsi_improving is None or adx_rising is None:
        return ""
    if rsi_improving and adx_rising:
        return "strengthening"
    if rsi_improving and not adx_rising:
        return "supported_turn"
    if not rsi_improving and adx_rising:
        return "weakening"
    return "fading"


# --- Relative RSI vs NIFTY (ported from "Relative RSI Momentum vs NIFTY.pine",
#     Daily Trade Files) — same RSI(8)+EMA8 as above, but measured against the
#     benchmark: is the stock's momentum above/below NIFTY's, and is that gap
#     widening (strongBull/strongBear) or turning (weakBull/recovering)? This
#     is the actual relative-strength ranking axis; classify_pattern() above
#     only checks a stock against its OWN history, not the market. ---------
REL_SMOOTH_LEN = 5
REL_ACCEL_LEN = 3

def relative_rsi_state(stock_rsi: np.ndarray, stock_dates, nifty_rsi_series: pd.Series):
    stock_series = pd.Series(stock_rsi, index=pd.to_datetime(stock_dates))
    combined = pd.concat([stock_series.rename("stock"), nifty_rsi_series.rename("nifty")], axis=1, join="inner").sort_index()
    if len(combined) < REL_SMOOTH_LEN + REL_ACCEL_LEN + 5:
        return np.nan, np.nan, ""
    rsi_diff = (combined["stock"] - combined["nifty"]).to_numpy(dtype=np.float64)
    rel_mom = ema(rsi_diff, REL_SMOOTH_LEN)
    accel = np.full(len(rel_mom), np.nan)
    accel[REL_ACCEL_LEN:] = rel_mom[REL_ACCEL_LEN:] - rel_mom[:-REL_ACCEL_LEN]
    valid_idx = np.where(~np.isnan(rel_mom) & ~np.isnan(accel))[0]
    if len(valid_idx) == 0:
        return np.nan, np.nan, ""
    i = valid_idx[-1]
    rm, ac = round(float(rel_mom[i]), 2), round(float(accel[i]), 2)
    if rm > 0 and ac > 0:
        state = "strongBull"
    elif rm > 0 and ac <= 0:
        state = "weakBull"
    elif rm <= 0 and ac > 0:
        state = "recovering"
    else:
        state = "strongBear"
    return rm, ac, state


# --- 6. Tier 2: sector / index ranking --------------------------------------
t = time.time()
sector_rows = []
for index_name, members in index_members.items():
    if index_name in INDEX_BQ_MAP and INDEX_BQ_MAP[index_name] in price_wide.columns:
        series, source = price_wide[INDEX_BQ_MAP[index_name]], "direct"
    else:
        series, source = mcap_weighted_series(members, weights, price_wide), "synthetic"
    if series.empty:
        continue
    sector_rows.append({"index_name": index_name, "source": source, "n_members": len(members), **compute_returns(series)})

sector_df = pd.DataFrame(sector_rows).sort_values(RANK_ON, ascending=False).reset_index(drop=True)
sector_df.insert(0, "rank", range(1, len(sector_df) + 1))
print(f"Sector ranking: {time.time()-t:.1f}s | {len(sector_df)} indices")

# --- 7. Tier 3: basic_industry ranking (liquid universe only) ---------------
t = time.time()
industry_rows = []
for basic_industry, grp in liquid.groupby("basic_industry"):
    if not basic_industry or len(grp) < MIN_STOCKS_PER_INDUSTRY:
        continue
    series = mcap_weighted_series(list(grp["scrip"]), weights, price_wide)
    if series.empty:
        continue
    industry_rows.append({
        "basic_industry": basic_industry,
        "sector": grp["sector"].mode().iat[0] if not grp["sector"].mode().empty else "",
        "macro_economic_sector": grp["macro_economic_sector"].mode().iat[0] if not grp["macro_economic_sector"].mode().empty else "",
        "n_stocks": len(grp),
        **compute_returns(series),
    })

industry_df = pd.DataFrame(industry_rows).sort_values(RANK_ON, ascending=False).reset_index(drop=True)
industry_df.insert(0, "rank", range(1, len(industry_df) + 1))
print(f"Industry ranking: {time.time()-t:.1f}s | {len(industry_df)} basic_industry groups (min {MIN_STOCKS_PER_INDUSTRY} stocks)")

# --- 8. Tier 4: stock-level return ranking ----------------------------------
t = time.time()
stock_rows = []
for _, row in liquid.iterrows():
    scrip = row["scrip"]
    if scrip not in price_wide.columns:
        continue
    rets = compute_returns(price_wide[scrip])
    stock_rows.append({
        "scrip": scrip,
        "name": row.get("name", ""),
        "macro_economic_sector": row["macro_economic_sector"],
        "sector": row["sector"],
        "basic_industry": row["basic_industry"],
        "market_cap_cr": row["market_cap_cr"],
        **rets,
    })

stock_df = pd.DataFrame(stock_rows).dropna(subset=[RANK_ON]).sort_values(RANK_ON, ascending=False).reset_index(drop=True)
stock_df.insert(0, "rank_overall", range(1, len(stock_df) + 1))
stock_df["rank_within_industry"] = (
    stock_df.groupby("basic_industry")[RANK_ON].rank(ascending=False, method="first").astype(int)
)
industry_rank_map = industry_df.set_index("basic_industry")["rank"].to_dict()
stock_df["basic_industry_rank"] = stock_df["basic_industry"].map(industry_rank_map)
print(f"Stock return ranking: {time.time()-t:.1f}s | {len(stock_df):,} stocks")

# --- 9. Tier 4b: RSI(8)+EMA8 / ADX(8) momentum pattern, D/W/M --------------
#         (per-scrip recursive loop -- can't vectorize across scrips; only
#          run over the liquid universe stock_df already ranked above)
t = time.time()
weekly_all = (
    daily_all.groupby(["scrip", pd.Grouper(key="dt", freq="W-FRI")])
    .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    .dropna(subset=["open", "close"]).reset_index()
)
monthly_all = (
    daily_all.groupby(["scrip", pd.Grouper(key="dt", freq="ME")])
    .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    .dropna(subset=["open", "close"]).reset_index()
)
print(f"Weekly/Monthly aggregation: {time.time()-t:.1f}s | W={len(weekly_all):,} rows, M={len(monthly_all):,} rows")

target_scrips = [s for s in stock_df["scrip"] if s in daily_all["scrip"].values]
daily_by_scrip = {s: g for s, g in daily_all[daily_all["scrip"].isin(target_scrips)].groupby("scrip", sort=False)}
weekly_by_scrip = {s: g for s, g in weekly_all[weekly_all["scrip"].isin(target_scrips)].groupby("scrip", sort=False)}
monthly_by_scrip = {s: g for s, g in monthly_all[monthly_all["scrip"].isin(target_scrips)].groupby("scrip", sort=False)}

# NIFTY's own RSI(8)+EMA8 per TF, computed once and reused as the benchmark
# for every stock's relative-RSI state (NIFTY is always fetched -- it's the
# BQ mapping for NIFTY50 in INDEX_BQ_MAP).
nifty_rsi_by_tf = {}
for tf_label, frame in (("d", daily_all), ("w", weekly_all), ("m", monthly_all)):
    ng = frame[frame["scrip"] == "NIFTY"].sort_values("dt")
    if ng.empty:
        nifty_rsi_by_tf[tf_label] = pd.Series(dtype=float)
        continue
    nrsi = rsi_wilder_then_ema(ng["close"].to_numpy(dtype=np.float64), 8, 8)
    nifty_rsi_by_tf[tf_label] = pd.Series(nrsi, index=pd.to_datetime(ng["dt"].values))

t = time.time()
pattern_rows = []
for scrip in tqdm(target_scrips, desc="RSI/ADX momentum + relative-RSI vs NIFTY", unit="scrip"):
    row = {"scrip": scrip}
    for tf_label, by_scrip in (("d", daily_by_scrip), ("w", weekly_by_scrip), ("m", monthly_by_scrip)):
        g = by_scrip.get(scrip)
        if g is None or len(g) < 20:
            row[f"rsi_{tf_label}"] = np.nan
            row[f"adx_{tf_label}"] = np.nan
            row[f"pattern_{tf_label}"] = ""
            row[f"relmom_{tf_label}"] = np.nan
            row[f"relaccel_{tf_label}"] = np.nan
            row[f"relstate_{tf_label}"] = ""
            continue
        g = g.sort_values("dt")
        close = g["close"].to_numpy(dtype=np.float64)
        high = g["high"].to_numpy(dtype=np.float64)
        low = g["low"].to_numpy(dtype=np.float64)
        rsi = rsi_wilder_then_ema(close, 8, 8)
        adx = adx_wilder(high, low, close, 8)
        rsi_val, rsi_improving = last_and_slope(rsi, SLOPE_LOOKBACK)
        adx_val, adx_rising = last_and_slope(adx, SLOPE_LOOKBACK)
        row[f"rsi_{tf_label}"] = rsi_val
        row[f"adx_{tf_label}"] = adx_val
        row[f"pattern_{tf_label}"] = classify_pattern(rsi_improving, adx_rising)

        rm, ac, rel_state = relative_rsi_state(rsi, g["dt"].values, nifty_rsi_by_tf[tf_label])
        row[f"relmom_{tf_label}"] = rm
        row[f"relaccel_{tf_label}"] = ac
        row[f"relstate_{tf_label}"] = rel_state
    pattern_rows.append(row)

pattern_df = pd.DataFrame(pattern_rows)
print(f"RSI/ADX + relative-RSI momentum: {time.time()-t:.1f}s | {len(pattern_df):,} scrips")

stock_df = stock_df.merge(pattern_df, on="scrip", how="left")
BULLISH_PATTERNS = {"strengthening", "supported_turn"}
BULLISH_REL_STATES = {"strongBull", "recovering"}
stock_df["bullish_tf_count"] = stock_df[["pattern_d", "pattern_w", "pattern_m"]].isin(BULLISH_PATTERNS).sum(axis=1)
stock_df["rs_bullish_tf_count"] = stock_df[["relstate_d", "relstate_w", "relstate_m"]].isin(BULLISH_REL_STATES).sum(axis=1)
stock_df["total_bullish_signals"] = stock_df["bullish_tf_count"] + stock_df["rs_bullish_tf_count"]

# --- 10. save + preview -------------------------------------------------
run_date = datetime.now().strftime("%Y-%m-%d")
sector_path = f"{OUT_DIR}/sector_ranking_{run_date}.csv"
industry_path = f"{OUT_DIR}/industry_ranking_{run_date}.csv"
stock_path = f"{OUT_DIR}/stock_ranking_{run_date}.csv"

sector_df.to_csv(sector_path, index=False)
industry_df.to_csv(industry_path, index=False)
stock_df.to_csv(stock_path, index=False)
print(f"\nSaved:\n  {sector_path}\n  {industry_path}\n  {stock_path}")

print("\nTop 10 sectors by 1m return:")
print(sector_df.head(10).to_string(index=False))
print(f"\nTop 10 basic_industry groups by 1m return (min {MIN_STOCKS_PER_INDUSTRY} stocks):")
print(industry_df.head(10).to_string(index=False))
print("\nTop 15 stocks by combined bullish signal count (own-momentum pattern + relative-RSI vs NIFTY, D/W/M):")
print(stock_df.sort_values(["total_bullish_signals", RANK_ON], ascending=False).head(15)[
    ["scrip", "sector", "basic_industry", "1w", "1m", "3m",
     "pattern_d", "pattern_w", "pattern_m", "relstate_d", "relstate_w", "relstate_m", "total_bullish_signals"]
].to_string(index=False))
