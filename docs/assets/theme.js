/**
 * theme.js — 深/淺主題切換鈕(stage10 Batch 1)
 * 深色為預設(:root);淺色 = <html data-theme="light">(tokens.css)。
 * pre-paint 設定由 head inline script 負責(asset_version._THEME_BOOT),
 * 本檔只負責右上角切換鈕 + localStorage 記憶。
 */
(function () {
  'use strict';
  function cur() { return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'; }
  function label() { return cur() === 'light' ? '🌙 深色' : '☀️ 淺色'; }

  function mount() {
    if (document.querySelector('.theme-toggle')) return;
    var btn = document.createElement('button');
    btn.className = 'theme-toggle';
    btn.type = 'button';
    btn.title = '切換深/淺主題';
    btn.textContent = label();
    btn.addEventListener('click', function () {
      var next = cur() === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('theme', next); } catch (e) { /* 私密模式等,忽略 */ }
      btn.textContent = label();
    });
    document.body.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
