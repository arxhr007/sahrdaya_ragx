"""
sql_smoke_test.py — quick shared DB sanity checks.

Usage:
    python sql_smoke_test.py
"""

import sqlite3
import sys

DB_PATH = "college.db"

REQUIRED_TABLES = [
    "faculty",
    "former_people",
    "students",
    "interests",
    "student_interests",
]


def fail(msg: str) -> int:
    print(f"[FAIL] {msg}")
    return 1


def main() -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
    except Exception as e:
        return fail(f"Unable to open {DB_PATH}: {e}")

    try:
        for table in REQUIRED_TABLES:
            exists = cur.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]
            if not exists:
                conn.close()
                return fail(f"Missing table: {table}")

        faculty_count = cur.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
        former_count = cur.execute("SELECT COUNT(*) FROM former_people").fetchone()[0]
        student_count = cur.execute("SELECT COUNT(*) FROM students").fetchone()[0]

        if faculty_count <= 0:
            conn.close()
            return fail("No faculty rows found")
        if former_count <= 0:
            conn.close()
            return fail("No former_people rows found")
        if student_count <= 0:
            conn.close()
            return fail("No student rows found")

        sample = cur.execute(
            "SELECT name, year_of_graduation, department FROM students ORDER BY name LIMIT 1"
        ).fetchone()
        if not sample:
            conn.close()
            return fail("Student lookup sample query returned no row")

        print("[OK] SQL smoke test passed")
        print(f"[OK] faculty rows: {faculty_count}")
        print(f"[OK] former_people rows: {former_count}")
        print(f"[OK] students rows: {student_count}")
        print(f"[OK] sample student: {sample[0]} ({sample[1]}), {sample[2]}")
        conn.close()
        return 0
    except Exception as e:
        conn.close()
        return fail(f"Query error: {e}")


if __name__ == "__main__":
    sys.exit(main())
