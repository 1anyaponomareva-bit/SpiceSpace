-- Small key-value store for app settings, e.g. auto-refreshed Meta tokens
-- (Threads / Instagram) for the owner's stats. Only the bot's service role uses it.
create table if not exists app_kv (
    key        text primary key,
    value      jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

alter table app_kv enable row level security;
