
import shutil
import sqlite3
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
SOURCE_DB = BASE_DIR / "generated_exam_scheduling.db"


def main():
    if not SOURCE_DB.exists():
        raise FileNotFoundError(
            f"Database not found: {SOURCE_DB}"
        )

    # Test on a temporary copy, never on the main database.
    with tempfile.TemporaryDirectory() as temp_dir:
        test_db = Path(temp_dir) / "exam_scheduling_test.db"
        shutil.copy2(SOURCE_DB, test_db)

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row

        try:
            print("TEST 1: Schedule event history")

            columns = [
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(ScheduleEvent)"
                )
            ]

            print("ScheduleEvent columns:", columns)

            event_count = conn.execute(
                "SELECT COUNT(*) FROM ScheduleEvent"
            ).fetchone()[0]

            assignment_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM ScheduleAssignment
                WHERE status = 'ASSIGNED'
                """
            ).fetchone()[0]

            print("Total schedule events:", event_count)
            print("Active assignments:", assignment_count)

            assert event_count > 0, "No schedule events found."

            if "event_type" in columns:
                print("\nEvent types:")
                for row in conn.execute(
                    """
                    SELECT event_type, COUNT(*) AS total
                    FROM ScheduleEvent
                    GROUP BY event_type
                    ORDER BY event_type
                    """
                ):
                    print(f"  {row['event_type']}: {row['total']}")

            print("\nTEST 2: Check audit triggers")

            triggers = conn.execute(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name = 'ScheduleEvent'
                ORDER BY name
                """
            ).fetchall()

            for trigger in triggers:
                print("Trigger:", trigger["name"])

            assert triggers, "No ScheduleEvent triggers found."

            # Check whether a schedule event can be updated.
            first_column = columns[0]
            update_sql = (
                f'UPDATE ScheduleEvent '
                f'SET "{first_column}" = "{first_column}" '
                f'WHERE rowid = '
                f'(SELECT rowid FROM ScheduleEvent LIMIT 1)'
            )

            update_blocked = False

            try:
                conn.execute(update_sql)
            except sqlite3.DatabaseError as exc:
                update_blocked = True
                print("Update blocked:", exc)

            assert update_blocked, (
                "FAIL: ScheduleEvent allowed an update."
            )

            # Check whether a schedule event can be deleted.
            delete_blocked = False

            try:
                conn.execute(
                    """
                    DELETE FROM ScheduleEvent
                    WHERE rowid = (
                        SELECT rowid FROM ScheduleEvent LIMIT 1
                    )
                    """
                )
            except sqlite3.DatabaseError as exc:
                delete_blocked = True
                print("Delete blocked:", exc)

            assert delete_blocked, (
                "FAIL: ScheduleEvent allowed a delete."
            )

            # Confirm both rejected operations preserved the history.
            final_count = conn.execute(
                "SELECT COUNT(*) FROM ScheduleEvent"
            ).fetchone()[0]

            assert final_count == event_count, (
                "Event count changed during the tests."
            )

            print("\nTEST 3: Confirm history was preserved")
            print("Events before tests:", event_count)
            print("Events after tests:", final_count)

            print("\nPASS: Audit history is append-only.")
            print("The main database was not modified.")

        finally:
            conn.close()


if __name__ == "__main__":
    main()




