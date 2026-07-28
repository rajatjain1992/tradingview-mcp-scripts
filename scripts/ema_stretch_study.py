#!/usr/bin/env python
"""
EMA-stretch reversal hypothesis — conditional forward-return study.

Hypothesis: reversals/rejections cluster when the ROLLING PERCENTILE of any of
  1. EMA spread      = (EMA20 - EMA200)/EMA200 * 100
  2. Price-to-EMA200 = (close - EMA200)/EMA200 * 100
  3. Price-to-EMA20  = (close - EMA20 )/EMA20  * 100
exceeds a high threshold (or drops below its mirror), across TFs 5/15/30/60/120/240/D.

Design (guards against the "one sample" trap):
  * Point-in-time rolling percentile (no look-ahead).
  * Signed, both tails tested via a DIRECTIONAL outcome:
        signed_fwd = -sign(stretch) * fwd_return
    so a move AGAINST the stretch (= reversal) is always positive.
    Baseline mean ~ 0; hypothesis => conditional mean > 0.
  * THREE outcome measures cross-checked: forward return, triple-barrier, MFE/MAE.
  * Threshold SWEEP + monotonicity (edge should grow with percentile, not spike).
  * Conditional-vs-baseline gap is the edge (not the conditional number alone).
  * Mann-Whitney U + block-bootstrap CI (respects autocorrelation/overlap).
  * Per-year sign stability (not driven by one regime/year).
"""
import sys
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

CSV = r"C:\Users\Rajat\Downloads\Daily Trade Files\NIFTY 2020-2026 Data.csv"

TFS = {"5": 5, "15": 15, "30": 30, "60": 60, "120": 120, "240": 240, "D": None}
METRICS = ["ema_spread", "px_ema200", "px_ema20"]
FAST, SLOW = 20, 200
PCT_LOOKBACK = 500          # bars for rolling percentile
FWD_BARS = 20               # forward horizon (bars) — scales with TF
BARRIER_ATR_MULT = 1.5      # triple-barrier width in ATR14
THRESHOLDS = [70, 80, 85, 90, 95, 97, 99]
HEADLINE_TH = 90
N_BOOT = 2000
rng = np.random.default_rng(42)


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"])
    df = df[["timestamp", "open", "high", "low", "close"]].sort_values("timestamp")
    df["date"] = df["timestamp"].dt.date
    return df.reset_index(drop=True)


def resample(df, minutes):
    """Bar-count chunking anchored at each session open; EMAs computed on the
    continuous resampled series afterward (carry across days)."""
    if minutes is None:  # daily
        g = df.groupby("date")
        out = pd.DataFrame({
            "timestamp": g["timestamp"].first(),
            "open": g["open"].first(), "high": g["high"].max(),
            "low": g["low"].min(), "close": g["close"].last(),
        }).reset_index(drop=True)
        return out
    parts = []
    for _, day in df.groupby("date", sort=True):
        d = day.reset_index(drop=True)
        d["chunk"] = d.index // minutes
        g = d.groupby("chunk")
        parts.append(pd.DataFrame({
            "timestamp": g["timestamp"].first(),
            "open": g["open"].first(), "high": g["high"].max(),
            "low": g["low"].min(), "close": g["close"].last(),
        }))
    out = pd.concat(parts, ignore_index=True)
    return out


