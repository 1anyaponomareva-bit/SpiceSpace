# Language fix: no Russian in English users' context

## What was wrong (found in the code)

For every user, English included, the chat prompt contained Russian:

1. `get_current_time_for_user` always returns Russian weekday and month, and `user_message_with_fresh_time` prefixes every user message with `[Текущее время пользователя: …]`.
2. `CURRENT_TIME_INSTRUCTION` is Russian only.
3. `COACH_STYLE_INSTRUCTION` (main.py) is Russian only and contains the line "Ты ведёшь диалог на русском". It goes into the system prompt for everyone.
4. In `_coach_reply`, the blocks "Важные факты", "Профиль личности…", "Прошлая неделя", the personality labels and the level lines are Russian for everyone.
5. `claude_client.response_text` returns Russian fallbacks to everyone.

The model copies the language of its context. Shouting "NEVER write in Russian" three times does not beat a Russian prefix on every message.

## Files

Put `lang_context.py` and `test_lang_context.py` next to `prompts.py`. Do not change anything else before step 1 is done and the tests pass.

## Step 1. prompts.py

1. Add after the `claude_budget` import:

```python
from lang_context import (
    TIME_LINE_RE,
    current_time_instruction,
    format_user_time,
    time_label,
)
```

2. Add above `get_current_time_for_user`:

```python
def _lang_of(profile: dict | None) -> str:
    if isinstance(profile, dict):
        return str(profile.get("language_code") or "en")
    return "en"
```

3. In `get_current_time_for_user`, replace the last four lines (`now = ...` through `return f"{now.strftime..."`) with:

```python
    return format_user_time(datetime.now(tz), _lang_of(profile))
```

4. In `user_message_with_fresh_time`, replace the `prefix = ...` line with:

```python
    prefix = f"[{time_label(_lang_of(profile))}: {stamp}]"
```

5. In `prepend_user_time`, replace the `line = ...` and `dynamic_bit = ...` lines with:

```python
    lang = _lang_of(profile)
    line = f"{time_label(lang)}: {get_current_time_for_user(profile)}"
    dynamic_bit = f"{line}\n\n{current_time_instruction(lang)}"
```

6. In `refresh_user_time_in_system`, replace the body before `return prepend_user_time(...)` with:

```python
    fresh_line = f"{time_label(_lang_of(profile))}: {get_current_time_for_user(profile)}"
    text = (system or "").strip()
    if TIME_LINE_RE.search(text):
        return TIME_LINE_RE.sub(lambda _m: fresh_line, text, count=1)
```

(The regex matches both the Russian and the English label, so refresh keeps working.)

7. Run `pytest test_lang_context.py`. All six tests must pass. I ran exactly these edits on a copy of your `prompts.py` and they pass; on the original the last test fails on the Russian time line.

## Step 2. main.py, `_coach_reply`

1. Imports:

```python
from lang_context import (
    coach_style_for,
    facts_header,
    last_week_text,
    personality_intro,
    personality_text as personality_text_lang,
    level_context as level_context_text,
)
```

2. Move these two lines up, right after `prof` is loaded and refreshed (they are now below `extra`):

```python
lang = str(prof.get("language_code") or "en")
is_russian = lang.lower().startswith("ru")
```

3. Replace the Russian blocks:

```python
personality_text = personality_text_lang(personality, lang)
...
weekly_context = last_week_text(last_week, lang) if last_week else ""
...
if facts_text:
    extra_parts.append(f"{facts_header(lang)}\n{facts_text}")
if personality_text:
    extra_parts.append(f"{personality_intro(lang)}\n{personality_text}")
if weekly_context:
    extra_parts.append(weekly_context)   # keep your own "Прошлая неделя" label only for ru if you want it
```

and replace the whole `level_context = ...` string plus the `if level["key"] == ...` chain with:

```python
extra_parts.append(
    level_context_text(level["key"], level["name"], completed_count, lang)
)
```

Check that `level["name"]` is not a Russian word for English users; if it is, add English names next to the Russian ones in `_calculate_user_level`.

4. In both `build_chat_system(...)` calls (chat and photo) change:

```python
coach_style=coach_style_for(lang, COACH_STYLE_INSTRUCTION),
```

(In the photo handler use `prof.get("language_code")` if `lang` is not defined there.)

## Step 3. claude_client.py (rare path, but it is a Russian string for everyone)

```python
from lang_context import empty_fallback, refusal_fallback

def response_text(response: object, lang: str = "ru") -> str:
    ...
    if stop == "refusal":
        return refusal_fallback(lang)
    return empty_fallback(lang)
```

Add `lang: str = "ru"` to `generate(...)` and end it with `return response_text(response, lang)`. The default keeps old behaviour for callers you have not touched. In `_coach_reply` pass `lang=lang` to `claude_generate(...)`.

## Not now

- Removing the three copies of the "CRITICAL: English ONLY" text (`build_chat_system`, the two `extra` prefixes in main.py). They cost tokens and are loud, but remove them only after you have run 20-30 English dialogs with steps 1-3 in place and seen no Russian.
- Morning, evening, re-engagement and photo paths were not audited. Run this in the repo: `grep -n "[А-Яа-я]" main.py` and look for Russian literals that are added to prompts without a language check. The test covers the chat prompt only.

## How to check on real dialogs

Make a test account with `language_code = en`. Send 20 messages: practice, an emotion, a task, a refusal, a question about the day of the week. Count replies with any Cyrillic. Target: zero. If some remain, send me those replies and the system prompt for that call.
