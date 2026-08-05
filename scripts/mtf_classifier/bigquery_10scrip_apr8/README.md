# BigQuery 10-scrip setup search, cutoff 2026-04-08

Direct BigQuery access via service-account JSON (`Downloads/Daily Trade Files/rajat-trade-c411eaec7c51.json`),
NOT the MCP connector (was invalidated) and NOT Colab (unnecessary once the JSON key was found).

Universe: 10 scrips ranked by average 15m traded volume from the intraday-active universe
(days>420 out of 439 possible since 2024-07-01), excluding NIFTY, as of cutoff:
IDEA, YESBANK, SUZLON, JPPOWER, ETERNAL, RPOWER, EASEMYTRIP, TATASTEEL, IRB, ADANIPOWER.
Note: volume-ranked skews to low-priced high-turnover names, not blue-chips.

Timeframes: 15m/30m/60m/120m/240m (session-anchored from `stock_intraday_prices_dhan`,
15m data only starts 2024-07-01) + D/W from `stock_daily_prices_dhan` (full history back to
2003 for these names -- daily/weekly setups have vastly more statistical power than
intraday-derived ones as a result).

No-lookahead discipline: Q2 fetched OHLC only through the cutoff date (no forward data at
fetch time, per instruction). Forward-return mining used only rows whose 7-calendar-day
target date is <= cutoff (checked: max date with a known outcome = 2026-04-01, 7 days
before cutoff, zero leakage).

setup_search_results.csv: full systematic search (40 setup definitions x trend-following vs
mean-reversion x TF x threshold), market-neutral (same-day cross-sectional mean subtracted),
direction-adjusted, clustered-by-date significance.

Key finding: every deep-history (2003-2026) SHORT setup on D/W trend-following or
overbought-fade is mis-signed (p<.05, xs%<0) -- shorting daily/weekly downtrend extremes on
these 10 names has lost money for 20 years. Only 3 shallow-window (2024-26) short setups
survive correctly-signed. Longs dominate throughout.

bq_mtf_setups_2026-04-08.csv: the winning (p<.05, correctly-signed) setups applied to the
2026-04-08 cutoff data -- per-scrip Long/Short/NoCall/Mixed call with which specific setups
fired and a conviction score (sum of each fired setup's historical xs%).
