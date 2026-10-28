
import argparse
import csv
import json
import random
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path


DEFAULT_SEED = 20261022
BASE_DIR = Path(__file__).resolve().parent
SCHEMA_FILE = BASE_DIR / "schema_v2.sql"

ID_FORMATS = {
    "Student": "STU-######",
    "Course": "CRS-######",
    "Enrollment": "ENR-######",
    "Exam": "EXAM-2026-######",
    "ExamRequirement": "REQ-######",
    "Room": "ROOM-######",
    "RoomCapacity": "CAP-######",
    "Invigilator": "INV-######",
    "TimeSlot": "SLOT-######",
    "Availability": "AVL-######",
    "Accommodation": "ACC-######",
}

DERIVED = {
    ("Exam", "estimated_students"):
        "Derived from active enrollments for the exam's course.",
    ("ExamRequirement", "extra_time_minutes"):
        "Largest extra time among enrolled students with active accommodations.",
    ("ExamRequirement", "required_duration_minutes"):
        "Base duration plus extra time.",
    ("ExamRequirement", "accessible_room_required"):
        "1 if any enrolled student has an active accessible-room accommodation.",
    ("ExamRequirement", "required_seats"):
        "Equals Exam.estimated_students.",
    ("ConflictGraph", "shared_student_count"):
        "Number of students enrolled in both exams' courses.",
}


def make_id(prefix, number, width=6):
    return f"{prefix}{number:0{width}d}"


def describe_allowed(cursor, table, field):
    result = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()

    sql = result[0] if result and result[0] else ""
    match = re.search(
        r"\b%s\b\s+IN\s*\(([^)]*)\)" % re.escape(field),
        sql,
        re.IGNORECASE,
    )

    if match:
        values = re.sub(r"\s+", " ", match.group(1)).replace("'", "")
        return "One of: " + values

    if field.endswith("_minutes"):
        return "Minutes (integer)"

    if field in (
        "capacity", "estimated_students", "required_seats",
        "assigned_seats", "shared_student_count",
    ):
        return "Count (integer)"

    if "datetime" in field.lower():
        return "ISO date-time text"

    if field.endswith("_date") or field.startswith("effective"):
        return "ISO date text (YYYY-MM-DD)"

    return "Free text / identifier"


def describe_rule(cursor, table, field, primary_key):
    parts = []

    if primary_key:
        fmt = ID_FORMATS.get(table)
        rule = "Primary key, unique"
        if fmt:
            rule += f"; format {fmt}"
        parts.append(rule)

    foreign_keys = cursor.execute(
        f'PRAGMA foreign_key_list("{table}")'
    ).fetchall()

    for fk in foreign_keys:
        if fk[3] == field:
            parts.append(f"Foreign key to {fk[2]}.{fk[4]}")

    if (table, field) in DERIVED:
        parts.append(DERIVED[(table, field)])

    return "; ".join(parts) if parts else "Must satisfy the table's CHECK constraints"


def build_ground_truth(cursor):
    """Build five concrete test fixtures outside the operational database."""

    def fetch_one(test_name, sql):
        result = cursor.execute(sql).fetchone()

        if result is None:
            raise RuntimeError(
                f"Could not create {test_name}: no matching records found. "
                "Try the default dataset sizes or review the test-case query."
            )

        return result

    # Two exams that share at least one student.
    r1 = fetch_one(
        "TEST-001",
        """
        SELECT exam_id_1, exam_id_2
        FROM ConflictGraph
        ORDER BY shared_student_count DESC, exam_id_1, exam_id_2
        LIMIT 1
        """,
    )

    # An active room that is too small for an exam.
    r2 = fetch_one(
        "TEST-002",
        """
        SELECT e.exam_id, r.room_id
        FROM Exam e
        JOIN Room r ON r.status = 'ACTIVE'
        JOIN RoomCapacity rc
          ON rc.room_id = r.room_id
         AND rc.capacity < e.estimated_students
        WHERE e.estimated_students < 101
        ORDER BY e.exam_id, r.room_id
        LIMIT 1
        """,
    )

    # An active invigilator available for a slot.
    r3 = fetch_one(
        "TEST-003",
        """
        SELECT a.resource_id, a.slot_id
        FROM Availability a
        JOIN Invigilator i
          ON i.invigilator_id = a.resource_id
        WHERE a.resource_type = 'INVIGILATOR'
          AND a.status = 'AVAILABLE'
          AND i.status = 'ACTIVE'
        ORDER BY a.resource_id, a.slot_id
        LIMIT 1
        """,
    )

    # An accessible-room requirement paired with a sufficiently large,
    # active room that is not accessible.
    r4 = fetch_one(
        "TEST-004",
        """
        SELECT q.exam_id, r.room_id
        FROM ExamRequirement q
        JOIN Room r
          ON r.accessible = 0
         AND r.status = 'ACTIVE'
        JOIN RoomCapacity rc
          ON rc.room_id = r.room_id
         AND rc.capacity >= q.required_seats
        WHERE q.accessible_room_required = 1
        ORDER BY q.exam_id, r.room_id
        LIMIT 1
        """,
    )

    # An unavailable room that otherwise has enough capacity.
    r5 = fetch_one(
        "TEST-005",
        """
        SELECT a.resource_id, a.slot_id, e.exam_id
        FROM Availability a
        JOIN Room r
          ON r.room_id = a.resource_id
        JOIN RoomCapacity rc
          ON rc.room_id = r.room_id
        JOIN Exam e
          ON rc.capacity >= e.estimated_students
        WHERE a.resource_type = 'ROOM'
          AND a.status = 'UNAVAILABLE'
        ORDER BY a.resource_id, a.slot_id, e.exam_id
        LIMIT 1
        """,
    )

    columns = [
        "test_id",
        "issue_type",
        "exam_id",
        "exam_id_2",
        "room_id",
        "invigilator_id",
        "slot_id",
        "expected_result",
        "description",
    ]

    rows = [
        (
            "TEST-001",
            "STUDENT_OVERLAPPING_EXAMS",
            r1[0], r1[1], "", "", "",
            "REJECT_CONFLICTING_SLOT",
            "Place both exams that share students in the same slot.",
        ),
        (
            "TEST-002",
            "ROOM_CAPACITY_EXCEEDED",
            r2[0], "", r2[1], "", "",
            "REJECT_ROOM",
            "Assign the exam to a room smaller than its student count.",
        ),
        (
            "TEST-003",
            "INVIGILATOR_DOUBLE_BOOKED",
            "", "", "", r3[0], r3[1],
            "REJECT_DOUBLE_BOOKING",
            "Give the same invigilator two exams in the same slot.",
        ),
        (
            "TEST-004",
            "ACCOMMODATION_IGNORED",
            r4[0], "", r4[1], "", "",
            "REJECT_ASSIGNMENT",
            "Assign an exam requiring accessibility to a non-accessible room.",
        ),
        (
            "TEST-005",
            "EXAM_OUTSIDE_ROOM_AVAILABILITY",
            r5[2], "", r5[0], "", r5[1],
            "REJECT_CLOSED_ROOM",
            "Assign an exam to a room during an unavailable slot.",
        ),
    ]

    return columns, rows


