
import json
from database import get_connection

CORE_TABLES = [
    "Student", "Course", "Enrollment", "Exam",
    "ExamRequirement", "Room", "RoomCapacity",
    "Invigilator", "Availability", "Accommodation",
    "ScheduleAssignment"
]

with get_connection() as conn:
    conn.row_factory = __import__("sqlite3").Row

    print("DATABASE AUDIT")
    print("=" * 60)

    tables = {
        row["name"]: row["sql"]
        for row in conn.execute("""
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
    }

    print("\n1. TABLES AND ROW COUNTS")
    for table in tables:
        count = conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]
        print(f"{table}: {count}")

    print("\n2. CORE TABLE SCHEMAS")
    for table in CORE_TABLES:
        if table not in tables:
            print(f"\nMISSING TABLE: {table}")
            continue

        print(f"\n{table}")
        for col in conn.execute(
            f'PRAGMA table_info("{table}")'
        ):
            print(
                f"  {col['name']} | {col['type']} | "
                f"NOT NULL={bool(col['notnull'])} | "
                f"PRIMARY KEY={bool(col['pk'])}"
            )

    print("\n3. FOREIGN KEY RELATIONSHIPS")
    for table in tables:
        for fk in conn.execute(
            f'PRAGMA foreign_key_list("{table}")'
        ):
            print(
                f"{table}.{fk['from']} -> "
                f"{fk['table']}.{fk['to']}"
            )

    print("\n4. UNIQUE INDEXES")
    for table in CORE_TABLES:
        if table not in tables:
            continue
        for index in conn.execute(
            f'PRAGMA index_list("{table}")'
        ):
            if index["unique"]:
                cols = conn.execute(
                    f'PRAGMA index_info("{index["name"]}")'
                ).fetchall()
                names = [col["name"] for col in cols]
                print(f"{table}: {names}")

    print("\n5. DATA DICTIONARY SAMPLE")
    if "DataDictionary" in tables:
        rows = conn.execute(
            "SELECT * FROM DataDictionary LIMIT 5"
        ).fetchall()
        for row in rows:
            print(dict(row))

    print("\n6. GROUND TRUTH")
    if "GroundTruth" in tables:
        for row in conn.execute(
            "SELECT * FROM GroundTruth ORDER BY test_id"
        ):
            print(dict(row))

    print("\n7. TABLE CONSTRAINT DEFINITIONS")
    for table in CORE_TABLES:
        if table in tables:
            print(f"\n{table}:")
            print(tables[table] or "(No SQL definition available)")

    print("\n8. INTEGRITY")
    print("SQLite:", conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0])
    print("Foreign-key violations:", len(
        conn.execute("PRAGMA foreign_key_check").fetchall()
    ))
