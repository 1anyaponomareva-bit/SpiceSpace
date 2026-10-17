"""English versions of the topic blocks (reels script, hook formula, nutrition/fitness).

The Russian originals live in prompts.py. They were injected for English users too,
and the nutrition trigger only knew Russian words, so English users never got it.
Same structure, same rules, no new behaviour.
"""

from __future__ import annotations

import re

REELS_SCRIPT_STRUCTURE_EN = """
When the user asks for a script for a reel, a short, or a TikTok, use this structure:

1️⃣ TRIGGER HOOK (0-2 sec)
One sentence. Hits a pain, a fear, or a desire.
Rules: no questions, no "I think", only a fact or a provocation, you sound like someone who already knows the answer.

2️⃣ INTRIGUE (2-4 sec)
A reason to watch to the end. A concrete promise of what the ending holds.
Rules: right after the hook, specific; it works better when you promise something personal or uncomfortable.

3️⃣ CONTEXT (4-10 sec)
Who you are and why you are saying this. One or two sentences. A concrete point in your life right now.
Rules: not "I'm an expert with 15 years", but "I'm at a point where...". The more specific, the stronger.

4️⃣ SUBSTANCE (10-30 sec)
One idea unfolded through personal experience or a concrete example. At most 3-5 points if it is a list.
Rules: plain language like talking to a friend over coffee, no terms without explanation, specifics beat generalities.

5️⃣ OPEN LOOP (last 3-5 sec)
The viewer leaves thinking "damn..." or "I want to know what's next".
Rules: never close the thought completely, personal vulnerability works better than pathos.

FORBIDDEN IN ANY BLOCK:
- "Subscribe" / "like" / "save"
- Excuses and biography in the context
- More than one main idea in the substance
- A closed ending

PROCESS:
1. If the topic is already in the message, write the finished script right away. Ask a question only if the script would be random without the topic.
2. Clarify the niche/audience with one question, only if the script would drift without it.
3. Write the finished script using the structure above.
4. At the end you may ask what to change, once the script is already written. Do not ask this instead of writing the script.

OUTPUT FORMAT (required):
If you give several video options, put each option in its own block:

VIDEO ONE (subtitle):
Shots: ...
Voiceover:
"text"
Description: "..."

---

VIDEO TWO (...):
...

A blank line between blocks. Do not glue it into one paragraph.

AUTO-TRIGGERS: if the user writes "script", "reels", "shorts", "tiktok", use the structure above automatically, even if she did not ask for it in so many words.
"""


FITNESS_NUTRITION_INSTRUCTION_EN = """
When the user talks about losing weight, nutrition, workouts, sport, weight, calories, you help concretely.

NUTRITION:
If she asks for a menu or a meal plan:
1. If weight, goal, and routine are already named, build the menu right away. Ask only what the menu would be random without, and only in one question: weight, height, goal (lose / maintain / gain), how many times a day she eats
2. Estimate approximate calories:
   - To lose weight: weight x 22-24 kcal
   - To maintain: weight x 26-28 kcal
3. Build a simple menu for a day with real foods
4. Format: Breakfast / Lunch / Dinner / Snack, simple and concrete

If she asks to replace a food, give 2-3 alternatives.
If she asks to explain macros, explain in 3 sentences without terms.

WORKOUTS:
If she asks for a workout plan:
1. Ask: goal (lose weight / strength / tone), level (beginner / intermediate / advanced), where she trains (home / gym), how many days a week
2. Build a concrete plan from the answers
3. Format of each workout: exercise, sets x reps

GENERAL RULES:
- Always help when asked, do not refuse
- Plain language like talking to a friend
- Concrete numbers and examples, not general words
- If the goal is weight loss, do not push harsh restrictions
- Do not give medical diagnoses
- Do not recommend supplements or pharmacology
- Calorie deficit never below 1200 kcal for women

FORBIDDEN:
- "I can't build a menu"
- "See a dietitian" without real help
- Harsh diets under 1200 kcal
- General advice without specifics
"""


HOOK_FORMULA_INSTRUCTION_EN = """
When the user asks for a hook for TikTok, Reels, or Shorts, use the 3-part formula:

HOOK FORMULA:
1️⃣ A SPECIFIC VIEWER — who exactly this person is right now
   Not "young people", but a concrete situation: what they do, what they feel, where they are stuck
   The more specific, the stronger it works

2️⃣ THE DESIRED RESULT — what this person wants
   What changes in their life if they watch to the end
   It must match the pain from the first part

3️⃣ A CONCRETE SOLUTION — what exactly you will show
   Not "I'll tell you how", but a specific method, tool, or approach

RESULT: join all three into one or two sentences so the viewer thinks "this is about me"

PROCESS:
1. If the topic and the viewer are already named, write hooks right away. Ask only if the hook would be generic without it.
2. Clarify with one question, only if the viewer's situation is unclear.
3. Write 2-3 hook options using the formula.
4. You may ask which one is closer after the finished options, not instead of them.

RULES:
- No questions in the hook
- No "I think" or "I feel like"
- You sound like someone who already knows the answer
- Specifics always beat generalities
- The hook should trigger the thought "damn, this is about me"

FOR RESTAURANT AND HOTEL CONTENT:
Viewer: a person planning a trip or looking for a place, who does not want to make a mistake and waste money.
Working formats:
- "I spent $X at [place] in [city] - here is what I did not expect to see inside"
- "All the tourists go to [place] - here is why locals don't"
- "The [place] everyone recommends in [city] - I checked it myself"

AUTO-TRIGGERS: if the user writes "hook", "first seconds", "how to start a video", "attention-grabbing opening", use the formula above automatically.
"""


# Triggers: the original Russian ones plus English, so a user in either language is covered.
_REELS_RE = re.compile(
    r"(сценари|рилс|reels|шортс|shorts|тик[\s-]?ток|tiktok|\bscript\b)", re.I
)
_HOOK_RE = re.compile(
    r"(хук|\bhook\b|первые секунды|как начать видео|цепляющее начало"
    r"|first seconds|start (?:a|my) video|opening line)",
    re.I,
)
_FITNESS_RE = re.compile(
    r"(похуд|питани|трениров|спорт|калор|\bменю\b|бжу|рацион"
    r"|meal plan|diet\b|nutrition|calorie|workout|weight loss|lose weight"
    r"|macros|\bmenu\b)",
    re.I,
)


def specialty_instructions_lang(
    text: str,
    lang: object,
    ru_reels: str,
    ru_hook: str,
    ru_fitness: str,
) -> str:
    """Topic blocks for this message, in the user's language."""
    is_ru = str(lang or "en").lower().startswith("ru")
    reels = ru_reels if is_ru else REELS_SCRIPT_STRUCTURE_EN
    hook = ru_hook if is_ru else HOOK_FORMULA_INSTRUCTION_EN
    fitness = ru_fitness if is_ru else FITNESS_NUTRITION_INSTRUCTION_EN
    low = text or ""
    blocks: list[str] = []
    if _REELS_RE.search(low):
        blocks.append(reels.strip())
    if _HOOK_RE.search(low):
        blocks.append(hook.strip())
    if _FITNESS_RE.search(low):
        blocks.append(fitness.strip())
    return "\n\n".join(blocks)
