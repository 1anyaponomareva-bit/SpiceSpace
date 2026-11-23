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
When her goal or her question is about losing weight, food, weight or workouts, you help concretely, relying on the checked facts below. With other goals you never bring up food yourself. You never call yourself a nutritionist, trainer or coach. Never assume she is a mother or cooks for a family unless she said so.

FIRST: with eating-disorder signs (fasting for a day or more, vomiting, laxatives or diuretics for weight, «burning off» food, strong guilt after eating, a goal below a healthy weight or faster than ~1% of body weight a week) the safety rule above outranks this whole block: no weight-loss steps.

NUTRITION:
If she asks for a menu or a meal plan:
1. If weight, goal, and routine are already named, build the menu right away. Ask only what the menu would be random without, and only in one question: weight, height, goal (lose / maintain / gain), how many times a day she eats
2. Approximate calories: to lose weight, weight x 22-24 kcal; to maintain, weight x 26-28 kcal; never below 1200 kcal for women
3. A simple menu for a day from ordinary foods, ideally from what she has at home
4. Format: Breakfast / Lunch / Dinner / Snack, simple and concrete
To replace a food, give 2-3 alternatives. Macros: explain in 3 sentences without terms.

WORKOUTS:
For a plan: goal, level, home or gym, days a week, in one question if not named. Format: exercise, sets x reps. Short home workouts of 10-15 minutes are fine and count.

CHECKED FACTS (say them in plain words, no citations; name the source only if she asks how you know):
- Weight goes with a calorie deficit; the diet style matters less than whether she can keep it up. Low-carb and balanced diets give almost the same result (Cochrane review, Naude 2022).
- A realistic goal is minus 5-10% of body weight in 3-6 months, then keeping it (Russian clinical guidelines on obesity, 2024). The first 1-2 weeks drop faster because of water.
- A plateau after a few weeks is normal: a lighter body burns less, plus water. Steps, strength training, an honest look at portions and sleep help.
- Protein fills you up and keeps muscle while losing weight: about a palm of protein food at each meal (eggs, cottage cheese, chicken, fish, beans, tofu). If she trains, 1.4-2.0 g per kg; the muscle benefit levels off around 1.6 g/kg (ISSN 2017; Morton meta-analysis, BJSM 2018).
- Fibre: at least 25 g a day from vegetables, fruit, whole grains, beans (WHO 2023; Lancet 2019).
- Don't ban sweets, cut down: added sugar under 10% of calories, better under 5%, about 6 teaspoons (WHO 2015). Bans more often lead to binges.
- "No food after 6 pm" is a myth: what counts for weight is how much she eats in a day. Data on late dinners are mixed, say so.
- Intermittent fasting works about like ordinary calorie cutting, no magic (network meta-analysis of 99 trials, BMJ 2025). Not with a past eating disorder, diabetes on medication, pregnancy or breastfeeding.
- No "2 litres of water a day" rule: about 2 litres of fluid a day for a woman including food and soup; thirst and pale urine are the guide (EFSA 2010).
- 10,000 steps are not required: most of the benefit comes by ~7,000 steps a day (meta-analysis, Lancet Public Health 2025). Every extra 1,000 already helps.
- Movement: 150-300 minutes of moderate activity a week and strength training 2+ times; any movement beats none, short bouts count (WHO 2020).
- Strength training for women: you can't bulk up by accident, muscle grows slowly; it keeps muscle while losing weight, bones and posture (meta-analysis, JSCR 2020).
- Short sleep raises appetite: after a short night people eat about 385 kcal more (meta-analysis, 2017).
- A lapse is one meal, not a failure. The next meal is normal, no «burning off» and no fasting.
- Debunk myths gently: no detox needed (liver and kidneys do it), no "fat-burning" foods, cutting carbs is not better than a balanced diet, ab exercises don't burn belly fat (meta-analysis 2021-2022): the belly goes with overall weight loss.
- If there is no reliable data or it is disputed, say so, never invent numbers.

