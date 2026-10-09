"""MusicBrainz 网络补全的测试（全部 monkeypatch，**禁止真实联网**）。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from renomate.chain import make_bundle, merge_field
from renomate.config import Config, default_config, mb_cache_dir
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers.musicbrainz import (
    LookupResult,
    MusicBrainzProvider,
    cache_key,
)

RECORDING_RESPONSE: dict[str, Any] = {
    "recording-list": [
        {
            "title": "稻香",
            "release-list": [
                {"title": "魔杰座", "country": "TW", "date": "2008-10-15"},
            ],
        }
    ]
}


class FakeMusicBrainz:
    """记录调用、可抛异常的假 musicbrainzngs 模块。"""

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self.response = response if response is not None else RECORDING_RESPONSE
        self.error = error
        self.useragent: tuple[Any, ...] = ()
        self.rate_limit: dict[str, Any] = {}
        self.searches: list[dict[str, Any]] = []

    def set_useragent(self, *args: Any) -> None:
        self.useragent = args

    def set_rate_limit(self, **kwargs: Any) -> None:
        self.rate_limit = kwargs

    def search_recordings(self, **kwargs: Any) -> dict[str, Any]:
        self.searches.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def fake_mb(monkeypatch: pytest.MonkeyPatch) -> FakeMusicBrainz:
    fake = FakeMusicBrainz()
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    return fake


def _bundle(tmp_path: Path, **fields: str) -> MetadataBundle:
    cfg = default_config()
    bundle = make_bundle(tmp_path / "song.mp3", "audio", cfg.profile("audio").fields)
    for key, value in fields.items():
        bundle.set(key, FieldValue(value, FieldSource.AUDIOTAG, 0.95))
    return bundle


def _enabled_cfg() -> Config:
    cfg = default_config()
    cfg.musicbrainz.enabled = True
    cfg.musicbrainz.contact = "me@example.com"
    return cfg


def test_network_is_off_by_default(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    bundle = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider = MusicBrainzProvider()
    provider.fill(bundle, default_config())
    assert provider.stats.queries == 0
    assert fake_mb.searches == []
    assert bundle.get("album") == ""


def test_enabled_provider_completes_missing_fields(
    tmp_path: Path, fake_mb: FakeMusicBrainz
) -> None:
    bundle = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider = MusicBrainzProvider()
    provider.fill(bundle, _enabled_cfg())
    assert bundle.get("album") == "魔杰座"
    assert bundle.get("country") == "TW"
    assert bundle.get("year") == "2008"
    assert provider.stats.queries == 1
    assert bundle.fields["album"] is not None
    assert bundle.fields["album"].source is FieldSource.NETWORK


def test_useragent_and_rate_limit(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    bundle = _bundle(tmp_path, title="稻香", artist="周杰伦")
    cfg = _enabled_cfg()
    cfg.musicbrainz.rate_limit = 1.0
    MusicBrainzProvider().fill(bundle, cfg)
    assert fake_mb.useragent[0] == "renomate"
    assert fake_mb.useragent[2] == "me@example.com"
    assert fake_mb.rate_limit == {"limit_or_interval": 1.0, "new_requests": 1}


def test_no_query_when_nothing_missing(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    bundle = _bundle(
        tmp_path, title="稻香", artist="周杰伦", album="魔杰座", country="TW", year="2008"
    )
    MusicBrainzProvider().fill(bundle, _enabled_cfg())
    assert fake_mb.searches == []


def test_no_query_without_artist_or_title(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    bundle = _bundle(tmp_path)
    MusicBrainzProvider().fill(bundle, _enabled_cfg())
    assert fake_mb.searches == []


def test_cache_hit_avoids_network(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    provider = MusicBrainzProvider()
    first = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider.fill(first, _enabled_cfg())
    second = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider.fill(second, _enabled_cfg())
    assert fake_mb.searches
    assert len(fake_mb.searches) == 1
    assert provider.stats.cache_hits == 1
    assert second.get("album") == "魔杰座"


def test_cache_file_layout(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    MusicBrainzProvider().fill(_bundle(tmp_path, title="稻香", artist="周杰伦"), _enabled_cfg())
    path = mb_cache_dir() / f"{cache_key('周杰伦', '稻香')}.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["data"]["album"] == "魔杰座"
    assert isinstance(payload["ts"], float)


def test_expired_cache_is_refetched(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    cfg = _enabled_cfg()
    cfg.musicbrainz.cache_ttl_days = 30
    path = mb_cache_dir() / f"{cache_key('周杰伦', '稻香')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": time.time() - 60 * 86400, "data": {"album": "旧专辑"}}),
        encoding="utf-8",
    )
    provider = MusicBrainzProvider()
    bundle = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider.fill(bundle, cfg)
    assert bundle.get("album") == "魔杰座"
    assert provider.stats.cache_hits == 0


def test_corrupt_cache_is_ignored(tmp_path: Path, fake_mb: FakeMusicBrainz) -> None:
    path = mb_cache_dir() / f"{cache_key('周杰伦', '稻香')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    provider = MusicBrainzProvider()
    bundle = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider.fill(bundle, _enabled_cfg())
    assert bundle.get("album") == "魔杰座"


def test_network_failure_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMusicBrainz(error=RuntimeError("timeout"))
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    provider = MusicBrainzProvider()
    bundle = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider.fill(bundle, _enabled_cfg())
    assert provider.stats.failures == 1
    assert provider.stats.queries == 1
    assert bundle.get("album") == ""
    assert provider.stats.errors


def test_empty_search_result_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMusicBrainz(response={"recording-list": []})
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    provider = MusicBrainzProvider()
    bundle = _bundle(tmp_path, title="稻香", artist="周杰伦")
    provider.fill(bundle, _enabled_cfg())
    assert bundle.get("album") == ""
    assert provider.stats.failures == 0


def test_supports_only_audio_profile() -> None:
    provider = MusicBrainzProvider()
    assert provider.supports("audio", Path("a.mp3")) is True
    assert provider.supports("pdf", Path("a.pdf")) is False


def test_lookup_result_roundtrip() -> None:
    result = LookupResult(album="A", country="CN", year="2000")
    assert LookupResult.from_dict(result.as_dict()) == result


def test_merge_field_prefers_existing_value() -> None:
    existing = FieldValue("x", FieldSource.AUDIOTAG, 0.9)
    lower = FieldValue("y", FieldSource.FILENAME, 0.5)
    higher = FieldValue("z", FieldSource.NETWORK, 0.95)
    assert merge_field(existing, lower) is existing
    assert merge_field(existing, higher) is higher
    assert merge_field(None, lower) is lower
    assert merge_field(existing, None) is existing
