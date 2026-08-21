---
name: tradingview-ui-access
description: Reliable techniques for pulling data out of the TradingView Desktop UI via CDP when the standard MCP tools aren't enough — especially getting accurate per-timeframe indicator values for custom Pine scripts. Use before looping chart_set_symbol across many symbols, and before trusting data_get_study_values on any script with duplicate plot titles.
---

# TradingView UI Access — Lessons Learned

This skill exists so future sessions don't re-derive things the hard way. Read it fully
before starting a multi-symbol scan or before debugging "why is this indicator value wrong."

## The core problem: `data_get_study_values` can lie

`data_get_study_values` reads TradingView's **Data Window**, which TradingView builds as a
single object keyed by **plot title**. If a script has multiple `plot()` calls that share
the exact same title string (a copy-paste bug, common in scripts that loop over timeframes
like "Bees to Elephant" style MTF indicators), the Data Window only shows ONE value for that
title — silently dropping the others. This is invisible unless you go looking for it.

**Symptom:** an indicator that's supposed to show 8 timeframes only exposes 2-3 distinct
keys in `data_get_study_values`, and one of those keys is actually showing the wrong
timeframe's value (typically whichever plot() call executes *last* in source order wins).

**Do not trust `data_get_study_values` at face value for any script you haven't verified.**
Check the Pine source for duplicate plot titles first (`pine_get_source` + grep for repeated
string literals in `plot(..., "title", ...)` calls).

## The fix: Table View → Download Data

TradingView's chart export (right-click chart → **Table view** → **Download data** →
**Download**) reads from a *different* code path than the Data Window. It exports one
column per underlying plot series, correctly disambiguated by timeframe/checkbox label —
**even when the plot() title strings collide**. This is the reliable way to get true
per-timeframe values for a buggy script without touching the Pine source.

### UI automation steps (coordinates are stable across symbol switches)

1. Make sure the target indicators are visible on the chart (`chart_get_state` to confirm).
2. Right-click the price pane: `ui_mouse_click` with `button: "right"` at roughly the pane's
   center (e.g. `x:200, y:150` on a standard layout).
3. Screenshot (`capture_screenshot region:full`) to find "Table view" in the context menu —
   position shifts slightly with layout, so locate it visually rather than hardcoding blindly
   the first time. Click it.
4. You're now in Table View. **Changing symbol via `chart_set_symbol` while already in Table
   View works and auto-refreshes the table** — you do NOT need to right-click again per
   symbol. This is the key speedup for scanning many symbols.
5. Click "Download data" link (top-left of the table, e.g. `x:175, y:136`).
6. A confirmation dialog appears ("Download chart data"). Click its "Download" button
   (e.g. `x:1112, y:653`).
7. The CSV lands in the OS Downloads folder as `NSE_<SYMBOL>, <resolution>_<hash>.csv`.
   Find it with `ls -t` (most-recently-modified), not by exact name guessing.

### Reading the CSV — critical gotchas

- **Rows are chronological ascending** (oldest first). The most recent bar is the **last
  line of the file**, not the second line (first data row). Use `tail -1`, not `NR==2`.
- **Column headers can repeat** (e.g. "W" appears twice in MTF Spread-Exhaustion: once for
  the real weekly value, once for a mislabeled `rangeV` histogram plot). When mapping header
  name → column index, **keep the first occurrence**, not the last — a naive
  `{h: i for i, h in enumerate(header)}` dict comprehension keeps the *last* occurrence and
  will silently grab the wrong column.
- Always parse with `csv.reader` (or equivalent) rather than naive `split(",")` — some
  Multi-Mode Indicator columns can be empty/na and column count must stay aligned.
- Delete the downloaded CSV after reading it (`os.remove`) to avoid cluttering the user's
  Downloads folder across a multi-symbol scan.

See `references/extract_row.py` for a working extraction script (Python, Windows path-aware)
that implements all of the above and prints a tab-separated summary row per symbol.

## Known indicator-specific bugs (status as of last check)

- **MTF Spread-Exhaustion (directional percentile):** no known bugs. All timeframes
  (5m/15m/30m/60m/120m/240m/D/W) export correctly via Table View. Fully reliable.
- **MTF RSI Bees to Elephant / MTF adx Bees to Elephant: FIXED.** These two scripts
  previously had duplicate plot titles (most non-5m/60m timeframes were all literally
  titled "RSI 15m" / "adx 15m" in source), which caused 5m, 15m, and W to export blank/NA
  regardless of chart resolution. **This has been corrected in the live scripts** — do not
  assume the bug is still present. Still, re-verify all 8 timeframes on one symbol the first
  time you touch these indicators in a new session, since script edits can regress this.

