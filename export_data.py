
import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database import DB_PATH, get_connection


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR / "exports"
CSV_DIR = EXPORT_DIR / "csv"
JSON_DIR = EXPORT_DIR / "json"

EXPECTED_GROUND_TRUTH_IDS = {
    "TEST-001",
    "TEST-002",
    "TEST-003",
    "TEST-004",
    "TEST-005",
}


def sha256_file(path):
    """Calculate the SHA-256 checksum of a file."""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def quote_identifier(name):
    """Safely quote an SQLite identifier."""
    return '"' + name.replace('"', '""') + '"'


def load_ground_truth():
    """Load and validate separate ground-truth CSV and JSON files."""
    source_csv = DB_PATH.parent / "ground_truth.csv"
    source_json = DB_PATH.parent / "ground_truth.json"

    if not source_csv.is_file() or not source_json.is_file():
        raise FileNotFoundError(
            "Ground-truth CSV/JSON files must exist beside "
            f"the source database: {DB_PATH.parent}"
        )

    with source_csv.open(
        encoding="utf-8-sig", newline=""
    ) as file:
        csv_records = list(csv.DictReader(file))

    with source_json.open(encoding="utf-8") as file:
        json_records = json.load(file)

    if not isinstance(json_records, list):
        raise RuntimeError("Ground-truth JSON must contain a list.")

    csv_ids = {row.get("test_id") for row in csv_records}
    json_ids = {row.get("test_id") for row in json_records}

    if (
        len(csv_records) != 5
        or len(json_records) != 5
        or csv_ids != EXPECTED_GROUND_TRUTH_IDS
        or json_ids != EXPECTED_GROUND_TRUTH_IDS
    ):
        raise RuntimeError(
            "Ground truth must contain exactly TEST-001 through TEST-005."
        )

    sorted_csv = sorted(csv_records, key=lambda row: row["test_id"])
    sorted_json = sorted(json_records, key=lambda row: row["test_id"])

    # CSV values are text; normalize JSON values for comparison.
    normalized_json = [
        {key: "" if value is None else str(value)
         for key, value in row.items()}
        for row in sorted_json
    ]

    if sorted_csv != normalized_json:
        raise RuntimeError(
            "Ground-truth CSV and JSON records do not match."
        )

    return source_csv, source_json


def main():
    if not DB_PATH.is_file():
        raise FileNotFoundError(
            f"Source database not found: {DB_PATH}"
        )

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)

    # Validate ground truth before writing any exports.
    source_gt_csv, source_gt_json = load_ground_truth()

    exported_files = []
    table_summary = {}

    with get_connection() as source:
        # 1. Database integrity.
        integrity = source.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"Database integrity check failed: {integrity}"
            )

        # 2. Foreign-key integrity.
        foreign_key_errors = source.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if foreign_key_errors:
            raise RuntimeError(
                f"Database has {len(foreign_key_errors)} "
                "foreign-key violations."
            )

        # 3. Discover operational tables.
        tables = [
            row[0]
            for row in source.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]

        # Ground truth must not be stored as an operational table.
        if "GroundTruth" in tables:
            raise RuntimeError(
                "GroundTruth table found in operational database. "
                "Ground truth must remain in separate CSV/JSON files."
            )

        # 4. Export each operational table to CSV and JSON.
        for table in tables:
            quoted_table = quote_identifier(table)
            cursor = source.execute(
                f"SELECT * FROM {quoted_table}"
            )
            columns = [
                item[0] for item in cursor.description
            ]

            csv_path = CSV_DIR / f"{table}.csv"
            json_path = JSON_DIR / f"{table}.json"
            row_count = 0

            with (
                csv_path.open(
                    "w", newline="", encoding="utf-8-sig"
                ) as csv_file,
                json_path.open("w", encoding="utf-8") as json_file,
            ):
                writer = csv.writer(csv_file)
                writer.writerow(columns)

                json_file.write("[\n")
                first_record = True

                while True:
                    rows = cursor.fetchmany(1000)
                    if not rows:
                        break

                    for row in rows:
                        record = {
                            column: row[column]
                            for column in columns
                        }

                        writer.writerow([
                            "" if value is None else value
                            for value in record.values()
                        ])

                        if not first_record:
                            json_file.write(",\n")

                        json_file.write(
                            json.dumps(
                                record,
                                ensure_ascii=False,
                                default=str,
                            )
                        )

                        first_record = False
                        row_count += 1

                json_file.write("\n]\n")

            table_summary[table] = {
                "row_count": row_count,
                "columns": columns,
            }

            exported_files.extend([csv_path, json_path])

            print(
                f"Exported {table}: {row_count} rows to CSV and JSON"
            )

        # 5. Copy the validated ground-truth files separately.
        ground_truth_csv = EXPORT_DIR / "ground_truth.csv"
        ground_truth_json = EXPORT_DIR / "ground_truth.json"

        ground_truth_csv.write_bytes(source_gt_csv.read_bytes())
        ground_truth_json.write_bytes(source_gt_json.read_bytes())

        exported_files.extend([
            ground_truth_csv,
            ground_truth_json,
        ])

        print("Exported five separate ground-truth cases.")

        # 6. Create a SQLite snapshot of the same source database.
        snapshot_path = EXPORT_DIR / "exam_scheduling_export.db"
        destination = sqlite3.connect(snapshot_path)

        try:
            source.backup(destination)
        finally:
            destination.close()

        exported_files.append(snapshot_path)

        # 7. Create a portable SQL dump.
        sql_path = EXPORT_DIR / "exam_scheduling_export.sql"

        with sql_path.open("w", encoding="utf-8") as sql_file:
            for statement in source.iterdump():
                sql_file.write(statement + "\n")

        exported_files.append(sql_path)

    # 8. Generate the manifest from the completed exports.
    try:
        relative_db_path = str(DB_PATH.relative_to(BASE_DIR))
    except ValueError:
        relative_db_path = DB_PATH.name

    manifest = {
        "exported_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_database": DB_PATH.name,
        "source_database_path": relative_db_path,
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "table_count": len(table_summary),
        "tables": table_summary,
        "files": {},
    }

    for path in exported_files:
        relative_path = str(path.relative_to(EXPORT_DIR)).replace(
            "\\", "/"
        )
        manifest["files"][relative_path] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    manifest_path = EXPORT_DIR / "manifest.json"

    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(
            manifest,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print("\nExport completed successfully.")
    print("Source database:", DB_PATH)
    print("Tables exported:", len(table_summary))
    print("Output folder:", EXPORT_DIR)
    print("Manifest:", manifest_path)
    print("SQL dump:", sql_path)
    print("SQLite snapshot:", snapshot_path)


if __name__ == "__main__":
    main()
