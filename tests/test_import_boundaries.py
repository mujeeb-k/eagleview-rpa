"""Invariant 1: no LLM on the replay path.

`eagleview.replay`, `eagleview.surfaces` and `eagleview.safety` must never import `anthropic` or
`eagleview.discovery`, directly or through anything they import. Checked two ways: statically, by
walking the import graph of the source tree (including package `__init__` files, which run on
import), and dynamically, by importing the packages in a fresh interpreter."""

import ast
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
GUARDED = ("eagleview.replay", "eagleview.surfaces", "eagleview.safety")
FORBIDDEN = ("anthropic", "eagleview.discovery")


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _modules(root: Path) -> dict[str, Path]:
    return {_module_name(p, root): p for p in root.rglob("*.py")}


def _imports(path: Path, module: str, is_package: bool) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                if node.level > 1:
                    base = base[: -(node.level - 1)]
                prefix = ".".join(base)
                target = f"{prefix}.{node.module}" if node.module else prefix
            else:
                target = node.module or ""
            yield target
            for alias in node.names:  # `from eagleview import discovery`
                yield f"{target}.{alias.name}"


def _ancestors(module: str) -> list[str]:
    parts = module.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def reachable(start: str, modules: dict[str, Path]) -> dict[str, list[str]]:
    """Every name imported, transitively, from `start`, with one import chain for each."""
    chains: dict[str, list[str]] = {}
    queue: list[tuple[str, list[str]]] = [(start, [start])]
    while queue:
        name, chain = queue.pop()
        if name in chains:
            continue
        chains[name] = chain
        path = modules.get(name)
        if path is None:
            continue
        # Importing a.b.c runs a/__init__.py and a/b/__init__.py first.
        for parent in _ancestors(name):
            if parent not in chains:
                queue.append((parent, [*chain, parent]))
        for target in _imports(path, name, path.name == "__init__.py"):
            if target and target not in chains:
                queue.append((target, [*chain, target]))
    return chains


def _forbidden_hits(chains: dict[str, list[str]]) -> list[str]:
    hits: list[str] = []
    for name, chain in chains.items():
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
            hits.append(" -> ".join(chain))
    return sorted(hits)


@pytest.mark.parametrize("package", GUARDED)
def test_guarded_package_exists(package: str) -> None:
    """A missing package would make the boundary checks pass vacuously."""
    assert package in _modules(SRC), f"{package} is missing; the boundary test would be vacuous"


@pytest.mark.parametrize("package", GUARDED)
def test_no_forbidden_imports_statically(package: str) -> None:
    modules = _modules(SRC)
    starts = [m for m in modules if m == package or m.startswith(package + ".")]
    assert starts
    for start in starts:
        hits = _forbidden_hits(reachable(start, modules))
        assert not hits, "forbidden import chain(s):\n" + "\n".join(hits)


def test_no_forbidden_imports_at_runtime() -> None:
    code = textwrap.dedent(
        f"""
        import importlib, pkgutil, sys
        for name in {GUARDED!r}:
            pkg = importlib.import_module(name)
            for info in pkgutil.walk_packages(pkg.__path__, name + "."):
                importlib.import_module(info.name)
        forbidden = {FORBIDDEN!r}
        bad = sorted(
            m for m in sys.modules if any(m == f or m.startswith(f + ".") for f in forbidden)
        )
        print("\\n".join(bad))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"loaded on the replay path: {result.stdout}"


def test_checker_catches_a_planted_transitive_import(tmp_path: Path) -> None:
    """Self-test: a guarded package that reaches anthropic through two hops, one of them a
    relative import inside a package __init__, is caught with its chain."""
    root = tmp_path / "src"
    files = {
        "eagleview/__init__.py": "",
        "eagleview/replay/__init__.py": "from . import engine\n",
        "eagleview/replay/engine.py": "from eagleview.util import helpers\n",
        "eagleview/util/__init__.py": "from .llm import client\n",
        "eagleview/util/helpers.py": "x = 1\n",
        "eagleview/util/llm.py": "import anthropic\nclient = None\n",
        "eagleview/safety/__init__.py": "from eagleview import discovery\n",
        "eagleview/discovery/__init__.py": "",
    }
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    modules = _modules(root)
    replay_hits = _forbidden_hits(reachable("eagleview.replay.engine", modules))
    assert any(h.endswith("eagleview.util.llm -> anthropic") for h in replay_hits), replay_hits
    safety_hits = _forbidden_hits(reachable("eagleview.safety", modules))
    assert any("eagleview.discovery" in h for h in safety_hits), safety_hits
