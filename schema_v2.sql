PRAGMA foreign_keys = ON;

CREATE TABLE Student (
    student_id TEXT PRIMARY KEY,
    student_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE'))
);

CREATE TABLE Course (
    course_id TEXT PRIMARY KEY,
    course_name TEXT NOT NULL,
    department TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','RETIRED'))
);

CREATE TABLE Enrollment (
    enrollment_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES Student(student_id),
    course_id TEXT NOT NULL REFERENCES Course(course_id),
    enrollment_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','DROPPED')),
    UNIQUE(student_id, course_id)
);

CREATE TABLE Exam (
    exam_id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES Course(course_id),
    exam_name TEXT NOT NULL,
    base_duration_minutes INTEGER NOT NULL CHECK(base_duration_minutes > 0),
    estimated_students INTEGER NOT NULL CHECK(estimated_students >= 0),
    status TEXT NOT NULL CHECK(
        status IN ('PLANNED','SCHEDULED','UNSCHEDULED','CANCELLED')
    )
);

CREATE TABLE ExamRequirement (
    requirement_id TEXT PRIMARY KEY,
    exam_id TEXT NOT NULL UNIQUE REFERENCES Exam(exam_id),
    extra_time_minutes INTEGER NOT NULL CHECK(extra_time_minutes >= 0),
    required_duration_minutes INTEGER NOT NULL CHECK(required_duration_minutes > 0),
    accessible_room_required INTEGER NOT NULL CHECK(accessible_room_required IN (0,1)),
    projector_required INTEGER NOT NULL CHECK(projector_required IN (0,1)),
    required_seats INTEGER NOT NULL CHECK(required_seats >= 0),
    CHECK(required_duration_minutes > extra_time_minutes)
);

CREATE TABLE Room (
    room_id TEXT PRIMARY KEY,
    room_name TEXT NOT NULL,
    accessible INTEGER NOT NULL CHECK(accessible IN (0,1)),
    has_projector INTEGER NOT NULL CHECK(has_projector IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','MAINTENANCE','RETIRED'))
);

CREATE TABLE RoomCapacity (
    room_capacity_id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL REFERENCES Room(room_id),
    capacity INTEGER NOT NULL CHECK(capacity > 0),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    CHECK(effective_to IS NULL OR effective_to >= effective_from),
    UNIQUE(room_id, effective_from)
);

CREATE TABLE Invigilator (
    invigilator_id TEXT PRIMARY KEY,
    invigilator_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','ON_LEAVE','INACTIVE'))
);

CREATE TABLE TimeSlot (
    slot_id TEXT PRIMARY KEY,
    start_datetime TEXT NOT NULL,
    end_datetime TEXT NOT NULL,
    slot_date TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes > 0),
    CHECK(end_datetime > start_datetime)
);

CREATE TABLE Availability (
    availability_id TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL CHECK(resource_type IN ('ROOM','INVIGILATOR')),
    resource_id TEXT NOT NULL,
    slot_id TEXT NOT NULL REFERENCES TimeSlot(slot_id),
    start_datetime TEXT NOT NULL,
    end_datetime TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('AVAILABLE','UNAVAILABLE')),
    reason_code TEXT,
    CHECK(
        (status = 'AVAILABLE' AND reason_code IS NULL)
        OR
        (status = 'UNAVAILABLE' AND reason_code IS NOT NULL)
    ),
    UNIQUE(resource_type, resource_id, slot_id)
);

