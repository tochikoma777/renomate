"""MusicBrainz 可选网络补全 Provider（默认关闭，SPEC 7.4）。

合规要求（MusicBrainz 服务条款）：
- 必须设置含联系方式的 User-Agent；
- 限流 1 req/s；
- 本地缓存（sha1(query).json，TTL 默认 30 天），命中缓存不发请求；
- 网络异常一律降级为「该字段缺失」，绝不中断整批流程。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from renomate import __version__
from renomate.config import Config, mb_cache_dir
from renomate.country import normalize_country
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers import register_provider

NETWORK_CONFIDENCE = 0.85
COMPLETABLE_FIELDS = ("album", "country", "year")


@dataclass
class MusicBrainzStats:
    """一次批处理的网络统计，供报告展示。"""

    queries: int = 0
    cache_hits: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class LookupResult:
    album: str = ""
    country: str = ""
    year: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"album": self.album, "country": self.country, "year": self.year}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LookupResult:
        return cls(
            album=str(data.get("album", "")),
            country=str(data.get("country", "")),
            year=str(data.get("year", "")),
        )


def cache_key(artist: str, title: str) -> str:
    digest = hashlib.sha1(f"{artist}\x00{title}".encode("utf-8")).hexdigest()
    return digest


def _cache_path(artist: str, title: str, cache_dir: Path) -> Path:
    return cache_dir / f"{cache_key(artist, title)}.json"


class MusicBrainzProvider:
    """只补全缺失字段；默认关闭，需配置 `musicbrainz.enabled = true`。"""

    name = "network"

    def __init__(self) -> None:
        self.stats = MusicBrainzStats()

    def supports(self, profile: str, path: Path) -> bool:
        return profile in {"audio"}

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        if not cfg.musicbrainz.enabled:
            return
        if not self.supports(bundle.profile, bundle.path):
            return

        artist = bundle.get("artist").strip()
        title = bundle.get("title").strip()
        if not artist or not title:
            return

        targets = [f for f in COMPLETABLE_FIELDS if f in bundle.fields and not bundle.get(f)]
        if not targets:
            return

        result = self.lookup(artist, title, cfg)
        for field_name in targets:
            value = getattr(result, field_name, "")
            if not value:
                continue
            if field_name == "country":
                value = normalize_country(value)
            bundle.fields[field_name] = FieldValue(value, FieldSource.NETWORK, NETWORK_CONFIDENCE)

    # ------------------------------------------------------------------
    # 查询与缓存
    # ------------------------------------------------------------------
    def lookup(self, artist: str, title: str, cfg: Config) -> LookupResult:
        """查缓存 → 查网络；任何异常都降级为空结果并计入 failures。"""
        directory = mb_cache_dir()
        path = _cache_path(artist, title, directory)
        cached = self._read_cache(path, cfg.musicbrainz.cache_ttl_days)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached

        self.stats.queries += 1
        try:
            data = self._query(artist, title, cfg)
        except Exception as exc:  # noqa: BLE001 - 网络异常不得中断整批流程
            self.stats.failures += 1
            self.stats.errors.append(f"{artist} - {title}: {type(exc).__name__}")
            return LookupResult()

        self._write_cache(path, data)
        return data

    def _read_cache(self, path: Path, ttl_days: int) -> LookupResult | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        stored_at = raw.get("ts")
        if not isinstance(stored_at, (int, float)):
            return None
        if ttl_days > 0 and time.time() - float(stored_at) > ttl_days * 86400:
            return None
        data = raw.get("data")
        if not isinstance(data, dict):
            return None
        return LookupResult.from_dict(data)

    def _write_cache(self, path: Path, result: LookupResult) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"ts": time.time(), "data": result.as_dict()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:  # pragma: no cover - 缓存不可写不影响主流程
            self.stats.errors.append("cache_write_failed")

    def _query(self, artist: str, title: str, cfg: Config) -> LookupResult:
        """真实网络查询（测试里 monkeypatch `musicbrainzngs` 模块）。"""
        mb = importlib.import_module("musicbrainzngs")
        mb.set_useragent("renomate", __version__, cfg.musicbrainz.contact)
        mb.set_rate_limit(limit_or_interval=cfg.musicbrainz.rate_limit, new_requests=1)

        response = mb.search_recordings(artist=artist, recording=title, limit=1)
        recordings = response.get("recording-list") if isinstance(response, dict) else None
        if not recordings:
            return LookupResult()

        releases = recordings[0].get("release-list") or []
        if not releases:
            return LookupResult()

        release = releases[0]
        date = str(release.get("date", "") or "")
        year = "".join(ch for ch in date if ch.isdigit())[:4]
        return LookupResult(
            album=str(release.get("title", "") or ""),
            country=str(release.get("country", "") or ""),
            year=year,
        )


register_provider(MusicBrainzProvider())
