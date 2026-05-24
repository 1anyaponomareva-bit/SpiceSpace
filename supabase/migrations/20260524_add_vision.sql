-- Add vision (3-month dream) to user profiles for 12-Week Year onboarding
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS vision text;

COMMENT ON COLUMN user_profiles.vision IS 'User dream / 3-month vision from onboarding';
