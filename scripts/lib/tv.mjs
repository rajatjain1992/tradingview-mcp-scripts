/**
 * Shared plumbing for the screener scripts.
 *
 * Two data paths, used for different jobs:
 *   scanner API  — thousands of symbols per second, but only current values.
 *                  Anything needing bar history is out of reach.
 *   CDP chart    — real bar history, but ~2.5s per symbol. Use it last, on a
 *                  short list the scanner has already narrowed.
 */

import CDP from 'chrome-remote-interface';

const CDP_HOST = 'localhost';
const CDP_PORT = 9223;
const SCANNER = 'https://scanner.tradingview.com/india/scan';
const BATCH = 500;

let client = null;

export async function connect() {
  const targets = await (await fetch(`http://${CDP_HOST}:${CDP_PORT}/json/list`)).json();
  const page = targets.find((t) => t.type === 'page' && /tradingview/i.test(t.url));
  if (!page) throw new Error(`No TradingView page found on CDP port ${CDP_PORT}`);
  client = await CDP({ host: CDP_HOST, port: CDP_PORT, target: page.id });
  await client.Runtime.enable();
}

export async function disconnect() {
  if (client) { await client.close(); client = null; }
}

export async function evaluate(expression) {
  const { result, exceptionDetails } = await client.Runtime.evaluate({
    expression, returnByValue: true, awaitPromise: true,
  });
  if (exceptionDetails) throw new Error(exceptionDetails.text || 'evaluate failed');
  return result.value;
}

/** Watchlists live behind the session cookie, so this has to run in the page. */
export async function fetchWatchlists(names) {
  const raw = await evaluate(`
    fetch("/api/v1/symbols_list/custom/", { credentials: "include" })
      .then(r => r.json())
      .then(j => JSON.stringify(j.map(l => ({ name: l.name, symbols: l.symbols || [] }))))
  `);
  const lists = JSON.parse(raw);
  const picked = lists.filter((l) => names.includes(l.name));
  const missing = names.filter((n) => !picked.some((l) => l.name === n));
  if (missing.length) console.warn(`  warning: no such list: ${missing.join(', ')}`);
  return {
    symbols: [...new Set(picked.flatMap((l) => l.symbols))],
    breakdown: picked.map((l) => `${l.name}:${l.symbols.length}`).join(' '),
  };
}

/** Scanner API, batched. Returns one object per symbol keyed by column name. */
export async function scanAll(tickers, columns, onProgress) {
  const out = [];
  for (let i = 0; i < tickers.length; i += BATCH) {
    const slice = tickers.slice(i, i + BATCH);
    const res = await fetch(SCANNER, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: { tickers: slice, query: { types: [] } }, columns }),
    });
    if (!res.ok) throw new Error(`scanner ${res.status}`);
    const j = await res.json();
    for (const row of j.data || []) {
      const o = { sym: row.s };
      columns.forEach((c, k) => { o[c] = row.d[k]; });
      out.push(o);
    }
    if (onProgress) onProgress(out.length, tickers.length);
  }
  return out;
}

/**
 * Installs the in-page analyzer. Computes the EMA-spread squeeze/expansion
 * figures from real bar history — the part the scanner cannot provide.
 */
export const ANALYZER = `
window.__tvAnalyze = function () {
  var series = window.TradingViewApi._chartWidgetCollection.activeChartWidget.value()
                 .model().mainSeries();
  var si = series.symbolInfo ? series.symbolInfo() : null;
  var actual = si ? String(si.full_name || si.ticker || '') : '';
  var b = series.bars();
  var n = b.size(), c = [], h = [], l = [];
  for (var i = 0; i < n; i++) {
    var v = b.valueAt(i);
    if (v && v[4] != null) { c.push(v[4]); h.push(v[2]); l.push(v[3]); }
  }
  n = c.length;
  if (n < 120) return { actual: actual, skip: 'bars:' + n };

  function ema(src, p) {
    var k = 2 / (p + 1), out = new Array(src.length), s = 0;
    for (var i = 0; i < src.length; i++) {
      if (i < p) { s += src[i]; out[i] = (i === p - 1) ? s / p : null; }
      else { out[i] = src[i] * k + out[i - 1] * (1 - k); }
    }
    return out;
  }
  function pctRank(a, v) {
    var cnt = 0, t = 0;
    for (var i = 0; i < a.length; i++) { if (a[i] == null) continue; t++; if (a[i] <= v) cnt++; }
    return t ? cnt / t * 100 : null;
  }

  var e20 = ema(c, 20), e50 = ema(c, 50), e100 = ema(c, 100);
  var e200 = n >= 220 ? ema(c, 200) : null;
  var sp = new Array(n);
  for (var i = 0; i < n; i++) {
    var v = [e20[i], e50[i], e100[i]];
    if (e200) v.push(e200[i]);
    if (v.some(function (x) { return x == null; }) || !c[i]) { sp[i] = null; continue; }
    sp[i] = (Math.max.apply(null, v) - Math.min.apply(null, v)) / c[i] * 100;
  }

  var L = n - 1;
  if (sp[L] == null || sp[L - 10] == null || sp[L - 3] == null) return { skip: 'short' };
  var hist = sp.slice(Math.max(0, L - 119), L + 1).filter(function (x) { return x != null; });
  if (hist.length < 60) return { skip: 'hist' };
  var recent = sp.slice(Math.max(0, L - 14), L + 1).filter(function (x) { return x != null; });

  return {
    actual: actual,
    price: c[L],
    bull: e20[L] > e50[L] && e50[L] > e100[L] && (!e200 || e100[L] > e200[L]),
    bear: e20[L] < e50[L] && e50[L] < e100[L] && (!e200 || e100[L] < e200[L]),
    aboveE20: c[L] > e20[L],
    mom: c[L] - c[L - 3],
    squeezePctl: pctRank(hist, Math.min.apply(null, recent)),
    nowPctl: pctRank(hist, sp[L]),
    expansion: sp[L - 10] > 0 ? sp[L] / sp[L - 10] : 0,
    spread: sp[L],
    bars: n,
  };
};
1`;

