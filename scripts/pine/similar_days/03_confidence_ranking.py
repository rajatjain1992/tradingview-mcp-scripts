"""Lists ALL top-25 matched dates from 02's feature panel with a "confidence %" --
IMPORTANT: this is a similarity PERCENTILE RANK within the candidate pool (this day
is closer to today than X% of all historical days), NOT a probability that the
forward outcome will repeat. Conflating the two is the mistake to avoid when reading
this output -- see 06 for a case where a 99.93% "confidence" top match turned out to
be a poor match in absolute terms once inspected feature-by-feature.
"""
import pandas as pd, numpy as np

base = pd.read_parquet(r"C:\Users\Rajat\Downloads\Daily Trade Files\MTF_V4\daily_mtf_feature_panel.parquet")
base["timestamp"] = pd.to_datetime(base["timestamp"])

FEAT_COLS = [c for c in base.columns if c not in
             ["timestamp","open","high","low","close","fwd1","fwd2","fwd3","fwd5"]]
FEAT_COLS = [c for c in FEAT_COLS if not c.startswith("M_signedPct")]

n = len(base)
TODAY_IDX = n - 1
today = base.iloc[TODAY_IDX]

hist = base.iloc[:TODAY_IDX].dropna(subset=FEAT_COLS).copy()
hist = hist[hist.timestamp < today.timestamp - pd.Timedelta(days=15)]
mu, sd = hist[FEAT_COLS].mean(), hist[FEAT_COLS].std()
sd = sd.replace(0, 1e-9)
Z = (hist[FEAT_COLS]-mu)/sd
tz = (pd.Series({f:today[f] for f in FEAT_COLS})-mu)/sd
hist["dist"] = np.sqrt(((Z-tz.values)**2).sum(axis=1))

# "confidence" = similarity percentile among the FULL candidate pool (n=1337 here) --
# i.e. this day is closer to today than X% of all historical days. NOT a probability
# that the outcome will repeat -- purely a measure of pattern-match closeness.
hist["pct_rank"] = 100 * (1 - hist["dist"].rank(pct=True))
top = hist.nsmallest(25, "dist").sort_values("dist")

print(f"candidate pool size: {len(hist)} historical days\n")
print(f"{'#':>2} {'date':12} {'dist':>6} {'confid%':>8} | {'fwd1':>6} {'fwd2':>6} {'fwd3':>6} {'fwd5':>6}")
for i,(_,r) in enumerate(top.iterrows(),1):
    print(f"{i:2d} {str(r.timestamp.date()):12} {r.dist:6.2f} {r.pct_rank:7.2f}% | {r.fwd1:+6.0f} {r.fwd2:+6.0f} {r.fwd3:+6.0f} {r.fwd5:+6.0f}")

print(f"\nmean confidence of top-25 pool: {top.pct_rank.mean():.1f}%")
print(f"distance range: {top.dist.min():.2f} - {top.dist.max():.2f}  (pool median dist: {hist.dist.median():.2f})")
