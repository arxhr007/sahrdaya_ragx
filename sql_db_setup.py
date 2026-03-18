"""
sql_db_setup.py — Build shared SQLite DB for faculty, former people, and students.

Orchestrates three separate extraction modules while writing into one DB file:
- faculty_extractor.py
- former_people_extractor.py
- student_db.py

Usage:
    python sql_db_setup.py          # Build / rebuild shared DB
    python sql_db_setup.py --dump   # Print faculty rows as a table
"""

import os
import sqlite3
import sys

from sql_extractors.faculty_extractor import insert_faculty, parse_listing_pages, parse_profiles
from sql_extractors.former_people_extractor import insert_former_people, parse_former_people
from sql_extractors.student_db import load_students_into_connection

RAW_FILE = "data/raw/sahrdaya_rag.txt"
DB_FILE = "data/sql/college.db"
MIN_PROFILE_PARSE_WARN = 20


def build_db(db_path: str = DB_FILE, raw_path: str = RAW_FILE):
    """Parse data/raw/sahrdaya_rag.txt + data/students.csv and create/rebuild a shared SQLite DB."""
    if not os.path.exists(raw_path):
        print(f"[!] {raw_path} not found")
        return 0

    with open(raw_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    # 1) Parse faculty profile pages
    profiles = parse_profiles(raw_text)
    profile_count = len(profiles)
    existing_emails = {p["email"] for p in profiles if p["email"]}
    print(f"[*] Parsed {profile_count} faculty from individual profile pages")

    # 2) Parse listing pages for additional faculty
    listing_profiles = parse_listing_pages(raw_text, existing_emails)
    print(f"[*] Parsed {len(listing_profiles)} additional faculty from listing pages")

    if profile_count < MIN_PROFILE_PARSE_WARN:
        print(
            "[!] WARNING: very low faculty profile extraction count. "
            "Source format may have changed; verify scraper/preprocess outputs before relying on this DB."
        )

    profiles.extend(listing_profiles)

    # 3) Parse former people from dedicated extractor module
    former = parse_former_people(raw_text)
    print(f"[*] Parsed {len(former)} former people records")

    # 4) Write all entities to ONE shared DB file
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    insert_faculty(conn, profiles)
    insert_former_people(conn, former)

    # 5) Load students into the same DB via separate student module
    student_stats = load_students_into_connection(conn)

    conn.commit()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM faculty")
    total = cur.fetchone()[0]
    cur.execute("SELECT department, COUNT(*) FROM faculty GROUP BY department ORDER BY COUNT(*) DESC")
    dept_counts = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM faculty WHERE has_phd = 1")
    phd_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM faculty WHERE phd_pursuing = 1")
    pursuing_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM former_people")
    former_total = cur.fetchone()[0]
    cur.execute("SELECT role, COUNT(*) FROM former_people GROUP BY role ORDER BY COUNT(*) DESC")
    former_counts = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM students")
    students_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM interests")
    interests_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM student_interests")
    student_interest_links = cur.fetchone()[0]

    print(f"\n[*] data/sql/college.db built — {total} faculty + {former_total} former people")
    print(f"    PhDs: {phd_count}  |  PhD pursuing: {pursuing_count}")
    if student_stats.get("csv_found"):
        print(
            f"    Students: {students_total}  |  Canonical interests: {interests_total}  |  Links: {student_interest_links}"
        )
    else:
        print("    Students: data/students.csv not found (student tables created, no rows loaded)")
    print("    Departments:")
    for dept, count in dept_counts:
        print(f"      {dept}: {count}")
    print("    Former people by role:")
    for role, count in former_counts:
        print(f"      {role}: {count}")

    conn.close()
    return total


def dump_db(db_path: str = DB_FILE):
    """Print all faculty as a table."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT name, designation, department, email, has_phd, experience_years, publications, awards "
        "FROM faculty ORDER BY department, name"
    )
    rows = cur.fetchall()

    print(
        f"\n{'Name':<30} {'Designation':<30} {'Department':<40} {'Email':<30} {'PhD':>3} {'Exp':>5} {'Pubs':>4} {'Awd':>3}"
    )
    print("-" * 175)
    for r in rows:
        name, desig, dept, email, phd, exp, pubs, awards = r
        print(f"{name:<30} {desig:<30} {dept:<40} {email:<30} {'Y' if phd else 'N':>3} {exp:>5.0f} {pubs:>4} {awards:>3}")

    print(f"\nTotal: {len(rows)}")
    conn.close()


if __name__ == "__main__":
    if "--dump" in sys.argv:
        dump_db()
    else:
        build_db()
