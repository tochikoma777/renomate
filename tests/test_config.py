"""配置加载 / 默认值 / XDG 路径的测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from renomate import config as config_module
from renomate.config import (
    TOML_TEXT,
    Config,
    config_path,
    default_config,
    init_config,
    journal_dir,
    load_config,
    mb_cache_dir,
    set_value,
)


def test_default_config_audio_profile() -> None:
    cfg = default_config()
    audio = cfg.profile("audio")
    assert audio.default_template == "{title} - {artist} - {album} - {country}"
    assert "mp3" in audio.extensions
    assert audio.required_fields == ["title", "artist"]
    assert audio.providers == ["audiotag", "path", "filename", "network", "default"]


def test_default_config_network_disabled() -> None:
    cfg = default_config()
    assert cfg.musicbrainz.enabled is False
    assert cfg.sanitize.platform == "windows"
    assert cfg.safety.require_confirm is True


def test_default_presets_are_available() -> None:
    cfg = default_config()
    assert cfg.presets["simple"] == "{title} - {artist}"
    assert cfg.presets["sorted"] == "{track|pad:2}. {title} - {artist}"
    assert cfg.presets["by_album"].startswith("{album|default:")
    assert cfg.presets["with_year"].endswith("{country_zh}")


def test_load_config_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert (
        cfg.profile("audio").default_template == default_config().profile("audio").default_template
    )
    assert cfg.source_path == tmp_path / "nope.toml"


def test_load_config_invalid_toml_falls_back(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[unclosed\n", encoding="utf-8")
    cfg = load_config(bad)
    assert cfg.general.default_profile == "audio"


def test_load_config_reads_sections(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[general]
default_profile = "audio"
max_batch = 12

[profiles.audio]
extensions = ["mp3"]
default_template = "{title}"

[presets]
mine = "{title} - {artist}"

[sanitize]
platform = "posix"
strategy = "drop"
max_name_length = 40
truncate_suffix = ""
lowercase_extension = true

[sanitize.replacement_map]
"/" = "_"

[safety]
extra_blocked = ["/mnt/d/secret"]
allow_missing_fields = true
require_confirm = false
confirm_token = "GO"

[musicbrainz]
enabled = true
contact = "me@example.com"
rate_limit = 2.0
cache_ttl_days = 3
timeout_seconds = 5

[ui]
ascii_icons = true
sort_by = "src"
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.general.max_batch == 12
    assert cfg.profile("audio").extensions == ["mp3"]
    assert cfg.profile("audio").default_template == "{title}"
    assert cfg.presets["mine"] == "{title} - {artist}"
    assert cfg.presets["simple"]  # 内置预设保留
    assert cfg.sanitize.platform == "posix"
    assert cfg.sanitize.strategy == "drop"
    assert cfg.sanitize.max_name_length == 40
    assert cfg.sanitize.lowercase_extension is True
    assert cfg.sanitize.replacement_map == {"/": "_"}
    assert cfg.safety.extra_blocked == ["/mnt/d/secret"]
    assert cfg.safety.allow_missing_fields is True
    assert cfg.safety.confirm_token == "GO"
    assert cfg.musicbrainz.enabled is True
    assert cfg.musicbrainz.rate_limit == 2.0
    assert cfg.musicbrainz.cache_ttl_days == 3
    assert cfg.musicbrainz.timeout_seconds == 5
    assert cfg.ui.ascii_icons is True
    assert cfg.ui.sort_by == "src"


def test_shipped_default_config_is_valid_toml(tmp_path: Path) -> None:
    """`config --init` 写出的模板必须能被自己解析，且保留默认值。"""
    path = init_config(tmp_path / "generated.toml")
    cfg = load_config(path)
    assert cfg.profile("audio").extensions == default_config().profile("audio").extensions
    assert cfg.musicbrainz.enabled is False
    assert cfg.sanitize.replacement_map["/"] == "／"
    assert cfg.sanitize.replacement_map["\\"] == "＼"
    assert cfg.sanitize.replacement_map['"'] == "＂"
    assert TOML_TEXT  # 模板本身非空，便于快照式回归


def test_init_config_is_idempotent_and_force(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    init_config(path)
    first = path.read_text(encoding="utf-8")
    path.write_text("modified", encoding="utf-8")
    init_config(path)
    assert path.read_text(encoding="utf-8") == "modified"
    init_config(path, force=True)
    assert path.read_text(encoding="utf-8") == first


def test_xdg_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-conf")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-data")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg-cache")
    assert config_path() == Path("/tmp/xdg-conf/renomate/config.toml")
    assert journal_dir() == Path("/tmp/xdg-data/renomate/journals")
    assert mb_cache_dir() == Path("/tmp/xdg-cache/renomate/mb")


def test_xdg_paths_fall_back_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    assert config_path() == tmp_path / ".config/renomate/config.toml"
    assert journal_dir() == tmp_path / ".local/share/renomate/journals"
    assert mb_cache_dir() == tmp_path / ".cache/renomate/mb"


def test_os_environ_is_untouched_after_xdg_helpers() -> None:
    """XDG helper 只读环境变量，不应写入。"""
    before = dict(os.environ)
    config_path()
    journal_dir()
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("general.max_batch", "42", 42),
        ("general.default_profile", "audio", "audio"),
        ("sanitize.platform", "posix", "posix"),
        ("sanitize.lowercase_extension", "true", True),
        ("safety.extra_blocked", "/a,/b", ["/a", "/b"]),
        ("safety.require_confirm", "no", False),
        ("musicbrainz.enabled", "yes", True),
        ("musicbrainz.rate_limit", "0.5", 0.5),
        ("ui.sort_by", "src", "src"),
    ],
)
def test_set_value_coerces_types(key: str, value: str, expected: object) -> None:
    cfg = set_value(default_config(), key, value)
    section, field_name = key.split(".", 1)
    section_obj = getattr(cfg, section)
    assert getattr(section_obj, field_name) == expected
    assert isinstance(cfg, Config)


def test_set_value_rejects_bad_keys() -> None:
    with pytest.raises(ValueError, match="section.key"):
        set_value(default_config(), "max_batch", "1")
    with pytest.raises(ValueError, match="未知配置段"):
        set_value(default_config(), "nope.x", "1")
    with pytest.raises(ValueError, match="没有字段"):
        set_value(default_config(), "general.nope", "1")


def test_profile_fallback_for_unknown_name() -> None:
    cfg = default_config()
    assert cfg.profile("does-not-exist").name == "audio"


def test_default_config_is_not_shared_mutable_state() -> None:
    a = default_config()
    b = default_config()
    a.presets["x"] = "y"
    a.safety.extra_blocked.append("/z")
    assert "x" not in b.presets
    assert b.safety.extra_blocked == []


def test_module_level_defaults_are_stable() -> None:
    assert config_module.GeneralConfig().max_batch == 5000
    assert config_module.SafetyConfig().confirm_token == "APPLY"
    assert config_module.MusicBrainzConfig().timeout_seconds == 8
