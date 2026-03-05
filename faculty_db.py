"""
faculty_db.py — Parse data.txt and build a SQLite faculty database.

Extracts structured faculty data from "Back to Faculty Directory" profile pages
in data.txt. Each profile contains: name, designation, department, email,
PhD status, experience, publications, research, awards, patents, books,
join date, research areas, education, memberships.

Usage:
    python faculty_db.py          # Build / rebuild faculty.db
    python faculty_db.py --dump   # Print all rows as a table
"""

import sqlite3
import re
import os
import sys

RAW_FILE = "data.txt"
DB_FILE  = "faculty.db"

# ─── Schema ─────────────────────────────────────────────────────────────────────

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS faculty (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    designation     TEXT,
    department      TEXT,
    email           TEXT,
    has_phd         INTEGER DEFAULT 0,
    phd_pursuing    INTEGER DEFAULT 0,
    experience_years REAL DEFAULT 0,
    publications    INTEGER DEFAULT 0,
    research        INTEGER DEFAULT 0,
    awards          INTEGER DEFAULT 0,
    patents         INTEGER DEFAULT 0,
    books           INTEGER DEFAULT 0,
    joined          TEXT,
    research_areas  TEXT,
    education       TEXT,
    memberships     TEXT
);
"""

CREATE_FORMER_TABLE = """
CREATE TABLE IF NOT EXISTS former_people (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL,
    start_year INTEGER,
    end_year   INTEGER
);
"""

# ─── Department normalisation ────────────────────────────────────────────────────

DEPT_NORMALISE = {
    "computer science engineering":              "Computer Science Engineering",
    "electronics and communication engineering": "Electronics and Communication Engineering",
    "electronics & communication engineering":   "Electronics and Communication Engineering",
    "electrical and electronics engineering":    "Electrical and Electronics Engineering",
    "civil engineering":                         "Civil Engineering",
    "biotechnology engineering":                 "Biotechnology Engineering",
    "biomedical engineering":                    "Biomedical Engineering",
    "applied science & humanities":              "Applied Science and Humanities",
    "applied science and humanities":            "Applied Science and Humanities",
    "mechanical engineering":                    "Mechanical Engineering",
}

def normalise_dept(raw: str) -> str:
    key = raw.strip().lower()
    # Handle cases where "Joined:" info leaked into dept name
    key = re.sub(r'\s*joined.*', '', key)
    return DEPT_NORMALISE.get(key, raw.strip())


# ─── Former People parsing ───────────────────────────────────────────────────────

# Role headings as they appear in the raw text (order matters — longest first)
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
# Build a regex that splits the text at role headings (inline, no newlines needed)
# Uses lookahead so the role label is kept as a separate group
_FORMER_ROLE_PAT = re.compile(
    r'\s(' + '|'.join(re.escape(r) for r in _FORMER_ROLES) + r')\s',
    re.IGNORECASE,
)
# Each person entry: Name  YYYY - YYYY
_PERSON_ENTRY = re.compile(
    r'([A-Za-z][A-Za-z .()\'\'\-]+?)\s+(\d{4})\s*[-–]\s*(\d{4})',
)


def parse_former_people(raw_text: str) -> list[dict]:
    """Extract former people records from the raw data.txt text.

    Returns a list of dicts with keys: name, role, start_year, end_year.
    """
    # Locate the "Former People" section
    chunks = raw_text.split('\t')
    former_text = ""
    for i, chunk in enumerate(chunks):
        if 'Former People' in chunk and 'Distinguished' in chunk:
            # Strip chunk marker (e.g. "\nchunk_14") from end of text
            cleaned = re.sub(r'\n?chunk_\d+\s*$', '', chunk)
            former_text = cleaned
            if i + 1 < len(chunks) and _PERSON_ENTRY.search(chunks[i + 1]):
                next_cleaned = re.sub(r'\n?chunk_\d+\s*$', '', chunks[i + 1])
                former_text += " " + next_cleaned
            break

    if not former_text:
        return []

    # Split by role headings — re.split with a capture group keeps the delimiters
    parts = _FORMER_ROLE_PAT.split(former_text)
    # parts alternates: [preamble, role1, text1, role2, text2, ...]
    # Build a list of (role, text_after) pairs
    role_lower_map = {r.lower(): r for r in _FORMER_ROLES}
    records: list[dict] = []
    current_role = None

    for part in parts:
        stripped = part.strip()
        # Check if this part is a role label
        if stripped.lower() in role_lower_map:
            current_role = role_lower_map[stripped.lower()]
            continue
        if current_role is None:
            continue
        # Parse person entries under this role
        for m in _PERSON_ENTRY.finditer(part):
            name = re.sub(r'\s+', ' ', m.group(1)).strip()
            # Title-case ALL CAPS names
            if name == name.upper() and len(name) > 3:
                name = ' '.join(w.title() if len(w) > 1 else w for w in name.split())
            records.append({
                'name': name,
                'role': current_role,
                'start_year': int(m.group(2)),
                'end_year': int(m.group(3)),
            })

    return records

# ─── Parsing ─────────────────────────────────────────────────────────────────────

# Known roles (longest first)
_ROLES = [
    "Assistant Head Of Department",
    "Head Of Department", "Head of Department",
    "Associate Professor", "Assistant Professor",
    "Professor", "Principal", "Dean",
]
_ROLE_PAT = re.compile("|".join(re.escape(r) for r in _ROLES), re.IGNORECASE)

# Known departments
_DEPTS = [
    "Computer Science Engineering",
    "Electronics and Communication Engineering",
    "Electronics & Communication Engineering",
    "Electrical and Electronics Engineering",
    "Electrical And Electronics Engineering",
    "Civil Engineering",
    "Biotechnology Engineering",
    "Biomedical Engineering",
    "Applied Science & Humanities",
    "Applied Science and Humanities",
    "Mechanical Engineering",
]
_DEPT_PAT = re.compile("|".join(re.escape(d) for d in _DEPTS), re.IGNORECASE)


def _clean_name(name: str) -> str:
    """Strip titles, digits, normalise whitespace, title-case ALL CAPS."""
    name = re.sub(r'^(?:Dr\.?\s+|Mr\.?\s+|Ms\.?\s+|Mrs\.?\s+)', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'^\d+\s*', '', name).strip()
    name = re.sub(r'\s+', ' ', name)
    if name == name.upper() and len(name) > 3:
        name = ' '.join(w.title() if len(w) > 1 else w for w in name.split())
    return name


def _detect_phd(text: str) -> tuple[bool, bool]:
    """Return (has_phd, phd_pursuing) by inspecting education / biography text."""
    lower = text.lower()
    pursuing_keywords = ["phd(doing)", "phd (doing)", "phd(pursuing)", "phd (pursuing)",
                         "ph.d(doing)", "ph.d (doing)", "ph.d(pursuing)", "ph.d (pursuing)",
                         "ph.d. (pursuing)", "p.hd(pursuing)", "pursuing phd", "pursuing ph.d",
                         "pursuing p.hd", "pursuing a ph.d", "pursuing a phd",
                         "ph.d.-doing", "ph.d -doing", "phd -doing", "phd-doing",
                         "pursuing a doctor of philosophy",
                         "pursuing doctor of philosophy"]
    is_pursuing = any(kw in lower for kw in pursuing_keywords)

    completed_keywords = ["phd ", "ph.d ", "ph.d.", "ph. d", "p.hd ", "ph.d\n"]
    has_completed = False
    for kw in completed_keywords:
        idx = lower.find(kw)
        if idx >= 0:
            # Make sure it's not just "pursuing" or "doing"
            surrounding = lower[max(0, idx-30):idx+50]
            if "pursuing" not in surrounding and "doing" not in surrounding:
                has_completed = True
                break

    # Also check for "Dr." / "Dr " prefix on name in the original text
    if re.match(r'Back to Faculty Directory\s+Dr\.?\s', text, re.IGNORECASE):
        has_completed = True

    return has_completed, is_pursuing


def parse_profiles(raw_text: str) -> list[dict]:
    """Parse all 'Back to Faculty Directory' profile blocks from raw data."""
    # Split by tab (chunk separator in data.txt)
    chunks = raw_text.split('\t')

    profiles = []
    seen_emails: set[str] = set()

    for chunk in chunks:
        if 'Back to Faculty Directory' not in chunk:
            continue

        # ── Header: Name  Role  Department  Joined: ... ──
        header_m = re.match(
            r'Back to Faculty Directory\s+'
            r'(.+?)\s+'
            r'(' + '|'.join(re.escape(r) for r in _ROLES) + r')\s+'
            r'(' + '|'.join(re.escape(d) for d in _DEPTS) + r')',
            chunk, re.IGNORECASE,
        )
        if not header_m:
            continue

        raw_name = header_m.group(1).strip()
        designation = header_m.group(2).strip()
        department = normalise_dept(header_m.group(3))

        name = _clean_name(raw_name)
        if len(name) < 3 or len(name) > 50:
            continue
        if not re.match(r'^[A-Za-z][A-Za-z.\' ]+$', name):
            continue

        # ── Email ──
        email_m = re.search(r'[a-z][a-z0-9.]*@sahrdaya\.ac\.in', chunk, re.IGNORECASE)
        email = email_m.group(0).lower() if email_m else ""
        if email in seen_emails:
            continue  # dedup
        if email:
            seen_emails.add(email)

        # ── Numeric fields from the stats line ──
        #    "3 Publications 0 Research 0 Awards 17 Years Exp. 0 Patents 0 Books"
        pubs_m     = re.search(r'(\d+)\s+Publications', chunk, re.IGNORECASE)
        research_m = re.search(r'(\d+)\s+Research', chunk, re.IGNORECASE)
        awards_m   = re.search(r'(\d+)\s+Awards', chunk, re.IGNORECASE)
        exp_m      = re.search(r'(\d+(?:\.\d+)?)\s+Years?\s+Exp', chunk, re.IGNORECASE)
        patents_m  = re.search(r'(\d+)\s+Patents', chunk, re.IGNORECASE)
        books_m    = re.search(r'(\d+)\s+Books', chunk, re.IGNORECASE)

        publications = int(pubs_m.group(1))     if pubs_m     else 0
        research     = int(research_m.group(1))  if research_m else 0
        awards       = int(awards_m.group(1))    if awards_m   else 0
        experience   = float(exp_m.group(1))     if exp_m      else 0.0
        patents      = int(patents_m.group(1))   if patents_m  else 0
        books        = int(books_m.group(1))     if books_m    else 0

        # ── Joined date ──
        joined_m = re.search(r'Joined:\s*(\d{4}-\d{2}-\d{2})', chunk)
        joined = joined_m.group(1) if joined_m else ""

        # ── PhD detection ──
        has_phd, phd_pursuing = _detect_phd(chunk)

        # ── Research areas ──
        areas = ""
        areas_m = re.search(r'Areas of Interest\s+(.+?)(?:\s+Memberships|\s+Current Responsibilities|\s+Biography|\s+Education)', chunk, re.IGNORECASE)
        if areas_m:
            a = areas_m.group(1).strip()
            if a.lower() != "no areas specified":
                areas = a

        # ── Education ──
        edu = ""
        edu_m = re.search(r'Education\s+(.+?)(?:\s+Employment History|\s+Publications\s*\(|\s+Training Programs|\s*$)', chunk, re.IGNORECASE | re.DOTALL)
        if edu_m:
            edu = re.sub(r'\s+', ' ', edu_m.group(1).strip())[:500]

        # ── Memberships ──
        memberships = ""
        mem_m = re.search(r'Memberships\s+(.+?)(?:\s+Current Responsibilities|\s+Biography|\s+Education)', chunk, re.IGNORECASE)
        if mem_m:
            memberships = re.sub(r'\s+', ' ', mem_m.group(1).strip())

        profiles.append({
            'name':             name,
            'designation':      designation,
            'department':       department,
            'email':            email,
            'has_phd':          1 if has_phd else 0,
            'phd_pursuing':     1 if phd_pursuing else 0,
            'experience_years': experience,
            'publications':     publications,
            'research':         research,
            'awards':           awards,
            'patents':          patents,
            'books':            books,
            'joined':           joined,
            'research_areas':   areas,
            'education':        edu,
            'memberships':      memberships,
        })

    return profiles


# ─── Also parse the faculty listing pages (chunks 122-130) ──────────────────────
# These give us entries that may NOT have individual profile pages.

def parse_listing_pages(raw_text: str, existing_emails: set[str]) -> list[dict]:
    """Parse the all-faculty listing page entries (View Profile format).
    Only returns entries whose email is NOT already in existing_emails."""
    boundary_pat = re.compile(
        r'(?:View Profile|Profile Meet[^\n]*?|\d+\s+Awards?)\s*',
        re.IGNORECASE,
    )
    email_re = re.compile(r'[a-z][a-z0-9.]*@sahrdaya\.ac\.in', re.IGNORECASE)
    dept_pat = re.compile('|'.join(re.escape(d) for d in _DEPTS), re.IGNORECASE)

    profiles = []
    seen: set[str] = set(existing_emails)

    for em in email_re.finditer(raw_text):
        email = em.group(0).lower()
        if email in seen:
            continue

        start = max(0, em.start() - 500)
        prefix = raw_text[start:em.start()]

        dept_matches = list(dept_pat.finditer(prefix))
        if not dept_matches:
            continue
        dept_m = dept_matches[-1]
        department = normalise_dept(dept_m.group(0))

        boundaries = list(boundary_pat.finditer(prefix))
        boundaries_before = [b for b in boundaries if b.end() <= dept_m.start()]
        if not boundaries_before:
            continue
        last_bound = boundaries_before[-1]

        before_dept = prefix[last_bound.end():dept_m.start()].strip()
        if not before_dept or len(before_dept) < 3:
            continue

        role_m = _ROLE_PAT.search(before_dept)
        if role_m:
            name = before_dept[:role_m.start()].strip()
            role = role_m.group(0)
        else:
            name = before_dept
            role = ""

        name = _clean_name(name)
        if len(name) < 3 or len(name) > 50:
            continue
        if not re.match(r'^[A-Za-z][A-Za-z.\' ]+$', name):
            continue

        # Extract experience from text after email
        after = raw_text[em.end():em.end() + 200]
        exp_m = re.search(r'(\d+(?:\.\d+)?)\s+years?\s+experience', after, re.IGNORECASE)
        experience = float(exp_m.group(1)) if exp_m else 0.0
        pubs_m = re.search(r'(\d+)\s+Publications', after, re.IGNORECASE)
        publications = int(pubs_m.group(1)) if pubs_m else 0

        # Check for Dr. prefix → PhD
        has_phd = 1 if re.match(r'Dr\.?\s', raw_text[max(0, em.start()-100):em.start()], re.IGNORECASE) else 0

        seen.add(email)
        profiles.append({
            'name':             name,
            'designation':      role,
            'department':       department,
            'email':            email,
            'has_phd':          has_phd,
            'phd_pursuing':     0,
            'experience_years': experience,
            'publications':     publications,
            'research':         0,
            'awards':           0,
            'patents':          0,
            'books':            0,
            'joined':           "",
            'research_areas':   "",
            'education':        "",
            'memberships':      "",
        })

    return profiles


# ─── Database operations ─────────────────────────────────────────────────────────

def build_db(db_path: str = DB_FILE, raw_path: str = RAW_FILE):
    """Parse data.txt and create/rebuild faculty.db."""
    if not os.path.exists(raw_path):
        print(f"[!] {raw_path} not found")
        return 0

    with open(raw_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    # 1. Parse individual profile pages (richest data)
    profiles = parse_profiles(raw_text)
    existing_emails = {p['email'] for p in profiles if p['email']}
    print(f"[*] Parsed {len(profiles)} faculty from individual profile pages")

    # 2. Parse listing pages for any we missed
    listing_profiles = parse_listing_pages(raw_text, existing_emails)
    print(f"[*] Parsed {len(listing_profiles)} additional faculty from listing pages")
    profiles.extend(listing_profiles)

    # 3. Parse former people
    former = parse_former_people(raw_text)
    print(f"[*] Parsed {len(former)} former people records")

    # 4. Write to SQLite
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(CREATE_TABLE)
    cur.execute(CREATE_FORMER_TABLE)

    for p in profiles:
        cur.execute("""
            INSERT INTO faculty (
                name, designation, department, email,
                has_phd, phd_pursuing, experience_years,
                publications, research, awards, patents, books,
                joined, research_areas, education, memberships
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p['name'], p['designation'], p['department'], p['email'],
            p['has_phd'], p['phd_pursuing'], p['experience_years'],
            p['publications'], p['research'], p['awards'], p['patents'], p['books'],
            p['joined'], p['research_areas'], p['education'], p['memberships'],
        ))

    for fp in former:
        cur.execute(
            "INSERT INTO former_people (name, role, start_year, end_year) VALUES (?, ?, ?, ?)",
            (fp['name'], fp['role'], fp['start_year'], fp['end_year']),
        )

    conn.commit()

    # Print summary
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

    print(f"\n[*] faculty.db built — {total} faculty + {former_total} former people")
    print(f"    PhDs: {phd_count}  |  PhD pursuing: {pursuing_count}")
    print(f"    Departments:")
    for dept, count in dept_counts:
        print(f"      {dept}: {count}")
    print(f"    Former people by role:")
    for role, count in former_counts:
        print(f"      {role}: {count}")

    conn.close()
    return total


def dump_db(db_path: str = DB_FILE):
    """Print all faculty as a table."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name, designation, department, email, has_phd, experience_years, publications, awards FROM faculty ORDER BY department, name")
    rows = cur.fetchall()

    print(f"\n{'Name':<30} {'Designation':<30} {'Department':<40} {'Email':<30} {'PhD':>3} {'Exp':>5} {'Pubs':>4} {'Awd':>3}")
    print("-" * 175)
    for r in rows:
        name, desig, dept, email, phd, exp, pubs, awards = r
        print(f"{name:<30} {desig:<30} {dept:<40} {email:<30} {'Y' if phd else 'N':>3} {exp:>5.0f} {pubs:>4} {awards:>3}")

    print(f"\nTotal: {len(rows)}")
    conn.close()


# ─── CLI ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--dump" in sys.argv:
        dump_db()
    else:
        build_db()
