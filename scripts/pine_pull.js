#!/usr/bin/env node
// Pull current Pine Script source from TradingView editor → scripts/current.pine
import CDP from 'chrome-remote-interface';
import { writeFileSync } from 'fs';

const targets = await (await fetch('http://localhost:9223/json/list')).json();
// The Pine Editor opens as its own detached CDP target (url contains /pine/?id=...),
// separate from the main chart window -- prefer that, fall back to the docked case.
const t = targets.find(t => t.type === 'page' && /tradingview\.com\/pine\//i.test(t.url))
  || targets.find(t => t.type === 'page' && /tradingview\.com\/chart/i.test(t.url))
  || targets.find(t => t.type === 'page' && /tradingview\.com/i.test(t.url));
if (!t) { console.error('No TradingView target'); process.exit(1); }
const c = await CDP({ host: 'localhost', port: 9223, target: t.id });
await c.Runtime.enable();

// Multiple Monaco editor instances can stay alive in the DOM at once (background
// tabs from previously-opened scripts) -- getEditors()[0] is NOT reliably the
// active/visible one. Pick the editor whose DOM node is actually rendered
// (non-zero size, not display:none) instead of blindly taking the first.
const src = (await c.Runtime.evaluate({
  expression: '(function(){var c=document.querySelector(".monaco-editor.pine-editor-monaco");if(!c)return null;var el=c;var fk;for(var i=0;i<20;i++){if(!el)break;fk=Object.keys(el).find(function(k){return k.startsWith("__reactFiber$")});if(fk)break;el=el.parentElement}if(!fk)return null;var cur=el[fk];for(var d=0;d<15;d++){if(!cur)break;if(cur.memoizedProps&&cur.memoizedProps.value&&cur.memoizedProps.value.monacoEnv){var env=cur.memoizedProps.value.monacoEnv;if(env.editor&&typeof env.editor.getEditors==="function"){var eds=env.editor.getEditors();var visible=eds.find(function(ed){var node=ed.getDomNode&&ed.getDomNode();if(!node)return false;var r=node.getBoundingClientRect();return r.width>0&&r.height>0&&node.offsetParent!==null});if(visible)return visible.getValue();if(eds.length>0)return eds[eds.length-1].getValue()}}cur=cur.return}return null})()',
  returnByValue: true,
})).result?.value;

if (!src) { console.error('Could not read Pine editor'); await c.close(); process.exit(1); }

const outPath = new URL('../scripts/current.pine', import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1');
writeFileSync(outPath, src);
console.log(`Pulled ${src.split('\n').length} lines → scripts/current.pine`);
await c.close();
