// tv_open_chart.mjs — 把 TV 的主 app 視窗導航回 chart URL(復原 chart target)
//
// 背景:tv_restart.sh 舊的復原用 `curl -X PUT $CDP/json/new?URL`,但該 CDP HTTP 端點
//   在 Chrome 140(TV 3.3.0 內嵌)已停用(回 "Not supported"),Target.createTarget 也被
//   Electron 擋。實測可行的復原是:對 TV 主 app 視窗(url 結尾 index.html)下 Page.navigate
//   到 chart URL——這正是 TV 健康時「整個視窗導到 chart」的做法。
//
// 用途:① tv_restart.sh 重啟後 TV 未自動重開 chart 時的復原步驟;② 手動救回。
// 退出:0 = chart target 出現;1 = 找不到可導航的視窗 / 逾時。
import CDP from '/Users/mardichao/tradingview-mcp/node_modules/chrome-remote-interface/index.js';

const URL = process.env.TV_CHART_URL || 'https://tw.tradingview.com/chart/xdySlor8/';
const PORT = 9222;

async function list() {
  return await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
}
async function hasChart() {
  return (await list()).some(t => t.type === 'page' && /tradingview\.com\/chart/.test(t.url || ''));
}

if (await hasChart()) { console.log('[open_chart] chart target 已存在,無需導航'); process.exit(0); }

const targets = await list();
// 主 app 視窗:url 結尾 index.html(非 ?rendererInitialData 子渲染);找不到就退而求其次挑任一 index.html
const cands = targets.filter(t => t.type === 'page' && /index\.html$/.test(t.url || ''));
if (!cands.length) { console.log('[open_chart] 找不到可導航的 app 視窗'); process.exit(1); }

for (const t of cands) {
  try {
    const c = await CDP({ host: 'localhost', port: PORT, target: t.id });
    await c.Page.enable();
    await c.Page.navigate({ url: URL });
    await c.close();
    console.log(`[open_chart] 已導航 ${t.id} → ${URL}`);
  } catch (e) {
    console.log(`[open_chart] 導航 ${t.id} 失敗:${e.message}`);
  }
}

// 等 chart target 出現(最多 60s)
for (let i = 0; i < 12; i++) {
  await new Promise(r => setTimeout(r, 5000));
  if (await hasChart()) { console.log(`[open_chart] ✓ chart target 出現(約 ${(i + 1) * 5}s)`); process.exit(0); }
}
console.log('[open_chart] ✗ 導航後 60s 內 chart target 未出現');
process.exit(1);
