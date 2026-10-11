"""Provider 链与字段合并策略的测试（SPEC 7.1）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from renomate.chain import ProviderChain, make_bundle, merge_field
from renomate.config import Config, default_config
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers import PROVIDER_REGISTRY, register_provider, unregister_provider
from tests.test_musicbrainz import FakeMusicBrainz


class DummyProvider:
    """测试用 Provider：按给定置信度填字段。"""

    def __init__(
        self, name: str, values: dict[str, str], confidence: float, profile: str = "audio"
    ):
        self.name = name
        self.values = values
        self.confidence = confidence
        self.only_profile = profile
        self.calls = 0

    def supports(self, profile: str, path: Path) -> bool:
        return profile == self.only_profile

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        self.calls += 1
        for key, value in self.values.items():
            if key in bundle.fields:
                bundle.fields[key] = FieldValue(value, FieldSource.FILENAME, self.confidence)


def _bundle(path: str = "/music/a.mp3", profile: str = "audio") -> MetadataBundle:
    cfg = default_config()
    return make_bundle(Path(path), profile, cfg.profile(profile).fields)


def test_default_order_is_spec_order() -> None:
    from renomate.providers import PROVIDER_ORDER

    assert PROVIDER_ORDER == ["manual", "audiotag", "path", "filename", "network", "default"]


def test_chain_fills_from_filename_when_no_tags(tmp_path: Path) -> None:
    target = tmp_path / "周杰伦 - 稻香 [320K].mp3"
    target.write_bytes(b"x")
    cfg = default_config()
    bundle = make_bundle(target, "audio", cfg.profile("audio").fields)
    ProviderChain(cfg).fill(bundle)
    assert bundle.get("title") == "稻香"
    assert bundle.get("artist") == "周杰伦"
    assert bundle.fields["title"] is not None
    assert bundle.fields["title"].source is FieldSource.FILENAME


def test_audiotag_wins_over_path_and_filename(music_lib: Path) -> None:
    cfg = default_config()
    target = music_lib / "周杰伦" / "魔杰座" / "01 稻香.mp3"
    bundle = make_bundle(target, "audio", cfg.profile("audio").fields)
    ProviderChain(cfg).fill(bundle)
    assert bundle.fields["title"] is not None
    assert bundle.fields["title"].source is FieldSource.AUDIOTAG
    assert bundle.get("title") == "稻香"
    assert bundle.get("album") == "魔杰座"


def test_path_provider_infers_album_from_directory(tmp_path: Path) -> None:
    directory = tmp_path / "陈奕迅" / "打得火热"
    directory.mkdir(parents=True)
    # 文件名不具备 `Artist - Title` 结构 → 歌手/专辑只能来自目录（SPEC 7.1 顺序 2）
    target = directory / "unknown.mp3"
    target.write_bytes(b"x")
    cfg = default_config()
    bundle = make_bundle(target, "audio", cfg.profile("audio").fields)
    ProviderChain(cfg, providers=["audiotag", "path", "filename"]).fill(bundle)
    assert bundle.get("title") == "unknown"
    assert bundle.get("album") == "打得火热"
    assert bundle.get("artist") == "陈奕迅"


def test_locked_value_is_not_overwritten() -> None:
    bundle = _bundle()
    bundle.set("title", FieldValue("手工标题", FieldSource.MANUAL, 1.0, locked=True))
    cfg = default_config()
    ProviderChain(cfg).fill(bundle)
    assert bundle.get("title") == "手工标题"


def test_manual_values_beat_everything(music_lib: Path) -> None:
    cfg = default_config()
    target = music_lib / "周杰伦" / "魔杰座" / "01 稻香.mp3"
    bundle = make_bundle(target, "audio", cfg.profile("audio").fields)
    ProviderChain(cfg, manual={"artist": "Jay Chou"}).fill(bundle)
    assert bundle.get("artist") == "Jay Chou"
    assert bundle.fields["artist"] is not None
    assert bundle.fields["artist"].locked is True


def test_low_confidence_does_not_override_high() -> None:
    """先到先得：后到的低置信度值不会覆盖已有值。"""
    high = DummyProvider("p1", {"title": "高置信"}, 0.9)
    low = DummyProvider("p2", {"title": "低置信"}, 0.3)
    register_provider(high)
    register_provider(low)
    try:
        cfg = default_config()
        bundle = _bundle()
        ProviderChain(cfg, providers=["p1", "p2"]).fill(bundle)
        assert bundle.get("title") == "高置信"
    finally:
        unregister_provider("p1")
        unregister_provider("p2")


def test_high_confidence_upgrades_low() -> None:
    """置信度更高的值可以升级低置信度占位值。"""
    low = DummyProvider("p1", {"title": "低置信"}, 0.3)
    high = DummyProvider("p2", {"title": "高置信"}, 0.9)
    register_provider(low)
    register_provider(high)
    try:
        cfg = default_config()
        bundle = _bundle()
        ProviderChain(cfg, providers=["p1", "p2"]).fill(bundle)
        assert bundle.get("title") == "高置信"
    finally:
        unregister_provider("p1")
        unregister_provider("p2")


def test_locked_new_value_can_override_unlocked_old_value() -> None:
    old = merge_field(FieldValue("旧", FieldSource.FILENAME, 0.9), None)
    locked_new = FieldValue("新", FieldSource.MANUAL, 1.0, locked=True)
    assert merge_field(old, locked_new) is locked_new


def test_default_provider_fills_remaining_fields() -> None:
    cfg = default_config()
    bundle = _bundle()
    ProviderChain(cfg, providers=["default"]).fill(bundle)
    assert bundle.get("title") == "未知标题"
    assert bundle.get("artist") == "未知歌手"
    assert bundle.get("country") == "CN"
    assert bundle.fields["title"] is not None
    assert bundle.fields["title"].confidence == 0.10


def test_custom_defaults_are_used() -> None:
    cfg = default_config()
    cfg.defaults.values["title"] = "无题"
    bundle = _bundle()
    ProviderChain(cfg, providers=["default"]).fill(bundle)
    assert bundle.get("title") == "无题"


def test_network_provider_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMusicBrainz()
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    cfg = default_config()
    cfg.musicbrainz.enabled = False
    bundle = _bundle()
    bundle.set("title", FieldValue("稻香", FieldSource.FILENAME, 0.8))
    bundle.set("artist", FieldValue("周杰伦", FieldSource.FILENAME, 0.8))
    chain = ProviderChain(cfg, use_network=True)
    assert chain.use_network is False
    chain.fill(bundle)
    assert fake.searches == []


def test_network_provider_used_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """文件没有 ALBUM/PATH 来源时，缺失的 album 由网络补全。"""
    fake = FakeMusicBrainz()
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    cfg = default_config()
    cfg.musicbrainz.enabled = True
    bundle = make_bundle(tmp_path / "a.mp3", "audio", cfg.profile("audio").fields)
    bundle.path.write_bytes(b"x")
    bundle.set("title", FieldValue("稻香", FieldSource.FILENAME, 0.8))
    bundle.set("artist", FieldValue("周杰伦", FieldSource.FILENAME, 0.8))
    chain = ProviderChain(cfg, use_network=True, providers=["filename", "network"])
    assert chain.use_network is True
    chain.fill(bundle)
    assert bundle.get("album") == "魔杰座"
    assert bundle.get("country") == "TW"
    assert len(fake.searches) == 1


def test_network_does_not_override_known_album(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """album 已知时，网络只补其余缺失字段，不能改写 album。"""
    fake = FakeMusicBrainz()
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    cfg = default_config()
    cfg.musicbrainz.enabled = True
    bundle = make_bundle(tmp_path / "a.mp3", "audio", cfg.profile("audio").fields)
    bundle.set("title", FieldValue("稻香", FieldSource.FILENAME, 0.8))
    bundle.set("artist", FieldValue("周杰伦", FieldSource.FILENAME, 0.8))
    bundle.set("album", FieldValue("已有专辑", FieldSource.AUDIOTAG, 0.95))
    ProviderChain(cfg, use_network=True, providers=["filename", "network"]).fill(bundle)
    assert bundle.get("album") == "已有专辑"
    assert bundle.get("country") == "TW"  # 其它缺失字段仍被补齐


def test_fill_all_returns_stats(tmp_path: Path) -> None:
    cfg = default_config()
    bundles = [
        make_bundle(tmp_path / f"{i}.mp3", "audio", cfg.profile("audio").fields) for i in range(4)
    ]
    for bundle in bundles:
        bundle.path.write_bytes(b"x")
    stats = ProviderChain(cfg, providers=["default"]).fill_all(bundles)
    assert stats.total == 4
    assert stats.network_failed == 0
    assert stats.network_queries == 0


def test_fill_all_on_empty_list() -> None:
    stats = ProviderChain(default_config()).fill_all([])
    assert stats.total == 0


def test_fill_all_collects_network_stats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeMusicBrainz(error=RuntimeError("boom"))
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    cfg = default_config()
    cfg.musicbrainz.enabled = True
    bundles = []
    for i in range(3):
        bundle = make_bundle(tmp_path / f"{i}.mp3", "audio", cfg.profile("audio").fields)
        bundle.path.write_bytes(b"x")
        bundle.set("title", FieldValue("稻香", FieldSource.FILENAME, 0.8))
        bundle.set("artist", FieldValue("周杰伦", FieldSource.FILENAME, 0.8))
        bundles.append(bundle)
    stats = ProviderChain(cfg, use_network=True).fill_all(bundles)
    assert stats.network_failed == 3
    assert stats.network_queries == 3
    assert len(stats.errors) == 3


def test_order_excludes_unknown_and_disabled_providers() -> None:
    cfg = default_config()
    chain = ProviderChain(cfg, providers=["audiotag", "nope", "network"])
    assert chain.order() == ["audiotag"]


def test_provider_not_supporting_profile_is_skipped() -> None:
    provider = DummyProvider("onlyaudio", {"title": "x"}, 0.9, profile="audio")
    register_provider(provider)
    try:
        cfg = default_config()
        bundle = _bundle(profile="audio")
        ProviderChain(cfg, providers=["onlyaudio"]).fill(bundle)
        assert provider.calls == 1
        assert bundle.get("title") == "x"
    finally:
        unregister_provider("onlyaudio")


def test_make_bundle_initializes_fields_as_none() -> None:
    bundle = make_bundle(Path("a.mp3"), "audio", ["title", "artist"])
    assert bundle.fields == {"title": None, "artist": None}
    assert bundle.missing() == ["title", "artist"]


def test_country_zh_is_derived(tmp_path: Path) -> None:
    cfg = default_config()
    profile_fields = cfg.profile("audio").fields + ["country_zh"]
    bundle = make_bundle(tmp_path / "a.mp3", "audio", profile_fields)
    bundle.set("country", FieldValue("CN", FieldSource.AUDIOTAG, 0.7))
    ProviderChain(cfg, providers=["default"]).fill(bundle)
    assert bundle.get("country_zh") == "中国"


def test_country_zh_empty_without_country() -> None:
    cfg = default_config()
    bundle = make_bundle(Path("a.mp3"), "audio", cfg.profile("audio").fields + ["country_zh"])
    ProviderChain(cfg, providers=["filename"]).fill(bundle)
    assert bundle.get("country") == ""
    assert bundle.get("country_zh") == ""


def test_registry_is_mutable_for_plugins() -> None:
    provider = DummyProvider("plugin", {"title": "plugin"}, 0.99)
    register_provider(provider)
    assert PROVIDER_REGISTRY["plugin"] is provider
    unregister_provider("plugin")
    assert "plugin" not in PROVIDER_REGISTRY


def test_merge_field_locked_protection() -> None:
    locked = FieldValue("keep", FieldSource.MANUAL, 1.0, locked=True)
    assert merge_field(locked, FieldValue("new", FieldSource.AUDIOTAG, 0.95)) is locked


def test_missing_fields_are_reported() -> None:
    bundle = _bundle()
    bundle.set("title", FieldValue("", FieldSource.FILENAME, 0.5))
    assert bundle.missing() == [
        "title",
        "artist",
        "album",
        "country",
        "year",
        "track",
        "genre",
        "extra",
    ]


@pytest.mark.parametrize("workers", [1, 4])
def test_thread_pool_size_is_configurable(workers: int, tmp_path: Path) -> None:
    cfg = default_config()
    cfg.general.max_workers = workers
    bundles = [
        make_bundle(tmp_path / f"{i}.mp3", "audio", cfg.profile("audio").fields) for i in range(3)
    ]
    for bundle in bundles:
        bundle.path.write_bytes(b"x")
    stats = ProviderChain(cfg, providers=["default"]).fill_all(bundles)
    assert stats.total == 3


def test_any_provider_object_works() -> None:
    """Provider 只需满足协议：name / supports / fill。"""
    provider: Any = DummyProvider("any", {"title": "ok"}, 1.0)
    register_provider(provider)
    try:
        bundle = _bundle()
        ProviderChain(default_config(), providers=["any"]).fill(bundle)
        assert bundle.get("title") == "ok"
    finally:
        unregister_provider("any")
