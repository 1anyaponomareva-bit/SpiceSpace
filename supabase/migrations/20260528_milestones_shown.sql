-- Track which milestone days were already shown (Mini App + Telegram)
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS milestones_shown JSONB DEFAULT '{}';
