"""Who may import whom between the HTTP layer and everything under it.

Routers declare the paths; ``app.api`` holds what they share (dependencies,
guards, presenters); services hold what the application does and know nothing of
HTTP. The rules are cheap to state and expensive to recover once broken, so a
test holds them: a router that borrows another router's internals cannot be read
or moved on its own, and a service that imports the HTTP layer cannot be called
from the CLI or the MCP server without dragging it in.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).parents[1] / "app"


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def python_files(package: str) -> list[Path]:
    return sorted((APP / package).rglob("*.py"))


def router_module(path: Path) -> str:
    return ".".join(("app", *path.relative_to(APP).with_suffix("").parts))


class TestRouters:
    def test_no_router_imports_another(self) -> None:
        """Shared things live in ``app.api``. A router's own package may import its parts."""
        offenders: list[str] = []
        for path in python_files("routers"):
            if path.name == "__init__.py":
                continue
            package = router_module(path).rsplit(".", 1)[0]
            for module in imports_of(path):
                if not module.startswith("app.routers"):
                    continue
                if module == package or module.startswith(f"{package}."):
                    continue
                offenders.append(f"{router_module(path)} imports {module}")

        assert offenders == []


class TestWhatIsUnderTheHttpLayer:
    def test_nothing_below_it_imports_it(self) -> None:
        offenders: list[str] = []
        for package in ("services", "models", "core", "auth", "realtime", "schemas"):
            for path in python_files(package):
                for module in imports_of(path):
                    if module.startswith(("app.routers", "app.api")):
                        offenders.append(f"{path.relative_to(APP)} imports {module}")

        assert offenders == []
