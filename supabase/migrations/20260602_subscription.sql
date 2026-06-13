-- Telegram Stars subscription fields.
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS subscription_end TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS trial_start_date TEXT DEFAULT '';
