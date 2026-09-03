"""SQLite storage for GSC performance data.

Schema is deliberately simple: one row per (date, query, page, country, device)
with metrics. All downstream agents read from these tables — never from the API.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS performance (
    date        TEXT NOT NULL,
    query       TEXT NOT NULL,
    page        TEXT NOT NULL,
    country     TEXT NOT NULL,
    device      TEXT NOT NULL,
    clicks      REAL NOT NULL,
    impressions REAL NOT NULL,
    ctr         REAL NOT NULL,
    position    REAL NOT NULL,
    PRIMARY KEY (date, query, page, country, device)
);

CREATE INDEX IF NOT EXISTS idx_perf_page  ON performance(page, date);
CREATE INDEX IF NOT EXISTS idx_perf_query ON performance(query, date);
CREATE INDEX IF NOT EXISTS idx_perf_date  ON performance(date);

-- Watermark so we know what date ranges we already collected.
CREATE TABLE IF NOT EXISTS sync_state (
    dimension_set TEXT PRIMARY KEY,   -- e.g. "query,page,country,device"
    last_date     TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    sql = """
        INSERT OR REPLACE INTO performance
        (date, query, page, country, device, clicks, impressions, ctr, position)
        VALUES (:date, :query, :page, :country, :device,
                :clicks, :impressions, :ctr, :position)
    """
    with conn:
        conn.executemany(sql, rows)
    return len(rows)


def last_synced_date(conn: sqlite3.Connection, dimension_set: str) -> str | None:
    row = conn.execute(
        "SELECT last_date FROM sync_state WHERE dimension_set = ?",
        (dimension_set,),
    ).fetchone()
    return row[0] if row else None


def set_synced_date(conn: sqlite3.Connection, dimension_set: str, date: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO sync_state (dimension_set, last_date) VALUES (?, ?)",
            (dimension_set, date),
        )
