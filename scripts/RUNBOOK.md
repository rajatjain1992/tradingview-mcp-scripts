# Screener runbook

Three screens, two systems, two different clocks. TradingView Desktop must be
running with CDP on port 9223.

| Script | System | Signal TF | When to run |
|---|---|---|---|
| `swing-screen.mjs` | MTF swing, 1–3 wk, ~10% | Daily | After the close, ~16:00 |
| `fno-screen.mjs` | F&O short-term, ₹10k/trade | 15m | Pre-market, then during windows |
| `setup-screen.mjs` | Setups A/B/B+/C census | Daily + 15m | Weekly, for regime context |

Shared plumbing lives in `lib/tv.mjs`. Two data paths: the scanner API for
breadth (thousands of symbols/second, current values only) and CDP chart loads
for depth (real bar history, ~2.5s each). Anything needing history — squeeze
percentile, expansion ratio, 3-bar momentum — must come from the chart, which is
why every screen is a funnel.

---

## 1. Swing screen

```bash
node scripts/swing-screen.mjs --lists A,B,C,D --json scripts/swing-latest.json
```

~50s over 3,363 symbols. Long-only, because MTF cannot short.

**Run after the close, never during market hours.** The point of a daily clock
is that decisions get made when nothing is moving. Orders go in next morning as
pre-planned limits. Sunday: re-run, review positions, prune.

### Buckets

| Bucket | Meaning | Action |
|---|---|---|
| FIRING | Deep squeeze, expanding, not yet extended | **Entry candidates** |
| COILED | Tight but not expanding yet | Tomorrow's pipeline |
| EXTENDED | Spread percentile ≥85, move spent | Skip. Do not chase |
| DEAD | Trend broken or below EMA20 | Ignore |

`exp` = spread now vs 10 bars ago. `sqz` = how deep the recent squeeze was, as a
percentile of the last 120 bars (lower is tighter). `now` = current spread
percentile (lower means more room left).

**Want a low `sqz` with a low-to-mid `now`.** A fired signal with `now` near 100
has already moved without you — that is what EXTENDED catches.

### Position rules
- Hard cap 5 candidates; realistically 1–3 open on current capital.
- Stop is 1.5 × daily ATR below entry. Target +10%. Time stop 3 weeks.
- Under ~₹10 crore/day turnover, size down — exits get expensive.

---

## 2. F&O screen

```bash
node scripts/fno-screen.mjs --top 3
```

~15s over 213 symbols. Runs both directions — F&O can short, which is where the
validated edge actually lives.

A 15m signal only counts when the 60m and daily agree; conflicted context is
dropped before anything else is measured. Liquidity floor is ₹20 crore/day,
far higher than swing, because intraday exits are unforgiving.

**Pre-market:** build the day's shortlist, max 3 names. During the session you
watch only those. Do not re-scan looking for something better — that is how the
10-trade limit dies.

Entry timing on the 5m. AVOID slots still apply: 10:45–11:00, 13:00–13:30,
14:15–14:45.

---

## 3. Setup census

```bash
node scripts/setup-screen.mjs --deep 40
```

~90s. Counts hits for the four researched setups across both universes.

- **A** — thrust ≥3.5 × ATR, closing in the top/bottom 30% of range, with context agreeing.
- **B** — context bearish, price under EMA20, EMA spread expanding, 3-bar momentum down.
- **B+** — B plus a confirming bear thrust ≥2.5 × ATR.
- **C** — context bearish, RSI <35, price >2 × context-ATR below the context EMA20. Watch only.

A and C compute from current values, so their counts cover the whole universe.
B and B+ need history, so they are scanner-prefiltered then confirmed on `--deep`
chart loads — reported as "confirmed of checked", never extrapolated.

**This is a regime read, not a signal list.** A/B/B+/C were validated
out-of-sample on NIFTY *index* 1-minute data for intraday option buying. Nothing
here is validated on equities or daily bars. B and B+ are shorts, so on the swing
lists they are informational only — MTF cannot short them.

---

## Two gotchas worth remembering

**The watchlist panel is virtualized.** Scraping the DOM returns only the
rendered viewport — about 38 rows of a 940-symbol list. Always read watchlists
from `/api/v1/symbols_list/custom/` inside the page.

**`chart.symbol()` updates before the bars do.** It reports the new symbol while
the previous symbol's series is still loaded, which silently attributes one
symbol's bars to another. Gate on `mainSeries().symbolInfo().full_name` instead;
`lib/tv.mjs` does this and also re-checks after analysis.

---

## Empty results are a result

Some days nothing fires. That is the screen working. The cost of skipping a day
is zero; the cost of forcing a trade is not.
