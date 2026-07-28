#!/usr/bin/env node
/**
 * Daily swing screen for MTF long positions (1-3 week holds).
 *
 * Two stages, because the watchlists hold ~3,400 symbols and loading each one
 * onto the chart costs ~2.5s:
 *
 *   Stage 1  scanner API — current EMA values only, no history, so it filters
 *            on trend alignment and liquidity. Cuts thousands to dozens.
 *   Stage 2  CDP chart loads — real daily bar history, the only source for the
 *            squeeze percentile and expansion ratio the buckets depend on.
 *
 * Long-only by design: MTF cannot short.
 *
 * Usage:
 *   node scripts/swing-screen.mjs
 *   node scripts/swing-screen.mjs --lists A,B --deep 60 --top 8
 *   node scripts/swing-screen.mjs --json scripts/swing-latest.json
 */

import { writeFileSync } from 'node:fs';
import * as tv from './lib/tv.mjs';

const LISTS = tv.arg('lists', 'A,B,C,D').split(',').map((s) => s.trim()).filter(Boolean);
const DEEP = Number(tv.arg('deep', 40));
const TOP = Number(tv.arg('top', 5));
const JSON_OUT = tv.arg('json', null);

const MIN_PRICE = 50;
const MIN_TURNOVER = 5e7; // ₹5 crore average daily traded value

const SQUEEZE_PCTL = 35;
const EXPANSION_MIN = 1.15;
const EXTENDED_PCTL = 85;

const COLS = [
  'close', 'EMA20', 'EMA50', 'EMA100', 'EMA200',
  'EMA20|1W', 'average_volume_10d_calc', 'ATR', 'change',
];

function stage1Filter(rows) {
  const out = [];
  for (const r of rows) {
    const { close: c, EMA20: e20, EMA50: e50, EMA100: e100, EMA200: e200 } = r;
    const w20 = r['EMA20|1W'];
    if (![c, e20, e50, e100].every(Number.isFinite)) continue;
    if (c < MIN_PRICE) continue;

    const turnover = c * (r.average_volume_10d_calc || 0);
    if (turnover < MIN_TURNOVER) continue;

    // Daily bull stack. EMA200 only enforced when there is enough history.
    if (!(e20 > e50 && e50 > e100)) continue;
    if (Number.isFinite(e200) && !(e100 > e200)) continue;
    if (!(c > e20)) continue;

    // Weekly context.
    if (Number.isFinite(w20) && !(c > w20)) continue;

    out.push({
      sym: r.sym,
      close: c,
      atr: r.ATR,
      turnoverCr: turnover / 1e7,
      // Tightness proxy; stage 2 measures real squeeze depth from history.
      spreadPct: ((e20 - e100) / c) * 100,
    });
  }
  return out.sort((a, b) => a.spreadPct - b.spreadPct);
}

function bucketOf(a) {
  if (!a || a.skip) return 'NO DATA';
  if (!a.bull || !a.aboveE20) return 'DEAD';
  if (a.nowPctl >= EXTENDED_PCTL) return 'EXTENDED';
  if (a.squeezePctl <= SQUEEZE_PCTL && a.expansion >= EXPANSION_MIN && a.mom > 0) return 'FIRING';
  if (a.squeezePctl <= SQUEEZE_PCTL) return 'COILED';
  return 'DEAD';
}

/** Rewards a deep squeeze that is expanding but has not yet run. */
const scoreOf = (a) =>
  Math.round(Math.min(40, (a.expansion - 1) * 100) +
             Math.max(0, SQUEEZE_PCTL - a.squeezePctl) +
             Math.max(0, (EXTENDED_PCTL - a.nowPctl) / 2));

function report(rows) {
  const by = (b) => rows.filter((r) => r.bucket === b);
  const firing = by('FIRING').sort((a, b) => b.score - a.score);
  const coiled = by('COILED').sort((a, b) => a.squeezePctl - b.squeezePctl);

  console.log(`\n  FIRING ${firing.length}   COILED ${coiled.length}   EXTENDED ${by('EXTENDED').length}   DEAD ${by('DEAD').length}\n`);

  if (!firing.length) {
    console.log('  Nothing firing today. That is a valid result — do not force a trade.\n');
  } else {
    console.log(`  TOP ${Math.min(TOP, firing.length)} — entry candidates\n`);
    console.log('  ' + 'symbol'.padEnd(20) + 'price'.padStart(10) + 'exp'.padStart(7) +
                'sqz'.padStart(6) + 'now'.padStart(6) + 'stop'.padStart(10) +
                'target'.padStart(10) + '  ₹cr/day');
    for (const r of firing.slice(0, TOP)) {
      const stop = r.atr ? r.price - 1.5 * r.atr : null;
      console.log(
        '  ' + r.sym.padEnd(20) +
        r.price.toFixed(2).padStart(10) +
        (r.expansion.toFixed(2) + 'x').padStart(7) +
        Math.round(r.squeezePctl).toString().padStart(6) +
        Math.round(r.nowPctl).toString().padStart(6) +
        (stop ? stop.toFixed(2) : '-').padStart(10) +
        (r.price * 1.1).toFixed(2).padStart(10) +
        '  ' + r.turnoverCr.toFixed(1)
      );
    }
  }

  if (coiled.length) {
    console.log(`\n  WATCH — coiled, not yet expanding (tomorrow's candidates)`);
    console.log('  ' + coiled.slice(0, 10).map((r) => r.sym.split(':').pop()).join(', '));
  }
  console.log('');
}

(async () => {
  const t0 = Date.now();
  try {
    await tv.connect();

    const { symbols, breakdown } = await tv.fetchWatchlists(LISTS);
    console.log(`\n  Lists: ${breakdown}  →  ${symbols.length} unique symbols`);

    const scanned = await tv.scanAll(symbols, COLS, (n, t) =>
      process.stdout.write(`\r  stage 1: ${n}/${t}   `));
    process.stdout.write('\n');

    const survivors = stage1Filter(scanned);
    console.log(`  stage 1: ${survivors.length} passed trend + liquidity filter`);

    const candidates = survivors.slice(0, DEEP);
    console.log(`  stage 2: reading daily history for the ${candidates.length} tightest\n`);

    const analyzed = await tv.analyzeSeries(candidates, '1D', 'stage 2');
    const results = analyzed.map((r) => {
      const bucket = bucketOf(r);
      return { ...r, bucket, score: bucket === 'FIRING' ? scoreOf(r) : 0 };
    });

    report(results);

    if (JSON_OUT) {
      writeFileSync(JSON_OUT, JSON.stringify(results, null, 2));
      console.log(`  wrote ${JSON_OUT}\n`);
    }

    await tv.restoreChart();
    console.log(`  done in ${Math.round((Date.now() - t0) / 1000)}s\n`);
  } catch (err) {
    console.error('\n  error:', err.message, '\n');
    process.exitCode = 1;
  } finally {
    await tv.disconnect();
  }
})();
