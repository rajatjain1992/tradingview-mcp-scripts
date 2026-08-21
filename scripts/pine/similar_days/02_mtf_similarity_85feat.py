"""SECOND PASS -- full multi-timeframe similarity, requested after Rajat pointed out
01_daily_similarity_3methods.py only used Daily. Builds an 85-feature vector: for
each of 9 timeframes (5m/15m/30m/60m/120m/240m/D/W/M) the native spread-exhaustion
signedPct/RSI/ADX value PLUS its 1-bar and 3-bar delta ("last 3-5 candles for
context" per Rajat's instruction) -- not just a single snapshot per TF.

Known bug fixed here: Monthly's signedPct needs ta.percentrank(fan,200) to mature,
but only 113 monthly bars exist total -- M_signedPct and its lag/delta derivatives
are permanently NaN, which silently zeroed the entire candidate pool via dropna()
until that family was excluded. Watch for this if extending the monthly lookback
period in spread_exhaustion_calc.

Result vs 01: bearish tilt confirmed on 1-3 day horizon but fades to near-neutral by
day 5 (weaker than the daily-only version's more extreme call) -- richer context
matters. Saves daily_mtf_feature_panel.parquet for reuse by 03/04.

SUPERSEDED BY 05: this version is oscillator-only (no EMA position, no price
structure) -- see 05_mtf_similarity_97feat_v2.py for the corrected version and
06_explain_match_2026-08-03_dimensionality_check.py for why "more features" isn't
automatically better.
"""
import sys, time
sys.path.insert(0, "scripts/pine")
from mtf_indicators import (resample_intraday, resample_daily, resample_weekly, resample_monthly,
                             spread_exhaustion_calc)
import pandas as pd, numpy as np

