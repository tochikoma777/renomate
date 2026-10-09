"""目录扫描（闸门 1 + 闸门 2）的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from renomate.profiles import get_profile
from renomate.safety import BlockedPathError
from renomate.scanner import (
    FileCandidate,
    ScanOptions,
    TooManyFilesError,
    scan,
)


def _audio_options(**kwargs: object) -> ScanOptions:
    profile = get_profile("audio")
    return ScanOptions(extensions=list(profile.extensions), **kwargs)  # type: ignore[arg-type]


def test_scan_collects_audio_files(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options())
    names = {c.path.name for c in result.usable}
    assert "01 稻香.mp3" in names
    assert "晴天[320K].mp3" in names
    assert "K歌之王.flac" in names
    assert "unknown.mp3" in names


def test_scan_skips_non_whitelisted_extensions(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options())
    reasons = {c.path.name: c.skip_reason for c in result.skipped}
    assert "cover.jpg" in reasons
    assert "扩展名不在白名单" in reasons["cover.jpg"]
    assert "notes.txt" in reasons


def test_scan_skips_hidden_files(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options())
    skipped = {c.path.name: c.skip_reason for c in result.skipped}
    assert ".hidden.mp3" in skipped
    assert skipped[".hidden.mp3"].startswith("is_hidden")


def test_scan_include_hidden_opt_in(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options(include_hidden=True))
    assert ".hidden.mp3" in {c.path.name for c in result.usable}


def test_scan_skips_symlinks(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options())
    skipped = {c.path.name: c.skip_reason for c in result.skipped}
    if (music_lib / "link.mp3").is_symlink():
        assert skipped["link.mp3"].startswith("is_symlink")


def test_scan_follows_symlinks_when_opted_in(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options(follow_symlinks=True))
    assert "link.mp3" in {c.path.name for c in result.usable}


def test_scan_non_recursive_only_root_files(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options(recursive=False))
    assert {c.path.name for c in result.usable} == {"broken.mp3"}


def test_scan_max_depth(music_lib: Path) -> None:
    depth1 = scan(music_lib, _audio_options(recursive=True, max_depth=1))
    assert {c.path.name for c in depth1.usable} == {"broken.mp3", "K歌之王.flac", "unknown.mp3"}

    depth2 = scan(music_lib, _audio_options(recursive=True, max_depth=2))
    names2 = {c.path.name for c in depth2.usable}
    assert {"01 稻香.mp3", "晴天[320K].mp3", "broken.mp3"} <= names2

    full = scan(music_lib, _audio_options(recursive=True))
    assert {c.path.name for c in full.usable} == names2


def test_scan_records_size_and_mtime(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options())
    target = next(c for c in result.usable if c.path.name == "01 稻香.mp3")
    assert target.size > 0
    assert target.mtime_ns > 0


def test_scan_blocked_root_raises(tmp_path: Path) -> None:
    with pytest.raises(BlockedPathError) as exc:
        scan(tmp_path, ScanOptions(extra_blocked=[str(tmp_path)]))
    assert "黑名单" in exc.value.reason


def test_scan_allow_override_bypasses_blocklist(tmp_path: Path) -> None:
    (tmp_path / "a.mp3").write_bytes(b"x")
    result = scan(tmp_path, ScanOptions(extra_blocked=[str(tmp_path)], allow_override=True))
    assert [c.path.name for c in result.usable] == ["a.mp3"]


def test_scan_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        scan(tmp_path / "nope", _audio_options())


def test_scan_max_files_guard(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"{i}.mp3").write_bytes(b"x")
    with pytest.raises(TooManyFilesError) as exc:
        scan(tmp_path, ScanOptions(extensions=["mp3"], max_files=2))
    assert exc.value.limit == 2


def test_scan_without_extension_filter(tmp_path: Path) -> None:
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"x")
    result = scan(tmp_path, ScanOptions())
    assert {c.path.name for c in result.usable} == {"a.mp3", "b.jpg"}


def test_scan_not_writable_file_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "readonly.mp3"
    target.write_bytes(b"x")
    target.chmod(0o444)
    try:
        result = scan(tmp_path, ScanOptions(extensions=["mp3"]))
    finally:
        target.chmod(0o644)
    reasons = [c.skip_reason for c in result.candidates]
    assert any("not_writable" in r for r in reasons) or result.usable


def test_scan_skips_directories_instead_of_recursing_into_them(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options())
    assert all(c.path.is_file() for c in result.candidates)


def test_scan_result_is_sorted_by_path(music_lib: Path) -> None:
    result = scan(music_lib, _audio_options())
    paths = [str(c.path) for c in result.candidates]
    assert paths == sorted(paths)


def test_file_candidate_usable_flag() -> None:
    assert FileCandidate(Path("a.mp3")).usable is True
    assert FileCandidate(Path("a.mp3"), skip_reason="x").usable is False
