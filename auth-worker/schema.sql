-- Run with: wrangler d1 execute agants --file=schema.sql

CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  username    TEXT    NOT NULL UNIQUE,
  api_key     TEXT    NOT NULL UNIQUE,
  created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
  hide_record INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS matches (
  id           TEXT    PRIMARY KEY,
  red_agent_id INTEGER REFERENCES users(id),
  blue_agent_id INTEGER REFERENCES users(id),
  winner_id    INTEGER REFERENCES users(id),
  ticks        INTEGER,
  ended_at     INTEGER,
  result_path  TEXT
);
