/**
 * TradingView Daily Data Collector  v2
 * - 讀 config/watchlist.json（不硬寫 symbol 清單）
 * - 預設 180 bars；CLI：node scripts/tv_collect.mjs [--days N]
 * - 增量模式：查 kline.db MIN+MAX date 決定抓取策略
 *     新代號           → full 180 bars
 *     歷史不足 180 天  → backfill 180 bars
 *     歷史足夠但非最新 → increment 10 bars
 *     已是最新(台股)   → skip(19:00 跑時台股已收盤,當日 bar 安全)
 *     已是最新(非台股) → refresh 最近 N 根(預設 3,--refresh-bars 可調)
 *                        ★ P0-C:美/歐股 19:00 台北跑時尚未收盤,當日 bar 是盤中半成品,
 *                          永遠重抓最近幾根 + import REPLACE,半成品隔天自動被收盤值覆寫
 * - 重試：每支 symbol 最多 2 次（4s / 7s）
 * - 失敗寫 logs/tv_collect_{YYYY-MM-DD}.log
 * Run: node scripts/tv_collect.mjs [--days N]
 */

import CDP from '/Users/mardichao/tradingview-mcp/node_modules/chrome-remote-interface/index.js';
import { writeFileSync, readFileSync, mkdirSync, appendFileSync } from 'fs';
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = join(__dirname, '..');
const CDP_PORT = 9222;
const CHART_API = "window.TradingViewApi._activeChartWidgetWV.value()";
const BARS_PATH = `${CHART_API}._chartWidget.model().mainSeries().bars()`;
// Preflight 等 chart page 把 TradingViewApi 物件 expose 出來(2026-06-01 修補)
const PREFLIGHT_PROBE = `(()=>{try{return typeof window.TradingViewApi==='object'&&typeof window.TradingViewApi._activeChartWidgetWV==='object'&&typeof window.TradingViewApi._activeChartWidgetWV.value==='function'&&typeof window.TradingViewApi._activeChartWidgetWV.value()==='object'}catch(e){return false}})()`;

// ── CLI args ────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const daysIdx = args.indexOf('--days');
const DAYS = daysIdx >= 0 ? parseInt(args[daysIdx + 1], 10) : 180;
const symbolIdx = args.indexOf('--symbol');
const SINGLE_SYMBOL = symbolIdx >= 0 ? args[symbolIdx + 1] : null;
const timeoutIdx = args.indexOf('--timeout-min');
const TIMEOUT_MIN = timeoutIdx >= 0 ? parseInt(args[timeoutIdx + 1], 10) : 30;
// P0-C:非台股已是最新時,重抓最近 N 根覆寫半成品(一次性清洗可調大,如 --refresh-bars 5)
const refreshIdx = args.indexOf('--refresh-bars');
const REFRESH_BARS = refreshIdx >= 0 ? parseInt(args[refreshIdx + 1], 10) : 3;
// 只跑指定交易所(P0-D 美股補跑 / 一次性非台股清洗用),逗號分隔,如 --exchanges NASDAQ,NYSE
const exIdx = args.indexOf('--exchanges');
const ONLY_EXCHANGES = exIdx >= 0 ? new Set(args[exIdx + 1].split(',')) : null;
// §6.5:單檔逾時(6/7 事故:第 2 檔卡死燒光 30 分鐘全域額度)。逾時 → 記 errors → 下一檔。
const PER_SYMBOL_TIMEOUT_MS = 60000;

// ── 整體 timeout(避免 5/31 那種 TV Desktop 自己卡連帶 tv_collect 跟著卡)──
const GLOBAL_TIMEOUT_MS = TIMEOUT_MIN * 60 * 1000;
const globalKiller = setTimeout(() => {
  console.error(`[FATAL] tv_collect exceeded ${TIMEOUT_MIN} min global timeout — bailing`);
  process.exit(124);   // unix conventional timeout exit code
}, GLOBAL_TIMEOUT_MS);
globalKiller.unref();

// ── watchlist.json → symbol lists ──────────────────────────────────────────
const watchlist = JSON.parse(
  readFileSync(join(PROJECT_ROOT, 'config', 'watchlist.json'), 'utf8')
);

function extractCodes(group) {
  return Object.values(group).flatMap(sector => sector['成員'].map(m => m.code));
}

const GLOBAL_SYMBOLS = extractCodes(watchlist['國際族群']);
const TW_SYMBOLS     = extractCodes(watchlist['台股板塊']);
const ALL_SYMBOLS    = [...GLOBAL_SYMBOLS, ...TW_SYMBOLS];

// sector maps for output JSON
const global_sectors = Object.fromEntries(
  Object.entries(watchlist['國際族群']).map(([k, v]) => [k, v['成員'].map(m => m.code)])
);
const tw_sectors = Object.fromEntries(
  Object.entries(watchlist['台股板塊']).map(([k, v]) => [k, v['成員'].map(m => m.code)])
);

