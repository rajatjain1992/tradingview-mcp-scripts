import pandas as pd, numpy as np
from scipy import stats

panel = pd.read_parquet("daily_panel.parquet").sort_values(["scrip","date"]).reset_index(drop=True)
ceval = pd.read_parquet("classified_panel_eval.parquet")[["scrip","date","close_fwd7","ret_fwd7"]]
panel = panel.merge(ceval, on=["scrip","date"], how="left")
mkt = panel.groupby("date")["ret_fwd7"].transform("mean")
panel["ret_fwd7_x"] = panel["ret_fwd7"] - mkt

first_valid = panel[panel.v_D.notna()].date.min()
last_ok = panel.date.max() - pd.Timedelta(days=10)
dates_sorted = np.sort(panel.date.unique())
grid = dates_sorted[(dates_sorted>=first_valid)&(dates_sorted<=last_ok)][::5]

def build(adx_floor, tfs, w, vote_th, score_th, min_trend_tfs, v_th=10, rsi_band=5):
    def classify(row):
        votes, wsum, n_trend = 0.0, 0.0, 0
        for tf in tfs:
            adx = row[f"adx_{tf}"]
            if pd.notna(adx) and adx >= adx_floor: n_trend += 1
            v,s,rsi = row[f"v_{tf}"], row[f"structure_{tf}"], row[f"rsi_{tf}"]
            if pd.isna(v) or pd.isna(s) or pd.isna(rsi) or pd.isna(adx) or adx < adx_floor:
                continue
            dv = np.sign(v) if abs(v)>=v_th else 0
            dr = 1 if rsi>=50+rsi_band else (-1 if rsi<=50-rsi_band else 0)
            agree = np.sign(dv) + s + dr
            vt = np.sign(agree) if abs(agree)>=vote_th else 0.0
            votes += vt*w[tf]; wsum += w[tf]
        if wsum==0 or n_trend<min_trend_tfs: return np.nan, "NoCall"
        sc = votes/wsum
        return sc, ("Long" if sc>=score_th else ("Short" if sc<=-score_th else "NoCall"))
    out = panel.apply(lambda r: pd.Series(classify(r), index=["score","call"]), axis=1)
    return pd.concat([panel[["scrip","date","ret_fwd7","ret_fwd7_x"]], out], axis=1)

def evaluate(d, label, grid=grid):
    d = d[d.date.isin(grid)]
    dirn = d.call.map({"Long":1,"Short":-1,"NoCall":0})
    rows=[]
    for call in ["Long","Short"]:
        sub = d[d.call==call]
        if len(sub)<20: continue
        rd  = dirn[sub.index]*sub.ret_fwd7
        rdx = dirn[sub.index]*sub.ret_fwd7_x
        pb = rdx.groupby(sub.date).mean()
        t,p = stats.ttest_1samp(pb.dropna(),0) if pb.notna().sum()>5 else (np.nan,np.nan)
        rows.append({"variant":label,"call":call,"n":len(sub),"dates":sub.date.nunique(),
                     "raw%":rd.mean(),"xs%":rdx.mean(),"xs_hit%":(rdx>0).mean()*100,"p":p})
    return pd.DataFrame(rows)

W1 = {"60m":1,"120m":1.5,"240m":2,"D":2.5}
variants = [
    ("v1 baseline (60/120/240/D, adx20, vote>=2, |score|>=0.5)",
     build(20, ["60m","120m","240m","D"], W1, 2, 0.5, 2)),
    ("A: HTF only (240/D), adx20",
     build(20, ["240m","D"], {"240m":1,"D":1.5}, 2, 0.5, 1)),
    ("B: stricter conviction |score|>=0.7",
     build(20, ["60m","120m","240m","D"], W1, 2, 0.7, 2)),
    ("C: higher adx_floor=25",
     build(25, ["60m","120m","240m","D"], W1, 2, 0.5, 2)),
    ("D: wider RSI band (rsi_band=10)",
     build(20, ["60m","120m","240m","D"], W1, 2, 0.5, 2, rsi_band=10)),
    ("E: require unanimous vote (need 3/3 per TF)",
     build(20, ["60m","120m","240m","D"], W1, 3, 0.5, 2)),
    ("F: add 30m + W, drop 60m",
     build(20, ["30m","120m","240m","D","W"], {"30m":0.5,"120m":1.5,"240m":2,"D":2.5,"W":1.5}, 2, 0.5, 2)),
]
all_r = pd.concat([evaluate(d,label) for label,d in variants], ignore_index=True)
print(all_r.round(3).to_string(index=False))
