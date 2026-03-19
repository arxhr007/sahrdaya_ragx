#!/usr/bin/env python3
"""
Multi-threaded Web Scraper with Sitemap Support

Usage:
    # Standard crawl (auto-detects sahrdaya.ac.in and uses sitemap)
    python scraper.py https://www.sahrdaya.ac.in/ -o output --threads 8 --use-playwright
    
    # Force sitemap mode
    python scraper.py https://www.sahrdaya.ac.in/ -o output --sitemap --threads 8
    
    # Discovery mode for other sites
    python scraper.py https://example.com -o site_output --threads 4
    
    # Single-threaded (legacy)
    python scraper.py https://example.com -o site_output --threads 1
    
    # Scrape a single page and APPEND to existing output files
    python scraper.py https://www.sahrdaya.ac.in/authors -o sahrdaya --single
    python scraper.py https://www.sahrdaya.ac.in/faculty -o sahrdaya --single --use-playwright

Options:
    --threads, -t   Number of concurrent threads (default: 4)
    --sitemap       Use predefined sitemap (auto-enabled for sahrdaya.ac.in)
    --single        Scrape only the given URL (no crawling) and append to existing outputs
    --use-playwright Use Playwright for JS-heavy sites
    --max-pages     Maximum pages to scrape
    --delay         Delay between requests in seconds

Outputs:
    <prefix>_raw.txt         -> combined raw text per page
    <prefix>_structured.json -> structured JSON (Groq or local fallback)
    <prefix>_rag.txt         -> chunked RAG-ready file: <id>\\t<text>
    <prefix>_tracking.json   -> URL tracking with hashes and chunk mappings
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import threading
from datetime import datetime
from typing import Dict, List, Set, Optional
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty

import requests
from bs4 import BeautifulSoup

# ------------------ GROQ API KEY (Environment) ------------------
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
# ---------------------------------------------------------------

if not GROQ_API_KEY or GROQ_API_KEY.strip() == "":
    print("\n⚠️  WARNING: Groq API Key is missing!")
    print("   → Groq-based JSON structuring will be skipped (fallback to local structuring)")
    print("   → Get your API key from: https://console.groq.com/keys")
    print("   → Set env var GROQ_API_KEY (or in .env) before running for model-based structuring\n")

# Optional Playwright support (only if installed)
try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except Exception:
    _HAS_PLAYWRIGHT = False

# Optional Groq client import (only if installed)
try:
    from groq import Groq
    _HAS_GROQ = True
except Exception:
    Groq = None
    _HAS_GROQ = False

# ---------------- Configuration (tweak if needed) ----------------
MAX_PAGES = 1000           # safety cap on number of pages to crawl
REQUEST_DELAY = .5       # seconds delay between requests (politeness)
USER_AGENT = "Mozilla/5.0 (compatible; site-crawler/1.0; +https://example.com)"
CHUNK_CHAR_SIZE = 1800    # characters per chunk for RAG
MAX_CHUNKS_FOR_MODEL = 12 # how many chunks to send to Groq for structuring
NUM_THREADS = 4           # default number of concurrent threads
# -----------------------------------------------------------------

DOCUMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".odt", ".rtf", ".txt", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"
)

ASSET_SKIP_EXTENSIONS = (
    ".css", ".js", ".ico", ".woff", ".woff2", ".ttf", ".eot",
    ".mp4", ".webm", ".mp3", ".wav", ".zip", ".rar", ".7z"
)

# ---------------- Sahrdaya.ac.in Known Sitemap ----------------
# Dynamic departments for URL expansion
SAHRDAYA_DEPARTMENTS = ['cse', 'ece', 'eee', 'ce', 'bme', 'bte', 'ash']

# Base routes (will be prefixed with base URL)
SAHRDAYA_ROUTES = [
    '/',
    '/about', '/about/about-us', '/about/administrators', '/about/amenities', '/about/annual-report',
    '/about/approval-letters', '/about/audited-statements', '/about/college-handbook', '/about/downloads',
    '/about/former-people', '/about/governing-body', '/about/ktu-regulation', '/about/legacy',
    '/about/magazine', '/about/management', '/about/mandatory-disclosure', '/about/newsletter',
    '/about/policies', '/about/policies/hrpolicy', '/about/professor-of-practice', '/about/profile',
    '/about/ptwa', '/about/scholarships', '/about/sister-concerns', '/about/vision-2030', '/about/vision-mission',
    '/admission', '/admission/application', '/admission/application/btech', '/admission/application/btech-let',
    '/admission/application/mtech', '/admission/application/pg-diploma', '/admission/portal', '/admission/select-course',
    '/announcements', '/authors', '/autonomous', '/autonomous/academic-calendar', '/autonomous/academic-council',
    '/autonomous/board-of-studies', '/autonomous/curriculum', '/autonomous/finance-committee',
    '/autonomous/governing-body', '/autonomous/programmes', '/autonomous/regulations',
    '/career', '/clubs', '/clubs/iedc', '/clubs/iedc/activities', '/clubs/iedc/constitution',
    '/clubs/iedc/duties-responsibility', '/clubs/iedc/meeting-minutes', '/clubs/iedc/reports',
    '/clubs/nss', '/clubs/nss/activities', '/clubs/nss/constitution', '/clubs/nss/duties-responsibility',
    '/clubs/nss/meeting-minutes', '/clubs/nss/reports', '/clubs/pals', '/clubs/pals/activities',
    '/clubs/pals/constitution', '/clubs/pals/duties-responsibility', '/clubs/pals/meeting-minutes',
    '/committees', '/ctpaiml', '/cu', '/cu/activities', '/cu/constitution', '/cu/duties-and-responsibilities',
    '/cu/meeting-minutes', '/department/about', '/department/curriculum', '/department/facultyandstaff',
    '/department/programmes',
    '/drugs', '/drugs/activities', '/drugs/constitution', '/drugs/duties-and-responsibilities', '/drugs/meeting-minutes',
    '/eca', '/eca/activities', '/eca/constitution', '/eca/duties-and-responsibilities', '/eca/meeting-minutes',
    '/equity', '/equity/constitution', '/equity/duties-responsibilities', '/equity/meeting-minutes',
    '/examination', '/examination/condonation-promotion', '/examination/conduct', '/examination/examination-calender',
    '/examination/execom', '/examination/fee', '/examination/malpractices', '/examination/results',
    '/examination/revaluation', '/examination/schedule', '/examination/system',
    '/faculty', '/faculty/leadership',
    '/faculty-grievance', '/faculty-grievance/constitution', '/faculty-grievance/duties-and-responsibilities',
    '/faculty-grievance/grievance-form', '/faculty-grievance/meeting-minutes', '/faculty-grievance/regulations',
    '/hostel', '/HRpolicy',
    '/icc', '/icc/complaint-form', '/icc/constitution', '/icc/duties-and-responsibilities', '/icc/meeting-minutes',
    '/iedc', '/IIIC', '/IIIC/constitution', '/IIIC/duties-and-responsibilities', '/IIIC/meeting-minutes', '/IIIC/mous',
    '/internal-examination-cell', '/internal-examination-cell/constitution-members',
    '/internal-examination-cell/timetable-invigilation',
    '/iqac', '/iqac/about', '/iqac/accreditation', '/iqac/aqar-reports', '/iqac/audit-reports',
    '/iqac/best-practices', '/iqac/committee', '/iqac/feedback-action-reports', '/iqac/functions',
    '/iqac/internships', '/iqac/meeting-minutes', '/iqac/nirf', '/iqac/objectives',
    '/iqac/quality-policy-initiatives', '/iqac/sir-reports', '/iqac/strategies', '/iqac/vision-mission-goals',
    '/library', '/library/resources', '/ndli', '/ndli/about', '/ndli/appreciation-certificates',
    '/ndli/dashboard', '/ndli/documents', '/ndli/events', '/ndli/meeting-minutes', '/ndli/registration-certificates',
    '/news', '/nss', '/pals',
    '/pshysical-education', '/pshysical-education/activities', '/pshysical-education/constitution',
    '/pshysical-education/duties-and-responsibilities', '/pshysical-education/meeting-minutes',
    '/pshysical-education/statutes',
    '/ptwa', '/ptwa/activities', '/ptwa/constitution', '/ptwa/duties-and-responsibilities',
    '/ptwa/meeting-minutes', '/ptwa/reports',
    '/ragging', '/ragging/activities', '/ragging/constitution', '/ragging/duties-and-responsibilities',
    '/ragging/meeting-minutes',
    '/ragx', '/research',
    '/scholarships', '/scholarships/available-scholarships', '/scholarships/constitution',
    '/scholarships/duties-and-responsibilities', '/scholarships/winners',
    '/scst', '/scst/constitution', '/scst/duties-and-responsibilities', '/scst/meeting-minutes',
    '/sitemap',
    '/skill-enhancment', '/skill-enhancment/activities', '/skill-enhancment/constitution',
    '/skill-enhancment/duties-and-responsibilities', '/skill-enhancment/meeting-minutes',
    '/student-grievence', '/student-grievence/constitution', '/student-grievence/duties-and-responsibilities',
    '/student-grievence/grievance-form', '/student-grievence/meeting-minutes', '/student-grievence/regulations',
    '/technical-staff', '/traning-and-placement',
]

# Department-specific sub-pages (will be expanded for each department)
SAHRDAYA_DEPT_SUBPAGES = [
    '/about/overview', '/about/vision-mission', '/hod-message/message', '/programmes/programmes',
    '/outcomes/outcomes', '/outcomes/objectives', '/outcomes/specific-outcomes', '/curriculum/curriculum',
    '/newsletter/newsletter', '/people/students', '/people/advisory', '/people/dqac', '/people/board',
    '/faculty-staff/faculty', '/faculty-staff/staff', '/facilities/labs', '/achievements/projects',
    '/achievements/faculty-achievements', '/achievements/student-achievements', '/achievements/mous',
    '/achievements/publications', '/achievements/alumni', '/association/association', '/association/events',
    '/innovative-tlm/innovative-tlm', '/placements/statistics', '/placements/partners', '/placements/alumni',
    '/activities/fdps', '/activities/workshops', '/activities/certifications', '/internships/short-term',
]

def get_sahrdaya_urls(base_url: str = "https://www.sahrdaya.ac.in") -> List[str]:
    """Generate full list of Sahrdaya URLs from known sitemap."""
    urls = []
    
    # Add base routes
    for route in SAHRDAYA_ROUTES:
        urls.append(f"{base_url}{route}")
    
    # Add department pages
    for dept in SAHRDAYA_DEPARTMENTS:
        urls.append(f"{base_url}/department/{dept}")
        # Add department sub-pages
        for subpage in SAHRDAYA_DEPT_SUBPAGES:
            urls.append(f"{base_url}/department/{dept}{subpage}")
    
    return urls
# -----------------------------------------------------------------

# ---------------- Thread-Safe Data Structures ----------------
class ThreadSafeSet:
    """Thread-safe set for tracking visited URLs."""
    def __init__(self):
        self._set: Set[str] = set()
        self._lock = threading.Lock()
    
    def add(self, item: str) -> bool:
        """Add item, returns True if added (was not present)."""
        with self._lock:
            if item in self._set:
                return False
            self._set.add(item)
            return True
    
    def __contains__(self, item: str) -> bool:
        with self._lock:
            return item in self._set
    
    def __len__(self) -> int:
        with self._lock:
            return len(self._set)


class ThreadSafeList:
    """Thread-safe list for collecting pages."""
    def __init__(self):
        self._list: List[Dict] = []
        self._lock = threading.Lock()
    
    def append(self, item: Dict):
        with self._lock:
            self._list.append(item)
    
    def __iter__(self):
        with self._lock:
            return iter(list(self._list))
    
    def __len__(self) -> int:
        with self._lock:
            return len(self._list)
    
    def to_list(self) -> List[Dict]:
        with self._lock:
            return list(self._list)


class RateLimiter:
    """Thread-safe rate limiter for polite crawling."""
    def __init__(self, delay: float):
        self._delay = delay
        self._lock = threading.Lock()
        self._last_request = 0.0
    
    def wait(self):
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request
            if elapsed < self._delay:
                time.sleep(self._delay - elapsed)
            self._last_request = time.time()


class ThreadSafeCounter:
    """Thread-safe counter for progress tracking."""
    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()
    
    def increment(self) -> int:
        with self._lock:
            self._count += 1
            return self._count
    
    @property
    def value(self) -> int:
        with self._lock:
            return self._count
# -----------------------------------------------------------------

# Global state
visited: Set[str] = set()
collected_pages: List[Dict] = []
url_tracking: Dict[str, Dict] = {}  # Track URL -> {hash, chunks, metadata}
rate_limiter: Optional[RateLimiter] = None
progress_counter: Optional[ThreadSafeCounter] = None

# ---------------- Helpers: Hashing ----------------
def compute_content_hash(text: str) -> str:
    """Compute SHA256 hash of content for change detection."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

