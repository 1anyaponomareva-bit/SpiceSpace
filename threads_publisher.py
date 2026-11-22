"""Publishes the threads Anya picked («Беру» in Пульт) at set times, then the reply chain.

The queue lives in app_kv under QUEUE_KEY. Claude (on her word) sends the day's picks
to POST /api/threads/queue; a scheduler job calls run_once() every minute.

Pools, Moscow time (her audience): morning 9-11, day 13-15, evening 19-21. Inside a
pool threads go out one by one, a random SPACING_MIN..SPACING_MAX apart (an even
12-minute beat looked robotic; Anya, 18.11.2026: 15 a day, 5 per pool). Each thread may carry a two-message
chain: `reply` (no link) under the thread, then `reply2` (with the link) under `reply`.
The chain goes out once the thread has CHAIN_MIN_REPLIES comments, or CHAIN_AFTER
after posting, whichever comes first.

Two accounts (Anya, 22.11.2026): "ru" (the first one, Moscow pools) and "en" (@anyapnm,
US Eastern pools 8-10, 12-14, 19-21). Every item carries `account` (missing = "ru"); a
day of one account is queued, replaced and cancelled without touching the other.

Nothing here logs or returns the token.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("coach_bot")

QUEUE_KEY = "threads_queue"
THREADS_BASE = "https://graph.threads.net/v1.0"
MSK = timezone(timedelta(hours=3))
POOLS = {"morning": (9, 0), "day": (13, 0), "evening": (19, 0)}
POOL_ORDER = ("morning", "day", "evening")
# account -> (zone, pools, Railway variable with its token, app_kv kind of the refreshed copy)
ACCOUNTS: dict[str, dict[str, Any]] = {
    "ru": {"tz": MSK, "pools": POOLS, "env": "THREADS_ACCESS_TOKEN", "kind": "threads"},
    "en": {
        "tz": ZoneInfo("America/New_York"),
        "pools": {"morning": (8, 0), "day": (12, 0), "evening": (19, 0)},
        "env": "THREADS_ACCESS_TOKEN_EN",
        "kind": "threads_en",
    },
}
DEFAULT_ACCOUNT = "ru"


def norm_account(value: Any) -> str:
    """'en' / 'ru'; anything else (or nothing) is the first account."""
    v = str(value or "").strip().lower()
    return v if v in ACCOUNTS else DEFAULT_ACCOUNT


def account_of(item: dict) -> str:
    return norm_account(item.get("account"))
POOL_SIZE = 5
SPACING_MIN, SPACING_MAX = 15, 25  # minutes between threads in a pool
TIGHT_MIN, TIGHT_MAX = 10, 12  # evening pool that would run past 23:59 Moscow
MIN_TIGHT = 5  # never closer than this, even when the evening is overfull
CHAIN_MIN_REPLIES = 15
CHAIN_AFTER = timedelta(hours=2)
CHAIN_CHECK_AFTER = timedelta(minutes=20)
CHAIN_BACKFILL_GAP = timedelta(minutes=6)
MAX_ATTEMPTS = 3
KEEP_DAYS = 4
MAX_TEXT = 500

_lock = threading.Lock()

Poster = Callable[[str, dict], Any]
Getter = Callable[[str, dict], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _http(method: str, url: str, params: dict) -> Any:
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.request(method, url, params=params)
    except httpx.HTTPError as e:
        raise RuntimeError(f"network error: {type(e).__name__}") from None
    if r.status_code >= 400:
        try:
            msg = (r.json().get("error") or {}).get("message") or r.text
        except Exception:
            msg = r.text
        raise RuntimeError(f"HTTP {r.status_code}: {str(msg)[:200]}")
    return r.json()


def http_post(url: str, params: dict) -> Any:
    return _http("POST", url, params)


def http_get(url: str, params: dict) -> Any:
    return _http("GET", url, params)


# ------------------------------------------------------------------ schedule
def schedule(day: date, items: list[dict], now: datetime | None = None,
             rng: random.Random | None = None, account: str = DEFAULT_ACCOUNT) -> list[dict]:
    """Give each picked thread a pool and a time. Items may carry pool
    (morning/day/evening); the rest fill pools in order, POOL_SIZE each.
    Times are in the account's own zone (Moscow for ru, US Eastern for en)."""
    now = now or _now()
    account = norm_account(account)
    zone, pools = ACCOUNTS[account]["tz"], ACCOUNTS[account]["pools"]
    rng = rng or random.Random()
    by_pool: dict[str, list[dict]] = {p: [] for p in POOL_ORDER}
    loose: list[dict] = []
    for it in items:
        pool = str(it.get("pool") or "").strip().lower()
        (by_pool[pool] if pool in by_pool else loose).append(it)
    for it in loose:
        target = next((p for p in POOL_ORDER if len(by_pool[p]) < POOL_SIZE), POOL_ORDER[-1])
        by_pool[target].append(it)

    out: list[dict] = []
    for pool in POOL_ORDER:
        h, m = pools[pool]
        start = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)
        if start < now:
            # picks came in late: start this pool right away
            start = now + timedelta(minutes=2)
        todo = [it for it in by_pool[pool] if str(it.get("text") or "").strip()]
        deadline = datetime(day.year, day.month, day.day, 23, 59, tzinfo=zone) if pool == POOL_ORDER[-1] else None
        for it, at in zip(todo, _pool_times(start, len(todo), deadline, rng)):
            text = str(it.get("text") or "").strip()
            out.append({
                "account": account,
                "day": day.isoformat(),
                "tid": str(it.get("tid") or it.get("id") or ""),
                "pool": pool,
                "at": _iso(at),
                "text": text[:MAX_TEXT],
                "reply": str(it.get("reply") or "").strip()[:MAX_TEXT],
                "reply2": str(it.get("reply2") or "").strip()[:MAX_TEXT],
                "status": "queued",
                "attempts": 0,
            })
    return out


