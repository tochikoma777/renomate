"""架构测试：用 `ast` 断言依赖方向铁律（SPEC 第 4 节）。

核心层（models / template / safety / executor / journal / plan / config / report /
scanner）与 `renomate/platform` 不得 import mutagen / musicbrainzngs / textual。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "renomate"

CORE_MODULES = {
    "models.py",
    "template.py",
    "safety.py",
    "executor.py",
    "journal.py",
    "plan.py",
    "config.py",
    "report.py",
    "scanner.py",
    "profiles.py",
}
FORBIDDEN_IMPORTS = {"mutagen", "musicbrainzngs", "textual"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("filename", sorted(CORE_MODULES))
def test_core_modules_do_not_import_plugins_or_ui(filename: str) -> None:
    path = SRC_ROOT / filename
    if not path.exists():  # pragma: no cover - 允许分阶段实现
        pytest.skip(f"{filename} 尚未实现")
    offenders = _imported_roots(path) & FORBIDDEN_IMPORTS
    assert not offenders, f"{filename} 不应 import {sorted(offenders)}"


def test_platform_layer_is_plugin_free() -> None:
    for path in (SRC_ROOT / "platform").rglob("*.py"):
        offenders = _imported_roots(path) & FORBIDDEN_IMPORTS
        assert not offenders, f"{path.name} 不应 import {sorted(offenders)}"


def test_providers_do_not_import_ui() -> None:
    providers_dir = SRC_ROOT / "providers"
    if not providers_dir.exists():  # pragma: no cover
        pytest.skip("providers 尚未实现")
    for path in providers_dir.rglob("*.py"):
        offenders = _imported_roots(path) & {"textual"}
        assert not offenders, f"{path.name} 不应 import textual"


def test_tui_does_not_import_mutagen_directly() -> None:
    tui_dir = SRC_ROOT / "tui"
    if not tui_dir.exists():  # pragma: no cover
        pytest.skip("tui 尚未实现")
    for path in tui_dir.rglob("*.py"):
        offenders = _imported_roots(path) & {"mutagen", "musicbrainzngs"}
        assert not offenders, f"{path.name} 不应直接 import {sorted(offenders)}"


def test_source_tree_layout() -> None:
    """目录结构必须遵循 SPEC 第 5 节。"""
    assert (SRC_ROOT / "__init__.py").exists()
    assert (SRC_ROOT / "providers").is_dir()
    assert (SRC_ROOT / "tui").is_dir()
    assert (SRC_ROOT / "platform").is_dir()