// ── kline.db 狀態查詢（透過 Python child_process）──────────────────────────
function getKlineStatus() {
  const dbPath = join(PROJECT_ROOT, 'kline.db');
  const script = `
import sqlite3, json, os, sys
p = r'${dbPath.replace(/\\/g, '\\\\')}'
if not os.path.exists(p):
    print('{}'); sys.exit(0)
conn = sqlite3.connect(p)
rows = conn.execute("SELECT symbol, MIN(date), MAX(date) FROM kline GROUP BY symbol").fetchall()
conn.close()
print(json.dumps({r[0]: {'min': r[1], 'max': r[2]} for r in rows}))
`.trim();
  try {
    const out = execFileSync('python3', ['-c', script], { encoding: 'utf8' });
    return JSON.parse(out.trim());
  } catch {
    return {};
  }
}

// 台股(TWSE/TPEX)19:00 跑時已收盤,當日 bar 安全;其餘市場(美/歐/日韓)需防半成品
function isTW(symbol) {
  return symbol.startsWith('TWSE:') || symbol.startsWith('TPEX:');
}

// ── 抓取策略決定 ─────────────────────────────────────────────────────────────
function getStrategy(symbol, status, today, threshold) {
  const s = status[symbol];
  if (!s)            return { mode: 'full',      bars: DAYS, reason: '新代號' };
  if (s.min > threshold) return { mode: 'backfill', bars: DAYS, reason: `歷史不足(${s.min}>${threshold})` };
  if (s.max >= today) {
    // P0-C:非台股「已是最新」也重抓最近 N 根覆寫(當日 bar 可能是收盤前半成品)
    if (isTW(symbol)) return { mode: 'skip',    bars: 0,            reason: `已是最新(${s.max})` };
    return              { mode: 'refresh', bars: REFRESH_BARS, reason: `非台股重抓最近${REFRESH_BARS}根(${s.max})` };
  }
  return               { mode: 'increment', bars: 10,   reason: `往前補(${s.max}→${today})` };
}

// ── Log ──────────────────────────────────────────────────────────────────────
const TODAY_STR = new Date().toISOString().slice(0, 10);
const LOG_DIR   = join(PROJECT_ROOT, 'logs');
mkdirSync(LOG_DIR, { recursive: true });
const LOG_FILE  = join(LOG_DIR, `tv_collect_${TODAY_STR}.log`);

function logFail(symbol, attempt, reason) {
  const ts = new Date().toISOString();
  appendFileSync(LOG_FILE, `${ts}\t${symbol}\tattempt=${attempt}\t${reason}\n`);
}

// ── CDP helpers ───────────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// §6.5:單檔逾時包裝 — 卡死的個股不再拖垮整批,逾時即 reject 換下一檔
function withTimeout(promise, ms) {
  let t;
  const timer = new Promise((_, reject) => {
    t = setTimeout(() => reject(new Error(`單檔逾時 ${ms / 1000}s`)), ms);
    t.unref?.();
  });
  return Promise.race([promise, timer]).finally(() => clearTimeout(t));
}

// fetch 包 timeout(防 5/31 那種「CDP server 接受連線但不回應」)
async function fetchWithTimeout(url, ms) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

async function connectCDP() {
  const resp    = await fetchWithTimeout(`http://localhost:${CDP_PORT}/json/list`, 5000);
  const targets = await resp.json();
  const target  = targets.find(t => t.type === 'page' && /tradingview\.com\/chart/i.test(t.url))
                || targets.find(t => t.type === 'page' && /tradingview/i.test(t.url));
  if (!target) throw new Error('No TradingView chart target found');
  console.log(`[CDP] target: ${target.url.slice(0, 80)}`);
  const client = await CDP({ host: 'localhost', port: CDP_PORT, target: target.id });
  await client.Runtime.enable();
  return client;
}

// Preflight:等 chart page 完整把 TradingViewApi 物件 expose(避免 5/31 race)
// 失敗 → exit 2 → daily_supervisor 抓到「API drift / chart not ready」
async function waitForApiReady(client, timeoutMs = 30000) {
  const start = Date.now();
  let attempt = 0;
  while (Date.now() - start < timeoutMs) {
    attempt++;
    try {
      const ready = await ev(client, PREFLIGHT_PROBE);
      if (ready === true) {
        console.log(`[CDP] ✓ TradingViewApi ready (attempt ${attempt}, ${Date.now() - start}ms)`);
        return;
      }
    } catch { /* swallow,重試 */ }
    await sleep(2000);
  }
  // 30s 還沒就緒 → drift / chart 沒載入
  const diag = await ev(client, `(()=>{try{return JSON.stringify({tvapi:typeof window.TradingViewApi,wv:typeof window.TradingViewApi?._activeChartWidgetWV,val:typeof window.TradingViewApi?._activeChartWidgetWV?.value,url:location.href,title:document.title})}catch(e){return e.message}})()`)
    .catch(() => '(eval failed)');
  throw new Error(`TradingViewApi not ready after ${timeoutMs}ms — possible API drift or chart page not loaded. diag=${diag}`);
}

