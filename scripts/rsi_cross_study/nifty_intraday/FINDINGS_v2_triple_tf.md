# NIFTY 3-TF gated crossover — findings v2 (2026-08-21)

Per Rajat's refined request: fast-TF crosses mid-TF, gated by slow-TF STATE
(rsi2 > 50, long-only), tested as both swing holds (days) and intraday-close
holds (same session), across multiple TF triplets. Same rsi2 =
EMA(RSI(close,8),8) variable throughout. Core: `triple_tf_core.py`.

## Track A (swing hold, event-bounded MFE/MAE to next-signal-or-365d)

| Combo | n signals | win%@365d | mean | vs baseline win%/mean |
|---|---|---|---|---|
| 15m crosses 1h, gated by D>50 | 153 | 79.4% | +11.0% | 78.6% / +11.7% (~same) |
| 1h crosses D, gated by W>50 | 193 | 78.4% | +9.9% | 79.7% / +10.4% (~same) |

**No edge over baseline** in either combo — matches every other pairwise
crossover tested so far (stocks, NIFTY 240m/Daily). Adding the 3rd-TF
bullish-state gate did not create an edge the plain 2-TF cross lacked.

## Track B (intraday close, same-session exit only)

| Combo | n signals | ~signals/day | win% (final_ret) | mean |
|---|---|---|---|---|
| 1m crosses 5m, gated by 15m>50 | 11,458 | 4.97 | 45.5% | -0.018% |
| 15m crosses 1h, gated by D>50 | 902 | 0.41 | 46.8% | -0.031% |

**Below coin-flip in both** — worse than the plain 2-TF version tested
earlier (5m/15m, 49.3-49.5%). Adding the bullish-state gate did not help;
if anything the 3rd condition made it slightly worse. Consistent with the
standing S4 verdict: alignment/crossing is a lagging confirmation.

## The MFE-percentile-bucket texture appears in EVERY combo (important caveat)

All 4 combos show the same shape when bucketed by MFE: bottom bucket ~0-12%
win rate, top bucket 84-97% win rate. This recurs so reliably across totally
different signals/timeframes/holding-periods that **it is likely partly
tautological** — trades that happened to move favorably will, by
definition, look good in a bucket built from that same favorable move. This
is NOT automatically a tradeable ex-ante filter.

**Checked whether it converts to one:** segmented the two swing combos by
`fast_adx` at entry (the one indicator value known BEFORE the outcome) —
no clean separation:

| fast_adx at entry | 15m-1h-D win%/mean | 1h-D-W win%/mean |
|---|---|---|
| <20 | 70.0% / +12.7% | 66.7% / +1.7% |
| 20-25 | 88.9% / +7.6% | 87.0% / +9.6% |
| 25-30 | 77.8% / +7.3% | 75.6% / +9.1% |
| 30+ | 80.0% / +12.2% | 78.3% / +10.7% |

No monotonic pattern, small n per bucket (6-106). Entry-time ADX does not
predict which bucket a trade will land in.

## Honest conclusion, before any SL/Target work

**None of the 4 combos tested show a genuine directional edge at entry
time** — swing versions match baseline, intraday-close versions are
slightly worse than a coin flip. The MFE-bucket "great trades" are real in
hindsight but not yet traceable to anything observable at entry, which
means deriving a stop-loss/target from this data right now would be fitting
noise, not signal — a SL/Target pair only means something once there's a
population of trades with a real edge to protect.

**Not yet done, needed before any SL/Target recommendation:**
- Try the U-shape-style segmentation that worked for stocks (extremes of
  slow-TF RSI, not just >50/<50 binary) instead of the current binary
  bull-gate — the stock study's edge lived specifically at RSI extremes,
  not "above 50."
- Try segmenting by the FAST-mid RSI gap size at the cross (how far fast
  overshot mid) rather than just ADX.
- Consider that NIFTY (index) may simply behave differently from individual
  stocks here — the stock study's edge came from stock-specific
  weakness/base patterns; NIFTY's own price structure is index-level and
  may not carry the same texture.

Recommend holding off on SL/Target derivation until one of these produces a
real ex-ante-identifiable subset, rather than backing into stop/target
numbers from a population that doesn't show an edge yet.
