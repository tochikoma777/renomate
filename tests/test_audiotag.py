"""内嵌标签 Provider 的测试（mutagen 可选依赖，动态 import）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from renomate import providers
from renomate.chain import make_bundle
from renomate.config import Config, default_config
from renomate.models import FieldSource
from renomate.providers import audiotag


def _bundle(path: Path) -> Any:
    cfg = default_config()
    return make_bundle(path, "audio", cfg.profile("audio").fields)


def test_reads_mp3_tags(music_lib: Path) -> None:
    bundle = _bundle(music_lib / "周杰伦" / "魔杰座" / "01 稻香.mp3")
    audiotag.AudioTagProvider().fill(bundle, default_config())
    assert bundle.get("title") == "稻香"
    assert bundle.get("artist") == "周杰伦"
    assert bundle.get("album") == "魔杰座"
    assert bundle.get("year") == "2008"
    assert bundle.get("track") == "1"
    assert bundle.fields["title"] is not None
    assert bundle.fields["title"].source is FieldSource.AUDIOTAG
    assert bundle.fields["title"].confidence == 0.95


def test_multi_value_join(tmp_path: Path) -> None:
    from tests.conftest import tag_flac

    path = tmp_path / "duet.flac"
    tag_flac(path, title="K歌之王", artist=["陈奕迅", "张学友"], album="打得火热")
    bundle = _bundle(path)
    cfg = default_config()
    audiotag.AudioTagProvider().fill(bundle, cfg)
    assert bundle.get("artist") == "陈奕迅、张学友"


def test_broken_file_degrades_gracefully(music_lib: Path) -> None:
    provider = audiotag.AudioTagProvider()
    bundle = _bundle(music_lib / "broken.mp3")
    provider.fill(bundle, default_config())
    assert bundle.get("title") == ""
    assert provider.last_error != ""


def test_unsupported_suffix_is_skipped(tmp_path: Path) -> None:
    provider = audiotag.AudioTagProvider()
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    assert provider.supports("audio", target) is False
    bundle = _bundle(target)
    provider.fill(bundle, default_config())
    assert bundle.get("title") == ""


def test_missing_mutagen_degrades(music_lib: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_import(name: str) -> Any:
        if name == "mutagen":
            msg = "no module named mutagen"
            raise ImportError(msg)
        return __import__(name)

    monkeypatch.setattr(audiotag.importlib, "import_module", fake_import)
    provider = audiotag.AudioTagProvider()
    bundle = _bundle(music_lib / "周杰伦" / "魔杰座" / "01 稻香.mp3")
    provider.fill(bundle, default_config())
    assert bundle.get("title") == ""
    assert "mutagen" in provider.last_error


def test_itunes_country_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """MP4 的 `----:com.apple.iTunes:COUNTRY` freeform 回退。"""

    class FakeFreeForm:
        def __init__(self, text: bytes) -> None:
            self._text = text

        def __bytes__(self) -> bytes:
            return self._text

        def __str__(self) -> str:
            return self._text.decode("utf-8")

    class FakeTags(dict):  # type: ignore[type-arg]
        pass

    class FakeAudio:
        def __init__(self) -> None:
            self.tags: dict[str, Any] = {
                "title": ["歌名"],
                "artist": ["歌手"],
                "----:com.apple.iTunes:COUNTRY": [FakeFreeForm(b"TW")],
            }

    class FakeMutagen:
        @staticmethod
        def File(path: Path, easy: bool = False) -> FakeAudio:  # noqa: N802
            return FakeAudio()

    monkeypatch.setattr(audiotag, "_load_mutagen", lambda: FakeMutagen)
    provider = audiotag.AudioTagProvider()
    target = tmp_path / "x.m4a"
    target.write_bytes(b"x")
    bundle = _bundle(target)
    provider.fill(bundle, default_config())
    assert bundle.get("country") == "TW"
    assert bundle.get("title") == "歌名"


def test_txxx_country_and_language_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ID3 TXXX:COUNTRY 优先；没有时用 LANGUAGE 推断。"""

    class FakeFrame:
        def __init__(self, text: list[str]) -> None:
            self.text = text

    class FakeID3(dict):  # type: ignore[type-arg]
        def getall(self, key: str) -> list[FakeFrame]:
            if key == "TXXX:COUNTRY":
                return [FakeFrame(["中国"])]
            return []

    class FakeAudio:
        def __init__(self, tags: dict[str, Any] | None) -> None:
            self.tags = tags

    class FakeMutagen:
        calls: list[bool] = []

        @staticmethod
        def File(path: Path, easy: bool = False) -> FakeAudio:  # noqa: N802
            FakeMutagen.calls.append(easy)
            if easy:
                return FakeAudio({"title": ["稻香"], "artist": ["周杰伦"]})
            return FakeAudio(FakeID3({"TIT2": FakeFrame(["稻香"])}))

    monkeypatch.setattr(audiotag, "_load_mutagen", lambda: FakeMutagen)
    provider = audiotag.AudioTagProvider()
    target = tmp_path / "x.mp3"
    target.write_bytes(b"x")
    bundle = _bundle(target)
    provider.fill(bundle, default_config())
    assert bundle.get("country") == "CN"


