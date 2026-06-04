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

  const SPARK_OFFSETS = [
    [-118, -82],
    [108, -90],
    [-150, 10],
    [150, 18],
    [-90, 86],
    [96, 92],
    [-44, -126],
    [42, -132],
    [-178, -48],
    [176, -38],
    [-30, 132],
    [32, 126],
    [-132, 58],
    [132, 60],
    [-70, -58],
    [72, -54],
  ];

  let opened = false;
  let sparksReady = false;
  let bound = false;

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
    for (const [x, y] of SPARK_OFFSETS) {
      const s = document.createElement('span');
      s.className = 'spark';
      s.style.setProperty('--x', `${x}px`);
      s.style.setProperty('--y', `${y}px`);
      overlay.appendChild(s);
    }
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
    if (tg?.ready) tg.ready();
    if (tg?.expand) tg.expand();
  }

  function setFortuneText(data) {
    const textEl = document.getElementById('fortune-text');
    const subEl = document.getElementById('fortune-sub');
    if (textEl && data?.text) textEl.textContent = data.text;
    if (subEl && data?.sub) subEl.textContent = data.sub;
  }

  async function downloadPaper() {
    const paper = document.getElementById('fortune-paper');
    if (!paper || typeof html2canvas !== 'function') return;
    try {
      const canvas = await html2canvas(paper, {
        backgroundColor: '#faf6ee',
        scale: 2,
        useCORS: true,
      });
      const link = document.createElement('a');
      link.download = `spicespace-fortune-${localISODate()}.png`;
      link.href = canvas.toDataURL('image/png');
      link.click();
    } catch (_) {}
  }

  function bindOverlay(overlay) {
    const cookie = document.getElementById('fortune-cookie');
    const btnGo = document.getElementById('fortune-btn-go');
    const btnSave = document.getElementById('fortune-btn-save');

    cookie?.addEventListener('click', () => {
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

  async function tryShow() {
    const overlay = document.getElementById('fortune-overlay');
    if (!overlay || wasSeenToday()) return;

    const data = await apiFetch('/api/fortune/today');
    if (!data?.text) return;

    setFortuneText(data);
    ensureSparks(overlay);
    if (!bound) {
      bound = true;
      bindOverlay(overlay);
    }
    showOverlay(overlay);
  }

  window.SpiceFortune = { tryShow, markSeenToday };
})();