async function ev(client, expr, awaitPromise = false) {
  const res = await client.Runtime.evaluate({ expression: expr, returnByValue: true, awaitPromise });
  if (res.exceptionDetails) {
    const msg = res.exceptionDetails.exception?.description || res.exceptionDetails.text || 'Unknown';
    throw new Error(msg);
  }
  return res.result?.value;
}

async function setSymbol(client, symbol) {
  await ev(client,
    `(function(){var c=${CHART_API};return new Promise(function(r){c.setSymbol(${JSON.stringify(symbol)},{});setTimeout(r,500);});})()`,
    true
  );
}

async function waitReady(client, timeoutMs = 9000) {
  const start = Date.now();
  let lastCount = -1, stable = 0;
  while (Date.now() - start < timeoutMs) {
    try {
      const count = await ev(client, `${BARS_PATH}.size()`);
      if (count === lastCount && count > 0) { if (++stable >= 2) return; }
      else { lastCount = count; stable = 0; }
    } catch { /* ignore */ }
    await sleep(500);
  }
}

async function getOHLCV(client, bars) {
  const raw = await ev(client, `(function(){
    var b=${BARS_PATH};
    if (!b || typeof b.lastIndex !== 'function') return '[]';
    var end=b.lastIndex(), start=Math.max(b.firstIndex(), end-${bars}+1), out=[];
    for(var i=start;i<=end;i++){
      var v=b.valueAt(i);
      if(v) out.push({time:v[0],open:v[1],high:v[2],low:v[3],close:v[4],volume:v[5]||0});
    }
    return JSON.stringify(out);
  })()`);
  return { bars: JSON.parse(raw || '[]') };
}

