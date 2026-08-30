"""Firestore faculty extraction.

The website renders /faculty and /faculty/profile/* client-side, so a crawl can capture
zero profiles while reporting success — that is how faculty extraction once collapsed from
109 rows to 5. The Firestore REST API is the source of truth instead, and it carries the
`position` field that makes "who is the HOD of X" answerable at all.

Runs against a checked-in fixture, so no network.
"""
import json
import pathlib

import pytest

from sql_extractors.firestore_faculty import (
    _as_count,
    _as_number,
    _clean_name,
    _detect_phd,
    _get,
    _scalar,
    _to_row,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "firestore_faculty.json"


@pytest.fixture(scope="module")
def documents():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["documents"]


@pytest.fixture(scope="module")
def rows(documents):
    return [_to_row(d) for d in documents]


class TestScalarUnwrapping:
    def test_string_and_integer(self):
        assert _scalar({"stringValue": " Dr. X "}) == "Dr. X"
        assert _scalar({"integerValue": "14"}) == "14"

    def test_array_joins_values(self):
        field = {"arrayValue": {"values": [{"stringValue": "a"}, {"stringValue": "b"}]}}
        assert _scalar(field) == "a, b"

    def test_missing_and_malformed(self):
        assert _scalar({}) == ""
        assert _scalar(None) == ""

    def test_get_falls_back_through_candidates(self):
        fields = {"mailId": {"stringValue": "x@y.z"}}
        assert _get(fields, "email", "mailId") == "x@y.z"
        assert _get(fields, "nope") == ""


class TestNumericCoercion:
    @pytest.mark.parametrize("raw,expected", [("14", 14.0), ("10.5 years", 10.5), ("", 0.0), ("n/a", 0.0)])
    def test_as_number(self, raw, expected):
        assert _as_number(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("7", 7),            # already a count
        ("", 0),
        ("a, b, c", 3),      # a list of entries means "how many"
    ])
    def test_as_count(self, raw, expected):
        assert _as_count(raw) == expected


class TestNameCleaning:
    def test_all_caps_is_title_cased(self):
        assert _clean_name("ADARSH SR") == "Adarsh Sr"

    def test_whitespace_collapsed(self):
        assert _clean_name("  Dr.   Jis   Paul ") == "Dr. Jis Paul"

    def test_honorific_is_preserved(self):
        # Unlike the HTML parser, the API name is authoritative and keeps its title.
        assert _clean_name("Dr. Ambily Francis") == "Dr. Ambily Francis"


class TestPhdDetection:
    def test_dr_prefix_implies_completed(self):
        assert _detect_phd("M.Tech", "Dr. Someone") == (1, 0)

    def test_pursuing_is_not_completed(self):
        completed, pursuing = _detect_phd("Ph.D.-doing NLP KTU", "Jane Doe")
        assert (completed, pursuing) == (0, 1)

    def test_plain_masters(self):
        assert _detect_phd("M.Tech Computer Science", "Jane Doe") == (0, 0)


class TestRowMapping:
    def test_position_maps_to_designation(self, rows):
        by_name = {r["name"]: r for r in rows}
        assert by_name["Dr. Ambily Francis"]["designation"] == "Head of Department"
        assert by_name["Dr. Ramkumar S"]["designation"] == "Principal"

    def test_hod_row_has_a_department(self, rows):
        hod = next(r for r in rows if r["designation"] == "Head of Department")
        assert hod["department"], "HOD rows must carry a department for role lookups"

    def test_email_lowercased(self, rows):
        for row in rows:
            assert row["email"] == row["email"].lower()

    def test_missing_position_is_empty_not_none(self, rows):
        # One fixture doc has no position; it must still produce a usable row.
        blanks = [r for r in rows if r["designation"] == ""]
        assert blanks and all(r["name"] for r in blanks)

    def test_every_row_matches_the_faculty_table_columns(self, rows):
        expected = {
            "name", "designation", "department", "email", "has_phd", "phd_pursuing",
            "experience_years", "publications", "research", "awards", "patents",
            "books", "joined", "research_areas", "education", "memberships",
        }
        for row in rows:
            assert set(row) == expected

    def test_document_without_a_name_is_skipped(self):
        assert _to_row({"fields": {"position": {"stringValue": "Professor"}}}) is None
