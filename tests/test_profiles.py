"""Profile 注册表的测试（SPEC 第 15 节扩展点）。"""

from __future__ import annotations

import pytest

from renomate.config import Config, ProfileConfig, default_config
from renomate.profiles import (
    PROFILES,
    Profile,
    UnknownProfileError,
    get_profile,
    list_profiles,
    profile_from_config,
    register_profile,
    unregister_profile,
)


def test_builtin_audio_profile_is_registered() -> None:
    assert "audio" in PROFILES
    audio = get_profile("audio")
    assert audio.name == "audio"
    assert "mp3" in audio.extensions
    assert audio.required_fields == ["title", "artist"]
    assert audio.providers[0] == "audiotag"


def test_extension_matching_is_case_insensitive_and_dot_tolerant() -> None:
    audio = get_profile("audio")
    assert audio.matches_extension(".MP3") is True
    assert audio.matches_extension("flac") is True
    assert audio.matches_extension(".zip") is False


def test_get_profile_reads_config_profiles() -> None:
    cfg = default_config()
    cfg.profiles["podcast"] = ProfileConfig(
        name="podcast", extensions=["mp3"], fields=["title"], default_template="{title}"
    )
    profile = get_profile("podcast", cfg)
    assert profile.extensions == ["mp3"]
    assert "podcast" in list_profiles(cfg)


def test_get_profile_falls_back_to_default_name() -> None:
    cfg = default_config()
    assert get_profile(None, cfg).name == "audio"


def test_unknown_profile_raises() -> None:
    with pytest.raises(UnknownProfileError, match="未知 Profile"):
        get_profile("nope", default_config())


def test_register_and_unregister_custom_profile() -> None:
    custom = Profile(
        name="pdf",
        extensions=["pdf"],
        fields=["title", "author", "year"],
        required_fields=["title"],
        default_template="{title} - {author}",
        providers=["pdftag", "filename", "default"],
    )
    register_profile(custom)
    try:
        assert get_profile("pdf").default_template == "{title} - {author}"
        assert list_profiles() == ["audio", "pdf"]
    finally:
        unregister_profile("pdf")
    assert "pdf" not in PROFILES


def test_unregister_missing_is_noop() -> None:
    before = dict(PROFILES)
    unregister_profile("does-not-exist")
    assert PROFILES == before


def test_profile_from_config_roundtrip() -> None:
    cfg_profile = ProfileConfig(name="books", extensions=["epub"], fields=["title"])
    profile = profile_from_config(cfg_profile)
    assert profile.name == "books"
    assert profile.extensions == ["epub"]
    assert profile.fields == ["title"]


def test_registering_the_same_name_overwrites() -> None:
    first = Profile(name="x", extensions=["a"])
    second = Profile(name="x", extensions=["b"])
    register_profile(first)
    register_profile(second)
    try:
        assert get_profile("x").extensions == ["b"]
    finally:
        unregister_profile("x")


def test_list_profiles_is_sorted_and_unique() -> None:
    cfg = Config()
    names = list_profiles(cfg)
    assert names == sorted(set(names))
    assert "audio" in names