def test_language_inference_without_country_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeAudio:
        def __init__(self) -> None:
            self.tags: dict[str, Any] = {"title": ["稻香"], "language": ["粤语"]}

    class FakeMutagen:
        @staticmethod
        def File(path: Path, easy: bool = False) -> FakeAudio:  # noqa: N802
            return FakeAudio()

    monkeypatch.setattr(audiotag, "_load_mutagen", lambda: FakeMutagen)
    provider = audiotag.AudioTagProvider()
    target = tmp_path / "x.mp3"
    target.write_bytes(b"x")
    bundle = _bundle(target)
    provider.fill(bundle, default_config())
    assert bundle.get("country") == "HK"


def test_year_normalization(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeAudio:
        def __init__(self) -> None:
            self.tags: dict[str, Any] = {"date": ["2010-05-01"], "tracknumber": ["3/12"]}

    class FakeMutagen:
        @staticmethod
        def File(path: Path, easy: bool = False) -> FakeAudio:  # noqa: N802
            return FakeAudio()

    monkeypatch.setattr(audiotag, "_load_mutagen", lambda: FakeMutagen)
    provider = audiotag.AudioTagProvider()
    target = tmp_path / "x.mp3"
    target.write_bytes(b"x")
    bundle = _bundle(target)
    provider.fill(bundle, default_config())
    assert bundle.get("year") == "2010"
    assert bundle.get("track") == "3"


def test_registry_contains_builtin_providers() -> None:
    assert "audiotag" in providers.PROVIDER_REGISTRY
    assert "filename" in providers.PROVIDER_REGISTRY
    assert "path" in providers.PROVIDER_REGISTRY
    assert "network" in providers.PROVIDER_REGISTRY
    assert "default" in providers.PROVIDER_REGISTRY
    assert "manual" in providers.PROVIDER_REGISTRY


def test_provider_is_registered_singleton() -> None:
    assert isinstance(providers.PROVIDER_REGISTRY["audiotag"], audiotag.AudioTagProvider)


def test_config_is_respected_for_joiner(tmp_path: Path) -> None:
    from tests.conftest import tag_flac

    path = tmp_path / "duet2.flac"
    tag_flac(path, title="t", artist=["a", "b"])
    cfg = default_config()
    cfg.general.multi_value_join = " & "
    bundle = _bundle(path)
    audiotag.AudioTagProvider().fill(bundle, cfg)
    assert bundle.get("artist") == "a & b"


def test_suffix_support_matrix() -> None:
    provider = audiotag.AudioTagProvider()
    assert provider.supports("audio", Path("x.MP3")) is True
    assert provider.supports("audio", Path("x.flac")) is True
    assert provider.supports("audio", Path("x.zip")) is False


def test_empty_config_object_is_enough() -> None:
    """fill 只依赖 Config 的 multi_value_join，缺省配置即可。"""
    provider = audiotag.AudioTagProvider()
    assert isinstance(provider.last_error, str)
    assert Config().general.multi_value_join == "、"
