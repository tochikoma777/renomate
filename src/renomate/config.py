"""TOML 配置加载 / 默认值 / XDG 路径。

读不到配置文件时使用内置默认值，**不报错**（SPEC 第 14 节）。
网络补全（MusicBrainz）默认关闭。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.11 以下需 tomli
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

TOML_TEXT = """\
# renomate 默认配置（由 `renomate config --init` 生成）

[general]
default_profile = "audio"
max_batch = 5000
follow_symlinks = false
include_hidden = false
multi_value_join = "、"
max_workers = 8

[profiles.audio]
extensions = ["mp3", "flac", "m4a", "ogg", "opus", "wav", "ape", "wma", "aiff", "aif"]
fields = ["title", "artist", "album", "country", "year", "track", "genre", "extra"]
required_fields = ["title", "artist"]
default_template = "{title} - {artist} - {album} - {country}"
providers = ["audiotag", "path", "filename", "network", "default"]

[defaults]
title = "未知标题"
artist = "未知歌手"
album = "未知专辑"
country = "CN"
year = ""
genre = ""
extra = ""

[presets]
simple = "{title} - {artist}"
sorted = "{track|pad:2}. {title} - {artist}"
by_album = "{album|default:未知专辑}/{track|pad:2} - {title} - {artist}"
with_year = "{title} - {artist} ({year}) - {country_zh}"

[sanitize]
platform = "windows"
strategy = "replace"
max_name_length = 180
truncate_suffix = "…"
lowercase_extension = false

[sanitize.replacement_map]
"/" = "／"
"\\\\" = "＼"
":" = "："
"?" = "？"
"*" = "＊"
"\\"" = "＂"
"<" = "＜"
">" = "＞"
"|" = "｜"

[safety]
extra_blocked = []
allow_missing_fields = false
require_confirm = true
confirm_token = "APPLY"

[musicbrainz]
enabled = false
contact = "you@example.com"
rate_limit = 1.0
cache_ttl_days = 30
timeout_seconds = 8

