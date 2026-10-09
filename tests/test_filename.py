"""文件名解析 Provider 的测试（SPEC 7.3）。"""

from __future__ import annotations

from pathlib import Path

from renomate.config import Config
from renomate.models import MetadataBundle
from renomate.providers.filename import (
    FILENAME_PATTERNS,
    clean_noise,
    parse_filename,
)


def _bundle(name: str, cfg: Config | None = None) -> MetadataBundle:
    from renomate.chain import make_bundle
    from renomate.config import default_config

    cfg = cfg or default_config()
    profile = cfg.profile("audio")
    return make_bundle(Path("/music") / name, "audio", profile.fields)


def test_patterns_are_declared_in_priority_order() -> None:
    names = [name for name, _, _ in FILENAME_PATTERNS]
    assert names[0] == "artist-title"
    assert names[1] == "title-artist"
    assert names[2] == "brackets-album"
    assert names[3] == "track-title"
    assert names[4] == "track-artist-title"
    assert names[5] == "paren-title"


def test_clean_noise_removes_quality_marks() -> None:
    assert clean_noise("稻香 [320K]")[0] == "稻香"
    assert clean_noise("稻香（FLAC）")[0] == "稻香"
    assert clean_noise("稻香 [HQ]")[0] == "稻香"


def test_clean_noise_removes_site_watermark() -> None:
    text, _ = clean_noise("稻香 [www.example.com]")
    assert text == "稻香"
    text2, _ = clean_noise("稻香 【无损资源共享】")
    assert text2 == "稻香"


def test_clean_noise_normalizes_whitespace() -> None:
    text, _ = clean_noise("稻香　　-　　周杰伦")
    assert text == "稻香 - 周杰伦"


def test_artist_title_pattern() -> None:
    fields, confidence, matched = parse_filename(Path("周杰伦 - 稻香.mp3"))
    assert fields["artist"] == "周杰伦"
    assert fields["title"] == "稻香"
    assert matched == "artist-title"
    assert confidence == 0.80


def test_title_artist_pattern_is_second_choice() -> None:
    fields, _, matched = parse_filename(Path("稻香 - 周杰伦.mp3"))
    assert matched == "artist-title"  # 第一个模式优先命中（顺序固定）
    assert fields["artist"] == "稻香"


def test_track_title_pattern() -> None:
    fields, confidence, matched = parse_filename(Path("01 稻香.mp3"))
    assert fields["track"] == "01"
    assert fields["title"] == "稻香"
    assert matched == "track-title"
    assert confidence == 0.55


def test_patterns_match_in_isolation() -> None:
    """逐个模式单独验证（链上按优先级命中，`artist-title` 会先命中带横杠的名字）。"""
    import re

    lookup = {name: re.compile(pattern) for name, pattern, _ in FILENAME_PATTERNS}

    m = lookup["track-artist-title"].match("03. 周杰伦 - 稻香")
    assert m is not None
    assert m.group("track") == "03"
    assert m.group("artist") == "周杰伦"
    assert m.group("title") == "稻香"

    m = lookup["brackets-album"].match("[魔杰座] 周杰伦 - 稻香")
    assert m is not None
    assert m.group("album") == "魔杰座"

    m = lookup["title-artist"].match("稻香 - 周杰伦")
    assert m is not None
    assert m.group("title") == "稻香"
    assert m.group("artist") == "周杰伦"


def test_artist_title_wins_over_later_patterns() -> None:
    """SPEC 7.3：第一个完整命中（title + artist）即停。"""
    fields, _, matched = parse_filename(Path("03. 周杰伦 - 稻香.mp3"))
    assert matched == "artist-title"
    assert fields["artist"] == "03. 周杰伦"
    assert fields["title"] == "稻香"


def test_paren_extra_is_kept_not_dropped() -> None:
    fields, _, matched = parse_filename(Path("稻香 (Live).mp3"))
    assert matched == "paren-title"
    assert fields["title"] == "稻香"
    assert fields["extra"] == "Live"


def test_feat_goes_to_extra() -> None:
    fields, _, _ = parse_filename(Path("稻香 (feat. 杨瑞代).mp3"))
    assert "feat." in fields["extra"]
    assert fields["title"] == "稻香"


def test_quality_marks_are_removed_before_parsing() -> None:
    fields, _, matched = parse_filename(Path("周杰伦 - 稻香 [320K].mp3"))
    assert matched == "artist-title"
    assert fields["title"] == "稻香"


def test_fallback_uses_whole_name_as_title() -> None:
    fields, confidence, matched = parse_filename(Path("unknown.mp3"))
    assert fields["title"] == "unknown"
    assert confidence == 0.40
    assert matched == "fallback-whole-name"


def test_name_that_is_pure_noise_yields_nothing() -> None:
    fields, confidence, _ = parse_filename(Path("[320K].mp3"))
    assert confidence == 0.0
    assert fields == {}


def test_provider_fills_only_declared_fields() -> None:
    from renomate.providers.filename import FilenameProvider

    bundle = _bundle("周杰伦 - 稻香 [320K].mp3")
    FilenameProvider().fill(bundle, Config())
    assert bundle.get("title") == "稻香"
    assert bundle.get("artist") == "周杰伦"
    assert bundle.fields["title"] is not None
    assert bundle.fields["title"].source.value == "filename"


def test_provider_supports_any_named_file() -> None:
    from renomate.providers.filename import FilenameProvider

    assert FilenameProvider().supports("audio", Path("a.mp3")) is True
    assert FilenameProvider().supports("audio", Path("")) is False
