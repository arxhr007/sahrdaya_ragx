"""The lazy engine contract.

rag_setup used to build FAISS, download two models and open a Groq client at import,
then exit(1) if no key was set. These tests hold the new contract in place: importing is
cheap and side-effect free, and every name callers relied on still resolves.

Nothing here builds the engine — that would defeat the point and take ~20s.
"""
import importlib
import subprocess
import sys

import pytest

# Names main.py imports and api/routes/chat.py reads off the module. If the lazy
# __getattr__ misses one, it fails at runtime rather than import, so assert on the set.
PUBLIC_SURFACE = [
    # main.py
    "qa_chain_with_context", "chat_history", "retrieve_with_metadata", "expand_query",
    "classify_and_generate_sql", "execute_faculty_sql", "format_sql_results",
    "validate_faculty_sql", "retrieve_supporting_urls",
    # api/routes/chat.py
    "_FACULTY_SCHEMA", "_SQL_CLASSIFY_PROMPT", "_SQL_HISTORY_LIMIT",
    "_is_bulk_entity_query", "_role_lookup_sql", "_student_single_lookup_sql",
    "canonicalize_query_pipeline", "prompt",
]

LAZY_ATTRIBUTES = [
    "llm", "prompt", "retriever", "retriever_large", "CONTENT_GRAPH", "_reranker",
    "qa_chain", "qa_chain_with_context", "embeddings", "docs", "_sql_classify_chain",
]


def test_import_is_cheap_and_loads_no_models():
    """A subprocess so the measurement is not polluted by an already-warm sys.modules."""
    code = (
        "import time,sys;"
        "t=time.time();"
        "import rag_setup;"
        "dt=time.time()-t;"
        "heavy=[m for m in ('torch','sentence_transformers','faiss') if m in sys.modules];"
        "print(dt);print(heavy);print(rag_setup.engine_ready())"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr
    elapsed, heavy, ready = out.stdout.strip().splitlines()[:3]
    assert float(elapsed) < 5.0, "import took %ss; heavy work leaked back in" % elapsed
    assert heavy == "[]", "import pulled in %s" % heavy
    assert ready == "False", "engine was built at import time"


def test_import_succeeds_without_an_api_key():
    """It used to call exit(1), killing any process that merely imported the module."""
    code = (
        "import os;"
        "os.environ['GROQ_API_KEY']='';os.environ['GROQ_API_KEYS']='';"
        "import rag_setup;"
        "print(rag_setup._deterministic_query_map('ece hod'))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, "import failed without a key:\n%s" % out.stderr
    assert "Head of Department" in out.stdout


@pytest.mark.parametrize("name", PUBLIC_SURFACE)
def test_public_surface_is_declared(name):
    """Every name callers use must be a real global or a declared lazy attribute."""
    rag_setup = importlib.import_module("rag_setup")
    assert name in vars(rag_setup) or name in rag_setup._LAZY_NAMES, (
        "%r is neither defined at module level nor declared lazy; "
        "callers would hit AttributeError at runtime" % name
    )


@pytest.mark.parametrize("name", LAZY_ATTRIBUTES)
def test_heavy_attributes_are_lazy(name):
    rag_setup = importlib.import_module("rag_setup")
    assert name in rag_setup._LAZY_NAMES


def test_unknown_attribute_still_raises_attribute_error():
    rag_setup = importlib.import_module("rag_setup")
    with pytest.raises(AttributeError):
        rag_setup.definitely_not_a_real_name


def test_pure_logic_works_without_building_the_engine():
    rag_setup = importlib.import_module("rag_setup")
    assert not rag_setup.engine_ready()
    assert rag_setup._deterministic_query_map("cse list") == \
        "list all faculty in Computer Science Engineering"
    assert not rag_setup.engine_ready(), "pure call should not have built the engine"