def write_ground_truth(ground_truth, folder):
    columns, rows = ground_truth
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    with (folder / "ground_truth.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(rows)

    data = [dict(zip(columns, row)) for row in rows]

    (folder / "ground_truth.json").write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def generate_database(
    output_path,
    seed=DEFAULT_SEED,
    students=5000,
    exams=300,
    rooms=100,
    invigilators=200,
):
    if min(students, exams, rooms, invigilators) < 1:
        raise ValueError("All record counts must be positive.")

    if exams < 5:
        raise ValueError("Use at least 5 exams for this dataset.")

    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(f"Missing schema: {SCHEMA_FILE}")

    output_path = Path(output_path).resolve()

    if output_path == (BASE_DIR / "exam_scheduling.db").resolve():
        raise ValueError("Cannot overwrite the existing project database.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        output_path.unlink()

    rng = random.Random(seed)
    connection = sqlite3.connect(output_path)
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        connection.executescript(schema)
        cursor = connection.cursor()

        # 1. Students
        student_ids = [
            make_id("STU-", i)
            for i in range(1, students + 1)
        ]

        cursor.executemany(
            "INSERT INTO Student VALUES (?, ?, ?)",
            [
                (sid, f"Synthetic Student {i:05d}", "ACTIVE")
                for i, sid in enumerate(student_ids, start=1)
            ],
        )

        # 2. Courses
        course_ids = [
            make_id("CRS-", i)
            for i in range(1, exams + 1)
        ]

        departments = [
            "Computer Science",
            "Mathematics",
            "Physics",
            "Electronics",
            "Management",
        ]

        cursor.executemany(
            "INSERT INTO Course VALUES (?, ?, ?, ?)",
            [
                (
                    cid,
                    f"Synthetic Course {i:03d}",
                    departments[(i - 1) % len(departments)],
                    "ACTIVE",
                )
                for i, cid in enumerate(course_ids, start=1)
            ],
        )

        # 3. Enrollments. The final course is a deliberately large course.
        course_students = defaultdict(set)
        enrollment_rows = []
        enrollment_number = 1

        common_course = course_ids[-1]
        other_courses = course_ids[:-1]

        common_count = min(
            students,
            max(1, round(students * 0.09)),
        )
        common_students = set(rng.sample(student_ids, common_count))

        for sid in student_ids:
            desired_count = 4 + rng.randint(0, 1)

            if sid in common_students:
                course_count = min(len(course_ids), desired_count)
                selected_courses = [common_course] + rng.sample(
                    other_courses,
                    course_count - 1,
                )
            else:
                course_count = min(len(other_courses), desired_count)
                selected_courses = rng.sample(
                    other_courses,
                    course_count,
                )

            for cid in selected_courses:
                enrollment_rows.append(
                    (
                        make_id("ENR-", enrollment_number),
                        sid,
                        cid,
                        "2026-08-01",
                        "ACTIVE",
                    )
                )
                course_students[cid].add(sid)
                enrollment_number += 1

        cursor.executemany(
            "INSERT INTO Enrollment VALUES (?, ?, ?, ?, ?)",
            enrollment_rows,
        )

        # 4. Accommodations are selected before exam requirements.
        accommodation_count = min(
            students,
            max(1, round(students * 0.015)),
        )
        acc_selected = rng.sample(student_ids, accommodation_count)

        acc_extra = {}
        acc_access = set()

        for sid in acc_selected:
            acc_extra[sid] = rng.choices(
                [30, 60, 90],
                weights=[50, 35, 15],
            )[0]

            if rng.random() < 0.35:
                acc_access.add(sid)

        # Ensure the dataset contains at least one accessible accommodation.
        if not acc_access:
            acc_access.add(acc_selected[0])

        # 5. Exams and derived requirements
        exam_rows = []
        requirement_rows = []

        for i, cid in enumerate(course_ids, start=1):
            exam_id = make_id("EXAM-2026-", i)
            enrolled_students = course_students[cid]
            enrolled_count = len(enrolled_students)

            base_duration = rng.choices(
                [60, 90, 120, 180],
                weights=[15, 27, 45, 13],
            )[0]

            extra_time = max(
                (
                    acc_extra[sid]
                    for sid in enrolled_students
                    if sid in acc_extra
                ),
                default=0,
            )

            needs_accessible = int(
                any(sid in acc_access for sid in enrolled_students)
            )

            exam_rows.append(
                (
                    exam_id,
                    cid,
                    f"Synthetic Exam {i:03d}",
                    base_duration,
                    enrolled_count,
                    "PLANNED",
                )
            )

            requirement_rows.append(
                (
                    make_id("REQ-", i),
                    exam_id,
                    extra_time,
                    base_duration + extra_time,
                    needs_accessible,
                    int(rng.random() < 0.20),
                    max(1, enrolled_count),
                )
            )

        cursor.executemany(
            "INSERT INTO Exam VALUES (?, ?, ?, ?, ?, ?)",
            exam_rows,
        )

        cursor.executemany(
            "INSERT INTO ExamRequirement VALUES (?, ?, ?, ?, ?, ?, ?)",
            requirement_rows,
        )

        # 6. Rooms and effective-dated capacities
        room_ids = [
            make_id("ROOM-", i)
            for i in range(1, rooms + 1)
        ]

        room_rows = []
        capacity_rows = []
        room_status_by_id = {}
        room_capacity_by_id = {}

        for i, rid in enumerate(room_ids, start=1):
            status = "MAINTENANCE" if i % 25 == 0 else "ACTIVE"

            capacity = rng.choices(
                [30, 50, 80, 100, 150, 200, 300, 400],
                weights=[12, 18, 22, 18, 14, 8, 5, 3],
            )[0]

            if i == 1:
                capacity = 400

            room_status_by_id[rid] = status
            room_capacity_by_id[rid] = capacity

            room_rows.append(
                (
                    rid,
                    f"Synthetic Room {i:03d}",
                    int(i % 3 != 0),
                    int(i % 4 != 0),
                    status,
                )
            )

            capacity_rows.append(
                (
                    make_id("CAP-", i),
                    rid,
                    capacity,
                    "2026-11-01",
                    None,
                )
            )

        cursor.executemany(
            "INSERT INTO Room VALUES (?, ?, ?, ?, ?)",
            room_rows,
        )

        cursor.executemany(
            "INSERT INTO RoomCapacity VALUES (?, ?, ?, ?, ?)",
            capacity_rows,
        )

        # 7. Invigilators
        invigilator_ids = [
            make_id("INV-", i)
            for i in range(1, invigilators + 1)
        ]

        invigilator_status_by_id = {
            iid: ("ON_LEAVE" if i % 30 == 0 else "ACTIVE")
            for i, iid in enumerate(invigilator_ids, start=1)
        }

        cursor.executemany(
            "INSERT INTO Invigilator VALUES (?, ?, ?)",
            [
                (
                    iid,
                    f"Synthetic Invigilator {i:03d}",
                    invigilator_status_by_id[iid],
                )
                for i, iid in enumerate(invigilator_ids, start=1)
            ],
        )

        # 8. Time slots: three 240-minute slots per day for 30 days.
        slot_rows = []
        slot_number = 1
        first_day = date(2026, 11, 2)
        start_times = [time(8, 0), time(12, 0), time(16, 0)]

        for day_offset in range(30):
            slot_day = first_day + timedelta(days=day_offset)

            for start_time in start_times:
                start_dt = datetime.combine(slot_day, start_time)
                end_dt = start_dt + timedelta(minutes=240)

                slot_rows.append(
                    (
                        make_id("SLOT-", slot_number),
                        start_dt.isoformat(timespec="seconds"),
                        end_dt.isoformat(timespec="seconds"),
                        slot_day.isoformat(),
                        240,
                    )
                )
                slot_number += 1

        cursor.executemany(
            "INSERT INTO TimeSlot VALUES (?, ?, ?, ?, ?)",
            slot_rows,
        )

        # 9. Availability for rooms and invigilators.
        availability_rows = []
        availability_number = 1

        resources = [
            ("ROOM", room_ids, room_status_by_id),
            (
                "INVIGILATOR",
                invigilator_ids,
                invigilator_status_by_id,
            ),
        ]

        for resource_type, resource_ids, status_by_id in resources:
            for resource_id in resource_ids:
                for slot in slot_rows:
                    unavailable = rng.random() < 0.02

                    if status_by_id[resource_id] != "ACTIVE":
                        unavailable = True

                    status = "UNAVAILABLE" if unavailable else "AVAILABLE"
                    reason = (
                        "SYNTHETIC_UNAVAILABILITY"
                        if unavailable
                        else None
                    )

                    availability_rows.append(
                        (
                            make_id("AVL-", availability_number),
                            resource_type,
                            resource_id,
                            slot[0],
                            slot[1],
                            slot[2],
                            status,
                            reason,
                        )
                    )
                    availability_number += 1

        cursor.executemany(
            """
            INSERT INTO Availability
            (availability_id, resource_type, resource_id, slot_id,
             start_datetime, end_datetime, status, reason_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            availability_rows,
        )

        # 10. Store the selected accommodation records.
        accommodation_rows = []

        for number, sid in enumerate(acc_selected, start=1):
            accommodation_rows.append(
                (
                    make_id("ACC-", number),
                    sid,
                    int(sid in acc_access),
                    acc_extra[sid],
                    "2026-08-01",
                    None,
                    "ACTIVE",
                )
            )

        cursor.executemany(
            "INSERT INTO Accommodation VALUES (?, ?, ?, ?, ?, ?, ?)",
            accommodation_rows,
        )

        # 11. Conflict graph derived from shared students.
        conflict_rows = []

        for i, course_1 in enumerate(course_ids):
            for j in range(i + 1, len(course_ids)):
                course_2 = course_ids[j]
                shared = len(
                    course_students[course_1]
                    & course_students[course_2]
                )

                if shared:
                    conflict_rows.append(
                        (
                            make_id("EXAM-2026-", i + 1),
                            make_id("EXAM-2026-", j + 1),
                            shared,
                        )
                    )

        cursor.executemany(
            "INSERT INTO ConflictGraph VALUES (?, ?, ?)",
            conflict_rows,
        )

        # 12. Create ground-truth fixtures outside the operational database.
        ground_truth_rows = build_ground_truth(cursor)

        # 13. Build the data dictionary from the actual database schema.
        dictionary_rows = []

        table_names = [
            row[0]
            for row in cursor.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                  AND name != 'DataDictionary'
                ORDER BY name
                """
            )
        ]

        for table_name in table_names:
            columns = cursor.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()

            for column in columns:
                _, field, data_type, not_null, _, primary_key = column
                required = "Yes" if not_null or primary_key else "No"
                unique = (
                    "Yes" if primary_key else "See schema constraints"
                )

                dictionary_rows.append(
                    (
                        table_name,
                        field,
                        data_type or "TEXT",
                        required,
                        describe_allowed(cursor, table_name, field),
                        unique,
                        describe_rule(
                            cursor, table_name, field, primary_key
                        ),
                    )
                )

        cursor.executemany(
            """
            INSERT INTO DataDictionary
            ("table", field, data_type, required,
             allowed_values_or_units, "unique", validation_rule)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            dictionary_rows,
        )

        # 14. Validate database integrity.
        fk_errors = cursor.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if fk_errors:
            raise RuntimeError(
                f"Foreign-key validation failed: {fk_errors[:5]}"
            )

        integrity = cursor.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"SQLite integrity check failed: {integrity}"
            )

        # Write the fixtures before committing the database.
        # If fixture writing fails, the database transaction is rolled back.
        write_ground_truth(ground_truth_rows, output_path.parent)
        connection.commit()

        print("Synthetic database created successfully.")
        print(f"Output: {output_path}")
        print(f"Seed: {seed}")

        for table_name in table_names:
            count = cursor.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            print(f"{table_name}: {count}")

        print(f"DataDictionary: {len(dictionary_rows)}")
        print("Ground-truth cases: 5")
        print("Foreign-key check: PASS")
        print("SQLite integrity check: PASS")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate a reproducible synthetic exam scheduling database."
        )
    )

    parser.add_argument(
        "--output",
        default="generated_exam_scheduling.db",
        help="Output database path.",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--students", type=int, default=5000)
    parser.add_argument("--exams", type=int, default=300)
    parser.add_argument("--rooms", type=int, default=100)
    parser.add_argument("--invigilators", type=int, default=200)

    args = parser.parse_args()

    generate_database(
        output_path=args.output,
        seed=args.seed,
        students=args.students,
        exams=args.exams,
        rooms=args.rooms,
        invigilators=args.invigilators,
    )


if __name__ == "__main__":
    main()
