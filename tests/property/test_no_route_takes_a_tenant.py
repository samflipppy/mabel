"""No portal route accepts a tenant identifier.

The same rule the MCP tool schemas have, applied to the other surface. There it
stops the model choosing whose data it sees; here it stops a logged-in user
doing the same by editing a URL.

Checked against the generated OpenAPI schema rather than by reading the source,
because the schema is what actually exists at runtime — including anything
FastAPI inferred from a Pydantic model that nobody looked at closely.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from mabel_api.main import openapi_schema

SCHEMA = openapi_schema()

# Anything that names a tenant, an account, or a business.
FORBIDDEN = re.compile(
    r"^(tenant|tenant_id|tenantid|account|account_id|business|business_id|"
    r"shop|shop_id|org|org_id|company|company_id)$",
    re.I,
)

# Webhooks authenticate by signature and are not portal routes. The MCP server
# has its own equivalent test against the tool schemas.
EXEMPT_PREFIXES = ("/webhooks/", "/mcp", "/health")


def _portal_paths() -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for path, operations in SCHEMA.get("paths", {}).items():
        if path.startswith(EXEMPT_PREFIXES):
            continue
        for method, operation in operations.items():
            if method in {"get", "post", "put", "delete", "patch"}:
                found.append((path, method, operation))
    return found


PORTAL_PATHS = _portal_paths()


def test_there_are_routes_to_check():
    # A guard that silently checks nothing is worse than no guard.
    assert PORTAL_PATHS, "found no portal routes in the OpenAPI schema"


@pytest.mark.parametrize(
    ("path", "method", "operation"),
    PORTAL_PATHS,
    ids=[f"{m.upper()} {p}" for p, m, _ in PORTAL_PATHS],
)
def test_no_parameter_names_a_tenant(path: str, method: str, operation: dict[str, Any]):
    offenders = [
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if FORBIDDEN.match(parameter["name"])
    ]
    assert not offenders, (
        f"{method.upper()} {path} accepts {offenders}. The tenant comes from the "
        "session, not from the request. See apps/api/src/mabel_api/deps.py."
    )


@pytest.mark.parametrize(
    ("path", "method", "operation"),
    PORTAL_PATHS,
    ids=[f"{m.upper()} {p}" for p, m, _ in PORTAL_PATHS],
)
def test_no_path_segment_names_a_tenant(path: str, method: str, operation: dict[str, Any]):
    del operation
    segments = re.findall(r"\{(\w+)\}", path)
    offenders = [segment for segment in segments if FORBIDDEN.match(segment)]
    assert not offenders, f"{method.upper()} {path} has a tenant in its path: {offenders}"


@pytest.mark.parametrize(
    ("path", "method", "operation"),
    PORTAL_PATHS,
    ids=[f"{m.upper()} {p}" for p, m, _ in PORTAL_PATHS],
)
def test_no_request_body_carries_a_tenant(path: str, method: str, operation: dict[str, Any]):
    """A body is the easiest place for one to appear unnoticed, because it
    arrives via a Pydantic model somebody else wrote."""
    body = operation.get("requestBody")
    if not body:
        return

    for content in body.get("content", {}).values():
        ref = content.get("schema", {}).get("$ref")
        if not ref:
            continue
        name = ref.rsplit("/", 1)[-1]
        model = SCHEMA["components"]["schemas"].get(name, {})
        offenders = [field for field in model.get("properties", {}) if FORBIDDEN.match(field)]
        assert not offenders, f"{method.upper()} {path} accepts {offenders} in its body via {name}."


def test_every_portal_route_requires_a_session():
    """A route that does not depend on `tenant_conn` or `current_user` has no
    tenant context, so under RLS it can only return nothing — which means it is
    either broken or it is reaching for something it should not."""
    import ast
    from pathlib import Path

    routes_dir = Path(__file__).resolve().parents[2] / "apps/api/src/mabel_api/routes"
    for module in routes_dir.glob("*.py"):
        if module.name == "__init__.py":
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            decorated = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr in {"get", "post", "put", "delete", "patch"}
                for d in node.decorator_list
            )
            if not decorated:
                continue
            annotations = {ast.unparse(arg.annotation) for arg in node.args.args if arg.annotation}
            assert any("TenantConn" in a or "CurrentUserDep" in a for a in annotations), (
                f"{module.name}::{node.name} is a route with no session dependency. "
                "Every portal route resolves its tenant from the session."
            )
