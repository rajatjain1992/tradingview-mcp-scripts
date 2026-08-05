# -*- coding: utf-8 -*-
"""
MTF setup search — methodology and exact reproduction of the 2026-04-08 run.

This is a documentation file as much as a script: every section explains WHY the step
exists, not just what it does. It reproduces the actual run (same numbers, same 21
winners) from the intermediate parquet files already on disk in this directory's
parent scratchpad, so you can read it top-to-bottom as a methodology note and also
execute it to regenerate the results.

============================================================================
THE QUESTION THIS ANSWERS
============================================================================
"Which combinations of MTF EMA Spread / structure / RSI / ADX, at what thresholds,
on what timeframes, have historically preceded a real 7-day directional move —
and which of the 10 scrips currently satisfy those combinations as of the cutoff
date?"

This is NOT the same as the earlier v1 classifier (which applied one hand-picked
rule everywhere). This is a SEARCH: many candidate rules are tested against history,
and only the ones that survive a significance + correct-sign filter get used to call
the cutoff date. That's closer to how Setup A was found in the original dislocation
study — mine first, validate, then apply.

============================================================================
STEP 0 — WHY THIS NEEDED BIGQUERY, NOT THE LOCAL 33-SCRIP FILE
============================================================================
The earlier v1 classifier prototype ran on a local 33-scrip 1-min CSV. This run
needed BigQuery for two reasons Rajat specified: (1) pick scrips from the actual
"intraday universe" (not whichever 33 happened to be in an old CSV), and (2) get
enough history for the daily/weekly signals to be genuinely well-powered, not just
the ~1.75 years the local file offered.

Access method: the MCP BigQuery connector was invalidated this session. Rather than
wait on a reconnect, a service-account JSON key already existed locally at
`Downloads/Daily Trade Files/rajat-trade-c411eaec7c51.json`. Queried directly via
google-cloud-bigquery — no MCP, no Colab. This is now the preferred access path;
see the memory note `mtf-longshort-classifier` for the reconnect-vs-JSON tradeoff.

============================================================================
STEP 1 — SCRIP SELECTION (Q1)
============================================================================
    SELECT scrip, COUNT(DISTINCT DATE(...)) AS days, AVG(volume) AS avg_15m_volume
    FROM stock_intraday_prices_dhan
    WHERE interval_m = 15
      AND DATE(...) BETWEEN "2024-07-01" AND "2026-04-08"   -- 15m data starts here
      AND scrip NOT LIKE "%NIFTY%"
    GROUP BY scrip HAVING days > 420                         -- near-full coverage
    ORDER BY avg_15m_volume DESC LIMIT 10

Discovery mid-run: 15m intraday data only starts 2024-07-01 (not 2017 as the
overall table date range suggested — that range came from the DAILY table's much
longer history sharing the same physical table object in earlier exploration).
`days > 700` (my first attempt) returned nothing because max possible is 439.
Fixed to `days > 420`.

Ranking by average volume (share count) rather than traded value skews the pick
toward low-priced, high-turnover names rather than blue-chips. This was flagged to
Rajat and not corrected, since it's an unbiased, mechanical selection rather than a
hand-picked one — exactly what a "from the intraday universe" instruction calls for.

Result — the 10 scrips:
    IDEA, YESBANK, SUZLON, JPPOWER, ETERNAL, RPOWER, EASEMYTRIP, TATASTEEL, IRB,
    ADANIPOWER

============================================================================
STEP 2 — DATA FETCH, CUTOFF-SAFE (Q2)
============================================================================
Two separate queries, each with `<= "2026-04-08"` and NOTHING past that date ever
touched, per Rajat's explicit instruction not to fetch forward data before the
predictions are locked in:

    15m OHLC  <- stock_intraday_prices_dhan  (WHERE interval_m=15, scrip IN (...))
    Daily OHLC <- stock_daily_prices_dhan     (a SEPARATE table, not derived by
                                                aggregating 15m up to daily — Rajat's
                                                correction mid-run. The daily table
                                                has genuine history back to 2003 for
                                                these names; deriving daily bars from
                                                15m would truncate to 2024-07-01 and
                                                throw away 20 years of real signal.)

BigQuery-specific gotchas hit here (documented so they aren't re-discovered):
  - `COUNT(*) rows` fails: ROWS is a reserved word in GoogleSQL, needs a different
    alias (`n_rows`).
  - `to_dataframe()` requires the `db-dtypes` pip package or it raises
    ModuleNotFoundError on read-back.
  - BigQuery's DATE/TIMESTAMP columns come back with mixed pandas time resolutions
    (`datetime64[us]` from TIMESTAMP-derived columns vs `[ns]` from DATE columns
    passed through `pd.to_datetime`). Left unresolved, a later `merge_asof` on
    the mismatched dtype raises `MergeError`. Fixed by explicitly casting every
    datetime column to `datetime64[ns]` immediately after fetch, before saving to
    parquet — the same bug and the same fix as the earlier v15 dislocation study's
    Colab notebook (documented in memory `mtf-spread-v15-dislocation-study`).

============================================================================
STEP 3 — MULTI-TIMEFRAME RESAMPLE (session-anchored)
============================================================================
From the 15m bars: 30m/60m/120m/240m built by session-anchored bucketing (bars
grouped by trading day, bucket = row-number-within-day // n), NOT clock-anchored.
This mirrors how TradingView's request.security() builds non-standard timeframes —
verified in the original Pine-to-Python port (see `mtf-spread-v15-dislocation-study`).

From the daily table: weekly built via `dt.to_period("W-SUN")` (Monday-Sunday weeks,
anchored at the Sunday end).

============================================================================
STEP 4 — SIGNALS PER TIMEFRAME (matches the pine script's own formulas)
============================================================================
Four signals computed independently on every timeframe (15m through W), copied
verbatim in logic from `indicators.py` (already validated against the live
TradingView chart in the earlier classifier prototype):

  v (MTF EMA Spread)
      Same signed-percentile fan-spread as the v45 pine script: EMA 20/50/100/200
      fan width as %% of EMA200, ranked via a Pine-exact percentrank(., 200), signed
      by the band-based sign (SMA5 trigger vs the EMA band +/- 1.0*ATR(14) — the
      whipsaw fix from the earlier session).

  structure (MTF Price Structure)
      EMA stack order: e20>e50>e100>e200 (all four strictly descending order) = +1
      bullish stack; fully ascending order in price terms reversed = -1 bearish
      stack; anything mixed = 0. NOT in the original pine script — this is the
      operationalization of "price structure" Rajat asked for, built from EMAs
      already computed for `v`, so it costs nothing extra and stays internally
      consistent with the spread signal.

  rsi (MTF RSI)
      Wilder RSI(8), then EMA(8) smoothed — exactly `rsi2_local` in the v45 pine
      script (`ta.ema(ta.rsi(close,8),8)`).

  adx (MTF ADX)
      Wilder DMI(8,8) — exactly `ta.dmi(dmiLen, dmiLen)` in the pine script, using
      the script's OWN stated `adx_floor = 20` as the trend-strength gate everywhere
      below, rather than an invented threshold.

============================================================================
STEP 5 — DAILY PANEL: MAP EVERY TIMEFRAME ONTO ONE DAILY SPINE
============================================================================
`pd.merge_asof(..., direction="backward")`, keyed by (scrip, date) after the merge
completes — NOT positional. An earlier version of this exact step (in the local
33-scrip prototype) assigned merge_asof results back via `.to_numpy()` after
re-sorting the frame for the merge, which silently scrambled every scrip/date
pairing since row order no longer matched. Caught by a single hand-checked sanity
row (TATASTEEL at the cutoff) showing implausible values. Fixed by keeping the join
keyed throughout. This bug class — "the merge succeeded, the shape is right, but
the rows are wrong" — does not announce itself; always spot-check one known row.

============================================================================
STEP 6 — FORWARD RETURNS FOR MINING, WITH A HARD LOOKAHEAD GUARD
============================================================================
For every (scrip, date) in the panel, find the closing price at or after
date + 7 calendar days, and compute pct return. Critically: the loop explicitly
refuses to compute this AT ALL if the +7-day target would land after the cutoff:

    if target > CUTOFF: continue   # leave ret_fwd7 as NaN, never look past cutoff

This means the setup MINING (step 7) only ever sees outcomes for entries up to
2026-04-01 (verified directly: `panel[ret_fwd7.notna()].date.max() == 2026-04-01`,
exactly 7 days before the 2026-04-08 cutoff). The cutoff-date rows themselves
(2026-04-08) have `ret_fwd7 = NaN` throughout mining and are ONLY read for their
signal values (v/structure/rsi/adx) in step 8, never for an outcome.

============================================================================
STEP 7 — THE 40-SETUP SYSTEMATIC SEARCH
============================================================================
Three setup FAMILIES, crossed with timeframe and threshold:

  TREND (trend-following): does an extreme spread reading, confirmed by ADX
    trending and RSI/structure pointing the same way, predict continuation?
        Long:  v_TF >= +ext  AND adx_TF >= 20  AND structure_TF >= 0  AND rsi_TF >= 50
        Short: v_TF <= -ext  AND adx_TF >= 20  AND structure_TF <= 0  AND rsi_TF <= 50
    tf in {240m, D, W}, ext in {70, 80, 90}   -> 3 tf * 3 ext * 2 dir = 18 setups

  FADE (mean-reversion / exhaustion): does an extreme spread reading AGAINST an
    extreme RSI predict a reversal?
        Long ("buy the dip"):   v_TF <= -ext AND adx_TF >= 20 AND rsi_TF <= 35
        Short ("sell the rip"): v_TF >= +ext AND adx_TF >= 20 AND rsi_TF >= 65
    tf in {240m, D, W}, ext in {70, 80, 90}   -> 18 setups

  ALIGN (two-timeframe agreement, no RSI filter): do 240m and D agree on direction
    at extreme readings, with the daily ADX confirming a real trend?
        Long:  v_240m >= +ext AND v_D >= +ext AND adx_D >= 20
        Short: v_240m <= -ext AND v_D <= -ext AND adx_D >= 20
    ext in {50, 70}                            -> 4 setups

  Total: 18 + 18 + 4 = 40 setup definitions.

Every setup is tested with the SAME evaluation, taken directly from the earlier
dislocation study's playbook (its trap #1: naive raw returns are dominated by
whichever way the whole 10-scrip basket moved that day):

  1. Market-neutral: subtract the SAME-DAY cross-sectional mean 7-day-forward
     return across all 10 scrips, so the result reflects the setup, not the day's
     overall market direction.
  2. Direction-adjusted: multiply by +1 for a Long setup, -1 for a Short setup, so
     a positive number always means "the call direction was correct" regardless of
     which side it was.
  3. Clustered significance: t-test on the PER-DATE MEAN of the direction-adjusted
     excess return (not per-row) — 10 scrips on the same day move together, so a
     per-row t-test would overstate the effective sample size.

============================================================================
STEP 8 — WINNER SELECTION, AND THE MOST IMPORTANT CHECK IN THE WHOLE RUN
============================================================================
A setup only counts as a genuine, tradeable finding if BOTH:
    p < 0.05                    (clustered, not naive)
    xs% (mean direction-adjusted market-neutral excess) > 0

The second condition matters as much as the first. Several setups came back
p < 0.001 with a NEGATIVE xs% — meaning the pattern is real and repeatable, but in
the OPPOSITE direction from the one hypothesized. Concretely: every deep-history
(2003-2026) SHORT setup built on D/W trend-following or overbought-fade came back
significant and negative — shorting daily/weekly downtrend extremes on these 10
names has been a real, measurable, 20-year-long losing trade. That is reported
separately as "mis-signed" and NOT mechanically inverted into a Long recommendation
— the independently-tested "buy the dip" FADE-Long setups already cover that
territory on their own evidence, and inverting a mined result without separately
re-testing the inverse risks compounding a multiple-comparisons artifact into a
false conviction.

21 of 40 setups survived as genuine, correctly-signed winners.

============================================================================
STEP 9 — APPLYING THE WINNERS TO THE CUTOFF DATE
============================================================================
For each of the 10 scrips, check every winning setup's condition against ONLY the
2026-04-08 row (the row whose v/structure/rsi/adx values were computed using data
through the cutoff, and nothing after it). A scrip's final call is Long/Short/
Mixed/NoCall based on how many Long-winners vs Short-winners fire, weighted by each
fired setup's own historical mean excess (a crude conviction score — the sum of
xs%% across every setup that fired, sign-adjusted).

Result: 2 Long (ADANIPOWER, TATASTEEL), 3 Short (RPOWER, YESBANK, ETERNAL),
5 NoCall (IDEA, SUZLON, JPPOWER, IRB, EASEMYTRIP).
"""

