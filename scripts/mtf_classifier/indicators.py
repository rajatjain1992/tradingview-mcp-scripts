import pandas as pd, numpy as np
from numpy.lib.stride_tricks import sliding_window_view

def wilder_ema(s, n):
    return s.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def compute_tf(df, prLen=200, sigLen=5, atrLen=14, atrK=1.0, rsiLen=8, dmiLen=8):
    df = df.sort_values(["scrip","dt"]).reset_index(drop=True)
    g = df.groupby("scrip", observed=True)

    # ---- EMAs / fan / spread sign (v45 logic) ----
    E = {n: g["close"].transform(lambda s,n=n: s.ewm(span=n,adjust=False,min_periods=n).mean()) for n in (20,50,100,200)}
    e1,e2,e3,e4 = E[20],E[50],E[100],E[200]
    stack = pd.concat([e1,e2,e3,e4],axis=1)
    mx, mn = stack.max(axis=1), stack.min(axis=1)
    fan = (mx-mn)/e4*100.0

    trig = g["close"].transform(lambda s: s.rolling(sigLen,min_periods=sigLen).mean())
    pc = g["close"].shift(1)
    tr = pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    atr = tr.groupby(df.scrip, observed=True).transform(lambda s: wilder_ema(s, atrLen))
    band = atr*atrK

    sc = pd.factorize(df.scrip)[0]
    up = (trig > mx+band).to_numpy(); dn = (trig < mn-band).to_numpy()
    bandSgn = np.empty(len(df)); cur=1.0; prev=-1
    for i in range(len(df)):
        if sc[i]!=prev: cur=1.0; prev=sc[i]
        if up[i]: cur=1.0
        elif dn[i]: cur=-1.0
        bandSgn[i]=cur

    def pine_percentrank(x, length):
        n=len(x); out=np.full(n,np.nan)
        finite=np.where(np.isfinite(x))[0]
        if not len(finite): return out
        f=finite[0]; a=x[f:]; m=len(a)
        if m<=length: return out
        w=sliding_window_view(a,length)[:m-length]; curv=a[length:m]
        ok=np.isfinite(curv)&np.isfinite(w).all(axis=1)
        res=np.full(m-length,np.nan)
        if ok.any(): res[ok]=100.0*(w[ok]<=curv[ok,None]).sum(axis=1)/length
        out[f+length:]=res
        return out

    magPct = fan.groupby(df.scrip, observed=True).transform(lambda s: pd.Series(pine_percentrank(s.to_numpy(),prLen), index=s.index))
    v = np.where(magPct.notna(), bandSgn*magPct, np.nan)

    # ---- structure: EMA stack order ----
    asc = (e1>e2)&(e2>e3)&(e3>e4)
    desc= (e1<e2)&(e2<e3)&(e3<e4)
    structure = np.where(asc,1.0, np.where(desc,-1.0,0.0))

    # ---- RSI(8) then EMA(8) smoothed ----
    delta = g["close"].transform(lambda s: s.diff())
    gain = delta.clip(lower=0); loss=(-delta).clip(lower=0)
    avgG = gain.groupby(df.scrip, observed=True).transform(lambda s: wilder_ema(s, rsiLen))
    avgL = loss.groupby(df.scrip, observed=True).transform(lambda s: wilder_ema(s, rsiLen))
    rs = avgG/avgL.replace(0,np.nan)
    rsi = 100 - 100/(1+rs)
    rsi_sm = rsi.groupby(df.scrip, observed=True).transform(lambda s: s.ewm(span=rsiLen,adjust=False,min_periods=rsiLen).mean())

    # ---- Wilder DMI/ADX(8,8) ----
    up_move = g["high"].transform(lambda s: s.diff())
    down_move = -g["low"].transform(lambda s: s.diff())
    plusDM = np.where((up_move>down_move)&(up_move>0), up_move, 0.0)
    minusDM= np.where((down_move>up_move)&(down_move>0), down_move, 0.0)
    plusDM = pd.Series(plusDM, index=df.index); minusDM = pd.Series(minusDM, index=df.index)
    atrD = tr.groupby(df.scrip, observed=True).transform(lambda s: wilder_ema(s, dmiLen))
    plusDI = 100*plusDM.groupby(df.scrip, observed=True).transform(lambda s: wilder_ema(s, dmiLen))/atrD
    minusDI= 100*minusDM.groupby(df.scrip, observed=True).transform(lambda s: wilder_ema(s, dmiLen))/atrD
    dx = 100*(plusDI-minusDI).abs()/(plusDI+minusDI).replace(0,np.nan)
    adx = dx.groupby(df.scrip, observed=True).transform(lambda s: wilder_ema(s, dmiLen))

    out = df[["scrip","dt"]].copy()
    out["v"] = v
    out["structure"] = structure
    out["rsi"] = rsi_sm.to_numpy()
    out["adx"] = adx.to_numpy()
    return out

if __name__=="__main__":
    for name in ["m15","m30","m60","m120","m240","daily","weekly"]:
        df = pd.read_parquet(f"{name}_ohlc.parquet")
        r = compute_tf(df)
        r.to_parquet(f"{name}_ind.parquet", index=False)
        print(f"{name}: v={r.v.notna().sum()}/{len(r)} rsi={r.rsi.notna().sum()} adx={r.adx.notna().sum()}  "
              f"v range [{r.v.min():.1f},{r.v.max():.1f}] rsi range [{r.rsi.min():.1f},{r.rsi.max():.1f}] adx range [{r.adx.min():.1f},{r.adx.max():.1f}]")
