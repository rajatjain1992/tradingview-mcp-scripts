import pandas as pd, numpy as np

panel = pd.read_parquet("classified_panel.parquet").sort_values(["scrip","date"]).reset_index(drop=True)

def fwd_close(g, horizon_days=7):
    dates = g.date.to_numpy(); closes = g.close.to_numpy()
    out = np.full(len(g), np.nan)
    j = 0
    for i in range(len(g)):
        target = dates[i] + np.timedelta64(horizon_days, "D")
        j = max(j, i)
        while j < len(g) and dates[j] < target:
            j += 1
        out[i] = closes[j] if j < len(g) else np.nan
    return out

panel["close_fwd7"] = panel.groupby("scrip", group_keys=False).apply(lambda g: pd.Series(fwd_close(g), index=g.index))
panel["ret_fwd7"] = (panel.close_fwd7/panel.close - 1)*100

cutoff = panel[panel.date=="2026-03-12"].copy()
print("=== 2026-03-12 cutoff, next-7-calendar-day forward return ===")
print(cutoff[["scrip","call","score","close","close_fwd7","ret_fwd7"]].sort_values("score").round(2).to_string(index=False))

print("\n=== by call type (this one date) ===")
g = cutoff.groupby("call")["ret_fwd7"].agg(["count","mean","median",lambda s:(s>0).mean()*100])
g.columns=["n","mean%","median%","hit%"]
print(g.round(2))

panel.to_parquet("classified_panel_eval.parquet", index=False)
