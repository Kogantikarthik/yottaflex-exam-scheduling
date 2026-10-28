
import os
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(
    os.environ.get(
        "EXAM_SCHEDULING_DB",
        str(BASE_DIR / "exam_scheduling.db"),
    )
).expanduser().resolve()


def get_connection():
    """Open a connection to the configured exam scheduling database."""
    if not DB_PATH.is_file():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn
