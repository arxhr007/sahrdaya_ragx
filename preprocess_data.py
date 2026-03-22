"""
preprocess_data.py — Run this ONCE (or whenever data/raw/sahrdaya_rag.txt changes) to produce
data_cleaned.jsonl which rag_setup.py loads at startup.

Usage:
    python preprocess_data.py

Input:  data/raw/sahrdaya_rag.txt  (raw scraped chunks:  chunk_N<TAB>content)
        data/raw/student_inputs/*.txt (optional plain-text student data files)
Output: data/processed/data_cleaned.jsonl (one JSON object per optimized chunk)
"""

import json
import os
import re
import sys

import nltk
from nltk.tokenize import sent_tokenize

# Ensure punkt tokenizer data is available
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)

# ═══════════════════════════════════════════════════════════════════════════════
# 1.  TEXT CLEANING
# ═══════════════════════════════════════════════════════════════════════════════

_NAV_NOISE = re.compile(
    r"(?:Back to (?:Home|Clubs|Cells|CU|Faculty Directory|Internal Examination Cell|IQAC)\s*)|"
    r"(?:Refresh\s*)|"
    r"(?:View (?:PDF|Details|Document|Full Profile)\s*)|"
    r"(?:Download\s*(?:PDF)?)|"
    r"(?:Open in New Tab\s*)|"
    r"(?:Drop here to move to end\s*)|"
    r"(?:Select (?:First|Second|Third) Preference\s*)|"
    r"(?:Optional - Select if you want a \w+ preference\s*)",
    re.IGNORECASE,
)

_REPEATED_WHITESPACE = re.compile(r"[ \t]{3,}")
_REPEATED_NEWLINES   = re.compile(r"\n{3,}")
_TRAILING_BULLETS    = re.compile(r"(?:^[\s•\-\*]+$)", re.MULTILINE)


