
import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from database import DB_PATH


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR / "exports"


def sha256_file(path):
    """Calculate the SHA-256 checksum of a file."""
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def main():
    manifest_path = EXPORT_DIR / "manifest.json"
    snapshot_path = EXPORT_DIR / "exam_scheduling_export.db"

    # 1. Check required files.
    if not DB_PATH.is_file():
        raise FileNotFoundError(
            f"Source database not found: {DB_PATH}"
        )

    if not manifest_path.is_file():
        raise FileNotFoundError(
            "Manifest not found. Run python export_data.py first."
        )

    if not snapshot_path.is_file():
        raise FileNotFoundError(
            f"Database snapshot not found: {snapshot_path}"
        )

    with manifest_path.open(encoding="utf-8") as file:
        manifest = json.load(file)

    # 2. Connect to source and exported database.
    source = sqlite3.connect(DB_PATH)
    snapshot = sqlite3.connect(snapshot_path)

    try:
        # 3. Check table row counts.
        print("Checking table row counts...")

        for table, info in manifest["tables"].items():
            quoted = '"' + table.replace('"', '""') + '"'

            source_count = source.execute(
                f"SELECT COUNT(*) FROM {quoted}"
            ).fetchone()[0]

            snapshot_count = snapshot.execute(
                f"SELECT COUNT(*) FROM {quoted}"
            ).fetchone()[0]

            csv_path = EXPORT_DIR / "csv" / f"{table}.csv"
            json_path = EXPORT_DIR / "json" / f"{table}.json"

            if not csv_path.is_file():
                raise FileNotFoundError(
                    f"CSV export missing: {csv_path}"
                )

            if not json_path.is_file():
                raise FileNotFoundError(
                    f"JSON export missing: {json_path}"
                )

            with csv_path.open(
                encoding="utf-8-sig",
                newline=""
            ) as file:
                csv_count = sum(
                    1 for _ in csv.reader(file)
                ) - 1

            with json_path.open(
                encoding="utf-8"
            ) as file:
                json_records = json.load(file)

            if not isinstance(json_records, list):
                raise AssertionError(
                    f"{table}: JSON export must contain a list."
                )

            json_count = len(json_records)
            expected = info["row_count"]

            counts = [
                source_count,
                snapshot_count,
                csv_count,
                json_count,
            ]

            if any(count != expected for count in counts):
                raise AssertionError(
                    f"{table}: expected {expected}, "
                    f"got source/snapshot/CSV/JSON={counts}"
                )

            print(f"PASS {table}: {expected} rows")

        # 4. Check separate ground-truth exports.
        print("\nChecking separate ground-truth exports...")

        gt_csv = EXPORT_DIR / "ground_truth.csv"
        gt_json = EXPORT_DIR / "ground_truth.json"

        if not gt_csv.is_file():
            raise FileNotFoundError(
                f"Ground-truth CSV missing: {gt_csv}"
            )

        if not gt_json.is_file():
            raise FileNotFoundError(
                f"Ground-truth JSON missing: {gt_json}"
            )

        with gt_csv.open(
            encoding="utf-8-sig",
            newline=""
        ) as file:
            gt_csv_rows = list(csv.DictReader(file))

        with gt_json.open(
            encoding="utf-8"
        ) as file:
            gt_json_rows = json.load(file)

        if not isinstance(gt_json_rows, list):
            raise AssertionError(
                "Ground-truth JSON must contain a list."
            )

        expected_ids = {
            "TEST-001",
            "TEST-002",
            "TEST-003",
            "TEST-004",
            "TEST-005",
        }

        csv_ids = {
            row["test_id"] for row in gt_csv_rows
        }

        json_ids = {
            row["test_id"] for row in gt_json_rows
        }

        assert csv_ids == expected_ids, (
            "Ground-truth CSV has missing or unexpected test IDs."
        )

        assert json_ids == expected_ids, (
            "Ground-truth JSON has missing or unexpected test IDs."
        )

        assert len(gt_csv_rows) == len(gt_json_rows) == 5, (
            "Expected exactly five ground-truth cases."
        )

        # Ensure both exports contain the same records.
        csv_records = sorted(
            gt_csv_rows,
            key=lambda row: row["test_id"]
        )

        json_records = sorted(
            gt_json_rows,
            key=lambda row: row["test_id"]
        )

        assert csv_records == json_records, (
            "Ground-truth CSV and JSON contents do not match."
        )

        print(
            "PASS: Five ground-truth cases exported separately."
        )

        # 5. Verify exported file checksums.
        print("\nChecking export file checksums...")

        for relative_path, info in manifest["files"].items():
            path = EXPORT_DIR / relative_path

            if not path.is_file():
                raise AssertionError(
                    f"Missing export file: {relative_path}"
                )

            actual_hash = sha256_file(path)

            if actual_hash != info["sha256"]:
                raise AssertionError(
                    f"Checksum mismatch: {relative_path}"
                )

        print("All exported file checksums match.")

        # 6. Final result.
        print("\nPASS: All export checks succeeded.")

    finally:
        source.close()
        snapshot.close()


if __name__ == "__main__":
    main()
