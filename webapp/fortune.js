(() => {
  'use strict';

  const tg = window.Telegram?.WebApp ?? null;
  const BACKEND_URL = (
    document.querySelector('meta[name="spicespace-backend"]')?.content ||
    window.location.origin ||
    ''
  )
    .trim()
    .replace(/\/+$/, '');

  const FORTUNE_TEST_IDS = new Set(['8412438788']);

  const SPARK_OFFSETS = [
    [-118, -82],
    [108, -90],
    [-150, 10],
    [150, 18],
    [-90, 86],
    [96, 92],
    [-44, -126],
    [42, -132],
  ];

  let opened = false;
  let sparksReady = false;
  let bound = false;

  function fortuneT(key) {
    return (typeof window.t === 'function' ? window.t(key) : null) || key;
  }

  function applyFortuneI18n() {
    const title = document.querySelector('#fortune-overlay .fortune-title');
    if (title) title.innerHTML = fortuneT('fortune_title_html');
    const tap = document.querySelector('#fortune-overlay .tap-hint');
    if (tap) tap.textContent = fortuneT('fortune_tap');
    const hit = document.getElementById('fortune-cookie-hit');
    if (hit) hit.setAttribute('aria-label', fortuneT('fortune_open_aria'));
    const btnSave = document.getElementById('fortune-btn-save');
    if (btnSave) btnSave.textContent = fortuneT('fortune_btn_save');
    const btnGo = document.getElementById('fortune-btn-go');
    if (btnGo) btnGo.textContent = fortuneT('fortune_btn_go');
    const sigEl = document.getElementById('fortune-signature');
    if (sigEl && sigEl.dataset.fromApi !== '1') {
      sigEl.textContent = fortuneT('fortune_signature');
    }
    const testBtn = document.getElementById('btn-fortune-test');
    if (testBtn && !testBtn.hidden) {
      testBtn.textContent = fortuneT('fortune_test_btn');
    }
  }

  function getTelegramId() {
    const fromUrl = new URLSearchParams(window.location.search).get('telegram_id');
    if (fromUrl && /^\d+$/.test(String(fromUrl).trim())) return String(fromUrl).trim();
    const user = tg?.initDataUnsafe?.user;
    if (user?.id) return String(user.id);
    return null;
  }

  function localISODate(d = new Date()) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const da = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${da}`;
  }

  function seenStorageKey() {
    const uid = getTelegramId() || '0';
    return `spicespace_fortune_seen_v1_${uid}_${localISODate()}`;
  }

  function wasSeenToday() {
    try {
      return localStorage.getItem(seenStorageKey()) === '1';
    } catch (_) {
      return false;
    }
  }

  function markSeenToday() {
    try {
      localStorage.setItem(seenStorageKey(), '1');
    } catch (_) {}
  }

  function appendTelegramIdQuery(url) {
    if (tg?.initData || url.includes('telegram_id=')) return url;
    const tid = getTelegramId();
    if (!tid) return url;
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}telegram_id=${encodeURIComponent(tid)}`;
  }

  function isFortuneTester() {
    const tid = getTelegramId();
    return Boolean(tid && FORTUNE_TEST_IDS.has(tid));
  }

  async function apiFetch(path) {
    if (!BACKEND_URL) return null;
    const headers = {};
    if (tg?.initData) headers.Authorization = `tma ${tg.initData}`;
    let url = `${BACKEND_URL}${path.startsWith('/') ? path : `/${path}`}`;
    url = appendTelegramIdQuery(url);
    try {
      const resp = await fetch(url, { headers, cache: 'no-store' });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (_) {
      return null;
    }
  }

  function ensureSparks(overlay) {
    if (sparksReady) return;
    sparksReady = true;
    const stage = overlay.querySelector('.fortune-stage');
    if (!stage) return;
    for (const [x, y] of SPARK_OFFSETS) {
      const s = document.createElement('span');
      s.className = 'spark';
      s.style.setProperty('--x', `${x}px`);
      s.style.setProperty('--y', `${y}px`);
      stage.appendChild(s);
    }
  }

  function preloadBrokenCookie(overlay) {
    const img = overlay.querySelector('.cookie-broken');
    if (!img || img.dataset.loaded === '1') return;
    const src = img.getAttribute('data-src') || img.getAttribute('src');
    if (!src) return;
    img.src = src;
    img.dataset.loaded = '1';
  }

  function hideOverlay(overlay) {
    overlay.hidden = true;
    overlay.classList.remove('opened');
    overlay.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('fortune-open');
    opened = false;
  }

  function showOverlay(overlay) {
    overlay.hidden = false;
    overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('fortune-open');
    preloadBrokenCookie(overlay);
    if (tg?.ready) tg.ready();
    if (tg?.expand) tg.expand();
  }

  function setFortuneText(data) {
    const textEl = document.getElementById('fortune-text');
    const sigEl = document.getElementById('fortune-signature');
    if (textEl && data?.text) textEl.textContent = data.text;
    if (sigEl) {
      if (data?.sub) {
        sigEl.textContent = data.sub;
        sigEl.dataset.fromApi = '1';
      } else {
        delete sigEl.dataset.fromApi;
        sigEl.textContent = fortuneT('fortune_signature');
      }
    }
  }

  function wrapCanvasLines(ctx, text, x, y, maxWidth, lineHeight) {
    const words = String(text || '').split(/\s+/).filter(Boolean);
    let line = '';
    let cy = y;
    for (let i = 0; i < words.length; i += 1) {
      const test = line ? `${line} ${words[i]}` : words[i];
      if (ctx.measureText(test).width > maxWidth && line) {
        ctx.fillText(line, x, cy);
        line = words[i];
        cy += lineHeight;
      } else {
        line = test;
      }
    }
    if (line) ctx.fillText(line, x, cy);
    return cy;
  }

  function renderFortuneImage(text, signature) {
    const W = 900;
    const pad = 56;
    const innerW = W - pad * 2;
    const measure = document.createElement('canvas').getContext('2d');
    if (!measure) return null;
    measure.font = '400 34px Inter, system-ui, sans-serif';
    const words = String(text || '').split(/\s+/).filter(Boolean);
    const lines = [];
    let line = '';
    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (measure.measureText(test).width > innerW && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    if (line) lines.push(line);

    const sigLines = [];
    measure.font = 'italic 26px Inter, system-ui, sans-serif';
    const sigWords = String(signature || '').split(/\s+/).filter(Boolean);
    line = '';
    for (const word of sigWords) {
      const test = line ? `${line} ${word}` : word;
      if (measure.measureText(test).width > innerW && line) {
        sigLines.push(line);
        line = word;
      } else {
        line = test;
      }
    }
    if (line) sigLines.push(line);

    const bodyH = 52 + lines.length * 46 + 36 + sigLines.length * 34 + 48;
    const H = Math.max(640, bodyH + pad * 2);
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    ctx.fillStyle = '#faf6ee';
    ctx.fillRect(0, 0, W, H);

    const px = pad;
    const py = pad;
    const pw = W - pad * 2;
    const ph = H - pad * 2;
    ctx.fillStyle = '#f3ebdc';
    ctx.fillRect(px + 6, py + 8, pw, ph);
    ctx.fillStyle = '#faf6ee';
    ctx.fillRect(px, py, pw, ph);

    ctx.fillStyle = '#7a5c31';
    ctx.font = '700 20px Unbounded, Inter, system-ui, sans-serif';
    ctx.fillText('FORTUNE COOKIE', px + 28, py + 44);

    ctx.fillStyle = '#191919';
    ctx.font = '400 34px Inter, system-ui, sans-serif';
    let cy = py + 96;
    for (const ln of lines) {
      ctx.fillText(ln, px + 28, cy);
      cy += 46;
    }

    cy += 10;
    ctx.strokeStyle = 'rgba(25, 25, 25, 0.12)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(px + 28, cy);
    ctx.lineTo(px + pw - 28, cy);
    ctx.stroke();
    cy += 36;

    ctx.fillStyle = 'rgba(25, 25, 25, 0.58)';
    ctx.font = 'italic 26px Inter, system-ui, sans-serif';
    for (const ln of sigLines) {
      ctx.fillText(ln, px + 28, cy);
      cy += 34;
    }

    return canvas;
  }

  function triggerDownload(canvas) {
    return new Promise((resolve) => {
      canvas.toBlob(
        (blob) => {
          if (!blob) {
            resolve(false);
            return;
          }
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.download = `spicespace-fortune-${localISODate()}.png`;
          link.href = url;
          link.rel = 'noopener';
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(() => URL.revokeObjectURL(url), 3000);
          resolve(true);
        },
        'image/png',
        0.92,
      );
    });
  }

  async function downloadPaper() {
    const text = document.getElementById('fortune-text')?.textContent?.trim() || '';
    const signature =
      document.getElementById('fortune-signature')?.textContent?.trim() ||
      fortuneT('fortune_signature');
    const btn = document.getElementById('fortune-btn-save');
    if (btn) btn.disabled = true;
    try {
      const canvas = renderFortuneImage(text, signature);
      if (!canvas) return;
      await triggerDownload(canvas);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function bindOverlay(overlay) {
    const hit = document.getElementById('fortune-cookie-hit');
    const btnGo = document.getElementById('fortune-btn-go');
    const btnSave = document.getElementById('fortune-btn-save');

    hit?.addEventListener('click', () => {
      if (opened) return;
      opened = true;
      overlay.classList.add('opened');
    });

    btnSave?.addEventListener('click', () => {
      downloadPaper();
    });

    btnGo?.addEventListener('click', () => {
      markSeenToday();
      hideOverlay(overlay);
    });
  }

  function prepareOverlay(overlay) {
    opened = false;
    overlay.classList.remove('opened');
  }

  async function tryShow({ force = false } = {}) {
    const overlay = document.getElementById('fortune-overlay');
    if (!overlay) return;
    if (!force && wasSeenToday()) return;

    applyFortuneI18n();
    prepareOverlay(overlay);
    const path = force ? '/api/fortune/today?force=1' : '/api/fortune/today';
    let data = await apiFetch(path);
    if (!data?.text) {
      data = { text: fortuneT('fortune_fallback') };
    }

    setFortuneText(data);
    ensureSparks(overlay);
    if (!bound) {
      bound = true;
      bindOverlay(overlay);
    }
    showOverlay(overlay);
  }

  async function tryShowForce() {
    try {
      localStorage.removeItem(seenStorageKey());
    } catch (_) {}
    await tryShow({ force: true });
  }

  function initFortuneTestButton() {
    const btn = document.getElementById('btn-fortune-test');
    if (!btn || !isFortuneTester()) return;
    btn.hidden = false;
    btn.textContent = fortuneT('fortune_test_btn');
    btn.addEventListener('click', () => {
      tryShowForce();
    });
  }

  window.SpiceFortune = {
    tryShow,
    tryShowForce,
    markSeenToday,
    initFortuneTestButton,
    applyI18n: applyFortuneI18n,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFortuneTestButton, { once: true });
  } else {
    initFortuneTestButton();
  }
})();
