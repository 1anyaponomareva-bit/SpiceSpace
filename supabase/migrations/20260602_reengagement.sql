-- Re-engagement tracking for silent users.
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS last_user_message_date TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS reengagement_sent_date TEXT DEFAULT '';
