#!/usr/bin/env node
/**
 * Counts hits for the four researched setups (A, B, B+, C) across both
 * universes: the swing lists on daily bars, and the F&O list on 15m bars.
 *
 * IMPORTANT — these setups were validated out-of-sample on NIFTY *index*
 * 1-minute data, for intraday option buying. Nothing here is validated on
 * equities or on daily bars. Treat the counts as a regime read, not as signals
 * with a known edge. See the §7.0 meta-rule: no OOS validation, no trust.
 *
 * Timeframe mapping keeps the original ~1:4 signal-to-context ratio:
 *   swing  signal = daily,  context = weekly EMA20 + daily EMA50
 *   fno    signal = 15m,    context = 60m EMA20 + daily EMA20
 *
 * Setups A and C are fully computable from current values, so their counts
 * cover the entire universe. B and B+ need EMA-spread history and a 3-bar
 * momentum check, so they are scanner-prefiltered and then confirmed on a
 * capped sample of chart loads — their counts are reported as "confirmed of
 * checked", not extrapolated.
 *
 * Usage:
 *   node scripts/setup-screen.mjs
 *   node scripts/setup-screen.mjs --universe swing --deep 60
 *   node scripts/setup-screen.mjs --universe fno
 */

import * as tv from './lib/tv.mjs';

const UNIVERSE = tv.arg('universe', 'both');
const DEEP = Number(tv.arg('deep', 40));
const SHOW = Number(tv.arg('show', 6));

const THRUST_ATR = 3.5;   // Setup A: bar range vs ATR(14)
const THRUST_B_ATR = 2.5; // Setup B+: lower bar for the confirming thrust
const CLOSE_ZONE = 0.70;  // close must sit in the top/bottom 30% of the range
const SNAPBACK_ATR = 2.0; // Setup C: distance below the context EMA20
const SNAPBACK_RSI = 35;

const UNIVERSES = {
  swing: {
    lists: ['A', 'B', 'C', 'D'],
    tf: '1D',
    label: 'SWING  (lists A-D, daily bars)',
    minTurnover: 5e7,
    cols: ['close', 'open', 'high', 'low', 'ATR', 'RSI', 'EMA20', 'EMA50',
           'EMA20|1W', 'ATR|1W', 'average_volume_10d_calc'],
    pick: (r) => ({
      c: r.close, hi: r.high, lo: r.low, atr: r.ATR, rsi: r.RSI,
      e20: r.EMA20, ctx1: r['EMA20|1W'], ctx2: r.EMA50,
      ctxAtr: r['ATR|1W'],
    }),
    shortable: false, // MTF is long-only; shorts need futures
  },
  fno: {
    lists: ['FnO'],
    tf: '15',
    label: 'F&O  (FnO list, 15m bars)',
    minTurnover: 2e8,
    cols: ['close', 'ATR', 'average_volume_10d_calc',
           'close|15', 'high|15', 'low|15', 'ATR|15', 'RSI|15', 'EMA20|15',
           'EMA20|60', 'ATR|60', 'EMA20'],
    pick: (r) => ({
      c: r['close|15'], hi: r['high|15'], lo: r['low|15'],
      atr: r['ATR|15'], rsi: r['RSI|15'],
      e20: r['EMA20|15'], ctx1: r['EMA20|60'], ctx2: r.EMA20,
      ctxAtr: r['ATR|60'],
    }),
    shortable: true,
  },
};

/** Evaluates setups A and C, and builds the B/B+ prefilter, from current values. */
function evaluateSetups(rows, uni) {
  const hits = { A_long: [], A_short: [], C_watch: [], B_pre: [] };

  for (const r of rows) {
    const turnover = r.close * (r.average_volume_10d_calc || 0);
    if (turnover < uni.minTurnover) continue;

    const p = uni.pick(r);
    if (![p.c, p.hi, p.lo, p.atr, p.e20, p.ctx1, p.ctx2].every(Number.isFinite)) continue;
    if (p.atr <= 0) continue;

    const range = p.hi - p.lo;
    const ctxBear = p.c < p.ctx1 && p.c < p.ctx2;
    const ctxBull = p.c > p.ctx1 && p.c > p.ctx2;
    const base = { sym: r.sym, close: p.c, atr: p.atr, rsi: p.rsi, turnoverCr: turnover / 1e7 };

    // ── Setup A: momentum thrust continuation ──
    if (range > 0) {
      const thrust = range / p.atr;
      const closePos = (p.c - p.lo) / range; // 1 = closed on the high
      if (thrust >= THRUST_ATR) {
        if (closePos >= CLOSE_ZONE && p.c > p.e20 && ctxBull) {
          hits.A_long.push({ ...base, thrust, closePos });
        }
        if (closePos <= 1 - CLOSE_ZONE && p.c < p.e20 && ctxBear) {
          hits.A_short.push({ ...base, thrust, closePos });
        }
      }
    }

    // ── Setup C: snapback watch (discretionary long) ──
    // Distance is measured against the CONTEXT timeframe's ATR. Using the
    // signal ATR here inflates the multiple badly — a 15m ATR against a 60m
    // EMA distance made almost everything clear the 2.0 threshold.
    const ctxAtr = Number.isFinite(p.ctxAtr) && p.ctxAtr > 0 ? p.ctxAtr : null;
    if (ctxBear && ctxAtr && Number.isFinite(p.rsi) &&
        p.rsi < SNAPBACK_RSI && (p.ctx1 - p.c) > SNAPBACK_ATR * ctxAtr) {
      hits.C_watch.push({ ...base, belowAtr: (p.ctx1 - p.c) / ctxAtr });
    }

    // ── Setup B prefilter: context bearish + below the signal EMA20 ──
    // The expansion and 3-bar momentum tests need history; stage 2 does those.
    if (ctxBear && p.c < p.e20) {
      const thrust = range > 0 ? range / p.atr : 0;
      const closePos = range > 0 ? (p.c - p.lo) / range : 0.5;
      hits.B_pre.push({
        ...base,
        spreadProxy: Math.abs((p.e20 - p.ctx1) / p.c) * 100,
        bearThrust: thrust >= THRUST_B_ATR && closePos <= 1 - CLOSE_ZONE,
      });
    }
  }
  return hits;
}

