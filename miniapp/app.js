(() => {
  'use strict';

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  const BRAND = {
    cream: '#FAF7F2',
    spice: '#E8500A',
    charcoal: '#1A1A1A',
  };

  const BACKEND_META = (
    document.querySelector('meta[name="spicespace-backend"]')?.content || ''
  ).trim();
  const BACKEND_URL = (
    BACKEND_META || window.location.origin || ''
  ).replace(/\/+$/, '');
  const BOT_USERNAME = (
    document.querySelector('meta[name="spicespace-bot-username"]')?.content || ''
  ).replace(/^@/, '');

  const DEMO_TG = new URLSearchParams(window.location.search).get('telegram_id') || '';

  let lastProfile = null;
  let tasksCache = [];
  let planWeekOffset = 0;

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
    if (tg && tg.initData && !headers.Authorization) {
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

  function dowKeyFromLocalDate(d) {
    const w = d.getDay();
    return ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'][w];
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

  function taskAppliesOnDate(task, iso) {
    if (task.done || task.status !== 'active') return false;
    const r = task.repeat || 'none';
    if (r === 'daily') return true;
    if (r === 'weekly') {
      const days = Array.isArray(task.days_of_week) ? task.days_of_week : [];
      const key = dowKeyFromLocalDate(parseISODate(iso));
      return days.includes(key);
    }
    return (task.date || '') === iso;
  }

  function taskMissed(task, todayIso) {
    if ((task.repeat || 'none') !== 'none' || task.done) return false;
    const td = task.date || '';
    return td < todayIso;
  }

  async function loadTasks() {
    tasksCache = [];
    if (!BACKEND_URL) return;
    if (!(tg && tg.initData) && !DEMO_TG) return;
    const resp = await apiFetch('/api/tasks');
    if (!resp.ok) return;
    const data = await resp.json();
    tasksCache = Array.isArray(data.tasks) ? data.tasks : [];
  }

  function renderPlanToday() {
    const host = document.getElementById('plan-today-list');
    if (!host) return;
    const todayIso = localISODate();
    const list = tasksCache.filter((t) => taskAppliesOnDate(t, todayIso));
    if (!list.length) {
      host.innerHTML = '<div class="plan-empty">Сегодня пока без задач — добавь первую ✨</div>';
      return;
    }
    list.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    host.innerHTML = list.map((t) => {
      const st = t.done ? 'done' : taskMissed(t, todayIso) ? 'missed' : '';
      const meta = t.done ? 'сделано' : t.repeat === 'daily' ? 'каждый день' : t.repeat === 'weekly' ? 'по дням' : 'разово';
      return `
        <div class="plan-task-row ${st}" data-task-id="${escapeHtml(t.id)}">
          <div class="plan-task-time">${escapeHtml(t.time || '')}</div>
          <div class="plan-task-body">
            <div class="plan-task-title">${escapeHtml(t.title || '')}</div>
            <div class="plan-task-meta">${escapeHtml(meta)}</div>
          </div>
          <div class="plan-task-actions">
            <button type="button" class="pt-btn" data-act="done" data-id="${escapeHtml(t.id)}" ${t.done ? 'disabled' : ''}>Сделано</button>
            <button type="button" class="pt-btn secondary" data-act="postpone" data-id="${escapeHtml(t.id)}">Перенести</button>
          </div>
        </div>`;
    }).join('');
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

  function renderPlanWeek() {
    const row = document.getElementById('plan-days-row');
    const title = document.getElementById('plan-week-title');
    if (!row) return;
    const mon = mondayOfDisplayedWeek();
    const labels = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const todayIso = localISODate();
    const parts = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + i);
      const iso = localISODate(d);
      const num = d.getDate();
      const dayTasks = tasksCache.filter((t) => taskAppliesOnDate(t, iso));
      const anyDone = dayTasks.some((x) => x.done);
      const anyMiss = dayTasks.some((x) => taskMissed(x, todayIso));
      const anyActive = dayTasks.some((x) => !x.done);
      let cls = 'day-circle';
      if (iso === todayIso) cls += ' today';
      if (anyDone) cls += ' done';
      else if (anyMiss) cls += ' missed';
      else if (anyActive) cls += ' partial';
      let dotCls = 'day-dot';
      if (anyDone) dotCls += ' done';
      if (iso === todayIso) dotCls += ' today';
      parts.push(`<div class="day-item" data-day="${iso}">
        <div class="day-label">${labels[i]}</div>
        <div class="${cls}">${num}</div>
        <div class="${dotCls}"></div>
      </div>`);
    }
    row.innerHTML = parts.join('');
    if (title) {
      const end = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + 6);
      title.textContent = `${mon.getDate()}.${String(mon.getMonth() + 1).padStart(2, '0')} — ${end.getDate()}.${String(end.getMonth() + 1).padStart(2, '0')}`;
    }
  }

  async function refreshPlanUI() {
    await loadTasks();
    renderPlanToday();
    renderPlanWeek();
  }

  const SIGNAL_LABELS = {
    energy: 'Больше энергии утром',
    anxiety: 'Меньше тревоги',
    sleep: 'Лучше сон',
    stability: 'Больше стабильности в делах',
    joy: 'Больше удовольствия от дня',
  };

  const SIGNAL_ICONS = {
    energy: '⚡',
    anxiety: '🌿',
    sleep: '🌙',
    stability: '⚖️',
    joy: '🌞',
  };

  function initTelegram() {
    if (!tg) return null;
    document.body.classList.add('tg-app');
    try { tg.ready(); } catch (_) {}
    try { tg.expand(); } catch (_) {}
    try { tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (_) {}
    try {
      if (typeof tg.setHeaderColor === 'function') tg.setHeaderColor(BRAND.cream);
      if (typeof tg.setBackgroundColor === 'function') tg.setBackgroundColor(BRAND.cream);
    } catch (_) {}

    syncViewportHeight();
    if (typeof tg.onEvent === 'function') {
      tg.onEvent('viewportChanged', syncViewportHeight);
    }
    return tg;
  }

  function syncViewportHeight() {
    if (!tg) return;
    const h = tg.viewportStableHeight || tg.viewportHeight;
    if (h && Number.isFinite(h)) {
      document.documentElement.style.setProperty('--tg-app-height', h + 'px');
    }
  }

  function pickDisplayName(user, profile) {
    if (profile && profile.name) return String(profile.name).trim();
    if (!user) return 'Анна';
    const name = (user.first_name || user.username || '').toString().trim();
    if (!name) return 'Друг';
    return name.length > 18 ? name.slice(0, 18).trim() + '…' : name;
  }

  function cssEscapeUrl(url) {
    return String(url).replace(/"/g, '%22');
  }

  function applyDynamicGreeting() {
    const el = document.getElementById('greeting');
    if (!el) return;
    const h = new Date().getHours();
    let g = 'Доброе утро,';
    if (h >= 12 && h < 18) g = 'Добрый день,';
    else if (h >= 18 && h < 23) g = 'Добрый вечер,';
    else if (h >= 23 || h < 5) g = 'Доброй ночи,';
    el.textContent = g;
  }

  function startStatusClock() {
    const el = document.getElementById('status-time');
    if (!el) return;
    const tick = () => {
      const d = new Date();
      const hh = String(d.getHours()).padStart(2, '0');
      const mm = String(d.getMinutes()).padStart(2, '0');
      el.textContent = `${hh}:${mm}`;
    };
    tick();
    setInterval(tick, 30 * 1000);
  }

  function haptic(type) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (type === 'select') tg.HapticFeedback.selectionChanged();
      else if (type === 'success') tg.HapticFeedback.notificationOccurred('success');
      else tg.HapticFeedback.impactOccurred('light');
    } catch (_) {}
  }

  async function fetchProfile() {
    if (!BACKEND_URL) return { profile: null, user: null, status: 'no-backend' };
    if (!(tg && tg.initData) && !DEMO_TG) return { profile: null, user: null, status: 'no-init-data' };
    try {
      const resp = await apiFetch('/api/profile');
      if (resp.status === 401) {
        return { profile: null, user: null, status: 'unauthorized' };
      }
      if (!resp.ok) {
        return { profile: null, user: null, status: 'error' };
      }
      const data = await resp.json();
      const profile = data.profile || data;
      const hasGoal = profile && (
        profile.main_goal || profile.raw_goal || profile.final_goal
      );
      return {
        profile: hasGoal ? profile : null,
        user: data.user || null,
        status: hasGoal ? 'ok' : 'empty',
      };
    } catch (e) {
      console.warn('fetchProfile failed', e);
      return { profile: null, user: null, status: 'error' };
    }
  }

  function applyIdentity(user, profile) {
    const nameEl = document.getElementById('user-name');
    if (nameEl) {
      const name = pickDisplayName(user, profile);
      nameEl.textContent = `${name} ✦`;
    }

    const avatarEl = document.getElementById('avatar');
    const tgUser = tg && tg.initDataUnsafe ? tg.initDataUnsafe.user : null;
    const photo = (user && user.photo_url) || (tgUser && tgUser.photo_url);
    if (avatarEl && photo) {
      avatarEl.style.backgroundImage = `url("${cssEscapeUrl(photo)}")`;
      avatarEl.textContent = '';
    }
  }

  function renderEmpty() {
    document.body.classList.add('mode-empty');
    const empty = document.getElementById('empty-state');
    const main = document.getElementById('main-content');
    if (empty) empty.hidden = false;
    if (main) main.hidden = true;
  }

  function renderProfile(profile) {
    document.body.classList.remove('mode-empty');
    lastProfile = profile;
    const empty = document.getElementById('empty-state');
    const main = document.getElementById('main-content');
    if (empty) empty.hidden = true;
    if (main) main.hidden = false;

    const goalType = (profile.goal_type || 'measurable').toLowerCase();
    document.body.classList.toggle('mode-measurable', goalType === 'measurable');
    document.body.classList.toggle('mode-qualitative', goalType === 'qualitative');

    renderStreak(profile);
    renderToday(profile);
    renderBotMessage(profile);
    renderGoalsSection(profile);
  }

  function renderStreak(profile) {
    const streak = Number(profile.streak || 0);
    const num = document.getElementById('streak-number');
    if (num) num.textContent = String(streak);

    const sub = document.getElementById('streak-sub');
    if (sub) {
      if (streak <= 0) sub.textContent = 'начинаем серию';
      else if (streak === 1) sub.textContent = 'первый день в движении';
      else sub.textContent = 'дней подряд в движении';
    }

    const dots = document.getElementById('streak-dots');
    if (!dots) return;
    const total = 7;
    const todayIdx = Math.min(streak, total - 1);
    const html = [];
    for (let i = 0; i < total; i++) {
      let cls = 'dot';
      if (i < streak) cls += ' done';
      else if (i === todayIdx && streak < total) cls += ' today';
      html.push(`<div class="${cls}"></div>`);
    }
    dots.innerHTML = html.join('');
  }

  function renderToday(profile) {
    const goalType = (profile.goal_type || 'measurable').toLowerCase();
    const label = document.getElementById('today-label');
    const time = document.getElementById('today-time');
    const desc = document.getElementById('today-desc');
    if (!label || !time || !desc) return;

    if (goalType === 'qualitative') {
      label.textContent = 'Сегодня в фокусе';
      time.textContent = '🌿 Состояние, а не цифра';
      const focus = (profile.raw_goal || profile.final_goal || 'твоё состояние').trim();
      desc.textContent = focus.length > 80 ? focus.slice(0, 80) + '…' : focus;
    } else {
      label.textContent = 'Сегодня в фокусе';
      const fg = (profile.final_goal || profile.raw_goal || 'твоя цель').trim();
      time.textContent = '🎯 Один маленький шаг';
      desc.textContent = fg.length > 80 ? fg.slice(0, 80) + '…' : fg;
    }
  }

  function renderBotMessage(profile) {
    const text = document.getElementById('bot-text');
    const time = document.getElementById('bot-time');
    if (!text) return;
    const name = (profile.name || '').trim();
    const goalType = (profile.goal_type || 'measurable').toLowerCase();
    const streak = Number(profile.streak || 0);
    const greeting = name ? `${name}, ` : '';

    if (goalType === 'qualitative') {
      text.textContent = streak > 0
        ? `${greeting}ты уже ${streak} ${pluralize(streak, ['день', 'дня', 'дней'])} подряд держишься рядом со своим состоянием. Так и идём — мягко.`
        : `${greeting}сегодня — один маленький шаг ближе к состоянию, которое ты хочешь. Без давления.`;
    } else {
      text.textContent = streak > 0
        ? `${greeting}ты уже ${streak} ${pluralize(streak, ['день', 'дня', 'дней'])} не сливаешься. Это не случайность. Это ты. 🔥`
        : `${greeting}у тебя есть цель. Сегодня нужен один маленький шаг — и серия начнётся.`;
    }

    if (time) {
      const d = new Date();
      time.textContent = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
  }

  function pluralize(n, forms) {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    if (b === 1) return forms[0];
    return forms[2];
  }

  function renderGoalsSection(profile) {
    const section = document.getElementById('goals-section-title');
    const list = document.getElementById('goals-list');
    if (!section || !list) return;

    const goalType = (profile.goal_type || 'measurable').toLowerCase();
    const week = Number(profile.current_week || 1);

    if (goalType === 'qualitative') {
      section.textContent = `Отслеживаем · Неделя ${week}`;
      list.innerHTML = '';
      const signals = Array.isArray(profile.goal_signals) ? profile.goal_signals : [];
      if (!signals.length) {
        list.appendChild(makeGoalCard({
          icon: '🌿',
          iconClass: 'spirit',
          name: profile.raw_goal || 'твоё состояние',
          fillClass: 'spirit',
          percent: profile.weekly_score || 0,
          meta: 'Признаки появятся позже',
          done: false,
        }));
        return;
      }
      signals.forEach((sig) => {
        list.appendChild(makeGoalCard({
          icon: SIGNAL_ICONS[sig] || '🌿',
          iconClass: 'spirit',
          name: SIGNAL_LABELS[sig] || sig,
          fillClass: 'spirit',
          percent: profile.weekly_score || 0,
          meta: 'отслеживаем',
          done: false,
        }));
      });
      return;
    }

    section.textContent = `Моя цель · Неделя ${week}`;
    list.innerHTML = '';
    const finalGoal = (profile.final_goal || profile.raw_goal || 'твоя цель').trim();
    const percent = Math.max(0, Math.min(100, Number(profile.weekly_score || 0)));
    const completed = Array.isArray(profile.completed_tasks) ? profile.completed_tasks.length : 0;
    const meta = completed > 0
      ? `${completed} ${pluralize(completed, ['задача', 'задачи', 'задач'])} сделана`
      : 'двигаемся первыми шагами';
    list.appendChild(makeGoalCard({
      icon: '🎯',
      iconClass: 'body',
      name: finalGoal,
      fillClass: 'body',
      percent,
      meta,
      done: percent >= 100,
    }));
  }

  function makeGoalCard(opts) {
    const { icon, iconClass, name, fillClass, percent, meta, done } = opts;
    const card = document.createElement('div');
    card.className = 'goal-card' + (done ? ' active' : '');
    card.innerHTML = `
      <div class="goal-icon ${iconClass}">${escapeHtml(icon)}</div>
      <div class="goal-info">
        <div class="goal-name">${escapeHtml(name)}</div>
        <div class="progress-bar"><div class="progress-fill ${fillClass}" style="width:${percent}%"></div></div>
        <div class="goal-meta">
          <span class="goal-percent">${percent ? percent + '% выполнено' : 'старт'}</span>
          <span class="goal-days">${escapeHtml(meta)}</span>
        </div>
      </div>
      <div class="goal-check ${done ? 'done' : ''}">${done ? '✓' : ''}</div>
    `;
    return card;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function bindGoals() {
    document.getElementById('goals-list')?.addEventListener('click', (e) => {
      const card = e.target.closest('.goal-card');
      if (!card || card.classList.contains('skeleton')) return;
      const check = card.querySelector('.goal-check');
      if (!check) return;
      if (check.classList.contains('done')) {
        check.classList.remove('done');
        check.textContent = '';
        card.classList.remove('active');
      } else {
        check.classList.add('done');
        check.textContent = '✓';
        card.classList.add('active');
        haptic('success');
      }
    });
  }

  function bindTodayActions() {
    const card = document.getElementById('today-card');
    if (!card) return;
    card.querySelectorAll('[data-action]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const action = btn.getAttribute('data-action');
        if (action === 'mark-done') {
          card.classList.add('done');
          btn.textContent = '✓ Готово!';
          haptic('success');
        } else if (action === 'postpone') {
          haptic('select');
        }
      });
    });
  }

  function bindBottomNav() {
    const items = document.querySelectorAll('.nav-item');
    const panelHome = document.getElementById('panel-home');
    const panelPlan = document.getElementById('panel-plan');
    const scrollArea = document.querySelector('.scroll-area');

    const showPanel = (name) => {
      if (panelHome) {
        const hideHome = name === 'plan';
        panelHome.hidden = hideHome;
        panelHome.classList.toggle('tab-panel--visible', !hideHome);
      }
      if (panelPlan) {
        panelPlan.hidden = name !== 'plan';
        panelPlan.classList.toggle('tab-panel--visible', name === 'plan');
      }
      if (scrollArea) scrollArea.scrollTop = 0;
    };

    items.forEach((item) => {
      item.addEventListener('click', () => {
        items.forEach((i) => i.classList.remove('active'));
        item.classList.add('active');
        haptic('select');
        const tab = item.getAttribute('data-tab') || 'home';
        if (tab === 'bot') {
          const link = BOT_USERNAME ? `https://t.me/${BOT_USERNAME}` : '';
          if (link && tg && typeof tg.openTelegramLink === 'function') {
            try { tg.openTelegramLink(link); } catch (_) {}
          } else if (link) window.open(link, '_blank');
          return;
        }
        if (tab === 'goals') {
          showPanel('goals');
          document.getElementById('goals-section-title')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          return;
        }
        if (tab === 'plan') {
          showPanel('plan');
          refreshPlanUI();
          return;
        }
        showPanel('home');
      });
    });
  }

  function bindPlanInteractions() {
    document.getElementById('plan-week-nav')?.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-plan-nav]');
      if (!b) return;
      const d = Number(b.getAttribute('data-plan-nav') || 0);
      planWeekOffset += d;
      renderPlanWeek();
      haptic('select');
    });

    document.getElementById('plan-today-list')?.addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-act]');
      if (!btn) return;
      const id = btn.getAttribute('data-id');
      const act = btn.getAttribute('data-act');
      if (!id) return;
      const task = tasksCache.find((x) => x.id === id);
      if (act === 'done') {
        const resp = await apiFetch(`/api/tasks/${encodeURIComponent(id)}/done`, { method: 'POST' });
        if (resp.ok) {
          haptic('success');
          await refreshPlanUI();
        }
        return;
      }
      if (act === 'postpone' && task) {
        const patch = (task.repeat || 'none') === 'none'
          ? { date: addDaysISO(task.date || localISODate(), 1) }
          : { snooze_until: localISODate() };
        const resp = await apiFetch(`/api/tasks/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        });
        if (resp.ok) {
          haptic('select');
          await refreshPlanUI();
        }
      }
    });
  }

  function readSheetForm() {
    const title = (document.getElementById('tf-title')?.value || '').trim();
    const date = document.getElementById('tf-date')?.value || '';
    const timeRaw = document.getElementById('tf-time')?.value || '';
    const tp = timeRaw.split(':');
    const time = tp.length >= 2
      ? `${String(Number(tp[0] || 0)).padStart(2, '0')}:${String(tp[1] || '00').padStart(2, '0').slice(0, 2)}`
      : '';
    const repeatEl = document.querySelector('#tf-repeat .seg-btn.active');
    const repeat = repeatEl?.getAttribute('data-repeat') || 'none';
    const remindEl = document.querySelector('#tf-remind .seg-btn.active');
    const remind = Number(remindEl?.getAttribute('data-remind') || 0);
    const dow = [];
    document.querySelectorAll('#tf-dow .dow-btn.active').forEach((b) => {
      const k = b.getAttribute('data-dow');
      if (k) dow.push(k);
    });
    const tz = (lastProfile && lastProfile.timezone) ? String(lastProfile.timezone) : 'Asia/Ho_Chi_Minh';
    return { title, date, time, repeat, remind_before_minutes: remind, days_of_week: dow, timezone: tz };
  }

  function openTaskSheet() {
    const sh = document.getElementById('task-sheet');
    if (!sh) return;
    document.getElementById('tf-title').value = '';
    document.getElementById('tf-date').value = localISODate();
    document.getElementById('tf-time').value = '14:00';
    document.querySelectorAll('#tf-repeat .seg-btn').forEach((b) => b.classList.remove('active'));
    document.querySelector('#tf-repeat .seg-btn[data-repeat="none"]')?.classList.add('active');
    document.querySelectorAll('#tf-remind .seg-btn').forEach((b) => b.classList.remove('active'));
    document.querySelector('#tf-remind .seg-btn[data-remind="0"]')?.classList.add('active');
    document.querySelectorAll('#tf-dow .dow-btn').forEach((b) => b.classList.remove('active'));
    document.getElementById('tf-dow-wrap').hidden = true;
    sh.hidden = false;
  }

  function closeTaskSheet() {
    const sh = document.getElementById('task-sheet');
    if (sh) sh.hidden = true;
  }

  function bindTaskSheet() {
    document.getElementById('btn-add-task')?.addEventListener('click', () => {
      haptic('select');
      openTaskSheet();
    });
    document.getElementById('task-sheet-backdrop')?.addEventListener('click', closeTaskSheet);
    document.getElementById('task-sheet-cancel')?.addEventListener('click', closeTaskSheet);

    document.getElementById('tf-repeat')?.addEventListener('click', (e) => {
      const b = e.target.closest('.seg-btn[data-repeat]');
      if (!b) return;
      document.querySelectorAll('#tf-repeat .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      const r = b.getAttribute('data-repeat');
      document.getElementById('tf-dow-wrap').hidden = r !== 'weekly';
    });

    document.getElementById('tf-remind')?.addEventListener('click', (e) => {
      const b = e.target.closest('.seg-btn[data-remind]');
      if (!b) return;
      document.querySelectorAll('#tf-remind .seg-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
    });

    document.getElementById('tf-dow')?.addEventListener('click', (e) => {
      const b = e.target.closest('.dow-btn[data-dow]');
      if (!b) return;
      b.classList.toggle('active');
      haptic('select');
    });

    document.getElementById('task-sheet-save')?.addEventListener('click', async () => {
      const f = readSheetForm();
      if (!f.title) {
        haptic('select');
        return;
      }
      if (f.repeat === 'weekly' && !f.days_of_week.length) {
        haptic('select');
        return;
      }
      const body = {
        title: f.title,
        description: '',
        date: f.date,
        time: f.time,
        repeat: f.repeat,
        days_of_week: f.days_of_week,
        remind_before_minutes: f.remind_before_minutes,
        timezone: f.timezone,
      };
      const resp = await apiFetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (resp.ok) {
        haptic('success');
        closeTaskSheet();
        await refreshPlanUI();
      }
    });
  }

  function bindEmptyState() {
    const btn = document.getElementById('open-bot-btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
      if (btn.dataset.busy === '1') return;
      btn.dataset.busy = '1';
      haptic('success');

      const originalText = btn.textContent;
      btn.textContent = 'Открываю чат…';
      btn.disabled = true;

      // Deep-link с параметром ?start=onboarding автоматически отправит
      // боту команду /start onboarding — пользователю не надо вводить руками.
      const link = BOT_USERNAME
        ? `https://t.me/${BOT_USERNAME}?start=onboarding`
        : '';

      const opened = (() => {
        if (link && tg && typeof tg.openTelegramLink === 'function') {
          try { tg.openTelegramLink(link); return true; } catch (_) {}
        }
        if (link) {
          try { window.open(link, '_blank'); return true; } catch (_) {}
        }
        return false;
      })();

      // Закрываем Mini App, чтобы пользователь увидел чат с ботом
      // и пришедшее сообщение от /start. В большинстве клиентов
      // openTelegramLink уже закрывает Mini App, но подстрахуемся.
      setTimeout(() => {
        if (tg && typeof tg.close === 'function') {
          try { tg.close(); return; } catch (_) {}
        }
        // Если по какой-то причине ничего не сработало —
        // возвращаем кнопку в исходное состояние.
        btn.textContent = originalText;
        btn.disabled = false;
        btn.dataset.busy = '';
      }, opened ? 250 : 0);
    });
  }

  async function start() {
    initTelegram();
    applyDynamicGreeting();
    startStatusClock();
    bindGoals();
    bindTodayActions();
    bindBottomNav();
    bindPlanInteractions();
    bindTaskSheet();
    bindEmptyState();

    const tgUser = tg && tg.initDataUnsafe ? tg.initDataUnsafe.user : null;
    applyIdentity(tgUser, null);

    const { profile, user, status } = await fetchProfile();

    if (status === 'no-backend' || (status === 'no-init-data' && !DEMO_TG)) {
      applyIdentity(tgUser, null);
      renderProfile(buildDemoProfile(tgUser));
      return;
    }

    if (!profile) {
      applyIdentity(user || tgUser, null);
      renderEmpty();
      return;
    }

    applyIdentity(user || tgUser, profile);
    renderProfile(profile);
    refreshPlanUI();
  }

  function buildDemoProfile(tgUser) {
    return {
      name: (tgUser && tgUser.first_name) || 'Анна',
      goal_type: 'measurable',
      raw_goal: 'демо-режим',
      final_goal: 'демо-режим (без backend)',
      streak: 0,
      weekly_score: 0,
      current_week: 1,
      goal_signals: [],
      completed_tasks: [],
      timezone: 'Asia/Ho_Chi_Minh',
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
