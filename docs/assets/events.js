/**
 * events.js — 儀表板 client 資料層(stage9)+ Batch3 版面(stage10 §6/§7/§10)
 * fetch events.json / macro.json / news.json(與 19:00 render 解耦,08:30 更新即生效)
 *
 * §7 事件中樞:橫向日欄(只 render 有事件日);重要度徽章 高=實心 accent/中高=填色/
 *   中低=outline;法說=chip+股名連結(永不折疊);除權息=每日「+N 筆除權息」預設折疊
 *   (2026-07-08 拍板,取代先前低重要度折疊規則);stale 標題列右側標示。
 * §6 總經橫條:格線 cells;來源 N/A → 「— 等待美股 08:30」(不顯示 0/空白)。
 * §10 新聞區塊:標頭折疊(安靜日收/熱鬧日開,localStorage 記憶)+ 關鍵字列在塊底。
 * 追加:個股卡「近期事件」行(14 日內除權息/法說,同 events.json 資料源)。
 */
(function () {
  'use strict';

  const WD = ['日', '一', '二', '三', '四', '五', '六'];
  const REPO = 'mardichao-dotcom/daily-stock-analysis';
  const NEWS_KW_RAW = `https://raw.githubusercontent.com/${REPO}/main/config/news_keywords.json`;
  const NEWS_KW_EDIT = `https://github.com/${REPO}/edit/main/config/news_keywords.json`;

  function fmtDate(iso) {
    const d = new Date(iso + 'T00:00:00+08:00');
    return `${iso.slice(5).replace('-', '/')}(${WD[d.getDay()]})`;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function lvlOf(e) { return e.level || (e.importance === 'high' ? 'high' : 'medium'); }
  function wlHref(sym) { return `watchlist_v2.html#card-${esc(sym).replace(/:/g, '_')}`; }

  // ─── §7 事件中樞:橫向日欄 ───────────────────────────────────────────────
  function evBadge(e) {
    const lv = lvlOf(e);
    const cls = lv === 'high' ? 'evb-high' : (lv === 'medium_high' ? 'evb-mid' : 'evb-low');
    return `<span class="evb ${cls}">${esc(e.name)}</span>`;
  }

  function renderDayCol(date, items) {
    let h = `<div class="evc-col"><div class="evc-date">${esc(fmtDate(date))}</div>`;
    // 總經/結算:重要度徽章(直接顯示)
    items.filter(e => ['macro', 'macro_tw', 'settlement'].includes(e.type)).forEach(e => {
      h += `<div class="evc-item" title="${esc(e.title || '')}">${evBadge(e)}</div>`;
    });
    // 法說會:chip + 股名連結(永不折疊)
    items.filter(e => e.type === 'conference').forEach(e => {
      h += `<div class="evc-item"><span class="evc-chip">法說</span>`
         + `<a class="evc-stock" href="${wlHref(e.symbol || '')}">${esc(e.name)}</a></div>`;
    });
    // 除權息:預設折疊「+N 筆除權息」(2026-07-08 拍板)
    const divs = items.filter(e => e.type === 'dividend');
    if (divs.length) {
      const rows = divs.map(e =>
        `<div class="evc-item"><span class="evc-chip">${esc((e.importance || '除權息').slice(0, 3))}</span>`
        + `<a class="evc-stock" href="${wlHref(e.symbol || '')}">${esc(e.name)}</a></div>`).join('');
      h += `<details class="evc-divfold"><summary>+${divs.length} 筆除權息</summary>${rows}</details>`;
    }
    return h + '</div>';
  }

  function renderBlock(data) {
    const evs = (data.events || []).slice().sort((a, b) => a.date.localeCompare(b.date));
    const byDate = {};
    evs.forEach(e => { (byDate[e.date] = byDate[e.date] || []).push(e); });
    const days = Object.keys(byDate).sort();

    // 標題列:區間 + 法說源狀態(>2 天 → stale 虛線)
    const range = days.length ? `${days[0].slice(5).replace('-', '/')} – ${days[days.length - 1].slice(5).replace('-', '/')}` : '';
    let src = '';
    const confSrc = data.conference_source_date || '';
    if (confSrc) {
      const ageDays = Math.floor((new Date() - new Date(confSrc + 'T00:00:00+08:00')) / 86400000);
      src = (data.conference_stale || ageDays > 2)
        ? `<span class="evc-stale">法說源 stale · ${ageDays} 日前</span>`
        : `<span class="evc-src">法說會源:${esc(confSrc.slice(5).replace('-', '/'))} 更新</span>`;
    }
    let html = `<div class="evc-head"><span class="evc-range">${esc(range)}</span>${src}</div>`;
    if (data.dividend_stale) {
      html += `<div class="events-stale">⚠️ 除權息抓取失敗,顯示前次資料(可能過時)</div>`;
    }
    if (!days.length) return html + '<div class="events-empty">未來 14 天無事件</div>';
    html += `<div class="evc-scroll">${days.map(d => renderDayCol(d, byDate[d])).join('')}</div>`;
    return html;
  }

  // ─── 個股卡徽章 + 近期事件行(14 日內,同一資料源)────────────────────────
  function _addBadge(summary, cls, txt, title) {
    const b = document.createElement('span');
    b.className = cls; b.title = title; b.textContent = txt;
    summary.insertBefore(b, summary.firstChild);
  }

  function markCardBadges(data) {
    const t0 = new Date(new Date().toDateString());
    const soon7 = new Date(t0.getTime() + 7 * 86400000);
    const soon14 = new Date(t0.getTime() + 14 * 86400000);
    const conf = {}, div = {}, upcoming = {};
    (data.events || []).forEach(e => {
      if (!e.symbol) return;
      const d = new Date(e.date + 'T00:00:00+08:00');
      if (d < t0 || d > soon14) return;
      (upcoming[e.symbol] = upcoming[e.symbol] || []).push(e);
      if (d > soon7) return;
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
      // 展開態「近期事件」行(有才出現;插在 body 最上方)
      const evsFor = upcoming[sym];
      const body = card.querySelector('.card-body, .wl-stock-body');
      if (evsFor && body && !card.querySelector('.card-events')) {
        const parts = evsFor.sort((a, b) => a.date.localeCompare(b.date)).map(e => {
          const mmdd = e.date.slice(5).replace('-', '/');
          if (e.type === 'conference') return `📅 法說 ${mmdd}`;
          const kind = (e.importance || '').includes('權')
            ? ((e.importance || '').includes('息') ? '除權息' : '除權') : '除息';
          return `💰 ${kind} ${mmdd}`;
        });
        const div2 = document.createElement('div');
        div2.className = 'card-events';
        div2.innerHTML = `<span class="ce-label">近期事件</span>${esc(parts.join(' · '))}`;
        body.insertBefore(div2, body.firstChild);
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

  // ─── §6 總經快覽橫條(格線 cells)────────────────────────────────────────
  function macroCell(it, key) {
    if (!it) return '';
    const label = esc(it.label || key);
    if (it.value === 'N/A' || it.value == null) {
      // 等待美股(§6):不顯示 0 或空白
      return `<div class="mb2-cell"><span class="mb2-label">${label}</span>`
           + `<span class="mb2-wait">— 等待美股 08:30</span></div>`;
    }
    const chg = it.change_pct;
    let chgHtml = '';
    if (typeof chg === 'number') {
      const cls = chg > 0 ? 'up' : (chg < 0 ? 'down' : 'flat');
      const arrow = chg > 0 ? '▲' : (chg < 0 ? '▼' : '');
      chgHtml = `<span class="mb2-chg ${cls}">${arrow}${Math.abs(chg).toFixed(2)}%</span>`;
    }
    const unit = it.unit ? `<span class="mb2-unit">${esc(it.unit)}</span>` : '';
    // §6 融資脈絡字(2026-07-07 改版):N日連增/連減 · 近一年百分位
    let ctx = '';
    if (typeof it.streak === 'number' && typeof it.percentile === 'number') {
      const trend = it.streak > 0 ? `${it.streak}日連增`
                  : (it.streak < 0 ? `${-it.streak}日連減` : '持平');
      ctx = `<span class="mb2-ctx">${trend}｜近一年 ${it.percentile}%</span>`;
    }
    return `<div class="mb2-cell"><span class="mb2-label">${label}</span>`
         + `<span class="mb2-val">${Number(it.value).toLocaleString()}${unit}</span>${chgHtml}${ctx}</div>`;
  }

  function injectMacroBar(header, m) {
    const order = ['taiex', 'sp500', 'nasdaq', 'vix', 'nikkei', 'dxy', 'margin'];
    const cells = order.map(k => macroCell(m.data && m.data[k], k)).join('');
    const gen = (m.generated_at || '').slice(5, 16).replace('T', ' ');
    const bar = document.createElement('div');
    bar.className = 'macro-bar2';
    bar.innerHTML = `<div class="container mb2-inner">${cells}`
      + `<span class="mb2-gen">更新 ${esc(gen)}</span></div>`;
    header.insertAdjacentElement('afterend', bar);
  }

  // ─── §10 新聞區塊(折疊 + 關鍵字列在塊底)────────────────────────────────
  function newsRow(it) {
    const t = (it.published_at || '').slice(11, 16);
    const kw = (it.matched_keywords || [])[0] || '';
    const host = (() => { try { return new URL(it.url).host.replace('www.', ''); } catch (e) { return it.source || ''; } })();
    return `<div class="nw-row">`
      + `<span class="nw-time">${esc(t)}</span>`
      + (kw ? `<span class="nw-kw">${esc(kw)}</span>` : '')
      + `<a class="nw-title" href="${esc(it.url)}" target="_blank" rel="noopener" title="${esc(it.title)}">${esc(it.title)}</a>`
      + `<span class="nw-src">${esc(it.source || host)} ↗</span></div>`;
  }

  async function initNews() {
    const block = document.getElementById('news-block');
    if (!block) return;
    let news = null, kwCfg = null;
    try {
      const r = await fetch('data/v2/news.json', { cache: 'no-cache' });
      if (r.ok) news = await r.json();
    } catch (e) { /* 無 news.json → 空狀態 */ }
    try {
      const r2 = await fetch(NEWS_KW_RAW, { cache: 'no-cache' });
      if (r2.ok) kwCfg = await r2.json();
    } catch (e) { /* 關鍵字清單缺 → 只列新聞 */ }

    const items = (news && news.items) || [];
    const kws = (kwCfg && kwCfg.keywords) || [];
    document.getElementById('news-meta').textContent =
      `${items.length} 則命中 · 關鍵字 ${kws.length} 組`;
    document.getElementById('news-list').innerHTML = items.length
      ? items.map(newsRow).join('')
      : '<div class="nw-empty">今日無命中新聞</div>';
    const fetched = news && news.generated_at ? news.generated_at.slice(5, 16).replace('T', ' ') : '';
    document.getElementById('news-kwfoot').innerHTML =
      `<span class="nwk-label">🔑 關鍵字</span>`
      + kws.map(k => `<span class="nwk-chip">${esc(k)}</span>`).join('')
      + `<a class="nwk-add" href="${NEWS_KW_EDIT}" target="_blank" rel="noopener">+ 新增</a>`
      + (fetched ? `<span class="nwk-gen">抓取 ${esc(fetched)}</span>` : '');

    // 折疊:預設由模板參數(熱鬧日開/安靜日收);使用者操作記 localStorage
    let open;
    try { open = localStorage.getItem('newsFold'); } catch (e) { open = null; }
    if (open === null || open === undefined) {
      open = block.dataset.defaultOpen === '1' ? 'open' : 'closed';
    }
    function apply() {
      const isOpen = open === 'open';
      document.getElementById('news-list').style.display = isOpen ? '' : 'none';
      document.getElementById('news-kwfoot').style.display = isOpen ? '' : 'none';
      document.getElementById('news-caret').textContent = isOpen ? '▴' : '▾';
      document.getElementById('news-peek').textContent =
        (!isOpen && items.length) ? items.slice(0, 2).map(i => i.title).join(' / ') : '';
    }
    document.getElementById('news-head').addEventListener('click', () => {
      open = open === 'open' ? 'closed' : 'open';
      try { localStorage.setItem('newsFold', open); } catch (e) { /* ignore */ }
      apply();
    });
    apply();
    block.hidden = false;
  }

  async function initMacro() {
    const header = document.querySelector('header.page-header');
    const hub = document.getElementById('events-hub');
    if (!header || !hub) return;         // 只在儀表板注入
    try {
      const r = await fetch('data/v2/macro.json', { cache: 'no-cache' });
      if (!r.ok) return;
      injectMacroBar(header, await r.json());
    } catch (e) { /* macro 缺 → 不注入 */ }
  }

  // Batch4 深連結:任何頁面以 #card-SYMBOL 落地(排行條 C/D、事件股名、其餘品項 chip、
  // 徽章連結)→ 自動展開該卡(含外層板塊折疊)、平滑捲動、邊框高亮 1.2s——
  // 與儀表板內錨點同體驗(用戶 Batch3 實測回報:落地後要自己滾動找)。
  function initDeepLink() {
    const h = location.hash;
    if (!h || !h.startsWith('#card-')) return;
    const card = document.getElementById(h.slice(1));
    if (!card) return;
    // 展開自身 + 所有外層 <details>(watchlist 板塊折疊)
    let el = card;
    while (el) {
      if (el.tagName === 'DETAILS') el.open = true;
      el = el.parentElement ? el.parentElement.closest('details') : null;
    }
    setTimeout(function () {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('rb-flash');
      setTimeout(() => card.classList.remove('rb-flash'), 1200);
    }, 150);                                   // 等展開後再定位
  }

  // §9 排行條:點擊平滑捲動至戰區卡 + 邊框高亮 1.2s 淡出
  function initRankingBar() {
    document.querySelectorAll('.rb-name[data-rb-target]').forEach(a => {
      a.addEventListener('click', function (ev) {
        const card = document.getElementById('card-' + a.dataset.rbTarget);
        if (!card) return;               // 無本頁卡(C/D)→ 走原 href 跳 Watchlist
        ev.preventDefault();
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.classList.add('rb-flash');
        setTimeout(() => card.classList.remove('rb-flash'), 1200);
      });
    });
  }

  function boot() { init(); initMacro(); initNews(); initRankingBar(); initDeepLink(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
