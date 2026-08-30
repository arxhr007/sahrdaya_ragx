"""HTML-parser name cleaning.

Used only on the fallback path now that faculty comes from the Firestore API, but that
path is what runs if the API is ever unavailable. It once wrote "file Dr. V. Vijikala"
into the database because the honorific pattern is anchored and the leftover UI word in
front of it stopped the match.
"""
import pytest

from sql_extractors.faculty_extractor import _clean_name, normalise_dept


class TestLeftoverUiText:
    @pytest.mark.parametrize("raw,expected", [
        ("file Dr. V. Vijikala", "V. Vijikala"),      # the row that reached the DB
        ("View Profile Dr Ambily", "Ambily"),          # two stacked noise words
        ("Download Dr. Jis Paul", "Jis Paul"),
        ("Back to Dr. Someone", "Someone"),
    ])
    def test_noise_is_stripped(self, raw, expected):
        assert _clean_name(raw) == expected


class TestHonorificsAndNumbers:
    @pytest.mark.parametrize("raw,expected", [
        ("Dr. Jis Paul", "Jis Paul"),
        ("Mr. A B", "A B"),
        ("Mrs. C D", "C D"),
        ("23 Dr. Manishankar S", "Manishankar S"),     # digits must go before the honorific
    ])
    def test_stripped(self, raw, expected):
        assert _clean_name(raw) == expected


class TestCasing:
    def test_all_caps_title_cased(self):
        assert _clean_name("RAMKUMAR S") == "Ramkumar S"

    def test_normal_names_untouched(self):
        assert _clean_name("Anju Babu") == "Anju Babu"

    def test_whitespace_collapsed(self):
        assert _clean_name("  Anju    Babu  ") == "Anju Babu"


class TestDepartmentNormalisation:
    @pytest.mark.parametrize("raw", [
        "Computer Science Engineering",
        "computer science engineering",
    ])
    def test_canonical_form(self, raw):
        assert normalise_dept(raw) == "Computer Science Engineering"

    def test_ampersand_variant_matches_the_and_form(self):
        # Firestore uses "Applied Science & Humanities"; the DB stores the "and" form.
        assert normalise_dept("Applied Science & Humanities") == "Applied Science and Humanities"
