"""`packages/domain/` and `packages/verticals/` are pure. No I/O, no DB, no
network.

This is enforced by reading the source, not by trusting a convention. The
check is static — it walks the AST of every module and looks at what it
imports and what it calls — so it catches the import someone adds at 2am
without running the code.

The point isn't purity for its own sake. These two packages carry the rules
that decide whether an owner gets woken up, and they are the packages an agent
is most likely to be let loose on. Keeping them I/O-free is what makes them
trivially testable and safe to change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PURE_PACKAGES = {
    "domain": REPO / "packages" / "domain" / "mabel_domain",
    "verticals": REPO / "packages" / "verticals" / "mabel_verticals",
}

# Anything that reaches outside the process.
FORBIDDEN_IMPORTS = {
    "asyncpg",
    "boto3",
    "httpx",
    "psycopg",
    "psycopg2",
    "requests",
    "socket",
    "sqlalchemy",
    "ssl",
    "urllib",
    "urllib3",
    "websockets",
    "aiohttp",
    "redis",
    "stripe",
    "supabase",
    "telnyx",
    "smtplib",
    "subprocess",
    "multiprocessing",
    "http",
    "ftplib",
}

# `os` and `pathlib` are not forbidden outright — the verticals loader has to
# read its own ruleset JSON off disk, which is bundled package data, not I/O
# against the world. Everything else that touches the filesystem is.
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "input"}


def _modules(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _all_pure_modules() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for label, root in PURE_PACKAGES.items():
        if not root.is_dir():
            continue
        found.extend((label, path) for path in _modules(root))
    return found


PURE_MODULES = _all_pure_modules()


def test_the_pure_packages_actually_exist():
    # A guard that silently checks nothing is worse than no guard.
    assert PURE_MODULES, f"found no modules under {list(PURE_PACKAGES.values())}"


@pytest.mark.parametrize(
    ("label", "path"), PURE_MODULES, ids=[f"{k}:{p.name}" for k, p in PURE_MODULES]
)
def test_no_io_imports(label: str, path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, stays inside the package
                continue
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_IMPORTS:
                offenders.append(f"line {node.lineno}: from {node.module} import ...")

    assert not offenders, (
        f"{path.relative_to(REPO)} is in a pure package but reaches outside the "
        f"process: {offenders}. Move this code to packages/db/ or a client package."
    )


@pytest.mark.parametrize(
    ("label", "path"), PURE_MODULES, ids=[f"{k}:{p.name}" for k, p in PURE_MODULES]
)
def test_no_dynamic_execution(label: str, path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = [
        f"line {node.lineno}: {node.func.id}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FORBIDDEN_CALLS
    ]
    assert not offenders, f"{path.relative_to(REPO)} executes dynamically: {offenders}"


@pytest.mark.parametrize(
    ("label", "path"), PURE_MODULES, ids=[f"{k}:{p.name}" for k, p in PURE_MODULES]
)
def test_nothing_imports_upward(label: str, path: Path):
    """`packages/` never imports from `apps/`. The dependency arrow points one
    way, or the pure packages stop being independently testable."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            root = name.split(".")[0]
            if root in {"mabel_api", "mabel_media", "mabel_worker"}:
                offenders.append(f"line {node.lineno}: {name}")
    assert not offenders, f"{path.relative_to(REPO)} imports from apps/: {offenders}"


def test_domain_does_not_import_verticals_or_vice_versa_at_module_scope():
    """The two pure packages stay independent of each other. `verticals` may
    describe an emergency; `domain` may hold a lead. Neither needs the other,
    and coupling them makes the ruleset harder to fixture."""
    for label, path in PURE_MODULES:
        other = "mabel_verticals" if label == "domain" else "mabel_domain"
        source = path.read_text(encoding="utf-8")
        assert f"import {other}" not in source and f"from {other}" not in source, (
            f"{path.relative_to(REPO)} couples the two pure packages together"
        )
