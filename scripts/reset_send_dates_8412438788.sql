UPDATE user_profiles
SET last_morning_sent_date = NULL,
    last_evening_sent_date = NULL,
    last_daily_sent_date = NULL
WHERE user_id = 8412438788;
