"""A test suite that skips itself is worse than no test suite.

`tests/isolation/conftest.py` skips its own suite when there is no database.
`pytest_collection_modifyitems` is handed *every* item in the session, not just
the ones under that directory, so a missing path filter there skips the entire
repo — and a green run of 585 skipped tests looks a lot like a green run.

That happened once while this was being written. This is the guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"


def _collection_hooks() -> list[Path]:
    return [
        path
        for path in TESTS.rglob("conftest.py")
        if "pytest_collection_modifyitems" in path.read_text(encoding="utf-8")
    ]


def test_every_collection_hook_is_scoped_to_its_own_directory():
    for path in _collection_hooks():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hook = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "pytest_collection_modifyitems"
            ),
            None,
        )
        assert hook is not None
        source = ast.get_source_segment(path.read_text(encoding="utf-8"), hook) or ""
        # The hook is handed every item in the session. It has to narrow to the
        # ones it means — by path, or by which fixtures they ask for — or it
        # silently disables the whole repo's tests.
        assert "fixturenames" in source or "fspath" in source, (
            f"{path.relative_to(REPO)} modifies collection for the whole session "
            "without narrowing. That skips every test in the repo."
        )


def test_this_suite_is_not_empty():
    # If the guard above is ever satisfied vacuously — no hooks found — this
    # still proves the file ran.
    assert TESTS.is_dir()
    assert list(TESTS.rglob("test_*.py"))
