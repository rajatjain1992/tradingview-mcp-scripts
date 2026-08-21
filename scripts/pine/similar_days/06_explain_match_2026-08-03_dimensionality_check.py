"""THE CAUTIONARY TALE of this whole matching exercise. Rajat visually compared
2026-08-03/04 (05's #1/#2 match, 99.93%/99.85% "confidence") against the current
chart and asked "do you think they match anyway" -- correctly, on visual inspection
they didn't: Aug 3-4 was a strong overbought rally (wide EMA fan, high spread-
exhaustion readings, rising ADX, positive OBV) vs today's compressed oversold
recovery (tight EMA ribbon, negative OBV). This script proves it numerically: the
top contributors to the match were 60m/120m/30m RSI and 15m/30m signedPct sitting at
NEAR-OPPOSITE values (e.g. 60m RSI 24 vs 75, 15m signedPct -96 vs +82) -- individual
mismatches of 3-5 std devs that only ranked #1 because summed across 97 dimensions
they happened to be the LEAST mismatched of the whole pool, not because they were
actually close.

Lesson: a 99%+ "confidence" (percentile rank) from a high-dimensional nearest-
neighbor search does NOT mean the match is good in absolute terms. Always decompose
into per-feature contributions (this script's method) before trusting a rank. The
fix isn't more features -- either fewer/curated features, or an absolute-distance
sanity check (e.g. reject if any single feature's z-gap exceeds ~2) instead of pure
rank-based selection.
"""
import pandas as pd, numpy as np

base = pd.read_parquet(r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\daily_mtf_feature_panel_v2.parquet")
base["timestamp"] = pd.to_datetime(base["timestamp"])
FEAT_COLS = [c for c in base.columns if c not in ["timestamp","open","high","low","close","fwd1","fwd2","fwd3","fwd5"]]
FEAT_COLS = [c for c in FEAT_COLS if not c.startswith("M_signedPct")]

n = len(base)
today = base.iloc[n-1]
hist = base.iloc[:n-1].dropna(subset=FEAT_COLS).copy()
hist = hist[hist.timestamp < today.timestamp - pd.Timedelta(days=15)]
mu, sd = hist[FEAT_COLS].mean(), hist[FEAT_COLS].std()
sd = sd.replace(0, 1e-9)

match = base[base.timestamp == "2026-08-03"].iloc[0]
rows = []
for f in FEAT_COLS:
    zt=(today[f]-mu[f])/sd[f]; zm=(match[f]-mu[f])/sd[f]
    rows.append((f, today[f], match[f], zt, zm, (zt-zm)**2))
R = pd.DataFrame(rows, columns=["feature","today","match","z_today","z_match","contrib"]).sort_values("contrib")
total = R.contrib.sum()
print(f"total dist = {np.sqrt(total):.2f}\n")

print("=== EMA POSITION + ema_stack + price-structure features specifically ===")
key = [f for f in FEAT_COLS if f.startswith("pos_") or f in ("ema_stack","ret_1d","ret_3d","ret_5d","pct_from_hi20","pct_from_lo20","ema_spread_percentile")]
Rk = R[R.feature.isin(key)].sort_values("contrib", ascending=False)
print(f"{'feature':22s} {'today':>9} {'match(8/3)':>10} {'z_today':>8} {'z_match':>8} {'contrib':>8} {'%tot':>6}")
for _,r in Rk.iterrows():
    print(f"{r.feature:22s} {r.today:9.2f} {r.match:10.2f} {r.z_today:8.2f} {r.z_match:8.2f} {r.contrib:8.3f} {100*r.contrib/total:5.1f}%")

print(f"\nkey-group total contribution: {100*Rk.contrib.sum()/total:.1f}% of total distance")
print("\n=== top 15 overall contributors (what actually drove the match) ===")
for _,r in R.sort_values('contrib',ascending=False).head(15).iterrows():
    print(f"{r.feature:26s} today={r.today:9.2f} match={r.match:9.2f} contrib={r.contrib:6.3f} ({100*r.contrib/total:4.1f}%)")
