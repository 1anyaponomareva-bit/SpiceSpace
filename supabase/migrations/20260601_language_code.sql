ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS language_code TEXT DEFAULT 'ru';