// ── CDP 重連(hyp2 修復)──────────────────────────────────────────────────────
// 單檔逾時後,withTimeout 的 Promise.race 只是「放棄等待」,底層那個卡住的
// Runtime.evaluate 仍掛在同一條 CDP 連線上 → 會污染下一檔(6/12 事故:symbol 6
// 的 60s 計時器在累積的殭屍操作下沒能正常觸發,一路卡到 30 分全域逾時)。
// 修法:每次失敗就 close 舊 client + 重新 connect + 重跑 preflight,讓殭屍操作
// 隨死掉的 socket 一起被丟棄,下一檔在乾淨連線上重來。
async function reconnectClient(oldClient) {
  try { await oldClient?.close(); } catch { /* 已壞,忽略 */ }
  const c = await connectCDP();
  await waitForApiReady(c, 30000);
  return c;
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const startTime = Date.now();
  const today     = TODAY_STR;
  const threshold = new Date(Date.now() - DAYS * 24 * 3600 * 1000).toISOString().slice(0, 10);

  // 單檔模式(debug)/ 交易所過濾(P0-D 美股補跑、一次性非台股清洗)
  let targetSymbols = SINGLE_SYMBOL ? [SINGLE_SYMBOL] : ALL_SYMBOLS;
  if (ONLY_EXCHANGES) {
    targetSymbols = targetSymbols.filter(s => ONLY_EXCHANGES.has(s.split(':')[0]));
  }
  const modeTag = SINGLE_SYMBOL ? ` SINGLE=${SINGLE_SYMBOL}`
                : ONLY_EXCHANGES ? ` EXCHANGES=${[...ONLY_EXCHANGES].join(',')}` : '';

  console.log(`[START] ${new Date().toISOString()} | ${targetSymbols.length} symbols${modeTag} | DAYS=${DAYS} | TIMEOUT=${TIMEOUT_MIN}min`);
  console.log(`        threshold(${DAYS}d ago)=${threshold} | today=${today}`);

  const klineStatus = getKlineStatus();
  console.log(`        kline.db: ${Object.keys(klineStatus).length} symbols already tracked`);

  let client = await connectCDP();
  // ★ Preflight:等 TradingViewApi expose,否則 0 秒 87 檔全 fail(5/31 教訓)
  try {
    await waitForApiReady(client, 30000);
  } catch (e) {
    await client.close().catch(() => {});
    console.error(`[FATAL] preflight failed: ${e.message}`);
    process.exit(2);
  }

  const results = {};
  const errors  = {};
  let skipped = 0, totalBars = 0;
  // hyp2/hyp3:連續失敗達上限 → TradingView Desktop 疑似劣化,提前中止(不空耗到全域逾時)
  let consecutiveFails = 0;
  let bailed = false;
  const MAX_CONSECUTIVE_FAILS = 5;

  for (let i = 0; i < targetSymbols.length; i++) {
    const symbol   = targetSymbols[i];
    const strategy = getStrategy(symbol, klineStatus, today, threshold);
    const tag      = `[${String(i+1).padStart(2,'0')}/${targetSymbols.length}]`;

    if (strategy.mode === 'skip') {
      console.log(`${tag} ${symbol} ... SKIP (${strategy.reason})`);
      skipped++;
      continue;
    }

    process.stdout.write(`${tag} ${symbol} (${strategy.mode}, ${strategy.bars}bars) ... `);

    // 單檔採集(含 2 次重試),整包以 60s 逾時保護(§6.5)
    const attemptCollect = async () => {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await setSymbol(client, symbol);
          await sleep(attempt === 0 ? 4000 : 7000);
          await waitReady(client, 10000);
          const data = await getOHLCV(client, strategy.bars);
          if (data?.bars?.length > 0) return data;
          throw new Error('empty bars');
        } catch (err) {
          logFail(symbol, attempt + 1, err.message.slice(0, 200));
          if (attempt === 0) process.stdout.write(`retry... `);
          else throw err;
        }
      }
      return null;
    };

    const work = attemptCollect();
    work.catch(() => {});   // 逾時後被遺棄的 promise 之後若 reject,避免 unhandledRejection
    try {
      const data = await withTimeout(work, PER_SYMBOL_TIMEOUT_MS);
      if (data?.bars?.length > 0) {
        results[symbol] = data;
        totalBars += data.bars.length;
        consecutiveFails = 0;
        process.stdout.write(`OK (${data.bars.length} bars)\n`);
      } else {
        errors[symbol] = errors[symbol] || 'empty data';
        process.stdout.write(`FAIL: ${errors[symbol]}\n`);
      }
    } catch (err) {
      errors[symbol] = err.message.slice(0, 120);
      process.stdout.write(`FAIL: ${errors[symbol]}\n`);
    }

    // hyp2/hyp3:本檔失敗 → (a) 連續失敗達上限就提前中止;(b) 否則重連 CDP 再跑下一檔
    if (errors[symbol]) {
      consecutiveFails++;
      if (consecutiveFails >= MAX_CONSECUTIVE_FAILS) {
        console.error(`\n[FATAL] 連續 ${consecutiveFails} 檔失敗 — TradingView Desktop 疑似劣化,`
          + `提前中止(不空耗到 ${TIMEOUT_MIN} 分全域逾時)。建議重啟 TV Desktop 後重跑。`);
        bailed = true;
        break;
      }
      try {
        process.stdout.write(`     ↻ 重連 CDP(丟棄卡住的底層操作)...\n`);
        client = await withTimeout(reconnectClient(client), 45000);
      } catch (e) {
        console.error(`\n[FATAL] CDP 重連失敗:${e.message} — 中止`);
        bailed = true;
        break;
      }
    }
  }

  await client.close().catch(() => {});

  const elapsed   = Math.round((Date.now() - startTime) / 1000);
  const failList  = Object.keys(errors).join(', ') || 'none';
  const lines = [
    '',
    '═══════════════════════════════════════════',
    '  tv_collect 執行摘要',
    '───────────────────────────────────────────',
    `  總 symbols：${targetSymbols.length}${SINGLE_SYMBOL ? ' (SINGLE)' : ''}`,
    `  成功      ：${Object.keys(results).length}`,
    `  跳過      ：${skipped}`,
    `  失敗      ：${Object.keys(errors).length}${Object.keys(errors).length > 0 ? ' (' + failList + ')' : ''}`,
    `  抓取總筆數：${totalBars}`,
    `  耗時      ：${Math.floor(elapsed/60)} 分 ${elapsed%60} 秒`,
    `  資料寫入  ：/tmp/tv_daily_data.json`,
    Object.keys(errors).length > 0 ? `  Log 檔    ：${LOG_FILE}` : null,
    '═══════════════════════════════════════════',
  ].filter(l => l !== null);
  console.log(lines.join('\n'));

  writeFileSync('/tmp/tv_daily_data.json', JSON.stringify({
    generated_at: new Date().toISOString(),
    elapsed_seconds: elapsed,
    global_sectors,
    tw_sectors,
    results,
    errors,
  }));
  console.log('[SAVED] /tmp/tv_daily_data.json');

  // 提前中止(TV 劣化)→ 以非 0 退出,讓 run_all 安全跳過 import_kline(不匯入殘缺資料)
  if (bailed) {
    console.error('[EXIT 3] 因連續失敗提前中止 — 下游將被跳過,請重啟 TV Desktop 後重跑');
    process.exit(3);
  }
}

main().catch(e => { console.error('[FATAL]', e.message); process.exit(1); });
