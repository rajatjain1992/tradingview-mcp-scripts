# Session summary — 2026-08-20 / 2026-08-21

Three connected pieces of work in one session: recovering a wiped BigQuery table,
porting 4 of Rajat's live Pine indicators to Python (validated against TradingView),
and building/iterating a multi-timeframe "similar days" matcher for NIFTY.

## 1. BigQuery data recovery

`rajat-trade.stock_data_set.stock_intraday_prices_dhan` was accidentally wiped
(table-wide, all scrips/intervals — likely `run_daily_reload(mode="lifetime")`
combined with the known Dhan rate-limiter bug, DH-904, killing the refetch
mid-run). Recovered via BigQuery time travel:

```sql
CREATE OR REPLACE TABLE `rajat-trade.stock_data_set.stock_intraday_prices_dhan` AS
SELECT * FROM `rajat-trade.stock_data_set.stock_intraday_prices_dhan`
FOR SYSTEM_TIME AS OF TIMESTAMP("2026-08-19 12:00:00 UTC")
```

Confirmed clean afterward (no duplicates, 13.4M rows table-wide). The local
canonical 1-min NIFTY file (`Downloads/Daily Trade Files/NIFTY 2020-2026 Data.csv`)
was re-synced from the restored table with proper RTH filtering (09:15–15:29 IST) —
see [`fetch_nifty_daily_and_1min.py`](fetch_nifty_daily_and_1min.py). Range extended
from 2019-12-30 back to **2017-04-03** in the process (BigQuery had deeper history
than the old local file).

**Lesson banked:** `stock_intraday_prices_dhan` is a rolling/live table with almost
no retained history day-to-day — don't rely on it as a backfill source; the
`NIFTY 2020-2026 Data.csv` + `MTF_V4/nifty_1m_clean.csv` pair is the durable local
copy.

## 2. Pine → Python indicator ports

All 5 of the MTF indicator scripts currently on Rajat's chart, fetched via
`pine_open`+`pine_get_source` (not trusted from git — `mtf_spread_exhaustion.pine`
was stale, git had v45, live TradingView was v83 with a different "directional
percentile" formula; fixed by re-syncing from the live source):

| Script | Status |
|---|---|
| `mtf_spread_exhaustion.pine` (v83) | ported → [`mtf_indicators.py`](mtf_indicators.py) |
| `mtf_adx_indicator.pine` (+ ADX Bowl/Elephant/Expansion detector) | ported → same file |
| `mtf_rsi_indicator.pine` | ported → same file |
| `multi_mode_indicator.pine` (524 lines) | ported → [`multi_mode_calc.py`](multi_mode_calc.py) |
| `mtf_obv_oscillator.pine` | **not ported** — no usable NIFTY volume feed (see below) |

**Validation method:** `data_get_study_values` (the MCP Data Window reader) gives
silently wrong numbers on this chart — multiple scripts share plot titles ("5m",
"15m", ... across 3 different indicators), so the API returns whichever plot last
wrote that title, not necessarily the one you asked for. Confirmed empirically:
`data_get_study_values` claimed "MTF Spread-Exhaustion" 5m = 31.21, but that's
actually ADX(8,8)'s value, not the spread-exhaustion signedPct. Used TradingView's
**Table View → Download Data** CSV export instead (a different code path, correctly
disambiguated per underlying plot series even when titles collide) for all ground
truth. See the `tradingview-ui-access` skill for the general technique.

**Bugs found and fixed while validating:**
- `ema()`/`rma()` seeded on a fixed index (`src[0]` / mean of first `length`
  values). Nested calls like `ema(rsi(...), 8)` or `rma(dx, adxLen)` have their own
  NaN warm-up prefix — a fixed-index seed picks up that NaN and, because these are
  IIR recursive filters, poisons the *entire* multi-year output forward. Fixed to
  match Pine's actual builtin behavior: re-seed on the first valid bar (`ema`) or
  first fully-valid rolling window (`rma`), not a fixed offset.
- `request.security` join logic: a lower-timeframe bar shows the **last closed**
  higher-timeframe bar, not whichever HTF bucket its own timestamp falls into.
  Naive `merge_asof` on raw bar-start timestamps grabbed the still-forming HTF bar
  instead, throwing 60m ADX off by 3+ points until fixed (shift each HTF series'
  join key forward by its own bar duration first).

**Result after both fixes:** ADX matches TradingView to 12+ decimal places on both
a 5m and 60m validation bar; RSI/signedPct match within normal Dhan-vs-TradingView
cross-vendor tick noise. Multi-Mode's EMAs/day-variables validated similarly
(`dayHigh`/`dayLow`/`day50`/`high_30`/`low_30` exact; VWAP within 0.18 points
despite the volume caveat below).

**Volume caveat (OBV, VWAP, absorption, ema_vol_200):** NIFTY's own volume field in
both the BigQuery/Dhan intraday table and our local 1-min file is unreliable
(median 0, occasional negative values, huge outliers) — completely different from
what TradingView's own feed shows (which is legitimate: 16.52K/4.49M MA on a live
check). OBV was not ported/run for this reason. Multi-Mode's volume-dependent
columns (VWAP, `close_vwap`, `absorption`, `ema_vol_200`) ARE computed — the
formulas are correct and will work for any stock with a real volume feed — but
won't match TradingView for NIFTY specifically.

