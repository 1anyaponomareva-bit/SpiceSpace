ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS cycle_flags JSONB DEFAULT '{}';
