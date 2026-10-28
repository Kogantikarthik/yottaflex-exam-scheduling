
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
GENERATOR = BASE_DIR / "generate_data.py"
SCHEDULER = BASE_DIR / "scheduler.py"
SEED = 20261022

# These fields represent run-specific audit metadata, not scheduling results.
VOLATILE_COLUMNS = {
    "created_at",
    "updated_at",
    "generated_at",
    "exported_at",
    "recorded_at",
    "event_timestamp",
    "event_time",
    "timestamp",
}

def run_command(command, env=None):
    """Run a command and stop if it fails."""
    subprocess.run(
        command,
        cwd=BASE_DIR,
        env=env,
        check=True,
    )


def create_database(output_dir):
    """Generate and schedule a fresh database."""
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path = output_dir / "generated_exam_scheduling.db"

    run_command([
        sys.executable,
        str(GENERATOR),
        "--output",
        str(database_path),
        "--seed",
        str(SEED),
    ])

    env = os.environ.copy()
    env["EXAM_SCHEDULING_DB"] = str(database_path.resolve())

    run_command([sys.executable, str(SCHEDULER)], env=env)

    return database_path


def read_database(path):
    """Read database contents in a consistent order."""
    connection = sqlite3.connect(path)

    try:
        tables = [
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]

        snapshot = {}

        for table in tables:
            # Quote identifiers safely.
            quoted_table = '"' + table.replace('"', '""') + '"'

            info = connection.execute(
                f"PRAGMA table_info({quoted_table})"
            ).fetchall()

            columns = [
                row[1]
                for row in info
                if row[1].lower() not in VOLATILE_COLUMNS
            ]

            if not columns:
                snapshot[table] = ([], [])
                continue

            quoted_columns = ", ".join(
                '"' + column.replace('"', '""') + '"'
                for column in columns
            )

            rows = connection.execute(
                f"SELECT {quoted_columns} FROM {quoted_table}"
            ).fetchall()

            snapshot[table] = (
                columns,
                sorted(rows, key=repr),
            )

        return snapshot
    finally:
        connection.close()


def main():
    if not GENERATOR.is_file() or not SCHEDULER.is_file():
        raise FileNotFoundError(
            "generate_data.py or scheduler.py was not found."
        )

    # Temporary folders are removed automatically.
    # The existing project databases are not overwritten.
    with tempfile.TemporaryDirectory(
        prefix="repro_check_",
        dir=BASE_DIR,
    ) as temporary:
        root = Path(temporary)

        print("Generating database A...")
        first_path = create_database(root / "run_a")

        print("\nGenerating database B...")
        second_path = create_database(root / "run_b")

        print("\nComparing generated and scheduled database contents...")
        first = read_database(first_path)
        second = read_database(second_path)

        differences = []

        for table in sorted(set(first) | set(second)):
            if first.get(table) != second.get(table):
                differences.append(table)

        if differences:
            print("FAIL: The regenerated databases differ.")
            for table in differences:
                print(f"Different table: {table}")
            sys.exit(1)

        print("PASS: Both fresh databases have matching contents.")
        print(f"Seed: {SEED}")
        print(f"Tables compared: {len(first)}")
        print("Existing project databases were not modified.")


if __name__ == "__main__":
    main()
