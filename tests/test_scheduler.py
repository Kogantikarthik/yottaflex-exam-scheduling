
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

GENERATED_DB = PROJECT_ROOT / "generated_exam_scheduling.db"
LEGACY_DB = PROJECT_ROOT / "exam_scheduling.db"

SOURCE_DB = (
    GENERATED_DB
    if GENERATED_DB.is_file()
    else LEGACY_DB
)


class ScheduleValidationTests(unittest.TestCase):

    def run_validation(self, mutation=None):
        """Validate an isolated copy of the selected database."""

        if not SOURCE_DB.is_file():
            raise FileNotFoundError(
                f"Test database not found: {SOURCE_DB}. "
                "Run generate_data.py first."
            )

        required_files = [
            PROJECT_ROOT / "database.py",
            PROJECT_ROOT / "validator.py",
        ]

        for file_path in required_files:
            if not file_path.is_file():
                raise FileNotFoundError(
                    f"Required project file not found: {file_path}"
                )

        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)

            shutil.copy2(
                PROJECT_ROOT / "database.py",
                temp_dir / "database.py",
            )
            shutil.copy2(
                PROJECT_ROOT / "validator.py",
                temp_dir / "validator.py",
            )

            test_db = temp_dir / SOURCE_DB.name
            shutil.copy2(SOURCE_DB, test_db)

            if mutation:
                conn = sqlite3.connect(test_db)

                try:
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA foreign_keys = ON")

                    mutation(conn)
                    conn.commit()

                finally:
                    conn.close()

            env = None
            import os

            env = os.environ.copy()
            env["EXAM_SCHEDULING_DB"] = str(test_db)

            result = subprocess.run(
                [sys.executable, "validator.py"],
                cwd=temp_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

            report_path = temp_dir / "validation_report.json"

            self.assertEqual(
                result.returncode,
                0,
                f"Validator crashed.\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}",
            )

            self.assertTrue(
                report_path.is_file(),
                "Validator did not create validation_report.json",
            )

            with report_path.open(encoding="utf-8") as file:
                return json.load(file)

    def assert_rejected(self, mutation, expected_words):
        """Assert that an invalid schedule fails validation."""

        report = self.run_validation(mutation)

        self.assertFalse(
            report["passed"],
            f"Invalid schedule was accepted: {report}",
        )

        errors = " ".join(
            str(error).lower()
            for error in report.get("errors", [])
        )

        self.assertTrue(
            any(word in errors for word in expected_words),
            f"Expected an error mentioning {expected_words}, "
            f"but got: {errors}",
        )

    def test_clean_baseline_passes(self):
        report = self.run_validation()

        self.assertTrue(
            report["passed"],
            f"Baseline should pass: {report}",
        )

        self.assertEqual(
            report["errors_count"],
            0,
            f"Baseline should have zero errors: {report}",
        )

        required_metrics = [
            "active_assignments",
            "distinct_unscheduled_exams",
            "total_non_cancelled_exams",
        ]

        for metric in required_metrics:
            self.assertIn(
                metric,
                report,
                f"Validation report is missing metric: {metric}",
            )

        self.assertEqual(
            report["active_assignments"]
            + report["distinct_unscheduled_exams"],
            report["total_non_cancelled_exams"],
            "Every non-cancelled exam must be assigned or "
            "recorded as unscheduled.",
        )

    def test_001_student_exam_conflict(self):
        def mutation(conn):
            pair = conn.execute("""
                SELECT
                    a1.assignment_id AS assignment_id_1,
                    a2.assignment_id AS assignment_id_2,
                    a1.slot_id AS slot_id
                FROM ConflictGraph c
                JOIN ScheduleAssignment a1
                  ON a1.exam_id = c.exam_id_1
                 AND a1.status = 'ASSIGNED'
                JOIN ScheduleAssignment a2
                  ON a2.exam_id = c.exam_id_2
                 AND a2.status = 'ASSIGNED'
                WHERE a1.slot_id <> a2.slot_id
                LIMIT 1
            """).fetchone()

            if not pair:
                raise AssertionError(
                    "No suitable conflicting exam pair was found"
                )

            conn.execute("""
                UPDATE ScheduleAssignment
                SET slot_id = ?
                WHERE assignment_id = ?
            """, (
                pair["slot_id"],
                pair["assignment_id_2"],
            ))

        self.assert_rejected(mutation, ["conflict"])

    def test_002_room_capacity_exceeded(self):
        def mutation(conn):
            row = conn.execute("""
                SELECT
                    a.assignment_id,
                    rc.capacity
                FROM ScheduleAssignment a
                JOIN TimeSlot s
                  ON s.slot_id = a.slot_id
                JOIN RoomCapacity rc
                  ON rc.room_id = a.room_id
                 AND rc.effective_from <= substr(s.start_datetime, 1, 10)
                 AND (
                     rc.effective_to IS NULL
                     OR rc.effective_to >= substr(s.start_datetime, 1, 10)
                 )
                WHERE a.status = 'ASSIGNED'
                LIMIT 1
            """).fetchone()

            if not row:
                raise AssertionError(
                    "No assignment with an effective room capacity found"
                )

            conn.execute("""
                UPDATE ScheduleAssignment
                SET assigned_seats = ?
                WHERE assignment_id = ?
            """, (
                row["capacity"] + 1,
                row["assignment_id"],
            ))

        self.assert_rejected(mutation, ["capacity", "seat"])

    def test_003_invigilator_double_booking(self):
        def mutation(conn):
            pair = conn.execute("""
                SELECT
                    a1.assignment_id AS first_id,
                    a1.invigilator_id AS first_invigilator,
                    a2.assignment_id AS second_id
                FROM ScheduleAssignment a1
                JOIN ScheduleAssignment a2
                  ON a1.slot_id = a2.slot_id
                 AND a1.assignment_id <> a2.assignment_id
                 AND a1.room_id <> a2.room_id
                WHERE a1.status = 'ASSIGNED'
                  AND a2.status = 'ASSIGNED'
                LIMIT 1
            """).fetchone()

            if not pair:
                raise AssertionError(
                    "No two assignments sharing a time slot were found"
                )

            conn.execute("""
                UPDATE ScheduleAssignment
                SET invigilator_id = ?
                WHERE assignment_id = ?
            """, (
                pair["first_invigilator"],
                pair["second_id"],
            ))

        self.assert_rejected(
            mutation,
            ["invigilator", "overlap", "double"],
        )

    def test_004_accessibility_accommodation_ignored(self):
        def mutation(conn):
            row = conn.execute("""
                SELECT
                    a.assignment_id,
                    r.room_id AS bad_room
                FROM ScheduleAssignment a
                JOIN ExamRequirement er
                  ON er.exam_id = a.exam_id
                JOIN Room r
                  ON r.accessible = 0
                WHERE a.status = 'ASSIGNED'
                  AND er.accessible_room_required = 1
                LIMIT 1
            """).fetchone()

            if not row:
                raise AssertionError(
                    "No accessible-room requirement and inaccessible "
                    "room combination was found"
                )

            conn.execute("""
                UPDATE ScheduleAssignment
                SET room_id = ?
                WHERE assignment_id = ?
            """, (
                row["bad_room"],
                row["assignment_id"],
            ))

        self.assert_rejected(
            mutation,
            ["accessible", "accommodation"],
        )

    def test_005_room_unavailable_during_exam(self):
        def mutation(conn):
            row = conn.execute("""
                SELECT
                    a.room_id,
                    a.slot_id
                FROM ScheduleAssignment a
                JOIN Availability v
                  ON v.resource_type = 'ROOM'
                 AND v.resource_id = a.room_id
                 AND v.slot_id = a.slot_id
                 AND v.status = 'AVAILABLE'
                WHERE a.status = 'ASSIGNED'
                LIMIT 1
            """).fetchone()

            if not row:
                raise AssertionError(
                    "No assigned room with an available record was found"
                )

            conn.execute("""
                UPDATE Availability
                SET status = 'UNAVAILABLE',
                    reason_code = 'TEST_CLOSED'
                WHERE resource_type = 'ROOM'
                  AND resource_id = ?
                  AND slot_id = ?
            """, (
                row["room_id"],
                row["slot_id"],
            ))

        self.assert_rejected(
            mutation,
            ["availability", "unavailable", "room"],
        )

    def test_extended_duration_requirement(self):
        def mutation(conn):
            row = conn.execute("""
                SELECT
                    a.assignment_id,
                    a.exam_id,
                    a.slot_id,
                    ts.duration_minutes
                FROM ScheduleAssignment a
                JOIN TimeSlot ts
                  ON ts.slot_id = a.slot_id
                WHERE a.status = 'ASSIGNED'
                LIMIT 1
            """).fetchone()

            if not row:
                raise AssertionError("No assignment found")

            conn.execute("""
                UPDATE ExamRequirement
                SET required_duration_minutes = ?
                WHERE exam_id = ?
            """, (
                row["duration_minutes"] + 30,
                row["exam_id"],
            ))

        self.assert_rejected(
            mutation,
            ["duration", "slot", "long"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
