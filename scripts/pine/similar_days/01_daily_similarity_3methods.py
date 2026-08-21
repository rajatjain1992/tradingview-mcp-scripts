"""FIRST PASS -- daily-only similarity search, 3 independent methods, run 2026-08-21
against the 2026-08-20 close. Superseded by 02/05 (full multi-timeframe versions) but
kept as the baseline: this is what "only Daily" looked like before Rajat asked for
all timeframes.

Method 1: z-score Euclidean nearest-neighbor on 8 daily features (rsi_D, adx_D,
DI-net, ema_spread_percentile, price_to_ema200_percentile, momentum, adx14, di_net14).
Method 2: fuzzy threshold band on the same feature family (looser bounds, larger n).
Method 3: pure 10-day price-return SHAPE match, indicator-agnostic.
Baseline: unconditional forward-return stats across all 2315 days for comparison
(NIFTY has a persistent positive drift -- "flat" from a method is itself informative).

Needs scripts/pine/run_multi_mode.py and run_mtf_indicators.py already run once
(reads their saved CSV outputs from Downloads/Daily Trade Files/MTF_V4/).
"""
import pandas as pd, numpy as np

mm = pd.read_csv(r'C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\multi_mode_D.csv')
dwm = pd.read_csv(r'C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\mtf_indicators_dwm.csv')
dwm_d = dwm[dwm.tf=='D'].reset_index(drop=True)

df = mm.merge(dwm_d[['timestamp','close','curTF_pDI','curTF_mDI','curTF_adxCur','rsi_D','adx_D','v_D']], on='timestamp')
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp').reset_index(drop=True)
df['di_net'] = df['curTF_pDI'] - df['curTF_mDI']
df['di_net14'] = df['diPlus14'] - df['diMinus14']

# forward returns (close-to-close, N trading days ahead)
close = df['close'].values
n = len(df)
for h in [1,2,3,5]:
    df[f'fwd{h}'] = np.concatenate([close[h:] - close[:-h], [np.nan]*h])

TODAY_IDX = n - 1
today = df.iloc[TODAY_IDX]
print(f"=== TODAY (trigger state as of {today.timestamp.date()} close) ===")
print(f"close={today.close:.2f}  rsi_D={today.rsi_D:.1f}  adx_D={today.adx_D:.1f}  adx14={today.adx14:.1f}")
print(f"di_net(8)={today.di_net:+.1f}  di_net(14)={today.di_net14:+.1f}  momentum={today.momentum:+.3f}")
print(f"ema_spread_pctile={today.ema_spread_percentile:.1f}  price_to_ema200_pctile={today.price_to_ema200_percentile:.1f}")
print(f"ema_stack={today.ema_stack}  bandColor={today.bandColor}  v_D={today.v_D:+.1f}")
print()

# ============================ METHOD 1: z-score Euclidean nearest-neighbor ============================
feats = ['rsi_D','adx_D','di_net','ema_spread_percentile','price_to_ema200_percentile','momentum','adx14','di_net14']
hist = df.iloc[:TODAY_IDX].dropna(subset=feats).copy()  # exclude today itself
mu, sd = hist[feats].mean(), hist[feats].std()
Z = (hist[feats]-mu)/sd
tz = (pd.Series({f:today[f] for f in feats})-mu)/sd
hist['dist'] = np.sqrt(((Z-tz.values)**2).sum(axis=1))
# exclude the 10 trading days immediately preceding today (not independent/too autocorrelated)
hist = hist[hist.timestamp < today.timestamp - pd.Timedelta(days=15)]
top1 = hist.nsmallest(25, 'dist')

print("=== METHOD 1: nearest-neighbor (z-score Euclidean, 8 features) — top 15 ===")
print(f"{'date':12} {'dist':>5} {'rsi_D':>6} {'adx_D':>6} {'di_net':>7} {'emaSprPct':>9} {'mom':>6} | {'fwd1':>6} {'fwd3':>6} {'fwd5':>6}")
for _,r in top1.head(15).iterrows():
    print(f"{str(r.timestamp.date()):12} {r.dist:5.2f} {r.rsi_D:6.1f} {r.adx_D:6.1f} {r.di_net:+7.1f} {r.ema_spread_percentile:9.1f} {r.momentum:+6.2f} | {r.fwd1:+6.0f} {r.fwd3:+6.0f} {r.fwd5:+6.0f}")

def outcome_stats(sub, label):
    print(f"\n--- {label} (n={len(sub)}) ---")
    for h in [1,2,3,5]:
        s = sub[f'fwd{h}'].dropna()
        if len(s)==0: continue
        print(f"  fwd{h}d: mean={s.mean():+7.1f}  median={s.median():+7.1f}  %up={100*(s>0).mean():4.0f}%  std={s.std():6.1f}")

outcome_stats(top1, "METHOD 1 top-25 nearest neighbors")

# ============================ METHOD 2: fuzzy threshold band ============================
band = hist[
    (hist.rsi_D.between(30,52)) &
    (hist.ema_spread_percentile <= 15) &
    (hist.price_to_ema200_percentile <= 25) &
    (hist.momentum < 0) &
    (hist.di_net < 0) & (hist.di_net14 < 0)
]
print(f"\n=== METHOD 2: fuzzy threshold band (RSI 30-52, EMA-compression<=15pct, price-near-EMA200<=25pct, -DI both lengths, mom<0) ===")
print(f"matches: {len(band)}")
if len(band):
    print(band[['timestamp','close','rsi_D','adx_D','ema_spread_percentile','di_net','momentum']].tail(10).to_string(index=False))
outcome_stats(band, "METHOD 2 fuzzy band")

# ============================ METHOD 3: price-structure pattern match (10-day return shape) ============================
W = 10
rets = df['close'].pct_change().values
target_shape = rets[TODAY_IDX-W+1:TODAY_IDX+1]
target_shape = (target_shape - np.nanmean(target_shape)) / np.nanstd(target_shape)

dists = []
for i in range(W, TODAY_IDX-15):  # leave a 15-day gap like method 1
    window = rets[i-W+1:i+1]
    if np.any(np.isnan(window)): continue
    wz = (window - window.mean())/ (window.std() if window.std()>0 else 1e-9)
    d = np.sqrt(np.sum((wz-target_shape)**2))
    dists.append((i, d))
dists.sort(key=lambda x: x[1])
top3_idx = [i for i,d in dists[:25]]
top3 = df.iloc[top3_idx].copy()
top3['dist'] = [d for i,d in dists[:25]]
print(f"\n=== METHOD 3: 10-day price-return SHAPE match (independent of indicators) — top 10 ===")
print(f"{'date':12} {'dist':>6} | {'fwd1':>6} {'fwd3':>6} {'fwd5':>6}")
for _,r in top3.head(10).iterrows():
    print(f"{str(r.timestamp.date()):12} {r.dist:6.2f} | {r.fwd1:+6.0f} {r.fwd3:+6.0f} {r.fwd5:+6.0f}")
outcome_stats(top3, "METHOD 3 top-25 price-shape matches")

# ============================ baseline (unconditional) ============================
outcome_stats(df.iloc[:TODAY_IDX], "BASELINE: all historical days (unconditional)")
