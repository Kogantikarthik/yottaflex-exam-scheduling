
from datetime import datetime
import json

from database import get_connection


def minutes_between(start, end):
    start_time = datetime.fromisoformat(start)
    end_time = datetime.fromisoformat(end)
    return (end_time - start_time).total_seconds() / 60


def validate_schedule():
    errors = []
    warnings = []

    with get_connection() as conn:
        # --------------------------------------------------
        # 1. LOAD ASSIGNMENTS AND DATE-VALID ROOM CAPACITY
        # --------------------------------------------------
        assignments = conn.execute("""
            SELECT
                a.assignment_id,
                a.exam_id,
                a.room_id,
                a.slot_id,
                a.invigilator_id,
                a.assigned_seats,
                a.status,
                e.course_id,
                e.base_duration_minutes,
                e.estimated_students,
                q.required_duration_minutes,
                q.required_seats,
                q.accessible_room_required,
                q.projector_required,
                s.start_datetime,
                s.end_datetime,
                r.accessible,
                r.has_projector,
                c.capacity,
                c.effective_from,
                c.effective_to
            FROM ScheduleAssignment a
            JOIN Exam e
                ON e.exam_id = a.exam_id
            JOIN ExamRequirement q
                ON q.exam_id = e.exam_id
            JOIN TimeSlot s
                ON s.slot_id = a.slot_id
            JOIN Room r
                ON r.room_id = a.room_id
            LEFT JOIN RoomCapacity c
                ON c.room_id = r.room_id
                AND c.effective_from <= substr(s.start_datetime, 1, 10)
                AND (
                    c.effective_to IS NULL
                    OR c.effective_to >= substr(s.start_datetime, 1, 10)
                )
            WHERE a.status = 'ASSIGNED'
        """).fetchall()

        # --------------------------------------------------
        # 2. CHECK FOR MULTIPLE ACTIVE ASSIGNMENTS
        # --------------------------------------------------
        duplicate_exams = conn.execute("""
            SELECT exam_id, COUNT(*) AS total
            FROM ScheduleAssignment
            WHERE status = 'ASSIGNED'
            GROUP BY exam_id
            HAVING COUNT(*) > 1
        """).fetchall()

        for row in duplicate_exams:
            errors.append(
                f"{row['exam_id']}: has multiple active assignments"
            )

        # --------------------------------------------------
        # 3. VALIDATE EACH ASSIGNMENT
        # --------------------------------------------------
        for a in assignments:
            slot_date = a["start_datetime"][:10]

            duration = minutes_between(
                a["start_datetime"],
                a["end_datetime"]
            )

            # Find active accommodations for students enrolled
            # in this exam's course.
            accommodation = conn.execute("""
                SELECT
                    MAX(ac.extra_time_minutes) AS extra_minutes,
                    MAX(ac.needs_accessible_room) AS needs_accessible
                FROM Enrollment en
                JOIN Accommodation ac
                    ON ac.student_id = en.student_id
                WHERE en.course_id = ?
                  AND en.status = 'ACTIVE'
                  AND ac.status = 'ACTIVE'
                  AND ac.effective_from <= ?
                  AND (
                      ac.effective_to IS NULL
                      OR ac.effective_to >= ?
                  )
            """, (
                a["course_id"],
                slot_date,
                slot_date
            )).fetchone()

            extra = accommodation["extra_minutes"] or 0

            needs_accessible = (
                bool(a["accessible_room_required"])
                or bool(accommodation["needs_accessible"])
            )

            required_duration = max(
                a["required_duration_minutes"],
                a["base_duration_minutes"] + extra
            )

            required_seats = max(
                a["required_seats"],
                a["estimated_students"]
            )

            # Check slot duration.
            if duration < required_duration:
                errors.append(
                    f"{a['exam_id']}: slot is shorter than required "
                    f"duration (slot={duration:g} minutes, "
                    f"required={required_duration} minutes)"
                )

            # --------------------------------------------------
            # 4. VALIDATE EFFECTIVE ROOM CAPACITY
            # --------------------------------------------------
            if a["capacity"] is None:
                errors.append(
                    f"{a['exam_id']}: no room capacity record is "
                    f"effective on {slot_date}"
                )
            else:
                if a["capacity"] < required_seats:
                    errors.append(
                        f"{a['exam_id']}: room capacity is insufficient "
                        f"(capacity={a['capacity']}, "
                        f"required={required_seats})"
                    )

                if a["assigned_seats"] < required_seats:
                    errors.append(
                        f"{a['exam_id']}: assigned seat count is "
                        f"insufficient "
                        f"(assigned={a['assigned_seats']}, "
                        f"required={required_seats})"
                    )

                if a["assigned_seats"] > a["capacity"]:
                    errors.append(
                        f"{a['exam_id']}: assigned seat count exceeds "
                        f"room capacity "
                        f"(assigned={a['assigned_seats']}, "
                        f"capacity={a['capacity']})"
                    )

                # Retain the effective-date check, but only when
                # a capacity record was actually found.
                if (
                    a["effective_from"] > slot_date
                    or (
                        a["effective_to"]
                        and a["effective_to"] < slot_date
                    )
                ):
                    errors.append(
                        f"{a['exam_id']}: room capacity is outside "
                        f"its effective dates"
                    )

            # --------------------------------------------------
            # 5. CHECK ROOM ACCESSIBILITY AND PROJECTOR
            # --------------------------------------------------
            if needs_accessible and not a["accessible"]:
                errors.append(
                    f"{a['exam_id']}: accessible room requirement "
                    f"not met"
                )

            if a["projector_required"] and not a["has_projector"]:
                errors.append(
                    f"{a['exam_id']}: required projector is missing"
                )

            # --------------------------------------------------
            # 6. CHECK ROOM AVAILABILITY
            # --------------------------------------------------
            room_available = conn.execute("""
                SELECT 1
                FROM Availability
                WHERE resource_type = 'ROOM'
                  AND resource_id = ?
                  AND slot_id = ?
                  AND status = 'AVAILABLE'
            """, (
                a["room_id"],
                a["slot_id"]
            )).fetchone()

            if not room_available:
                errors.append(
                    f"{a['exam_id']}: room is unavailable for its slot"
                )

            # --------------------------------------------------
            # 7. CHECK INVIGILATOR AVAILABILITY
            # --------------------------------------------------
            inv_available = conn.execute("""
                SELECT 1
                FROM Availability
                WHERE resource_type = 'INVIGILATOR'
                  AND resource_id = ?
                  AND slot_id = ?
                  AND status = 'AVAILABLE'
            """, (
                a["invigilator_id"],
                a["slot_id"]
            )).fetchone()

            if not inv_available:
                errors.append(
                    f"{a['exam_id']}: invigilator is unavailable "
                    f"for its slot"
                )

        # --------------------------------------------------
        # 8. CHECK ROOM AND INVIGILATOR TIME OVERLAPS
        # --------------------------------------------------
        for i, first in enumerate(assignments):
            for second in assignments[i + 1:]:
                overlap = (
                    first["start_datetime"] < second["end_datetime"]
                    and second["start_datetime"] < first["end_datetime"]
                )

                if not overlap:
                    continue

                if first["room_id"] == second["room_id"]:
                    errors.append(
                        f"{first['exam_id']} and {second['exam_id']}: "
                        f"room double-booked"
                    )

                if (
                    first["invigilator_id"]
                    == second["invigilator_id"]
                ):
                    errors.append(
                        f"{first['exam_id']} and {second['exam_id']}: "
                        f"invigilator double-booked"
                    )

        # --------------------------------------------------
        # 9. CHECK STUDENT EXAM CONFLICTS
        # --------------------------------------------------
        conflicts = conn.execute("""
            SELECT exam_id_1, exam_id_2
            FROM ConflictGraph
        """).fetchall()

        assignment_by_exam = {
            a["exam_id"]: a for a in assignments
        }

        for conflict in conflicts:
            first_id = conflict["exam_id_1"]
            second_id = conflict["exam_id_2"]

            first = assignment_by_exam.get(first_id)
            second = assignment_by_exam.get(second_id)

            if first is None or second is None:
                continue

            overlap = (
                first["start_datetime"] < second["end_datetime"]
                and second["start_datetime"] < first["end_datetime"]
            )

            if overlap:
                errors.append(
                    f"{first_id} and {second_id}: "
                    f"student exam conflict"
                )

        # --------------------------------------------------
        # 10. CHECK SCHEDULING OUTCOMES
        # --------------------------------------------------
        missing_outcomes = conn.execute("""
            SELECT e.exam_id
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
        """).fetchall()

        for row in missing_outcomes:
            errors.append(
                f"{row['exam_id']}: has no assignment or "
                f"unscheduled reason"
            )

        unscheduled_without_reason = conn.execute("""
            SELECT exam_id
            FROM UnassignedExam
            WHERE reason_code IS NULL
               OR TRIM(reason_code) = ''
        """).fetchall()

        for row in unscheduled_without_reason:
            errors.append(
                f"{row['exam_id']}: unscheduled exam has no reason code"
            )

        # --------------------------------------------------
        # 11. CHECK SCHEDULE EVENT HISTORY
        # --------------------------------------------------
        event_count = conn.execute("""
            SELECT COUNT(*)
            FROM ScheduleEvent
        """).fetchone()[0]

        if event_count == 0 and assignments:
            warnings.append(
                "No schedule events found; assignment history "
                "may be missing."
            )

        # --------------------------------------------------
        # 12. COLLECT SUMMARY COUNTS
        # --------------------------------------------------
        exam_count = conn.execute("""
            SELECT COUNT(*)
            FROM Exam
            WHERE status != 'CANCELLED'
        """).fetchone()[0]

        scheduled_count = len(assignments)

        unscheduled_count = conn.execute("""
            SELECT COUNT(DISTINCT exam_id)
            FROM UnassignedExam
        """).fetchone()[0]

    # --------------------------------------------------
    # 13. GENERATE VALIDATION REPORT
    # --------------------------------------------------
    report = {
        "total_non_cancelled_exams": exam_count,
        "active_assignments": scheduled_count,
        "distinct_unscheduled_exams": unscheduled_count,
        "errors_count": len(errors),
        "warnings_count": len(warnings),
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }

    with open(
        "validation_report.json",
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(report, file, indent=2)

    print(json.dumps(report, indent=2))
    print("\nReport saved to validation_report.json")


if __name__ == "__main__":
    validate_schedule()
