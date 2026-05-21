-- SpiceSpace v2: morning/evening times + daily task fields
-- Run in Supabase SQL Editor after initial schema

alter table user_profiles add column if not exists morning_time text;
alter table user_profiles add column if not exists evening_time text;
alter table user_profiles add column if not exists last_morning_sent_date date;
alter table user_profiles add column if not exists last_evening_sent_date date;
alter table user_profiles add column if not exists streak int default 0;
alter table user_profiles add column if not exists current_week int default 1;

alter table daily_summaries add column if not exists task text;
alter table daily_summaries add column if not exists completed boolean;