def _pool_times(start: datetime, n: int, deadline: datetime | None,
                rng: random.Random) -> list[datetime]:
    """Times for n threads from start, the usual 15-25 minutes apart. The evening pool
    must end by 23:59 Moscow (Anya, 21.11.2026: «все сегодня выкладывай», threads of the
    day go out that day): if it doesn't fit, 10-12 minutes apart, and if even that
    doesn't fit, evenly spread up to the deadline (never closer than MIN_TIGHT)."""
    def walk(lo: int, hi: int) -> list[datetime]:
        out, at = [], start
        for _ in range(n):
            out.append(at)
            at += timedelta(minutes=rng.randint(lo, hi))
        return out

    times = walk(SPACING_MIN, SPACING_MAX)
    if deadline is None or n < 2 or times[-1] <= deadline:
        return times
    times = walk(TIGHT_MIN, TIGHT_MAX)
    if times[-1] <= deadline or deadline <= start:
        return times  # sent after the deadline: no burst, just the tight beat
    step =max((deadline - start) / (n - 1), timedelta(minutes=MIN_TIGHT))
    return [start + step * i for i in range(n)]


def load(kv_get) -> list[dict]:
    data = kv_get(QUEUE_KEY) or {}
    items = data.get("items") if isinstance(data, dict) else None
    return [x for x in (items or []) if isinstance(x, dict)]


def _prune(items: list[dict], now: datetime) -> list[dict]:
    cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    return [x for x in items if str(x.get("day") or "") >= cutoff]


