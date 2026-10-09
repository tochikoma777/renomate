"""用户手工值 Provider 与缺省值 Provider（SPEC 7.1 顺序 0 与 5）。

手工值 `locked=True`，优先级最高且不被后续 Provider 覆盖；
缺省值置信度 0.1，仅兜底，缺失字段仍会在渲染时记入 `missing`。
"""

from __future__ import annotations

from pathlib import Path

from renomate.config import Config
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers import register_provider

MANUAL_CONFIDENCE = 1.0
DEFAULT_CONFIDENCE = 0.10


class ManualProvider:
    """用户在 TUI/CLI 手工指定的值（locked=True，置信度 1.0）。"""

    name = "manual"

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})

    def supports(self, profile: str, path: Path) -> bool:
        return bool(self.values)

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        for field, value in self.values.items():
            if value.strip():
                bundle.set(
                    field,
                    FieldValue(value.strip(), FieldSource.MANUAL, MANUAL_CONFIDENCE, locked=True),
                )


class DefaultProvider:
    """配置缺省值兜底（置信度 0.1）。"""

    name = "default"

    def supports(self, profile: str, path: Path) -> bool:
        return True

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        for field in bundle.fields:
            fallback = cfg.defaults.get(field)
            if fallback:
                bundle.fields[field] = FieldValue(fallback, FieldSource.DEFAULT, DEFAULT_CONFIDENCE)


register_provider(ManualProvider())
register_provider(DefaultProvider())
