"""SQLite persistence layer for the kanban CLI."""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('todo', 'doing', 'done'))
);
"""


class StorageError(Exception):
    """Raised when the database cannot be opened or is unusable."""


def connect(db_path):
    """Open (creating on first write) the database at db_path.

    Raises StorageError if the path cannot be opened or the file is not a
    valid SQLite database.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        # Force SQLite to touch the file header now so that a non-database
        # file surfaces as StorageError rather than later, as a usage error.
        conn.execute("SELECT 1 FROM projects").fetchone()
        conn.commit()
    except sqlite3.DatabaseError as exc:
        try:
            conn.close()
        except Exception:
            pass
        raise StorageError(str(exc))
    except sqlite3.Error as exc:
        raise StorageError(str(exc))
    return conn