async function runUniverse(key) {
  const uni = UNIVERSES[key];
  console.log(`\n${'─'.repeat(72)}\n  ${uni.label}\n${'─'.repeat(72)}`);

  const { symbols, breakdown } = await tv.fetchWatchlists(uni.lists);
  console.log(`  ${breakdown}  →  ${symbols.length} symbols`);

  const scanned = await tv.scanAll(symbols, uni.cols, (n, t) =>
    process.stdout.write(`\r  scanning: ${n}/${t}   `));
  process.stdout.write('\n');

  const hits = evaluateSetups(scanned, uni);

  // Stage 2: confirm Setup B on the widest-spread prefilter names.
  const toCheck = hits.B_pre.sort((a, b) => b.spreadProxy - a.spreadProxy).slice(0, DEEP);
  let bConfirmed = [], bPlus = [];
  if (toCheck.length) {
    console.log(`  confirming Setup B on ${toCheck.length} of ${hits.B_pre.length} prefiltered\n`);
    const analyzed = await tv.analyzeSeries(toCheck, uni.tf, '  setup B');
    for (const r of analyzed) {
      if (r.skip) continue;
      // Spread expanding vs 10 bars back, price below EMA20, momentum down.
      if (r.expansion >= 1.15 && !r.aboveE20 && r.mom < 0) {
        bConfirmed.push(r);
        if (r.bearThrust) bPlus.push(r);
      }
    }
  }

  return { key, uni, hits, bConfirmed, bPlus, checked: toCheck.length };
}

function printResult({ uni, hits, bConfirmed, bPlus, checked }) {
  const note = uni.shortable ? '' : '   ← not MTF-tradeable (needs futures)';
  const rows = [
    ['Setup A  long   (thrust continuation)', hits.A_long.length, ''],
    ['Setup A  short  (thrust continuation)', hits.A_short.length, note],
    ['Setup B  short  (EMA expansion)', `${bConfirmed.length} of ${checked} checked`, note],
    ['Setup B+ short  (B + bear thrust)', `${bPlus.length} of ${checked} checked`, note],
    ['Setup C  watch  (snapback long)', hits.C_watch.length, '   discretionary only'],
  ];
  console.log('');
  for (const [name, n, extra] of rows) {
    console.log('  ' + name.padEnd(40) + String(n).padStart(16) + extra);
  }

  const show = (title, arr, fmt) => {
    if (!arr.length) return;
    console.log(`\n  ${title}`);
    for (const r of arr.slice(0, SHOW)) {
      console.log('    ' + r.sym.replace(/^(NSE|BSE):/, '').padEnd(16) +
                  r.close.toFixed(2).padStart(10) + '  ' + fmt(r));
    }
  };

  show('A long', hits.A_long.sort((a, b) => b.thrust - a.thrust),
       (r) => `${r.thrust.toFixed(1)}x ATR, closed ${(r.closePos * 100).toFixed(0)}% of range`);
  show('A short', hits.A_short.sort((a, b) => b.thrust - a.thrust),
       (r) => `${r.thrust.toFixed(1)}x ATR, closed ${(r.closePos * 100).toFixed(0)}% of range`);
  show('B short (confirmed)', bConfirmed.sort((a, b) => b.expansion - a.expansion),
       (r) => `${r.expansion.toFixed(2)}x expansion, squeeze ${Math.round(r.squeezePctl)}`);
  show('C watch', hits.C_watch.sort((a, b) => a.rsi - b.rsi),
       (r) => `RSI ${r.rsi.toFixed(0)}, ${r.belowAtr.toFixed(1)} ATR below context EMA20`);
}

(async () => {
  const t0 = Date.now();
  try {
    await tv.connect();
    const keys = UNIVERSE === 'both' ? ['swing', 'fno'] : [UNIVERSE];
    const results = [];
    for (const k of keys) results.push(await runUniverse(k));

    for (const r of results) printResult(r);

    console.log(`\n${'─'.repeat(72)}`);
    console.log('  Setups A/B/B+/C were validated on NIFTY index 1m only. These counts are');
    console.log('  a regime read on equities, not signals with a measured edge.');
    console.log(`${'─'.repeat(72)}`);

    await tv.restoreChart();
    console.log(`\n  done in ${Math.round((Date.now() - t0) / 1000)}s\n`);
  } catch (err) {
    console.error('\n  error:', err.message, '\n');
    process.exitCode = 1;
  } finally {
    await tv.disconnect();
  }
})();
