"""内嵌音频标签 Provider（mutagen，可选依赖 + 运行时动态 import）。

mutagen 是 GPL-2.0-or-later，因此必须：
1. 放在 `[project.optional-dependencies] audio` 里；
2. 运行时 `importlib.import_module("mutagen")` 动态导入，缺失时降级为文件名解析，绝不崩溃。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from renomate.config import Config
from renomate.country import normalize_country
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers import register_provider

CONFIDENCE: dict[str, float] = {
    "title": 0.95,
    "artist": 0.95,
    "album": 0.95,
    "year": 0.90,
    "track": 0.90,
    "genre": 0.80,
    "country": 0.70,
}

COUNTRY_TAG_KEYS = ("TXXX:COUNTRY", "COUNTRY", "----:com.apple.iTunes:COUNTRY")
LANGUAGE_KEYS = ("LANGUAGE", "language", "TSOL")

AUDIO_SUFFIXES = {
    ".mp3",
    ".flac",
    ".m4a",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".ape",
    ".wma",
    ".aiff",
    ".aif",
}


class MutagenMissingError(RuntimeError):
    """未安装 audio 额外依赖（`pip install "renomate[audio]"`）。"""


def _load_mutagen() -> Any:
    try:
        return importlib.import_module("mutagen")
    except ImportError as exc:  # pragma: no cover - 取决于环境
        msg = '未安装可选依赖 mutagen，请执行 pip install "renomate[audio]"'
        raise MutagenMissingError(msg) from exc


def _first(values: object) -> str:
    if values is None:
        return ""
    if isinstance(values, (list, tuple)):
        return str(values[0]) if values else ""
    return str(values)


def _join(values: object, separator: str) -> str:
    if values is None:
        return ""
    if isinstance(values, (list, tuple)):
        return separator.join(str(v) for v in values)
    return str(values)


def _year(value: str) -> str:
    """`2010-05-01` / `2010` → `2010`（取前 4 位数字）。"""
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[:4]


def _country_from_raw(raw: Any) -> str:
    """非标准 country 回退：TXXX:COUNTRY → COUNTRY → iTunes freeform。"""
    tags = getattr(raw, "tags", None)
    if tags is None:
        return ""
    getall = getattr(tags, "getall", None)
    if callable(getall):
        for key in COUNTRY_TAG_KEYS:
            try:
                frames = getall(key)
            except (KeyError, ValueError, TypeError):
                continue
            for frame in frames:
                text = getattr(frame, "text", None)
                if text:
                    return _join(text, "、").strip()
    for key in COUNTRY_TAG_KEYS:
        try:
            value = tags[key]
        except (KeyError, TypeError):
            continue
        if value:
            return _join(value, "、").strip()
    return ""


def _lookup(source: Any, keys: tuple[str, ...], separator: str) -> str:
    """在 easy/raw 标签容器里按键依次查找。"""
    if source is None:
        return ""
    try:
        for key in keys:
            value = source.get(key)
            if value:
                return _join(value, separator).strip()
    except (AttributeError, KeyError, TypeError):
        return ""
    return ""


def _language_from(raw: Any, easy: Any) -> str:
    for source in (easy, raw, getattr(easy, "tags", None), getattr(raw, "tags", None)):
        value = _lookup(source, LANGUAGE_KEYS, "、")
        if value:
            return value
    return ""


class AudioTagProvider:
    """用 mutagen 读内嵌标签。损坏文件只记原因，绝不中断整批扫描。"""

    name = "audiotag"

    def __init__(self) -> None:
        self.last_error: str = ""

    def supports(self, profile: str, path: Path) -> bool:
        return path.suffix.casefold() in AUDIO_SUFFIXES

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        if not self.supports(bundle.profile, bundle.path):
            return
        try:
            mutagen = _load_mutagen()
            easy = mutagen.File(bundle.path, easy=True)
            raw = mutagen.File(bundle.path)
        except MutagenMissingError:
            self.last_error = "mutagen 未安装，已降级为文件名解析"
            return
        except Exception as exc:  # noqa: BLE001 - 损坏文件不得中断整批扫描
            self.last_error = f"读取内嵌标签失败: {type(exc).__name__}"
            return

        self.last_error = ""
        if easy is None:
            self.last_error = "不支持的音频格式或损坏文件"
            return

        joiner = cfg.general.multi_value_join
        easy_tags = getattr(easy, "tags", None) or {}

        for field in ("title", "artist", "album", "genre"):
            if field in bundle.fields:
                value = _join(easy_tags.get(field), joiner).strip()
                if value:
                    bundle.fields[field] = FieldValue(
                        value, FieldSource.AUDIOTAG, CONFIDENCE[field]
                    )

        if "artist" in bundle.fields and not bundle.fields.get("artist"):
            album_artist = _join(easy_tags.get("albumartist"), joiner).strip()
            if album_artist:
                bundle.fields["artist"] = FieldValue(
                    album_artist, FieldSource.AUDIOTAG, CONFIDENCE["artist"]
                )

        if "year" in bundle.fields:
            raw_date = _first(easy_tags.get("date")).strip()
            year = _year(raw_date)
            if year:
                bundle.fields["year"] = FieldValue(year, FieldSource.AUDIOTAG, CONFIDENCE["year"])

        if "track" in bundle.fields:
            track = _first(easy_tags.get("tracknumber")).strip()
            track = track.split("/")[0]
            if track:
                bundle.fields["track"] = FieldValue(
                    track, FieldSource.AUDIOTAG, CONFIDENCE["track"]
                )

        if "country" in bundle.fields:
            country = _country_from_raw(raw)
            if not country:
                language = _language_from(raw, easy)
                if language:
                    country = normalize_country(language)
            if country:
                bundle.fields["country"] = FieldValue(
                    normalize_country(country), FieldSource.AUDIOTAG, CONFIDENCE["country"]
                )


register_provider(AudioTagProvider())