def put_day(kv_get, kv_set, day: date, picked: list[dict], now: datetime | None = None,
            account: str = DEFAULT_ACCOUNT) -> list[dict]:
    """Replace the not-yet-posted threads of `day` (of this account) with a fresh schedule.
    Threads already posted (same tid) stay as they are and are not posted twice."""
    now = now or _now()
    account = norm_account(account)
    with _lock:
        items = load(kv_get)
        d = day.isoformat()

        def mine(x: dict) -> bool:
            return x.get("day") == d and account_of(x) == account

        # resending the day re-queues what failed or was cancelled; posted stays posted
        done = {x.get("tid") for x in items if mine(x) and x.get("status") == "posted"}
        keep = [x for x in items if not mine(x) or x.get("status") == "posted"]
        # threads already out don't get a slot: scheduling them and dropping them after
        # left 50-100 minute holes and pushed the evening into the night (21.11.2026)
        planned = schedule(
            day, [p for p in picked if str(p.get("tid") or p.get("id") or "") not in done], now,
            account=account,
        )
        # a thread that went out without a chain gets one if the resend carries it
        # (Anya, 18.11.2026: under every thread - thread + reply + reply2);
        # spread them out so links don't land all in one burst
        by_tid = {
            str(p.get("tid") or p.get("id") or ""): {
                "reply": str(p.get("reply") or "").strip()[:MAX_TEXT],
                "reply2": str(p.get("reply2") or "").strip()[:MAX_TEXT],
            }
            for p in picked
        }
        slot = now
        for x in keep:
            if not mine(x) or x.get("reply") or x.get("chain"):
                continue
            new = by_tid.get(x.get("tid"))
            if new and new.get("reply"):
                x["reply"], x["reply2"] = new["reply"], new.get("reply2", "")
                x["chain_not_before"] = _iso(slot)
                slot += CHAIN_BACKFILL_GAP
        fresh = [x for x in planned if x["tid"] not in done]
        items = _prune(keep + fresh, now)
        if not kv_set(QUEUE_KEY, {"items": items}):
            raise RuntimeError("queue not saved (app_kv)")
        return [x for x in items if mine(x)]


def cancel_day(kv_get, kv_set, day: date, account: str = DEFAULT_ACCOUNT) -> int:
    account = norm_account(account)
    with _lock:
        items = load(kv_get)
        d = day.isoformat()
        n = 0
        for x in items:
            if x.get("day") == d and account_of(x) == account and x.get("status") == "queued":
                x["status"] = "cancelled"
                n += 1
        kv_set(QUEUE_KEY, {"items": items})
        return n


# ------------------------------------------------------------------ Threads API
def _publish_text(token: str, text: str, reply_to: str = "", post: Poster = http_post,
                  get: Getter = http_get, sleep: Callable[[float], None] | None = None) -> str:
    params = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to:
        params["reply_to_id"] = reply_to
    try:
        container = post(f"{THREADS_BASE}/me/threads", params) or {}
    except RuntimeError as e:
        raise RuntimeError(f"create: {e}") from None
    cid = str(container.get("id") or "")
    if not cid:
        raise RuntimeError("no container id")
    # Publishing a container that is not ready yet fails with
    # "The requested resource does not exist" — wait until it is FINISHED.
    sleep = sleep or time.sleep
    for _ in range(10):
        sleep(3)
        try:
            st = get(f"{THREADS_BASE}/{cid}", {"fields": "status,error_message", "access_token": token}) or {}
        except Exception:
            continue
        status = str(st.get("status") or "")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {status}: {str(st.get('error_message') or '')[:120]}")
    try:
        res = post(f"{THREADS_BASE}/me/threads_publish", {"creation_id": cid, "access_token": token}) or {}
    except RuntimeError as e:
        raise RuntimeError(f"publish: {e}") from None
    pid = str(res.get("id") or "")
    if not pid:
        raise RuntimeError("no post id")
    return pid


def permalink(token: str, post_id: str, get: Getter = http_get) -> str:
    try:
        return str((get(f"{THREADS_BASE}/{post_id}", {"fields": "permalink", "access_token": token}) or {}).get("permalink") or "")
    except Exception:
        return ""


def _reply_count(token: str, post_id: str, get: Getter = http_get) -> int:
    data = get(f"{THREADS_BASE}/{post_id}/insights", {"metric": "replies", "access_token": token}) or {}
    for row in data.get("data") or []:
        vals = row.get("values") or []
        if vals:
            try:
                return int(vals[-1].get("value") or 0)
            except (TypeError, ValueError):
                return 0
        tv = row.get("total_value") or {}
        if "value" in tv:
            return int(tv.get("value") or 0)
    return 0