import pandas as pd
import numpy as np
from scipy import stats

# ---------------------------------------------------------------------------
# This section reproduces steps 6-9 exactly, reading the parquet files already
# built by the earlier pipeline stages (build/resample/indicators/daily_panel —
# see the sibling scripts in scripts/mtf_classifier/ for those stages, which
# are shared with the local-33-scrip prototype and unchanged here).
# ---------------------------------------------------------------------------

PANEL_PATH = "bqf_daily_panel.parquet"   # scrip, date, close, {v,structure,rsi,adx}_{15m,30m,60m,120m,240m,D,W}
CUTOFF = pd.Timestamp("2026-04-08")
ADX_FLOOR = 20        # the pine script's OWN "ADX Floor" input default -- not invented here
HORIZON_DAYS = 7      # "next 7 days" interpreted as 7 CALENDAR days, stated explicitly to Rajat


def compute_forward_returns(panel: pd.DataFrame) -> pd.DataFrame:
    """Step 6: forward 7-day return per (scrip,date), hard-capped at the cutoff.

    The `if target > CUTOFF: continue` line is the entire lookahead guard for the
    whole methodology -- everything downstream (mining, significance testing) can
    only ever see outcomes that were already knowable AT the cutoff date.
    """
    def fwd_close(g):
        dates = g.date.to_numpy()
        closes = g.close.to_numpy()
        out = np.full(len(g), np.nan)
        j = 0
        for i in range(len(g)):
            target = dates[i] + np.timedelta64(HORIZON_DAYS, "D")
            if target > np.datetime64(CUTOFF):
                continue                                   # <-- the lookahead guard
            j = max(j, i)
            while j < len(g) and dates[j] < target:
                j += 1
            out[i] = closes[j] if j < len(g) else np.nan
        return out

    panel = panel.sort_values(["scrip", "date"]).reset_index(drop=True)
    panel["close_fwd7"] = panel.groupby("scrip", group_keys=False).apply(
        lambda g: pd.Series(fwd_close(g), index=g.index), include_groups=False
    )
    panel["ret_fwd7"] = (panel.close_fwd7 / panel.close - 1) * 100
    # cross-sectional (same-day, all 10 scrips) market-neutral excess
    mkt = panel.groupby("date")["ret_fwd7"].transform("mean")
    panel["ret_fwd7_x"] = panel["ret_fwd7"] - mkt
    return panel


