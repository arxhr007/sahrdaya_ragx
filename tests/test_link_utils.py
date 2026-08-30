"""Link honesty.

Three link questions once answered "the context does not contain a direct URL" while the
real PDFs sat in the retrieved context. Two causes, both regression-tested here:

  1. the fallback guard used a bare URL regex, so the generic homepage the model likes to
     append ("please visit https://sahrdaya.ac.in/") counted as a real link and suppressed
     the fallback entirely;
  2. the denial-stripper only matched sentences ending in a period, missing
     "...does not contain a direct URL, so visit the home page:".

Both fixes were initially wrong in ways only ad-hoc probing caught — a markdown-bolded
homepage (**https://site/**) kept its asterisks and read as a path. Hence these tests.
"""
import pytest

from link_utils import (
    extract_urls,
    format_fallback_links,
    harmonize_response_with_links,
    has_useful_link,
    is_homepage_url,
    query_likely_needs_links,
)

REAL_PDF = ("https://firebasestorage.googleapis.com/v0/b/college-website-27cf1"
            ".firebasestorage.app/o/tpo%2Fplacement%2Fsah%2F2018-22%2F2022merged.pdf?alt=media")


class TestHomepageDetection:
    @pytest.mark.parametrize("url", [
        "https://sahrdaya.ac.in/",
        "https://sahrdaya.ac.in",
        "http://www.sahrdaya.ac.in/",
    ])
    def test_bare_roots_are_homepages(self, url):
        assert is_homepage_url(url)

    @pytest.mark.parametrize("url", [
        "https://sahrdaya.ac.in/placements",
        REAL_PDF,
    ])
    def test_specific_pages_are_not(self, url):
        assert not is_homepage_url(url)


class TestHasUsefulLink:
    @pytest.mark.parametrize("text", [
        "please visit https://sahrdaya.ac.in/",
        "**https://sahrdaya.ac.in/**",            # markdown bold kept the asterisks
        "`https://sahrdaya.ac.in/`",
        "see https://sahrdaya.ac.in/logo.png",    # static asset is not a document
        "no links here at all",
        "",
    ])
    def test_rejects_non_answers(self, text):
        assert not has_useful_link(text)

    @pytest.mark.parametrize("text", [
        "report: " + REAL_PDF,
        "**%s**" % REAL_PDF,
        "visit https://sahrdaya.ac.in/ or %s" % REAL_PDF,
    ])
    def test_accepts_real_documents(self, text):
        assert has_useful_link(text)


class TestDenialStripping:
    @pytest.mark.parametrize("denial", [
        "The provided context does not contain a direct URL to the placement report page.",
        "I'm sorry, but the provided information does not contain a direct URL for any syllabus PDF.",
        "The context does not include direct links for this.",
        "The context does not contain a direct URL to that page, so visit the home page:",
    ])
    def test_removed_when_links_are_appended(self, denial):
        out = harmonize_response_with_links(denial + "\n" + REAL_PDF, links_appended=True)
        assert "does not" not in out.lower()

    def test_left_alone_when_nothing_was_appended(self):
        denial = "The context does not contain a direct URL."
        assert harmonize_response_with_links(denial, links_appended=False) == denial


class TestExtraction:
    def test_documents_rank_before_pages(self):
        text = "https://sahrdaya.ac.in/about and %s" % REAL_PDF
        assert extract_urls(text)[0] == REAL_PDF

    def test_static_assets_and_duplicates_dropped(self):
        text = "a.png https://sahrdaya.ac.in/x.png %s %s" % (REAL_PDF, REAL_PDF)
        urls = extract_urls(text)
        assert urls == [REAL_PDF]

    def test_limit_is_honoured(self):
        text = " ".join("https://sahrdaya.ac.in/p%d" % i for i in range(20))
        assert len(extract_urls(text, limit=3)) == 3


class TestQueryDetection:
    @pytest.mark.parametrize("question", [
        "placement report pdf 2022-23", "download the syllabus", "where are the audit statistics",
    ])
    def test_link_seeking(self, question):
        assert query_likely_needs_links(question)

    def test_non_link_seeking(self):
        assert not query_likely_needs_links("who is the principal")


class TestFallbackFormatting:
    def test_placement_links_grouped_by_year(self):
        urls = [
            "https://x/o/tpo%2Fplacement%2Fsah%2F2019-23%2Fa.pdf",
            "https://x/o/tpo%2Fplacement%2Fsah%2F2018-22%2Fb.pdf",
        ]
        out = format_fallback_links("placement report", urls)
        assert out.index("2018-22") < out.index("2019-23"), "years must be ascending"

    def test_generic_queries_get_a_plain_list(self):
        out = format_fallback_links("syllabus", [REAL_PDF])
        assert out.startswith("Direct links from context:")
