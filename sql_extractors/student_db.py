"""
student_db.py — Ingest data/students.csv into shared data/sql/college.db.

Creates and maintains three tables:
- students
- interests
- student_interests (many-to-many)

This module standardizes comma-separated interest strings into canonical tokens
so SQL queries like "students interested in chess" can match reliably.
"""

import csv
import os
import re
import sqlite3
from typing import Iterable

STUDENTS_CSV = "data/students.csv"

CREATE_STUDENTS_TABLE = """
CREATE TABLE IF NOT EXISTS students (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          TEXT,
    name               TEXT NOT NULL,
    year_of_graduation INTEGER,
    department         TEXT,
    bio                TEXT,
    photo_url          TEXT,
    instagram_username TEXT,
    github_url         TEXT,
    projects_links     TEXT,
    linkedin_url       TEXT,
    personal_website   TEXT,
    UNIQUE(name, year_of_graduation, department)
);
"""

CREATE_INTERESTS_TABLE = """
CREATE TABLE IF NOT EXISTS interests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE
);
"""

CREATE_STUDENT_INTERESTS_TABLE = """
CREATE TABLE IF NOT EXISTS student_interests (
    student_id  INTEGER NOT NULL,
    interest_id INTEGER NOT NULL,
    PRIMARY KEY(student_id, interest_id),
    FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY(interest_id) REFERENCES interests(id) ON DELETE CASCADE
);
"""

_DEPT_MAP = {
    "cs": "Computer Science Engineering",
    "cse": "Computer Science Engineering",
    "computer science": "Computer Science Engineering",
    "computer science engineering": "Computer Science Engineering",
    "ece": "Electronics and Communication Engineering",
    "electronics and communication engineering": "Electronics and Communication Engineering",
    "eee": "Electrical and Electronics Engineering",
    "electrical and electronics engineering": "Electrical and Electronics Engineering",
    "ce": "Civil Engineering",
    "civil": "Civil Engineering",
    "civil engineering": "Civil Engineering",
    "bt": "Biotechnology Engineering",
    "biotech": "Biotechnology Engineering",
    "biotechnology engineering": "Biotechnology Engineering",
    "bme": "Biomedical Engineering",
    "biomedical": "Biomedical Engineering",
    "biomedical engineering": "Biomedical Engineering",
    "ash": "Applied Science and Humanities",
    "applied science and humanities": "Applied Science and Humanities",
}

# Canonical interest aliases. Keep this focused; unknown values still normalize
# to a cleaned canonical token so matching remains deterministic.
_INTEREST_ALIASES = {
    "ches": "chess",
    "chess club": "chess",
    "football game": "football",
    "math": "mathematics",
    "maths": "mathematics",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "webdev": "web development",
    "web dev": "web development",
    "cyber security": "cybersecurity",
}


def _normalize_department(raw: str) -> str:
    key = (raw or "").strip().lower()
    return _DEPT_MAP.get(key, (raw or "").strip())


def _normalize_interest_token(raw: str) -> str:
    token = (raw or "").strip().lower()
    token = token.replace("&", " and ")
    token = re.sub(r"[^a-z0-9+ #]+", " ", token)
    token = re.sub(r"\s+", " ", token).strip()
    if not token:
        return ""
    return _INTEREST_ALIASES.get(token, token)


def _split_interests(raw: str) -> list[str]:
    if not raw:
        return []
    items = []
    for part in raw.split(","):
        normalized = _normalize_interest_token(part)
        if normalized:
            items.append(normalized)
    # De-duplicate while preserving order.
    return list(dict.fromkeys(items))


def _safe_int(value: str) -> int | None:
    text = (value or "").strip()
    if text.isdigit():
        return int(text)
    return None


def ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(CREATE_STUDENTS_TABLE)
    cur.execute(CREATE_INTERESTS_TABLE)
    cur.execute(CREATE_STUDENT_INTERESTS_TABLE)

    # Backward-compatible migration for existing DBs created before projects_links.
    cur.execute("PRAGMA table_info(students)")
    student_cols = {row[1] for row in cur.fetchall()}
    if "projects_links" not in student_cols:
        cur.execute("ALTER TABLE students ADD COLUMN projects_links TEXT")


