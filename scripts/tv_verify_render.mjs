/**
 * tv_verify_render.mjs — 第三層驗證:注入 JS 確認 chart 頁「實際渲染」(非只驗 target 存在)。
 *
 * 7/6 白屏事故:18:45 重啟後 TV Desktop 白屏(視窗在、內容空白)→ CDP 殼活著但渲染程序沒載入
 * → 舊 preflight 的「chart 頁存在」誤判通過 → tv_collect 等永不 ready 的 API 燒滿 30 分。
 *
 * 判定:
 *   - 連不到 chart target(connectCDP throw)→ 未渲染(exit 1)
 *   - document.body 內容過少(白屏)→ 未渲染(exit 1)
 *   - body 有內容但 TradingViewApi 60s 內未 ready(半殘)→ 未渲染(exit 1)
 *   - body 有內容且 TradingViewApi ready → 已渲染(exit 0)
 * 全程有硬 timeout(evaluate 已包 EVAL_TIMEOUT;另設 process 級 90s 保險)。
 *
 * 跑:node scripts/tv_verify_render.mjs   (0=已渲染 / 1=白屏或未渲染)
 */
import { connectCDP, ev, waitForApiReady } from './tv_collect.mjs';

const BODY_MIN = 50;                 // document.body.innerText 少於此視為白屏
const API_TIMEOUT_MS = 60000;        // TradingViewApi ready 等待上限(用戶指定 60s)
const HARD_MS = 90000;               // process 級硬上限

const killer = setTimeout(() => {
  console.error('[render] ❌ 驗證整體逾時(90s)— 視為未渲染');
  process.exit(1);
}, HARD_MS);
killer.unref?.();

(async () => {
  let client = null;
  try {
    client = await connectCDP();     // 找 tradingview.com/chart target;無則 throw
  } catch (e) {
    console.error(`[render] ❌ 無 chart target(${e.message})— 可能白屏/停在 new-tab`);
    process.exit(1);
  }
  try {
    const bodyLen = await ev(client, `((document.body && document.body.innerText) || '').length`)
      .catch(() => 0);
    if (bodyLen < BODY_MIN) {
      console.error(`[render] ❌ 白屏:document.body 內容僅 ${bodyLen} 字(<${BODY_MIN})`);
      await client.close().catch(() => {});
      process.exit(1);
    }
    await waitForApiReady(client, API_TIMEOUT_MS);   // 內部 ev 已包 EVAL_TIMEOUT,60s 迴圈才有效
    console.log(`[render] ✓ 已渲染(document.body ${bodyLen} 字,TradingViewApi ready)`);
    await client.close().catch(() => {});
    process.exit(0);
  } catch (e) {
    console.error(`[render] ❌ 未渲染/半殘:${e.message}`);
    await client.close().catch(() => {});
    process.exit(1);
  }
})();
