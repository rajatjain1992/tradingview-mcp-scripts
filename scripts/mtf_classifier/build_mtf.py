import pandas as pd, numpy as np

P = r"C:\Users\Rajat\Downloads\Daily Trade Files\Scrips 6 months 1min Data.csv"
d = pd.read_csv(P, usecols=["scrip","timestamp","open","high","low","close"],
                dtype={"scrip":"category","open":"float32","high":"float32","low":"float32","close":"float32"})
d["dt"] = pd.to_datetime(d.timestamp, unit="s").dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
d = d.sort_values(["scrip","dt"])
d["date"] = d.dt.dt.date
print("scrips:", sorted(d.scrip.unique().tolist()))
print("range:", d.dt.min(), "->", d.dt.max())

def resample(df, n):
    g = df.sort_values(["scrip","dt"])
    b = g.groupby(["scrip","date"], observed=True).cumcount() // n
    o = (g.assign(_b=b).groupby(["scrip","date","_b"], observed=True)
           .agg(dt=("dt","max"), high=("high","max"), low=("low","min"), close=("close","last"))
           .reset_index()[["scrip","dt","high","low","close"]]
           .sort_values(["scrip","dt"]).reset_index(drop=True))
    return o

m15  = resample(d, 15)
m30  = resample(d, 30)
m60  = resample(d, 60)
m120 = resample(d, 120)
m240 = resample(d, 240)

daily = (d.groupby(["scrip","date"], observed=True)
          .agg(high=("high","max"), low=("low","min"), close=("close","last"))
          .reset_index())
daily["dt"] = pd.to_datetime(daily["date"])
daily = daily[["scrip","dt","high","low","close"]].sort_values(["scrip","dt"]).reset_index(drop=True)

wk = daily.copy()
wk["wk"] = wk["dt"].dt.to_period("W-SUN")
weekly = (wk.groupby(["scrip","wk"], observed=True)
           .agg(dt=("dt","max"), high=("high","max"), low=("low","min"), close=("close","last"))
           .reset_index()[["scrip","dt","high","low","close"]]
           .sort_values(["scrip","dt"]).reset_index(drop=True))

for name, df in [("m15",m15),("m30",m30),("m60",m60),("m120",m120),("m240",m240),("daily",daily),("weekly",weekly)]:
    df.to_parquet(f"{name}_ohlc.parquet", index=False)
    print(f"{name}: {len(df)} rows, {df.scrip.nunique()} scrips, {df.dt.min()} -> {df.dt.max()}")