# ---------------- Helpers: fetching HTML ----------------
def fetch_with_requests(url: str, timeout: int = 15) -> str:
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def is_document_link(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(DOCUMENT_EXTENSIONS)


def _looks_like_document_url(url: str) -> bool:
    """Heuristic for document-like URLs, including storage links without file extension in path."""
    if not url:
        return False
    clean = url.strip()
    if not clean:
        return False

    path = urlparse(clean).path.lower()

    # Ignore static image/media assets commonly requested while opening modals.
    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico")):
        return False

    if is_document_link(clean):
        return True

    lower = clean.lower()
    if "firebasestorage.googleapis.com" in lower and (".pdf" in lower or "alt=media" in lower):
        return True
    if "/download" in lower and ("pdf" in lower or "report" in lower or "document" in lower):
        return True
    return False


def _extract_urls_from_js_snippet(snippet: str) -> List[str]:
    if not snippet:
        return []
    return re.findall(r"https?://[^\s\"'<>]+", snippet)


def _collect_document_urls_from_page(page, base_url: str) -> Set[str]:
    """Collect document-like URLs from anchors and common data attributes."""
    found: Set[str] = set()

    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href') || '')")
        for href in hrefs or []:
            full = urljoin(base_url, (href or "").strip())
            if _looks_like_document_url(full):
                found.add(full)
    except Exception:
        pass

    try:
        values = page.eval_on_selector_all(
            "[data-url], [data-href], [onclick], [href]",
            """
            els => els.flatMap(e => [
                e.getAttribute('data-url') || '',
                e.getAttribute('data-href') || '',
                e.getAttribute('href') || '',
                e.getAttribute('onclick') || ''
            ])
            """,
        )
        for raw in values or []:
            raw = (raw or "").strip()
            if not raw:
                continue
            if raw.lower().startswith("javascript:"):
                for u in _extract_urls_from_js_snippet(raw):
                    if _looks_like_document_url(u):
                        found.add(u)
                continue

            if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/"):
                full = urljoin(base_url, raw)
                if _looks_like_document_url(full):
                    found.add(full)
            else:
                for u in _extract_urls_from_js_snippet(raw):
                    if _looks_like_document_url(u):
                        found.add(u)
    except Exception:
        pass

    return found


def _click_modal_triggers_and_capture(page, url: str) -> Set[str]:
    """Open likely modals/popups and capture hidden document links from actions inside."""
    captured: Set[str] = set()

    def _remember(u: str):
        clean = (u or "").split("#")[0].rstrip("/")
        if _looks_like_document_url(clean):
            captured.add(clean)

    # Observe network requests triggered by popup actions (download/open external).
    def _on_request(req):
        _remember(req.url)

    try:
        page.on("request", _on_request)
    except Exception:
        pass

    # Initial pass from visible DOM.
    for u in _collect_document_urls_from_page(page, url):
        _remember(u)

    trigger_selectors = [
        "button:has-text('Stats')",
        "a:has-text('Stats')",
        "button:has-text('View Statistics')",
        "a:has-text('View Statistics')",
        "button:has-text('View')",
        "a:has-text('View')",
    ]
    action_selectors = [
        "button:has-text('Download')",
        "a:has-text('Download')",
        "button:has-text('Open External')",
        "a:has-text('Open External')",
        "button:has-text('Open')",
        "a:has-text('Open')",
        "a:has-text('PDF')",
        "button:has-text('PDF')",
    ]
    close_selectors = [
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "button:has-text('Close')",
        "button:has-text('×')",
        "button:has-text('X')",
    ]

    max_trigger_clicks = 40
    clicked = 0

    try:
        for sel in trigger_selectors:
            loc = page.locator(sel)
            count = min(loc.count(), 15)
            for i in range(count):
                if clicked >= max_trigger_clicks:
                    break
                try:
                    el = loc.nth(i)
                    if not el.is_visible():
                        continue
                    el.click(timeout=2500)
                    clicked += 1
                    time.sleep(0.35)

                    # Collect links after modal opens.
                    for u in _collect_document_urls_from_page(page, url):
                        _remember(u)

                    # Click modal actions that reveal direct URLs.
                    for action_sel in action_selectors:
                        action_loc = page.locator(action_sel)
                        action_count = min(action_loc.count(), 8)
                        for j in range(action_count):
                            try:
                                act = action_loc.nth(j)
                                if not act.is_visible():
                                    continue
                                href = act.get_attribute("href") or ""
                                if href:
                                    _remember(urljoin(url, href))
                                act.click(timeout=2000)
                                time.sleep(0.2)
                            except Exception:
                                continue

                    # Collect again after actions.
                    for u in _collect_document_urls_from_page(page, url):
                        _remember(u)

                    # Try to close the modal before moving to next row.
                    closed = False
                    for close_sel in close_selectors:
                        close_loc = page.locator(close_sel)
                        if close_loc.count() > 0:
                            try:
                                close_loc.first.click(timeout=1000)
                                closed = True
                                time.sleep(0.2)
                                break
                            except Exception:
                                continue
                    if not closed:
                        try:
                            page.keyboard.press("Escape")
                            time.sleep(0.15)
                        except Exception:
                            pass
                except Exception:
                    continue
            if clicked >= max_trigger_clicks:
                break
    except Exception:
        pass

    return captured


def _inject_synthetic_doc_links(html: str, urls: Set[str]) -> str:
    """Append discovered links to HTML so downstream soup extraction sees them."""
    if not urls:
        return html
    anchors = "\n".join(
        f'<li><a href="{u}">Document Link {i+1}</a></li>'
        for i, u in enumerate(sorted(urls))
    )
    injected = (
        "<div id=\"scraper-discovered-doc-links\">"
        "<h2>Document Links</h2><ul>"
        f"{anchors}</ul></div>"
    )
    if "</body>" in html:
        return html.replace("</body>", injected + "</body>")
    return html + injected

def fetch_with_playwright(url: str, timeout: int = 60) -> str:
    if not _HAS_PLAYWRIGHT:
        raise RuntimeError("Playwright not installed. Install with `pip install playwright` and run `playwright install`.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        
        # Use 'load' instead of 'networkidle' for better compatibility with Firestore sites
        try:
            page.goto(url, timeout=timeout*1000, wait_until="load")
        except Exception as e:
            # If load fails, try with domcontentloaded
            try:
                page.goto(url, timeout=timeout*1000, wait_until="domcontentloaded")
            except Exception:
                raise e
        
        # Wait for page to be somewhat settled
        time.sleep(2)
        
        # Scroll to bottom to trigger lazy loading
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(1)
        except Exception:
            pass
        
        # Try to wait for network idle but don't fail if it times out
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            print(f"[*] Networkidle timeout for {url}, continuing anyway...")
        
        # Additional wait for any remaining async operations
        time.sleep(2)

        # Capture hidden links shown only via popups/modals (e.g., Stats -> Download/Open External).
        discovered_doc_urls = _click_modal_triggers_and_capture(page, url)

        html = page.content()
        html = _inject_synthetic_doc_links(html, discovered_doc_urls)
        browser.close()
        return html

# ---------------- Helpers: parsing & link extraction ----------------
def clean_text_from_soup(soup: BeautifulSoup) -> str:
    # Remove scripts/styles/iframes
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas"]):
        tag.decompose()
    # Prefer <main> or <article> if present
    main = soup.find("main") or soup.find("article")
    source = main if main else soup.body if soup.body else soup
    text = source.get_text(separator="\n", strip=True)
    # collapse multiple newlines and trim lines
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)

def extract_links_and_buttons(soup: BeautifulSoup, base_url: str) -> Set[str]:
    links = set()

    # anchors
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not href:
            continue
        # ignore javascript: anchors
        if href.strip().lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        links.add(full)

    # common patterns in button onclicks: location.href='...', location='...', window.location="..."
    onclick_pattern = re.compile(r"""(?:location|window\.location|location\.href)\s*=\s*['"]([^'"]+)['"]""", re.I)

    for btn in soup.find_all(["button", "input"]):
        onclick = btn.get("onclick", "") or btn.get("data-href", "")
        if onclick:
            m = onclick_pattern.search(onclick)
            if m:
                full = urljoin(base_url, m.group(1))
                links.add(full)

    # also try <form action=""> with method GET (some pages use forms for navigation)
    for form in soup.find_all("form", action=True):
        action = form.get("action")
        method = form.get("method", "").lower()
        if action and (method in ("get", "")):
            full = urljoin(base_url, action)
            links.add(full)

    return links


def _nearest_heading_text(tag) -> str:
    """Best-effort section heading near a link/button for context labeling."""
    try:
        # First try heading in the same section/container.
        container = tag
        for _ in range(4):
            if not container:
                break
            if getattr(container, "name", None) in ("section", "article", "div", "main"):
                h = container.find(["h1", "h2", "h3", "h4", "h5", "h6"])
                if h:
                    txt = h.get_text(" ", strip=True)
                    if txt:
                        return txt
            container = container.parent

        # Fallback: nearest previous heading in document order.
        prev_h = tag.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
        if prev_h:
            txt = prev_h.get_text(" ", strip=True)
            if txt:
                return txt
    except Exception:
        pass
    return ""


def extract_document_references(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """Extract document links (pdf/doc/etc.) plus a short purpose label."""
    refs_by_url: Dict[str, Dict[str, str]] = {}

    def add_ref(url: str, label: str, section: str = ""):
        clean = url.split("#")[0].rstrip("/")
        if not is_document_link(clean):
            return

        label = re.sub(r"\s+", " ", (label or "").strip())
        section = re.sub(r"\s+", " ", (section or "").strip())
        if not label:
            filename = os.path.basename(urlparse(clean).path)
            label = filename or "Document"

        purpose = f"{section} - {label}" if section and section.lower() not in label.lower() else label
        refs_by_url[clean] = {
            "url": clean,
            "label": label,
            "purpose": purpose,
        }

    # Anchor-based links (main source for downloadable PDFs/docs)
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        text = a.get_text(" ", strip=True)
        label = text or (a.get("title") or "") or (a.get("aria-label") or "")
        section = _nearest_heading_text(a)
        add_ref(full, label, section)

    # Some pages use button/data-href downloads.
    for btn in soup.find_all(["button", "input"]):
        cand = (btn.get("data-href") or "").strip()
        if not cand:
            onclick = (btn.get("onclick") or "")
            m = re.search(r"(?:location|window\.location|location\.href)\s*=\s*['\"]([^'\"]+)['\"]", onclick, re.I)
            if m:
                cand = m.group(1)
        if not cand:
            continue
        full = urljoin(base_url, cand)
        label = (btn.get_text(" ", strip=True) if hasattr(btn, "get_text") else "") or (btn.get("value") or "")
        section = _nearest_heading_text(btn)
        add_ref(full, label, section)

    return sorted(refs_by_url.values(), key=lambda r: (r.get("purpose", ""), r.get("url", "")))

def extract_meta(soup: BeautifulSoup) -> Dict:
    title = ""
    try:
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
    except Exception:
        title = ""

    def meta_content(name):
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.has_attr("content"):
            return tag["content"].strip()
        tag = soup.find("meta", attrs={"property": name})
        if tag and tag.has_attr("content"):
            return tag["content"].strip()
        return ""

    description = meta_content("description") or meta_content("og:description")
    return {"title": title, "description": description}

# ---------------- Chunking ----------------
def chunk_text(text: str, max_chars: int = CHUNK_CHAR_SIZE, start_id: int = 0) -> List[Dict]:
    """Chunk text into smaller pieces. Returns list of chunk dicts with id, text, char_count."""
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    cid = start_id
    current = ""
    for p in paragraphs:
        if len(current) + len(p) + 2 <= max_chars:
            current = current + ("\n\n" if current else "") + p
        else:
            if current:
                chunks.append({"id": f"chunk_{cid}", "text": current.strip(), "char_count": len(current)})
                cid += 1
            if len(p) > max_chars:
                # split long paragraph
                for i in range(0, len(p), max_chars):
                    part = p[i:i+max_chars]
                    chunks.append({"id": f"chunk_{cid}", "text": part.strip(), "char_count": len(part)})
                    cid += 1
                current = ""
            else:
                current = p
    if current:
        chunks.append({"id": f"chunk_{cid}", "text": current.strip(), "char_count": len(current)})
    return chunks


def chunk_pages_with_tracking(pages: List[Dict]) -> tuple[List[Dict], Dict[str, Dict]]:
    """
    Chunk all pages and create tracking info mapping URLs to their chunks.
    Returns: (all_chunks, url_tracking_dict)
    """
    all_chunks = []
    tracking = {}
    current_chunk_id = 0
    
    for page in pages:
        url = page.get("url", "")
        text = page.get("text", "")
        content_hash = compute_content_hash(text)
        
        # Chunk this page's content
        page_chunks = chunk_text(text, start_id=current_chunk_id)
        
        # Track chunk IDs for this URL
        chunk_ids = [c["id"] for c in page_chunks]
        chunk_range = {
            "start": current_chunk_id,
            "end": current_chunk_id + len(page_chunks) - 1 if page_chunks else current_chunk_id,
            "count": len(page_chunks)
        }
        
        # Build tracking entry
        tracking[url] = {
            "url": url,
            "title": page.get("title", ""),
            "description": page.get("description", ""),
            "document_links": page.get("document_links", []),
            "content_hash": content_hash,
            "chunk_ids": chunk_ids,
            "chunk_range": chunk_range,
            "word_count": len(text.split()),
            "char_count": len(text),
            "scraped_at": datetime.now().isoformat()
        }
        
        all_chunks.extend(page_chunks)
        current_chunk_id += len(page_chunks)
    
    return all_chunks, tracking

# ---------------- Groq structuring ----------------
def structure_with_groq(pages: List[Dict], all_chunks: List[Dict]) -> Dict:
    """Send up to the first MAX_CHUNKS_FOR_MODEL chunks to Groq to produce a structured JSON.
       If Groq client is not available or the call fails, return a local-constructed structured object.
    """
    combined_text = "\n\n".join(p["text"] for p in pages)

    structured = {
        "title": pages[0].get("title") if pages else None,
        "url": pages[0].get("url") if pages else None,
        "description": pages[0].get("description") if pages else None,
        "page_count": len(pages),
        "word_count": len(combined_text.split()),
        "chunks": all_chunks,
        "top_entities": []
    }

    if not _HAS_GROQ:
        # Groq not installed; return the local structured object
        return structured

    try:
        client = Groq(api_key=GROQ_API_KEY)
        # Build a minimal prompt / payload. We send metadata + several chunks for structuring.
        payload = {
            "pages": [{"url": p["url"], "title": p.get("title", ""), "description": p.get("description", "")} for p in pages[:8]],
            "chunks": [{"id": c["id"], "text": c["text"]} for c in all_chunks[:MAX_CHUNKS_FOR_MODEL]]
        }

        system_msg = (
            "You are a JSON-only assistant. Return VALID JSON (no surrounding text). "
            "Produce an object matching this shape: "
            "{title: string|null, url: string, description: string|null, headings: [string], chunks: [{id,text,char_count}], top_entities: [string], word_count: int}."
            "If a field is not available, use null or an empty list as appropriate."
        )

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]

        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0.0,
            max_completion_tokens=1500
        )

        # Parse response text robustly
        raw_text = None
        try:
            # try object-like access
            if hasattr(resp, "choices") and resp.choices:
                first = resp.choices[0]
                if getattr(first, "message", None) and getattr(first.message, "content", None):
                    raw_text = first.message.content
                elif getattr(first, "text", None):
                    raw_text = first.text
        except Exception:
            pass

        if raw_text is None:
            # try dict-style
            try:
                raw_text = resp["choices"][0]["message"]["content"]
            except Exception:
                raw_text = None

        if not raw_text:
            raise RuntimeError("Could not extract text from Groq response.")

        # Parse JSON returned by model
        parsed = json.loads(raw_text)
        # attach full chunk list computed locally (ensures full coverage)
        parsed.setdefault("chunks", all_chunks)
        parsed.setdefault("word_count", structured["word_count"])
        parsed.setdefault("page_count", structured["page_count"])
        structured = parsed
    except Exception as e:
        # If anything fails, return the fallback structured object
        print("[!] Groq structuring failed, falling back to local structure. Error:", e)
    return structured

