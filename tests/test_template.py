"""模板渲染引擎的测试（SPEC 8）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from renomate.chain import make_bundle
from renomate.config import SanitizeConfig, default_config
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.template import (
    TemplateError,
    build_filename,
    list_filters,
    render,
    template_fields,
    truncate_name,
    validate_template,
)

FIELDS = ["title", "artist", "album", "country", "year", "track", "genre", "extra"]


def _bundle(path: str = "a.mp3", **values: str) -> MetadataBundle:
    bundle = make_bundle(Path(path), "audio", FIELDS)
    for key, value in values.items():
        bundle.set(key, FieldValue(value, FieldSource.AUDIOTAG, 0.95))
    return bundle


def _render(template: str, bundle: MetadataBundle, **kwargs: object) -> str:
    result = render(template, bundle, sanitize_cfg=default_config().sanitize, **kwargs)  # type: ignore[arg-type]
    return result.name


def test_plain_placeholders() -> None:
    bundle = _bundle(title="稻香", artist="周杰伦", album="魔杰座", country="CN")
    assert (
        _render("{title} - {artist} - {album} - {country}", bundle) == "稻香 - 周杰伦 - 魔杰座 - CN"
    )


def test_missing_fields_are_reported() -> None:
    bundle = _bundle(title="稻香")
    result = render("{title} - {artist}", bundle, sanitize_cfg=SanitizeConfig())
    assert result.missing == ["artist"]
    assert result.name == "稻香"


def test_empty_collapse_removes_orphan_separators() -> None:
    bundle = _bundle(title="稻香", artist="周杰伦")
    rendered = _render("{title} - {album} - {artist}", bundle)
    assert rendered == "稻香 - 周杰伦"
    assert "  " not in rendered


def test_trailing_and_leading_separators_are_stripped() -> None:
    bundle = _bundle(title="稻香")
    assert _render("{country} - {title}", bundle) == "稻香"
    assert _render("{title} - {country}", bundle) == "稻香"


def test_all_fields_empty_yields_empty_name() -> None:
    bundle = _bundle()
    result = render("{title} - {artist}", bundle, sanitize_cfg=SanitizeConfig())
    assert result.name == ""
    assert set(result.missing) == {"title", "artist"}


@pytest.mark.parametrize(
    ("expression", "value", "expected"),
    [
        ("upper", "abc", "ABC"),
        ("lower", "ABC", "abc"),
        ("title_case", "hello world", "Hello World"),
        ("title_case", "its my life", "Its My Life"),
        ("pad:2", "7", "07"),
        ("pad:3", "07", "007"),
        ("pad", "9", "09"),
        ("pad:2", "x", "x"),
        ("strip_feat", "song (feat. X)", "song"),
        ("strip_feat", "song feat. X", "song"),
        ("default:未知", "", "未知"),
        ("default:未知", "real", "real"),
    ],
)
def test_filters(expression: str, value: str, expected: str) -> None:
    bundle = _bundle(title=value)
    assert _render("{title|" + expression + "}", bundle) == expected


def test_filter_chaining() -> None:
    bundle = _bundle(title="稻香 (feat. X)", artist="jay")
    assert _render("{title|strip_feat|upper}", bundle) == "稻香"


def test_filter_list_is_stable() -> None:
    assert list_filters() == [
        "default",
        "lower",
        "pad",
        "strip_feat",
        "title_case",
        "upper",
    ]


def test_unknown_filter_raises() -> None:
    bundle = _bundle(title="x")
    with pytest.raises(TemplateError, match="未知过滤器"):
        _render("{title|nope}", bundle)


def test_unknown_placeholder_raises_when_known_fields_given() -> None:
    bundle = _bundle(title="x")
    with pytest.raises(TemplateError, match="未知字段"):
        render(
            "{nope}",
            bundle,
            sanitize_cfg=SanitizeConfig(),
            known_fields=FIELDS,
        )


def test_template_fields_and_validation() -> None:
    assert template_fields("{title} - {artist|upper} - {title}") == ["title", "artist"]
    assert validate_template("{title} - {zzz}", FIELDS) == ["zzz"]
    assert validate_template("{title}", FIELDS) == []


def test_per_field_sanitization() -> None:
    bundle = _bundle(title="a/b", artist="c?d")
    result = render("{title} - {artist}", bundle, sanitize_cfg=SanitizeConfig())
    assert result.name == "a／b - c？d"
    assert result.replaced == {"/": "／", "?": "？"}


def test_separators_survive_sanitization() -> None:
    """分隔符是字面量，不能被逐字段 sanitize 误伤。"""
    bundle = _bundle(title="A", artist="B")
    assert _render("{title} - {artist}", bundle) == "A - B"


def test_reserved_device_name_is_escaped() -> None:
    bundle = _bundle(title="CON")
    assert _render("{title}", bundle) == "_CON"


def test_build_filename_appends_extension() -> None:
    bundle = _bundle("song.mp3", title="稻香", artist="周杰伦")
    name, result = build_filename("{title} - {artist}", bundle, sanitize_cfg=SanitizeConfig())
    assert name == "稻香 - 周杰伦.mp3"
    assert result.truncated is False


def test_build_filename_lowercases_extension_when_configured() -> None:
    cfg = default_config()
    cfg.sanitize.lowercase_extension = True
    bundle = _bundle("song.MP3", title="稻香")
    name, _ = build_filename("{title}", bundle, sanitize_cfg=cfg.sanitize)
    assert name == "稻香.mp3"


def test_build_filename_returns_empty_for_empty_render() -> None:
    bundle = _bundle("song.mp3")
    name, result = build_filename("{title}", bundle, sanitize_cfg=SanitizeConfig())
    assert name == ""
    assert result.name == ""


def test_truncation_keeps_extension_and_is_unique() -> None:
    long_name = "稻" * 300
    name, truncated = truncate_name(long_name, ".mp3", max_length=180)
    assert truncated is True
    assert len(name + ".mp3") <= 180
    assert name.endswith(".mp3") is False
    other = "曲" * 300
    other_name, _ = truncate_name(other, ".mp3", max_length=180)
    assert name != other_name


def test_truncation_skip_when_short_enough() -> None:
    name, truncated = truncate_name("short", ".mp3", max_length=180)
    assert name == "short"
    assert truncated is False


def test_truncation_without_suffix() -> None:
    name, truncated = truncate_name("a" * 200, ".mp3", max_length=180, truncate_suffix="")
    assert truncated is True
    assert len(name + ".mp3") <= 180


def test_build_filename_truncates_long_results() -> None:
    cfg = default_config()
    cfg.sanitize.max_name_length = 40
    bundle = _bundle(title="x" * 100)
    name, result = build_filename("{title}", bundle, sanitize_cfg=cfg.sanitize, max_length=40)
    assert result.truncated is True
    assert len(name) <= 40


def test_preset_templates_render() -> None:
    cfg = default_config()
    bundle = _bundle(title="稻香", artist="周杰伦", album="魔杰座", track="1", country="CN")
    bundle.set("country_zh", FieldValue("中国", FieldSource.DEFAULT, 0.5))
    for preset in cfg.presets.values():
        result = render(preset, bundle, sanitize_cfg=cfg.sanitize)
        assert result.name.strip() != ""
    assert _render("{track|pad:2}. {title} - {artist}", bundle) == "01. 稻香 - 周杰伦"


def test_template_can_create_subdirectory() -> None:
    bundle = _bundle(title="稻香", artist="周杰伦", album="魔杰座", track="1")
    assert _render("{album}/{track|pad:2} - {title}", bundle) == "魔杰座/01 - 稻香"


def test_unicode_and_emoji_are_preserved() -> None:
    bundle = _bundle(title="稻香🌾", artist="周杰伦")
    assert _render("{title} - {artist}", bundle) == "稻香🌾 - 周杰伦"


def test_error_message_lists_available_items() -> None:
    bundle = _bundle(title="x")
    with pytest.raises(TemplateError) as exc:
        _render("{title|nope}", bundle)
    assert "pad" in str(exc.value)
