# scripts/

Analysis, screening, and Pine tooling built around the TradingView MCP server.

## Python studies
- `ema_stretch_study.py` — EMA-stretch reversal/continuation study (percentile extremes across timeframes)
- `directional_study.py` — directional fan-spread study + backtest
- `stretch_mtf_align.py` — multi-timeframe stretch alignment
- `stretch_multi.py` — multi-instrument stretch scan
- `astral_analysis.py` — per-symbol (ASTRAL) analysis
- `fno_screen.py` — F&O screener

## Node screeners (`.mjs`)
- `swing-screen.mjs` — swing screen across watchlists (writes `swing-latest.json`)
- `setup-screen.mjs` — setup screener
- `lib/tv.mjs` — shared TradingView/CDP helper used by the `.mjs` scripts

## Pine
- `pine/mtf_spread_exhaustion.pine` — MTF Spread-Exhaustion indicator
- `pine/mtf_spread_exhaustion_lines.pine` — line-drawing variant

## Launchers / helpers
- `launch_tv_debug.*` — start TradingView Desktop with CDP (port 9222) on Win/Mac/Linux
- `pine_pull.js`, `pine_push.js` — pull/push Pine source via CDP
- `RUNBOOK.md` — operational notes

## Data
- `stretch_data/` — cached OHLC pulls (per-symbol 60m/daily JSON) used by the stretch studies
- `*_out.txt`, `*.csv`, `swing-latest.json` — generated outputs (snapshots of prior runs)

> Logs (`*.log`) and Python `__pycache__/` are gitignored.
