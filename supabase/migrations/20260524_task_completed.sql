-- task_completed: null | true | false | partial
ALTER TABLE daily_summaries
  ADD COLUMN IF NOT EXISTS task_completed text;

ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS cycle_start_date date;
