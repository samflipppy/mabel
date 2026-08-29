"""No-database E2E tests. The DB-backed ones live under tests/isolation/e2e/
so they share the scratch Postgres fixtures.
"""

from __future__ import annotations

from mabel_media.inbound import bind_inbound_opener
import pytest


@pytest.fixture(autouse=True)
def _reset_inbound_opener():
    bind_inbound_opener(None)
    yield
    bind_inbound_opener(None)
