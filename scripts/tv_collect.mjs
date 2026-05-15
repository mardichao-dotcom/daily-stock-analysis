/**
 * TradingView Daily Data Collector  v2
 * - 讀 config/watchlist.json（不硬寫 symbol 清單）
 * - 預設 180 bars；CLI：node scripts/tv_collect.mjs [--days N]
 * - 增量模式：查 kline.db MIN+MAX date 決定抓取策略
 *     新代號           → full 180 bars
 *     歷史不足 180 天  → backfill 180 bars（INSERT OR IGNORE 去重）
 *     歷史足夠但非最新 → increment 10 bars
 *     已是最新         → skip
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

// ── CLI args ────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const daysIdx = args.indexOf('--days');
const DAYS = daysIdx >= 0 ? parseInt(args[daysIdx + 1], 10) : 180;

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

// ── 抓取策略決定 ─────────────────────────────────────────────────────────────
function getStrategy(symbol, status, today, threshold) {
  const s = status[symbol];
  if (!s)            return { mode: 'full',      bars: DAYS, reason: '新代號' };
  if (s.min > threshold) return { mode: 'backfill', bars: DAYS, reason: `歷史不足(${s.min}>${threshold})` };
  if (s.max >= today)    return { mode: 'skip',      bars: 0,    reason: `已是最新(${s.max})` };
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

async function connectCDP() {
  const resp    = await fetch(`http://localhost:${CDP_PORT}/json/list`);
  const targets = await resp.json();
  const target  = targets.find(t => t.type === 'page' && /tradingview\.com\/chart/i.test(t.url))
                || targets.find(t => t.type === 'page' && /tradingview/i.test(t.url));
  if (!target) throw new Error('No TradingView chart target found');
  const client = await CDP({ host: 'localhost', port: CDP_PORT, target: target.id });
  await client.Runtime.enable();
  return client;
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

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const startTime = Date.now();
  const today     = TODAY_STR;
  const threshold = new Date(Date.now() - DAYS * 24 * 3600 * 1000).toISOString().slice(0, 10);

  console.log(`[START] ${new Date().toISOString()} | ${ALL_SYMBOLS.length} symbols | DAYS=${DAYS}`);
  console.log(`        threshold(${DAYS}d ago)=${threshold} | today=${today}`);

  const klineStatus = getKlineStatus();
  console.log(`        kline.db: ${Object.keys(klineStatus).length} symbols already tracked`);

  const client = await connectCDP();
  const results = {};
  const errors  = {};
  let skipped = 0, totalBars = 0;

  for (let i = 0; i < ALL_SYMBOLS.length; i++) {
    const symbol   = ALL_SYMBOLS[i];
    const strategy = getStrategy(symbol, klineStatus, today, threshold);
    const tag      = `[${String(i+1).padStart(2,'0')}/${ALL_SYMBOLS.length}]`;

    if (strategy.mode === 'skip') {
      console.log(`${tag} ${symbol} ... SKIP (${strategy.reason})`);
      skipped++;
      continue;
    }

    process.stdout.write(`${tag} ${symbol} (${strategy.mode}, ${strategy.bars}bars) ... `);

    let ok = false;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await setSymbol(client, symbol);
        await sleep(attempt === 0 ? 4000 : 7000);
        await waitReady(client, 10000);
        const data = await getOHLCV(client, strategy.bars);
        if (data?.bars?.length > 0) {
          results[symbol] = data;
          totalBars += data.bars.length;
          ok = true;
          process.stdout.write(`OK (${data.bars.length} bars)\n`);
          break;
        } else {
          throw new Error('empty bars');
        }
      } catch (err) {
        logFail(symbol, attempt + 1, err.message.slice(0, 200));
        if (attempt === 0) process.stdout.write(`retry... `);
        else {
          errors[symbol] = err.message.slice(0, 120);
          process.stdout.write(`FAIL: ${errors[symbol]}\n`);
        }
      }
    }
    if (!ok && !errors[symbol]) errors[symbol] = 'empty data';
  }

  await client.close();

  const elapsed   = Math.round((Date.now() - startTime) / 1000);
  const failList  = Object.keys(errors).join(', ') || 'none';
  const lines = [
    '',
    '═══════════════════════════════════════════',
    '  tv_collect 執行摘要',
    '───────────────────────────────────────────',
    `  總 symbols：${ALL_SYMBOLS.length}`,
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
}

main().catch(e => { console.error('[FATAL]', e.message); process.exit(1); });