def clean_text(text: str) -> str:
    """Remove UI / navigation noise and normalise whitespace."""
    text = _NAV_NOISE.sub(" ", text)
    text = _TRAILING_BULLETS.sub("", text)
    text = _REPEATED_WHITESPACE.sub(" ", text)
    text = _REPEATED_NEWLINES.sub("\n\n", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  CATEGORY DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_CATEGORY_RULES = [
    ("faculty",        re.compile(r"faculty|professor|assistant professor|associate professor|head of department|HOD|teaching staff", re.I)),
    ("admissions",     re.compile(r"admission|apply|application form|eligibility|intake|KEAM|counselling", re.I)),
    ("examination",    re.compile(r"examination|exam|result|revaluation|timetable|invigilation|malpractice|semester.*exam", re.I)),
    ("department_cse", re.compile(r"computer science|CSE\b|dept.*of.*cs", re.I)),
    ("department_ece", re.compile(r"electronics.*communication|ECE\b|dept.*of.*ec", re.I)),
    ("department_eee", re.compile(r"electrical.*electronics|EEE\b|dept.*of.*eee", re.I)),
    ("department_ce",  re.compile(r"civil engineering|\bCE\b.*(?:engineering|department|dept)|dept.*of.*civil", re.I)),
    ("department_bt",  re.compile(r"biotechnology|BT\b.*engineering|dept.*of.*bt", re.I)),
    ("department_bme", re.compile(r"biomedical|BME\b|dept.*of.*bm", re.I)),
    ("department_ash", re.compile(r"applied science.*humanities|ASH\b", re.I)),
    ("placement",      re.compile(r"placement|internship|recruit|training.*placement|industry partner", re.I)),
    ("research",       re.compile(r"publication|journal|conference paper|patent|funded project|research", re.I)),
    ("announcement",   re.compile(r"announcement|event|workshop|hackathon|competition|seminar|webinar", re.I)),
    ("governance",     re.compile(r"governing body|academic council|board of studies|finance committee|IQAC|NAAC|NBA|autonomous", re.I)),
    ("clubs",          re.compile(r"NSS|IEDC|PALS|club|cell|committee|association", re.I)),
    ("infrastructure", re.compile(r"hostel|canteen|library|lab|auditorium|campus|amenit|transport|bus", re.I)),
    ("about",          re.compile(r"vision|mission|about us|institution overview|history|profile|management|diocese|meet the team|website team|behind the.*website", re.I)),
    ("mou",            re.compile(r"MOU|memorandum|partnership|collaboration|industry.*academ", re.I)),
]


def detect_categories(text: str) -> list[str]:
    cats = [cat for cat, pat in _CATEGORY_RULES if pat.search(text)]
    return cats if cats else ["general"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2b.  SEARCH-ALIAS INJECTION
# ═══════════════════════════════════════════════════════════════════════════════
# Inject concatenated name variants so BM25 can match "jispaul" → "Jis Paul" etc.

# Pattern: "Dr. Firstname Lastname", "Mr. Firstname Lastname", etc.
_TITLED_NAME = re.compile(
    r"(?:Dr\.|Mr\.|Ms\.|Mrs\.|Prof\.|Fr\.|Sr\.)\s+"
    r"([A-Z][a-z]{2,})\s+"                       # first name
    r"([A-Z][a-z]{2,}(?:\s+[A-Z]\.?)?)",         # last name (+ optional initial)
    re.MULTILINE,
)

# Pattern: plain "Firstname Lastname" in title-case near faculty keywords
_PLAIN_NAME = re.compile(
    r"(?:(?:Professor|HOD|Head of Department|Dean|Warden)\s+)"
    r"([A-Z][a-z]{2,})\s+"
    r"([A-Z][a-z]{2,})",
    re.IGNORECASE,
)

# Singular/plural aliases for common terms
_TERM_ALIASES = {
    "HOD":  "HODs Head of Department Heads of Department",
    "MOU":  "MOUs Memorandum Memorandums",
    "NSS":  "National Service Scheme",
    "IEDC": "Innovation Entrepreneurship Development Centre",
    "PALS": "Peer Assisted Learning Scheme",
}

# Specific name aliases for people whose names don't get matched
# due to common name parts (e.g. "Thomas" appears 100+ times)
_SPECIFIC_NAME_ALIASES = {
    "Aaron Thomas":    "AaronThomas aaronthomas website team backend developer",
    "Shayen Thomas":   "ShayenThomas shayenthomas website team infrastructure developer",
    "Mishal Shanavas": "MishalShanavas mishalshanavas website team devops",
    "Mathew Geejo":    "MathewGeejo mathewgeejo website team frontend developer",
}


def inject_search_aliases(text: str) -> str:
    """Append search-friendly name and term aliases to chunk text."""
    aliases: list[str] = []

    # Add concatenated forms for titled names  (Dr. Jis Paul → Jispaul JisPaul)
    for m in _TITLED_NAME.finditer(text):
        first, last = m.group(1), m.group(2).split()[0]  # ignore trailing initial
        concat = first + last                              # JisPaul
        concat_lower = concat.lower()                      # jispaul
        if concat.lower() not in text.lower():
            aliases.append(concat)
            aliases.append(concat_lower)

    # Add concatenated forms for plain-named roles
    for m in _PLAIN_NAME.finditer(text):
        first, last = m.group(1), m.group(2)
        concat = first + last
        if concat.lower() not in text.lower():
            aliases.append(concat)

    # Add term aliases where the term appears
    for term, expansion in _TERM_ALIASES.items():
        if re.search(rf'\b{term}\b', text):
            aliases.append(expansion)

    # Add specific name aliases for people with common name parts
    for name, expansion in _SPECIFIC_NAME_ALIASES.items():
        if name.lower() in text.lower():
            aliases.append(expansion)

    if aliases:
        return text + "\n[search aliases: " + " | ".join(dict.fromkeys(aliases)) + "]"
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  SENTENCE-AWARE RE-CHUNKING
# ═══════════════════════════════════════════════════════════════════════════════
# Uses nltk.sent_tokenize so chunks NEVER break mid-sentence, mid-name,
# or mid-table-row.  Sentences are the atomic unit; we greedily pack them
# into chunks up to CHUNK_SIZE, with 1-2 sentence overlap for context.

CHUNK_SIZE        = 700     # target chars per chunk
OVERLAP_SENTENCES = 1       # sentences carried from end of prev chunk
SPLIT_THRESHOLD   = int(CHUNK_SIZE * 1.3)   # 910 chars — only split above this

# Table-row pattern to avoid breaking tabular data
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


def _sentencize(text: str) -> list[str]:
    """Split text into sentences, preserving table rows as atomic units."""
    # If the text has markdown table rows, split them out first so they
    # aren't broken by sent_tokenize.
    segments: list[str] = []
    last_end = 0
    for m in _TABLE_ROW.finditer(text):
        # Prose before this table row  → sentence-tokenize it
        before = text[last_end : m.start()].strip()
        if before:
            segments.extend(sent_tokenize(before))
        # The table row itself is one atomic "sentence"
        segments.append(m.group(0).strip())
        last_end = m.end()
    # Remaining prose after last table row
    remaining = text[last_end:].strip()
    if remaining:
        segments.extend(sent_tokenize(remaining))
    return segments


def _split_text(text: str) -> list[str]:
    """Pack sentences into chunks of ~CHUNK_SIZE chars with sentence overlap."""
    sentences = _sentencize(text)
    if not sentences:
        return [text] if text.strip() else []

    # If the whole thing already fits, return as-is
    if len(text) <= CHUNK_SIZE:
        return [text]

    chunks: list[str] = []
    current_sents: list[str] = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent) + 1  # +1 for the joining space

        # If a single sentence exceeds CHUNK_SIZE, hard-wrap it
        if sent_len > CHUNK_SIZE:
            if current_sents:
                chunks.append(" ".join(current_sents))
                current_sents = []
                current_len = 0
            # Break oversized sentence at word boundaries
            words = sent.split()
            buf = ""
            for w in words:
                if buf and len(buf) + 1 + len(w) > CHUNK_SIZE:
                    chunks.append(buf)
                    buf = w
                else:
                    buf = (buf + " " + w) if buf else w
            if buf:
                current_sents = [buf]
                current_len = len(buf)
            continue

        if current_len + sent_len > CHUNK_SIZE and current_sents:
            chunks.append(" ".join(current_sents))
            # Overlap: carry last N sentences into next chunk
            overlap = current_sents[-OVERLAP_SENTENCES:]
            current_sents = overlap[:]
            current_len = sum(len(s) + 1 for s in current_sents)

        current_sents.append(sent)
        current_len += sent_len

    if current_sents:
        chunks.append(" ".join(current_sents))

    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

INPUT_FILE  = "data/raw/sahrdaya_rag.txt"
OUTPUT_FILE = "data/processed/data_cleaned.jsonl"
STUDENT_INPUT_DIR = "data/raw/student_inputs"


# ═══════════════════════════════════════════════════════════════════════════════
# 4b.  FORMER PEOPLE — STRUCTURED ROLE-BASED SPLITTING
# ═══════════════════════════════════════════════════════════════════════════════
# The raw "Former People" chunk is one long flat string where role labels
# (Chairman, Manager, Executive Director, etc.) are mixed inline with names
# and dates.  The generic sentence-based splitter cannot preserve role
# boundaries, so queries like "list all former Managers" return incomplete
# or incorrect results.
#
# This handler:
#   1. Detects the "Former People" chunk.
#   2. Parses it into per-role sections using known role labels.
#   3. Within each role, extracts individual (name, tenure) entries.
#   4. Emits one sub-chunk per role, formatted as a clear list.

_FORMER_ROLE_LABELS = [
    "Chairman",
    "Manager",
    "Executive Director",
    "Finance Officer",
    "Advisor",
    "Director",
    "Principal",
    "Vice Principal",
    "Media Director",
    "College Chairpersons",
]

# Build a regex that splits on role labels appearing in the text.
# We match the role label only when it is NOT preceded by "Executive" or "Vice"
# or "Media" (to avoid partial matches like "Director" eating "Executive Director").
# Strategy: match the longest role labels first.
_FORMER_ROLES_SORTED = sorted(_FORMER_ROLE_LABELS, key=len, reverse=True)
_FORMER_ROLE_PATTERN = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(r) for r in _FORMER_ROLES_SORTED) + r")(?=\s)",
)