/** Loads a symbol at a timeframe and waits for the bar series to settle. */
export async function loadChart(sym, tf) {
  return evaluate(`
    new Promise(function (resolve) {
      var chart = window.TradingViewApi.activeChart();
      chart.setSymbol(${JSON.stringify(sym)});
      setTimeout(function () {
        chart.setResolution(${JSON.stringify(tf)});
        var t0 = Date.now(), last = -1, stable = 0;
        var iv = setInterval(function () {
          var ok = false, n = 0;
          try {
            var c = window.TradingViewApi.activeChart();
            var series = window.TradingViewApi._chartWidgetCollection.activeChartWidget.value()
                           .model().mainSeries();
            n = series.bars().size();
            // Check the symbol the BARS belong to, not chart.symbol(): the chart
            // reports the new symbol before the series has swapped, which let a
            // previous symbol's bars be analyzed under the next symbol's name.
            var si = series.symbolInfo ? series.symbolInfo() : null;
            var full = si ? String(si.full_name || si.ticker || '') : '';
            var want = ${JSON.stringify(sym)};
            var symOk = full === want || full.split(':').pop() === want.split(':').pop();
            var r = String(c.resolution()), tf = ${JSON.stringify(tf)};
            var resOk = (r === tf) || (tf === "1D" && r === "D") || (tf === "D" && r === "1D");
            ok = symOk && resOk && n > 60;
          } catch (e) {}
          if (ok) {
            if (n === last) stable++; else { stable = 0; last = n; }
            if (stable >= 2) { clearInterval(iv); resolve(true); return; }
          }
          if (Date.now() - t0 > 12000) { clearInterval(iv); resolve(false); }
        }, 250);
      }, 300);
    })
  `);
}

/** Loads each symbol in turn and runs the in-page analyzer. */
export async function analyzeSeries(symbols, tf, label = 'analyzing') {
  await evaluate(ANALYZER);
  const out = [];
  for (const s of symbols) {
    const sym = typeof s === 'string' ? s : s.sym;
    const ok = await loadChart(sym, tf);
    const raw = ok ? await evaluate('JSON.stringify(window.__tvAnalyze())') : null;
    const parsed = raw ? JSON.parse(raw) : { skip: 'timeout' };

    // Belt and braces: never attribute one symbol's bars to another.
    const actual = parsed.actual || '';
    if (actual && actual.split(':').pop() !== sym.split(':').pop()) {
      out.push({ ...(typeof s === 'string' ? { sym } : s), skip: `mismatch:${actual}` });
    } else {
      out.push({ ...(typeof s === 'string' ? { sym } : s), ...parsed });
    }
    process.stdout.write(`\r  ${label}: ${out.length}/${symbols.length}   `);
  }
  process.stdout.write('\n');
  return out;
}

export async function restoreChart(sym = 'NSE:NIFTY', tf = '5') {
  await evaluate(`
    (function () {
      var c = window.TradingViewApi.activeChart();
      c.setSymbol(${JSON.stringify(sym)}); c.setResolution(${JSON.stringify(tf)});
    })()
  `);
}

export function arg(name, def) {
  const a = process.argv.slice(2);
  const i = a.indexOf(`--${name}`);
  return i >= 0 && a[i + 1] ? a[i + 1] : def;
}