# ---------------- robots.txt check ----------------
def is_allowed_by_robots(start_url: str) -> bool:
    """
    Basic robots.txt check using /robots.txt. If fetching/parsing robots fails, default to True (be cautious).
    """
    try:
        parsed = urlparse(start_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=8)
        if r.status_code != 200:
            return True  # no robots file -> assume allowed (still be polite)
        content = r.text
        # We will do a minimal check: if 'Disallow: /' appears at top-level, block
        # For thorough checks, use urllib.robotparser but keep simple here.
        # Use naive check: if Disallow: / exists and no Allow exceptions, block root.
        disallow_root = re.search(r"Disallow:\s*/\s*$", content, re.MULTILINE)
        if disallow_root:
            return False
    except Exception:
        # on any failure, return True but user should be cautious
        return True
    return True

# ---------------- Crawler ----------------
def fetch_single_page(url: str, use_playwright: bool = False) -> Optional[Dict]:
    """
    Fetch and parse a single page. Returns page dict or None on failure.
    Thread-safe - can be called from multiple threads.
    """
    normalized = url.rstrip("/#")

    # Skip direct document URLs — we keep document links from HTML pages,
    # and let the assistant return those links when users ask for PDFs/docs.
    if is_document_link(normalized):
        return None
    
    # Rate limiting
    if rate_limiter:
        rate_limiter.wait()
    
    html = ""
    try:
        if use_playwright and _HAS_PLAYWRIGHT:
            html = fetch_with_playwright(normalized)
        else:
            try:
                html = fetch_with_requests(normalized)
                if len(html) < 1500 and _HAS_PLAYWRIGHT and use_playwright:
                    html = fetch_with_playwright(normalized)
            except Exception as e_req:
                if _HAS_PLAYWRIGHT and use_playwright:
                    html = fetch_with_playwright(normalized)
                else:
                    return None
    except Exception as e:
        print(f"[!] Fetch failed for {normalized}: {e}")
        return None
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        meta = extract_meta(soup)
        text = clean_text_from_soup(soup)
        
        # Also extract links for discovery mode
        links = extract_links_and_buttons(soup, normalized)
        doc_refs = extract_document_references(soup, normalized)
        if doc_refs:
            lines = ["Document Links:"]
            for ref in doc_refs:
                lines.append(f"- {ref['purpose']}: {ref['url']}")
            text = text + "\n\n" + "\n".join(lines)
        
        return {
            "url": normalized,
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
            "text": text,
            "document_links": doc_refs,
            "links": list(links)
        }
    except Exception as e:
        print(f"[!] Parse error for {normalized}: {e}")
        return None