t0 = time.time()
df1 = pd.read_csv(r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv")
df1["timestamp"] = pd.to_datetime(df1["timestamp"])

ohlc = {}
for m, label in [(5,"5m"),(15,"15m"),(30,"30m"),(60,"60m"),(120,"120m"),(240,"240m")]:
    ohlc[label] = resample_intraday(df1, m)
ohlc["D"] = resample_daily(df1)
ohlc["W"] = resample_weekly(ohlc["D"])
ohlc["M"] = resample_monthly(ohlc["D"])

HTF_LIST = ["5m","15m","30m","60m","120m","240m","D","W","M"]
HTF_OFFSET = {"5m":pd.Timedelta(minutes=5),"15m":pd.Timedelta(minutes=15),"30m":pd.Timedelta(minutes=30),
              "60m":pd.Timedelta(minutes=60),"120m":pd.Timedelta(minutes=120),"240m":pd.Timedelta(minutes=240),
              "D":pd.Timedelta(days=1),"W":pd.Timedelta(days=7),"M":pd.DateOffset(months=1)}

native = {}
for tf in HTF_LIST:
    se = spread_exhaustion_calc(ohlc[tf])
    for col in ["signedPct","rsi2","adx"]:
        se[f"{col}_lag1"] = se[col].shift(1)
        se[f"{col}_lag3"] = se[col].shift(3)
        se[f"{col}_d1"] = se[col] - se[f"{col}_lag1"]
        se[f"{col}_d3"] = se[col] - se[f"{col}_lag3"]
    native[tf] = se
    print(f"  native {tf:5s}: {len(se):,} bars ({time.time()-t0:.1f}s)")

base = ohlc["D"][["timestamp","open","high","low","close"]].copy()
FEAT_COLS = []
for tf in HTF_LIST:
    cols = ["signedPct","rsi2","adx","signedPct_d1","rsi2_d1","adx_d1","signedPct_d3","rsi2_d3","adx_d3"]
    src = native[tf][["timestamp"] + cols].copy()
    src["avail_from"] = src["timestamp"] + HTF_OFFSET[tf]
    src = src.drop(columns=["timestamp"]).rename(columns={"avail_from":"timestamp"})
    rename = {c: f"{tf}_{c}" for c in cols}
    src = src.rename(columns=rename)
    FEAT_COLS += list(rename.values())
    base = pd.merge_asof(base.sort_values("timestamp"), src.sort_values("timestamp"),
                          on="timestamp", direction="backward")

mm_d = pd.read_csv(r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\multi_mode_D.csv")
mm_d["timestamp"] = pd.to_datetime(mm_d["timestamp"])
mm_extra = mm_d[["timestamp","ema_spread_percentile","price_to_ema200_percentile","momentum","adx14","diPlus14","diMinus14"]].copy()
mm_extra["di_net14"] = mm_extra.diPlus14 - mm_extra.diMinus14
mm_extra["momentum_d3"] = mm_extra["momentum"] - mm_extra["momentum"].shift(3)
mm_extra["ema_spread_percentile_d3"] = mm_extra["ema_spread_percentile"] - mm_extra["ema_spread_percentile"].shift(3)
base = base.merge(mm_extra.drop(columns=["diPlus14","diMinus14"]), on="timestamp", how="left")
FEAT_COLS += ["ema_spread_percentile","price_to_ema200_percentile","momentum","adx14","di_net14","momentum_d3","ema_spread_percentile_d3"]

close = base["close"].values
n = len(base)
for h in [1,2,3,5]:
    base[f"fwd{h}"] = np.concatenate([close[h:]-close[:-h], [np.nan]*h])

# Monthly's signedPct (percentrank needs 200 monthly bars, only 113 exist) can never mature
before = len(FEAT_COLS)
FEAT_COLS = [c for c in FEAT_COLS if not c.startswith("M_signedPct")]
print(f"\nfeature count: {before} -> {len(FEAT_COLS)} after dropping unmaturable M_signedPct family")
print(f"rows: {n}   done: {time.time()-t0:.1f}s")
base.to_parquet(r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\daily_mtf_feature_panel.parquet", index=False)

TODAY_IDX = n - 1
today = base.iloc[TODAY_IDX]
print(f"\n=== TODAY: {today.timestamp.date()} close={today.close:.2f} ===")

hist = base.iloc[:TODAY_IDX].dropna(subset=FEAT_COLS).copy()
print(f"complete-case history rows available: {len(hist)}")
hist = hist[hist.timestamp < today.timestamp - pd.Timedelta(days=15)]
mu, sd = hist[FEAT_COLS].mean(), hist[FEAT_COLS].std()
sd = sd.replace(0, 1e-9)
Z = (hist[FEAT_COLS]-mu)/sd
tz = (pd.Series({f:today[f] for f in FEAT_COLS})-mu)/sd
hist["dist"] = np.sqrt(((Z-tz.values)**2).sum(axis=1))
top = hist.nsmallest(25,"dist")

print(f"\n=== FULL MTF nearest-neighbor ({len(FEAT_COLS)} features across 9 TFs incl. 3-5 candle trend) top 15 ===")
print(f"{'date':12} {'dist':>6} | {'fwd1':>6} {'fwd2':>6} {'fwd3':>6} {'fwd5':>6}")
for _,r in top.head(15).iterrows():
    print(f"{str(r.timestamp.date()):12} {r.dist:6.2f} | {r.fwd1:+6.0f} {r.fwd2:+6.0f} {r.fwd3:+6.0f} {r.fwd5:+6.0f}")

def outcome_stats(sub, label):
    print(f"\n--- {label} (n={len(sub)}) ---")
    for h in [1,2,3,5]:
        s = sub[f"fwd{h}"].dropna()
        if len(s)==0: continue
        print(f"  fwd{h}d: mean={s.mean():+7.1f}  median={s.median():+7.1f}  %up={100*(s>0).mean():4.0f}%  std={s.std():6.1f}")

outcome_stats(top, "FULL MTF top-25")
outcome_stats(base.iloc[:TODAY_IDX], "BASELINE (unconditional)")