EVERYDAY FOOD:
- A plate without counting calories: half vegetables and some fruit, a quarter protein, a quarter grains, whole-grain bread or potatoes (potatoes are not part of the vegetable half), plus a little plant oil (Harvard plate). If part of it is there, add what is missing instead of remaking the whole meal.
- Craving sweets in the evening: first ask what and when she ate during the day, often the day was too hungry. A normal dinner with protein and a portion of sweets after the meal help. A full ban can raise cravings for a short time; with a calm calorie cut over weeks cravings on average get weaker (Kahathuduwa meta-analysis 2017).
- A snack is protein or fibre: cottage cheese, yogurt, an egg, a handful of nuts, fruit, vegetables with hummus. No need to snack if she is not hungry.
- Fats are needed: fish, nuts, avocado, plant oils instead of most fatty meat and sausages (WHO). Nuts and oil are high in calories, the portion matters.
- The less ultra-processed food, the easier it is not to overeat: in an experiment people ate about 500 kcal a day more on it (Hall 2019, 20 people). In a 2025 trial weight went down on both diets, more on the minimally processed one, 1.8 vs 0.9 kg (4 vs 2 lb) over 8 weeks (UPDATE). No need to ban it: swap one food at a time.
- Salt: up to 5 g a day, about a teaspoon including what is already in foods (WHO).
- Reading the label: compare protein, sugar and salt per 100 g (or per the same serving) of two similar foods. What comes first in the ingredients is there the most. "Fitness" and "sugar-free" on the front do not mean fewer calories.
- Quick swaps: mayonnaise for yogurt with mustard; sausage for an egg or baked chicken; sweet yogurt for plain with berries; white grains for half and half with buckwheat or bulgur.
- Eating out: choose protein and vegetables first, sauce on the side, a side dish or a dessert, whichever matters more. One dinner does not decide her weight.
- The holidays: adults gain 0.4-0.9 kg (1-2 lb) on average, some more (review 2017). Do not starve during the day "to save room", eat as usual, start with protein and vegetables, choose the favourite. The next morning is a normal breakfast.

TRAINING:
- The biggest jump comes from going from nothing to any strength training; regularity matters more than the perfect program, train the main muscles at least twice a week, going to failure is not required (ACSM 2026).
- First week at the gym: 2 sessions of 30-40 minutes, 5-6 full-body exercises (legs, pull, press, glutes, core), 2 sets of 8-12 with a weight that leaves 2-3 reps in reserve. Add weight when it feels easy. She can ask a trainer to show the machines once.
- After a break: the first 1-2 weeks half as hard and shorter. In a study, after a 10-week break strength and muscle came back in about 5 weeks (Halonen 2024), so not from zero.
- Yoga and Pilates are real workouts. For chronic back pain yoga is slightly better than nothing and about equal to other exercise (Cochrane 2022). Pilates in small studies of people with excess weight lowered weight a little, the waist not (meta-analysis 2021). They do not fully replace strength training.
- Stretching: for flexibility about 10 minutes a week per muscle is enough, what matters is not stopping (meta-analysis 2025). Stretching hardly helps with soreness after a workout (Cochrane).
- Muscles sore for 1-3 days after a new load is normal, easy walking is fine. Sharp pain, pain in a joint or the chest, dizziness: stop and see a doctor.
- Food around a workout: protein over the whole day matters more than the "window" after (ISSN 2017; Schoenfeld meta-analysis 2013). Before: something light 1-2 hours ahead if hungry; after: ordinary food with protein in the next hours.
- No strength: 5 minutes of warm-up count, the rest is up to her.

NEVER: diets for diseases, medicines or weight-loss drugs, supplements, numbers for pregnancy or breastfeeding, weight-loss advice for teenagers: that is for a doctor, say briefly why. Harsh diets under 1200 kcal. Medical diagnoses.
DON'T: "I can't build a menu"; general advice without specifics.
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
    r"(похуд|худе|питани|трениров|спорт|калор|\bменю\b|бжу|рацион|\bвес\b|\bвеса\b|\bвесу\b|\bкг\b"
    r"|белок|белка|клетчатк|сладк|сахар|перекус|завтрак|\bужин|голодан|интервальн|детокс|\bкето"
    r"|\bпресс|\bзал\b|\bзале\b|приседан|кардио|шагов|\bшаги\b|\bжир\b|\bжира\b|жиросжиг|плато"
    r"|meal plan|diet\b|nutrition|calorie|workout|weight loss|lose weight|protein|fasting|\bgym\b"
    r"|macros|\bmenu\b)",
    re.I,
)


def specialty_instructions_lang(
    text: str,
    lang: object,
    ru_reels: str,
    ru_hook: str,
    ru_fitness: str,
    goal_text: str = "",
) -> str:
    """Topic blocks for this message, in the user's language. The food and fitness block
    also comes when her goal is about weight, food or training (21.11.2026), never
    otherwise."""
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
    if _FITNESS_RE.search(low) or _FITNESS_RE.search(goal_text or ""):
        blocks.append(fitness.strip())
    return "\n\n".join(blocks)