def _first_non_empty(row: dict, keys: list[str]) -> str:
    for key in keys:
        val = (row.get(key) or "").strip()
        if val:
            return val
    return ""


def _iter_student_rows(csv_path: str) -> Iterable[dict]:
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def load_students_into_connection(conn: sqlite3.Connection, csv_path: str = STUDENTS_CSV) -> dict:
    """Load data/students.csv into students/interests tables using canonical interests.

    Returns counters for diagnostics.
    """
    ensure_tables(conn)

    stats = {
        "students_inserted": 0,
        "interest_links_inserted": 0,
        "rows_skipped": 0,
        "csv_found": os.path.exists(csv_path),
    }

    if not os.path.exists(csv_path):
        return stats

    cur = conn.cursor()

    for row in _iter_student_rows(csv_path):
        name = _first_non_empty(row, ["Name", "name"])
        grad_year = _safe_int(_first_non_empty(row, ["year of graduation", "Year of Graduation"]))
        department = _normalize_department(_first_non_empty(row, ["department", "Department"]))

        # Minimal required fields for stable queries.
        if not name or not department:
            stats["rows_skipped"] += 1
            continue

        cur.execute(
            """
            INSERT INTO students (
                timestamp, name, year_of_graduation, department, bio,
                photo_url, instagram_username, github_url, projects_links,
                linkedin_url, personal_website
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, year_of_graduation, department) DO UPDATE SET
                timestamp = excluded.timestamp,
                bio = excluded.bio,
                photo_url = excluded.photo_url,
                instagram_username = excluded.instagram_username,
                github_url = excluded.github_url,
                projects_links = excluded.projects_links,
                linkedin_url = excluded.linkedin_url,
                personal_website = excluded.personal_website
            """,
            (
                _first_non_empty(row, ["Timestamp", "timestamp"]),
                name,
                grad_year,
                department,
                _first_non_empty(row, ["biography", "bio", "Bio"]),
                _first_non_empty(row, ["photo", "Photo", "photo_url"]),
                _first_non_empty(row, ["instagram(username)", "instagram", "instagram username"]),
                _first_non_empty(row, ["Github(url)", "github(url)", "github", "github_url"]),
                _first_non_empty(row, ["projects(links seprated by commas)", "projects", "projects_links"]),
                _first_non_empty(row, ["linkedin(url)", "linkedin", "linkedin_url"]),
                _first_non_empty(row, ["personal website", "website", "personal_website"]),
            ),
        )

        # Resolve student_id even when row already existed.
        cur.execute(
            """
            SELECT id FROM students
            WHERE name = ? AND year_of_graduation IS ? AND department = ?
            """,
            (name, grad_year, department),
        )
        student_row = cur.fetchone()
        if not student_row:
            stats["rows_skipped"] += 1
            continue

        if cur.rowcount != 0:
            # rowcount is unreliable for SELECT; inserted count tracked below by existence check.
            pass

        student_id = student_row[0]

        interests = _split_interests(
            _first_non_empty(
                row,
                [
                    "Your interests (separate with commas) ",
                    "your intersts (send in separeted commas)",
                    "interests",
                    "interest",
                ],
            )
        )
        if interests:
            for interest in interests:
                cur.execute(
                    "INSERT OR IGNORE INTO interests (canonical_name) VALUES (?)",
                    (interest,),
                )
                cur.execute(
                    "SELECT id FROM interests WHERE canonical_name = ?",
                    (interest,),
                )
                interest_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT OR IGNORE INTO student_interests (student_id, interest_id) VALUES (?, ?)",
                    (student_id, interest_id),
                )
                if cur.rowcount > 0:
                    stats["interest_links_inserted"] += 1

    # Recompute inserted students deterministically from DB count delta is not available
    # because this function may run against pre-populated tables. We use CSV identity set.
    cur.execute("SELECT COUNT(*) FROM students")
    total_students = cur.fetchone()[0]
    stats["students_inserted"] = total_students

    return stats


def ensure_student_data(db_path: str, csv_path: str = STUDENTS_CSV) -> dict:
    """Convenience wrapper for callers that only have db path."""
    conn = sqlite3.connect(db_path)
    try:
        stats = load_students_into_connection(conn, csv_path=csv_path)
        conn.commit()
        return stats
    finally:
        conn.close()
