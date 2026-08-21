"""Core logic: detect Daily-RSI-crosses-above-Weekly-RSI events for one symbol's
daily OHLC and score forward returns at multiple horizons.

RSI HERE = Rajat's actual MTF RSI Indicator V4 variable, NOT plain textbook
RSI: `rsi2` from mtf_rsi_adx_calc() = EMA(RSI(close, 8), 8), dmi_len/rsi_len=8
(confirmed 2026-08-21 against the live Data Window's "RSI EMA8" label -- see
memory/canonical-python-indicator-port.md). Same 8/8 lengths on BOTH Daily and
Weekly, per his confirmation ("run on these always").

Weekly RSI join uses the same "last closed HTF bar" semantics validated in
scripts/pine/run_mtf_indicators.py (HTF_OFFSET pattern) -- a daily bar can only
see the weekly RSI of the most recently COMPLETED week (Fri close), not the
still-forming current week. Weekly bar labeled at its Monday (W-FRI, label=left)
becomes visible from Monday+7d (the following Monday) onward.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pine"))
import numpy as np
import pandas as pd
from mtf_indicators import mtf_rsi_adx_calc, resample_weekly

RSI_LEN = 8
DMI_LEN = 8
# Calendar-day horizons (not trading-day counts) -- "30d later" = first available
# close on/after event_date + 30 calendar days, so weekends/holidays don't skew it.
FWD_HORIZONS = {"1w": 7, "2w": 14, "30d": 30, "60d": 60, "90d": 90, "180d": 180, "1y": 365}
MIN_DAILY_BARS = 300  # ~1.5yr, need enough weeks for weekly rsi2 (RSI8+EMA8) to mature + buffer
MFE_MAE_CAP_DAYS = 365  # hard ceiling on an event's own window if no new signal fires first
MIN_GAP_DAYS = 10  # collapse whipsaw re-triggers within this many days into one independent signal


def compute_symbol(daily: pd.DataFrame, rsi_len: int = RSI_LEN, dmi_len: int = DMI_LEN) -> pd.DataFrame | None:
    """daily: columns [timestamp, open, high, low, close], one symbol, sorted asc.
    Returns the same df with daily_rsi, weekly_rsi (as-of, no lookahead; both are
    the rsi2 = EMA(RSI(close,8),8) variable), cross_up flag, and fwd{h} columns --
    or None if insufficient history."""
    d = daily.sort_values("timestamp").reset_index(drop=True).copy()
    if len(d) < MIN_DAILY_BARS:
        return None

    close = d["close"].to_numpy(dtype=float)
    daily_calc = mtf_rsi_adx_calc(d, dmi_len=dmi_len, rsi_len=rsi_len)
    d["daily_rsi"] = daily_calc["rsi2"].to_numpy()
    d["daily_adx"] = daily_calc["adx"].to_numpy()

    wk = resample_weekly(d[["timestamp", "open", "high", "low", "close"]])
    if len(wk) < rsi_len + 12:
        return None
    wk = wk.copy()
    wk_calc = mtf_rsi_adx_calc(wk, dmi_len=dmi_len, rsi_len=rsi_len)
    wk["weekly_rsi"] = wk_calc["rsi2"].to_numpy()
    wk["weekly_adx"] = wk_calc["adx"].to_numpy()
    wk["avail_from"] = wk["timestamp"] + pd.Timedelta(days=7)

    d = pd.merge_asof(
        d.sort_values("timestamp"),
        wk[["avail_from", "weekly_rsi", "weekly_adx"]].rename(columns={"avail_from": "timestamp"}).sort_values("timestamp"),
        on="timestamp", direction="backward",
    )

    prev_d = d["daily_rsi"].shift(1)
    prev_w = d["weekly_rsi"].shift(1)
    d["cross_up"] = (prev_d <= prev_w) & (d["daily_rsi"] > d["weekly_rsi"]) & prev_d.notna() & prev_w.notna()

    n = len(d)
    dates = d["timestamp"].to_numpy()
    for label, days in FWD_HORIZONS.items():
        target = dates + np.timedelta64(days, "D")
        idx = np.searchsorted(dates, target, side="left")  # first bar with date >= target
        fwd = np.full(n, np.nan)
        valid = idx < n
        fwd[valid] = close[idx[valid]] / close[valid] - 1.0
        d[f"fwd_{label}"] = fwd

    # Independent signal = first cross_up in a cluster, collapsing re-triggers
    # within MIN_GAP_DAYS of the prior kept signal (whipsaw around the weekly
    # line isn't N separate trades). This replaces the old post-hoc
    # dedupe_whipsaw() pass -- it has to happen here, BEFORE the MFE/MAE
    # bounding below, otherwise a kept event immediately followed by a
    # whipsaw re-trigger (which gets dropped) would have its window wrongly
    # truncated to the dropped re-trigger's date instead of the next REAL
    # independent signal.
    raw_idx = np.where(d["cross_up"].to_numpy())[0]
    sig_idx = []
    last_t = None
    for i in raw_idx:
        if last_t is None or (dates[i] - last_t) / np.timedelta64(1, "D") >= MIN_GAP_DAYS:
            sig_idx.append(i)
        last_t = dates[i]
    sig_idx = np.array(sig_idx, dtype=int)
    d["signal"] = False
    d.loc[sig_idx, "signal"] = True

    # MFE (best run-up) / MAE (worst drawdown), each measured over THIS event's
    # OWN window -- bounded by whichever comes first: the NEXT independent
    # signal for this symbol, or a 365-calendar-day cap. A blind fixed 1-year
    # window is wrong here: if a second signal fires on day 45, the days
    # 45-365 price action belongs to that second (independent) trade, not this
    # one -- a flat 1y window for every event lets nearby signals' excursions
    # bleed into each other and inflates/distorts the MFE/MAE distribution.
    cap_target = dates + np.timedelta64(MFE_MAE_CAP_DAYS, "D")
    cap_idx = np.searchsorted(dates, cap_target, side="left")
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    final_ret = np.full(n, np.nan)
    window_days = np.full(n, np.nan)
    for k, i in enumerate(sig_idx):
        next_i = sig_idx[k + 1] if k + 1 < len(sig_idx) else n
        j = min(cap_idx[i], next_i - 1, n - 1)
        if j <= i:
            continue
        path = close[i : j + 1]
        mfe[i] = path.max() / close[i] - 1.0
        mae[i] = path.min() / close[i] - 1.0
        final_ret[i] = close[j] / close[i] - 1.0
        window_days[i] = (dates[j] - dates[i]) / np.timedelta64(1, "D")
    d["mfe"] = mfe
    d["mae"] = mae
    d["final_ret"] = final_ret
    d["window_days"] = window_days

    return d


def extract_events(d: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Pull the independent-signal rows (already whipsaw-deduped inside
    compute_symbol, see 'signal' column) into an events table, symbol tagged."""
    ev = d[d["signal"]].copy()
    ev.insert(0, "symbol", symbol)
    cols = (["symbol", "timestamp", "close", "daily_rsi", "weekly_rsi", "daily_adx", "weekly_adx",
             "mfe", "mae", "final_ret", "window_days"]
            + [f"fwd_{k}" for k in FWD_HORIZONS])
    return ev[cols]


