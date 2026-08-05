import pandas as pd, numpy as np

panel = pd.read_parquet("daily_panel.parquet").sort_values(["scrip","date"]).reset_index(drop=True)

# script's own thresholds
ADX_FLOOR  = 20.0   # "ADX Floor" input
TREND_ADX  = 23.0   # "MTF Trend Min ADX" input

# swing horizon (7-day call) weights higher TFs; 30m is confirmation-only, not a vote
TFS = ["60m","120m","240m","D"]
W   = {"60m":1, "120m":1.5, "240m":2, "D":2.5}

def per_tf_vote(row, tf):
    v, s, rsi, adx = row[f"v_{tf}"], row[f"structure_{tf}"], row[f"rsi_{tf}"], row[f"adx_{tf}"]
    if pd.isna(v) or pd.isna(s) or pd.isna(rsi) or pd.isna(adx):
        return np.nan
    if adx < ADX_FLOOR:
        return 0.0                      # no trend on this TF -> doesn't vote
    dir_v = np.sign(v) if abs(v) >= 10 else 0     # ignore near-zero spread noise
    dir_s = s
    dir_r = 1 if rsi >= 55 else (-1 if rsi <= 45 else 0)
    agree = np.sign(dir_v) + dir_s + dir_r
    return np.sign(agree) if abs(agree) >= 2 else 0.0   # need 2 of 3 aligned on this TF

def classify(row):
    votes, wsum = 0.0, 0.0
    n_trend_tfs = 0
    for tf in TFS:
        adx = row[f"adx_{tf}"]
        if pd.notna(adx) and adx >= ADX_FLOOR:
            n_trend_tfs += 1
        vt = per_tf_vote(row, tf)
        if pd.isna(vt): continue
        votes += vt * W[tf]; wsum += W[tf]
    if wsum == 0 or n_trend_tfs < 2:
        return pd.Series({"score": np.nan, "call": "NoCall", "n_trend_tfs": n_trend_tfs})
    score = votes / wsum
    call = "Long" if score >= 0.5 else ("Short" if score <= -0.5 else "NoCall")
    return pd.Series({"score": score, "call": call, "n_trend_tfs": n_trend_tfs})

res = panel.apply(classify, axis=1)
panel = pd.concat([panel, res], axis=1)
panel.to_parquet("classified_panel.parquet", index=False)

print(panel.call.value_counts())
cutoff = panel[(panel.date=="2026-03-12")]
print(f"\n=== cutoff 2026-03-12: {len(cutoff)} scrips ===")
print(cutoff[["scrip","close","score","call","n_trend_tfs"]].sort_values("score").to_string(index=False))