def crawl_sitemap_multithreaded(urls: List[str], base_domain: str, use_playwright: bool = False, num_threads: int = 4) -> List[Dict]:
    """
    Multi-threaded crawler starting from known sitemap URLs.
    Also discovers and follows new links found on each page (hybrid recursive).
    """
    global progress_counter
    progress_counter = ThreadSafeCounter()
    
    # Initialize queue with sitemap URLs
    url_queue = Queue()
    queued = ThreadSafeSet()
    
    for url in urls:
        normalized = url.rstrip("/#")
        if queued.add(normalized):
            url_queue.put(url)
    
    initial_count = len(queued)
    results = ThreadSafeList()
    
    print(f"[*] Starting hybrid crawl with {num_threads} threads...")
    print(f"[*] Initial sitemap URLs: {initial_count}")
    print(f"[*] Will also discover and follow new links found on each page...")
    
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = {}
        
        while True:
            # Submit new tasks from queue (up to 2x threads to keep pipeline full)
            while not url_queue.empty() and len(futures) < num_threads * 2 and len(queued) <= MAX_PAGES:
                try:
                    url = url_queue.get_nowait()
                    future = executor.submit(fetch_single_page, url, use_playwright)
                    futures[future] = url
                except Empty:
                    break
            
            if not futures:
                # No active work and queue empty - we're done
                break
            
            # Check for completed futures
            done = [f for f in futures if f.done()]
            
            if not done:
                time.sleep(0.05)
                continue
            
            for future in done:
                url = futures.pop(future)
                try:
                    result = future.result()
                    if result:
                        count = progress_counter.increment()
                        discovered_count = len(queued) - initial_count
                        print(f"[+] ({count}) Processed: {url} [Discovered: {discovered_count} new URLs]")
                        
                        # Extract links and add new ones to queue
                        links = result.pop("links", [])
                        for link in links:
                            # Normalize and filter
                            link = link.split("#")[0].rstrip("/")
                            parsed = urlparse(link)
                            
                            # Only follow same-domain links
                            if parsed.netloc == base_domain or parsed.netloc == "":
                                if parsed.netloc == "":
                                    link = urljoin(url, link)
                                
                                normalized_link = link.rstrip("/#")
                                
                                # Skip static assets/media and document files.
                                skip_extensions = ASSET_SKIP_EXTENSIONS + DOCUMENT_EXTENSIONS
                                if any(normalized_link.lower().endswith(ext) for ext in skip_extensions):
                                    continue
                                
                                # Add to queue if new and under limit
                                if queued.add(normalized_link) and len(queued) <= MAX_PAGES:
                                    url_queue.put(link)
                        
                        # Store result if it has content
                        if result.get("text") and len(result.get("text", "")) > 50:
                            results.append(result)
                            
                except Exception as e:
                    print(f"[!] Error processing {url}: {e}")
    
    total_discovered = len(queued) - initial_count
    print(f"\n[*] Hybrid crawl complete!")
    print(f"[*] Pages from sitemap: {initial_count}")
    print(f"[*] Additional pages discovered: {total_discovered}")
    print(f"[*] Total pages with content: {len(results)}")
    
    return results.to_list()


