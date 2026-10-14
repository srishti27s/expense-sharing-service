

import os
import sqlite3
from pathlib import Path

from .money import split_equally

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_configured_database_path = Path(os.getenv("DATABASE_PATH", "expense_sharing.db")).expanduser()
if not _configured_database_path.is_absolute():
    _configured_database_path = PROJECT_ROOT / _configured_database_path
DATABASE_PATH = str(_configured_database_path.resolve())

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    joined_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    paid_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
    split_type TEXT NOT NULL DEFAULT 'equal' CHECK(split_type IN ('equal', 'custom')),
    description TEXT NOT NULL CHECK(length(trim(description)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_expenses_group_created ON expenses(group_id, created_at, id);
CREATE TABLE IF NOT EXISTS expense_shares (
    expense_id INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    share_paise INTEGER NOT NULL CHECK(share_paise >= 0),
    PRIMARY KEY (expense_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_expense_shares_user ON expense_shares(user_id, expense_id);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open a connection with foreign key checks and row access enabled."""
    db_path = path or DATABASE_PATH
    if db_path != ":memory:":
        Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: str | None = None) -> None:
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        expense_columns = {row["name"] for row in connection.execute("PRAGMA table_info(expenses)")}
        if "split_type" not in expense_columns:
            connection.execute(
                "ALTER TABLE expenses ADD COLUMN split_type TEXT NOT NULL DEFAULT 'equal' "
                "CHECK(split_type IN ('equal', 'custom'))"
            )
       
        group_ids = [row[0] for row in connection.execute("SELECT id FROM groups")]
        for group_id in group_ids:
            member_ids = [row[0] for row in connection.execute(
                "SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id", (group_id,)
            )]
            if not member_ids:
                continue
            expenses = connection.execute(
                "SELECT id, amount_paise FROM expenses WHERE group_id=? AND split_type='equal'", (group_id,)
            ).fetchall()
            for expense in expenses:
                expected = split_equally(expense["amount_paise"], member_ids)
                actual = {
                    row["user_id"]: row["share_paise"] for row in connection.execute(
                        "SELECT user_id, share_paise FROM expense_shares WHERE expense_id=?",
                        (expense["id"],),
                    )
                }
                if actual != expected:
                    connection.execute("DELETE FROM expense_shares WHERE expense_id=?", (expense["id"],))
                    connection.executemany(
                        "INSERT INTO expense_shares(expense_id, user_id, share_paise) VALUES (?, ?, ?)",
                        [(expense["id"], user_id, share) for user_id, share in expected.items()],
                    )
        connection.commit()
    finally:
        connection.close()
