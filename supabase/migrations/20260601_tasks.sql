-- User reminders / scheduled tasks (migrated from tasks.json)
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  telegram_id BIGINT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL DEFAULT '',
  time TEXT NOT NULL DEFAULT '',
  timezone TEXT NOT NULL DEFAULT 'UTC',
  repeat TEXT NOT NULL DEFAULT 'none',
  days_of_week JSONB NOT NULL DEFAULT '[]',
  remind_before_minutes INT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  done BOOLEAN NOT NULL DEFAULT false,
  last_sent_at TEXT NOT NULL DEFAULT '',
  snooze_until TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_telegram_id ON tasks (telegram_id);
CREATE INDEX IF NOT EXISTS idx_tasks_active ON tasks (status, done);
