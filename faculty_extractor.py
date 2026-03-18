"""
faculty_extractor.py — Parse faculty records from data.txt and write to SQLite.
"""

import re
import sqlite3

CREATE_FACULTY_TABLE = """
CREATE TABLE IF NOT EXISTS faculty (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    designation      TEXT,
    department       TEXT,
    email            TEXT,
    has_phd          INTEGER DEFAULT 0,
    phd_pursuing     INTEGER DEFAULT 0,
    experience_years REAL DEFAULT 0,
    publications     INTEGER DEFAULT 0,
    research         INTEGER DEFAULT 0,
    awards           INTEGER DEFAULT 0,
    patents          INTEGER DEFAULT 0,
    books            INTEGER DEFAULT 0,
    joined           TEXT,
    research_areas   TEXT,
    education        TEXT,
    memberships      TEXT
);
"""

_DEPT_NORMALISE = {
    "computer science engineering": "Computer Science Engineering",
    "electronics and communication engineering": "Electronics and Communication Engineering",
    "electronics & communication engineering": "Electronics and Communication Engineering",
    "electrical and electronics engineering": "Electrical and Electronics Engineering",
    "civil engineering": "Civil Engineering",
    "biotechnology engineering": "Biotechnology Engineering",
    "biomedical engineering": "Biomedical Engineering",
    "applied science & humanities": "Applied Science and Humanities",
    "applied science and humanities": "Applied Science and Humanities",
    "mechanical engineering": "Mechanical Engineering",
}

_ROLES = [
    "Assistant Head Of Department",
    "Head Of Department",
    "Head of Department",
    "Associate Professor",
    "Assistant Professor",
    "Professor",
    "Principal",
    "Dean",
]
_ROLE_PAT = re.compile("|".join(re.escape(r) for r in _ROLES), re.IGNORECASE)

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


def normalise_dept(raw: str) -> str:
    key = raw.strip().lower()
    key = re.sub(r"\s*joined.*", "", key)
    return _DEPT_NORMALISE.get(key, raw.strip())


def _clean_name(name: str) -> str:
    name = re.sub(r"^(?:Dr\.?\s+|Mr\.?\s+|Ms\.?\s+|Mrs\.?\s+)", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"^\d+\s*", "", name).strip()
    name = re.sub(r"\s+", " ", name)
    if name == name.upper() and len(name) > 3:
        name = " ".join(w.title() if len(w) > 1 else w for w in name.split())
    return name


def _detect_phd(text: str) -> tuple[bool, bool]:
    lower = text.lower()
    pursuing_keywords = [
        "phd(doing)", "phd (doing)", "phd(pursuing)", "phd (pursuing)",
        "ph.d(doing)", "ph.d (doing)", "ph.d(pursuing)", "ph.d (pursuing)",
        "ph.d. (pursuing)", "p.hd(pursuing)", "pursuing phd", "pursuing ph.d",
        "pursuing p.hd", "pursuing a ph.d", "pursuing a phd", "ph.d.-doing",
        "ph.d -doing", "phd -doing", "phd-doing", "pursuing a doctor of philosophy",
        "pursuing doctor of philosophy",
    ]
    is_pursuing = any(kw in lower for kw in pursuing_keywords)

    completed_keywords = ["phd ", "ph.d ", "ph.d.", "ph. d", "p.hd ", "ph.d\n"]
    has_completed = False
    for kw in completed_keywords:
        idx = lower.find(kw)
        if idx >= 0:
            surrounding = lower[max(0, idx - 30): idx + 50]
            if "pursuing" not in surrounding and "doing" not in surrounding:
                has_completed = True
                break

    if re.match(r"Back to Faculty Directory\s+Dr\.?\s", text, re.IGNORECASE):
        has_completed = True

    return has_completed, is_pursuing


def _split_raw_chunks(raw_text: str) -> list[str]:
    """Split scraper output into logical chunks for parsing.

    Supports both legacy tab-separated format and current `chunk_<id>` format.
    """
    by_chunk_marker = [p.strip() for p in re.split(r"\bchunk_\d+\s+", raw_text) if p.strip()]
    if len(by_chunk_marker) > 1:
        return by_chunk_marker
    return [c.strip() for c in raw_text.split("\t") if c.strip()]