def crawl_with_discovery_multithreaded(start_url: str, base_domain: str, use_playwright: bool = False, num_threads: int = 4) -> List[Dict]:
    """
    Multi-threaded crawler with link discovery (recursive mode but parallel).
    Discovers new URLs while crawling.
    """
    global progress_counter
    progress_counter = ThreadSafeCounter()
    
    url_queue = Queue()
    url_queue.put(start_url)
    
    queued = ThreadSafeSet()
    queued.add(start_url.rstrip("/#"))
    
    results = ThreadSafeList()
    
    print(f"[*] Starting multi-threaded discovery crawl with {num_threads} threads...")
    
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = {}
        
        while True:
            # Submit new tasks from queue
            while not url_queue.empty() and len(futures) < num_threads * 2 and len(queued) <= MAX_PAGES:
                try:
                    url = url_queue.get_nowait()
                    future = executor.submit(fetch_single_page, url, use_playwright)
                    futures[future] = url
                except Empty:
                    break
            
            if not futures:
                break
            
            # Check for completed futures
            done = [f for f in futures if f.done()]
            
            if not done:
                time.sleep(0.05)
                continue
            
            for future in done:
                url = futures.pop(future)
                try:
                    result = future.result()
                    if result:
                        count = progress_counter.increment()
                        print(f"[+] ({count}/{MAX_PAGES}) Processed: {url}")
                        
                        # Extract and queue new links
                        links = result.pop("links", [])
                        for link in links:
                            parsed = urlparse(link)
                            if parsed.netloc == base_domain:
                                normalized = link.split("#")[0].rstrip("/")
                                if any(normalized.lower().endswith(ext) for ext in (ASSET_SKIP_EXTENSIONS + DOCUMENT_EXTENSIONS)):
                                    continue
                                if queued.add(normalized) and len(queued) <= MAX_PAGES:
                                    url_queue.put(link)
                        
                        if result.get("text"):
                            results.append(result)
                except Exception as e:
                    print(f"[!] Error: {e}")
    
    return results.to_list()


