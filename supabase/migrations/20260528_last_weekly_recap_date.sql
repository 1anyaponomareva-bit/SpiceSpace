ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS last_weekly_recap_date TEXT;
