"""
Print the PySearch database schema version, tables, indexes and row counts.

Read-only: opens the database in SQLite read-only mode and never migrates it.

Usage (from the project root, with the backend venv activated):
    python scripts/inspect_db.py                 # uses data/pysearch.db
    python scripts/inspect_db.py path/to/other.db
"""

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "pysearch.db"


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not db.exists():
        print(f"No database at {db}. Start the backend once to create it.")
        return 1

    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    version = conn.execute("SELECT version FROM schema_info WHERE id = 1").fetchone()
    print(f"Database:       {db}")
    print(f"Schema version: {version[0] if version else 'unknown'}")
    print(f"Journal mode:   {conn.execute('PRAGMA journal_mode').fetchone()[0]}")
    print(f"Integrity:      {conn.execute('PRAGMA integrity_check').fetchone()[0]}")

    print("\nTables (rows):")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        count = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"  {t:<14} {count}")

    print("\nIndexes:")
    for name, table in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' ORDER BY tbl_name, name"
    ):
        kind = "(automatic, from UNIQUE)" if name.startswith("sqlite_autoindex") else ""
        print(f"  {table:<12} {name} {kind}")

    print("\nTriggers:")
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'"):
        print(f"  {name}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