CREATE TABLE Accommodation (
    accommodation_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES Student(student_id),
    needs_accessible_room INTEGER NOT NULL CHECK(needs_accessible_room IN (0,1)),
    extra_time_minutes INTEGER NOT NULL CHECK(extra_time_minutes >= 0),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','EXPIRED','REVOKED')),
    CHECK(effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE ConflictGraph (
    exam_id_1 TEXT NOT NULL REFERENCES Exam(exam_id),
    exam_id_2 TEXT NOT NULL REFERENCES Exam(exam_id),
    shared_student_count INTEGER NOT NULL CHECK(shared_student_count > 0),
    PRIMARY KEY(exam_id_1, exam_id_2),
    CHECK(exam_id_1 < exam_id_2)
);

CREATE TABLE ScheduleAssignment (
    assignment_id TEXT PRIMARY KEY,
    exam_id TEXT NOT NULL REFERENCES Exam(exam_id),
    room_id TEXT NOT NULL REFERENCES Room(room_id),
    slot_id TEXT NOT NULL REFERENCES TimeSlot(slot_id),
    invigilator_id TEXT NOT NULL REFERENCES Invigilator(invigilator_id),
    assigned_seats INTEGER NOT NULL CHECK(assigned_seats > 0),
    status TEXT NOT NULL CHECK(
        status IN ('ASSIGNED','CANCELLED','SUPERSEDED')
    ),
    created_at TEXT NOT NULL,
    reason_code TEXT
);

CREATE INDEX idx_assignment_slot
ON ScheduleAssignment(slot_id);

CREATE INDEX idx_assignment_exam
ON ScheduleAssignment(exam_id);

CREATE TABLE UnassignedExam (
    unassigned_id TEXT PRIMARY KEY,
    exam_id TEXT NOT NULL REFERENCES Exam(exam_id),
    reason_code TEXT NOT NULL CHECK(reason_code IN (
        'DURATION_EXCEEDS_SLOT',
        'NO_SUITABLE_ROOM',
        'INSUFFICIENT_CAPACITY',
        'STUDENT_CONFLICT',
        'NO_ROOM_AVAILABLE',
        'NO_INVIGILATOR_AVAILABLE'
    )),
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE ScheduleEvent (
    event_id TEXT PRIMARY KEY,
    assignment_id TEXT REFERENCES ScheduleAssignment(assignment_id),
    exam_id TEXT NOT NULL REFERENCES Exam(exam_id),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'ASSIGNED','REASSIGNED','CANCELLED','UNASSIGNED','REJECTED'
    )),
    old_status TEXT,
    new_status TEXT,
    event_time TEXT NOT NULL,
    reason_code TEXT,
    details_json TEXT CHECK(
        details_json IS NULL OR json_valid(details_json)
    )
);

CREATE TRIGGER schedule_event_no_update
BEFORE UPDATE ON ScheduleEvent
BEGIN
    SELECT RAISE(ABORT, 'Schedule events are append-only');
END;

CREATE TRIGGER schedule_event_no_delete
BEFORE DELETE ON ScheduleEvent
BEGIN
    SELECT RAISE(ABORT, 'Schedule events are append-only');
END;

CREATE TRIGGER availability_resource_check
BEFORE INSERT ON Availability
BEGIN
    SELECT CASE
        WHEN NEW.resource_type = 'ROOM'
          AND NOT EXISTS (
              SELECT 1 FROM Room WHERE room_id = NEW.resource_id
          )
        THEN RAISE(ABORT, 'Availability references an unknown room')
        WHEN NEW.resource_type = 'INVIGILATOR'
          AND NOT EXISTS (
              SELECT 1 FROM Invigilator
              WHERE invigilator_id = NEW.resource_id
          )
        THEN RAISE(ABORT, 'Availability references an unknown invigilator')
    END;
END;

CREATE TRIGGER availability_resource_check_update
BEFORE UPDATE ON Availability
BEGIN
    SELECT CASE
        WHEN NEW.resource_type = 'ROOM'
          AND NOT EXISTS (
              SELECT 1 FROM Room WHERE room_id = NEW.resource_id
          )
        THEN RAISE(ABORT, 'Availability references an unknown room')
        WHEN NEW.resource_type = 'INVIGILATOR'
          AND NOT EXISTS (
              SELECT 1 FROM Invigilator
              WHERE invigilator_id = NEW.resource_id
          )
        THEN RAISE(ABORT, 'Availability references an unknown invigilator')
    END;
END;

CREATE INDEX idx_enrollment_student
ON Enrollment(student_id);

CREATE INDEX idx_enrollment_course
ON Enrollment(course_id);

CREATE INDEX idx_availability_slot
ON Availability(slot_id);

CREATE INDEX idx_conflict_exam1
ON ConflictGraph(exam_id_1);

CREATE INDEX idx_conflict_exam2
ON ConflictGraph(exam_id_2);

CREATE TABLE "DataDictionary" ("table" TEXT, "field" TEXT, "data_type" TEXT, "required" TEXT, "allowed_values_or_units" TEXT, "unique" TEXT, "validation_rule" TEXT);
CREATE TABLE "ValidationReport" ("check" TEXT, "passed" TEXT, "details" TEXT);
-- GroundTruth is intentionally NOT stored here; see ground_truth.csv / ground_truth.json.