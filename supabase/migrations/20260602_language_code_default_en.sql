-- Default new profiles to English; Russian only when Telegram language is ru
ALTER TABLE user_profiles
  ALTER COLUMN language_code SET DEFAULT 'en';
