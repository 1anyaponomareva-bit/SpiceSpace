"""Publishes the threads Anya picked («Беру» in Пульт) at set times, then the reply chain.

The queue lives in app_kv under QUEUE_KEY. Claude (on her word) sends the day's picks
to POST /api/threads/queue; a scheduler job calls run_once() every minute.

Pools, Moscow time (her audience): morning 9-11, day 13-15, evening 19-21. Inside a
pool threads go out one by one, a random SPACING_MIN..SPACING_MAX apart (an even
12-minute beat looked robotic; Anya, 18.11.2026: 15 a day, 5 per pool). Each thread may carry a two-message
chain: `reply` (no link) under the thread, then `reply2` (with the link) under `reply`.
The chain goes out once the thread has CHAIN_MIN_REPLIES comments, or CHAIN_AFTER
after posting, whichever comes first.

Nothing here logs or returns the token.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx

log = logging.getLogger("coach_bot")

QUEUE_KEY = "threads_queue"
THREADS_BASE = "https://graph.threads.net/v1.0"
MSK = timezone(timedelta(hours=3))
POOLS = {"morning": (9, 0), "day": (13, 0), "evening": (19, 0)}
POOL_ORDER = ("morning", "day", "evening")
POOL_SIZE = 5
SPACING_MIN, SPACING_MAX = 15, 25  # minutes between threads in a pool
CHAIN_MIN_REPLIES = 15
CHAIN_AFTER = timedelta(hours=2)
CHAIN_CHECK_AFTER = timedelta(minutes=20)
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
             rng: random.Random | None = None) -> list[dict]:
    """Give each picked thread a pool and a time. Items may carry pool
    (morning/day/evening); the rest fill pools in order, POOL_SIZE each."""
    now = now or _now()
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
        h, m = POOLS[pool]
        start = datetime(day.year, day.month, day.day, h, m, tzinfo=MSK)
        if start < now:
            # picks came in late: start this pool right away
            start = now + timedelta(minutes=2)
        at = start
        for it in by_pool[pool]:
            text = str(it.get("text") or "").strip()
            if not text:
                continue
            out.append({
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
            at += timedelta(minutes=rng.randint(SPACING_MIN, SPACING_MAX))
    return out


def load(kv_get) -> list[dict]:
    data = kv_get(QUEUE_KEY) or {}
    items = data.get("items") if isinstance(data, dict) else None
    return [x for x in (items or []) if isinstance(x, dict)]


def _prune(items: list[dict], now: datetime) -> list[dict]:
    cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    return [x for x in items if str(x.get("day") or "") >= cutoff]


def put_day(kv_get, kv_set, day: date, picked: list[dict], now: datetime | None = None) -> list[dict]:
    """Replace the not-yet-posted threads of `day` with a fresh schedule.
    Threads already posted (same tid) stay as they are and are not posted twice."""
    now = now or _now()
    with _lock:
        items = load(kv_get)
        d = day.isoformat()
        # resending the day re-queues what failed or was cancelled; posted stays posted
        done = {x.get("tid") for x in items if x.get("day") == d and x.get("status") == "posted"}
        keep = [x for x in items if x.get("day") != d or x.get("status") == "posted"]
        fresh = [x for x in schedule(day, picked, now) if x["tid"] not in done]
        items = _prune(keep + fresh, now)
        if not kv_set(QUEUE_KEY, {"items": items}):
            raise RuntimeError("queue not saved (app_kv)")
        return [x for x in items if x.get("day") == d]


def cancel_day(kv_get, kv_set, day: date) -> int:
    with _lock:
        items = load(kv_get)
        d = day.isoformat()
        n = 0
        for x in items:
            if x.get("day") == d and x.get("status") == "queued":
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
             sleep: Callable[[float], None] | None = None) -> list[str]:
    """One tick: post at most one due thread, then any chains that are due.
    Returns short human notes for the owner (no secrets)."""
    now = now or _now()
    notes: list[str] = []
    if not token:
        return notes
    with _lock:
        items = load(kv_get)
        changed = False

        due = [x for x in items if x.get("status") == "queued" and (_parse(x.get("at")) or now) <= now]
        due.sort(key=lambda x: str(x.get("at")))
        if due:
            it = due[0]
            it["attempts"] = int(it.get("attempts") or 0) + 1
            try:
                it["post_id"] = _publish_text(token, it["text"], post=post, get=get, sleep=sleep)
                it["status"] = "posted"
                it["posted_at"] = _iso(now)
                it.pop("error", None)
                log.info("threads published tid=%s day=%s pool=%s", it.get("tid"), it.get("day"), it.get("pool"))
            except Exception as e:
                it["error"] = str(e)[:200]
                if it["attempts"] >= MAX_ATTEMPTS:
                    it["status"] = "error"
                    notes.append(f"⚠️ Тред {it.get('tid')} не вышел: {it['error']}")
                else:
                    it["at"] = _iso(now + timedelta(minutes=5))
                log.warning("threads publish failed tid=%s: %s", it.get("tid"), it["error"])
            changed = True

        for it in items:
            if it.get("status") != "posted" or not it.get("reply") or it.get("chain"):
                continue
            posted = _parse(it.get("posted_at"))
            if not posted or now - posted < CHAIN_CHECK_AFTER:
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
                    notes.append(f"⚠️ Ответ под тредом {it.get('tid')} не вышел: {it['chain_error']}")
            break  # at most one chain per tick

        if changed:
            kv_set(QUEUE_KEY, {"items": items})
    return notes


def summary(items: list[dict]) -> list[dict]:
    """What the owner may see: no tokens, short texts."""
    keys = ("day", "tid", "pool", "at", "status", "posted_at", "post_id", "chain", "chain_at", "error", "chain_error")
    return [{k: x.get(k) for k in keys if x.get(k) is not None} | {"text": str(x.get("text") or "")[:80]}
            for x in items]
