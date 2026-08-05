import pandas as pd, numpy as np
from scipy import stats

panel = pd.read_parquet("classified_panel_eval.parquet").sort_values(["scrip","date"]).reset_index(drop=True)

# market-neutralize: subtract same-day cross-sectional mean fwd7 return (all 33 names, any call)
mkt = panel.groupby("date")["ret_fwd7"].transform("mean")
panel["ret_fwd7_x"] = panel["ret_fwd7"] - mkt

dirn = panel.call.map({"Long":1, "Short":-1, "NoCall":0})
panel["ret_dir"]   = dirn * panel.ret_fwd7      # raw, direction-adjusted (what you'd bank)
panel["ret_dir_x"] = dirn * panel.ret_fwd7_x    # market-neutral, direction-adjusted

# walk-forward grid: every 5 trading days, restricted to where v/rsi/adx have warmed up
valid_dates = panel[panel.n_trend_tfs.notna()].date.unique()
valid_dates = np.sort(valid_dates)
first_valid = panel[panel.v_D.notna()].date.min()   # v needs 200-day warmup
last_ok = panel.date.max() - pd.Timedelta(days=10)  # leave room for the 7-day forward window
grid = valid_dates[(valid_dates>=first_valid) & (valid_dates<=last_ok)][::5]
print(f"walk-forward cutoffs: {len(grid)}  ({pd.Timestamp(grid[0]).date()} -> {pd.Timestamp(grid[-1]).date()})")

wf = panel[panel.date.isin(grid)].copy()
print(f"scrip-date rows in walk-forward: {len(wf)}")

def report(d, label):
    r = []
    for call in ["Long","Short","NoCall"]:
        sub = d[d.call==call]
        if len(sub) < 20: continue
        pb = sub.groupby("date")["ret_dir_x"].mean()   # cluster by date (33 names move together)
        t,p = stats.ttest_1samp(pb.dropna(), 0) if pb.notna().sum()>5 else (np.nan,np.nan)
        r.append({"call":call, "n":len(sub), "dates":sub.date.nunique(),
                   "raw_mean%":sub.ret_dir.mean(), "raw_hit%":(sub.ret_dir>0).mean()*100,
                   "xs_mean%":sub.ret_dir_x.mean(), "xs_hit%":(sub.ret_dir_x>0).mean()*100,
                   "p(clustered)":p})
    t = pd.DataFrame(r)
    print(f"\n=== {label} ===")
    print(t.round(3).to_string(index=False))
    return t

report(wf, "FULL WALK-FORWARD: all scrips x all cutoff dates (dir-adjusted, market-neutral)")

# split halves for robustness
mid = grid[len(grid)//2]
report(wf[wf.date<mid], f"H1 (< {pd.Timestamp(mid).date()})")
report(wf[wf.date>=mid], f"H2 (>= {pd.Timestamp(mid).date()})")

wf.to_parquet("walkforward.parquet", index=False)
