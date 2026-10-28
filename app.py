
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st


BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(
    os.environ.get(
        "EXAM_SCHEDULING_DB",
        str(BASE_DIR / "generated_exam_scheduling.db"),
    )
).expanduser().resolve()

GROUND_TRUTH_PATH = DB_PATH.parent / "ground_truth.csv"

st.set_page_config(
    page_title="Exam Scheduling System",
    page_icon="📅",
    layout="wide",
)


@st.cache_data(ttl=10)
def load_data(db_path):
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)

    try:
        tables = pd.read_sql_query(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """,
            conn,
        )["name"].tolist()

        def read_table(table):
            if table not in tables:
                return pd.DataFrame()

            return pd.read_sql_query(
                f'SELECT * FROM "{table}"',
                conn,
            )

        required_tables = {
            "ScheduleAssignment",
            "Exam",
            "Course",
            "Room",
            "TimeSlot",
            "Invigilator",
        }

        missing_tables = required_tables.difference(tables)

        if missing_tables:
            missing = ", ".join(sorted(missing_tables))
            raise sqlite3.DatabaseError(
                f"Database is missing required tables: {missing}. "
                "Regenerate the database using the updated schema."
            )

        schedule = pd.read_sql_query(
            """
            SELECT
                a.assignment_id,
                a.exam_id,
                e.exam_name,
                c.course_name,
                c.department,
                r.room_name,
                t.slot_date,
                t.start_datetime,
                t.end_datetime,
                i.invigilator_name,
                a.assigned_seats,
                a.status AS assignment_status,
                e.estimated_students
            FROM ScheduleAssignment a
            JOIN Exam e ON e.exam_id = a.exam_id
            JOIN Course c ON c.course_id = e.course_id
            JOIN Room r ON r.room_id = a.room_id
            JOIN TimeSlot t ON t.slot_id = a.slot_id
            JOIN Invigilator i ON i.invigilator_id = a.invigilator_id
            WHERE a.status = 'ASSIGNED'
              AND e.status != 'CANCELLED'
            ORDER BY t.start_datetime, r.room_name
            """,
            conn,
        )

        return {
            "tables": tables,
            "schedule": schedule,
            "exams": read_table("Exam"),
            "rooms": read_table("Room"),
            "invigilators": read_table("Invigilator"),
            "students": read_table("Student"),
            "unassigned": read_table("UnassignedExam"),
            "events": read_table("ScheduleEvent"),
            "dictionary": read_table("DataDictionary"),
            "accommodations": read_table("Accommodation"),
        }

    finally:
        conn.close()


@st.cache_data(ttl=10)
def load_validation_report():
    report_path = BASE_DIR / "validation_report.json"

    if not report_path.is_file():
        return None

    try:
        with report_path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


@st.cache_data(ttl=10)
def load_ground_truth(csv_path):
    path = Path(csv_path)

    if not path.is_file():
        return pd.DataFrame()

    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return pd.DataFrame()


st.title("Exam Scheduling and Room Allocation")
st.caption(
    "Scheduling overview, room assignments, validation, and audit history."
)

st.info(f"Active database: `{DB_PATH.name}`")

try:
    data = load_data(str(DB_PATH))
except (
    sqlite3.Error,
    FileNotFoundError,
    pd.errors.DatabaseError,
) as error:
    st.error(f"Unable to load the database: {error}")
    st.stop()

schedule = data["schedule"]
exams = data["exams"]
unassigned = data["unassigned"]
events = data["events"]
validation = load_validation_report()
ground_truth = load_ground_truth(str(GROUND_TRUTH_PATH))

# Dashboard metrics
total_exams = len(exams)
scheduled_exams = (
    schedule["exam_id"].nunique() if not schedule.empty else 0
)
unscheduled_exams = (
    unassigned["exam_id"].nunique() if not unassigned.empty else 0
)
rooms_used = (
    schedule["room_name"].nunique() if not schedule.empty else 0
)

st.subheader("Overview")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total exams", total_exams)
m2.metric("Scheduled exams", scheduled_exams)
m3.metric("Unscheduled exams", unscheduled_exams)
m4.metric("Rooms in use", rooms_used)

if validation:
    if validation.get("passed", False):
        st.success(
            "Latest validation report passed: "
            f"{validation.get('errors_count', 0)} errors and "
            f"{validation.get('warnings_count', 0)} warnings."
        )
    else:
        st.warning("The latest validation report contains issues.")
else:
    st.warning(
        "No validation report found. Run validator.py to generate one."
    )

dashboard_tab, schedule_tab, issues_tab, history_tab, data_tab = st.tabs(
    [
        "Dashboard",
        "Exam Schedule",
        "Unscheduled Exams",
        "Audit History",
        "Data Overview",
    ]
)

with dashboard_tab:
    st.subheader("Evaluation metrics")

    active_rooms = (
        int((data["rooms"]["status"] == "ACTIVE").sum())
        if not data["rooms"].empty
        and "status" in data["rooms"].columns
        else 0
    )

    k1, k2, k3 = st.columns(3)

    scheduling_rate = (
        f"{scheduled_exams / total_exams:.1%}"
        if total_exams
        else "N/A"
    )
    k1.metric("Scheduling rate", scheduling_rate)
    k2.metric("Room use (used / active rooms)", f"{rooms_used}/{active_rooms}")

    average_exams = (
        f"{schedule.groupby('invigilator_name').size().mean():.2f}"
        if not schedule.empty
        else "N/A"
    )
    k3.metric("Avg exams per invigilator used", average_exams)

    if not unassigned.empty and "reason_code" in unassigned.columns:
        st.write("Unscheduled exams by reason code")
        reason_summary = (
            unassigned.groupby("reason_code")
            .size()
            .reset_index(name="Exams")
        )
        st.dataframe(
            reason_summary,
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Scheduling summary")

    if not schedule.empty:
        department_summary = (
            schedule.groupby("department")
            .agg(
                Scheduled_Exams=("exam_id", "nunique"),
                Students_Allocated=("assigned_seats", "sum"),
            )
            .reset_index()
        )

        st.write("Schedule by department")
        st.dataframe(
            department_summary,
            use_container_width=True,
            hide_index=True,
        )

        daily_summary = (
            schedule.groupby("slot_date")
            .agg(Exams=("exam_id", "nunique"))
            .reset_index()
        )

        st.write("Exams scheduled per day")
        st.bar_chart(daily_summary.set_index("slot_date"))
    else:
        st.info("No active scheduled exams are available.")

    if not data["rooms"].empty and "status" in data["rooms"].columns:
        room_status = (
            data["rooms"]["status"]
            .value_counts()
            .rename_axis("Room status")
            .reset_index(name="Count")
        )

        st.write("Room status")
        st.dataframe(
            room_status,
            use_container_width=True,
            hide_index=True,
        )

with schedule_tab:
    st.subheader("Scheduled exams")
    st.caption("Use the filters to narrow the schedule.")

    if schedule.empty:
        st.info("No scheduled exams found.")
    else:
        f1, f2, f3 = st.columns(3)

        dates = sorted(schedule["slot_date"].dropna().unique().tolist())
        departments = sorted(
            schedule["department"].dropna().unique().tolist()
        )
        room_names = sorted(
            schedule["room_name"].dropna().unique().tolist()
        )

        with f1:
            selected_dates = st.multiselect(
                "Exam date", dates, default=[]
            )

        with f2:
            selected_departments = st.multiselect(
                "Department", departments, default=[]
            )

        with f3:
            selected_rooms = st.multiselect(
                "Room", room_names, default=[]
            )

        filtered = schedule.copy()

        if selected_dates:
            filtered = filtered[
                filtered["slot_date"].isin(selected_dates)
            ]

        if selected_departments:
            filtered = filtered[
                filtered["department"].isin(selected_departments)
            ]

        if selected_rooms:
            filtered = filtered[
                filtered["room_name"].isin(selected_rooms)
            ]

        display_columns = [
            "slot_date",
            "start_datetime",
            "end_datetime",
            "exam_name",
            "course_name",
            "department",
            "room_name",
            "invigilator_name",
            "estimated_students",
            "assigned_seats",
        ]

        st.write(f"Showing {len(filtered)} assignments")
        st.dataframe(
            filtered[display_columns],
            use_container_width=True,
            hide_index=True,
        )

        csv_data = (
            filtered[display_columns].to_csv(index=False).encode("utf-8")
        )

        st.download_button(
            "Download filtered schedule (CSV)",
            data=csv_data,
            file_name="exam_schedule.csv",
            mime="text/csv",
        )

with issues_tab:
    st.subheader("Unscheduled exams and reasons")

    if unassigned.empty:
        st.success("There are no recorded unscheduled exams.")
    else:
        st.dataframe(
            unassigned,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download unscheduled exams (CSV)",
            data=unassigned.to_csv(index=False).encode("utf-8"),
            file_name="unscheduled_exams.csv",
            mime="text/csv",
        )

with history_tab:
    st.subheader("Schedule audit history")
    st.caption(
        "Events are displayed from ScheduleEvent. The table records changes; "
        "it is not a live activity feed."
    )

    if events.empty:
        st.info("No schedule events are recorded.")
    else:
        if "event_time" in events.columns:
            events = events.sort_values(
                "event_time",
                ascending=False,
            )

        st.dataframe(
            events,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download audit history (CSV)",
            data=events.to_csv(index=False).encode("utf-8"),
            file_name="schedule_audit_history.csv",
            mime="text/csv",
        )

with data_tab:
    st.subheader("Dataset overview")

    summary_rows = []

    table_names = [
        "Student",
        "Course",
        "Enrollment",
        "Exam",
        "ExamRequirement",
        "Room",
        "RoomCapacity",
        "Invigilator",
        "TimeSlot",
        "Availability",
        "Accommodation",
        "ConflictGraph",
        "ScheduleAssignment",
        "ScheduleEvent",
        "UnassignedExam",
        "DataDictionary",
        "ValidationReport",
    ]

    conn = sqlite3.connect(str(DB_PATH))
    try:
        for table_name in table_names:
            if table_name in data["tables"]:
                count = conn.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]

                summary_rows.append(
                    {"Table": table_name, "Records": count}
                )
    finally:
        conn.close()

    st.dataframe(
        pd.DataFrame(summary_rows, columns=["Table", "Records"]),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Ground-truth cases")

    if not ground_truth.empty:
        st.dataframe(
            ground_truth,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(
            f"No ground-truth CSV found at {GROUND_TRUTH_PATH}. "
            "Run generate_data.py to create the ground-truth files."
        )

    st.subheader("Data dictionary")

    if not data["dictionary"].empty:
        st.dataframe(
            data["dictionary"],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No data dictionary records are available.")

st.divider()
st.caption(
    "Synthetic demonstration data only. Review the latest validation report "
    "before relying on the schedule."
)