def extract_raw_events(d: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """All cross_up rows incl. whipsaw re-triggers (mfe/mae are NaN on the
    dropped re-triggers -- only 'signal' rows get those) -- for measuring how
    much whipsaw the RSI/weekly-RSI cross produces before dedup."""
    ev = d[d["cross_up"]].copy()
    ev.insert(0, "symbol", symbol)
    cols = ["symbol", "timestamp", "close", "daily_rsi", "weekly_rsi"] + [f"fwd_{k}" for k in FWD_HORIZONS]
    return ev[cols]


def summarize(ev: pd.DataFrame, label: str = "") -> None:
    print(f"\n=== {label} : {len(ev)} events across {ev['symbol'].nunique()} symbols ===")
    if len(ev) == 0:
        return
    print(f"{'horizon':8} {'n':>5} {'win%':>6} {'mean%':>7} {'median%':>8} {'std%':>7}")
    for k in FWD_HORIZONS:
        s = ev[f"fwd_{k}"].dropna() * 100
        if len(s) == 0:
            continue
        print(f"{k:8} {len(s):5d} {100*(s>0).mean():5.1f}% {s.mean():+6.1f}% {s.median():+7.1f}% {s.std():6.1f}%")


def summarize_mfe_mae(ev: pd.DataFrame, label: str = "") -> None:
    """Distribution of MFE (best run-up) and MAE (worst drawdown), each already
    measured over the event's own next-signal-bounded window (see compute_symbol)
    -- not a blind fixed calendar window."""
    sub = ev.dropna(subset=["mfe", "mae", "final_ret", "window_days"])
    print(f"\n=== {label} MFE/MAE (n={len(sub)}, event-bounded windows) ===")
    for col, desc in [("mfe", "MFE (best run-up)"), ("mae", "MAE (worst drawdown)"), ("final_ret", "return at window end")]:
        s = sub[col] * 100
        q = s.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
        print(f"  {desc:22s} p10={q[0.10]:+7.1f}% p25={q[0.25]:+7.1f}% median={q[0.50]:+7.1f}% "
              f"p75={q[0.75]:+7.1f}% p90={q[0.90]:+7.1f}%")
    print(f"  {'window length (days)':22s} median={sub['window_days'].median():.0f}d "
          f"mean={sub['window_days'].mean():.0f}d p90={sub['window_days'].quantile(0.9):.0f}d")


def bucket_by_mfe(ev: pd.DataFrame, n_buckets: int = 10, top_n: int = 3) -> pd.DataFrame:
    """Percentile-bucket every event by its MFE (best favorable excursion within
    its own event-bounded window), print the bucket profile, and return the
    events in the top `top_n` buckets (highest MFE) for closer inspection."""
    sub = ev.dropna(subset=["mfe", "mae", "final_ret"]).copy()
    sub["mfe_bucket"] = pd.qcut(sub["mfe"], n_buckets, labels=False, duplicates="drop") + 1
    print(f"\n=== MFE percentile buckets (n={len(sub)}, {n_buckets} buckets) ===")
    print(f"{'bkt':>3} {'mfe range':>18} {'n':>5} {'avg mae':>8} {'avg final':>9} {'win%(final>0)':>13} {'avg wk_rsi':>10} {'avg wk_adx':>10}")
    for b in sorted(sub["mfe_bucket"].unique()):
        g = sub[sub["mfe_bucket"] == b]
        print(f"{int(b):3d} {g['mfe'].min()*100:+7.1f}%..{g['mfe'].max()*100:+6.1f}% {len(g):5d} "
              f"{g['mae'].mean()*100:+7.1f}% {g['final_ret'].mean()*100:+8.1f}% "
              f"{100*(g['final_ret']>0).mean():12.1f}% {g['weekly_rsi'].mean():10.1f} {g['weekly_adx'].mean():10.1f}")
    top_buckets = sorted(sub["mfe_bucket"].unique())[-top_n:]
    top = sub[sub["mfe_bucket"].isin(top_buckets)].sort_values("mfe", ascending=False)
    print(f"\ntop {top_n} buckets ({int(min(top_buckets))}-{int(max(top_buckets))}): {len(top)} events, "
          f"{top['symbol'].nunique()} symbols")
    return top
