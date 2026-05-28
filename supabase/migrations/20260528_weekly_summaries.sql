CREATE TABLE IF NOT EXISTS weekly_summaries (
    user_id BIGINT NOT NULL,
    week_number INT NOT NULL,
    week_start DATE,
    summary TEXT,
    achievements TEXT,
    challenges TEXT,
    next_week_goal TEXT,
    score INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, week_number)
);

CREATE INDEX IF NOT EXISTS idx_weekly_summaries_user_week
    ON weekly_summaries (user_id, week_number DESC);
