/**
 * events.js — 事件中樞前端(stage9 Day1)
 * fetch data/v2/events.json(與 19:00 render 解耦)→ 填「📅 未來 14 天」區塊
 * + 個股卡 7 天內法說會標 📅 徽章。
 */
(function () {
  'use strict';

  const IMP_COLOR = { high: '#ef4444', medium: '#f59e0b' };
  const WD = ['日', '一', '二', '三', '四', '五', '六'];

  function fmtDate(iso) {
    const d = new Date(iso + 'T00:00:00+08:00');
    return `${iso.slice(5)}（${WD[d.getDay()]}）`;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function renderBlock(data) {
    const evs = (data.events || []).slice().sort((a, b) => a.date.localeCompare(b.date));
    if (!evs.length) return '<div class="events-empty">未來 14 天無重大事件</div>';

    // group by date
    const byDate = {};
    evs.forEach(e => { (byDate[e.date] = byDate[e.date] || []).push(e); });

    let html = '';
    if (data.conference_stale) {
      html += `<div class="events-stale">⚠️ 法說會資料抓取失敗,顯示 ${esc(data.conference_source_date || '前次')} 的排程(可能過時);總經數據為最新</div>`;
    }
    Object.keys(byDate).sort().forEach(date => {
      html += `<div class="events-day"><div class="events-date">${esc(fmtDate(date))}</div><ul class="events-list">`;
      byDate[date].forEach(e => {
        if (e.type === 'macro') {
          const col = IMP_COLOR[e.importance] || 'var(--text-mute)';
          const dot = e.importance === 'high' ? '🔴' : '🟠';
          html += `<li><span class="ev-macro" style="color:${col}">${dot} ${esc(e.name)}</span>`
                + `<span class="ev-src">${esc(e.source)}</span></li>`;
        } else { // conference — 連到 watchlist 頁該個股卡(全 grade 皆有卡)
          const anchor = e.symbol ? `watchlist_v2.html#card-${esc(e.symbol).replace(/[:]/g, '_')}` : '';
          const nm = `📅 ${esc(e.name)} <code>${esc(e.symbol || '')}</code>`;
          html += `<li><span class="ev-conf">${anchor ? `<a href="${anchor}">${nm}</a>` : nm}</span>`
                + `<span class="ev-detail">${esc(e.time || '')} ${esc(e.title || '')}</span></li>`;
        }
      });
      html += '</ul></div>';
    });
    return html;
  }

  function markCardBadges(data) {
    const today = new Date();
    const soon = new Date(today.getTime() + 7 * 86400000);
    const soonSyms = {};
    (data.events || []).forEach(e => {
      if (e.type !== 'conference' || !e.symbol) return;
      const d = new Date(e.date + 'T00:00:00+08:00');
      if (d >= new Date(today.toDateString()) && d <= soon) soonSyms[e.symbol] = e.date;
    });
    document.querySelectorAll('.stock-card[data-symbol], .wl-stock[data-symbol]').forEach(card => {
      const sym = card.getAttribute('data-symbol');
      if (soonSyms[sym] && !card.querySelector('.conf-badge')) {
        const b = document.createElement('span');
        b.className = 'conf-badge';
        b.title = `${soonSyms[sym]} 有法說會`;
        b.textContent = '📅';
        const summary = card.querySelector('summary');
        if (summary) summary.insertBefore(b, summary.firstChild);
      }
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
