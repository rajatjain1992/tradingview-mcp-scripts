"""THIRD PASS -- adds what 02 was missing: Rajat asked "did u not match EMA position
or price structure" and the honest answer was no. This version adds 12 more features
on top of 02's 85: 6x EMA-distance ((close-emaN)/close for N in 5/9/20/50/100/200),
ema_stack (bearish/bullish order flag), and 5x price-structure (ret_1d/3d/5d,
pct_from_20d_high, pct_from_20d_low) -- 97 features total.

CAUTION -- read 06 before trusting this version's ranking at face value: at 97
features against ~1300 samples, "nearest neighbor by summed z-score distance" can
degrade into "least-bad average across many mismatches" rather than a genuinely
tight match on most axes. The #1 result here (2026-08-03) looked strong by rank
(99.93th percentile) but individual RSI readings were near-OPPOSITE of today's
(60m RSI 75 vs 24, 120m RSI 74 vs 29) -- a real curse-of-dimensionality trap, not a
real match. Always run 04-style per-feature decomposition on whatever this produces
before reporting a "top match" as meaningful. A smaller, more deliberately chosen
feature set (or per-timeframe-group distances instead of one combined vector) would
likely be more reliable than throwing in more features.
"""
import sys, time
sys.path.insert(0, "scripts/pine")
from mtf_indicators import (resample_intraday, resample_daily, resample_weekly, resample_monthly,
                             spread_exhaustion_calc, ema)
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
mm_extra = mm_d[["timestamp","ema_spread_percentile","price_to_ema200_percentile","momentum","adx14",
                  "diPlus14","diMinus14","ema5","ema9","ema20","ema50","ema100","ema200","ema_stack"]].copy()
mm_extra["di_net14"] = mm_extra.diPlus14 - mm_extra.diMinus14
mm_extra["momentum_d3"] = mm_extra["momentum"] - mm_extra["momentum"].shift(3)
mm_extra["ema_spread_percentile_d3"] = mm_extra["ema_spread_percentile"] - mm_extra["ema_spread_percentile"].shift(3)

# NEW: EMA position (where does price sit relative to the EMA ladder, in %)
for e in ["ema5","ema9","ema20","ema50","ema100","ema200"]:
    mm_extra[f"pos_{e}"] = (base.set_index("timestamp")["close"].reindex(mm_extra.timestamp).values - mm_extra[e]) / mm_extra[e] * 100
mm_extra["ema_stack"] = mm_extra["ema_stack"].astype(int)

base = base.merge(mm_extra.drop(columns=["diPlus14","diMinus14","ema5","ema9","ema20","ema50","ema100","ema200"]),
                   on="timestamp", how="left")
FEAT_COLS += ["ema_spread_percentile","price_to_ema200_percentile","momentum","adx14","di_net14",
              "momentum_d3","ema_spread_percentile_d3","ema_stack",
              "pos_ema5","pos_ema9","pos_ema20","pos_ema50","pos_ema100","pos_ema200"]

# NEW: price structure (recent return path + position within recent range)
close = base["close"].values
base["ret_1d"] = base["close"].pct_change(1)*100
base["ret_3d"] = base["close"].pct_change(3)*100
base["ret_5d"] = base["close"].pct_change(5)*100
hi20 = base["high"].rolling(20).max(); lo20 = base["low"].rolling(20).min()
base["pct_from_hi20"] = (base["close"]-hi20)/hi20*100
base["pct_from_lo20"] = (base["close"]-lo20)/lo20*100
FEAT_COLS += ["ret_1d","ret_3d","ret_5d","pct_from_hi20","pct_from_lo20"]

n = len(base)
for h in [1,2,3,5]:
    base[f"fwd{h}"] = np.concatenate([close[h:]-close[:-h], [np.nan]*h])

before = len(FEAT_COLS)
FEAT_COLS = [c for c in FEAT_COLS if not c.startswith("M_signedPct")]
print(f"feature count: {before} -> {len(FEAT_COLS)} (added EMA position x6 + ema_stack + price-structure x5)")
base.to_parquet(r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\daily_mtf_feature_panel_v2.parquet", index=False)

TODAY_IDX = n - 1
today = base.iloc[TODAY_IDX]
print(f"\n=== TODAY: {today.timestamp.date()} close={today.close:.2f} ===")
print(f"EMA position vs today's close: " + "  ".join(f"{e}={today[f'pos_{e}']:+.2f}%" for e in ['ema5','ema9','ema20','ema50','ema100','ema200']))
print(f"ema_stack={today.ema_stack}  ret_1d={today.ret_1d:+.2f}%  ret_5d={today.ret_5d:+.2f}%  pct_from_hi20={today.pct_from_hi20:+.2f}%  pct_from_lo20={today.pct_from_lo20:+.2f}%")

hist = base.iloc[:TODAY_IDX].dropna(subset=FEAT_COLS).copy()
print(f"\ncomplete-case pool: {len(hist)}")
hist = hist[hist.timestamp < today.timestamp - pd.Timedelta(days=15)]
mu, sd = hist[FEAT_COLS].mean(), hist[FEAT_COLS].std()
sd = sd.replace(0, 1e-9)
Z = (hist[FEAT_COLS]-mu)/sd
tz = (pd.Series({f:today[f] for f in FEAT_COLS})-mu)/sd
hist["dist"] = np.sqrt(((Z-tz.values)**2).sum(axis=1))
hist["pct_rank"] = 100 * (1 - hist["dist"].rank(pct=True))
top = hist.nsmallest(25,"dist").sort_values("dist")

print(f"\n=== v2 (incl. EMA position + price structure, {len(FEAT_COLS)} features) top 25 ===")
print(f"{'#':>2} {'date':12} {'dist':>6} {'confid%':>8} | {'fwd1':>6} {'fwd2':>6} {'fwd3':>6} {'fwd5':>6}")
for i,(_,r) in enumerate(top.iterrows(),1):
    print(f"{i:2d} {str(r.timestamp.date()):12} {r.dist:6.2f} {r.pct_rank:7.2f}% | {r.fwd1:+6.0f} {r.fwd2:+6.0f} {r.fwd3:+6.0f} {r.fwd5:+6.0f}")

def outcome_stats(sub, label):
    print(f"\n--- {label} (n={len(sub)}) ---")
    for h in [1,2,3,5]:
        s = sub[f"fwd{h}"].dropna()
        if len(s)==0: continue
        print(f"  fwd{h}d: mean={s.mean():+7.1f}  median={s.median():+7.1f}  %up={100*(s>0).mean():4.0f}%  std={s.std():6.1f}")
outcome_stats(top, "v2 top-25")
outcome_stats(base.iloc[:TODAY_IDX], "BASELINE")
