# Daily RSI × Weekly RSI crossover study — findings (2026-08-21)

Batch 1 of the market-cap-ranked universe (top 50 stocks by mcap, from
`scripts/mtf_classifier/full_universe_batches.csv`), 2005-2026 daily data from
`rajat-trade.stock_data_set.stock_daily_prices_dhan`.

## Methodology

- **RSI = Rajat's actual MTF RSI Indicator V4 variable**, not textbook RSI:
  `rsi2 = EMA(RSI(close, 8), 8)`, dmi_len/rsi_len=8, from `mtf_rsi_adx_calc()`
  in `scripts/pine/mtf_indicators.py` (confirmed against the live chart's "RSI
  EMA8" Data Window label). Same 8/8 lengths on Daily and Weekly.
- **Signal** = Daily rsi2 crosses from ≤ to > the as-of Weekly rsi2. Weekly
  value is joined using "last CLOSED week" semantics (shift the weekly join
  key forward by 7 days before `merge_asof`) — a mid-week daily bar never sees
  a still-forming weekly value.
- **Whipsaw dedup**: re-triggers within 10 days of a kept signal for the same
  symbol are collapsed into one independent signal (`signal` column in
  `compute_symbol()`).
- **MFE/MAE are event-bounded, NOT a blind fixed window.** Each signal's own
  window ends at whichever comes first: the NEXT independent signal for that
  symbol, or a 365-day cap. A flat 1-year window is wrong when a second signal
  can fire inside that year — the price action after that point belongs to
  the second (independent) trade, not the first. This was a real bug in an
  earlier version of this study (caught 2026-08-21) and is now fixed in
  `compute_symbol()`.

## Headline result: the plain crossover has no edge

| | n (1yr) | win% | mean | median |
|---|---|---|---|---|
| Plain crossover, deduped | 5,001 | 68.5% | +24.1% | +14.9% |
| Baseline (any random day, same 50 stocks) | 217,312 | 69.1% | +26.1% | +15.5% |

Essentially identical — the crossover alone is too common (~once every 5
weeks/stock) to be selective. **The edge only shows up once you condition on
the level of the weekly RSI / ADX at the moment of the cross.**

## RSI-level segmentation (1yr fwd return)

| Weekly RSI at cross | n | win% | mean | median |
|---|---|---|---|---|
| 0-20 | 6 | 83.3% | +142.7% | +152.8% (too small a sample to trust) |
| **20-30** | **154** | **76.6%** | **+52.6%** | **+25.3%** |
| 30-40 | 755 | 67.2% | +27.9% | +14.9% |
| 40-50 | 1518 | 67.1% | +20.3% | +13.4% |
| 50-60 | 1443 | 69.7% | +22.3% | +14.2% |
| 60+ | 1139 | 69.0% | +24.3% | +16.3% |

20-30 is the one bucket that clears baseline meaningfully on both win-rate and
magnitude (nearly 2x baseline mean). Everything else is baseline-level.

## ADX-level segmentation

Daily ADX at the cross barely differentiates outcomes (all buckets land near
baseline). **Weekly ADX <15** is a different, complementary finding: win rate
74.3% (vs 69.1% baseline) with much LOWER dispersion (std 37.6% vs baseline
~59.5%) — not bigger winners, but more *consistent* ones. Checked for overlap
with the weekly-RSI-20-30 group: **zero events satisfy both conditions in this
batch** — a stock whose weekly RSI has dropped under 30 has usually been in a
real decline (elevated ADX), so "deeply oversold" and "no trend at all" are
close to mutually exclusive. Treat these as two separate hypotheses, not a
combinable filter.

## Drawdown / stop-loss reality check

Even the median event-bounded trade draws down ~14% from entry before working
out; 25% of trades draw down 27%+ at some point. Tested stop-loss levels
directly: **no stop under ~20% survives the setup's own noise** — e.g. an 8%
stop gets hit on 64% of trades, and of those, 65% would have recovered to
profit if held. This is a slow, choppy setup by nature (buying into weakness),
not a clean breakout — a tight intraday-style stop actively fights it.

## Step 1+2: MFE percentile buckets (n=5301, this batch)

Every event bucketed by its own MFE (best favorable move reached within its
event-bounded window):

| bucket | MFE range | n | avg MAE | avg final_ret | win%(final>0) | avg weekly_rsi | avg weekly_adx |
|---|---|---|---|---|---|---|---|
| 1 | 0.0% .. 0.9% | 1061 | -11.8% | -8.0% | 4.7% | 51.3 | 28.0 |
| 2 | 0.9% .. 2.1% | 530 | -9.7% | -5.7% | 26.4% | 52.5 | 28.1 |
| 3 | 2.1% .. 3.7% | 530 | -8.9% | -4.6% | 31.5% | 51.9 | 28.0 |
| 4 | 3.7% .. 5.6% | 530 | -8.5% | -3.4% | 45.3% | 52.0 | 29.8 |
| 5 | 5.6% .. 8.2% | 530 | -6.6% | -0.5% | 58.9% | 51.9 | 30.0 |
| 6 | 8.2% .. 11.4% | 530 | -6.7% | +0.4% | 63.4% | 50.3 | 30.9 |
| 7 | 11.4% .. 15.9% | 530 | -5.2% | +4.8% | 76.2% | 48.6 | 30.8 |
| **8** | **15.9% .. 25.1%** | 530 | -4.2% | +11.5% | 86.2% | 48.6 | 32.4 |
| **9** | **25.1% .. 575%** | 530 | -3.1% | +37.8% | 94.2% | 47.5 | 35.4 |

**Top 3 buckets (7-9): 1,590 events, all 50 symbols represented.** Notably,
**weekly ADX rises steadily from bucket 1 (28.0) to bucket 9 (35.4)** — the
biggest winners tend to already have *some* established weekly trend (ADX
30+) at entry, not a flat/quiet market. This is the mirror image of the
"weekly ADX<15 = more consistent, not bigger" finding above: quiet ADX gives
reliability, elevated-but-not-extreme ADX (rising into the 30s) is where the
outsized winners cluster. Full event list saved to `top_mfe_events_batch1.csv`.

## Step 3: chart review (6 examples, top MFE buckets)

**Note on tooling**: the TradingView CDP chart integration only reliably
exposes roughly the most recent portion of a symbol's history when scrolling
back via `chart_set_visible_range` (varies by symbol/session state, not a
fixed calendar cutoff — seen anywhere from ~2.3 to ~4 years back before it
silently clamps to whatever's already buffered). Pre-2022 events (e.g. the
2009 GFC-bottom cluster, which dominates the raw top-MFE list) could NOT be
visually verified this way. Switched to picking examples from 2023 onward
instead. **Added this limitation to the `tradingview-ui-access` skill** so
future chart-verification work doesn't waste calls rediscovering it.

Picked 6 diverse 2023+ examples from the top-3 MFE buckets and pulled actual
daily charts:

1. **ETERNAL, 2023-04-03** (mfe +196%, biggest in the recent set) — clean base
   near the low, then a long, steady, low-volatility grind up in a rising EMA
   channel for the better part of a year. The textbook version of this setup.
2. **ADANIGREEN, 2026-03-20** (mfe +86%, mae -6.5%) — sharp rally off the base
   that peaked and gave back a meaningful chunk before settling; final_ret
   +50% is still strong but well short of the peak — illustrates why MFE and
   final_ret diverge and why "how much of the move do you actually capture"
   matters as much as "does the move happen."
3. **ADANIENT, 2026-04-06** (mfe +69%) — similar shape to ETERNAL: base,
   breakout, sustained grind higher along the EMA ribbon.
4. **SHRIRAMFIN, 2025-09-08** (mfe +60%) — cross-checked the actual bar
   against BigQuery (close 596.75, exact match, no split/data issue) — clean
   uptrend from ~597 to current ~1130, one of the strongest in the batch.
5. **HCLTECH, 2026-05-20** (mfe +18%, mae -11.6%) — genuine V-bottom: price
   fell into the entry, dipped a bit further (explaining the -11.6% MAE), then
   recovered sharply. Shows the setup can still work even when entry isn't
   the exact bottom.
6. **INFY, 2025-10-08** (mfe +14%, mae/final_ret both -18.3%) — **the
   cautionary case.** Entry fired mid-decline (a bounce inside an ongoing
   downtrend, not a real reversal), and the stock kept falling well below
   entry with no recovery by the window's end. Confirms the setup does NOT
   guarantee a bottom — it can catch a dead-cat bounce that resumes lower,
   which is exactly why the drawdown/stop-loss numbers above look the way
   they do.

**Qualitative takeaway**: the winning examples share a visible "base then
sustained grind" shape (not a V-spike), consistent with the long
window-length findings (median ~51 days, but the biggest winners run 150-300+
days). The one loser (INFY) fired without a visible base — RSI ticked up
during an unbroken downtrend rather than after one flattened out. That's a
plausible additional filter to test in a future pass: require some evidence
of basing (e.g. price stabilizing / ADX topping and turning down) rather than
taking every qualifying RSI cross regardless of what the trend looked like
going in.

## Open items for next pass

- Scale to the remaining ~16 batches (770 more stocks) with the same
  event-bounded MFE/MAE methodology.
- Test a "basing" pre-filter (motivated by the INFY counterexample) before
  scaling further.
- Weekly-RSI-20-30 and Weekly-ADX<15 are confirmed non-overlapping — worth
  tracking as two separate signal flavors across the full universe rather
  than trying to combine them.