def run_once(kv_get, kv_set, token: str, now: datetime | None = None,
             post: Poster = http_post, get: Getter = http_get,
             sleep: Callable[[float], None] | None = None,
             account: str = DEFAULT_ACCOUNT) -> list[str]:
    """One tick for one account: post at most one due thread, then any chains that are due.
    Returns short human notes for the owner (no secrets)."""
    now = now or _now()
    account = norm_account(account)
    notes: list[str] = []
    if not token:
        return notes
    tag = "" if account == DEFAULT_ACCOUNT else f" ({account})"
    with _lock:
        items = load(kv_get)
        changed = False

        due = [x for x in items if account_of(x) == account and x.get("status") == "queued"
               and (_parse(x.get("at")) or now) <= now]
        due.sort(key=lambda x: str(x.get("at")))
        if due:
            it = due[0]
            it["attempts"] = int(it.get("attempts") or 0) + 1
            try:
                it["post_id"] = _publish_text(token, it["text"], post=post, get=get, sleep=sleep)
                it["status"] = "posted"
                it["posted_at"] = _iso(now)
                it.pop("error", None)
                log.info("threads published account=%s tid=%s day=%s pool=%s",
                         account, it.get("tid"), it.get("day"), it.get("pool"))
            except Exception as e:
                it["error"] = str(e)[:200]
                if it["attempts"] >= MAX_ATTEMPTS:
                    it["status"] = "error"
                    notes.append(f"⚠️ Тред{tag} {it.get('tid')} не вышел: {it['error']}")
                else:
                    it["at"] = _iso(now + timedelta(minutes=5))
                log.warning("threads publish failed tid=%s: %s", it.get("tid"), it["error"])
            changed = True

        for it in items:
            if account_of(it) != account:
                continue
            if it.get("status") != "posted" or not it.get("reply") or it.get("chain"):
                continue
            posted = _parse(it.get("posted_at"))
            if not posted or now - posted < CHAIN_CHECK_AFTER:
                continue
            not_before = _parse(it.get("chain_not_before"))
            if not_before and now < not_before:
                continue
            last_check = _parse(it.get("chain_checked_at"))
            if last_check and now - last_check < timedelta(minutes=10) and now - posted < CHAIN_AFTER:
                continue
            try:
                replies = _reply_count(token, it["post_id"], get=get)
            except Exception:
                replies = 0
            it["chain_checked_at"] = _iso(now)
            changed = True
            if replies < CHAIN_MIN_REPLIES and now - posted < CHAIN_AFTER:
                continue
            try:
                r1 = _publish_text(token, it["reply"], reply_to=it["post_id"], post=post, get=get, sleep=sleep)
                it["reply_id"] = r1
                if it.get("reply2"):
                    it["reply2_id"] = _publish_text(token, it["reply2"], reply_to=r1, post=post, get=get, sleep=sleep)
                it["chain"] = "posted"
                it["chain_at"] = _iso(now)
                log.info("threads chain posted tid=%s replies=%s", it.get("tid"), replies)
            except Exception as e:
                it["chain_attempts"] = int(it.get("chain_attempts") or 0) + 1
                it["chain_error"] = str(e)[:200]
                if it["chain_attempts"] >= MAX_ATTEMPTS:
                    it["chain"] = "error"
                    notes.append(f"⚠️ Ответ под тредом{tag} {it.get('tid')} не вышел: {it['chain_error']}")
            break  # at most one chain per tick

        if changed:
            kv_set(QUEUE_KEY, {"items": items})
    return notes


def summary(items: list[dict]) -> list[dict]:
    """What the owner may see: no tokens, short texts."""
    keys = ("day", "tid", "pool", "at", "status", "posted_at", "post_id", "chain", "chain_at",
            "reply_id", "reply2_id", "chain_not_before", "error", "chain_error")
    return [{k: x.get(k) for k in keys if x.get(k) is not None}
            | {"account": account_of(x), "text": str(x.get("text") or "")[:80]}
            for x in items]