**Output data** (driver scripts [`run_mtf_indicators.py`](run_mtf_indicators.py) /
[`run_multi_mode.py`](run_multi_mode.py), saved to
`Downloads/Daily Trade Files/MTF_V4/`):
- `mtf_indicators_intraday.csv`/`.parquet` (1m/5m/15m/30m/60m, 1.14M rows) and
  `mtf_indicators_dwm.csv`/`.parquet` (D/W/M, 2919 rows) — spread-exhaustion/RSI/ADX
  + current-TF ADX Bowl state, all 9 cross-TF series as-of-joined per row.
- `multi_mode_<tf>.csv`/`.parquet` — one file per timeframe (1m/5m/15m/30m/60m/D/W/M),
  kept separate rather than concatenated given the column count.

## 3. Multi-timeframe "similar days" matcher

Iterative build, each round driven by a specific gap Rajat caught — see
[`similar_days/`](similar_days/) for the full script sequence and inline
docstrings explaining each version's methodology and what it superseded.

**Version history (scripts numbered in run order):**
1. `01_daily_similarity_3methods.py` — Daily-only, 3 methods (z-score NN, fuzzy
   band, price-shape). First cut; caught out for only using Daily.
2. `02_mtf_similarity_85feat.py` — expanded to all 9 timeframes with 1-bar/3-bar
   trend context per Rajat's "last 3-5 candles" instruction. Found and fixed a
   real bug: Monthly's `signedPct` can never mature (only 113 monthly bars vs a
   200-bar `percentrank` requirement), silently NaN-ing the whole candidate pool.
3. `03_confidence_ranking.py` — lists matched dates with a similarity percentile
   ("confidence %") — explicitly **not** an outcome probability.
4. `04_explain_match_2025-05-12.py` — per-feature distance decomposition, the
   template for sanity-checking any "top match" rather than trusting rank alone.
5. `05_mtf_similarity_97feat_v2.py` — added EMA-position (6 features) and
   price-structure (5 features) after Rajat asked "did u not match EMA position or
   price structure" — a fair catch, the first version was purely oscillator-based.
6. `06_explain_match_2026-08-03_dimensionality_check.py` — **the cautionary
   tale.** Rajat visually compared the new #1 match (2026-08-03, "99.93%
   confidence") against the live chart and correctly called it wrong. Decomposing
   the match proved it: individual RSI readings were near-opposite (60m RSI 24
   today vs 75 that day), it only ranked #1 by summing to the least-bad total
   across 97 dimensions — a real curse-of-dimensionality artifact, not a genuine
   match. **Lesson: more features isn't better; always decompose before trusting
   a rank, and a 99%+ percentile "confidence" measures relative rank in the pool,
   not match quality or outcome probability.**
7. `07_spread_band_match.py` — a different, simpler approach: hard threshold
   bounds on 5m/15m/30m/60m signedPct (Rajat's own bounds, read off the live
   chart) instead of distance-based ranking. Found 21 independent historical
   onsets since 2018; the pattern was same-day continuation (67% up to EOD) that
   historically fades by the next session (35% up next-day close) — an
   intraday-bounce-then-reversal shape, not a clean directional signal.

**Overall conclusion reached:** as of 2026-08-20 close, NIFTY's daily EMA
fan-spread sat at the 0.2nd percentile of the last 500 days (extreme compression),
with weak ADX(14)=12.8 and no active ADX-Bowl trigger — a genuinely quiet,
compressed *state*, confirmed directly from the data. But forward-return evidence
across every method run did NOT support "more consolidation ahead" — historical
analogs to this compression showed large-magnitude moves in both directions (std
devs of 140-546 points vs means of tens of points), consistent with the standard
reading that extreme compression precedes a resolution/breakout rather than
persisting. Declined to call a direction; the data doesn't support one.

## Caveats that apply to all of this

- Small sample sizes throughout (typically 20-25 matched days) — noise dominates
  signal in every method's std-dev vs mean.
- This is empirical pattern-matching against ~9 years of one index's history, not
  a causal model. Treat as context alongside price/levels reading, not a
  standalone signal.
- The volume-dependent limitation (OBV, VWAP, absorption) is specific to NIFTY the
  index; the same ported code should work correctly for individual stocks with
  real Dhan volume data.