def parse_profiles(raw_text: str) -> list[dict]:
    chunks = _split_raw_chunks(raw_text)

    profiles = []
    seen_emails: set[str] = set()
    email_re = re.compile(r"[a-z][a-z0-9.]*@sahr[a-z]*\.ac\.in", re.IGNORECASE)
    dept_pat = re.compile("|".join(re.escape(d) for d in _DEPTS), re.IGNORECASE)
    boundary_pat = re.compile(r"(?:View(?:\s+Full)?\s+Profile|Department Heads|Deans|Directors\s*&\s*Principals|Heads)\s*", re.IGNORECASE)

    for chunk in chunks:
        # ===== Legacy rich profile format =====
        if "Back to Faculty Directory" in chunk:
            header_m = re.search(
                r"Back to Faculty Directory\s+"
                r"(.+?)\s+"
                r"(" + "|".join(re.escape(r) for r in _ROLES) + r")\s+"
                r"(" + "|".join(re.escape(d) for d in _DEPTS) + r")",
                chunk,
                re.IGNORECASE,
            )
            if not header_m:
                continue

            raw_name = header_m.group(1).strip()
            designation = header_m.group(2).strip()
            department = normalise_dept(header_m.group(3))

            name = _clean_name(raw_name)
            if len(name) < 3 or len(name) > 50:
                continue
            if not re.match(r"^[A-Za-z][A-Za-z.' ]+$", name):
                continue

            email_m = email_re.search(chunk)
            email = email_m.group(0).lower() if email_m else ""
            if email in seen_emails:
                continue
            if email:
                seen_emails.add(email)

            pubs_m = re.search(r"(\d+)\s+Publications", chunk, re.IGNORECASE)
            research_m = re.search(r"(\d+)\s+Research", chunk, re.IGNORECASE)
            awards_m = re.search(r"(\d+)\s+Awards", chunk, re.IGNORECASE)
            exp_m = re.search(r"(\d+(?:\.\d+)?)\s+Years?\s+Exp", chunk, re.IGNORECASE)
            patents_m = re.search(r"(\d+)\s+Patents", chunk, re.IGNORECASE)
            books_m = re.search(r"(\d+)\s+Books", chunk, re.IGNORECASE)

            publications = int(pubs_m.group(1)) if pubs_m else 0
            research = int(research_m.group(1)) if research_m else 0
            awards = int(awards_m.group(1)) if awards_m else 0
            experience = float(exp_m.group(1)) if exp_m else 0.0
            patents = int(patents_m.group(1)) if patents_m else 0
            books = int(books_m.group(1)) if books_m else 0

            joined_m = re.search(r"Joined:\s*(\d{4}-\d{2}-\d{2})", chunk)
            joined = joined_m.group(1) if joined_m else ""

            has_phd, phd_pursuing = _detect_phd(chunk)

            areas = ""
            areas_m = re.search(
                r"Areas of Interest\s+(.+?)(?:\s+Memberships|\s+Current Responsibilities|\s+Biography|\s+Education)",
                chunk,
                re.IGNORECASE,
            )
            if areas_m:
                a = areas_m.group(1).strip()
                if a.lower() != "no areas specified":
                    areas = a

            edu = ""
            edu_m = re.search(
                r"Education\s+(.+?)(?:\s+Employment History|\s+Publications\s*\(|\s+Training Programs|\s*$)",
                chunk,
                re.IGNORECASE | re.DOTALL,
            )
            if edu_m:
                edu = re.sub(r"\s+", " ", edu_m.group(1).strip())[:500]

            memberships = ""
            mem_m = re.search(
                r"Memberships\s+(.+?)(?:\s+Current Responsibilities|\s+Biography|\s+Education)",
                chunk,
                re.IGNORECASE,
            )
            if mem_m:
                memberships = re.sub(r"\s+", " ", mem_m.group(1).strip())

            profiles.append(
                {
                    "name": name,
                    "designation": designation,
                    "department": department,
                    "email": email,
                    "has_phd": 1 if has_phd else 0,
                    "phd_pursuing": 1 if phd_pursuing else 0,
                    "experience_years": experience,
                    "publications": publications,
                    "research": research,
                    "awards": awards,
                    "patents": patents,
                    "books": books,
                    "joined": joined,
                    "research_areas": areas,
                    "education": edu,
                    "memberships": memberships,
                }
            )
            continue

        # ===== Current site format: listing cards with View Profile/View Full Profile =====
        if "View Profile" not in chunk and "View Full Profile" not in chunk:
            continue

        for em in email_re.finditer(chunk):
            email = em.group(0).lower()
            if email in seen_emails:
                continue

            after = chunk[em.end(): em.end() + 220]
            exp_m = re.search(r"(\d+(?:\.\d+)?)\s+years?\s+experience", after, re.IGNORECASE)
            if not exp_m:
                continue

            start = max(0, em.start() - 420)
            prefix = chunk[start: em.start()]

            dept_matches = list(dept_pat.finditer(prefix))
            if not dept_matches:
                continue

            dept_m = dept_matches[-1]
            department = normalise_dept(dept_m.group(0))

            before_dept = prefix[:dept_m.start()].strip()
            boundaries = list(boundary_pat.finditer(before_dept))
            if boundaries:
                before_dept = before_dept[boundaries[-1].end():].strip()

            role_m = _ROLE_PAT.search(before_dept)
            if role_m:
                raw_name = before_dept[: role_m.start()].strip()
                designation = role_m.group(0)
            else:
                raw_name = before_dept
                designation = ""

            name = _clean_name(raw_name)
            if len(name) < 3 or len(name) > 60:
                continue
            if not re.match(r"^[A-Za-z][A-Za-z.' ]+$", name):
                continue

            pubs_m = re.search(r"(\d+)\s+Publications", after, re.IGNORECASE)
            proj_m = re.search(r"(\d+)\s+Projects?", after, re.IGNORECASE)

            publications = int(pubs_m.group(1)) if pubs_m else 0
            research = int(proj_m.group(1)) if proj_m else 0
            experience = float(exp_m.group(1)) if exp_m else 0.0

            # Approximate research areas from text between department and email.
            dept_abs_end = start + dept_m.end()
            areas_raw = chunk[dept_abs_end: em.start()]
            areas = re.sub(r"\s+", " ", areas_raw).strip(" ,")[:300]

            has_phd = 1 if re.match(r"\s*Dr\.?\s", raw_name, re.IGNORECASE) else 0
            seen_emails.add(email)
            profiles.append(
                {
                    "name": name,
                    "designation": designation,
                    "department": department,
                    "email": email,
                    "has_phd": has_phd,
                    "phd_pursuing": 0,
                    "experience_years": experience,
                    "publications": publications,
                    "research": research,
                    "awards": 0,
                    "patents": 0,
                    "books": 0,
                    "joined": "",
                    "research_areas": areas,
                    "education": "",
                    "memberships": "",
                }
            )

    return profiles


