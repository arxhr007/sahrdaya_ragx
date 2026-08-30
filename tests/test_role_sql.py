"""_role_lookup_sql — the deterministic replacement for LLM-generated role queries.

The LLM classifier answered "who is the HOD of CSE" with
    SELECT * FROM faculty WHERE department LIKE '%Computer Science Engineering%'
which returns the whole department. This builder must always constrain designation.
"""
import re

import pytest

from rag_query import _deterministic_query_map, _role_lookup_sql


def sql_for(question):
    return _role_lookup_sql(_deterministic_query_map(question))


class TestFilters:
    def test_filters_on_designation_and_department(self):
        sql = sql_for("who is the HOD of CSE")
        assert "designation LIKE '%Head of Department%'" in sql
        assert "department LIKE '%Computer Science Engineering%'" in sql

    def test_role_without_department_omits_department_filter(self):
        sql = sql_for("who is the principal")
        assert "designation LIKE '%Principal%'" in sql
        assert "department LIKE" not in sql

    def test_never_selects_a_bare_department(self):
        """The exact shape of the original bug."""
        sql = sql_for("ece hod")
        assert "designation" in sql, sql
        assert not re.search(r"WHERE\s+department\s+LIKE[^A]*$", sql), sql

    def test_is_select_only(self):
        assert sql_for("who is the HOD of CSE").strip().upper().startswith("SELECT")

    def test_returns_named_columns_not_star(self):
        # SELECT * produced an 18-column table of mostly empty cells.
        sql = sql_for("who is the HOD of CSE")
        assert "SELECT *" not in sql
        for column in ("name", "designation", "department", "email"):
            assert column in sql


class TestNonRoleQueries:
    @pytest.mark.parametrize("question", [
        "list all faculty in Computer Science Engineering",
        "what is the admission process",
        "how many students are there",
    ])
    def test_returns_none(self, question):
        assert sql_for(question) is None

    def test_vice_principal_is_a_former_people_role(self):
        # Vice Principal exists in former_people, not in faculty.designation.
        assert _role_lookup_sql("who is the Vice Principal") is None


class TestAgainstLiveSchema:
    """Runs against data/sql/college.db when present — proves the SQL is executable."""

    def test_hod_query_returns_exactly_one_person(self):
        db = pytest.importorskip("sqlite3")
        import os
        if not os.path.exists("data/sql/college.db"):
            pytest.skip("college.db not built")
        from rag_query import execute_faculty_sql, validate_faculty_sql

        sql = sql_for("who is the HOD of CSE")
        assert validate_faculty_sql(sql)
        result = execute_faculty_sql(sql)
        assert result is not None
        _columns, rows = result
        assert len(rows) == 1, "expected one HOD, got %d rows" % len(rows)