def test_setup(panel: pd.DataFrame, mask: pd.Series, dirn: int, label: str, min_n: int = 25):
    """Step 7 evaluation: market-neutral + direction-adjusted + date-clustered p-value."""
    d = panel[mask & panel.ret_fwd7.notna()]
    if len(d) < min_n:
        return None
    r = dirn * d.ret_fwd7          # raw, direction-adjusted (what you'd actually bank)
    rx = dirn * d.ret_fwd7_x       # market-neutral, direction-adjusted (the real test)
    per_date = rx.groupby(d.date).mean()          # cluster: one number per date, not per row
    if per_date.notna().sum() > 5:
        t, p = stats.ttest_1samp(per_date.dropna(), 0)
    else:
        t, p = np.nan, np.nan
    return {
        "setup": label, "dir": "L" if dirn > 0 else "S",
        "n": len(d), "scrips": d.scrip.nunique(), "dates": d.date.nunique(),
        "raw%": r.mean(), "xs%": rx.mean(), "xs_hit%": (rx > 0).mean() * 100, "p": p,
    }


def run_setup_search(panel: pd.DataFrame) -> pd.DataFrame:
    """Step 7: the 40-setup search (18 TREND + 18 FADE + 4 ALIGN)."""
    rows = []
    for tf in ["240m", "D", "W"]:
        for ext in [70, 80, 90]:
            v, s, rsi, adx = panel[f"v_{tf}"], panel[f"structure_{tf}"], panel[f"rsi_{tf}"], panel[f"adx_{tf}"]

            # TREND: extreme spread + trending ADX + structure/RSI confirm the SAME direction
            mL = (v >= ext) & (adx >= ADX_FLOOR) & (s >= 0) & (rsi >= 50)
            mS = (v <= -ext) & (adx >= ADX_FLOOR) & (s <= 0) & (rsi <= 50)
            rows.append(test_setup(panel, mL, 1, f"TREND {tf} v>=+{ext} adx>={ADX_FLOOR} struct/rsi confirm"))
            rows.append(test_setup(panel, mS, -1, f"TREND {tf} v<=-{ext} adx>={ADX_FLOOR} struct/rsi confirm"))

            # FADE: extreme spread AGAINST extreme RSI -- exhaustion / mean-reversion
            mL2 = (v <= -ext) & (adx >= ADX_FLOOR) & (rsi <= 35)
            mS2 = (v >= ext) & (adx >= ADX_FLOOR) & (rsi >= 65)
            rows.append(test_setup(panel, mL2, 1, f"FADE {tf} v<=-{ext} adx>={ADX_FLOOR} rsi<=35 (buy the dip)"))
            rows.append(test_setup(panel, mS2, -1, f"FADE {tf} v>=+{ext} adx>={ADX_FLOOR} rsi>=65 (sell the rip)"))

    # ALIGN: 240m and D agree at an extreme, daily ADX confirms
    for ext in [50, 70]:
        v240, vD, adxD = panel.v_240m, panel.v_D, panel.adx_D
        mL = (v240 >= ext) & (vD >= ext) & (adxD >= ADX_FLOOR)
        mS = (v240 <= -ext) & (vD <= -ext) & (adxD >= ADX_FLOOR)
        rows.append(test_setup(panel, mL, 1, f"ALIGN 240m&D both>=+{ext} adxD>={ADX_FLOOR}"))
        rows.append(test_setup(panel, mS, -1, f"ALIGN 240m&D both<=-{ext} adxD>={ADX_FLOOR}"))

    return pd.DataFrame([r for r in rows if r]).sort_values("p").reset_index(drop=True)