def parse_listing_pages(raw_text: str, existing_emails: set[str]) -> list[dict]:
    boundary_pat = re.compile(
        r"(?:View Profile|Profile Meet[^\n]*?|\d+\s+Awards?)\s*",
        re.IGNORECASE,
    )
    email_re = re.compile(r"[a-z][a-z0-9.]*@sahr[a-z]*\.ac\.in", re.IGNORECASE)
    dept_pat = re.compile("|".join(re.escape(d) for d in _DEPTS), re.IGNORECASE)

    profiles = []
    seen: set[str] = set(existing_emails)

    for em in email_re.finditer(raw_text):
        email = em.group(0).lower()
        if email in seen:
            continue

        start = max(0, em.start() - 500)
        prefix = raw_text[start: em.start()]

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

        before_dept = prefix[last_bound.end(): dept_m.start()].strip()
        if not before_dept or len(before_dept) < 3:
            continue

        role_m = _ROLE_PAT.search(before_dept)
        if role_m:
            name = before_dept[: role_m.start()].strip()
            role = role_m.group(0)
        else:
            name = before_dept
            role = ""

        name = _clean_name(name)
        if len(name) < 3 or len(name) > 50:
            continue
        if not re.match(r"^[A-Za-z][A-Za-z.' ]+$", name):
            continue

        after = raw_text[em.end(): em.end() + 200]
        exp_m = re.search(r"(\d+(?:\.\d+)?)\s+years?\s+experience", after, re.IGNORECASE)
        experience = float(exp_m.group(1)) if exp_m else 0.0
        pubs_m = re.search(r"(\d+)\s+Publications", after, re.IGNORECASE)
        publications = int(pubs_m.group(1)) if pubs_m else 0

        has_phd = 1 if re.match(r"Dr\.?\s", raw_text[max(0, em.start() - 100): em.start()], re.IGNORECASE) else 0

        seen.add(email)
        profiles.append(
            {
                "name": name,
                "designation": role,
                "department": department,
                "email": email,
                "has_phd": has_phd,
                "phd_pursuing": 0,
                "experience_years": experience,
                "publications": publications,
                "research": 0,
                "awards": 0,
                "patents": 0,
                "books": 0,
                "joined": "",
                "research_areas": "",
                "education": "",
                "memberships": "",
            }
        )

    return profiles


def insert_faculty(conn: sqlite3.Connection, profiles: list[dict]) -> None:
    cur = conn.cursor()
    cur.execute(CREATE_FACULTY_TABLE)

    for p in profiles:
        cur.execute(
            """
            INSERT INTO faculty (
                name, designation, department, email,
                has_phd, phd_pursuing, experience_years,
                publications, research, awards, patents, books,
                joined, research_areas, education, memberships
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p["name"], p["designation"], p["department"], p["email"],
                p["has_phd"], p["phd_pursuing"], p["experience_years"],
                p["publications"], p["research"], p["awards"], p["patents"], p["books"],
                p["joined"], p["research_areas"], p["education"], p["memberships"],
            ),
        )
