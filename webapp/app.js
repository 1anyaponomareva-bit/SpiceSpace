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
  let calendarData = null;
  let activeTab = 'home';

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

  function haptic(type = 'light') {
    try { tg?.HapticFeedback?.impactOccurred?.(type); } catch (_) {}
  }

  function hapticSuccess() {
    try { tg?.HapticFeedback?.notificationOccurred?.('success'); } catch (_) {}
  }

  function setCanEditName(enabled) {
    const btn = document.getElementById('edit-name-btn');
    if (btn) btn.hidden = !enabled;
  }

  function setCanEditTimes(enabled) {
    const btn = document.getElementById('edit-times-btn');
    if (btn) btn.hidden = !enabled;
  }

  function formatTimeHHMM(raw, fallback) {
    const s = String(raw || '').trim();
    const m = s.match(/^(\d{1,2}):(\d{2})/);
    if (!m) return fallback;
    return `${String(Number(m[1])).padStart(2, '0')}:${m[2]}`;
  }

  function formatTime(hhmm) {
    if (!hhmm) return '';
    const parts = String(hhmm).trim().match(/^(\d{1,2}):(\d{2})/);
    if (!parts) return String(hhmm);
    const h = Number(parts[1]);
    const m = Number(parts[2]);
    const period = h >= 12 ? 'PM' : 'AM';
    const hour = h % 12 || 12;
    return `${hour}:${String(m).padStart(2, '0')} ${period}`;
  }

  function formatTimeForDisplay(hhmm, fallback) {
    return formatTime(formatTimeHHMM(hhmm, fallback));
  }

  function profileMorningTime(prof) {
    return formatTimeHHMM(prof?.morning_time || prof?.daily_time, '09:00');
  }

  function profileEveningTime(prof) {
    return formatTimeHHMM(prof?.evening_time, '21:00');
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

  function buildDemoProfile() {
    return {
      name: 'Привет! 👋',
      main_goal: 'Открой через бота чтобы увидеть свои цели',
      weekly_goal: '',
      streak: 0,
      display_streak: 0,
      current_week: 1,
      weekly_score: 0,
      morning_time: '09:00',
      evening_time: '21:00',
    };
  }

  function openBotChat() {
    const link = `https://t.me/${BOT_USERNAME}?start=webapp`;
    if (tg?.openTelegramLink) {
      try { tg.openTelegramLink(link); return; } catch (_) {}
    }
    window.open(link, '_blank');
  }

  function showSyncBanner() {
    const el = document.getElementById('sync-banner');
    if (el) el.hidden = false;
  }

  function hideSyncBanner() {
    const el = document.getElementById('sync-banner');
    if (el) el.hidden = true;
  }

  function showMain() {
    document.getElementById('empty-state').hidden = true;
    const home = document.getElementById('screen-home');
    if (home) {
      home.hidden = false;
      home.classList.add('screen--active');
    }
    const tabBar = document.getElementById('tab-bar');
    if (tabBar) tabBar.hidden = false;
  }

  function showEmptyState() {
    document.getElementById('empty-state').hidden = false;
    const home = document.getElementById('screen-home');
    if (home) {
      home.hidden = true;
      home.classList.remove('screen--active');
    }
    const tabBar = document.getElementById('tab-bar');
    if (tabBar) tabBar.hidden = true;
    hideSyncBanner();
    document.getElementById('settings-block')?.classList.add('loaded');
  }

  function switchTab(tab) {
    activeTab = tab;
    const home = document.getElementById('screen-home');
    const cal = document.getElementById('screen-calendar');
    document.querySelectorAll('.tab-bar__btn').forEach((btn) => {
      const on = btn.getAttribute('data-tab') === tab;
      btn.classList.toggle('tab-bar__btn--active', on);
      btn.setAttribute('aria-current', on ? 'page' : 'false');
    });
    if (home) {
      home.classList.toggle('screen--active', tab === 'home');
      home.hidden = tab !== 'home';
    }
    if (cal) {
      cal.classList.toggle('screen--active', tab === 'calendar');
      cal.hidden = tab !== 'calendar';
    }
    if (tab === 'calendar') renderCalendar();
    haptic('light');
  }

  function isTaskCompletedStrict(prof) {
    const tc = prof?.task_completed;
    return tc === true || tc === 'true';
  }

  function calendarCellClass(day) {
    if (day.is_future) return 'calendar-cell--future';
    if (day.is_today) {
      if (day.task_completed === 'true') return 'calendar-cell--done calendar-cell--today';
      if (day.task_completed === 'partial') return 'calendar-cell--partial calendar-cell--today';
      if (day.task_completed === 'false') return 'calendar-cell--miss calendar-cell--today';
      return 'calendar-cell--today';
    }
    if (day.task_completed === 'true') return 'calendar-cell--done';
    if (day.task_completed === 'partial') return 'calendar-cell--partial';
    if (day.task_completed === 'false') return 'calendar-cell--miss';
    return 'calendar-cell--future';
  }

  function renderCalendar() {
    const host = document.getElementById('calendar-grid');
    const badge = document.getElementById('calendar-week-badge');
    if (!host || !calendarData) return;

    const cw = Number(calendarData.current_week || profile?.current_week || 1);
    if (badge) badge.textContent = `Неделя ${cw} из 12`;

    const days = Array.isArray(calendarData.days) ? calendarData.days : [];
    const rows = [];
    for (let w = 0; w < 12; w++) {
      const slice = days.slice(w * 7, w * 7 + 7);
      const cells = slice.map((d) => `<div class="calendar-cell ${calendarCellClass(d)}" title="${escapeHtml(d.date || '')}"></div>`).join('');
      rows.push(
        `<div class="calendar-row${w + 1 === cw ? ' calendar-row--current' : ''}">` +
        `<span class="calendar-row-label">Нед ${w + 1}</span>${cells}</div>`
      );
    }
    host.innerHTML = rows.join('');
  }

  async function fetchCalendar() {
    const resp = await apiFetch('/api/calendar');
    if (!resp.ok) return null;
    const data = await resp.json();
    return data;
  }

  function isNoInitData() {
    return !tg?.initData && !DEMO_TG;
  }


  function startDemoMode(user) {
    profile = buildDemoProfile();
    tasks = [];
    setCanEditName(false);
    setCanEditTimes(false);
    showSyncBanner();
    showMain();
    renderAll(user);
    document.querySelector('.settings-block')?.classList.add('loaded');
  }

  const TODAY_TASK_FALLBACK = 'Задача будет поставлена утром 🌅';

  function normalizeGoalText(text) {
    return String(text || '').trim().toLowerCase().replace(/\s+/g, ' ');
  }

  function taskEqualsWeekly(task, weekly) {
    const a = normalizeGoalText(task);
    const b = normalizeGoalText(weekly);
    if (!a || !b) return false;
    if (a === b) return true;
    if (a.length >= 12 && b.length >= 12 && (a.includes(b) || b.includes(a))) return true;
    return false;
  }

  function isValidTask(task) {
    if (!task) return false;
    if (task.length > 150) return false;
    const invalidStarts = ['Мне кажется', 'Я думаю', 'Привет', 'Слушай', 'Кстати'];
    if (invalidStarts.some((s) => task.startsWith(s))) return false;
    if (task.includes('?') && task.length < 30) return false;
    const low = task.toLowerCase();
    if (
      /^привет[,\s!👋]/.test(low)
      || low.includes('доброе утро')
      || low.includes('давай начн')
      || low.includes('продуктивн')
      || (low.match(/\?/g) || []).length >= 2
    ) {
      return false;
    }
    return true;
  }

  function displayTodayTask(prof) {
    const raw = (prof.today_task || '').trim();
    if (!isValidTask(raw)) return '';
    if (taskEqualsWeekly(raw, prof.weekly_goal)) return '';
    return raw;
  }

  function weeklyGoalText(prof) {
    const wg = (prof.weekly_goal || '').trim();
    if (wg) return wg;
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

    const focus = displayTodayTask(prof);
    if (!focus) return out;

    const dup = out.some((t) => {
      const title = (t.title || '').trim().toLowerCase();
      return title && (title === focus.toLowerCase() || focus.toLowerCase().includes(title));
    });
    if (!dup) {
      out.unshift({
        id: '__today_focus__',
        title: focus,
        done: isTaskCompletedStrict(prof),
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

    document.getElementById('week-badge').textContent = `Неделя ${week} из 12`;
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
      const emptyMsg = displayTodayTask(prof)
        ? 'Пока нет задач — обсуди шаг с ботом утром.'
        : TODAY_TASK_FALLBACK;
      host.innerHTML = `<p class="task-empty">${escapeHtml(emptyMsg)}</p>`;
      return;
    }

    host.innerHTML = todayItems.map((t) => {
      const done = Boolean(t.done);
      const id = escapeHtml(t.id);
      const timeLabel = t.time ? formatTimeForDisplay(t.time, '') : '';
      const timeHtml = timeLabel
        ? `<span class="task-time">${escapeHtml(timeLabel)}</span>`
        : '';
      return `
        <div class="task-row">
          <button type="button" class="task-check${done ? ' done' : ''}" data-id="${id}" ${done ? 'disabled' : ''} aria-label="Отметить выполненным">${done ? '✓' : ''}</button>
          ${timeHtml}
          <span class="task-text${done ? ' done' : ''}">${escapeHtml(t.title || '')}</span>
        </div>`;
    }).join('');
  }

  function renderStreak(prof) {
    const streak = effectiveStreak(prof);
    const countEl = document.getElementById('streak-count');
    if (countEl) {
      countEl.textContent = streak > 0 ? `${streak} ${pluralizeDays(streak)} 🔥` : 'начни сегодня';
    }

    const host = document.getElementById('streak-dots');
    if (!host) return;

    // 7 dots left→right: previous days black, today green, rest grey (not a week calendar)
    const filled = Math.min(7, Math.max(0, streak));
    const parts = [];
    for (let i = 0; i < 7; i++) {
      let cls = 'streak-dot';
      if (filled > 0) {
        if (i < filled - 1) cls += ' done';
        else if (i === filled - 1) cls += ' today';
      }
      parts.push(`<div class="${cls}" style="animation-delay:${i * 0.05}s"></div>`);
    }
    host.innerHTML = parts.join('');
  }

  function renderTimes(prof) {
    const morningEl = document.getElementById('morning-time-val');
    const eveningEl = document.getElementById('evening-time-val');
    if (morningEl) {
      morningEl.textContent = formatTimeForDisplay(
        prof?.morning_time || prof?.daily_time,
        '09:00',
      );
    }
    if (eveningEl) {
      eveningEl.textContent = formatTimeForDisplay(prof?.evening_time, '21:00');
    }
  }

  function renderAll(user) {
    if (!profile) return;
    applyGreeting();
    renderHeader(user, profile);
    renderMonthGoal(profile);
    renderWeekCard(profile);
    renderTasks(profile, tasks);
    renderStreak(profile);
    renderTimes(profile);
    document.getElementById('today-date').textContent = formatTodayTag();
  }

  function syncTimezone() {
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (!tz) return;
      apiFetch('/api/profile/timezone', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timezone: tz }),
      }).catch(() => {});
    } catch (_) {}
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

  async function markDay(taskCompleted = 'true') {
    const paths = ['/api/profile/mark-day', '/api/mark-day'];
    for (const path of paths) {
      const resp = await apiFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_completed: taskCompleted }),
      });
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
      const updated = await markDay('true');
      if (updated) profile = updated;
      else if (profile) {
        profile.task_completed = 'true';
        profile.today_completed = true;
      }
      calendarData = await fetchCalendar();
      return;
    }
    const resp = await apiFetch(`/api/tasks/${encodeURIComponent(id)}/done`, { method: 'POST' });
    if (resp.ok) tasks = await fetchTasks();
  }

  async function saveEditName() {
    const input = document.getElementById('edit-name-input');
    const newName = (input?.value || '').trim().slice(0, 50);
    if (!newName) return;

    const saveBtn = document.getElementById('edit-name-save');
    if (saveBtn) saveBtn.disabled = true;

    const resp = await apiFetch('/api/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName }),
    });

    if (saveBtn) saveBtn.disabled = false;
    if (!resp.ok) return;

    const data = await resp.json();
    if (data.profile) profile = data.profile;
    else if (profile) profile.name = newName;

    document.getElementById('user-name').textContent = newName;
    closeEditNameSheet();
    hapticSuccess();
  }

  function openEditNameSheet() {
    const sheet = document.getElementById('edit-name-sheet');
    const input = document.getElementById('edit-name-input');
    if (!sheet || !input) return;
    const current = (profile?.name || document.getElementById('user-name')?.textContent || '').trim();
    input.value = current === '—' ? '' : current;
    sheet.hidden = false;
    setTimeout(() => input.focus(), 50);
    haptic('light');
  }

  function closeEditNameSheet() {
    const sheet = document.getElementById('edit-name-sheet');
    if (sheet) sheet.hidden = true;
  }

  function bindEditName() {
    document.getElementById('edit-name-btn')?.addEventListener('click', openEditNameSheet);
    document.getElementById('edit-name-backdrop')?.addEventListener('click', closeEditNameSheet);
    document.getElementById('edit-name-cancel')?.addEventListener('click', closeEditNameSheet);
    document.getElementById('edit-name-save')?.addEventListener('click', saveEditName);
    document.getElementById('edit-name-input')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') saveEditName();
    });
  }

  async function saveEditTimes() {
    const morning = document.getElementById('et-morning')?.value || '';
    const evening = document.getElementById('et-evening')?.value || '';
    if (!morning || !evening) return;

    const saveBtn = document.getElementById('edit-times-save');
    if (saveBtn) saveBtn.disabled = true;

    const resp = await apiFetch('/api/profile/times', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ morning_time: morning, evening_time: evening }),
    });

    if (saveBtn) saveBtn.disabled = false;
    if (!resp.ok) return;

    const data = await resp.json();
    if (data.profile) profile = data.profile;
    else if (profile) {
      profile.morning_time = morning;
      profile.daily_time = morning;
      profile.evening_time = evening;
    }

    renderTimes(profile);
    closeEditTimesSheet();
    hapticSuccess();
  }

  function openEditTimesSheet() {
    const sheet = document.getElementById('edit-times-sheet');
    const morningInput = document.getElementById('et-morning');
    const eveningInput = document.getElementById('et-evening');
    if (!sheet || !morningInput || !eveningInput) return;
    morningInput.value = profileMorningTime(profile);
    eveningInput.value = profileEveningTime(profile);
    sheet.hidden = false;
    haptic('light');
  }

  function closeEditTimesSheet() {
    const sheet = document.getElementById('edit-times-sheet');
    if (sheet) sheet.hidden = true;
  }

  function bindEditTimes() {
    document.getElementById('edit-times-btn')?.addEventListener('click', openEditTimesSheet);
    document.getElementById('edit-times-backdrop')?.addEventListener('click', closeEditTimesSheet);
    document.getElementById('edit-times-cancel')?.addEventListener('click', closeEditTimesSheet);
    document.getElementById('edit-times-save')?.addEventListener('click', saveEditTimes);
  }

  function bindEvents() {
    document.getElementById('sync-banner-open')?.addEventListener('click', openBotChat);
    document.getElementById('empty-open-bot')?.addEventListener('click', openBotChat);

    document.querySelectorAll('.tab-bar__btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        if (tab) switchTab(tab);
      });
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
    bindEditName();
    bindEditTimes();

    const tgUser = tg?.initDataUnsafe?.user || null;

    if (isNoInitData()) {
      startDemoMode(tgUser);
      return;
    }

    if (!BACKEND_URL) {
      startDemoMode(tgUser);
      return;
    }

    const result = await fetchProfile();
    if (!result.ok) {
      if (result.status === 404 && (tg?.initData || DEMO_TG)) {
        showEmptyState();
        return;
      }
      startDemoMode(tgUser);
      return;
    }

    profile = result.profile;
    hideSyncBanner();
    showMain();
    setCanEditName(true);
    setCanEditTimes(true);

    tasks = await fetchTasks();
    calendarData = await fetchCalendar();
    renderAll(result.user || tgUser);
    syncTimezone();
    document.querySelector('.settings-block')?.classList.add('loaded');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
