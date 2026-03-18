"""
former_people_extractor.py — Parse former people records from data.txt and write to SQLite.
"""

import re
import sqlite3

CREATE_FORMER_TABLE = """
CREATE TABLE IF NOT EXISTS former_people (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL,
    start_year INTEGER,
    end_year   INTEGER
);
"""

_FORMER_ROLES = [
    "College Chairpersons",
    "Executive Director",
    "Finance Officer",
    "Media Director",
    "Vice Principal",
    "Chairman",
    "Manager",
    "Advisor",
    "Director",
    "Principal",
]

_FORMER_ROLE_PAT = re.compile(
    r"\s(" + "|".join(re.escape(r) for r in _FORMER_ROLES) + r")\s",
    re.IGNORECASE,
)

_PERSON_ENTRY = re.compile(
    r"([A-Za-z][A-Za-z .()'\-]+?)\s+(\d{4})\s*[-–]\s*(\d{4})",
)


def parse_former_people(raw_text: str) -> list[dict]:
    """Extract former people records from the raw data text."""
    chunks = raw_text.split("\t")
    former_text = ""

    for i, chunk in enumerate(chunks):
        if "Former People" in chunk and "Distinguished" in chunk:
            cleaned = re.sub(r"\n?chunk_\d+\s*$", "", chunk)
            former_text = cleaned
            if i + 1 < len(chunks) and _PERSON_ENTRY.search(chunks[i + 1]):
                next_cleaned = re.sub(r"\n?chunk_\d+\s*$", "", chunks[i + 1])
                former_text += " " + next_cleaned
            break

    if not former_text:
        return []

    parts = _FORMER_ROLE_PAT.split(former_text)
    role_lower_map = {r.lower(): r for r in _FORMER_ROLES}
    records: list[dict] = []
    current_role = None

    for part in parts:
        stripped = part.strip()
        if stripped.lower() in role_lower_map:
            current_role = role_lower_map[stripped.lower()]
            continue
        if current_role is None:
            continue

        for m in _PERSON_ENTRY.finditer(part):
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            if name == name.upper() and len(name) > 3:
                name = " ".join(w.title() if len(w) > 1 else w for w in name.split())
            records.append(
                {
                    "name": name,
                    "role": current_role,
                    "start_year": int(m.group(2)),
                    "end_year": int(m.group(3)),
                }
            )

    return records


def insert_former_people(conn: sqlite3.Connection, former_records: list[dict]) -> None:
    cur = conn.cursor()
    cur.execute(CREATE_FORMER_TABLE)

    for fp in former_records:
        cur.execute(
            "INSERT INTO former_people (name, role, start_year, end_year) VALUES (?, ?, ?, ?)",
            (fp["name"], fp["role"], fp["start_year"], fp["end_year"]),
        )