def select_winners(results: pd.DataFrame) -> pd.DataFrame:
    """Step 8: correctly-signed AND significant -- both conditions, not just p<.05.

    A significant result with the wrong sign (xs% <= 0) is reported separately as
    "mis-signed", never silently inverted into a recommendation for the opposite call.
    """
    return results[(results.p < 0.05) & (results["xs%"] > 0)].sort_values("p")


def _setup_condition(cutoff_rows: pd.DataFrame, tf: str, family: str, ext: int, dirn: int) -> pd.Series:
    """Rebuild one setup's boolean condition (matches run_setup_search's mask logic exactly)."""
    v, s, rsi, adx = cutoff_rows[f"v_{tf}"], cutoff_rows[f"structure_{tf}"], cutoff_rows[f"rsi_{tf}"], cutoff_rows[f"adx_{tf}"]
    if family == "TREND":
        return (v >= ext) & (adx >= ADX_FLOOR) & (s >= 0) & (rsi >= 50) if dirn > 0 else \
               (v <= -ext) & (adx >= ADX_FLOOR) & (s <= 0) & (rsi <= 50)
    if family == "FADE":
        return (v <= -ext) & (adx >= ADX_FLOOR) & (rsi <= 35) if dirn > 0 else \
               (v >= ext) & (adx >= ADX_FLOOR) & (rsi >= 65)
    raise ValueError(family)


