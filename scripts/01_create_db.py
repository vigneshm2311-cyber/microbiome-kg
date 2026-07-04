"""
01_create_db.py

Builds (or rebuilds) the microbiome knowledge graph SQLite database
from db/schema.sql.

Usage:
    python scripts/01_create_db.py
"""

import sqlite3
from pathlib import Path

# Path objects, not hardcoded strings — this way the script works no
# matter what directory you run it from, as long as the folder
# structure underneath it stays the same.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"


def create_database(rebuild: bool = False) -> None:
    if rebuild and DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing database at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    with open(SCHEMA_PATH, "r") as f:
        schema_sql = f.read()

    # executescript runs multiple ; -separated statements in one call,
    # which a plain execute() can't do.
    conn.executescript(schema_sql)
    conn.commit()

    # Quick sanity check: list every table SQLite actually created,
    # so we immediately know if something in schema.sql failed silently.
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    conn.close()

    print(f"Database created at {DB_PATH}")
    print(f"Tables created ({len(tables)}):")
    for t in tables:
        print(f"  - {t}")


if __name__ == "__main__":
    create_database(rebuild=True)