If a similarly-structured MTF script (looping over timeframes with copy-pasted plot titles)
shows the same "some timeframes always blank" symptom in the future, the diagnosis process
above (check for duplicate `plot()` title strings, cross-check via Table View export) still
applies — this class of bug is easy to reintroduce when a script gets copied/extended.

## Efficient multi-symbol scan loop

Once already in Table View:
```
for symbol in symbols:
    chart_set_symbol(symbol)              # table auto-refreshes, no re-navigation needed
    ui_mouse_click(x=175, y=136)          # "Download data"
    ui_mouse_click(x=1112, y=653)         # confirm "Download"
    bash: run extract_row.py <symbol>     # finds newest matching CSV, prints + deletes it
```
This is 3 tool calls + 1 bash call per symbol — no screenshot needed per iteration once the
flow is validated (spot-check every ~10 symbols instead of every one, to catch drift without
burning tokens on redundant screenshots).

`chart_set_symbol` sometimes returns `chart_ready: false` — in practice this hasn't caused
stale reads in testing, but if a scan's numbers look implausible for a given symbol, screenshot
and re-check before trusting the row.

## When to prefer `data_get_study_values` anyway

For scripts with no duplicate-title issue (verify by grepping the source once), the normal
MCP tools are faster and don't require the Table View dance. Reserve this workflow for: (a)
scripts confirmed to have title collisions, or (b) whenever a value read via
`data_get_study_values` seems suspicious and needs cross-checking against ground truth.

## Viewing an indicator's source code via the UI

`pine_get_source` only works on the script currently open in the Pine Editor, and
`pine_open` requires knowing the exact saved script name. To inspect the source of an
indicator that's already sitting on the chart (faster when you don't know/remember its
exact name, or it's a built-in/community script):

1. **Left-click** directly on the indicator's line/plot in the pane to select it (this
   highlights that specific study, not just the pane).
2. **Right-click** the same spot — the context menu now includes a **Source code** option
   (only appears when a study is selected, not on a plain chart right-click).
3. Click it to open that indicator's source in the Pine Editor, from which
   `pine_get_source` / `pine_get_errors` etc. work normally.

## `chart_set_visible_range` can't reliably reach old history

`chart_scroll_to_date` has thrown a hard `"evaluate is not defined"` error every time it's
been tried (not transient — retried multiple times, same error). Use `chart_set_visible_range`
instead, but know its limit: **it only reliably lands on the requested range if that range is
within roughly the last ~2-4 years** (observed clamp floors varying from ~2.3 to ~4 years back
across different symbols/session state — not a fixed calendar cutoff, and not simply a
1000-bar-back rule either, since two symbols requested moments apart clamped to different
floors). Request a range further back than that and the call still returns `success: true`,
but the `actual` from/to in the response silently differs from what you asked for, floored to
whatever's already buffered for that symbol in this session — easy to miss if you don't check
`actual` against `requested`.

**Practical fix:** always compare `actual` vs `requested` in the response before trusting a
screenshot taken after it. If `actual.from` is later than requested, don't retry the same call
more than once or twice (it doesn't reliably self-correct) — either accept the clamped window,
or pick a more recent occurrence of whatever you're trying to look at (e.g. when chart-verifying
historical backtest events, prefer 2022+ examples over older ones if the point is just to see
what the setup looks like). Confirmed while chart-verifying `rsi_cross_study` backtest events
2026-08-21 — pre-2022 events (e.g. the 2009 GFC-bottom cluster) could not be visually reached
this way at all.

## Named watchlists — use these instead of typing symbol lists from memory

The account has pre-built watchlists for exactly this kind of scan. **Always check
`watchlist_get` for a relevant list before hand-typing/guessing a symbol universe** (e.g.
reconstructing "NIFTY 50 constituents" from memory is slower and error-prone — the current
constituent list already exists as a watchlist).

| Watchlist | Contents |
|---|---|
| `Nifty` | All NIFTY 50 stocks + a few key extras |
| `FnO` | All NSE F&O-eligible stocks |
| `A` | Top 1000 stocks by market cap |
| `B` | Next 1000 by market cap (1001–2000) |
| `C` | Next 1000 (2001–3000) |
| `D` | Next 1000 (3001–4000) |

For a "scan NIFTY 50" type request: `watchlist_get("Nifty")` first, feed that symbol list
into the scan loop above — don't reconstruct the index from memory.

## Named layouts — switch to the one built for the task

`layout_list` / `layout_switch` can jump straight to a purpose-built layout instead of
manually adding/arranging panes:

| Layout | Purpose |
|---|---|
| `Two Timeframes` | The main intraday-trading layout (dual timeframe panes side by side) |
| `Swing Trade` | Chart analysis layout set up for swing-trade review |

Switch layout first (`layout_switch`) when the task matches one of these, rather than
manually replicating the pane/indicator setup on whatever layout happens to be active.
