(() => {
  'use strict';

  const tg = window.Telegram?.WebApp ?? null;

  const BACKEND_META = (
    document.querySelector('meta[name="spicespace-backend"]')?.content || ''
  ).trim();
  const BACKEND_URL = (BACKEND_META || window.location.origin || '').replace(/\/+$/, '');
  const BOT_USERNAME = (
    document.querySelector('meta[name="spicespace-bot-username"]')?.content || 'SpiceSpacebot'
  ).replace(/^@/, '');

  const DEMO_TG = new URLSearchParams(window.location.search).get('telegram_id') || '';

  const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  const WEEKDAYS_SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

  let profile = null;
  let tasks = [];

  function withDemoQuery(path) {
    if (!DEMO_TG || tg?.initData) return path;
    const sep = path.includes('?') ? '&' : '?';
    return `${path}${sep}telegram_id=${encodeURIComponent(DEMO_TG)}`;
  }

  async function apiFetch(path, opts = {}) {
    if (!BACKEND_URL) {
      return { ok: false, status: 0, json: async () => ({}) };
    }
    const headers = { ...(opts.headers || {}) };
    if (tg?.initData && !headers.Authorization) {
      headers.Authorization = `tma ${tg.initData}`;
    }
    return fetch(`${BACKEND_URL}${withDemoQuery(path)}`, { ...opts, headers, cache: 'no-store' });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function localISODate(d = new Date()) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const da = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${da}`;
  }

  function parseISODate(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d);
  }

  function dowKeyFromDate(d) {
    return ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'][d.getDay()];
  }

  function mondayIndex(d = new Date()) {
    return (d.getDay() + 6) % 7;
  }

  function formatTodayTag() {
    const d = new Date();
    return `${WEEKDAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }

  function pluralizeDays(n) {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return 'дней';
    if (b > 1 && b < 5) return 'дня';
    if (b === 1) return 'день';
    return 'дней';
  }

  function applyGreeting() {
    const el = document.getElementById('greeting');
    if (!el) return;
    const h = new Date().getHours();
    let g = 'Доброе утро,';
    if (h >= 12 && h < 18) g = 'Добрый день,';
    else if (h >= 18 && h < 23) g = 'Добрый вечер,';
    else if (h >= 23 || h < 5) g = 'Доброй ночи,';
    el.textContent = g;
  }

  function pickName(user, prof) {
    if (prof?.name) return String(prof.name).trim();
    const n = (user?.first_name || user?.username || '').trim();
    return n || 'друг';
  }

  function initTelegram() {
    if (!tg) return;
    document.body.classList.add('tg-app');
    try { tg.ready(); } catch (_) {}
    try { tg.expand(); } catch (_) {}
    try { tg.disableVerticalSwipes?.(); } catch (_) {}
    try {
      tg.setHeaderColor?.('#F5F4F0');
      tg.setBackgroundColor?.('#F5F4F0');
    } catch (_) {}
    const h = tg.viewportStableHeight || tg.viewportHeight;
    if (h && Number.isFinite(h)) {
      document.documentElement.style.setProperty('--tg-app-height', `${h}px`);
    }
  }

  function showGate() {
    document.getElementById('gate').hidden = false;
    document.getElementById('main').hidden = true;
  }

  function showMain() {
    document.getElementById('gate').hidden = true;
    document.getElementById('main').hidden = false;
  }

  function weeklyGoalText(prof) {
    const wg = (prof.weekly_goal || '').trim();
    if (wg) return wg;
    const tt = (prof.today_task || '').trim();
    if (tt) return tt;
    const method = (prof.method || '').trim();
    if (method) return method;
    const main = (prof.main_goal || prof.final_goal || '').trim();
    if (main.length > 100) return `${main.slice(0, 97)}…`;
    return main || 'Шаги на эту неделю';
  }

  function effectiveStreak(prof) {
    return Math.max(0, Number(prof.display_streak ?? prof.streak ?? 0));
  }

  function taskAppliesToday(task, todayIso) {
    if (task.status && task.status !== 'active') return false;
    const r = task.repeat || 'none';
    if (r === 'daily') return true;
    if (r === 'weekly') {
      const days = Array.isArray(task.days_of_week) ? task.days_of_week : [];
      return days.includes(dowKeyFromDate(parseISODate(todayIso)));
    }
    return (task.date || '') === todayIso;
  }

  function todayTasksList(prof, list) {
    const todayIso = localISODate();
    const out = list
      .filter((t) => taskAppliesToday(t, todayIso))
      .sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')));

    const focus = (prof.today_task || '').trim();
    if (!focus) return out;

    const dup = out.some((t) => {
      const title = (t.title || '').trim().toLowerCase();
      return title && (title === focus.toLowerCase() || focus.toLowerCase().includes(title));
    });
    if (!dup) {
      out.unshift({
        id: '__today_focus__',
        title: focus,
        done: Boolean(prof.today_completed),
        virtual: true,
      });
    }
    return out;
  }

  function renderHeader(user, prof) {
    document.getElementById('user-name').textContent = pickName(user, prof);
    const avatar = document.getElementById('avatar');
    const photo = user?.photo_url || tg?.initDataUnsafe?.user?.photo_url;
    if (avatar && photo) {
      avatar.style.backgroundImage = `url("${String(photo).replace(/"/g, '%22')}")`;
    }
  }

  function renderMonthGoal(prof) {
    const text = (prof.main_goal || prof.final_goal || prof.raw_goal || '—').trim();
    document.getElementById('month-goal').textContent = text;
  }

  function renderWeekCard(prof) {
    const week = Number(prof.current_week || 1);
    const pct = Math.max(0, Math.min(100, Number(prof.weekly_score || 0)));

    document.getElementById('week-badge').textContent = `Неделя ${week}`;
    document.getElementById('week-goal').textContent = weeklyGoalText(prof);
    document.getElementById('week-pct').textContent = `${pct}%`;

    const fill = document.getElementById('week-fill');
    if (fill) {
      fill.style.width = '0%';
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          fill.style.width = `${pct}%`;
        });
      });
    }
  }

  function renderTasks(prof, list) {
    const host = document.getElementById('task-list');
    const todayItems = todayTasksList(prof, list);

    if (!todayItems.length) {
      host.innerHTML = '<p class="task-empty">Пока нет задач — обсуди шаг с ботом утром.</p>';
      return;
    }

    host.innerHTML = todayItems.map((t) => {
      const done = Boolean(t.done);
      const id = escapeHtml(t.id);
      return `
        <div class="task-row">
          <button type="button" class="task-check${done ? ' done' : ''}" data-id="${id}" ${done ? 'disabled' : ''} aria-label="Отметить выполненным">${done ? '✓' : ''}</button>
          <span class="task-text${done ? ' done' : ''}">${escapeHtml(t.title || '')}</span>
        </div>`;
    }).join('');
  }

  function renderStreak(prof) {
    const streak = effectiveStreak(prof);
    const todayIdx = mondayIndex();
    const countEl = document.getElementById('streak-count');
    if (countEl) {
      countEl.textContent = streak > 0 ? `${streak} ${pluralizeDays(streak)} 🔥` : 'начни сегодня';
    }

    const host = document.getElementById('streak-dots');
    if (!host) return;

    const parts = [];
    for (let i = 0; i < 7; i++) {
      let cls = 'streak-dot';
      if (i === todayIdx) {
        cls += ' today';
        if (streak > 0 && i >= todayIdx - streak) cls += ' done-today';
      } else if (i < todayIdx && i >= todayIdx - streak) {
        cls += ' done';
      }
      const delay = i * 0.05;
      parts.push(`<div class="${cls}" style="animation-delay:${delay}s"></div>`);
    }
    host.innerHTML = parts.join('');
  }

  function renderAll(user) {
    if (!profile) return;
    applyGreeting();
    renderHeader(user, profile);
    renderMonthGoal(profile);
    renderWeekCard(profile);
    renderTasks(profile, tasks);
    renderStreak(profile);
    document.getElementById('today-date').textContent = formatTodayTag();
  }

  async function fetchProfile() {
    const resp = await apiFetch('/api/profile');
    if (resp.status === 401 || resp.status === 404) return { ok: false, status: resp.status };
    if (!resp.ok) return { ok: false, status: resp.status };
    const data = await resp.json();
    return { ok: true, profile: data.profile || data, user: data.user || null };
  }

  async function fetchTasks() {
    const resp = await apiFetch('/api/tasks');
    if (!resp.ok) return [];
    const data = await resp.json();
    return Array.isArray(data.tasks) ? data.tasks : [];
  }

  async function markDay() {
    const paths = ['/api/profile/mark-day', '/api/mark-day'];
    for (const path of paths) {
      const resp = await apiFetch(path, { method: 'POST' });
      if (resp.ok) {
        const data = await resp.json();
        if (data.profile) return data.profile;
      }
      if (resp.status !== 404) break;
    }
    return null;
  }

  async function completeTask(id) {
    if (id === '__today_focus__') {
      const updated = await markDay();
      if (updated) profile = updated;
      else if (profile) profile.today_completed = true;
      return;
    }
    const resp = await apiFetch(`/api/tasks/${encodeURIComponent(id)}/done`, { method: 'POST' });
    if (resp.ok) tasks = await fetchTasks();
  }

  function bindEvents() {
    document.getElementById('gate-open-bot')?.addEventListener('click', () => {
      const link = `https://t.me/${BOT_USERNAME}`;
      if (tg?.openTelegramLink) {
        try { tg.openTelegramLink(link); return; } catch (_) {}
      }
      window.open(link, '_blank');
    });

    document.getElementById('task-list')?.addEventListener('click', async (e) => {
      const btn = e.target.closest('.task-check');
      if (!btn || btn.disabled) return;
      const id = btn.getAttribute('data-id');
      if (!id) return;
      btn.disabled = true;
      await completeTask(id);
      renderTasks(profile, tasks);
      renderStreak(profile);
    });

    document.getElementById('btn-reset')?.addEventListener('click', async () => {
      if (!confirm('Сбросить всю память бота? Это удалит твой профиль и историю.')) return;
      await apiFetch('/api/profile/reset', { method: 'POST' });
      if (tg) tg.close();
    });

    document.getElementById('btn-subscription')?.addEventListener('click', () => {
      alert('Подписка скоро будет доступна 💳');
    });

    document.getElementById('btn-stop')?.addEventListener('click', async () => {
      await apiFetch('/api/profile/stop', { method: 'POST' });
      alert('Бот остановлен. Напиши /start чтобы возобновить.');
      if (tg) tg.close();
    });
  }

  async function start() {
    initTelegram();
    bindEvents();

    if (!BACKEND_URL) {
      showGate();
      return;
    }

    if (!tg?.initData && !DEMO_TG) {
      showGate();
      return;
    }

    const result = await fetchProfile();
    if (!result.ok) {
      showGate();
      return;
    }

    profile = result.profile;
    showMain();

    const marked = await markDay();
    if (marked) profile = marked;

    tasks = await fetchTasks();
    renderAll(result.user || tg?.initDataUnsafe?.user);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