# Pattern: name followed by year range (YYYY - YYYY)
_PERSON_ENTRY = re.compile(
    r"(.+?)\s+(\d{4})\s*-\s*(\d{4})"
)


def _parse_former_people(text: str) -> list[dict] | None:
    """Parse the 'Former People' flat text into per-role structured chunks.
    Returns a list of dicts ready for data_cleaned.jsonl, or None if not applicable."""

    if "Former People" not in text:
        return None

    # Strip the intro header
    body = re.sub(
        r"^Former People\s+Distinguished alumni and former faculty members\s*",
        "", text, flags=re.IGNORECASE
    ).strip()

    # Split text by role labels
    # Find all role label positions
    matches = list(_FORMER_ROLE_PATTERN.finditer(body))
    if not matches:
        return None

    role_sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        role_label = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_text = body[start:end].strip()
        role_sections.append((role_label, section_text))

    # Build structured chunks
    result = []
    categories = ["faculty", "governance", "about"]

    for role_label, section_text in role_sections:
        entries = _PERSON_ENTRY.findall(section_text)
        if not entries:
            continue

        lines = [f"Former {role_label} of Sahrdaya College of Engineering & Technology (SCET):"]
        for name, year_from, year_to in entries:
            # Clean name: remove leading/trailing whitespace and dots
            name = name.strip().strip(".")
            if name:
                lines.append(f"  - {name} ({year_from} - {year_to})")

        chunk_content = "\n".join(lines)

        # Add search aliases for names
        aliases = []
        for name, _, _ in entries:
            name = name.strip().strip(".")
            parts = name.split()
            # Get last two significant name parts for alias
            significant = [p for p in parts if len(p) > 2 and p not in
                          ("Rev.", "Fr.", "Dr.", "Prof.", "Mr.", "Ms.", "Mrs.",
                           "V.", "MR.", "MS.", "MRS.", "Sr.")]
            if len(significant) >= 2:
                concat = significant[-2] + significant[-1]
                aliases.append(concat)
                aliases.append(concat.lower())

        if aliases:
            chunk_content += "\n[search aliases: " + " | ".join(dict.fromkeys(aliases)) + "]"

        cat_prefix = f"[{', '.join(categories)}] "
        safe_label = role_label.lower().replace(" ", "_")
        result.append({
            "id": f"chunk_13_former_{safe_label}",
            "parent_chunk": "chunk_13",
            "categories": categories,
            "content": cat_prefix + chunk_content,
        })

    # Also emit a summary chunk listing all roles
    summary_lines = ["Former People — Sahrdaya College of Engineering & Technology (SCET)",
                     "The following former leadership roles are documented:"]
    for role_label, section_text in role_sections:
        entries = _PERSON_ENTRY.findall(section_text)
        count = len(entries)
        summary_lines.append(f"  - Former {role_label}: {count} person(s)")
    summary_lines.append("Ask about a specific role (e.g. 'list all former Principals') for details.")

    cat_prefix = f"[{', '.join(categories)}] "
    result.insert(0, {
        "id": "chunk_13_former_summary",
        "parent_chunk": "chunk_13",
        "categories": categories,
        "content": cat_prefix + "\n".join(summary_lines),
    })

    return result


