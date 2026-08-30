"""SQL result rendering.

"list all faculty" returned a 21,391-character table: SELECT * gives 18 columns and most
are empty for most people. These narrow it without losing information that is actually
present.
"""
import pytest

from rag_query import _drop_empty_columns, _limit_people_columns, format_sql_results

FACULTY_COLUMNS = [
    "id", "name", "designation", "department", "email", "has_phd", "phd_pursuing",
    "experience_years", "publications", "research", "awards", "patents", "books",
    "joined", "research_areas", "education", "memberships",
]


def faculty_row(name, designation="Assistant Professor"):
    return (1, name, designation, "Computer Science Engineering", "x@sahrdaya.ac.in",
            0, 0, 10.0, 5, 0, 0, 0, 0, "", "", "", "")


class TestDropEmptyColumns:
    def test_all_blank_columns_removed(self):
        columns = ["name", "notes", "awards"]
        rows = [("A", "", 0), ("B", None, 0)]
        kept, out = _drop_empty_columns(columns, rows)
        assert kept == ["name"]
        assert out == [("A",), ("B",)]

    def test_columns_with_any_value_are_kept(self):
        columns = ["name", "awards"]
        rows = [("A", 0), ("B", 3)]
        kept, _ = _drop_empty_columns(columns, rows)
        assert kept == columns

    def test_no_change_when_everything_is_populated(self):
        columns = ["name", "email"]
        rows = [("A", "a@x"), ("B", "b@x")]
        assert _drop_empty_columns(columns, rows) == (columns, rows)


class TestLimitPeopleColumns:
    def test_roster_is_narrowed_to_summary_columns(self):
        rows = [faculty_row("A"), faculty_row("B")]
        kept, _ = _limit_people_columns(FACULTY_COLUMNS, rows)
        assert "name" in kept and "designation" in kept and "email" in kept
        assert "memberships" not in kept and "education" not in kept

    def test_untouched_when_there_is_no_name_column(self):
        columns = ["total"]
        rows = [(5,)]
        assert _limit_people_columns(columns, rows) == (columns, rows)


class TestFormatSqlResults:
    def test_wide_roster_is_much_smaller_than_select_star(self):
        rows = [faculty_row("Person %d" % i) for i in range(122)]
        out = format_sql_results(FACULTY_COLUMNS, rows, "list all faculty")
        assert len(out) < 21391, "table should be narrower than the original 18-column dump"
        assert "**Total: 122 result(s)**" in out
        assert "Memberships" not in out

    def test_every_row_is_rendered(self):
        rows = [faculty_row("Person %d" % i) for i in range(30)]
        out = format_sql_results(FACULTY_COLUMNS, rows, "list all faculty")
        assert "Person 0" in out and "Person 29" in out

    def test_aggregate_result(self):
        assert "**Count: 4**" in format_sql_results(["COUNT(*)"], [(4,)], "how many") \
            or "4" in format_sql_results(["COUNT(*)"], [(4,)], "how many")

    def test_no_rows(self):
        assert format_sql_results(["name"], [], "anything") == "No matching records found."

    def test_single_role_result_still_shows_designation(self):
        rows = [faculty_row("Dr Manishankar S", "Head of Department")]
        out = format_sql_results(FACULTY_COLUMNS, rows, "who is the HOD of CSE")
        assert "Head of Department" in out
        assert "Dr Manishankar S" in out
