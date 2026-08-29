"""The isolation suite executes the migration's DDL statement by statement.

If the splitter cuts a dollar-quoted block in half, the RLS section — which is
one large `DO $$ ... $$` containing every policy — silently fails to apply, and
the entire cross-tenant suite passes against a database with no policies on it.

That is the worst available failure mode: a green isolation run that proves
nothing. Hence this test, which needs no database.
"""

from __future__ import annotations

from tests.isolation.conftest import _load_sql, _split


def test_dollar_quoted_blocks_survive_intact():
    for statement in _split(_load_sql()):
        assert statement.count("$$") % 2 == 0, f"cut a $$ block in half:\n{statement[:200]}"
        assert statement.count("$f$") % 2 == 0, f"cut a $f$ block in half:\n{statement[:200]}"


def test_the_rls_block_arrives_as_one_statement():
    blocks = [s for s in _split(_load_sql()) if "CREATE POLICY tenant_isolation" in s]
    assert len(blocks) == 1, "the RLS DO block should be exactly one statement"
    block = blocks[0]
    assert "FORCE ROW LEVEL SECURITY" in block
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in block
    assert block.strip().upper().startswith("DO")


def test_the_uuidv7_function_body_is_not_split():
    blocks = [s for s in _split(_load_sql()) if "uuidv7()" in s and "CREATE OR REPLACE" in s]
    assert len(blocks) == 1
    assert "LANGUAGE sql VOLATILE" in blocks[0]


def test_every_statement_is_non_empty_and_comment_free():
    statements = _split(_load_sql())
    assert len(statements) > 40, f"suspiciously few statements: {len(statements)}"
    for statement in statements:
        assert statement.strip(), "produced an empty statement"
        assert not statement.strip().startswith("--")