[ui]
ascii_icons = false
sort_by = "dst"
"""


def _as_str(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else default


def _as_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _as_float(data: dict[str, Any], key: str, default: float) -> float:
    value = data.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key)
    return value if isinstance(value, bool) else default


def _as_str_list(data: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = data.get(key)
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return list(default)


def _as_str_map(data: dict[str, Any], key: str, default: dict[str, str]) -> dict[str, str]:
    value = data.get(key)
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return dict(default)


@dataclass
class GeneralConfig:
    default_profile: str = "audio"
    max_batch: int = 5000
    follow_symlinks: bool = False
    include_hidden: bool = False
    multi_value_join: str = "、"
    max_workers: int = 8


@dataclass
class ProfileConfig:
    name: str = "audio"
    extensions: list[str] = field(
        default_factory=lambda: [
            "mp3",
            "flac",
            "m4a",
            "ogg",
            "opus",
            "wav",
            "ape",
            "wma",
            "aiff",
            "aif",
        ]
    )
    fields: list[str] = field(
        default_factory=lambda: [
            "title",
            "artist",
            "album",
            "country",
            "year",
            "track",
            "genre",
            "extra",
        ]
    )
    required_fields: list[str] = field(default_factory=lambda: ["title", "artist"])
    default_template: str = "{title} - {artist} - {album} - {country}"
    providers: list[str] = field(
        default_factory=lambda: ["audiotag", "path", "filename", "network", "default"]
    )


@dataclass
class SanitizeConfig:
    platform: str = "windows"  # windows | posix
    strategy: str = "replace"  # replace | drop | underscore
    max_name_length: int = 180
    truncate_suffix: str = "…"
    lowercase_extension: bool = False
    replacement_map: dict[str, str] = field(
        default_factory=lambda: {
            "/": "／",
            "\\": "＼",
            ":": "：",
            "?": "？",
            "*": "＊",
            '"': "＂",
            "<": "＜",
            ">": "＞",
            "|": "｜",
        }
    )


@dataclass
class SafetyConfig:
    extra_blocked: list[str] = field(default_factory=list)
    allow_missing_fields: bool = False
    require_confirm: bool = True
    confirm_token: str = "APPLY"


@dataclass
class MusicBrainzConfig:
    enabled: bool = False  # 默认关闭
    contact: str = "you@example.com"  # User-Agent 的一部分，必填
    rate_limit: float = 1.0
    cache_ttl_days: int = 30
    timeout_seconds: int = 8


@dataclass
class UIConfig:
    ascii_icons: bool = False
    sort_by: str = "dst"  # dst | src


DEFAULT_FIELD_VALUES: dict[str, str] = {
    "title": "未知标题",
    "artist": "未知歌手",
    "album": "未知专辑",
    "country": "CN",
    "year": "",
    "genre": "",
    "extra": "",
}


@dataclass
class DefaultsConfig:
    """各字段的缺省兜底值（F2「默认值兜底」，置信度 0.1）。"""

    values: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FIELD_VALUES))

    def get(self, field_name: str) -> str:
        return self.values.get(field_name, "")


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)
    presets: dict[str, str] = field(default_factory=dict)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    sanitize: SanitizeConfig = field(default_factory=SanitizeConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    musicbrainz: MusicBrainzConfig = field(default_factory=MusicBrainzConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    source_path: Path | None = None

    def profile(self, name: str | None = None) -> ProfileConfig:
        """取指定 Profile 配置；不存在时回落到内置 audio 配置。"""
        key = name or self.general.default_profile
        builtin = default_config().profiles
        if key in self.profiles:
            return self.profiles[key]
        if key in builtin:
            return builtin[key]
        return builtin["audio"]


def xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    return Path(raw) if raw else Path.home() / ".config"


def xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    return Path(raw) if raw else Path.home() / ".local" / "share"


def xdg_cache_home() -> Path:
    raw = os.environ.get("XDG_CACHE_HOME")
    return Path(raw) if raw else Path.home() / ".cache"


def config_path() -> Path:
    return xdg_config_home() / "renomate" / "config.toml"


def journal_dir() -> Path:
    """SPEC 6.1：日志目录 `$XDG_DATA_HOME/renomate/journals/`。"""
    return xdg_data_home() / "renomate" / "journals"


def mb_cache_dir() -> Path:
    """SPEC 7.4：MusicBrainz 缓存 `$XDG_CACHE_HOME/renomate/mb/`。"""
    return xdg_cache_home() / "renomate" / "mb"


DEFAULT_PRESETS: dict[str, str] = {
    "simple": "{title} - {artist}",
    "sorted": "{track|pad:2}. {title} - {artist}",
    "by_album": "{album|default:未知专辑}/{track|pad:2} - {title} - {artist}",
    "with_year": "{title} - {artist} ({year}) - {country_zh}",
}


def default_config() -> Config:
    """内置默认配置（不读取磁盘）。"""
    audio = ProfileConfig(name="audio")
    return Config(
        general=GeneralConfig(),
        profiles={"audio": audio},
        presets=dict(DEFAULT_PRESETS),
        sanitize=SanitizeConfig(),
        safety=SafetyConfig(),
        musicbrainz=MusicBrainzConfig(),
        ui=UIConfig(),
    )


def _parse_config(data: dict[str, Any]) -> Config:
    cfg = default_config()

    general = data.get("general")
    if isinstance(general, dict):
        cfg.general = GeneralConfig(
            default_profile=_as_str(general, "default_profile", cfg.general.default_profile),
            max_batch=_as_int(general, "max_batch", cfg.general.max_batch),
            follow_symlinks=_as_bool(general, "follow_symlinks", cfg.general.follow_symlinks),
            include_hidden=_as_bool(general, "include_hidden", cfg.general.include_hidden),
            multi_value_join=_as_str(general, "multi_value_join", cfg.general.multi_value_join),
            max_workers=_as_int(general, "max_workers", cfg.general.max_workers),
        )

    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        for name, body in profiles.items():
            if not isinstance(body, dict):
                continue
            base = cfg.profiles.get(str(name), default_config().profile("audio"))
            cfg.profiles[str(name)] = ProfileConfig(
                name=str(name),
                extensions=_as_str_list(body, "extensions", base.extensions),
                fields=_as_str_list(body, "fields", base.fields),
                required_fields=_as_str_list(body, "required_fields", base.required_fields),
                default_template=_as_str(body, "default_template", base.default_template),
                providers=_as_str_list(body, "providers", base.providers),
            )

    presets = data.get("presets")
    if isinstance(presets, dict):
        cfg.presets.update({str(k): str(v) for k, v in presets.items()})

    defaults = data.get("defaults")
    if isinstance(defaults, dict):
        cfg.defaults = DefaultsConfig(
            values={**DEFAULT_FIELD_VALUES, **{str(k): str(v) for k, v in defaults.items()}}
        )

    sanitize = data.get("sanitize")
    if isinstance(sanitize, dict):
        cfg.sanitize = SanitizeConfig(
            platform=_as_str(sanitize, "platform", cfg.sanitize.platform),
            strategy=_as_str(sanitize, "strategy", cfg.sanitize.strategy),
            max_name_length=_as_int(sanitize, "max_name_length", cfg.sanitize.max_name_length),
            truncate_suffix=_as_str(sanitize, "truncate_suffix", cfg.sanitize.truncate_suffix),
            lowercase_extension=_as_bool(
                sanitize, "lowercase_extension", cfg.sanitize.lowercase_extension
            ),
            replacement_map=_as_str_map(sanitize, "replacement_map", cfg.sanitize.replacement_map),
        )

    safety = data.get("safety")
    if isinstance(safety, dict):
        cfg.safety = SafetyConfig(
            extra_blocked=_as_str_list(safety, "extra_blocked", cfg.safety.extra_blocked),
            allow_missing_fields=_as_bool(
                safety, "allow_missing_fields", cfg.safety.allow_missing_fields
            ),
            require_confirm=_as_bool(safety, "require_confirm", cfg.safety.require_confirm),
            confirm_token=_as_str(safety, "confirm_token", cfg.safety.confirm_token),
        )

    musicbrainz = data.get("musicbrainz")
    if isinstance(musicbrainz, dict):
        cfg.musicbrainz = MusicBrainzConfig(
            enabled=_as_bool(musicbrainz, "enabled", cfg.musicbrainz.enabled),
            contact=_as_str(musicbrainz, "contact", cfg.musicbrainz.contact),
            rate_limit=_as_float(musicbrainz, "rate_limit", cfg.musicbrainz.rate_limit),
            cache_ttl_days=_as_int(musicbrainz, "cache_ttl_days", cfg.musicbrainz.cache_ttl_days),
            timeout_seconds=_as_int(
                musicbrainz, "timeout_seconds", cfg.musicbrainz.timeout_seconds
            ),
        )

    ui = data.get("ui")
    if isinstance(ui, dict):
        cfg.ui = UIConfig(
            ascii_icons=_as_bool(ui, "ascii_icons", cfg.ui.ascii_icons),
            sort_by=_as_str(ui, "sort_by", cfg.ui.sort_by),
        )

    return cfg


def load_config(path: Path | None = None) -> Config:
    """加载配置；文件缺失或解析失败时用内置默认值，绝不抛异常。"""
    target = path or config_path()
    cfg = default_config()
    cfg.source_path = target
    try:
        raw = target.read_bytes()
    except OSError:
        return cfg
    try:
        data: object = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    parsed = _parse_config(data)
    parsed.source_path = target
    return parsed


def init_config(path: Path | None = None, *, force: bool = False) -> Path:
    """写入带注释的默认配置；已存在且不 force 时不覆盖。"""
    target = path or config_path()
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TOML_TEXT, encoding="utf-8")
    return target


def set_value(cfg: Config, key: str, value: str) -> Config:
    """按 `section.key=value` 就地修改配置（供 `renomate config --set` 使用）。"""
    if "." not in key:
        msg = f"配置项格式应为 section.key，收到: {key!r}"
        raise ValueError(msg)
    section, field_name = key.split(".", 1)
    mapping: dict[str, Any] = {
        "general": cfg.general,
        "sanitize": cfg.sanitize,
        "safety": cfg.safety,
        "musicbrainz": cfg.musicbrainz,
        "ui": cfg.ui,
    }
    target_obj = mapping.get(section)
    if target_obj is None:
        msg = f"未知配置段: {section!r}（可选: {', '.join(sorted(mapping))}）"
        raise ValueError(msg)
    if not hasattr(target_obj, field_name):
        msg = f"配置段 {section!r} 没有字段 {field_name!r}"
        raise ValueError(msg)
    current = getattr(target_obj, field_name)
    if isinstance(current, bool):
        coerced: Any = value.strip().casefold() in {"1", "true", "yes", "on"}
    elif isinstance(current, int):
        coerced = int(value)
    elif isinstance(current, float):
        coerced = float(value)
    elif isinstance(current, list):
        coerced = [part.strip() for part in value.split(",") if part.strip()]
    else:
        coerced = value
    setattr(target_obj, field_name, coerced)
    return cfg
