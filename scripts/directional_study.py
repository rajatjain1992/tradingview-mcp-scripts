#!/usr/bin/env python
"""
Backtest the NEW directional fan-spread exhaustion metric across instruments.

metric per TF:  fan=(max(EMA20,50,100,200)-min)/EMA200*100 ; magPct=percentile(fan,L)
                signedPct = sign(EMA20-EMA200)*magPct   (-100..+100)
Exhaustion = |signedPct|>=TH. Question: after an extreme, does price REVERT
(fade works) or CONTINUE (trend)? Plus persistence (bars pinned at extreme).
"""
import glob, json, sys
import numpy as np
import pandas as pd

DATA = r"C:\Users\Rajat\tradingview-mcp\scripts\stretch_data"
NIFTY_CSV = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv"
L, TH = 200, 85
H = {"5": 60, "15": 40, "30": 40, "60": 40, "120": 30, "240": 20, "D": 40, "W": 10}


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def load_json(sym, kind):
    fs = sorted(glob.glob(f"{DATA}/{sym}_{kind}_*.json"))
    if not fs: return None
    d = pd.concat([pd.DataFrame(json.load(open(f))) for f in fs], ignore_index=True)
    d["timestamp"] = pd.to_datetime(d["date"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return d[["timestamp","open","high","low","close"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def chunk(d, k):
    d = d.copy(); d["date"] = d["timestamp"].dt.date; parts = []
    for _, day in d.groupby("date", sort=True):
        x = day.reset_index(drop=True); x["c"] = x.index // k; g = x.groupby("c")
        parts.append(pd.DataFrame({"timestamp": g["timestamp"].first(), "close": g["close"].last()}))
    return pd.concat(parts, ignore_index=True)


def weekly(dd):
    x = dd.set_index("timestamp")
    return x["close"].resample("W-FRI").last().dropna().reset_index()


def nifty_tfs():
    df = pd.read_csv(NIFTY_CSV, parse_dates=["timestamp"]).sort_values("timestamp")
    df["date"] = df["timestamp"].dt.date
    out = {}
    for tf, m in [("5",5),("15",15),("30",30),("60",60),("120",120),("240",240)]:
        parts = []
        for _, day in df.groupby("date", sort=True):
            x = day.reset_index(drop=True); x["c"] = x.index // m
            parts.append(x.groupby("c").agg(timestamp=("timestamp","first"), close=("close","last")))
        out[tf] = pd.concat(parts, ignore_index=True)
    daily = df.groupby("date").agg(timestamp=("timestamp","first"), close=("close","last")).reset_index(drop=True)
    out["D"] = daily; out["W"] = weekly(daily)
    return out


def stock_tfs(sym):
    d60 = load_json(sym, "h60"); dd = load_json(sym, "day")
    out = {}
    if d60 is not None:
        out["60"] = d60[["timestamp","close"]].copy()
        out["120"] = chunk(d60, 2); out["240"] = chunk(d60, 4)
    if dd is not None:
        out["D"] = dd[["timestamp","close"]].copy(); out["W"] = weekly(dd)
    return out


def dstats(df, tf):
    c = df["close"].reset_index(drop=True)
    e = {n: ema(c, n) for n in (20,50,100,200)}
    mx = pd.concat(e.values(), axis=1).max(axis=1); mn = pd.concat(e.values(), axis=1).min(axis=1)
    fan = (mx - mn) / e[200] * 100
    magpct = fan.rolling(L).rank(pct=True) * 100
    sgn = np.sign(e[20] - e[200])
    sp = (sgn * magpct).values
    cv = c.values; h = H[tf]; n = len(cv)
    fwd = np.full(n, np.nan); fwd[:n-h] = cv[h:] / cv[:n-h] - 1
    signed_fwd = -np.sign(sp) * fwd    # +ve = reversal (fade works)
    base = np.nanmean(signed_fwd)
    up = (sp >= TH) & ~np.isnan(fwd); dn = (sp <= -TH) & ~np.isnan(fwd); ext = up | dn
    if ext.sum() < 20: return None
    # persistence: mean run length of |sp|>=TH
    pin = (np.abs(sp) >= TH).astype(int)
    runs = []; r = 0
    for x in pin:
        if x: r += 1
        elif r: runs.append(r); r = 0
    if r: runs.append(r)
    persist = np.mean(runs) if runs else 0
    return dict(tf=tf, n=int(ext.sum()),
        fwd_up=np.nanmean(fwd[up]) if up.sum() else np.nan,   # <0 => up-fan reverts down
        fwd_dn=np.nanmean(fwd[dn]) if dn.sum() else np.nan,   # >0 => down-fan reverts up
        edge=np.nanmean(signed_fwd[ext]) - base, base=base,
        cond=np.nanmean(signed_fwd[ext]), persist=persist,
        nup=int(up.sum()), ndn=int(dn.sum()))


INSTR = ["NIFTY","BANKNIFTY","ICICIBANK","RELIANCE","HDFCBANK","INFY","TCS","ASTRAL"]


def main():
    data = {}
    data["NIFTY"] = nifty_tfs()
    for s in INSTR[1:]:
        data[s] = stock_tfs(s)

    print("="*104)
    print(f"DIRECTIONAL FAN-SPREAD EXHAUSTION | L={L} TH=±{TH} | signed_fwd=-sign(spread)*fwd (reversal=+)")
    print("edge = cond_reversal - baseline (>0 REVERT/fade works, <0 TREND/continues) | persist = avg bars pinned")
    print("="*104)
    tf_order = ["240","D","W","120","60","30","15","5"]
    verdicts = {}
    for s in INSTR:
        print(f"\n### {s}")
        print(f"  {'TF':>4} {'N':>5} {'upFan→fwd':>10} {'dnFan→fwd':>10} {'revEdge':>8} {'persist':>8}  verdict")
        for tf in tf_order:
            if tf not in data[s]: continue
            r = dstats(data[s][tf], tf)
            if r is None: continue
            v = "REVERT" if r["edge"] > 0 else "TREND"
            verdicts[(s,tf)] = (r["edge"], v, r["persist"])
            fu = "n/a" if np.isnan(r["fwd_up"]) else f"{r['fwd_up']*100:+.1f}%"
            fd = "n/a" if np.isnan(r["fwd_dn"]) else f"{r['fwd_dn']*100:+.1f}%"
            print(f"  {tf:>4} {r['n']:>5} {fu:>10} {fd:>10} {r['edge']*100:>+7.2f}% {r['persist']:>7.1f}  {v}"
                  f"   (up {r['nup']} dn {r['ndn']})")

    # summary matrix on the decision TFs
    print("\n" + "="*104)
    print("SUMMARY — reversal edge (%) on higher TFs [+=fade works, -=trend]")
    print("="*104)
    print(f"  {'instrument':<11} " + " ".join(f"{tf:>8}" for tf in ["240","D","W"]))
    for s in INSTR:
        cells = []
        for tf in ["240","D","W"]:
            v = verdicts.get((s,tf))
            cells.append("     n/a" if not v else f"{v[0]*100:>+7.2f}")
        print(f"  {s:<11} " + " ".join(cells))


if __name__ == "__main__":
    main()
