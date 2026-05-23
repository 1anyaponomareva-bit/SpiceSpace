-- SpiceSpace v3: weekly goal + time per day (onboarding)
-- Run in Supabase SQL Editor after v2

alter table user_profiles add column if not exists weekly_goal text;
alter table user_profiles add column if not exists time_per_day text;
