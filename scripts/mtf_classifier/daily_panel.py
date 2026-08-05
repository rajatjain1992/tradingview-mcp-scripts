import pandas as pd, numpy as np

daily_close = pd.read_parquet("daily_ohlc.parquet")[["scrip","dt","close"]].rename(columns={"dt":"date"})
daily_close = daily_close.sort_values(["scrip","date"]).reset_index(drop=True)

TF = {"30m":"m30","60m":"m60","120m":"m120","240m":"m240","D":"daily","W":"weekly"}
panel = daily_close.copy()

for label, fname in TF.items():
    ind = pd.read_parquet(f"{fname}_ind.parquet").rename(columns={"dt":"ts"})
    base = panel.rename(columns={"date":"ts"})[["scrip","ts"]].sort_values(["ts","scrip"]).reset_index(drop=True)
    ind2 = ind.sort_values(["ts","scrip"]).reset_index(drop=True)
    m = pd.merge_asof(base, ind2, on="ts", by="scrip", direction="backward")
    m = m.rename(columns={"ts":"date", "v":f"v_{label}", "structure":f"structure_{label}",
                           "rsi":f"rsi_{label}", "adx":f"adx_{label}"})
    panel = panel.merge(m, on=["scrip","date"], how="left")   # keyed join, not positional

panel.to_parquet("daily_panel.parquet", index=False)
print(panel.shape)

chk = panel[(panel.scrip=="RELIANCE") & (panel.date=="2026-03-12")]
print("\n=== RELIANCE 2026-03-12 sanity (should match daily_ind.parquet v=3.5) ===")
print(chk.T)
