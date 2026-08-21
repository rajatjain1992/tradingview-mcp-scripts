# NIFTY intraday RSI-cross exploration — findings (2026-08-21)

Rajat asked whether the Daily/Weekly RSI-cross signal (built earlier this
session on 770 stocks) could be adapted intraday for NIFTY. Before testing,
surfaced that the core mechanism was already extensively tested on NIFTY
intraday specifically in prior research (`mtf-rsi-derived-rules` memory, "S4")
and rejected. Rajat asked for all three possible interpretations to be tested
anyway, same priority. Results below.

## Track A: intraday signal, swing hold (240m crosses Daily)

Closest analog to the already-validated "1h/4h/D bull alignment, swing
horizon" exception. 240m rsi2 crosses above Daily rsi2 (last-closed-day join,
same event-bounded MFE/MAE methodology as the stock study), held for days.

| | n | win%@365d | mean | median |
|---|---|---|---|---|
| Signal events | 131 | 85.5% | +12.1% | +9.6% |
| Baseline (every 240m bar) | 4,119 | 83.0% | +12.6% | +9.7% |

**No edge.** Same pattern as the stock study's "plain crossover" finding —
essentially identical to baseline, in some cases baseline is marginally
better. `track_a_swing_hold.py` / `track_a_events.csv`.

## Track B: intraday signal, intraday hold (5m crosses 15m, same-day exit)

Direct re-verification of "S4" on fresh, full data (2017-2026 vs the prior
study's 2020-26 window). ~2 signals/day, same-day exit only.

| Horizon | Signal win% | Baseline win% |
|---|---|---|
| +15min | 49.3% | 51.1% |
| +30min | 48.4% | 51.3% |
| +60min | 49.5% | 51.3% |
| +120min | 49.4% | 51.1% |
| to EOD | 49.4% | 50.0% |

**No edge — actually slightly WORSE than baseline at every horizon.** This
cleanly reconfirms the prior "coin flip, ~16/day, the move already happened"
verdict on current data. `track_b_intraday_hold.py` / `track_b_events.csv`.

## Track C: ADX-gated oversold bounce (60m RSI<30 by ADX regime)

Attempted re-verification of the prior finding "high ADX oversold does NOT
bounce, low/mid ADX oversold DOES" (`mtf-rsi-cycle-research` memory, finding
#4). **Did not cleanly reproduce it** — two event definitions tried (onset of
oversold, and "state" = every bar reading oversold), both against ADX
terciles: neither showed the previously-documented split (low/mid ADX
bouncing, high ADX flat). All three ADX regimes came back close to
coin-flip/near-zero mean in this quick pass.

This is most likely a methodology gap, not a contradiction of the prior
result — the original finding came from a rigorous 594k-row, 1-min-aligned
FORMING-bar panel (`panel_rsi_adx.parquet`) with careful regime
conditioning; this quick pass used bar-CLOSE-only 60m resampling with global
tercile cutpoints, a much cruder approximation. Sample sizes were also thin
after regime-splitting (n=11-101 per bucket). **Do not treat this as
overturning the prior finding** — it just means reproducing it properly needs
the original panel methodology, not a 20-minute re-implementation.
`track_c_adx_gated_oversold.py` / `track_c_events.csv`.

## Bottom line

Every mechanical "fast-TF-crosses-slow-TF" variant tested on NIFTY today —
swing-hold and intraday-hold both — showed no edge, consistent with (and now
triply confirming, alongside the stock study's "plain crossover has no edge"
finding and the prior S4 rejection) the standing conclusion in
`mtf-rsi-derived-rules`: **alignment/crossing is a lagging confirmation on
NIFTY intraday, not a leading trigger.** The ADX-gated oversold-bounce angle
remains the one candidate with real prior support, but reproducing it
properly is unfinished work, not a rejected one.
