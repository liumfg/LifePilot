import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    amount REAL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receive_id TEXT NOT NULL,
    receive_id_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(receive_id, receive_id_type)
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_lookup
ON conversation_messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS health_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meal_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    meal_name TEXT,
    protein_level TEXT,
    vegetable_level TEXT,
    staple_level TEXT,
    oiliness TEXT,
    balance_score INTEGER,
    summary TEXT,
    next_meal_focus TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS menstrual_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date TEXT,
    end_date TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_type_created
ON events (event_type, created_at);

CREATE INDEX IF NOT EXISTS idx_health_memories_lookup
ON health_memories (memory_type, id);

CREATE INDEX IF NOT EXISTS idx_meal_analyses_event
ON meal_analyses (event_id);

CREATE INDEX IF NOT EXISTS idx_menstrual_cycles_start
ON menstrual_cycles (start_date);

CREATE TABLE IF NOT EXISTS user_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_text TEXT NOT NULL,
    constraints_text TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_goals_active
ON user_goals (active, id);

CREATE TABLE IF NOT EXISTS daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date TEXT NOT NULL UNIQUE,
    goal_text TEXT NOT NULL,
    water_target_ml INTEGER NOT NULL,
    water_start TEXT NOT NULL,
    water_end TEXT NOT NULL,
    breakfast_time TEXT NOT NULL,
    lunch_time TEXT NOT NULL,
    dinner_time TEXT NOT NULL,
    sleep_time TEXT NOT NULL,
    exercise_minutes INTEGER NOT NULL,
    exercise_focus TEXT,
    notes TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_plans_date
ON daily_plans (plan_date);
"""


def init_db() -> None:
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.database_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def db():
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
