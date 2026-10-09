"""国别归一化：中文/常见写法 ↔ ISO 3166-1 alpha-2。

数据来自包内 `data/country_aliases.toml`（SPEC 7.4）。模板里 `{country}` 取代码，
`{country_zh}` 取中文名。
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.11 以下需 tomli
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

_DATA_FILE = Path(__file__).resolve().parent / "data" / "country_aliases.toml"


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, str], dict[str, str]]:
    try:
        raw: object = tomllib.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover - 包内数据缺失属安装损坏
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}
    aliases_raw: Any = raw.get("aliases", {})
    zh_raw: Any = raw.get("zh_names", {})
    aliases = {str(k).casefold(): str(v).upper() for k, v in aliases_raw.items()}
    zh = {str(k).upper(): str(v) for k, v in zh_raw.items()}
    return aliases, zh


def normalize_country(value: str) -> str:
    """把任意写法归一化成两位大写代码；无法识别时原样返回（去空白）。"""
    text = value.strip()
    if not text:
        return ""
    aliases, _ = _load()
    if len(text) == 2 and text.upper() in aliases.values():
        return text.upper()
    return aliases.get(text.casefold(), text.upper() if len(text) <= 3 else text)


def country_zh(code: str) -> str:
    """代码 → 中文名；无对应时返回原代码。"""
    aliases, zh = _load()
    normalized = aliases.get(code.casefold(), code.upper())
    return zh.get(normalized, normalized)
