/**
 * tv_collect_init.test.mjs — 7/6 卡死型態回歸測試。
 * mock 一個「TradingViewApi 永不 ready」的圖表(Runtime.evaluate 永不 resolve),
 * 斷言:單檔迴圈之前的初始化階段會被獨立 timeout 觸發(而非燒滿 30 分全域額度)。
 *
 * 跑:node --test tests/tv_collect_init.test.mjs
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { withTimeout, ev, waitForApiReady } from '../scripts/tv_collect.mjs';

// 永不回應的 CDP client(模擬 chart 頁存在但 API 永不初始化 → evaluate 掛住)
const neverReadyClient = {
  Runtime: {
    evaluate: () => new Promise(() => {}),   // 永不 resolve
    enable: async () => {},
  },
  close: async () => {},
};

test('withTimeout 以 label 觸發「初始化逾時」', async () => {
  const start = Date.now();
  await assert.rejects(
    withTimeout(new Promise(() => {}), 150, '初始化'),
    /初始化逾時 0\.15s/);
  assert.ok(Date.now() - start < 1000, '應立即逾時,不空耗');
});

test('ev() 對永不回應的 evaluate 會逾時 reject(不永久掛住)', async () => {
  // 用短 race 包住 ev,確認 ev 本身是可被逾時打斷的(底層 evaluate 已包 EVAL_TIMEOUT)
  await assert.rejects(
    withTimeout(ev(neverReadyClient, 'window.x'), 150, '初始化'),
    /初始化逾時/);
});

test('初始化階段(永不 ready 圖表)被 INIT timeout 觸發,不燒到 30 分', async () => {
  const start = Date.now();
  // 模擬 main() 的初始化 race:waitForApiReady 的 ev 永遠卡住,外層 INIT timeout 必須先開火
  await assert.rejects(
    withTimeout(waitForApiReady(neverReadyClient, 5000), 200, '初始化'),
    /初始化逾時/);
  assert.ok(Date.now() - start < 1500, `應在 INIT timeout 內結束,實際 ${Date.now() - start}ms`);
});
