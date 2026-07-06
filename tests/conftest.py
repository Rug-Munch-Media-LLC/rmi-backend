"""Pytest root conftest for the rmi-backend tests/ tree.

Excludes tests/manual/ from pytest auto-collection. Files in tests/manual/
are bespoke runners (custom @test() decorators, banner warnings about
pytest incompatibility, ad-hoc debugging scripts) that the audit's
manual-test suite owns. Run them directly with python3, not pytest:

    python3 tests/manual/run_tests.py
    python3 tests/manual/test_rag.py

To temporarily include them in a pytest run (e.g. for migration):

    pytest tests/ --collect-ignore-glob='"'"'!tests/manual/*'"'"'
"""

collect_ignore_glob = ["manual/*"]
