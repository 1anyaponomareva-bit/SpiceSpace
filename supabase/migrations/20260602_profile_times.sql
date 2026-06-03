-- Ensure Mini App can persist morning/evening times and language (fixes PGRST204).
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS morning_time text,
  ADD COLUMN IF NOT EXISTS evening_time text,
  ADD COLUMN IF NOT EXISTS language_code text DEFAULT 'en';

-- Backfill morning_time from legacy daily_time column.
UPDATE user_profiles
SET morning_time = daily_time
WHERE (morning_time IS NULL OR morning_time = '')
  AND daily_time IS NOT NULL
  AND daily_time <> '';

UPDATE user_profiles
SET evening_time = '21:00'
WHERE evening_time IS NULL OR evening_time = '';

-- One source of truth: morning follows daily_time when they disagree.
UPDATE user_profiles
SET morning_time = daily_time
WHERE daily_time IS NOT NULL
  AND daily_time <> ''
  AND (morning_time IS NULL OR morning_time = '' OR morning_time <> daily_time);