def _load_student_input_chunks(input_dir: str) -> list[tuple[str, str]]:
    """Load plain-text student input files and convert them to raw chunk tuples.

    Each .txt file is split by blank lines. Every non-empty block becomes one raw chunk.
    """
    if not os.path.isdir(input_dir):
        return []

    txt_files = sorted(
        name for name in os.listdir(input_dir)
        if name.lower().endswith(".txt")
    )
    chunks: list[tuple[str, str]] = []

    for filename in txt_files:
        file_path = os.path.join(input_dir, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read().strip()
        except OSError as exc:
            print(f"[!] Skipping {file_path}: {exc}")
            continue

        if not raw_text:
            continue

        blocks = [b.strip() for b in re.split(r"\n\s*\n+", raw_text) if b.strip()]
        file_tag = re.sub(r"[^a-zA-Z0-9]+", "_", os.path.splitext(filename)[0]).strip("_").lower()
        if not file_tag:
            file_tag = "student_input"

        for idx, block in enumerate(blocks, 1):
            # Prefix makes the content easier to retrieve for source/traceability queries.
            prefixed_block = f"Student-contributed data ({filename}):\n{block}"
            chunks.append((f"student_{file_tag}_{idx}", prefixed_block))

    return chunks


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"[!] {INPUT_FILE} not found — nothing to process.")
        sys.exit(1)

    # ── Read raw chunks ─────────────────────────────────────────────────────
    raw_chunks: list[tuple[str, str]] = []
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "\t" in line:
                parts = line.split("\t", 1)
            else:
                parts = line.split(None, 1)
            if len(parts) == 2:
                raw_chunks.append((parts[0], parts[1]))
            else:
                raw_chunks.append(("unknown", line))

    # ── Merge chunk_14 tail into chunk_13 (Former People continuation) ────
    # chunk_14 contains the tail of College Chairpersons that was cut off
    chunk_13_idx = None
    chunk_14_idx = None
    for i, (cid, _) in enumerate(raw_chunks):
        if cid == "chunk_13":
            chunk_13_idx = i
        elif cid == "chunk_14":
            chunk_14_idx = i
    if chunk_13_idx is not None and chunk_14_idx is not None:
        c13_id, c13_text = raw_chunks[chunk_13_idx]
        c14_id, c14_text = raw_chunks[chunk_14_idx]
        # chunk_14 starts with ". A 2007 - 2008" which is the tail of MR. ABILASH T
        if c14_text.strip().startswith(". A "):
            raw_chunks[chunk_13_idx] = (c13_id, c13_text + c14_text)
            raw_chunks[chunk_14_idx] = (c14_id, "")  # empty it, will be skipped

    student_chunks = _load_student_input_chunks(STUDENT_INPUT_DIR)
    if student_chunks:
        raw_chunks.extend(student_chunks)

    print(f"[1/4] Loaded {len(raw_chunks)} raw chunks from {INPUT_FILE}")
    if student_chunks:
        print(f"      + Included {len(student_chunks)} extra chunks from {STUDENT_INPUT_DIR}")

    # ── Clean ───────────────────────────────────────────────────────────────
    cleaned: list[tuple[str, str]] = []
    skipped = 0
    for cid, content in raw_chunks:
        c = clean_text(content)
        if len(c) < 30:
            skipped += 1
            continue
        cleaned.append((cid, c))

    print(f"[2/4] Cleaned text — kept {len(cleaned)} chunks, skipped {skipped} near-empty")

    # ── Categorise + re-chunk ───────────────────────────────────────────────
    final_docs: list[dict] = []
    rechunked_count = 0

    for cid, text in cleaned:
        # ── Special handling for "Former People" chunk ──────────────────────
        if cid == "chunk_13" and "Former People" in text:
            structured = _parse_former_people(text)
            if structured:
                final_docs.extend(structured)
                rechunked_count += 1
                continue

        categories = detect_categories(text)
        cat_prefix = f"[{', '.join(categories)}] "

        if len(text) > SPLIT_THRESHOLD:
            sub_parts = _split_text(text)
            rechunked_count += 1
            for i, part in enumerate(sub_parts):
                aliased = inject_search_aliases(part)
                final_docs.append({
                    "id": f"{cid}_p{i}",
                    "parent_chunk": cid,
                    "categories": categories,
                    "content": cat_prefix + aliased,
                })
        else:
            aliased = inject_search_aliases(text)
            final_docs.append({
                "id": cid,
                "parent_chunk": cid,
                "categories": categories,
                "content": cat_prefix + aliased,
            })

    print(f"[3/4] Categorised & re-chunked — {len(final_docs)} final chunks "
          f"({rechunked_count} large chunks were split)")

    # ── Write output ────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for doc in final_docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    # ── Stats ───────────────────────────────────────────────────────────────
    lengths = [len(d["content"]) for d in final_docs]
    avg_len = sum(lengths) / len(lengths) if lengths else 0
    min_len = min(lengths) if lengths else 0
    max_len = max(lengths) if lengths else 0

    cat_counts: dict[str, int] = {}
    for d in final_docs:
        for c in d["categories"]:
            cat_counts[c] = cat_counts.get(c, 0) + 1

    print(f"[4/4] Wrote {len(final_docs)} chunks to {OUTPUT_FILE}")
    print()
    print("── Summary ──────────────────────────────────────────")
    print(f"  Chunks        : {len(final_docs)}")
    print(f"  Avg length    : {avg_len:.0f} chars")
    print(f"  Min / Max     : {min_len} / {max_len} chars")
    print(f"  Categories    :")
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"    {cat:<20s} {cnt:>4d}")
    print("─────────────────────────────────────────────────────")
    print(f"\nDone!  Now run:  python main.py")


if __name__ == "__main__":
    main()
