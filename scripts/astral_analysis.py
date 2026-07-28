#!/usr/bin/env python
"""
ASTRAL — long-term direction / entry-exit hypothesis from EMA-stretch exhaustion,
with a per-timeframe PERCENTILE-LOOKBACK sweep.

Question: on each TF, does an extreme stretch percentile lead to reversal
(mean-revert => buy low / sell high) or continuation (trend)? And what lookback
best separates future returns? Then translate to concrete accumulation prices.
"""
import glob, json, sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

DATA = r"C:\Users\Rajat\tradingview-mcp\scripts\stretch_data"
FAST, SLOW = 20, 200
LOOKBACKS = [50, 100, 150, 200, 250, 300]


def load(pattern):
    fs = sorted(glob.glob(pattern))
    d = pd.concat([pd.DataFrame(json.load(open(f))) for f in fs], ignore_index=True)
    d["timestamp"] = pd.to_datetime(d["date"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return d[["timestamp", "open", "high", "low", "close"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def resample_k(d60, k):
    d = d60.copy(); d["date"] = d["timestamp"].dt.date
    parts = []
    for _, day in d.groupby("date", sort=True):
        x = day.reset_index(drop=True); x["chunk"] = x.index // k
        g = x.groupby("chunk")
        parts.append(pd.DataFrame({"timestamp": g["timestamp"].first(),
            "open": g["open"].first(), "high": g["high"].max(),
            "low": g["low"].min(), "close": g["close"].last()}))
    return pd.concat(parts, ignore_index=True)


def to_weekly(dfd):
    x = dfd.set_index("timestamp")
    w = x.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return w


def add_metrics(df):
    c = df["close"]
    df["ema20"] = c.ewm(span=FAST, adjust=False).mean()
    df["ema200"] = c.ewm(span=SLOW, adjust=False).mean()
    df["px_ema200"] = (c - df["ema200"]) / df["ema200"] * 100
    df["ema_spread"] = (df["ema20"] - df["ema200"]) / df["ema200"] * 100
    return df


def sweep(df, metric, horizon):
    """For each lookback: Spearman(percentile, fwd) + mean fwd of bottom-decile
    (cheap/oversold) minus top-decile (rich). +spread => mean-revert (buy low works)."""
    c = df["close"].values
    fwd = np.full(len(c), np.nan)
    fwd[:len(c) - horizon] = c[horizon:] / c[:len(c) - horizon] - 1
    m = df[metric].values
    out = []
    for L in LOOKBACKS:
        pctl = pd.Series(m).rolling(L).rank(pct=True).values * 100
        ok = ~np.isnan(pctl) & ~np.isnan(fwd)
        if ok.sum() < 60:
            out.append((L, np.nan, np.nan, np.nan, 0, np.nan)); continue
        rho = spearmanr(pctl[ok], fwd[ok]).correlation
        bot = ok & (pctl <= 10); top = ok & (pctl >= 90)
        fb = np.nanmean(fwd[bot]) if bot.sum() else np.nan
        ft = np.nanmean(fwd[top]) if top.sum() else np.nan
        spread = (fb - ft) if (bot.sum() and top.sum()) else np.nan
        hitb = np.mean(fwd[bot] > 0) if bot.sum() else np.nan   # long-from-cheap win rate
        out.append((L, rho, fb, ft, spread, hitb))
    return out


def q_of(df, metric, L, q):
    return pd.Series(df[metric].values).rolling(L).rank(pct=True)  # unused helper placeholder


def main():
    dD = add_metrics(load(f"{DATA}/ASTRAL_day_*.json"))
    dW = add_metrics(to_weekly(load(f"{DATA}/ASTRAL_day_*.json")))
    d60 = load(f"{DATA}/ASTRAL_h60_*.json")
    d240 = add_metrics(resample_k(d60, 4))
    d120 = add_metrics(resample_k(d60, 2))
    d60m = add_metrics(d60.copy())

    px = dD["close"].iloc[-1]
    print("="*92)
    print(f"ASTRAL review | daily {dD['timestamp'].iloc[0].date()}→{dD['timestamp'].iloc[-1].date()} "
          f"({len(dD)} bars) | last close {px:.1f}")
    # 2-year snapshot
    d2 = dD[dD["timestamp"] >= dD["timestamp"].iloc[-1] - pd.Timedelta(days=730)]
    ath = dD["close"].max(); athd = dD.loc[dD["close"].idxmax(), "timestamp"].date()
    print(f"2yr range {d2['low'].min():.0f}–{d2['high'].max():.0f} | 8yr ATH {ath:.0f} ({athd}), "
          f"now {(px/ath-1)*100:+.0f}% from ATH")
    print(f"Daily EMA200 {dD['ema200'].iloc[-1]:.0f} (px {'ABOVE' if px>dD['ema200'].iloc[-1] else 'BELOW'}), "
          f"slope {'up' if dD['ema200'].iloc[-1]>dD['ema200'].iloc[-40] else 'DOWN'} | "
          f"Weekly EMA200 {dW['ema200'].iloc[-1]:.0f} (px {'ABOVE' if px>dW['ema200'].iloc[-1] else 'BELOW'}), "
          f"slope {'up' if dW['ema200'].iloc[-1]>dW['ema200'].iloc[-8] else 'DOWN'}")

    tfs = [("W", dW, 12), ("D", dD, 60), ("240", d240, 40), ("120", d120, 40), ("60", d60m, 40)]
    print("\n" + "="*92)
    print("LOOKBACK SWEEP — metric=px_ema200 | rho=Spearman(pctl,fwd) (neg=mean-revert) | "
          "botFwd=fwd after cheap(≤10) | edge=bot-top")
    print("horizon: W=12w, D=60d, 240/120/60=40 bars")
    print("="*92)
    best = {}
    for name, df, H in tfs:
        rows = sweep(df, "px_ema200", H)
        # pick lookback with max mean-revert edge (bot-top), requiring rho<0
        cand = [(L, rho, fb, ft, sp, hb) for (L, rho, fb, ft, sp, hb) in rows if not np.isnan(sp)]
        pick = max(cand, key=lambda r: (r[4] if not np.isnan(r[4]) else -9)) if cand else None
        best[name] = pick
        print(f"\nTF {name:>3}: " + "  ".join(
            f"L{L}:rho{('n/a' if np.isnan(rho) else f'{rho:+.2f}')}/edge{('n/a' if np.isnan(sp) else f'{sp*100:+.1f}%')}"
            for (L, rho, fb, ft, sp, hb) in rows))
        if pick:
            L, rho, fb, ft, sp, hb = pick
            print(f"     -> best L={L} | rho {rho:+.2f} | buy≤10th → fwd {fb*100:+.1f}% (win {hb*100:.0f}%) "
                  f"vs rich≥90th → {ft*100:+.1f}% | edge {sp*100:+.1f}%  "
                  f"[{'MEAN-REVERT' if sp>0 else 'TREND'}]")

    # Also ema_spread on D/W for direction robustness
    print("\n" + "="*92)
    print("ema_spread cross-check (D & W), same sweep")
    print("="*92)
    for name, df, H in [("W", dW, 12), ("D", dD, 60)]:
        rows = sweep(df, "ema_spread", H)
        print(f"TF {name:>3}: " + "  ".join(
            f"L{L}:rho{('n/a' if np.isnan(rho) else f'{rho:+.2f}')}/edge{('n/a' if np.isnan(sp) else f'{sp*100:+.1f}%')}"
            for (L, rho, fb, ft, sp, hb) in rows))

    # ---------------- Best-price accumulation bands (D & W, using best L) ----------------
    print("\n" + "="*92)
    print("BEST-PRICE BANDS (accumulation on downside exhaustion) — px_ema200 percentiles of best-L window")
    print("="*92)
    for name, df in [("W", dW), ("D", dD)]:
        pick = best.get(name)
        L = pick[0] if pick else 200
        m = pd.Series(df["px_ema200"].values)
        pctl_now = m.rolling(L).rank(pct=True).iloc[-1] * 100
        win = df["px_ema200"].values[-L:]
        q5, q10, q25, q50, q90 = np.nanpercentile(win, [5, 10, 25, 50, 90])
        es = df["ema200"].iloc[-1]
        print(f"\nTF {name} (L={L}): current px_ema200 = {df['px_ema200'].iloc[-1]:+.1f}%  "
              f"(percentile {pctl_now:.0f}) | EMA200 = {es:.0f}")
        print(f"   accumulation bands (EMA200 × (1+q)):")
        print(f"     10th pct stretch {q10:+.1f}%  ->  ~{es*(1+q10/100):.0f}")
        print(f"      5th pct stretch {q5:+.1f}%  ->  ~{es*(1+q5/100):.0f}")
        print(f"     median {q50:+.1f}% -> {es*(1+q50/100):.0f} | rich(90th) {q90:+.1f}% -> {es*(1+q90/100):.0f} (trim zone)")


if __name__ == "__main__":
    main()
