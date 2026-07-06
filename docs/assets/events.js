/**
 * events.js — 事件中樞前端(stage9 Day1)
 * fetch data/v2/events.json(與 19:00 render 解耦)→ 填「📅 未來 14 天」區塊
 * + 個股卡 7 天內法說會標 📅 徽章。
 */
(function () {
  'use strict';

  // 重要度分級(§3.5 事件擴充):中高以上 = {high, medium_high}
  const LV = { high: { i: '🔴', c: '#ef4444' }, medium_high: { i: '🟠', c: '#f59e0b' },
               medium: { i: '⚪', c: '#94a3b8' } };
  const WD = ['日', '一', '二', '三', '四', '五', '六'];

  function fmtDate(iso) {
    const d = new Date(iso + 'T00:00:00+08:00');
    return `${iso.slice(5)}（${WD[d.getDay()]}）`;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function lvlOf(e) { return e.level || (e.importance === 'high' ? 'high' : 'medium'); }
  function isHigh(e) { const l = lvlOf(e); return l === 'high' || l === 'medium_high'; }
  function cardLink(sym, label) {
    const a = sym ? `watchlist_v2.html#card-${esc(sym).replace(/:/g, '_')}` : '';
    return a ? `<a href="${a}">${label}</a>` : label;
  }

  function mergedLine(icon, label, arr) {
    if (arr.length === 1) {
      const e = arr[0];
      const nm = `${esc(e.name)} <code>${esc(e.symbol || '')}</code>`;
      return `<li><span class="ev-conf">${icon} ${cardLink(e.symbol, nm)}</span>`
           + `<span class="ev-detail">${esc(e.title || '')}</span></li>`;
    }
    const links = arr.map(e => cardLink(e.symbol, esc(e.name))).join('、');
    return `<li><span class="ev-conf">${icon} ${label} ${arr.length} 檔:</span>`
         + ` <span class="ev-merged">${links}</span></li>`;
  }

  function renderDayItems(items) {
    // 同日多事件合併:個股類(法說會/除權息)併成一行;總經類一行一條
    const g = {};
    items.forEach(e => { (g[e.type] = g[e.type] || []).push(e); });
    let h = '';
    ['macro', 'macro_tw', 'settlement'].forEach(t => {
      (g[t] || []).forEach(e => {
        const L = LV[lvlOf(e)] || LV.medium;
        h += `<li><span class="ev-macro" style="color:${L.c}">${L.i} ${esc(e.name)}</span>`
           + `<span class="ev-detail">${esc(e.title || '')}</span></li>`;
      });
    });
    if (g.conference && g.conference.length) h += mergedLine('📅', '法說會', g.conference);
    if (g.dividend && g.dividend.length) h += mergedLine('💰', '除權息', g.dividend);
    return h;
  }

  function renderDays(arr) {
    const byDate = {};
    arr.forEach(e => { (byDate[e.date] = byDate[e.date] || []).push(e); });
    let h = '';
    Object.keys(byDate).sort().forEach(date => {
      h += `<div class="events-day"><div class="events-date">${esc(fmtDate(date))}</div>`
         + `<ul class="events-list">${renderDayItems(byDate[date])}</ul></div>`;
    });
    return h;
  }

  function renderBlock(data) {
    const evs = (data.events || []).slice().sort((a, b) => a.date.localeCompare(b.date));
    if (!evs.length) return '<div class="events-empty">未來 14 天無重大事件</div>';

    // 顯示規則(最終版):
    //   直接顯示 = 中高重要度 OR watchlist 個股事件(有 symbol:法說會/除權息,與卡片徽章並存)
    //   預設折疊 = 其餘低重要度(結算、美國中低總經等,無 symbol)→ 可點展開
    const shown = [], low = [];
    evs.forEach(e => ((isHigh(e) || e.symbol) ? shown : low).push(e));

    let html = '';
    if (data.conference_stale) {
      html += `<div class="events-stale">⚠️ 法說會抓取失敗,顯示 ${esc(data.conference_source_date || '前次')} 排程(可能過時);其餘為最新</div>`;
    }
    if (data.dividend_stale) {
      html += `<div class="events-stale">⚠️ 除權息抓取失敗,顯示前次資料(可能過時)</div>`;
    }
    html += renderDays(shown) || '<div class="events-empty">未來 14 天無中高重要度或個股事件</div>';
    if (low.length) {
      html += `<details class="events-lowfold"><summary>＋ ${low.length} 筆較低重要度事件</summary>`
            + `<div class="events-lowfold-body">${renderDays(low)}</div></details>`;
    }
    return html;
  }

  function _addBadge(summary, cls, txt, title) {
    const b = document.createElement('span');
    b.className = cls; b.title = title; b.textContent = txt;
    summary.insertBefore(b, summary.firstChild);
  }

  function markCardBadges(data) {
    const t0 = new Date(new Date().toDateString());
    const soon = new Date(t0.getTime() + 7 * 86400000);
    const conf = {}, div = {};
    (data.events || []).forEach(e => {
      if (!e.symbol) return;
      const d = new Date(e.date + 'T00:00:00+08:00');
      if (d < t0 || d > soon) return;
      if (e.type === 'conference') conf[e.symbol] = e.date;
      else if (e.type === 'dividend') div[e.symbol] = e.date;
    });
    document.querySelectorAll('.stock-card[data-symbol], .wl-stock[data-symbol]').forEach(card => {
      const sym = card.getAttribute('data-symbol');
      const summary = card.querySelector('summary');
      if (!summary) return;
      if (conf[sym] && !card.querySelector('.conf-badge'))
        _addBadge(summary, 'conf-badge', '📅', `${conf[sym]} 有法說會`);
      if (div[sym] && !card.querySelector('.div-badge'))
        _addBadge(summary, 'div-badge', '💰', `${div[sym]} 除權息`);
    });
  }

  async function init() {
    let data;
    try {
      const resp = await fetch('data/v2/events.json', { cache: 'no-cache' });
      if (!resp.ok) return;              // 無 events.json → 靜默(區塊維持 hidden)
      data = await resp.json();
    } catch (e) { return; }

    const hub = document.getElementById('events-hub');
    if (hub) {
      const body = hub.querySelector('.events-body');
      if (body) body.innerHTML = renderBlock(data);
      hub.hidden = false;
    }
    markCardBadges(data);
  }

  // ── 總經快覽橫條(§3.2)+ 新聞關鍵字(§3.4)—— 僅儀表板(有 events-hub)注入 ──
  const REPO = 'mardichao-dotcom/daily-stock-analysis';
  const NEWS_KW_RAW = `https://raw.githubusercontent.com/${REPO}/main/config/news_keywords.json`;
  const NEWS_KW_EDIT = `https://github.com/${REPO}/edit/main/config/news_keywords.json`;

  function macroItem(it) {
    if (!it || it.value === 'N/A') {
      return `<span class="mb-item mb-na" title="${esc((it && it.error) || '')}">${esc(it ? it.label : '')} <b>N/A</b></span>`;
    }
    const pct = typeof it.change_pct === 'number' ? it.change_pct : 0;
    const col = pct > 0 ? '#ef4444' : (pct < 0 ? '#10b981' : 'var(--text-mute)'); // 紅漲綠跌
    const arrow = pct > 0 ? '▲' : (pct < 0 ? '▼' : '');
    const u = it.unit === '張' ? ' 張' : '';
    const val = it.unit === '張' ? Number(it.value).toLocaleString() : it.value;
    return `<span class="mb-item"><span class="mb-label">${esc(it.label)}</span>`
         + `<span class="mb-val">${esc(val)}${u}</span>`
         + `<span class="mb-chg" style="color:${col}">${arrow}${pct.toFixed(2)}%</span></span>`;
  }

  function injectMacroBar(header, m) {
    const order = ['taiex', 'sp500', 'nasdaq', 'vix', 'nikkei', 'dxy', 'margin'];
    const items = order.map(k => macroItem(m.data && m.data[k])).join('');
    const staleWarn = (m.sources_failed > 0)
      ? `<span class="mb-fail" title="${esc((m.errors || []).join('; '))}">⚠️ ${m.sources_failed} 項失敗</span>` : '';
    const gen = (m.generated_at || '').slice(5, 16).replace('T', ' ');
    const bar = document.createElement('div');
    bar.className = 'macro-bar';
    bar.innerHTML = `<div class="container macro-bar-inner">${items}${staleWarn}`
                  + `<span class="mb-gen">總經 ${esc(gen)}</span></div>`;
    header.parentNode.insertBefore(bar, header.nextSibling);
  }

  function injectNewsKeywords(header, kw) {
    const list = (kw.keywords || []).map(esc).join('、');
    const bar = document.createElement('div');
    bar.className = 'news-kw-bar';
    bar.innerHTML = `<div class="container">📰 新聞關鍵字:<span class="nk-list">${list || '（無）'}</span>`
                  + ` <a class="nk-edit" href="${NEWS_KW_EDIT}" target="_blank" rel="noopener">✏️ 編輯</a></div>`;
    header.parentNode.insertBefore(bar, header.nextSibling);
  }

  async function initMacro() {
    const header = document.querySelector('header.page-header');
    if (!header || !document.getElementById('events-hub')) return; // 僅儀表板
    try {
      const r = await fetch('data/v2/macro.json', { cache: 'no-cache' });
      if (r.ok) injectMacroBar(header, await r.json());
    } catch (e) { /* 靜默 */ }
    try {
      const r = await fetch(NEWS_KW_RAW, { cache: 'no-cache' });
      if (r.ok) injectNewsKeywords(header, await r.json());
    } catch (e) { /* 靜默 */ }
  }

  function boot() { init(); initMacro(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
})();
