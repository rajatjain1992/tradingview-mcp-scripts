"""Per-feature breakdown of WHY a specific date matched -- decomposes the total
squared z-score distance into per-feature contributions, sorted both ways (closest-
matching features = the real basis of the match; largest-deviating = where it
actually differed). Template for sanity-checking any "top match" before trusting it;
this is what caught the 2026-08-03 false-positive in 06. Hardcoded to 2025-05-12
(the 02 pipeline's #1 match) -- change the date filter to reuse for another day.
"""
import pandas as pd, numpy as np

base = pd.read_parquet(r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\daily_mtf_feature_panel.parquet")
base["timestamp"] = pd.to_datetime(base["timestamp"])

FEAT_COLS = [c for c in base.columns if c not in
             ["timestamp","open","high","low","close","fwd1","fwd2","fwd3","fwd5"]]
FEAT_COLS = [c for c in FEAT_COLS if not c.startswith("M_signedPct")]

n = len(base)
today = base.iloc[n-1]
hist = base.iloc[:n-1].dropna(subset=FEAT_COLS).copy()
hist = hist[hist.timestamp < today.timestamp - pd.Timedelta(days=15)]
mu, sd = hist[FEAT_COLS].mean(), hist[FEAT_COLS].std()
sd = sd.replace(0, 1e-9)

match = base[base.timestamp == "2025-05-12"].iloc[0]

rows = []
for f in FEAT_COLS:
    zt = (today[f]-mu[f])/sd[f]
    zm = (match[f]-mu[f])/sd[f]
    contrib = (zt-zm)**2
    rows.append((f, today[f], match[f], zt, zm, contrib))
R = pd.DataFrame(rows, columns=["feature","today_val","match_val","z_today","z_match","contrib"])
R = R.sort_values("contrib")
total = R.contrib.sum()
print(f"total squared distance: {total:.2f}  (dist={np.sqrt(total):.2f})\n")

print("=== 20 CLOSEST-matching features (smallest contribution to distance -- these ARE the match) ===")
print(f"{'feature':28s} {'today':>10} {'2025-05-12':>10} {'z_today':>8} {'z_match':>8} {'contrib':>8}")
for _,r in R.head(20).iterrows():
    print(f"{r.feature:28s} {r.today_val:10.2f} {r.match_val:10.2f} {r.z_today:8.2f} {r.z_match:8.2f} {r.contrib:8.3f}")

print("\n=== 15 LARGEST-deviating features (where they actually differed) ===")
print(f"{'feature':28s} {'today':>10} {'2025-05-12':>10} {'z_today':>8} {'z_match':>8} {'contrib':>8} {'%oftotal':>8}")
for _,r in R.tail(15).sort_values('contrib',ascending=False).iterrows():
    print(f"{r.feature:28s} {r.today_val:10.2f} {r.match_val:10.2f} {r.z_today:8.2f} {r.z_match:8.2f} {r.contrib:8.3f} {100*r.contrib/total:7.1f}%")

print(f"\n=== outcome that followed 2025-05-12 ===")
print(f"close={match.close:.2f}  fwd1={match.fwd1:+.0f}  fwd2={match.fwd2:+.0f}  fwd3={match.fwd3:+.0f}  fwd5={match.fwd5:+.0f}")
