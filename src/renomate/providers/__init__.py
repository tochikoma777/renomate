"""Provider 插件层：PROVIDER_REGISTRY + 协议定义（SPEC 7.1）。

Provider 只负责「给一个路径、填几个字段」，不 import TUI/CLI。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from renomate.config import Config
from renomate.models import MetadataBundle

PROVIDER_ORDER: list[str] = ["manual", "audiotag", "path", "filename", "network", "default"]


@runtime_checkable
class MetadataProvider(Protocol):
    name: str

    def supports(self, profile: str, path: Path) -> bool:
        """该 Provider 是否能处理这个 profile + 路径。"""
        ...

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        """只填 bundle.fields 中为 None 且未被 locked 的字段；写值时附带 source / confidence。"""
        ...


PROVIDER_REGISTRY: dict[str, MetadataProvider] = {}


def register_provider(provider: MetadataProvider) -> MetadataProvider:
    """注册一个 Provider 实例（同名覆盖）。"""
    PROVIDER_REGISTRY[provider.name] = provider
    return provider


def unregister_provider(name: str) -> None:
    """仅供测试使用。"""
    PROVIDER_REGISTRY.pop(name, None)


def get_provider(name: str) -> MetadataProvider | None:
    return PROVIDER_REGISTRY.get(name)


def available_providers() -> list[str]:
    """按固定优先级顺序返回已注册的 Provider 名。"""
    known = [n for n in PROVIDER_ORDER if n in PROVIDER_REGISTRY]
    extra = sorted(n for n in PROVIDER_REGISTRY if n not in PROVIDER_ORDER)
    return known + extra


def _register_builtins() -> None:
    """延迟导入并注册内置 Provider。

    延迟导入是为了让缺失可选依赖（mutagen / musicbrainzngs）时依然能 import 本包。
    """
    from renomate.providers import audiotag, filename, manual, musicbrainz, path  # noqa: F401

    del audiotag, filename, manual, musicbrainz, path


_register_builtins()

__all__ = [
    "MetadataProvider",
    "PROVIDER_ORDER",
    "PROVIDER_REGISTRY",
    "available_providers",
    "get_provider",
    "register_provider",
    "unregister_provider",
]
