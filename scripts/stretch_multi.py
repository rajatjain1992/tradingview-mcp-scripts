#!/usr/bin/env python
"""
Higher-TF EMA-stretch reversal study across multiple instruments (Kite data).
Reuses the analysis engine from ema_stretch_study.py. Runs Daily + 240m
(240m built from 60min bars, 4 per chunk anchored at session open).
Focus metrics: px_ema200 (strongest on NIFTY), ema_spread, px_ema20.
"""
import sys, glob, json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

import ema_stretch_study as E   # engine: features, forward_measures, block_boot_ci, mwu_p, constants

DATA = r"C:\Users\Rajat\tradingview-mcp\scripts\stretch_data"
INSTRUMENTS = ["BANKNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS"]
HEADLINE_TH = 90


def load_json_ohlc(pattern):
    frames = []
    for f in sorted(glob.glob(pattern)):
        with open(f) as fh:
            frames.append(pd.DataFrame(json.load(fh)))
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d["timestamp"] = pd.to_datetime(d["date"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    d = d[["timestamp", "open", "high", "low", "close"]].drop_duplicates("timestamp").sort_values("timestamp")
    return d.reset_index(drop=True)


def resample_240_from_60(d):
    """Chunk 4 consecutive 60min bars per day; EMAs computed on continuous result."""
    d = d.copy()
    d["date"] = d["timestamp"].dt.date
    parts = []
    for _, day in d.groupby("date", sort=True):
        x = day.reset_index(drop=True)
        x["chunk"] = x.index // 4
        g = x.groupby("chunk")
        parts.append(pd.DataFrame({
            "timestamp": g["timestamp"].first(), "open": g["open"].first(),
            "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()}))
    return pd.concat(parts, ignore_index=True)


def analyze(d, sym, tf_name, out):
    d = E.features(d.copy())
    if len(d) < E.PCT_LOOKBACK + E.FWD_BARS + 20:
        print(f"  [skip {sym} {tf_name}: only {len(d)} bars]", file=sys.stderr)
        return
    fwd_ret, label, mfe, mae = E.forward_measures(d, E.FWD_BARS)
    d["year"] = pd.to_datetime(d["timestamp"]).dt.year
    for m in E.METRICS:
        pct = d[m + "_pct"].values
        ssign = np.sign(d[m].values)
        signed_fwd = -ssign * fwd_ret
        rev_hit = (label != 0) & (np.sign(label) == -ssign)
        base = signed_fwd[~np.isnan(signed_fwd)]
        base_mean = np.nanmean(signed_fwd)
        rows = []
        for th in E.THRESHOLDS:
            mask = ((pct >= th) | (pct <= (100 - th))) & ~np.isnan(signed_fwd)
            n = int(mask.sum())
            if n < 30:
                rows.append((th, n, base_mean, np.nan, np.nan, np.nan, np.nan)); continue
            cmean = signed_fwd[mask].mean()
            res = mask & (label != 0)
            hit = rev_hit[res].mean() if res.sum() else np.nan
            p = E.mwu_p(signed_fwd[mask], base)
            rows.append((th, n, base_mean, cmean, cmean - base_mean, hit, p))
        edges = np.array([r[4] for r in rows], float); ths = np.array([r[0] for r in rows], float)
        ok = ~np.isnan(edges)
        mono = spearmanr(ths[ok], edges[ok]).correlation if ok.sum() >= 3 else np.nan
        # per-year at headline
        hmask = ((pct >= HEADLINE_TH) | (pct <= 100 - HEADLINE_TH)) & ~np.isnan(signed_fwd)
        yr = d["year"].values; ym = {}
        for y in np.unique(yr[hmask]):
            sel = hmask & (yr == y)
            if sel.sum() >= 15: ym[int(y)] = signed_fwd[sel].mean()
        out.append({"sym": sym, "tf": tf_name, "metric": m, "rows": rows, "mono": mono,
                    "pos_years": sum(v > 0 for v in ym.values()), "n_years": len(ym), "ym": ym})


def fp(x):
    return "   n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:+6.3f}%"


def main():
    out = []
    for sym in INSTRUMENTS:
        print(f"== {sym} ==", file=sys.stderr)
        dd = load_json_ohlc(f"{DATA}/{sym}_day_*.json")
        if dd is not None:
            analyze(dd, sym, "D", out)
        h60 = load_json_ohlc(f"{DATA}/{sym}_h60_*.json")
        if h60 is not None:
            analyze(resample_240_from_60(h60), sym, "240", out)

    print("\n" + "=" * 100)
    print("HIGHER-TF EMA-STRETCH REVERSAL — 6 instruments | signed_fwd=-sign(stretch)*fwd_ret (reversal=+)")
    print(f"lookback={E.PCT_LOOKBACK} fwd={E.FWD_BARS}bars | edge=cond-base | +mono = edge grows with extremity")
    print("=" * 100)
    for r in out:
        print(f"\n### {r['sym']:<9} {r['tf']:>3} {r['metric']:<11} | mono(edge vs θ)={r['mono']:+.2f} | yrs edge>0: {r['pos_years']}/{r['n_years']}")
        print(f"  {'θ':>3} {'N':>6} {'base':>8} {'cond':>8} {'edge':>8} {'revhit':>7} {'MWUp':>7}")
        for (th, n, bm, cm, edge, hit, p) in r["rows"]:
            print(f"  {th:>3} {n:>6} {fp(bm)} {fp(cm)} {fp(edge)} "
                  f"{('  n/a' if hit is None or np.isnan(hit) else f'{hit*100:5.1f}%'):>7} "
                  f"{('  n/a' if p is None or np.isnan(p) else f'{p:.4f}'):>7}")
        if r["ym"]:
            print("  yr@θ90: " + "  ".join(f"{y}:{v*100:+.2f}%" for y, v in sorted(r["ym"].items())))

    # verdict scan
    print("\n" + "=" * 100)
    print("VERDICT SCAN — SURVIVES = +mono>0.3, θ95 edge>0, θ95 MWUp<0.05, yrs>0 ratio>=0.6")
    print("=" * 100)
    print(f"  {'sym':<9} {'tf':>3} {'metric':<11} {'mono':>6} {'edge95':>8} {'p95':>7} {'yrs':>6}  verdict")
    for r in out:
        th95 = [row for row in r["rows"] if row[0] == 95][0]
        edge95, p95 = th95[4], th95[6]
        surv = (not np.isnan(r["mono"]) and r["mono"] > 0.3 and edge95 is not None
                and not np.isnan(edge95) and edge95 > 0 and p95 is not None and not np.isnan(p95)
                and p95 < 0.05 and r["n_years"] and r["pos_years"]/r["n_years"] >= 0.6)
        print(f"  {r['sym']:<9} {r['tf']:>3} {r['metric']:<11} {r['mono']:+.2f} "
              f"{fp(edge95)} {('n/a' if p95 is None or np.isnan(p95) else f'{p95:.4f}'):>7} "
              f"{r['pos_years']}/{r['n_years']:<4}  {'*** SURVIVES ***' if surv else ''}")


if __name__ == "__main__":
    main()
