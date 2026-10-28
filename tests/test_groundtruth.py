
import csv
import os
import sqlite3
import unittest
from pathlib import Path



BASE_DIR = Path(__file__).resolve().parents[1]

GENERATED_DB = BASE_DIR / "gen" / "generated_exam_scheduling.db"
ROOT_DB = BASE_DIR / "exam_scheduling.db"

if "EXAM_SCHEDULING_DB" in os.environ:
    DB_PATH = Path(os.environ["EXAM_SCHEDULING_DB"]).expanduser().resolve()
elif ROOT_DB.is_file():
    DB_PATH = ROOT_DB.resolve()
elif GENERATED_DB.is_file():
    DB_PATH = GENERATED_DB.resolve()
else:
    DB_PATH = ROOT_DB.resolve()

GT_PATH = DB_PATH.parent / "ground_truth.csv"



EXPECTED = {
    "TEST-001": "STUDENT_OVERLAPPING_EXAMS",
    "TEST-002": "ROOM_CAPACITY_EXCEEDED",
    "TEST-003": "INVIGILATOR_DOUBLE_BOOKED",
    "TEST-004": "ACCOMMODATION_IGNORED",
    "TEST-005": "EXAM_OUTSIDE_ROOM_AVAILABILITY",
}

REQUIRED_COLUMNS = {
    "test_id",
    "issue_type",
    "exam_id",
    "exam_id_2",
    "room_id",
    "invigilator_id",
    "slot_id",
}