def crawl_page(url: str, base_domain: str, use_playwright: bool = False):
    """Single-threaded recursive crawler (legacy, for --threads 1)."""
    normalized = url.rstrip("/#")
    if normalized in visited:
        return
    if len(visited) >= MAX_PAGES:
        return
    if urlparse(normalized).netloc != base_domain:
        return

    print(f"[+] Visiting ({len(visited)+1}/{MAX_PAGES}): {normalized}")
    visited.add(normalized)

    html = ""
    try:
        if use_playwright and _HAS_PLAYWRIGHT:
            html = fetch_with_playwright(normalized)
        else:
            try:
                html = fetch_with_requests(normalized)
                if len(html) < 1500 and _HAS_PLAYWRIGHT and use_playwright:
                    html = fetch_with_playwright(normalized)
            except Exception as e_req:
                if _HAS_PLAYWRIGHT and use_playwright:
                    html = fetch_with_playwright(normalized)
                else:
                    print(f"[!] Failed to fetch {normalized} with requests: {e_req}")
                    return
    except Exception as e:
        print(f"[!] Fetch failed for {normalized}: {e}")
        return

    try:
        soup = BeautifulSoup(html, "html.parser")
        meta = extract_meta(soup)
        text = clean_text_from_soup(soup)
        links = extract_links_and_buttons(soup, normalized)

        collected_pages.append({
            "url": normalized,
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
            "text": text
        })

        time.sleep(REQUEST_DELAY)

        for link in links:
            parsed = urlparse(link)
            if not parsed.scheme:
                link = urljoin(normalized, link)
                parsed = urlparse(link)
            if parsed.netloc == base_domain:
                next_link = link.split("#")[0].rstrip("/")
                if any(next_link.lower().endswith(ext) for ext in (ASSET_SKIP_EXTENSIONS + DOCUMENT_EXTENSIONS)):
                    continue
                if next_link not in visited and len(visited) < MAX_PAGES:
                    crawl_page(next_link, base_domain, use_playwright=use_playwright)
    except Exception as e:
        print(f"[!] Parse or recurse error for {normalized}: {e}")

