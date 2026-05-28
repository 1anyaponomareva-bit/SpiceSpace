CREATE TABLE IF NOT EXISTS user_personality (
    user_id BIGINT PRIMARY KEY,
    communication_style TEXT,
    motivation_triggers TEXT,
    procrastination_patterns TEXT,
    best_time_of_day TEXT,
    response_to_pressure TEXT,
    personal_values TEXT,
    blockers TEXT,
    strengths TEXT,
    raw_insights TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
