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

  /** Fallback user id when initData string is empty (URL param or initDataUnsafe). */
  let BACKEND_TELEGRAM_ID = null;

  const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  const MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const WEEKDAYS_SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
  const WEEKDAYS_EN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  let profile = null;
  let tasks = [];
  let calendarData = null;
  let activeTab = 'home';
  /** User-visible times on home screen (survives stale API reloads). */
  let displayTimesCache = null;

  function getTelegramId() {
    const fromUrl = new URLSearchParams(window.location.search).get('telegram_id');
    if (fromUrl) {
      const tid = String(fromUrl).trim();
      if (/^\d+$/.test(tid)) return tid;
    }
    const user = tg?.initDataUnsafe?.user;
    if (user?.id) return String(user.id);
    return null;
  }

  function timesStorageKey() {
    const uid = tg?.initDataUnsafe?.user?.id || BACKEND_TELEGRAM_ID || getTelegramId() || '0';
    return `spicespace_display_times_v1_${uid}`;
  }

  function loadDisplayTimesFromStorage() {
    try {
      const raw = localStorage.getItem(timesStorageKey());
      if (!raw) return null;
      const o = JSON.parse(raw);
      const morning = formatTimeHHMM(o.morning, null);
      const evening = formatTimeHHMM(o.evening, null);
      if (morning && evening) return { morning, evening };
    } catch (_) {}
    return null;
  }

  function saveDisplayTimesToStorage(morning, evening) {
    const morningHm = formatTimeHHMM(morning, null);
    const eveningHm = formatTimeHHMM(evening, null);
    if (!morningHm || !eveningHm) return;
    try {
      localStorage.setItem(
        timesStorageKey(),
        JSON.stringify({ morning: morningHm, evening: eveningHm }),
      );
    } catch (_) {}
  }

  function appendTelegramIdQuery(url) {
    if (tg?.initData || url.includes('telegram_id=')) return url;
    const tid = BACKEND_TELEGRAM_ID || getTelegramId();
    if (!tid) return url;
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}telegram_id=${encodeURIComponent(tid)}`;
  }

  async function apiFetch(path, opts = {}) {
    if (!BACKEND_URL) {
      return { ok: false, status: 0, json: async () => ({}) };
    }
    const headers = { ...(opts.headers || {}) };
    if (tg?.initData && !headers.Authorization) {
      headers.Authorization = `tma ${tg.initData}`;
    }
    let url = `${BACKEND_URL}${path.startsWith('/') ? path : `/${path}`}`;
    url = appendTelegramIdQuery(url);
    return fetch(url, { ...opts, headers, cache: 'no-store' });
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
    const lang = window.userLang || 'en';
    if (lang === 'en') {
      return `${WEEKDAYS_EN[d.getDay()]} ${d.getDate()} ${MONTHS_EN[d.getMonth()]}`;
    }
    return `${WEEKDAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }

  function pluralizeDays(n) {
    const lang = window.userLang || 'en';
    if (lang === 'en') {
      return Math.abs(n) === 1 ? t('streak_day_one') : t('streak_days');
    }
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return t('streak_days');
    if (b > 1 && b < 5) return t('streak_day_few');
    if (b === 1) return t('streak_day_one');
    return t('streak_days');
  }

  function weekBadgeText(week) {
    return `${t('week_label')} ${week} ${t('of_12')}`;
  }

  function applyGreeting() {
    const el = document.getElementById('greeting');
    if (!el) return;
    const h = new Date().getHours();
    let g = t('greeting_morning');
    if (h >= 12 && h < 18) g = t('greeting_day');
    else if (h >= 18 && h < 23) g = t('greeting_evening');
    else if (h >= 23 || h < 5) g = t('greeting_night');
    el.textContent = g;
  }

  function pickName(user, prof) {
    if (prof?.name) return String(prof.name).trim();
    const n = (user?.first_name || user?.username || '').trim();
    return n || t('friend_default');
  }

  function applyStaticI18n() {
    document.documentElement.lang = window.userLang || 'en';

    const setText = (sel, key) => {
      const el = document.querySelector(sel);
      if (el) el.textContent = t(key);
    };

    setText('#sync-banner .sync-banner__text', 'sync_banner_text');
    setText('#sync-banner-open', 'sync_open');
    setText('#empty-state .empty-state__title', 'no_goal_title');
    setText('#empty-state .empty-state__text', 'no_goal_text');
    setText('#empty-open-bot', 'go_to_bot');

    const setLabelWithSvg = (selector, key) => {
      const el = document.querySelector(selector);
      if (!el) return;
      const svg = el.querySelector('svg');
      el.textContent = '';
      if (svg) el.appendChild(svg);
      el.append(` ${t(key)}`);
    };

    setLabelWithSvg('.month-card .card-label', 'goal_12weeks');
    setText('.week-card .card-label', 'goal_week');
    setLabelWithSvg('.today-card .card-label', 'tasks_today');
    setText('.streak-card .card-label', 'streak_label');
    setText('.time-item:first-child .time-label', 'morning_time');
    setText('.time-item:nth-child(3) .time-label', 'evening_time');

    const editTimesBtn = document.getElementById('edit-times-btn');
    if (editTimesBtn) editTimesBtn.textContent = `✏️ ${t('change_btn')}`;

    const editNameBtn = document.getElementById('edit-name-btn');
    if (editNameBtn) editNameBtn.setAttribute('aria-label', t('edit_name'));

    setText('#btn-reset', 'settings_reset');
    setText('#btn-subscription', 'settings_subscription');
    setText('#btn-stop', 'settings_stop');

    setText('.calendar-title', 'weeks_12');
    const legends = document.querySelectorAll('.calendar-legend .legend-item');
    const legendKeys = ['done_label', 'partial_label', 'no_label', 'no_data_label', 'future_label'];
    legends.forEach((el, i) => {
      if (legendKeys[i]) {
        const dot = el.querySelector('.legend-dot');
        el.textContent = '';
        if (dot) el.appendChild(dot);
        el.append(` ${t(legendKeys[i])}`);
      }
    });

    const tabHome = document.querySelector('.tab-bar__btn[data-tab="home"]');
    const tabCal = document.querySelector('.tab-bar__btn[data-tab="calendar"]');
    if (tabHome) tabHome.textContent = `🏠 ${t('tab_home')}`;
    if (tabCal) tabCal.textContent = `📅 ${t('tab_progress')}`;

    setText('#edit-name-heading', 'edit_name_title');
    const nameInput = document.getElementById('edit-name-input');
    if (nameInput) nameInput.placeholder = t('name_placeholder');
    setText('#edit-name-cancel', 'cancel');
    setText('#edit-name-save', 'save');

    setText('#edit-times-heading', 'times_sheet_title');
    const morningLbl = document.querySelector('label.edit-times-sheet__field');
    const eveningLbl = document.querySelectorAll('label.edit-times-sheet__field')[1];
    if (morningLbl) {
      const inp = morningLbl.querySelector('input');
      const savedVal = inp?.value;
      morningLbl.textContent = '';
      morningLbl.append(t('morning_field'));
      if (inp) {
        if (savedVal) inp.value = savedVal;
        morningLbl.appendChild(inp);
      }
    }
    if (eveningLbl) {
      const inp = eveningLbl.querySelector('input');
      const savedVal = inp?.value;
      eveningLbl.textContent = '';
      eveningLbl.append(t('evening_field'));
      if (inp) {
        if (savedVal) inp.value = savedVal;
        eveningLbl.appendChild(inp);
      }
    }
    setText('#edit-times-cancel', 'cancel');
    setText('#edit-times-save', 'save');
  }

  function applyLanguageFromProfile(prof) {
    const urlLang = new URLSearchParams(window.location.search).get('lang');
    if (urlLang === 'ru' || urlLang === 'en') {
      window.userLang = urlLang;
    } else {
      const lc = String(prof?.language_code || tg?.initDataUnsafe?.user?.language_code || '').toLowerCase();
      window.userLang = lc.startsWith('ru') ? 'ru' : 'en';
    }
    document.documentElement.lang = window.userLang;
    applyStaticI18n();
  }

  function syncLanguageCode() {
    const lang = window.userLang || 'en';
    apiFetch('/api/profile/language', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language_code: lang }),
    }).catch(() => {});
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
    // daily_time is canonical in Supabase; morning_time may be stale.
    return formatTimeHHMM(prof?.daily_time || prof?.morning_time, '09:00');
  }

  function profileEveningTime(prof) {
    return formatTimeHHMM(prof?.evening_time, '21:00');
  }

  function applyTimesToProfile(prof, morning, evening) {
    const base = { ...(prof || {}) };
    if (morning) {
      const mt = formatTimeHHMM(morning, null);
      if (mt) {
        base.morning_time = mt;
        base.daily_time = mt;
      }
    }
    if (evening) {
      const et = formatTimeHHMM(evening, null);
      if (et) base.evening_time = et;
    }
    return base;
  }

  function syncDisplayTimesCache(morning, evening) {
    const m = formatTimeHHMM(morning, null);
    const e = formatTimeHHMM(evening, null);
    if (!displayTimesCache) {
      displayTimesCache = { morning: '09:00', evening: '21:00' };
    }
    if (m) displayTimesCache.morning = m;
    if (e) displayTimesCache.evening = e;
    saveDisplayTimesToStorage(displayTimesCache.morning, displayTimesCache.evening);
    return displayTimesCache;
  }

  function getDisplayTimes(prof) {
    if (displayTimesCache?.morning && displayTimesCache?.evening) {
      return { ...displayTimesCache };
    }
    const stored = loadDisplayTimesFromStorage();
    if (stored) {
      syncDisplayTimesCache(stored.morning, stored.evening);
      return { ...displayTimesCache };
    }
    const p = prof || profile;
    return {
      morning: profileMorningTime(p),
      evening: profileEveningTime(p),
    };
  }

  /** Paint home-screen 🌅/🌙 times (localStorage + cache beat stale API). */
  function paintMainScreenTimes(morning, evening) {
    const stored = loadDisplayTimesFromStorage();
    const cache = syncDisplayTimesCache(
      morning ?? stored?.morning ?? displayTimesCache?.morning,
      evening ?? stored?.evening ?? displayTimesCache?.evening,
    );
    const m = cache.morning;
    const e = cache.evening;
    document.querySelectorAll(
      '#morning-time-val, .time-value--morning, .times-card .time-item:first-child .time-value',
    ).forEach((el) => {
      el.textContent = m;
      el.setAttribute('data-hm', m);
    });
    document.querySelectorAll(
      '#evening-time-val, .time-value--evening, .times-card .time-item:nth-child(3) .time-value',
    ).forEach((el) => {
      el.textContent = e;
      el.setAttribute('data-hm', e);
    });
    return cache;
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
      name: t('demo_name'),
      main_goal: t('demo_goal'),
      weekly_goal: '',
      streak: 0,
      display_streak: 0,
      current_week: 1,
      weekly_score: 0,
      morning_time: '09:00',
      evening_time: '21:00',
    };
  }

  function profileHasGoals(prof) {
    const g = String(
      prof?.main_goal || prof?.final_goal || prof?.raw_goal || '',
    ).trim();
    return Boolean(g);
  }

  function openBotChat(startPayload) {
    const payload = (startPayload || 'webapp').replace(/^\//, '');
    const link = `https://t.me/${BOT_USERNAME}?start=${encodeURIComponent(payload)}`;
    if (tg?.openTelegramLink) {
      try { tg.openTelegramLink(link); return; } catch (_) {}
    }
    window.open(link, '_blank');
  }

  function showSyncBanner(messageKey) {
    const el = document.getElementById('sync-banner');
    const textEl = el?.querySelector('.sync-banner__text');
    if (textEl && messageKey) textEl.textContent = t(messageKey);
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

  function showIncompleteProfileState() {
    const title = document.querySelector('#empty-state .empty-state__title');
    const text = document.querySelector('#empty-state .empty-state__text');
    const btn = document.getElementById('empty-open-bot');
    if (title) title.textContent = t('incomplete_title');
    if (text) text.textContent = t('incomplete_text');
    if (btn) btn.textContent = t('incomplete_btn');
    showEmptyState();
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

  function todayTaskCompletionStatus(prof) {
    const tc = prof?.task_completed;
    if (tc === 'true') return 'done';
    if (tc === 'false') return 'missed';
    return 'pending';
  }

  function getDayStatus(day) {
    if (day.task_completed === 'true') return 'done';
    if (day.task_completed === 'false') return 'missed';
    if (day.task_completed === 'partial') return 'partial';
    if (day.is_today) return 'today';
    if (day.is_future) return 'future';
    return 'no-data';
  }

  function isTaskCompletedStrict(prof) {
    return todayTaskCompletionStatus(prof) === 'done';
  }

  function renderCalendar() {
    const host = document.getElementById('calendar-grid');
    const badge = document.getElementById('calendar-week-badge');
    const dowHost = document.querySelector('.calendar-dow');
    if (dowHost) {
      const labels = ['1', '2', '3', '4', '5', '6', '7'];
      dowHost.innerHTML = `<span></span>${labels.map((d) => `<span>${d}</span>`).join('')}`;
    }
    if (!host || !calendarData) return;

    const cw = Number(calendarData.current_week || profile?.current_week || 1);
    if (badge) badge.textContent = weekBadgeText(cw);

    const days = Array.isArray(calendarData.days) ? calendarData.days : [];
    const rows = [];
    for (let w = 0; w < 12; w++) {
      const slice = days.slice(w * 7, w * 7 + 7);
      const cells = slice.map((d) => {
        const status = getDayStatus(d);
        return (
          `<div class="calendar-cell">` +
          `<div class="day-dot ${status}" title="${escapeHtml(d.date || '')}"></div>` +
          `</div>`
        );
      }).join('');
      rows.push(
        `<div class="calendar-row${w + 1 === cw ? ' calendar-row--current' : ''}">` +
        `<span class="calendar-row-label">${t('week_short')} ${w + 1}</span>${cells}</div>`
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
    return !tg?.initData && !(BACKEND_TELEGRAM_ID || getTelegramId());
  }

  function canLoadProfile() {
    return Boolean(tg?.initData || BACKEND_TELEGRAM_ID || getTelegramId());
  }


  function startDemoMode(user) {
    profile = buildDemoProfile();
    tasks = [];
    setCanEditName(false);
    setCanEditTimes(false);
    showSyncBanner('sync_banner_text');
    showMain();
    renderAll(user);
    document.querySelector('.settings-block')?.classList.add('loaded');
  }

  /** API недоступен, но пользователь открыл из Telegram — не подменяем демо-целями. */
  function startLoadErrorMode(user, status) {
    profile = {
      name: pickName(user, null),
      main_goal: '',
      weekly_goal: '',
      streak: 0,
      display_streak: 0,
      current_week: 1,
      weekly_score: 0,
      morning_time: '09:00',
      evening_time: '21:00',
    };
    tasks = [];
    setCanEditName(false);
    setCanEditTimes(false);
    if (status === 401 || status === 503) {
      showSyncBanner('err_auth');
    } else {
      showSyncBanner('err_load');
    }
    showMain();
    renderAll(user);
    document.querySelector('.settings-block')?.classList.add('loaded');
  }

  function todayTaskFallback() {
    return `${t('task_pending')} 🌅`;
  }

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
    return main || t('weekly_fallback');
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
      const focusStatus = todayTaskCompletionStatus(prof);
      out.unshift({
        id: '__today_focus__',
        title: focus,
        done: focusStatus === 'done',
        missed: focusStatus === 'missed',
        status: focusStatus,
        virtual: true,
      });
    }
    return out;
  }

  function renderHeader(user, prof) {
    document.getElementById('user-name').textContent = pickName(user, prof);
    const levelBadge = document.getElementById('level-badge');
    if (levelBadge) {
      const level = prof?.level || { name: t('level_spark'), emoji: '·', key: 'spark' };
      levelBadge.textContent = `${level.emoji || '·'} ${levelName(level)}`;
    }
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

    document.getElementById('week-badge').textContent = weekBadgeText(week);
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
        ? t('tasks_empty_discuss')
        : todayTaskFallback();
      host.innerHTML = `<p class="task-empty">${escapeHtml(emptyMsg)}</p>`;
      return;
    }

    host.innerHTML = todayItems.map((t) => {
      const done = Boolean(t.done);
      const missed = Boolean(t.missed);
      const isFocus = t.id === '__today_focus__';
      const id = escapeHtml(t.id);
      const timeLabel = t.time ? formatTimeForDisplay(t.time, '') : '';
      const timeHtml = timeLabel
        ? `<span class="task-time">${escapeHtml(timeLabel)}</span>`
        : '';
      const checkDisabled = done || missed || isFocus;
      const checkLabel = done
        ? t('task_done_aria')
        : missed
          ? t('task_missed_aria')
          : isFocus
            ? t('task_focus_aria')
            : t('task_mark_aria');
      const textStyle = missed ? ' style="text-decoration:line-through;opacity:0.7"' : '';
      return `
        <div class="task-row">
          <button type="button" class="task-check${done ? ' done' : ''}${missed ? ' missed' : ''}" data-id="${id}" ${checkDisabled ? 'disabled' : ''} aria-label="${escapeHtml(checkLabel)}">${done ? '✓' : missed ? '✗' : ''}</button>
          ${timeHtml}
          <span class="task-text${done ? ' done' : ''}${missed ? ' missed' : ''}"${textStyle}>${escapeHtml(t.title || '')}</span>
        </div>`;
    }).join('');
  }

  function renderStreakCircles(prof) {
    const streak = effectiveStreak(prof);
    const container = document.querySelector('.streak-circles');
    if (!container) return;

    container.innerHTML = '';

    for (let i = 0; i < 7; i++) {
      const wrap = document.createElement('div');
      wrap.className = 'streak-day-wrap';

      if (i < streak) {
        const isToday = i === streak - 1;
        wrap.innerHTML = `
          <div class="streak-day active${isToday ? ' is-today' : ''}">
            <img src="./spicespace-logo.jpg" alt="" class="streak-logo-icon"/>
          </div>
        `;
      } else {
        wrap.innerHTML = '<div class="streak-day empty"></div>';
      }

      container.appendChild(wrap);
    }
  }

  function renderStreak(prof) {
    const streak = effectiveStreak(prof);
    const countEl = document.getElementById('streak-count');
    if (countEl) {
      countEl.textContent = streak > 0
        ? `${streak} ${pluralizeDays(streak)} 🔥`
        : t('streak_start');
    }
    renderStreakCircles(prof);
  }

  function renderTimes(prof) {
    const p = prof || profile;
    if (!p && !displayTimesCache) return;
    if (!displayTimesCache && p) {
      syncDisplayTimesCache(
        profileMorningTime(p),
        profileEveningTime(p),
      );
    }
    const dt = getDisplayTimes(p);
    paintMainScreenTimes(dt.morning, dt.evening);
  }

  function renderProfile(prof) {
    if (!prof) return;
    profile = prof;
    const user = tg?.initDataUnsafe?.user || null;
    renderAll(user);
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
    const dt = getDisplayTimes(profile);
    paintMainScreenTimes(dt.morning, dt.evening);
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

  async function checkMilestone() {
    try {
      const resp = await apiFetch('/api/milestone');
      if (!resp.ok) return;
      const data = await resp.json();
      if (data?.milestone) {
        showMilestoneCard(data.milestone);
      }
    } catch (_) {}
  }

  function showMilestoneCard(milestone) {
    document.querySelector('.milestone-overlay')?.remove();

    const completedDays = Number(milestone.days) || 0;
    const totalDots = 12;
    const dotsHTML = Array.from({ length: totalDots }, (_, i) => {
      if (i < completedDays - 1) return '<div class="dot filled"></div>';
      if (i === completedDays - 1) return '<div class="dot current"></div>';
      return '<div class="dot"></div>';
    }).join('');

    const overlay = document.createElement('div');
    overlay.className = 'milestone-overlay';
    overlay.innerHTML = `
      <div class="milestone-card">
        <div class="handle"></div>
        <div class="star-wrap">
          <div class="star">✦</div>
        </div>
        <div class="days-row">
          <span class="days-number">${escapeHtml(String(completedDays))}</span>
          <span class="days-label">${escapeHtml(t('streak_days'))}</span>
        </div>
        <div class="subtitle">${escapeHtml(t('milestone_subtitle'))}</div>
        <div class="message">
          <p>${escapeHtml(milestone.message || '')}</p>
        </div>
        <div class="dots-row">${dotsHTML}</div>
        <button type="button" class="btn-milestone">${escapeHtml(t('milestone_btn'))}</button>
      </div>
    `;
    overlay.querySelector('.btn-milestone')?.addEventListener('click', () => overlay.remove());
    document.body.appendChild(overlay);
    setTimeout(() => launchConfetti(), 400);
  }

  function launchConfetti() {
    const colors = ['#D4F26B', '#ffffff', '#2a2a2a'];
    for (let i = 0; i < 60; i++) {
      setTimeout(() => {
        const c = document.createElement('div');
        c.className = 'confetti-piece';
        c.style.left = Math.random() * 100 + 'vw';
        c.style.background = colors[Math.floor(Math.random() * colors.length)];
        c.style.animationDuration = (0.9 + Math.random() * 0.8) + 's';
        document.body.appendChild(c);
        setTimeout(() => c.remove(), 2000);
      }, i * 20);
    }
  }

  async function fetchProfile() {
    try {
      const resp = await apiFetch(`/api/profile?_t=${Date.now()}`);
      if (!resp || resp.status === 0) {
        const tid = BACKEND_TELEGRAM_ID || getTelegramId();
        if (tid && BACKEND_URL) {
          const directResp = await fetch(
            `${BACKEND_URL}/api/profile?telegram_id=${encodeURIComponent(tid)}&_t=${Date.now()}`,
            { cache: 'no-store' },
          );
          if (directResp.ok) {
            const data = await directResp.json();
            return { ok: true, profile: data.profile || data, user: data.user || null };
          }
          return { ok: false, status: directResp.status };
        }
        return { ok: false, status: 0 };
      }
      if (resp.status === 401 || resp.status === 404) return { ok: false, status: resp.status };
      if (!resp.ok) return { ok: false, status: resp.status };
      const data = await resp.json();
      return { ok: true, profile: data.profile || data, user: data.user || null };
    } catch (e) {
      console.error('fetchProfile error:', e);
      const tid = BACKEND_TELEGRAM_ID || getTelegramId();
      if (tid && BACKEND_URL) {
        try {
          const directResp = await fetch(
            `${BACKEND_URL}/api/profile?telegram_id=${encodeURIComponent(tid)}&_t=${Date.now()}`,
            { cache: 'no-store' },
          );
          if (directResp.ok) {
            const data = await directResp.json();
            return { ok: true, profile: data.profile || data, user: data.user || null };
          }
        } catch (_) {}
      }
      return { ok: false, status: 0 };
    }
  }

  /**
   * Fresh profile from API (cache-bust).
   * @param {{ skipRender?: boolean }} opts — skipRender: only update `profile`, do not repaint UI
   */
  async function loadProfile(opts = {}) {
    try {
      const result = await fetchProfile();
      if (!result.ok || !result.profile) return result;

      profile = result.profile;
      const stored = loadDisplayTimesFromStorage();
      if (stored) {
        profile = applyTimesToProfile(profile, stored.morning, stored.evening);
        syncDisplayTimesCache(stored.morning, stored.evening);
      } else {
        syncDisplayTimesCache(
          profileMorningTime(profile),
          profileEveningTime(profile),
        );
      }

      if (!opts.skipRender) {
        const user = result.user || tg?.initDataUnsafe?.user || null;
        renderProfile(profile);
      } else {
        const dt = getDisplayTimes(profile);
        paintMainScreenTimes(dt.morning, dt.evening);
      }
      return result;
    } catch (e) {
      console.error('loadProfile failed:', e);
      return { ok: false, profile: null, user: null, status: 0 };
    }
  }

  async function fetchTasks() {
    const resp = await apiFetch('/api/tasks');
    if (!resp.ok) return [];
    const data = await resp.json();
    return Array.isArray(data.tasks) ? data.tasks : [];
  }

  async function markStreakOnOpen() {
    try {
      const resp = await apiFetch('/api/mark-day', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ streak_only: true }),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      const keep = getDisplayTimes(profile);
      if (data.profile) {
        profile = applyTimesToProfile(data.profile, keep.morning, keep.evening);
      } else if (profile) {
        if (data.streak != null) profile.streak = data.streak;
        if (data.display_streak != null) profile.display_streak = data.display_streak;
      }
      renderStreak(profile);
      paintMainScreenTimes(keep.morning, keep.evening);
    } catch (_) {}
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
    const morningRaw = document.getElementById('et-morning')?.value || '';
    const eveningRaw = document.getElementById('et-evening')?.value || '';
    const morning = formatTimeHHMM(morningRaw, '');
    const evening = formatTimeHHMM(eveningRaw, '');
    if (!morning || !evening) return;

    const saveBtn = document.getElementById('edit-times-save');
    if (saveBtn) saveBtn.disabled = true;

    try {
      const resp = await apiFetch('/api/profile/times', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ morning_time: morning, evening_time: evening }),
      });
      if (!resp.ok) {
        alert(t('save_failed') || 'Could not save. Try again.');
        return;
      }

      const data = await resp.json();
      profile = applyTimesToProfile(data.profile || profile || {}, morning, evening);
      syncDisplayTimesCache(morning, evening);
      saveDisplayTimesToStorage(morning, evening);
      paintMainScreenTimes(morning, evening);

      const reload = await loadProfile({ skipRender: true });
      if (reload.ok && reload.profile) {
        profile = applyTimesToProfile(reload.profile, morning, evening);
      }
      paintMainScreenTimes(morning, evening);

      closeEditTimesSheet();
      requestAnimationFrame(() => paintMainScreenTimes(morning, evening));
      setTimeout(() => paintMainScreenTimes(morning, evening), 120);
      hapticSuccess();
    } catch (e) {
      console.error('Failed to save times:', e);
      alert(t('save_failed') || 'Could not save. Try again.');
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  async function openEditTimesSheet() {
    const sheet = document.getElementById('edit-times-sheet');
    const morningInput = document.getElementById('et-morning');
    const eveningInput = document.getElementById('et-evening');
    if (!sheet || !morningInput || !eveningInput) return;

    if (BACKEND_URL && !isNoInitData()) {
      await loadProfile({ skipRender: true });
    }

    const dt = getDisplayTimes(profile);
    morningInput.value = dt.morning;
    eveningInput.value = dt.evening;
    paintMainScreenTimes(dt.morning, dt.evening);
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
    document.getElementById('sync-banner-open')?.addEventListener('click', () => openBotChat('webapp'));
    document.getElementById('empty-open-bot')?.addEventListener('click', () => openBotChat('reonboard'));

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
      if (!confirm(t('confirm_reset'))) return;
      await apiFetch('/api/profile/reset', { method: 'POST' });
      if (tg) tg.close();
    });

    document.getElementById('btn-subscription')?.addEventListener('click', () => {
      alert(t('subscription_soon'));
    });

    document.getElementById('btn-stop')?.addEventListener('click', async () => {
      await apiFetch('/api/profile/stop', { method: 'POST' });
      alert(t('bot_stopped'));
      if (tg) tg.close();
    });
  }

  async function start() {
    applyStaticI18n();
    initTelegram();
    BACKEND_TELEGRAM_ID = getTelegramId();
    bindEvents();
    bindEditName();
    bindEditTimes();

    const tgUser = tg?.initDataUnsafe?.user || null;
    const telegramId = BACKEND_TELEGRAM_ID;

    if (!tg?.initData && !telegramId) {
      startDemoMode(null);
      return;
    }

    if (!BACKEND_URL) {
      startDemoMode(tgUser);
      return;
    }

    const result = await loadProfile();
    alert('ok=' + result.ok + ' status=' + result.status + ' goal=' + (profile?.main_goal || 'EMPTY'));
    console.log('result.ok:', result.ok);
    console.log('result.status:', result.status);
    console.log('profile:', JSON.stringify(profile));
    console.log('main_goal:', profile?.main_goal);
    if (!result.ok) {
      if (result.status === 404 && canLoadProfile()) {
        showEmptyState();
        return;
      }
      if (canLoadProfile()) {
        startLoadErrorMode(tgUser, result.status);
        return;
      }
      startDemoMode(tgUser);
      return;
    }

    if (!profileHasGoals(profile)) {
      console.warn('No goals found but showing main screen anyway:', profile?.main_goal);
      // Don't block — show main screen with empty goals
    }

    applyLanguageFromProfile(profile);
    hideSyncBanner();
    showMain();
    setCanEditName(true);
    setCanEditTimes(true);
    await checkMilestone();

    await markStreakOnOpen();
    tasks = await fetchTasks();
    renderTasks(profile, tasks);
    calendarData = await fetchCalendar();
    syncTimezone();
    syncLanguageCode();
    document.querySelector('.settings-block')?.classList.add('loaded');

    const homeTimes = getDisplayTimes(profile);
    paintMainScreenTimes(homeTimes.morning, homeTimes.evening);

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible' || !profile || !BACKEND_URL) return;
      const keep = getDisplayTimes(profile);
      loadProfile({ skipRender: true })
        .then((r) => {
          if (r.ok && r.profile) {
            profile = applyTimesToProfile(r.profile, keep.morning, keep.evening);
          }
          paintMainScreenTimes(keep.morning, keep.evening);
        })
        .catch(() => paintMainScreenTimes(keep.morning, keep.evening));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
