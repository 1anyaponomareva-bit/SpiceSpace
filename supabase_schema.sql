-- SpiceSpace: run in Supabase SQL Editor

create table if not exists user_profiles (
  user_id bigint primary key,
  name text,
  morning_routine text,
  has_kids boolean,
  works text check (works in ('yes', 'no', 'own')),
  main_goal text,
  daily_time text not null default '09:30',
  morning_time text,
  evening_time text default '21:00',
  timezone text not null default 'Asia/Ho_Chi_Minh',
  daily_enabled boolean not null default true,
  last_daily_sent_date date,
  last_morning_sent_date date,
  last_evening_sent_date date,
  streak int default 0,
  current_week int default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists daily_summaries (
  id bigserial primary key,
  user_id bigint not null references user_profiles(user_id) on delete cascade,
  summary_date date not null,
  summary text,
  mood text,
  key_detail text,
  task text,
  completed boolean,
  created_at timestamptz not null default now(),
  unique (user_id, summary_date)
);

create index if not exists idx_daily_summaries_user_date
  on daily_summaries (user_id, summary_date desc);

alter table user_profiles enable row level security;
alter table daily_summaries enable row level security;

-- Backend uses service_role key (bypasses RLS). No public policies needed for MVP.

create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists user_profiles_updated_at on user_profiles;
create trigger user_profiles_updated_at
  before update on user_profiles
  for each row execute function set_updated_at();
