#!/usr/bin/env python
"""
MTF ALIGNMENT TEST — does the 240m EMA-stretch reversal edge strengthen when
60m + 120m + 240m are ALL extreme in the same direction at once?

Design (lookahead-safe, aligned boundaries): 240m = 2x120m = 4x60m built from
the cached 60-min bars, so every 240m bar close coincides exactly with a 120m
close and a 60m close. At each 240m signal bar we read the CONCURRENT 60m/120m
stretch state (known at that timestamp) and count how many TFs are extreme on
the SAME side as the 240m stretch (n_aligned in {1,2,3}; 1 = only 240m).

Outcome: signed_fwd = -sign(240m stretch) * fwd_ret  (reversal => positive),
forward 20 240m-bars. Hypothesis: mean edge rises with n_aligned.
"""
import sys, glob, json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

import ema_stretch_study as E

DATA = r"C:\Users\Rajat\tradingview-mcp\scripts\stretch_data"
INSTRUMENTS = ["BANKNIFTY", "ICICIBANK", "RELIANCE", "HDFCBANK", "TCS", "INFY"]
METRICS = ["px_ema200", "ema_spread"]
THS = [80, 85, 90]


def load60(sym):
    frames = [pd.DataFrame(json.load(open(f))) for f in sorted(glob.glob(f"{DATA}/{sym}_h60_*.json"))]
    d = pd.concat(frames, ignore_index=True)
    d["timestamp"] = pd.to_datetime(d["date"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return d[["timestamp", "open", "high", "low", "close"]].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def resample_k(d60, k):
    """Chunk k consecutive 60m bars per session; carry close_ts = last 60m ts of chunk."""
    if k == 1:
        d = d60.copy(); d["close_ts"] = d["timestamp"]; return d
    d = d60.copy(); d["date"] = d["timestamp"].dt.date
    parts = []
    for _, day in d.groupby("date", sort=True):
        x = day.reset_index(drop=True); x["chunk"] = x.index // k
        g = x.groupby("chunk")
        parts.append(pd.DataFrame({
            "timestamp": g["timestamp"].first(), "close_ts": g["timestamp"].last(),
            "open": g["open"].first(), "high": g["high"].max(),
            "low": g["low"].min(), "close": g["close"].last()}))
    return pd.concat(parts, ignore_index=True)


def state_map(df, metric):
    """close_ts -> (signed percentile 0..100, stretch sign)."""
    pct = df[metric + "_pct"].values; sgn = np.sign(df[metric].values)
    return {ts: (p, s) for ts, p, s in zip(df["close_ts"].values, pct, sgn)}


def aligned(other, ts, direction, th):
    """Is the other-TF bar at close_ts=ts extreme on the SAME side as `direction`?"""
    v = other.get(ts)
    if v is None:
        return False
    p, s = v
    if np.isnan(p):
        return False
    if direction > 0:
        return s > 0 and p >= th
    return s < 0 and p <= (100 - th)


def run(sym, out):
    d60 = load60(sym)
    d240 = E.features(resample_k(d60, 4))
    d120 = E.features(resample_k(d60, 2))
    d60f = E.features(resample_k(d60, 1))
    if len(d240) < E.PCT_LOOKBACK + E.FWD_BARS + 20:
        print(f"  skip {sym}", file=sys.stderr); return
    fwd_ret, label, mfe, mae = E.forward_measures(d240, E.FWD_BARS)

    for metric in METRICS:
        m120 = state_map(d120, metric); m60 = state_map(d60f, metric)
        pct = d240[metric + "_pct"].values; ssign = np.sign(d240[metric].values)
        cts = d240["close_ts"].values
        signed_fwd = -ssign * fwd_ret
        rev_hit = (label != 0) & (np.sign(label) == -ssign)
        base_mean = np.nanmean(signed_fwd)

        for th in THS:
            # 240m extreme mask (either tail)
            ext240 = (((pct >= th) & (ssign > 0)) | ((pct <= 100 - th) & (ssign < 0))) & ~np.isnan(signed_fwd)
            n_al = np.ones(len(pct), dtype=int)
            for i in np.where(ext240)[0]:
                d = ssign[i]
                n_al[i] += int(aligned(m120, cts[i], d, th)) + int(aligned(m60, cts[i], d, th))
            buckets = {}
            for k in (1, 2, 3):
                sel = ext240 & (n_al == k)
                n = int(sel.sum())
                if n < 15:
                    buckets[k] = (n, np.nan, np.nan, np.nan); continue
                cm = signed_fwd[sel].mean()
                res = sel & (label != 0)
                hit = rev_hit[res].mean() if res.sum() else np.nan
                p = E.mwu_p(signed_fwd[sel], signed_fwd[~np.isnan(signed_fwd)])
                buckets[k] = (n, cm, hit, p)
            out.append({"sym": sym, "metric": metric, "th": th,
                        "base": base_mean, "buckets": buckets})


def fp(x):
    return "   n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:+6.2f}%"


def main():
    out = []
    for sym in INSTRUMENTS:
        print(f"== {sym} ==", file=sys.stderr); run(sym, out)

    print("\n" + "=" * 104)
    print("MTF ALIGNMENT TEST (base=240m) | edge = cond_mean - baseline | reversal=+ | n=how many of 60/120/240m aligned")
    print("=" * 104)
    cur = None
    for r in out:
        key = (r["sym"], r["metric"])
        if key != cur:
            cur = key
            print(f"\n### {r['sym']:<9} {r['metric']:<10}  (baseline signed_fwd = {r['base']*100:+.2f}%)")
            print(f"  {'θ':>3} | {'n=1 (240 only)':>22} | {'n=2 (+1 TF)':>22} | {'n=3 (all 3)':>22}  gradient")
            print(f"  {'':>3} | {'N   edge   revhit':>22} | {'N   edge   revhit':>22} | {'N   edge   revhit':>22}")
        cells = []
        means = []
        for k in (1, 2, 3):
            n, cm, hit, p = r["buckets"][k]
            edge = None if cm is None or np.isnan(cm) else cm - r["base"]
            means.append(np.nan if cm is None else cm)
            hs = "  n/a" if hit is None or np.isnan(hit) else f"{hit*100:4.0f}%"
            star = "*" if (p is not None and not np.isnan(p) and p < 0.05) else " "
            cells.append(f"{n:>4} {fp(edge)}{star}{hs:>5}")
        mm = [m for m in means if not np.isnan(m)]
        grad = "UP" if len(mm) >= 2 and mm[-1] > mm[0] else ("--" if len(mm) < 2 else "flat/down")
        print(f"  {r['th']:>3} | {cells[0]:>22} | {cells[1]:>22} | {cells[2]:>22}  {grad}")

    # summary: fraction where n3 edge > n1 edge (θ=85)
    print("\n" + "=" * 104)
    print("SUMMARY @θ85 — does all-3-aligned beat 240m-alone? (edge_n3 vs edge_n1)")
    print("=" * 104)
    for r in out:
        if r["th"] != 85:
            continue
        n1, cm1, _, _ = r["buckets"][1]; n3, cm3, h3, p3 = r["buckets"][3]
        if cm1 is None or np.isnan(cm1) or cm3 is None or np.isnan(cm3):
            verdict = "insufficient N"
            d = np.nan
        else:
            d = cm3 - cm1
            verdict = "STRONGER when aligned" if d > 0 else "not stronger"
        print(f"  {r['sym']:<9} {r['metric']:<10} n1={n1:>4} n3={n3:>4}  "
              f"Δ(n3-n1)={('n/a' if np.isnan(d) else f'{d*100:+.2f}%'):>8}  "
              f"n3 revhit={('n/a' if h3 is None or np.isnan(h3) else f'{h3*100:.0f}%'):>4}  {verdict}")


if __name__ == "__main__":
    main()
