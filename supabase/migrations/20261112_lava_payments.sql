-- lava.top subscription payments: one row per contract (first payment and each renewal).
-- Links a lava contract to the Telegram user, because lava webhooks only carry the email.
create table if not exists lava_payments (
    contract_id        text primary key,
    parent_contract_id text,
    telegram_id        bigint,
    email              text,
    plan               text,
    currency           text,
    amount             numeric(12, 2),
    status             text,
    paid_until         text,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create index if not exists lava_payments_telegram on lava_payments (telegram_id, updated_at);
create index if not exists lava_payments_email on lava_payments (email, updated_at);

-- Only the bot's service role writes and reads this table.
alter table lava_payments enable row level security;
