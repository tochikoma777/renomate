"""文件名合规化 + 路径黑名单 + 风险判定的测试（SPEC 9、10）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from renomate.safety import (
    BlockedPathError,
    EmptyNameError,
    check_component_length,
    check_path_length,
    classify_file,
    is_blocked_path,
    is_mount_root,
    normalize_for_compare,
    sanitize_component,
)


def test_illegal_chars_are_replaced_and_recorded() -> None:
    name, replaced = sanitize_component('a/b\\c:d*e?f"g<h>i|j')
    assert name == "a／b＼c：d＊e？f＂g＜h＞i｜j"
    assert replaced == {
        "/": "／",
        "\\": "＼",
        ":": "：",
        "*": "＊",
        "?": "？",
        '"': "＂",
        "<": "＜",
        ">": "＞",
        "|": "｜",
    }


def test_custom_char_map() -> None:
    name, replaced = sanitize_component("a/b", char_map={"/": "_"})
    assert name == "a_b"
    assert replaced == {"/": "_"}


def test_drop_strategy() -> None:
    name, replaced = sanitize_component("a/b*c", strategy="drop")
    assert name == "abc"
    assert replaced == {"/": "", "*": ""}


def test_underscore_strategy() -> None:
    name, replaced = sanitize_component("a/b", strategy="underscore")
    assert name == "a_b"
    assert replaced == {"/": "_"}


def test_control_chars_removed() -> None:
    name, _ = sanitize_component("a\x01\x02b")
    assert name == "ab"


def test_trailing_dot_and_space_are_stripped() -> None:
    name, _ = sanitize_component("song.  ")
    assert name == "song"


def test_reserved_names_get_underscore() -> None:
    name, _ = sanitize_component("CON.txt")
    assert name == "_CON.txt"
    plain, _ = sanitize_component("NUL")
    assert plain == "_NUL"


def test_nfc_normalization() -> None:
    name, _ = sanitize_component("é")  # e + combining accent
    assert name == "é"


def test_posix_branch() -> None:
    name, _ = sanitize_component("a/b\x00c", platform="posix")
    assert name == "a／bc"
    dotted, _ = sanitize_component(".", platform="posix")
    assert dotted == "_."


@pytest.mark.parametrize("bad", ["", "   ", "...", "\x01\x02"])
def test_empty_name_raises(bad: str) -> None:
    with pytest.raises(EmptyNameError):
        sanitize_component(bad)


@pytest.mark.parametrize(
    "path",
    [
        "/etc",
        "/etc/nginx",
        "/proc/1",
        "/mnt/c/WINDOWS/System32",
        "/mnt/c/windows",
        "/mnt/c/Program Files",
        "/mnt/wsl",
        "C:\\Windows",
        "C:\\Program Files (x86)\\x",
        "/usr/lib/python3",
    ],
)
def test_blocked_paths(path: str) -> None:
    assert is_blocked_path(Path(path)) is not None


@pytest.mark.parametrize(
    "path",
    [
        "/home/tachikoma/Music",
        "/mnt/c/Users/tachikoma/Music",
        "/mnt/d/Media",
        "/home/tachikoma/projects",
    ],
)
def test_allowed_paths(path: str) -> None:
    assert is_blocked_path(Path(path)) is None


def test_extra_blocked_and_override() -> None:
    assert is_blocked_path(Path("/data/x"), extra_blocked=["/data"]) is not None
    assert is_blocked_path(Path("/data/x"), extra_blocked=["/data"], allow_override=True) is None
    assert is_blocked_path(Path("/etc"), allow_override=True) is None


@pytest.mark.parametrize("path", ["/", "/home", "/mnt", "/mnt/c", "C:\\", "D:/", "/usr"])
def test_mount_roots(path: str) -> None:
    assert is_mount_root(path) is True
    assert is_blocked_path(Path(path)) is not None


@pytest.mark.parametrize("path", ["/home/tachikoma", "/mnt/c/Users/me", "/mnt/d/x/y"])
def test_not_mount_roots(path: str) -> None:
    assert is_mount_root(path) is False


def test_normalize_for_compare() -> None:
    assert normalize_for_compare("C:\\Windows\\") == "c:/windows"
    assert normalize_for_compare("/Home/") == "/home"


def test_blocked_path_error_message() -> None:
    err = BlockedPathError(Path("/etc"), "黑名单")
    assert "/etc" in str(err)
    assert err.reason == "黑名单"


def test_check_path_length() -> None:
    assert check_path_length(Path("/a" * 40)) is True
    long_path = Path("a" * 300)
    assert check_path_length(long_path) is False
    assert check_path_length(long_path, platform="posix") is True


def test_check_component_length() -> None:
    assert check_component_length("a" * 255) is True
    assert check_component_length("a" * 256) is False


def test_classify_regular_file_passes(tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    assert classify_file(target, allowed_extensions=["mp3"]) is None


def test_classify_rejects_extension(tmp_path: Path) -> None:
    target = tmp_path / "song.exe"
    target.write_bytes(b"x")
    reason = classify_file(target, allowed_extensions=["mp3"])
    assert reason is not None and "白名单" in reason


def test_classify_rejects_directory(tmp_path: Path) -> None:
    assert classify_file(tmp_path, allowed_extensions=["mp3"]) is not None


def test_classify_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "song.mp3"
    target.write_bytes(b"x")
    link = tmp_path / "link.mp3"
    link.symlink_to(target)
    reason = classify_file(link, allowed_extensions=["mp3"])
    assert reason is not None and reason.startswith("is_symlink")
    assert classify_file(link, allowed_extensions=["mp3"], follow_symlinks=True) is None


def test_classify_hidden_file(tmp_path: Path) -> None:
    target = tmp_path / ".song.mp3"
    target.write_bytes(b"x")
    assert classify_file(target, allowed_extensions=["mp3"]) is not None
    assert classify_file(target, allowed_extensions=["mp3"], include_hidden=True) is None


def test_classify_skips_renomate_artifacts(tmp_path: Path) -> None:
    target = tmp_path / ".renomate_tmp_abc.mp3"
    target.write_bytes(b"x")
    reason = classify_file(target, allowed_extensions=["mp3"])
    assert reason is not None and "renomate" in reason


def test_classify_read_only_file(tmp_path: Path) -> None:
    target = tmp_path / "readonly.mp3"
    target.write_bytes(b"x")
    target.chmod(0o444)
    try:
        reason = classify_file(target, allowed_extensions=["mp3"])
    finally:
        target.chmod(0o644)
    if getattr(os, "geteuid", lambda: -1)() != 0:
        assert reason is not None and "not_writable" in reason


def test_classify_missing_file_is_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "gone.mp3"
    reason = classify_file(missing, allowed_extensions=["mp3"])
    assert reason is not None