def features(d):
    c = d["close"]
    ema_f = c.ewm(span=FAST, adjust=False).mean()
    ema_s = c.ewm(span=SLOW, adjust=False).mean()
    d["ema_spread"] = (ema_f - ema_s) / ema_s * 100
    d["px_ema200"] = (c - ema_s) / ema_s * 100
    d["px_ema20"] = (c - ema_f) / ema_f * 100
    # signed rolling percentile (0..1), point-in-time
    for m in METRICS:
        d[m + "_pct"] = d[m].rolling(PCT_LOOKBACK).rank(pct=True) * 100
    # ATR14 for barriers
    pc = c.shift(1)
    tr = pd.concat([(d["high"] - d["low"]),
                    (d["high"] - pc).abs(),
                    (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    return d


def forward_measures(d, h):
    """Return arrays aligned to entry bar t: fwd_ret, barrier_label(+1 up/-1 dn/0 timeout),
    mfe, mae (as fractions of entry close)."""
    H, L, C = d["high"].values, d["low"].values, d["close"].values
    atr = d["atr"].values
    n = len(C)
    fwd_ret = np.full(n, np.nan)
    fwd_ret[:n - h] = C[h:] / C[:n - h] - 1.0

    mfe = np.full(n, np.nan); mae = np.full(n, np.nan)
    label = np.full(n, np.nan)
    if n <= h + 1:
        return fwd_ret, label, mfe, mae
    swH = sliding_window_view(H, h)   # row i = H[i:i+h]
    swL = sliding_window_view(L, h)
    # entry t: forward window is rows starting t+1 -> swH[1 : n-h+1]
    fH = swH[1:n - h + 1]             # shape (n-h, h)
    fL = swL[1:n - h + 1]
    ent = C[:n - h]
    mfe[:n - h] = fH.max(axis=1) / ent - 1.0
    mae[:n - h] = fL.min(axis=1) / ent - 1.0
    # triple barrier
    up = ent + BARRIER_ATR_MULT * atr[:n - h]
    dn = ent - BARRIER_ATR_MULT * atr[:n - h]
    up_hit = fH >= up[:, None]
    dn_hit = fL <= dn[:, None]
    up_any, dn_any = up_hit.any(1), dn_hit.any(1)
    up_first = np.where(up_any, up_hit.argmax(1), h + 1)
    dn_first = np.where(dn_any, dn_hit.argmax(1), h + 1)
    lab = np.zeros(n - h)
    lab[up_first < dn_first] = 1.0
    lab[dn_first < up_first] = -1.0
    label[:n - h] = lab
    return fwd_ret, label, mfe, mae


def block_boot_ci(x, blk, n_boot=N_BOOT):
    x = x[~np.isnan(x)]
    if len(x) < blk + 5:
        return (np.nan, np.nan)
    nb = int(np.ceil(len(x) / blk))
    starts_max = len(x) - blk
    means = np.empty(n_boot)
    for b in range(n_boot):
        s = rng.integers(0, starts_max + 1, size=nb)
        idx = (s[:, None] + np.arange(blk)).ravel()[:len(x)]
        means[b] = x[idx].mean()
    return tuple(np.percentile(means, [2.5, 97.5]))


def mwu_p(a, b):
    from scipy.stats import mannwhitneyu
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 10 or len(b) < 10:
        return np.nan
    try:
        return mannwhitneyu(a, b, alternative="greater").pvalue
    except Exception:
        return np.nan


def run_tf(df, tf_name, minutes, report):
    d = features(resample(df, minutes))
    if len(d) < PCT_LOOKBACK + FWD_BARS + 20:
        return
    fwd_ret, label, mfe, mae = forward_measures(d, FWD_BARS)
    d["year"] = pd.to_datetime(d["timestamp"]).dt.year

    for m in METRICS:
        pct = d[m + "_pct"].values
        stretch_sign = np.sign(d[m].values)
        # directional outcome: reversal (move against stretch) is positive
        signed_fwd = -stretch_sign * fwd_ret
        # triple-barrier reversal: barrier opposite to stretch hit first
        # label +1=up first, -1=dn first ; reversal = opposite sign to stretch
        rev_hit = (label != 0) & (np.sign(label) == -stretch_sign)
        cont_hit = (label != 0) & (np.sign(label) == stretch_sign)
        signed_mae = -stretch_sign * np.where(stretch_sign > 0, mae, mfe)  # adverse-to-stretch excursion (positive = reversal room)

        base = signed_fwd[~np.isnan(signed_fwd)]
        base_mean = np.nanmean(signed_fwd)

        rows = []
        for th in THRESHOLDS:
            # both tails: upper (pct>th) OR lower (pct<100-th). signed_fwd already
            # orients both so reversal is positive; just union the extreme mask.
            mask = (pct >= th) | (pct <= (100 - th))
            mask &= ~np.isnan(signed_fwd)
            n_sig = int(mask.sum())
            if n_sig < 30:
                rows.append((th, n_sig, np.nan, np.nan, np.nan, np.nan, np.nan, (np.nan, np.nan)))
                continue
            cmean = signed_fwd[mask].mean()
            edge = cmean - base_mean
            # reversal hit-rate among resolved barriers in the conditioned set
            res = mask & (label != 0)
            hit = rev_hit[res].mean() if res.sum() else np.nan
            p = mwu_p(signed_fwd[mask], base)
            ci = block_boot_ci(signed_fwd[mask], blk=FWD_BARS)
            rows.append((th, n_sig, base_mean, cmean, edge, hit, p, ci))

        # monotonicity of edge across thresholds (Spearman of edge vs th)
        edges = np.array([r[4] for r in rows], float)
        ths = np.array([r[0] for r in rows], float)
        ok = ~np.isnan(edges)
        mono = np.nan
        if ok.sum() >= 3:
            from scipy.stats import spearmanr
            mono = spearmanr(ths[ok], edges[ok]).correlation

        # per-year sign stability at headline threshold
        hmask = ((pct >= HEADLINE_TH) | (pct <= 100 - HEADLINE_TH)) & ~np.isnan(signed_fwd)
        yr = d["year"].values
        year_means = {}
        for y in np.unique(yr[hmask]):
            ym = hmask & (yr == y)
            if ym.sum() >= 20:
                year_means[int(y)] = signed_fwd[ym].mean()
        pos_years = sum(v > 0 for v in year_means.values())

        report.append({
            "tf": tf_name, "metric": m, "rows": rows, "mono": mono,
            "year_means": year_means, "pos_years": pos_years, "n_years": len(year_means),
            "base_rev_rate": rev_hit[~np.isnan(fwd_ret)].mean() if (~np.isnan(fwd_ret)).sum() else np.nan,
        })


def fmt_pct(x):
    return "  n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:6.3f}%"


def main():
    print("Loading NIFTY 1-min ...", file=sys.stderr)
    df = load()
    print(f"  {len(df):,} bars  {df['timestamp'].min()} -> {df['timestamp'].max()}", file=sys.stderr)
    report = []
    for name, mins in TFS.items():
        print(f"TF {name} ...", file=sys.stderr)
        run_tf(df, name, mins, report)

    print("\n" + "=" * 96)
    print("EMA-STRETCH REVERSAL STUDY  |  signed_fwd = -sign(stretch)*fwd_ret  (reversal => positive)")
    print(f"lookback={PCT_LOOKBACK}  fwd={FWD_BARS} bars  barrier={BARRIER_ATR_MULT}xATR14  boot={N_BOOT}")
    print("=" * 96)
    for r in report:
        print(f"\n### TF {r['tf']:>3}  |  {r['metric']:<11}  "
              f"| edge-monotonicity(Spearman vs θ)={r['mono']:+.2f}  "
              f"| yrs edge>0: {r['pos_years']}/{r['n_years']}")
        print(f"  {'θ':>3} {'N':>7} {'base':>8} {'cond':>8} {'edge':>9} {'rev-hit':>8} {'MWU-p':>8} {'boot95CI(cond)':>22}")
        for (th, n, bm, cm, edge, hit, p, ci) in r["rows"]:
            ci_s = "n/a" if np.isnan(ci[0]) else f"[{ci[0]*100:+.3f},{ci[1]*100:+.3f}]%"
            print(f"  {th:>3} {n:>7} {fmt_pct(bm)} {fmt_pct(cm)} "
                  f"{('  n/a' if np.isnan(edge) else f'{edge*100:+7.3f}%'):>9} "
                  f"{('  n/a' if np.isnan(hit) else f'{hit*100:5.1f}%'):>8} "
                  f"{('  n/a' if np.isnan(p) else f'{p:6.4f}'):>8} {ci_s:>22}")
        if r["year_means"]:
            ys = "  ".join(f"{y}:{v*100:+.2f}%" for y, v in sorted(r["year_means"].items()))
            print(f"  per-year @θ{HEADLINE_TH}: {ys}")

    # headline verdict scan
    print("\n" + "=" * 96)
    print("HEADLINE SCAN — (TF, metric) with positive monotonicity, θ99 edge>0, MWU p<0.05, yrs>0 >= 4/6")
    print("=" * 96)
    for r in report:
        last = r["rows"][-1]  # θ99
        edge99, p99 = last[4], last[6]
        cond = (not np.isnan(r["mono"]) and r["mono"] > 0.3
                and not np.isnan(edge99) and edge99 > 0
                and not np.isnan(p99) and p99 < 0.05
                and r["n_years"] and r["pos_years"] / max(r["n_years"], 1) >= 0.66)
        flag = "  *** SURVIVES ***" if cond else ""
        print(f"  {r['tf']:>3} {r['metric']:<11} mono={r['mono']:+.2f} "
              f"edge99={'n/a' if np.isnan(edge99) else f'{edge99*100:+.3f}%'} "
              f"p99={'n/a' if np.isnan(p99) else f'{p99:.4f}'} "
              f"yrs={r['pos_years']}/{r['n_years']}{flag}")


if __name__ == "__main__":
    main()
