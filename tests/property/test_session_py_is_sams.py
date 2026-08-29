"""`apps/media/src/mabel_media/session.py` is Sam's file.

04-REPO.md and AGENTS.md both say so, and the reason is specific: the real-time
path against a sparsely documented API is where an agent writes confident,
wrong code. An agent that "helpfully" fills in `open_session()` would produce
something that looks right, passes review, and drops calls.

So this is a guard, not a courtesy. It fails if the TODOs are gone or the
function has grown a body.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SESSION = REPO / "apps" / "media" / "src" / "mabel_media" / "session.py"


def _tree() -> ast.Module:
    return ast.parse(SESSION.read_text(encoding="utf-8"))


def test_the_file_exists_and_says_whose_it_is():
    assert SESSION.is_file()
    body = SESSION.read_text(encoding="utf-8")
    assert "SAM WRITES THIS FILE" in body
    assert "04-REPO.md" in body


def test_open_session_is_still_unimplemented():
    """The interface exists so the rest of the call path can be built and
    tested against a stable shape. The body is not ours to write."""
    tree = _tree()
    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "open_session"
        ),
        None,
    )
    assert func is not None, "open_session should still be declared"

    statements = [node for node in func.body if not isinstance(node, ast.Expr)]
    assert len(statements) == 1, (
        "open_session has grown a body. That file is Sam's — see 04-REPO.md."
    )
    assert isinstance(statements[0], ast.Raise)


def test_the_todos_are_still_there():
    body = SESSION.read_text(encoding="utf-8")
    todos = [line for line in body.splitlines() if "TODO(sam)" in line]
    assert len(todos) >= 6, f"expected the TODO list to survive, found {len(todos)}"


def test_the_notes_point_at_the_finished_pieces():
    """The value of leaving this file empty is only realised if whoever writes
    it can find the parts that are done."""
    body = SESSION.read_text(encoding="utf-8")
    for reference in (
        "build_session_update",
        "build_opening_disclosure",
        "assert_session_is_safe",
        "render_prompt",
        "join_url",
        "mint_call_token",
        "postcall.finalize",
    ):
        assert reference in body, f"the handover notes should mention {reference}"


def test_the_fail_safe_rule_is_recorded():
    # A Mabel outage means the contractor is back where he started, not worse.
    body = SESSION.read_text(encoding="utf-8")
    assert "voicemail" in body
    assert "Fail safe" in body


def test_the_fake_transport_exists_so_nothing_else_needs_a_socket():
    tree = _tree()
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert "FakeSessionTransport" in names
    assert "SessionTransport" in names