# ---------------- Single Page Scrape & Append ----------------
def scrape_single_page(url: str, output_prefix: str, use_playwright: bool = False):
    """
    Scrape a single page (no link following) and APPEND its content
    to existing raw, RAG, structured, and tracking output files.

    Usage:
        python scraper.py https://www.sahrdaya.ac.in/authors -o sahrdaya --single
    """
    print(f"[*] Scraping single page: {url}")

    # Fetch HTML page through the shared extraction path.
    page = fetch_single_page(url, use_playwright=use_playwright)
    if not page:
        print("[!] Failed to fetch/extract page. Note: direct document URLs are skipped.")
        return

    normalized = page["url"]
    text = page.get("text", "")

    if not text or len(text) < 30:
        print("[!] Page had no meaningful content. Aborting.")
        return

    print(f"[+] Fetched: {page['title'] or normalized}  ({len(text)} chars)")

    # --- Determine next chunk_id from existing data ---
    tracking_path = f"{output_prefix}_tracking.json"
    existing_tracking = {}
    next_chunk_id = 0
    if os.path.exists(tracking_path):
        with open(tracking_path, "r", encoding="utf-8") as f:
            existing_tracking = json.load(f)
        # Find the highest existing chunk id
        for cid in existing_tracking.get("chunk_index", {}).keys():
            num = int(cid.replace("chunk_", ""))
            if num >= next_chunk_id:
                next_chunk_id = num + 1

    # --- Chunk the new page ---
    new_chunks = chunk_text(text, start_id=next_chunk_id)
    content_hash = compute_content_hash(text)
    chunk_ids = [c["id"] for c in new_chunks]
    print(f"[*] Created {len(new_chunks)} new chunks (IDs {next_chunk_id}–{next_chunk_id + len(new_chunks) - 1})")

    # --- 1. Append to raw text file ---
    raw_path = f"{output_prefix}_raw.txt"
    with open(raw_path, "a", encoding="utf-8") as f:
        f.write(f"\n\n=== URL: {normalized} ===\n\n")
        if page.get("title"):
            f.write(f"Title: {page['title']}\n\n")
        if page.get("description"):
            f.write(f"Description: {page['description']}\n\n")
        f.write(text + "\n")
    print(f"[+] Appended raw text to: {raw_path}")

    # --- 2. Append to RAG file ---
    rag_path = f"{output_prefix}_rag.txt"
    with open(rag_path, "a", encoding="utf-8") as f:
        for c in new_chunks:
            one_line = " ".join(c["text"].splitlines())
            f.write(f"{c['id']}\t{one_line}\n")
    print(f"[+] Appended {len(new_chunks)} chunks to: {rag_path}")

    # --- 3. Update structured JSON ---
    structured_path = f"{output_prefix}_structured.json"
    if os.path.exists(structured_path):
        with open(structured_path, "r", encoding="utf-8") as f:
            structured = json.load(f)
    else:
        structured = {"title": None, "url": None, "page_count": 0,
                      "word_count": 0, "chunks": [], "top_entities": []}

    structured["chunks"].extend(new_chunks)
    structured["page_count"] = structured.get("page_count", 0) + 1
    structured["word_count"] = structured.get("word_count", 0) + len(text.split())
    with open(structured_path, "w", encoding="utf-8") as f:
        json.dump(structured, f, ensure_ascii=False, indent=2)
    print(f"[+] Updated structured JSON: {structured_path}")

    # --- 4. Update tracking JSON ---
    if not existing_tracking:
        existing_tracking = {"metadata": {}, "urls": {}, "chunk_index": {}}

    existing_tracking["urls"][normalized] = {
        "url": normalized,
        "title": page.get("title", ""),
        "description": page.get("description", ""),
        "document_links": page.get("document_links", []),
        "content_hash": content_hash,
        "chunk_ids": chunk_ids,
        "chunk_range": {
            "start": next_chunk_id,
            "end": next_chunk_id + len(new_chunks) - 1 if new_chunks else next_chunk_id,
            "count": len(new_chunks),
        },
        "word_count": len(text.split()),
        "char_count": len(text),
        "scraped_at": datetime.now().isoformat(),
    }
    for c in new_chunks:
        existing_tracking["chunk_index"][c["id"]] = {
            "char_count": c.get("char_count", len(c["text"])),
            "preview": c["text"][:100] + "..." if len(c["text"]) > 100 else c["text"],
        }
    # Update metadata totals
    meta_block = existing_tracking.get("metadata", {})
    meta_block["total_pages"] = len(existing_tracking["urls"])
    meta_block["total_chunks"] = len(existing_tracking["chunk_index"])
    meta_block["last_single_scrape"] = datetime.now().isoformat()
    existing_tracking["metadata"] = meta_block

    with open(tracking_path, "w", encoding="utf-8") as f:
        json.dump(existing_tracking, f, ensure_ascii=False, indent=2)
    print(f"[+] Updated tracking JSON: {tracking_path}")

    print(f"\n[*] Done. Single page '{normalized}' appended to all output files.")
