"""Query canonicalisation and SQL routing.

These cover the defect where "who is the HOD of CSE" returned all 35 CSE staff: the
deterministic map treated "hod" as a people-entity keyword and rewrote the question into
a department listing, and nothing downstream could recover the single-person intent.
"""
import pytest

from rag_query import (
    _deterministic_query_map,
    _is_bulk_entity_query,
    _is_safe_mapped_query,
    _looks_single_person_query,
    _role_lookup_sql,
    expand_query,
    is_creator_query,
    is_list_query,
)


class TestRoleQueriesNameOnePerson:
    @pytest.mark.parametrize("question", [
        "who is the HOD of CSE",
        "ece hod",
        "who is teh hod of ece",          # typo tolerated without the LLM corrector
        "head of department of biotechnology",
        "cse department head",
    ])
    def test_maps_to_single_person_form(self, question):
        mapped = _deterministic_query_map(question)
        assert mapped.lower().startswith("who is the"), mapped
        assert not mapped.lower().startswith("list all faculty"), (
            "role question was rewritten into a department listing: %r" % mapped
        )

    def test_department_is_carried_through(self):
        assert "Electronics and Communication Engineering" in _deterministic_query_map("ece hod")
        assert "Computer Science Engineering" in _deterministic_query_map("who is the HOD of CSE")

    def test_assistant_hod_wins_over_plain_hod(self):
        mapped = _deterministic_query_map("who is the assistant hod of cse")
        assert "Assistant HOD" in mapped

    def test_principal_without_department(self):
        assert _deterministic_query_map("who is the principal") == "who is the Principal"


class TestBulkQueriesStayBulk:
    @pytest.mark.parametrize("question,expected", [
        ("cse list", "list all faculty in Computer Science Engineering"),
        ("all ece faculty", "list all faculty in Electronics and Communication Engineering"),
    ])
    def test_department_listings(self, question, expected):
        assert _deterministic_query_map(question) == expected

    def test_list_all_hods_is_still_bulk(self):
        mapped = _deterministic_query_map("list all hods")
        assert mapped.startswith("list all"), mapped

    def test_former_roles_route_to_former_people(self):
        assert _deterministic_query_map("list all former principals") == "list all former Principals"

    @pytest.mark.parametrize("question", [
        "who was the principal before",
        "former hod of cse",
        "past principal",
    ])
    def test_former_queries_never_take_the_current_role_path(self, question):
        assert _role_lookup_sql(_deterministic_query_map(question)) is None


class TestIntentDetectors:
    @pytest.mark.parametrize("question", ["ece hod", "who is the principal", "who is the dean"])
    def test_role_questions_look_single_person(self, question):
        assert _looks_single_person_query(question)

    @pytest.mark.parametrize("question", ["list all faculty", "how many former principals"])
    def test_bulk_questions_do_not(self, question):
        assert not _looks_single_person_query(question)

    def test_safety_guard_blocks_single_to_bulk_rewrite(self):
        assert not _is_safe_mapped_query("who is Dr Jis Paul", "list all faculty")
        assert _is_safe_mapped_query("cse list", "list all faculty in Computer Science Engineering")

    @pytest.mark.parametrize("question,expected", [
        ("list all faculty", True),
        ("how many former principals are there", True),
        ("what is the admission process", False),
    ])
    def test_bulk_entity_gate(self, question, expected):
        assert _is_bulk_entity_query(question) is expected

    def test_list_and_creator_detectors(self):
        assert is_list_query("list all faculty")
        assert is_creator_query("who created you")
        assert is_creator_query("who built this chatbot")
        assert not is_creator_query("who is the principal")


class TestExpansion:
    def test_department_acronyms_expand(self):
        assert "Computer Science Engineering" in expand_query("cse")
        assert "Electronics and Communication Engineering" in expand_query("ece")

    def test_expansion_is_not_destructive(self):
        assert expand_query("") == ""
        assert "admission" in expand_query("admission").lower()
