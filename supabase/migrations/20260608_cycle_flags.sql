-- Persist weekly recap / new-week flags across Railway restarts (Supabase).
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS cycle_flags jsonb DEFAULT '{}'::jsonb;
