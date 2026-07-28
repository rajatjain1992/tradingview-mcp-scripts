#!/usr/bin/env python
import os, json, time
from datetime import datetime
import numpy as np, pandas as pd
from google.cloud import bigquery

c = bigquery.Client()
syms = json.load(open(os.path.join(os.path.dirname(__file__) or ".", "_fno_syms.json"))) if os.path.exists("_fno_syms.json") else None
if syms is None:
    rows = c.query("""SELECT DISTINCT UNDERLYING_SYMBOL sym FROM `rajat-trade.stock_data_set.instrument_list`
                      WHERE INSTRUMENT_TYPE='FUTSTK' AND LOT_SIZE>0 ORDER BY sym""").result()
    syms = [r.sym for r in rows]
sl = ",".join(f"'{s}'" for s in syms)
print("scrips:", len(syms))

df = c.query(f"""SELECT scrip, exchange, trade_date, open, high, low, close, volume
    FROM `rajat-trade.stock_data_set.stock_daily_prices_dhan`
    WHERE scrip IN ({sl}) AND trade_date >= '2005-01-01'""").to_arrow().to_pandas()
df["trade_date"] = pd.to_datetime(df["trade_date"])
df = df.sort_values(["scrip", "trade_date"]).reset_index(drop=True)
exch = df.groupby("scrip")["exchange"].last().to_dict()
print("rows:", len(df))

def resample(g, freq):
    r = g.set_index("trade_date").resample(freq).agg(open=("open","first"), high=("high","max"),
        low=("low","min"), close=("close","last")).dropna(subset=["close"])
    return r

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

# TF config: fast, mid, slow, slopeN, hilo window, key name, slow name, min bars
CFG = {
 "D": dict(f=20,m=50,s=200,sl=10,hl=252,mn="50D",sn="200D",minb=60),
 "W": dict(f=20,m=50,s=200,sl=8, hl=52, mn="50W",sn="200W",minb=30),
 "M": dict(f=10,m=20,s=50, sl=4, hl=12, mn="20M",sn="50M", minb=14),
}

def tf_read(r, cfg):
    if len(r) < cfg["minb"]:
        return "insufficient history", 0
    cl = r["close"]
    ef, em, es = ema(cl,cfg["f"]).iloc[-1], ema(cl,cfg["m"]).iloc[-1], ema(cl,cfg["s"]).iloc[-1]
    px = cl.iloc[-1]
    emS = ema(cl,cfg["m"])
    slope_up = emS.iloc[-1] > emS.iloc[-1-cfg["sl"]]
    has_slow = not (pd.isna(es))
    bull = (ef>em) and (not has_slow or em>es)
    bear = (ef<em) and (not has_slow or em<es)
    stack = "bullish" if bull else "bearish" if bear else "mixed"
    above = px > em
    win = cl.iloc[-cfg["hl"]:]; hi, lo = win.max(), win.min()
    struct = "near highs" if px>=hi*0.97 else "near lows" if px<=lo*1.05 else "mid-range"
    pctslow = f"{(px/es-1)*100:+.0f}% vs {cfg['sn']}" if has_slow else "no "+cfg["sn"]
    s = (1 if above else -1) + (1 if slope_up else -1) + (1 if bull else -1 if bear else 0)
    cm = f"{'Above' if above else 'Below'} {'rising' if slope_up else 'falling'} {cfg['mn']}, {stack} stack, {pctslow}; {struct}"
    return cm, s

def verdict(sD, sW, sM, cmD):
    tot = sD*1.0 + sW*1.3 + sM*1.3
    dip = (sM>0 and sW>0 and "Above" in cmD and "near highs" not in cmD)  # higher-TF up, daily pulled back
    if tot >= 6:   reco = "STRONG BUY"
    elif tot >= 3: reco = "BUY ON DIP" if dip else "BUY"
    elif tot >= 0.5: reco = "ACCUMULATE / HOLD"
    elif tot > -3: reco = "WATCH"
    elif tot > -6: reco = "AVOID"
    else:          reco = "SELL / AVOID"
    def d(x): return "up" if x>0 else "down" if x<0 else "flat"
    v = f"LT(M) {d(sM)}, Wkly {d(sW)}, Daily {d(sD)} — "
    if sM>0 and sW>0 and sD>0: v += "aligned uptrend across all TFs."
    elif sM>0 and sW>0 and sD<=0: v += "uptrend intact, short-term pullback (dip-buy zone)."
    elif sM>0 and sW<=0: v += "long-term up but weekly weakening — wait for weekly to stabilize."
    elif sM<=0 and sW>0 and sD>0: v += "counter-trend bounce inside a weak long-term trend."
    elif sM<0 and sW<0 and sD<0: v += "aligned downtrend — avoid longs."
    else: v += "mixed signals — no clean edge."
    return v, reco, round(tot,1)

now = datetime.now()
out = []
for sym, g in df.groupby("scrip"):
    d_ = resample(g, "D") if len(g) else g
    d_ = g.set_index("trade_date")  # daily is already daily
    dR = g.rename(columns={}).set_index("trade_date")[["open","high","low","close"]]
    cmD, sD = tf_read(g[["trade_date","close"]].assign(close=g["close"]).set_index("trade_date"), CFG["D"])
    W = resample(g, "W-FRI"); M = resample(g, "ME")
    cmW, sW = tf_read(W, CFG["W"]); cmM, sM = tf_read(M, CFG["M"])
    v, reco, tot = verdict(sD, sW, sM, cmD)
    out.append(dict(
        Date_of_Analysis=now.strftime("%Y-%m-%d %H:%M"),
        Scrip=sym, Exchange=exch.get(sym,"NSE"),
        Close=round(float(g["close"].iloc[-1]),2),
        Daily_TF_Comment=cmD, Weekly_TF_Comment=cmW, Monthly_TF_Comment=cmM,
        Overall_Verdict=v, Buy_Sell_Reco=reco, Score=tot))

res = pd.DataFrame(out).sort_values("Score", ascending=False).reset_index(drop=True)
fname = f"stock_analysis_{now.strftime('%Y%d%m%H%M')}.csv"
path = os.path.join(os.path.dirname(__file__) or ".", fname)
res.to_csv(path, index=False)
print("wrote", path, res.shape)
print(res["Buy_Sell_Reco"].value_counts().to_dict())
print(res.head(6).to_string())
print(res.tail(3).to_string())
print("FNAME=" + fname)
print("PATH=" + path)
