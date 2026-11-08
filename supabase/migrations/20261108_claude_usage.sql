-- One row per Claude API call: what it was for, which model, tokens and cost.
-- Written by db.save_claude_usage(); survives deploys (the in-memory counter does not).
create table if not exists claude_usage (
    id                 bigserial primary key,
    created_at         timestamptz not null default now(),
    user_id            bigint,
    feature            text not null,
    model              text not null,
    input_tokens       integer not null default 0,
    output_tokens      integer not null default 0,
    cache_read_tokens  integer not null default 0,
    cache_write_tokens integer not null default 0,
    cost_usd           numeric(10, 6) not null default 0
);

create index if not exists claude_usage_created_at on claude_usage (created_at);
create index if not exists claude_usage_feature on claude_usage (feature, created_at);

-- Only the bot's service role writes and reads this table.
alter table claude_usage enable row level security;
