"""
firestore_faculty.py — Fetch faculty records from the college site's Firestore REST API.

The public website is a client-rendered SPA: /faculty and /faculty/profile/* serve an
empty shell that prints "Loading faculty data..." until JavaScript populates it. Scraping
those pages as HTML is therefore unreliable — a crawl can look successful while capturing
zero profiles.

The same backend exposes its faculty collection over the public read-only Firestore REST
API, which returns richer data than the rendered page ever did: every faculty member, clean
names, and a `position` field carrying Head of Department / Principal / Dean. That field
populates the `designation` column, which the HTML parser almost always left empty.

Used as the primary faculty source by sql_db_setup.build_db(), which falls back to
faculty_extractor's HTML parsing if this fetch fails for any reason.
"""

import json
import os
import urllib.error
import urllib.request

from sql_extractors.faculty_extractor import normalise_dept

DEFAULT_PROJECT_ID = "college-website-27cf1"
DEFAULT_COLLECTION = "faculty"
PAGE_SIZE = 300
TIMEOUT_SECONDS = 30


def _api_url() -> str:
    """Endpoint override order: explicit URL, then project id, then baked-in default."""
    explicit = (os.getenv("FACULTY_API_URL") or "").strip()
    if explicit:
        return explicit
    project = (os.getenv("FIRESTORE_PROJECT_ID") or DEFAULT_PROJECT_ID).strip()
    collection = (os.getenv("FIRESTORE_FACULTY_COLLECTION") or DEFAULT_COLLECTION).strip()
    return (
        f"https://firestore.googleapis.com/v1/projects/{project}"
        f"/databases/(default)/documents/{collection}"
    )


def _scalar(field: dict) -> str:
    """Unwrap one Firestore typed value into a plain string."""
    if not isinstance(field, dict):
        return ""
    for key in ("stringValue", "integerValue", "doubleValue", "timestampValue"):
        if key in field:
            return str(field[key]).strip()
    if "booleanValue" in field:
        return "yes" if field["booleanValue"] else ""
    if "arrayValue" in field:
        items = field["arrayValue"].get("values", []) or []
        return ", ".join(v for v in (_scalar(i) for i in items) if v)
    if "mapValue" in field:
        inner = field["mapValue"].get("fields", {}) or {}
        return ", ".join(v for v in (_scalar(i) for i in inner.values()) if v)
    return ""


def _get(fields: dict, *names: str) -> str:
    """First non-empty value among several candidate field names."""
    for name in names:
        value = _scalar(fields.get(name, {}))
        if value:
            return value
    return ""


def _as_number(value: str) -> float:
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError, AttributeError):
        return 0.0


def _as_count(value: str) -> int:
    """Counts may arrive as a number or as a list of entries; both mean 'how many'."""
    text = (value or "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return len([part for part in text.split(",") if part.strip()])


def _clean_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if name == name.upper() and len(name) > 3:
        name = " ".join(w.title() if len(w) > 1 else w for w in name.split())
    return name


def _detect_phd(doc_text: str, name: str) -> tuple[int, int]:
    """Completed vs pursuing, from the education text plus the honorific."""
    lower = doc_text.lower()
    pursuing = any(
        kw in lower
        for kw in ("pursuing", "(doing)", "-doing", "doing ph", "phd(doing)")
    )
    completed = any(kw in lower for kw in ("phd", "ph.d", "ph. d", "doctor of philosophy"))
    if name.strip().lower().startswith(("dr.", "dr ")):
        completed = True
    if pursuing and not name.strip().lower().startswith(("dr.", "dr ")):
        completed = False
    return int(completed), int(pursuing)


def fetch_faculty(url: str | None = None) -> list[dict]:
    """Return faculty rows shaped for faculty_extractor.insert_faculty().

    Raises on network/HTTP/JSON failure so the caller can fall back to HTML parsing.
    """
    base = url or _api_url()
    profiles: list[dict] = []
    page_token = ""

    while True:
        sep = "&" if "?" in base else "?"
        page_url = f"{base}{sep}pageSize={PAGE_SIZE}"
        if page_token:
            page_url += f"&pageToken={page_token}"

        request = urllib.request.Request(page_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise urllib.error.HTTPError(
                    page_url, response.status, "unexpected status", response.headers, None
                )
            payload = json.loads(response.read().decode("utf-8"))

        for doc in payload.get("documents", []) or []:
            row = _to_row(doc)
            if row:
                profiles.append(row)

        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break

    if not profiles:
        raise ValueError("Firestore returned no faculty documents")

    profiles.sort(key=lambda r: r["name"].lower())
    return profiles


def _to_row(doc: dict) -> dict | None:
    fields = doc.get("fields", {}) or {}

    name = _clean_name(_get(fields, "name", "fullName"))
    if not name:
        return None

    education = _get(fields, "education", "qualification")
    designation = _get(fields, "position", "designation", "role")
    has_phd, phd_pursuing = _detect_phd(f"{education} {designation}", name)

    return {
        "name": name,
        "designation": designation,
        "department": normalise_dept(_get(fields, "department", "dept")),
        "email": _get(fields, "mailId", "email", "mail").lower(),
        "has_phd": has_phd,
        "phd_pursuing": phd_pursuing,
        "experience_years": _as_number(_get(fields, "yearsOfExperience", "experience")),
        "publications": _as_count(_get(fields, "journalPublications", "publications")),
        "research": _as_count(_get(fields, "researchprojects", "fundedProjects")),
        "awards": _as_count(_get(fields, "awards")),
        "patents": _as_count(_get(fields, "patents")),
        "books": _as_count(_get(fields, "booksChaptersPublished", "books")),
        "joined": _get(fields, "joinedDate", "joined"),
        "research_areas": _get(fields, "areaOfInterest", "researchAreas"),
        "education": education,
        "memberships": _get(fields, "memberships"),
    }
