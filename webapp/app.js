(() => {
  'use strict';

  const tg = window.Telegram?.WebApp ?? null;

  const BACKEND_META = (
    document.querySelector('meta[name="spicespace-backend"]')?.content || ''
  ).trim();
  const BACKEND_URL = (BACKEND_META || window.location.origin || '').replace(/\/+$/, '');
  const BOT_USERNAME = (
    document.querySelector('meta[name="spicespace-bot-username"]')?.content || ''
  ).replace(/^@/, '');

  const DEMO_TG = new URLSearchParams(window.location.search).get('telegram_id') || '';

  let lastProfile = null;
  let tasksCache = [];
  let planWeekOffset = 0;
  let dayMarkedKey = '';

  const MONTHS_SHORT = [
    'янв', 'фев', 'мар', 'апр', 'май', 'июн',
    'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
  ];
  const WEEKDAY_LABELS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

  function withDemoQuery(path) {
    if (!DEMO_TG || (tg && tg.initData)) return path;
    const sep = path.includes('?') ? '&' : '?';
    return `${path}${sep}telegram_id=${encodeURIComponent(DEMO_TG)}`;
  }

  async function apiFetch(path, opts = {}) {
    if (!BACKEND_URL) {
      return { ok: false, status: 0, json: async () => ({}) };
    }
    const url = `${BACKEND_URL}${withDemoQuery(path)}`;
    const headers = { ...(opts.headers || {}) };
    if (tg?.initData && !headers.Authorization) {
      headers.Authorization = `tma ${tg.initData}`;
    }
    return fetch(url, { ...opts, headers, cache: 'no-store' });
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

  function addDaysISO(iso, n) {
    const dt = parseISODate(iso);
    dt.setDate(dt.getDate() + n);
    return localISODate(dt);
  }

  function dowKeyFromLocalDate(d) {
    return ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'][d.getDay()];
  }

  function taskAppliesOnDate(task, iso) {
    if (task.done || task.status !== 'active') return false;
    const r = task.repeat || 'none';
    if (r === 'daily') return true;
    if (r === 'weekly') {
      const days = Array.isArray(task.days_of_week) ? task.days_of_week : [];
      return days.includes(dowKeyFromLocalDate(parseISODate(iso)));
    }
    return (task.date || '') === iso;
  }

  function formatDayHeader(iso, todayIso) {
    const d = parseISODate(iso);
    const wd = WEEKDAY_LABELS[d.getDay()];
    const day = d.getDate();
    const mon = MONTHS_SHORT[d.getMonth()];
    if (iso === todayIso) return `Сегодня · ${wd} ${day} ${mon}`;
    const tomorrow = addDaysISO(todayIso, 1);
    if (iso === tomorrow) return `Завтра · ${wd} ${day} ${mon}`;
    return `${wd} ${day} ${mon}`;
  }

  function pluralize(n, forms) {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    if (b === 1) return forms[0];
    return forms[2];
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function cssEscapeUrl(url) {
    return String(url).replace(/"/g, '%22');
  }

  function haptic(type) {
    try {
      if (!tg?.HapticFeedback) return;
      if (type === 'select') tg.HapticFeedback.selectionChanged();
      else if (type === 'success') tg.HapticFeedback.notificationOccurred('success');
      else tg.HapticFeedback.impactOccurred('light');
    } catch (_) {}
  }

  function botLink() {
    return BOT_USERNAME ? `https://t.me/${BOT_USERNAME}` : '';
  }

  function openBotChat() {
    const link = botLink();
    if (!link) return;
    if (tg && typeof tg.openTelegramLink === 'function') {
      try { tg.openTelegramLink(link); return; } catch (_) {}
    }
    window.open(link, '_blank');
  }

  function initTelegram() {
    if (!tg) return;
    document.body.classList.add('tg-app');
    try { tg.ready(); } catch (_) {}
    try { tg.expand(); } catch (_) {}
    try { tg.disableVerticalSwipes?.(); } catch (_) {}
    try {
      tg.setHeaderColor?.('#F4F1EA');
      tg.setBackgroundColor?.('#F4F1EA');
    } catch (_) {}
    const h = tg.viewportStableHeight || tg.viewportHeight;
    if (h && Number.isFinite(h)) {
      document.documentElement.style.setProperty('--tg-app-height', `${h}px`);
    }
    if (typeof tg.onEvent === 'function') {
      tg.onEvent('viewportChanged', () => {
        const nh = tg.viewportStableHeight || tg.viewportHeight;
        if (nh && Number.isFinite(nh)) {
          document.documentElement.style.setProperty('--tg-app-height', `${nh}px`);
        }
      });
    }
  }

  function pickDisplayName(user, profile) {
    if (profile?.name) return String(profile.name).trim();
    if (!user) return 'друг';
    const name = (user.first_name || user.username || '').toString().trim();
    if (!name) return 'друг';
    return name.length > 22 ? `${name.slice(0, 22).trim()}…` : name;
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

  function applyIdentity(user, profile) {
    const nameEl = document.getElementById('user-name');
    if (nameEl) nameEl.textContent = pickDisplayName(user, profile);

    const avatarEl = document.getElementById('avatar');
    const tgUser = tg?.initDataUnsafe?.user;
    const photo = user?.photo_url || tgUser?.photo_url;
    if (avatarEl && photo) {
      avatarEl.style.backgroundImage = `url("${cssEscapeUrl(photo)}")`;
    }
  }

  function showGate(reason) {
    const gate = document.getElementById('gate');
    const main = document.getElementById('main');
    const title = gate?.querySelector('.gate-title');
    const text = gate?.querySelector('.gate-text');
    if (title && text) {
      if (reason === 'unauthorized') {
        title.textContent = 'Открой через бота';
        text.textContent =
          'Не удалось подтвердить Telegram. Открой Mini App из кнопки у бота SpiceSpace или через /app.';
      } else if (reason === 'not-found') {
        title.textContent = 'Профиль не найден';
        text.textContent = 'Напиши /start в боте SpiceSpace, пройди онбординг, затем снова открой Mini App.';
      } else {
        title.textContent = 'Открой через бота';
        text.textContent = 'Сначала пройди знакомство с ботом — нажми кнопку ниже.';
      }
    }
    if (gate) gate.hidden = false;
    if (main) main.hidden = true;
  }

  function showMain() {
    const gate = document.getElementById('gate');
    const main = document.getElementById('main');
    if (gate) gate.hidden = true;
    if (main) main.hidden = false;
  }

  async function fetchProfile() {
    if (!BACKEND_URL) return { profile: null, user: null, status: 'no-backend' };
    if (!tg?.initData && !DEMO_TG) return { profile: null, user: null, status: 'no-init-data' };
    try {
      const resp = await apiFetch('/api/profile');
      if (resp.status === 401) return { profile: null, user: null, status: 'unauthorized' };
      if (resp.status === 404) return { profile: null, user: null, status: 'not-found' };
      if (!resp.ok) return { profile: null, user: null, status: 'error' };
      const data = await resp.json();
      const profile = data.profile || data;
      const hasGoal = profile && (profile.main_goal || profile.raw_goal || profile.final_goal);
      return {
        profile: hasGoal ? profile : null,
        user: data.user || null,
        status: hasGoal ? 'ok' : 'empty',
      };
    } catch (e) {
      console.warn('fetchProfile', e);
      return { profile: null, user: null, status: 'error' };
    }
  }

  function goalTitle(profile) {
    return (
      profile.main_goal || profile.final_goal || profile.raw_goal || 'Твоя цель'
    ).trim();
  }

  function effectiveStreak(profile) {
    const fromApi = Number(profile.display_streak ?? profile.streak ?? 0);
    if (fromApi > 0) return fromApi;
    if (profile.today_completed) return 1;
    if ((profile.today_task || '').trim()) return 1;
    const today = localISODate();
    const key = `spicespace_day_${today}`;
    if (sessionStorage.getItem(key) === '1') return 1;
    return 0;
  }

  function weekScores(profile) {
    const raw = profile.week_scores;
    if (Array.isArray(raw) && raw.length >= 4) {
      return raw.slice(0, 4).map((x) => Math.max(0, Math.min(100, Number(x) || 0)));
    }
    const cw = Number(profile.current_week || 1);
    const ws = Math.max(0, Math.min(100, Number(profile.weekly_score || 0)));
    const out = [0, 0, 0, 0];
    if (cw >= 1 && cw <= 4) out[cw - 1] = ws;
    return out;
  }

  function renderStreak(profile) {
    const streak = effectiveStreak(profile);
    const numEl = document.getElementById('streak-number');
    if (numEl) numEl.textContent = String(streak);

    const dotsEl = document.getElementById('streak-dots');
    if (!dotsEl) return;
    const total = 7;
    const filled = Math.max(0, Math.min(streak, total));
    const parts = [];
    for (let i = 0; i < total; i++) {
      let cls = 'streak-dot';
      if (i < filled) cls += ' streak-dot--done';
      else if (filled > 0 && i === filled) cls += ' streak-dot--today';
      else if (filled === 0 && i === 0) cls += ' streak-dot--today';
      parts.push(`<span class="${cls}"></span>`);
    }
    dotsEl.innerHTML = parts.join('');
  }

  function renderWeekJourney(profile) {
    const host = document.getElementById('week-journey');
    if (!host) return;

    const currentWeek = Math.max(1, Math.min(4, Number(profile.current_week || 1)));
    const scores = weekScores(profile);
    const percent = scores[currentWeek - 1] ?? 0;

    const circles = scores.map((score, idx) => {
      const w = idx + 1;
      let dotCls = 'week-circle__dot';
      if (w < currentWeek) {
        dotCls += score >= 80 ? ' week-circle__dot--filled' : ' week-circle__dot--partial';
      } else if (w === currentWeek) {
        dotCls += ' week-circle__dot--current';
        if (score >= 80) dotCls += ' week-circle__dot--filled';
        else if (score > 0) dotCls += ' week-circle__dot--partial';
      } else {
        dotCls += ' week-circle__dot--future';
      }
      const labelCls = w === currentWeek
        ? 'week-circle__label week-circle__label--current'
        : 'week-circle__label';
      return `
        <div class="week-circle">
          <div class="${dotCls}" title="Неделя ${w}: ${score}%"></div>
          <span class="${labelCls}">${w}</span>
        </div>`;
    }).join('');

    const bookLine = scores.map((score, idx) => {
      const w = idx + 1;
      if (w < currentWeek) return score >= 80 ? '●' : '◐';
      if (w === currentWeek) return score >= 80 ? '●' : '◉';
      return '○';
    }).join('');

    host.innerHTML = `
      <div class="week-journey__head">
        <span class="week-journey__title">Неделя ${currentWeek}</span>
        <span class="week-journey__book" aria-hidden="true">${bookLine}</span>
        <span class="week-journey__percent">${percent}%</span>
      </div>
      <div class="week-journey__circles">${circles}</div>
      <div class="week-journey__bar">
        <div class="week-journey__fill" style="width:${percent}%"></div>
      </div>
      <p class="week-journey__bar-caption">выполнено за эту неделю · кружок закрашен при &gt;80%</p>
    `;
  }

  function renderGoals(profile) {
    const list = document.getElementById('goals-list');
    if (!list) return;

    const title = goalTitle(profile);
    const cards = [`<article class="goal-card">
      <h3 class="goal-card__title">${escapeHtml(title)}</h3>
    </article>`];

    const signals = Array.isArray(profile.goal_signals) ? profile.goal_signals : [];
    if ((profile.goal_type || '').toLowerCase() === 'qualitative' && signals.length) {
      const SIGNAL_LABELS = {
        energy: 'Больше энергии',
        anxiety: 'Меньше тревоги',
        sleep: 'Лучше сон',
        stability: 'Стабильность',
        joy: 'Больше радости',
      };
      signals.forEach((sig) => {
        const name = SIGNAL_LABELS[sig] || sig;
        cards.push(`<article class="goal-card">
          <h3 class="goal-card__title">${escapeHtml(name)}</h3>
        </article>`);
      });
    }

    list.innerHTML = cards.join('');
  }

  function isDayMarked(profile) {
    if (profile?.today_completed) return true;
    const today = localISODate();
    return sessionStorage.getItem(`spicespace_day_${today}`) === '1'
      || profile?.last_streak_date === today;
  }

  function renderMarkDayButton() {
    const btn = document.getElementById('btn-mark-day');
    if (!btn) return;
    const today = localISODate();
    dayMarkedKey = `spicespace_day_${today}`;
    const marked = isDayMarked(lastProfile || {});
    btn.disabled = marked;
    btn.classList.toggle('is-done', marked);
    btn.textContent = marked ? '✓ День отмечен' : '✓ Отметить день выполненным';
  }

  function renderProfile(profile) {
    lastProfile = profile;
    showMain();
    renderStreak(profile);
    renderWeekJourney(profile);
    renderGoals(profile);
    renderMarkDayButton();

    const planGoal = document.getElementById('plan-main-goal');
    if (planGoal) planGoal.textContent = goalTitle(profile);
  }

  async function loadTasks() {
    tasksCache = [];
    if (!BACKEND_URL) return;
    if (!tg?.initData && !DEMO_TG) return;
    const resp = await apiFetch('/api/tasks');
    if (!resp.ok) return;
    const data = await resp.json();
    tasksCache = Array.isArray(data.tasks) ? data.tasks : [];
  }

  function tasksForDay(iso, todayIso) {
    const list = tasksCache
      .filter((t) => taskAppliesOnDate(t, iso))
      .sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')));

    if (iso !== todayIso || !lastProfile) return list;

    const focus = (lastProfile.today_task || '').trim();
    if (!focus) return list;

    const dup = list.some((t) => {
      const title = (t.title || '').trim().toLowerCase();
      return title && (title === focus.toLowerCase() || focus.toLowerCase().includes(title));
    });
    if (dup) return list;

    list.unshift({
      id: '__today_focus__',
      title: focus,
      done: Boolean(lastProfile.today_completed),
      virtual: true,
    });
    return list;
  }

  function renderPlanDays() {
    const host = document.getElementById('plan-days');
    if (!host) return;
    const todayIso = localISODate();
    const days = [todayIso, addDaysISO(todayIso, 1), addDaysISO(todayIso, 2)];
    const blocks = days.map((iso) => {
      const list = tasksForDay(iso, todayIso);
      const head = formatDayHeader(iso, todayIso);
      if (!list.length) {
        const hint = iso === todayIso && lastProfile
          ? '<p class="day-card__empty">Обсуди шаг на сегодня с ботом утром — он появится здесь.</p>'
          : '<p class="day-card__empty">Задач нет</p>';
        return `
          <div class="day-card">
            <div class="day-card__head"><strong>${escapeHtml(head)}</strong></div>
            ${hint}
          </div>`;
      }
      const items = list.map((t) => {
        const done = Boolean(t.done);
        const isFocus = Boolean(t.virtual);
        return `
          <li class="task-item${done ? ' task-item--done' : ''}${isFocus ? ' task-item--focus' : ''}" data-task-id="${escapeHtml(t.id)}">
            <span class="task-item__title">${escapeHtml(t.title || '')}</span>
            <button type="button" class="task-item__mark" data-act="done" data-id="${escapeHtml(t.id)}" ${done ? 'disabled' : ''}>
              ${done ? 'Готово' : 'Сделано'}
            </button>
          </li>`;
      }).join('');
      return `
        <div class="day-card">
          <div class="day-card__head"><strong>${escapeHtml(head)}</strong></div>
          <ul class="task-list">${items}</ul>
        </div>`;
    });
    host.innerHTML = blocks.join('');
  }

  function mondayOfDisplayedWeek() {
    const base = new Date();
    base.setDate(base.getDate() + planWeekOffset * 7);
    const d = new Date(base.getFullYear(), base.getMonth(), base.getDate());
    const wd = d.getDay();
    const diff = wd === 0 ? -6 : 1 - wd;
    d.setDate(d.getDate() + diff);
    return d;
  }

  function renderWeekCalendar() {
    const row = document.getElementById('week-row');
    const title = document.getElementById('week-title');
    if (!row) return;
    const mon = mondayOfDisplayedWeek();
    const labels = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const todayIso = localISODate();
    const parts = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + i);
      const iso = localISODate(d);
      const dayTasks = tasksCache.filter((t) => taskAppliesOnDate(t, iso));
      const anyDone = dayTasks.some((x) => x.done);
      const anyActive = dayTasks.some((x) => !x.done);
      let circleCls = 'week-day__circle';
      if (iso === todayIso) circleCls += ' week-day__circle--today';
      else if (anyDone && !anyActive) circleCls += ' week-day__circle--done';
      else if (anyActive) circleCls += ' week-day__circle--partial';
      let dotCls = 'week-day__dot';
      if (anyActive || anyDone) dotCls += ' week-day__dot--active';
      parts.push(`
        <div class="week-day">
          <span class="week-day__label">${labels[i]}</span>
          <div class="${circleCls}">${d.getDate()}</div>
          <span class="${dotCls}"></span>
        </div>`);
    }
    row.innerHTML = parts.join('');
    if (title) {
      const end = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + 6);
      title.textContent = `${mon.getDate()}.${String(mon.getMonth() + 1).padStart(2, '0')} — ${end.getDate()}.${String(end.getMonth() + 1).padStart(2, '0')}`;
    }
  }

  async function refreshPlan() {
    await loadTasks();
    renderPlanDays();
    renderWeekCalendar();
  }

  function switchTab(name) {
    document.querySelectorAll('.tabbar__item').forEach((el) => {
      el.classList.toggle('tabbar__item--active', el.dataset.tab === name);
    });
    document.querySelectorAll('.panel').forEach((panel) => {
      const on = panel.dataset.panel === name;
      panel.hidden = !on;
      panel.classList.toggle('panel--active', on);
      panel.classList.toggle('panel--center', panel.dataset.panel === 'bot');
    });
    if (name === 'plan') refreshPlan();
    haptic('select');
  }

  function bindTabs() {
    document.querySelectorAll('.tabbar__item').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab || 'goals';
        switchTab(tab);
      });
    });
  }

  function bindPlan() {
    document.querySelector('[data-week-nav="-1"]')?.addEventListener('click', () => {
      planWeekOffset -= 1;
      renderWeekCalendar();
      haptic('select');
    });
    document.querySelector('[data-week-nav="1"]')?.addEventListener('click', () => {
      planWeekOffset += 1;
      renderWeekCalendar();
      haptic('select');
    });

    document.getElementById('plan-days')?.addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-act="done"]');
      if (!btn || btn.disabled) return;
      const id = btn.getAttribute('data-id');
      if (!id) return;
      if (id === '__today_focus__') {
        await markDayComplete();
        return;
      }
      const resp = await apiFetch(`/api/tasks/${encodeURIComponent(id)}/done`, { method: 'POST' });
      if (resp.ok) {
        haptic('success');
        await refreshPlan();
      }
    });
  }

  async function markDayComplete() {
    const resp = await apiFetch('/api/mark-day', { method: 'POST' });
    if (resp.ok) {
      const data = await resp.json();
      if (data.profile) {
        lastProfile = data.profile;
        renderProfile(data.profile);
        await refreshPlan();
      }
      sessionStorage.setItem(dayMarkedKey || `spicespace_day_${localISODate()}`, '1');
      haptic('success');
      return;
    }
    sessionStorage.setItem(dayMarkedKey || `spicespace_day_${localISODate()}`, '1');
    if (lastProfile) {
      lastProfile.today_completed = true;
      lastProfile.display_streak = Math.max(1, effectiveStreak(lastProfile));
      renderProfile(lastProfile);
    }
    haptic('success');
  }

  function bindMarkDay() {
    document.getElementById('btn-mark-day')?.addEventListener('click', () => {
      const btn = document.getElementById('btn-mark-day');
      if (!btn || btn.disabled) return;
      markDayComplete();
    });
  }

  function bindBotButtons() {
    document.getElementById('btn-open-chat')?.addEventListener('click', () => {
      haptic('success');
      openBotChat();
    });
    document.getElementById('gate-open-bot')?.addEventListener('click', () => {
      haptic('success');
      openBotChat();
    });
  }

  function buildDemoProfile(tgUser) {
    return {
      name: tgUser?.first_name || 'Анна',
      goal_type: 'measurable',
      main_goal: 'Демо без backend',
      streak: 3,
      display_streak: 3,
      weekly_score: 35,
      week_scores: [80, 35, 0, 0],
      current_week: 2,
      today_task: 'Один маленький шаг к цели',
      goal_signals: [],
    };
  }

  async function start() {
    initTelegram();
    applyGreeting();
    bindTabs();
    bindPlan();
    bindMarkDay();
    bindBotButtons();

    const tgUser = tg?.initDataUnsafe?.user ?? null;
    applyIdentity(tgUser, null);

    const { profile, user, status } = await fetchProfile();

    if (status === 'no-backend') {
      showMain();
      renderProfile(buildDemoProfile(tgUser));
      await refreshPlan();
      return;
    }

    if (status === 'no-init-data' && !DEMO_TG) {
      showGate('unauthorized');
      return;
    }

    if (!profile) {
      applyIdentity(user || tgUser, null);
      showGate(status === 'unauthorized' ? 'unauthorized' : status === 'not-found' ? 'not-found' : 'empty');
      return;
    }

    applyIdentity(user || tgUser, profile);
    renderProfile(profile);
    await refreshPlan();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