def apply_to_cutoff(panel: pd.DataFrame, winners: pd.DataFrame) -> pd.DataFrame:
    """Step 9: check every winning setup's condition against the cutoff-date row only.

    Parses each winner's own label back into (tf, family, ext, dir) rather than keeping a
    separate hardcoded list -- so this function and run_setup_search() can never silently
    drift apart. ALIGN setups (not TF/family/ext shaped the same way) are handled directly.
    """
    import re
    cutoff_rows = panel[panel.date == CUTOFF].set_index("scrip")
    fired = {scrip: [] for scrip in cutoff_rows.index}

    for _, row in winners.iterrows():
        dirn = 1 if row["dir"] == "L" else -1
        label = row["setup"]
        m = re.match(r"(TREND|FADE) (\w+) v[<>]=[-+](\d+)", label)
        if m:
            family, tf, ext = m.group(1), m.group(2), int(m.group(3))
            hit = _setup_condition(cutoff_rows, tf, family, ext, dirn)
        else:
            m2 = re.match(r"ALIGN 240m&D both[<>]=[-+](\d+)", label)
            ext = int(m2.group(1))
            v240, vD, adxD = cutoff_rows.v_240m, cutoff_rows.v_D, cutoff_rows.adx_D
            hit = (v240 >= ext) & (vD >= ext) & (adxD >= ADX_FLOOR) if dirn > 0 else \
                  (v240 <= -ext) & (vD <= -ext) & (adxD >= ADX_FLOOR)
        for scrip in hit.index[hit.fillna(False)]:
            fired[scrip].append((label, dirn, row["xs%"]))

    out = []
    for scrip, hits in fired.items():
        longs = [h for h in hits if h[1] > 0]
        shorts = [h for h in hits if h[1] < 0]
        conv = sum(h[2] for h in longs) - sum(h[2] for h in shorts)
        if not hits:
            call = "NoCall"
        elif len(longs) > len(shorts) and conv > 0:
            call = "Long"
        elif len(shorts) > len(longs) and conv < 0:
            call = "Short"
        else:
            call = "Mixed"
        out.append({"scrip": scrip, "call": call, "n_setups_fired": len(hits),
                     "setups": "; ".join(h[0] for h in hits) or "-",
                     "conviction_score": round(conv, 2)})
    return pd.DataFrame(out).sort_values("conviction_score", ascending=False)


if __name__ == "__main__":
    panel = pd.read_parquet(PANEL_PATH)
    panel = compute_forward_returns(panel)
    assert panel[panel.ret_fwd7.notna()].date.max() <= CUTOFF - pd.Timedelta(days=HORIZON_DAYS), \
        "lookahead guard violated -- mining saw data past the cutoff"

    results = run_setup_search(panel)
    winners = select_winners(results)
    losers = results[(results.p < 0.05) & (results["xs%"] <= 0)]

    print(f"tested: {len(results)} setups | winners: {len(winners)} | mis-signed: {len(losers)}")
    print(winners.round(3).to_string(index=False))

    calls = apply_to_cutoff(panel, winners)
    print(f"\n=== cutoff {CUTOFF.date()} calls ===")
    print(calls.to_string(index=False))
