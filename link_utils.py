"""
link_utils.py — URL extraction and link-fallback helpers shared by the CLI and the API.

These helpers previously existed as seven near-identical copies in main.py and
api/routes/chat.py, which had already begun to drift (_extract_urls defaulted to a
limit of 8 in one and 6 in the other). Both now import from here.

The important behaviour lives in has_useful_link(): the answer-post-processing step
only appends fallback links when the model failed to produce one, and it used to test
that with a bare URL regex. The model routinely ends an answer with the generic
homepage ("please visit https://sahrdaya.ac.in/"), which satisfied the regex and
suppressed the fallback — so questions asking for a specific PDF got the homepage and
a claim that no link existed, while the real PDF sat unused in the retrieved context.
A homepage is not an answer to "give me the placement report", so it does not count.
"""

import re
from urllib.parse import urlparse

URL_PATTERN = re.compile(r"https?://[^\s)\]\}>\"']+")

_STATIC_ASSET_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico", ".css", ".js",
)

_DOCUMENT_SUFFIXES = (".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")

LINK_KEYWORDS = [
    "link", "links", "url", "download", "pdf", "document", "docs",
    "placement", "placements", "stats", "statistics", "report", "handbook",
    "regulation", "syllabus", "approval", "audit",
]


def is_static_asset(url: str) -> bool:
    return url.lower().split("?")[0].endswith(_STATIC_ASSET_SUFFIXES)


def url_priority(url: str) -> int:
    """Rank documents ahead of ordinary pages; PDFs and stored files first."""
    low = url.lower()
    if ".pdf" in low or "alt=media" in low:
        return 0
    if any(ext in low for ext in _DOCUMENT_SUFFIXES):
        return 1
    return 2


def clean_url(url: str) -> str:
    """Trim trailing punctuation and markdown emphasis.

    Models emit bold links as **https://site/**, and the trailing asterisks would
    otherwise read as a URL path — making a bare homepage look like a specific page.
    """
    return (url or "").rstrip("*`\"'.,;:)")


def is_homepage_url(url: str) -> bool:
    """True for a bare site root such as https://sahrdaya.ac.in/ — no specific target."""
    try:
        parsed = urlparse(clean_url(url))
    except ValueError:
        return False
    path = (parsed.path or "").strip("/")
    return not path and not parsed.query and not parsed.fragment


def extract_urls(text: str, limit: int = 6) -> list[str]:
    """Unique, non-asset URLs from text, documents first."""
    seen: set[str] = set()
    candidates: list[str] = []
    for raw in URL_PATTERN.findall(text or ""):
        url = clean_url(raw)
        if not url or url in seen or is_static_asset(url):
            continue
        seen.add(url)
        candidates.append(url)

    return sorted(candidates, key=url_priority)[:limit]


def has_useful_link(text: str) -> bool:
    """Does this text already point somewhere specific?

    A bare homepage does not count: it is what the model emits when it has failed to
    find the document, and treating it as success is what suppressed the fallback.
    """
    for raw in URL_PATTERN.findall(text or ""):
        url = clean_url(raw)
        if url and not is_homepage_url(url) and not is_static_asset(url):
            return True
    return False


def query_likely_needs_links(query: str) -> bool:
    q = (query or "").lower()
    return any(keyword in q for keyword in LINK_KEYWORDS)


def format_fallback_links(query: str, urls: list[str]) -> str:
    """Render fallback links, grouping placement reports by academic year."""
    q = (query or "").lower()
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    if "placement" not in q:
        return "Direct links from context:\n" + "\n".join(f"- {u}" for u in unique)

    year_links: list[tuple[str, str]] = []
    extra_links: list[str] = []
    for url in unique:
        low = url.lower()
        match = re.search(r"tpo%2fplacement%2fsah%2f(\d{4}-\d{2})", low) or \
            re.search(r"/tpo/placement/sah/(\d{4}-\d{2})", low)
        if match:
            year_links.append((match.group(1), url))
        else:
            extra_links.append(url)

    def _year_start(label: str) -> int:
        try:
            return int(label.split("-")[0])
        except (ValueError, IndexError):
            return 9999

    year_links.sort(key=lambda pair: _year_start(pair[0]))

    lines = ["Verified placement report links (year-wise):"]
    lines.extend(f"- {year}: {url}" for year, url in year_links)
    lines.extend(f"- {url}" for url in extra_links)
    return "\n".join(lines)


def harmonize_response_with_links(response: str, links_appended: bool) -> str:
    """Remove contradictory 'no direct URL' claims when links are present below."""
    if not links_appended:
        return response
    text = response or ""
    replacement_map = [
        (r"\*?No\s+direct\s+(?:URL|URLs|link|links?)\s+(?:was|were|is|are)\s+(?:present|provided)\s+in\s+the\s+context\.?\*?", "-"),
        (r"\*No URL provided in the context\*", "-"),
        (r"\*no direct urls? (?:are|were) (?:present|provided) in the context\*", "-"),
        (r"No direct URL \(if any\)\s*[:\-]?\s*No[^\n|.]*", "-"),
        # Any sentence denying that a link exists, whatever it calls the source
        # ("the provided context", "the provided information", "the conversation
        # context"). Links are appended right below, so the claim is false.
        # Any line denying that a link exists, whatever it calls the source ("the
        # provided context", "the provided information", "the conversation context")
        # and however it ends (". " or ", so visit the home page:"). Real links are
        # appended directly below, so the whole line is false and goes.
        (r"(?m)^.*does\s+not\s+(?:contain|include|provide)\s+(?:a\s+|any\s+)?direct\s+(?:url|urls|link|links|download\s+link)[^\n]*$", ""),
        (r"(?m)^.*(?:I['’]m\s+sorry|Unfortunately)[^\n]*\bno\s+(?:direct\s+)?(?:url|link)[^\n]*$", ""),
        (r"-\s*\*\*Download links\*\*[^\n]*", ""),
        (r"\*\*Download links\*\*[^\n]*", ""),
        (r"the context mentions[^\n]*actual download links?[^\n]*\.", ""),
    ]
    for pattern, replacement in replacement_map:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