# -----------------------------------------------------------------

# ---------------- Main ----------------
def main():
    global MAX_PAGES, REQUEST_DELAY, NUM_THREADS, rate_limiter, visited, collected_pages
    parser = argparse.ArgumentParser(description="Multi-threaded web scraper with sitemap support.")
    parser.add_argument("url", help="Starting URL (include http/https)")
    parser.add_argument("-o", "--output", default="site_output", help="Output filename prefix")
    parser.add_argument("--use-playwright", action="store_true", help="Use Playwright for JS-heavy sites")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES, help="Maximum pages to crawl")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Seconds between requests")
    parser.add_argument("--threads", "-t", type=int, default=NUM_THREADS, help="Number of threads (default: 4)")
    parser.add_argument("--sitemap", action="store_true", help="Use known sitemap for sahrdaya.ac.in (faster)")
    parser.add_argument("--single", action="store_true", help="Scrape only the given URL (no crawling) and append to existing output files")
    args = parser.parse_args()

    # Override global config
    MAX_PAGES = args.max_pages
    REQUEST_DELAY = args.delay
    NUM_THREADS = args.threads
    
    # Initialize rate limiter
    rate_limiter = RateLimiter(REQUEST_DELAY)
    visited = set()
    collected_pages = []

    start_url = args.url.strip()
    if not start_url.startswith("http"):
        print("Please provide a full URL including http or https.")
        sys.exit(1)

    # --- Single page mode: scrape & append, then exit ---
    if args.single:
        scrape_single_page(start_url, args.output, use_playwright=args.use_playwright)
        return

    domain = urlparse(start_url).netloc
    
    # Check if sitemap mode for sahrdaya
    use_sitemap = args.sitemap or "sahrdaya.ac.in" in domain
    
    print(f"Starting crawl at: {start_url}")
    print(f"Domain-locked to: {domain}")
    print(f"Max pages: {MAX_PAGES}, delay: {REQUEST_DELAY}s, threads: {NUM_THREADS}")
    if use_sitemap:
        print(f"[*] Using known sitemap for {domain}")
    if args.use_playwright and not _HAS_PLAYWRIGHT:
        print("[!] Playwright flag provided but Playwright is not installed.")
    
    # Start crawling
    if use_sitemap and "sahrdaya.ac.in" in domain:
        # Use predefined sitemap + discover additional URLs
        base_url = f"https://{domain}"
        urls = get_sahrdaya_urls(base_url)[:MAX_PAGES]
        print(f"[*] Sitemap contains {len(urls)} initial URLs")
        pages_list = crawl_sitemap_multithreaded(urls, domain, use_playwright=args.use_playwright, num_threads=NUM_THREADS)
    elif NUM_THREADS > 1:
        # Multi-threaded discovery crawl
        pages_list = crawl_with_discovery_multithreaded(start_url, domain, use_playwright=args.use_playwright, num_threads=NUM_THREADS)
    else:
        # Single-threaded legacy mode
        crawl_page(start_url, domain, use_playwright=args.use_playwright)
        pages_list = list(collected_pages)

    print(f"\n[*] Crawl complete. Pages collected: {len(pages_list)}")

    # Write combined raw text file
    raw_path = f"{args.output}_raw.txt"
    with open(raw_path, "w", encoding="utf-8") as f:
        for p in pages_list:
            f.write(f"\n\n=== URL: {p['url']} ===\n\n")
            if p.get("title"):
                f.write(f"Title: {p['title']}\n\n")
            if p.get("description"):
                f.write(f"Description: {p['description']}\n\n")
            f.write(p.get("text", "") + "\n")
    print(f"Raw text written to: {raw_path}")

    # Chunk pages and create tracking info
    all_chunks, url_tracking = chunk_pages_with_tracking(pages_list)
    print(f"[*] Created {len(all_chunks)} chunks from {len(pages_list)} pages")

    # Structure via Groq (or fallback)
    structured = structure_with_groq(pages_list, all_chunks)
    structured_path = f"{args.output}_structured.json"
    with open(structured_path, "w", encoding="utf-8") as f:
        json.dump(structured, f, ensure_ascii=False, indent=2)
    print(f"Structured JSON written to: {structured_path}")

    # Create RAG-ready file (one chunk per line)
    rag_path = f"{args.output}_rag.txt"
    with open(rag_path, "w", encoding="utf-8") as f:
        for c in structured.get("chunks", []):
            one_line = " ".join(c["text"].splitlines())
            f.write(f"{c.get('id', '')}\t{one_line}\n")
    print(f"RAG-ready chunks written to: {rag_path}")

    # Create URL tracking file
    tracking_path = f"{args.output}_tracking.json"
    tracking_data = {
        "metadata": {
            "scraped_at": datetime.now().isoformat(),
            "start_url": start_url,
            "domain": domain,
            "total_pages": len(pages_list),
            "total_chunks": len(all_chunks),
            "max_pages_limit": MAX_PAGES,
            "chunk_size": CHUNK_CHAR_SIZE,
            "threads_used": NUM_THREADS,
            "sitemap_mode": use_sitemap
        },
        "urls": url_tracking,
        "chunk_index": {
            c["id"]: {
                "char_count": c.get("char_count", len(c["text"])),
                "preview": c["text"][:100] + "..." if len(c["text"]) > 100 else c["text"]
            }
            for c in all_chunks
        }
    }
    with open(tracking_path, "w", encoding="utf-8") as f:
        json.dump(tracking_data, f, ensure_ascii=False, indent=2)
    print(f"URL tracking JSON written to: {tracking_path}")

    print("\nDone.")
    print(f"Tracking file contains {len(url_tracking)} URLs with content hashes and chunk mappings.")
    if not _HAS_GROQ:
        print("Note: Groq not installed; structured JSON is a local fallback.")

if __name__ == "__main__":
    main()
