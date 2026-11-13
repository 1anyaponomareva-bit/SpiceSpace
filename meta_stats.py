"""Threads and Instagram numbers for the owner's dashboard, plus token auto-refresh.

Tokens come from Railway (THREADS_ACCESS_TOKEN, INSTAGRAM_ACCESS_TOKEN). Meta
long-lived tokens live 60 days, so a refreshed token is kept in the app_kv table
and used instead, until the owner puts a new one into Railway (detected by a hash
of the env value). Tokens never appear in the returned numbers or in the logs.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

log = logging.getLogger("coach_bot")

THREADS_BASE = "https://graph.threads.net/v1.0"
THREADS_REFRESH_URL = "https://graph.threads.net/refresh_access_token"
IG_BASE = "https://graph.instagram.com/v23.0"
IG_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
# Facebook Login for Business / system user token (does not expire, no refresh)
FB_BASE = "https://graph.facebook.com/v23.0"
REFRESH_EVERY = timedelta(days=7)
POST_DAYS = 14

THREADS_POST_METRICS = ("views", "likes", "replies", "reposts", "quotes")
IG_REEL_METRICS = ("views", "reach", "likes", "comments", "saved", "shares")

Getter = Callable[[str, dict], Any]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _http_get(url: str, params: dict) -> Any:
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, params=params)
    except httpx.HTTPError as e:
        # never pass the exception text on: it may carry the URL with the token
        raise RuntimeError(f"network error: {type(e).__name__}") from None
    if r.status_code >= 400:
        try:
            msg = (r.json().get("error") or {}).get("message") or r.text
        except Exception:
            msg = r.text
        raise RuntimeError(f"HTTP {r.status_code}: {str(msg)[:200]}")
    return r.json()


# ------------------------------------------------------------------ tokens
def current_token(
    kind: str,
    env_name: str,
    refresh_url: str,
    grant_type: str,
    kv_get: Callable[[str], dict | None],
    kv_set: Callable[[str, dict], bool],
    http_get: Getter = _http_get,
) -> tuple[str, dict]:
    """Best token for kind ('threads'/'instagram'); refreshes it once a week.

    Returns (token, info) where info has no secrets: source, refreshed_at, expires_at.
    """
    env_token = os.getenv(env_name, "").strip()
    if not env_token:
        return "", {"configured": False}
    key = f"meta_token_{kind}"
    stored = kv_get(key) or {}
    if stored.get("env_hash") == _hash(env_token) and stored.get("token"):
        token = str(stored["token"])
        info = {"source": "refreshed", "refreshed_at": stored.get("refreshed_at"),
                "expires_at": stored.get("expires_at")}
    else:
        token = env_token
        info = {"source": "railway", "refreshed_at": None, "expires_at": None}
        stored = {"env_hash": _hash(env_token)}

    last = _parse(str(stored.get("refreshed_at") or "")) if stored.get("refreshed_at") else None
    if last is None or _now() - last >= REFRESH_EVERY:
        try:
            data = http_get(refresh_url, {"grant_type": grant_type, "access_token": token})
            new = str((data or {}).get("access_token") or "").strip()
            if new:
                expires_in = int((data or {}).get("expires_in") or 0)
                now = _now()
                record = {
                    "env_hash": _hash(env_token),
                    "token": new,
                    "refreshed_at": now.isoformat(timespec="seconds"),
                    "expires_at": (now + timedelta(seconds=expires_in)).isoformat(timespec="seconds")
                    if expires_in else None,
                }
                if kv_set(key, record):
                    token = new
                    info = {"source": "refreshed", "refreshed_at": record["refreshed_at"],
                            "expires_at": record["expires_at"]}
                else:
                    info["refresh_warning"] = "refreshed token not saved (no app_kv table?)"
                    token = new
        except Exception as e:
            log.warning("meta %s token refresh failed: %s", kind, e)
            info["refresh_error"] = str(e)[:200]
    info["configured"] = True
    return token, info


# ------------------------------------------------------------------ Threads
def _insight_values(data: Any) -> dict:
    out: dict[str, Any] = {}
    for item in (data or {}).get("data") or []:
        name = item.get("name")
        if not name:
            continue
        if "total_value" in item:
            out[name] = (item.get("total_value") or {}).get("value")
        else:
            values = item.get("values") or []
            out[name] = sum(int(v.get("value") or 0) for v in values if isinstance(v, dict)) if values else None
    return out


def collect_threads(token: str, http_get: Getter = _http_get) -> dict:
    since = _now() - timedelta(days=POST_DAYS)
    posts: list[dict] = []
    url = f"{THREADS_BASE}/me/threads"
    params: dict | None = {
        "fields": "id,text,timestamp,permalink,media_type,is_quote_post",
        "since": int(since.timestamp()),
        "limit": 50,
        "access_token": token,
    }
    pages = 0
    while url and pages < 10:
        data = http_get(url, params or {})
        pages += 1
        for item in (data or {}).get("data") or []:
            ts = _parse(item.get("timestamp") or "")
            if ts and ts < since:
                continue
            post = {
                "id": item.get("id"),
                "text": item.get("text") or "",
                "date": item.get("timestamp"),
                "permalink": item.get("permalink"),
                "media_type": item.get("media_type"),
            }
            try:
                ins = http_get(
                    f"{THREADS_BASE}/{item.get('id')}/insights",
                    {"metric": ",".join(THREADS_POST_METRICS), "access_token": token},
                )
                vals = _insight_values(ins)
                for m in THREADS_POST_METRICS:
                    post[m] = vals.get(m)
            except Exception as e:
                post["insights_error"] = str(e)[:200]
            posts.append(post)
        nxt = ((data or {}).get("paging") or {}).get("next")
        url, params = (nxt, {}) if nxt else ("", None)

    account: dict[str, Any] = {}
    until = _now()
    for metric in ("views", "followers_count"):
        try:
            ins = http_get(
                f"{THREADS_BASE}/me/threads_insights",
                {
                    "metric": metric,
                    "since": int((until - timedelta(days=1)).timestamp()),
                    "until": int(until.timestamp()),
                    "access_token": token,
                },
            )
            account.update(_insight_values(ins))
        except Exception as e:
            account[f"{metric}_error"] = str(e)[:200]
    if "views" in account:
        account["views_last_24h"] = account.pop("views")
    if "followers_count" in account:
        account["followers"] = account.pop("followers_count")
    return {"posts": posts, "account": account}


# ------------------------------------------------------------------ Instagram
def collect_instagram(
    token: str,
    http_get: Getter = _http_get,
    base: str = IG_BASE,
    account_id: str = "me",
) -> dict:
    """base=IG_BASE, account_id='me' for Instagram Login tokens;
    base=FB_BASE, account_id=<IG business account id> for Facebook Login tokens."""
    since = _now() - timedelta(days=POST_DAYS)
    fields = "username,followers_count,media_count" + (",user_id" if account_id == "me" else "")
    me = http_get(f"{base}/{account_id}", {"fields": fields, "access_token": token}) or {}
    account: dict[str, Any] = {
        "username": me.get("username"),
        "followers": me.get("followers_count"),
        "media_count": me.get("media_count"),
    }
    until = _now()
    for metric in ("profile_views", "reach", "views"):
        try:
            ins = http_get(
                f"{base}/{account_id}/insights",
                {
                    "metric": metric,
                    "period": "day",
                    "metric_type": "total_value",
                    "since": int((until - timedelta(days=1)).timestamp()),
                    "until": int(until.timestamp()),
                    "access_token": token,
                },
            )
            vals = _insight_values(ins)
            if metric in vals:
                account[f"{metric}_last_24h"] = vals[metric]
        except Exception as e:
            account[f"{metric}_error"] = str(e)[:200]

    reels: list[dict] = []
    url = f"{base}/{account_id}/media"
    params: dict | None = {
        "fields": "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count",
        "limit": 50,
        "access_token": token,
    }
    pages = 0
    stop = False
    while url and pages < 5 and not stop:
        data = http_get(url, params or {})
        pages += 1
        for item in (data or {}).get("data") or []:
            ts = _parse(item.get("timestamp") or "")
            if ts and ts < since:
                stop = True  # newest first
                break
            if str(item.get("media_product_type") or "").upper() != "REELS":
                continue
            reel = {
                "id": item.get("id"),
                "caption": item.get("caption") or "",
                "date": item.get("timestamp"),
                "permalink": item.get("permalink"),
                "likes": item.get("like_count"),
                "comments": item.get("comments_count"),
            }
            try:
                ins = http_get(
                    f"{base}/{item.get('id')}/insights",
                    {"metric": ",".join(IG_REEL_METRICS), "access_token": token},
                )
                vals = _insight_values(ins)
                for m in IG_REEL_METRICS:
                    if vals.get(m) is not None:
                        reel[m] = vals[m]
            except Exception as e:
                reel["insights_error"] = str(e)[:200]
            reels.append(reel)
        nxt = ((data or {}).get("paging") or {}).get("next")
        url, params = (nxt, {}) if nxt else ("", None)
    return {"reels": reels, "account": account}


def _safe(fn: Callable[[], dict]) -> dict:
    try:
        return fn()
    except Exception as e:
        log.warning("meta stats failed: %s", e)
        return {"error": str(e)[:300]}


def collect_all(kv_get, kv_set, http_get: Getter = _http_get) -> dict:
    out: dict[str, Any] = {}
    th_token, th_info = current_token(
        "threads", "THREADS_ACCESS_TOKEN", THREADS_REFRESH_URL, "th_refresh_token",
        kv_get, kv_set, http_get,
    )
    if th_token:
        out["threads"] = {"token": th_info, **_safe(lambda: collect_threads(th_token, http_get))}
    else:
        out["threads"] = {"error": "THREADS_ACCESS_TOKEN не задан в Railway"}
    if th_token:
        out["threads_niche"] = _safe(lambda: search_threads_niche(th_token, http_get))
    out["instagram"] = _instagram_block(kv_get, kv_set, http_get)
    return out


# ------------------------------------------------------------------ niche search (Threads)
# Mostly US self-improvement / body / habits, ~10% Russian. Override with
# THREADS_SEARCH_TERMS="term one; term two; ..." in Railway.
DEFAULT_SEARCH_TERMS = (
    "day 1 of", "self improvement", "glow up", "start over monday", "lose weight mom",
    "new year goals", "accountability", "discipline", "healthy habits", "be kind to yourself",
    "начну с понедельника", "саморазвитие",
)
SEARCH_DAYS = 7
SEARCH_PER_TERM = 10


def search_terms() -> list[str]:
    raw = os.getenv("THREADS_SEARCH_TERMS", "").strip()
    if raw:
        return [t.strip() for t in raw.split(";") if t.strip()][:30]
    return list(DEFAULT_SEARCH_TERMS)


def search_threads_niche(token: str, http_get: Getter = _http_get) -> dict:
    """Top public Threads posts for niche keywords (needs threads_keyword_search).
    Until Meta approves that permission, the API returns only the owner's own posts,
    which are dropped here, so the list stays empty instead of misleading."""
    since = int((_now() - timedelta(days=SEARCH_DAYS)).timestamp())
    try:
        me = http_get(f"{THREADS_BASE}/me", {"fields": "username", "access_token": token}) or {}
        own = str(me.get("username") or "").lower()
    except Exception:
        own = ""
    posts: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    own_only = 0
    for term in search_terms():
        try:
            data = http_get(
                f"{THREADS_BASE}/keyword_search",
                {
                    "q": term,
                    "search_type": "TOP",
                    "since": since,
                    "limit": SEARCH_PER_TERM,
                    "fields": "id,text,media_type,permalink,timestamp,username,has_replies,is_quote_post,is_reply",
                    "access_token": token,
                },
            )
        except Exception as e:
            errors.append(f"{term}: {str(e)[:150]}")
            continue
        for rank, item in enumerate((data or {}).get("data") or [], start=1):
            pid = str(item.get("id") or "")
            if not pid or pid in seen or item.get("is_reply"):
                continue
            if own and str(item.get("username") or "").lower() == own:
                own_only += 1
                continue
            seen.add(pid)
            posts.append({
                "term": term,
                "rank": rank,
                "username": item.get("username"),
                "text": item.get("text") or "",
                "date": item.get("timestamp"),
                "permalink": item.get("permalink"),
                "media_type": item.get("media_type"),
            })
    out: dict[str, Any] = {"terms": search_terms(), "posts": posts}
    if errors:
        out["errors"] = errors[:10]
        if len(errors) == len(search_terms()):
            out["error"] = "поиск не работает: нужно разрешение threads_keyword_search"
    if not posts and own_only and not errors:
        out["note"] = ("Meta пока отдаёт только свои посты: разрешение threads_keyword_search "
                       "ещё не одобрено (нужна проверка приложения Meta)")
    return out


def find_ig_account_id(token: str, http_get: Getter = _http_get) -> str:
    """IG business account linked to a Facebook Page the token can see."""
    data = http_get(
        f"{FB_BASE}/me/accounts",
        {"fields": "name,instagram_business_account", "limit": 50, "access_token": token},
    )
    for page in (data or {}).get("data") or []:
        ig = (page or {}).get("instagram_business_account") or {}
        if ig.get("id"):
            return str(ig["id"])
    return ""


def ig_id_from_token(token: str, http_get: Getter = _http_get) -> str:
    """System user tokens for an Instagram asset: the account id is in the token's
    granular scopes (instagram_basic / instagram_manage_insights target_ids)."""
    data = http_get(
        f"{FB_BASE}/debug_token", {"input_token": token, "access_token": token}
    )
    scopes = ((data or {}).get("data") or {}).get("granular_scopes") or []
    for wanted in ("instagram_manage_insights", "instagram_basic"):
        for sc in scopes:
            if (sc or {}).get("scope") == wanted and (sc.get("target_ids") or []):
                return str(sc["target_ids"][0])
    return ""


def _instagram_block(kv_get, kv_set, http_get: Getter = _http_get) -> dict:
    """INSTAGRAM_API=facebook (or INSTAGRAM_BUSINESS_ACCOUNT_ID set) -> graph.facebook.com
    with a non-expiring system user token; otherwise Instagram Login + weekly refresh."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        return {"error": "INSTAGRAM_ACCESS_TOKEN не задан в Railway"}
    account_id = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "").strip()
    mode = os.getenv("INSTAGRAM_API", "").strip().lower()
    # the token itself says which API it is for: IG... = Instagram Login, EAA... = Facebook
    if token.startswith("IG"):
        mode = "instagram"
    elif token.startswith("EAA"):
        mode = "facebook"
    if mode != "instagram" and (account_id or mode == "facebook"):
        info: dict[str, Any] = {"configured": True, "source": "railway", "api": "facebook"}
        if not account_id:
            tried: list[str] = []
            for finder in (ig_id_from_token, find_ig_account_id):
                try:
                    account_id = finder(token, http_get)
                except Exception as e:
                    tried.append(f"{finder.__name__}: {str(e)[:150]}")
                    account_id = ""
                if account_id:
                    break
            if tried and not account_id:
                info["lookup_errors"] = tried
            if not account_id:
                return {"token": info, "error": "токен не видит Facebook-страницу со связанным "
                        "Instagram; задайте INSTAGRAM_BUSINESS_ACCOUNT_ID"}
        info["account_id"] = account_id
        return {"token": info, **_safe(lambda: collect_instagram(token, http_get, FB_BASE, account_id))}
    ig_token, ig_info = current_token(
        "instagram", "INSTAGRAM_ACCESS_TOKEN", IG_REFRESH_URL, "ig_refresh_token",
        kv_get, kv_set, http_get,
    )
    ig_info["api"] = "instagram"
    return {"token": ig_info, **_safe(lambda: collect_instagram(ig_token, http_get))}