class GroundTruthTests(unittest.TestCase):

    def setUp(self):
        if not DB_PATH.is_file():
            self.fail(
                f"Database not found: {DB_PATH}. "
                "Run generate_data.py first."
            )

        if not GT_PATH.is_file():
            self.fail(
                f"Ground-truth CSV not found: {GT_PATH}. "
                "Run generate_data.py to create it."
            )

        with GT_PATH.open(
            encoding="utf-8",
            newline="",
        ) as file:
            reader = csv.DictReader(file)

            if reader.fieldnames is None:
                self.fail("ground_truth.csv is empty or has no header.")

            missing_columns = REQUIRED_COLUMNS.difference(
                reader.fieldnames
            )

            if missing_columns:
                self.fail(
                    "ground_truth.csv is missing required columns: "
                    + ", ".join(sorted(missing_columns))
                )

            rows = list(reader)

        self.assertEqual(
            len(rows),
            len(EXPECTED),
            "ground_truth.csv must contain exactly five test cases.",
        )

        self.rows = {
            row["test_id"]: row
            for row in rows
        }

        self.assertEqual(
            len(self.rows),
            len(rows),
            "ground_truth.csv contains duplicate test IDs.",
        )

        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        if hasattr(self, "conn"):
            self.conn.close()

    def q(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()

    def test_five_cases_with_expected_types(self):
        actual_ids = set(self.rows)

        self.assertEqual(
            actual_ids,
            set(EXPECTED),
            "Ground-truth CSV must contain exactly TEST-001 to TEST-005.",
        )

        actual_types = {
            test_id: row["issue_type"]
            for test_id, row in self.rows.items()
        }

        self.assertEqual(
            actual_types,
            EXPECTED,
            "Ground-truth issue types do not match the expected values.",
        )

    def test_ground_truth_not_in_operational_db(self):
        result = self.q("""
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'GroundTruth'
        """)

        self.assertIsNone(
            result,
            "GroundTruth should be stored in CSV/JSON, "
            "not as an operational database table.",
        )

    def test_001_exams_share_students(self):
        row = self.rows["TEST-001"]

        exam_id_1 = row["exam_id"]
        exam_id_2 = row["exam_id_2"]

        self.assertTrue(
            exam_id_1 and exam_id_2,
            "TEST-001 must identify two exams.",
        )

        first, second = sorted([exam_id_1, exam_id_2])

        conflict = self.q("""
            SELECT 1
            FROM ConflictGraph
            WHERE exam_id_1 = ?
              AND exam_id_2 = ?
              AND shared_student_count > 0
        """, first, second)

        self.assertIsNotNone(
            conflict,
            "TEST-001 exams must share at least one student.",
        )

    def test_002_room_smaller_than_exam(self):
        row = self.rows["TEST-002"]

        capacity = self.q("""
            SELECT MIN(rc.capacity)
            FROM RoomCapacity rc
            WHERE rc.room_id = ?
        """, row["room_id"])

        exam = self.q("""
            SELECT estimated_students
            FROM Exam
            WHERE exam_id = ?
        """, row["exam_id"])

        self.assertIsNotNone(
            capacity,
            "TEST-002 room has no capacity records.",
        )

        self.assertIsNotNone(
            exam,
            "TEST-002 references an unknown exam.",
        )

        self.assertIsNotNone(
            capacity[0],
            "TEST-002 room has no recorded capacity.",
        )

        self.assertLess(
            capacity[0],
            exam["estimated_students"],
            "TEST-002 room capacity should be smaller than "
            "the exam's estimated student count.",
        )

    def test_003_invigilator_available(self):
        row = self.rows["TEST-003"]

        availability = self.q("""
            SELECT 1
            FROM Availability
            WHERE resource_type = 'INVIGILATOR'
              AND resource_id = ?
              AND slot_id = ?
              AND status = 'AVAILABLE'
        """, row["invigilator_id"], row["slot_id"])

        self.assertIsNotNone(
            availability,
            "TEST-003 invigilator must have an AVAILABLE record "
            "for the specified slot.",
        )

    def test_004_room_not_accessible(self):
        row = self.rows["TEST-004"]

        room = self.q("""
            SELECT accessible
            FROM Room
            WHERE room_id = ?
        """, row["room_id"])

        requirement = self.q("""
            SELECT accessible_room_required
            FROM ExamRequirement
            WHERE exam_id = ?
        """, row["exam_id"])

        self.assertIsNotNone(
            room,
            "TEST-004 references an unknown room.",
        )

        self.assertIsNotNone(
            requirement,
            "TEST-004 references an unknown exam requirement.",
        )

        self.assertEqual(
            room["accessible"],
            0,
            "TEST-004 room must be inaccessible.",
        )

        self.assertEqual(
            requirement["accessible_room_required"],
            1,
            "TEST-004 exam must require an accessible room.",
        )

    def test_005_room_unavailable(self):
        row = self.rows["TEST-005"]

        availability = self.q("""
            SELECT status
            FROM Availability
            WHERE resource_type = 'ROOM'
              AND resource_id = ?
              AND slot_id = ?
        """, row["room_id"], row["slot_id"])

        self.assertIsNotNone(
            availability,
            "TEST-005 must reference a room availability record.",
        )

        self.assertEqual(
            availability["status"],
            "UNAVAILABLE",
            "TEST-005 room must be unavailable for the specified slot.",
        )

    def test_every_unscheduled_exam_has_reason(self):
        missing_reasons = self.q("""
            SELECT COUNT(*)
            FROM UnassignedExam
            WHERE reason_code IS NULL
               OR TRIM(reason_code) = ''
        """)[0]

        self.assertEqual(
            missing_reasons,
            0,
            "Every unscheduled exam must have a reason code.",
        )

        unaccounted = self.q("""
            SELECT COUNT(*)
            FROM Exam e
            WHERE e.status != 'CANCELLED'
              AND NOT EXISTS (
                  SELECT 1
                  FROM ScheduleAssignment a
                  WHERE a.exam_id = e.exam_id
                    AND a.status = 'ASSIGNED'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM UnassignedExam u
                  WHERE u.exam_id = e.exam_id
              )
        """)[0]

        self.assertEqual(
            unaccounted,
            0,
            "Every non-cancelled exam must be assigned or "
            "recorded as unscheduled.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
