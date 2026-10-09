"""FileSystem 协议实现的测试（含 Windows 语义的假实现）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from renomate.platform import fs as fs_module
from renomate.platform.fs import FileSystem, LocalFileSystem, get_default_fs


def test_default_fs_is_singleton() -> None:
    assert get_default_fs() is get_default_fs()
    assert isinstance(get_default_fs(), LocalFileSystem)


def test_local_fs_roundtrip(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    directory = tmp_path / "sub"
    directory.mkdir()
    target = directory / "a.txt"
    target.write_text("x", encoding="utf-8")
    assert fs.exists(target) is True
    assert fs.lexists(target) is True
    assert fs.is_dir(target) is False
    assert fs.is_file(target) is True
    assert fs.is_symlink(target) is False
    assert fs.is_hidden(target) is False
    assert fs.stat(target).st_size == 1
    assert fs.access(target, os.W_OK) is True
    assert fs.iterdir(directory) == [target]
    assert fs.resolve(directory) == Path(os.path.realpath(directory))
    assert fs.same_device(directory, target) is True


def test_local_fs_missing_path(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    missing = tmp_path / "nope"
    assert fs.exists(missing) is False
    assert fs.lexists(missing) is False
    assert fs.is_file(missing) is False
    assert fs.is_dir(missing) is False
    assert fs.is_hidden(missing) is False
    assert fs.same_device(tmp_path, missing) is False


def test_local_fs_iterdir_on_broken_path_returns_empty(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    assert fs.iterdir(target) == []


def test_local_fs_rename(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    dst = tmp_path / "b.txt"
    fs.rename(src, dst)
    assert not src.exists()
    assert dst.exists()


def test_local_fs_mkdir_and_unlink(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    nested = tmp_path / "a" / "b"
    fs.mkdir(nested)
    assert nested.is_dir()
    target = nested / "f.txt"
    target.write_text("x", encoding="utf-8")
    fs.unlink(target)
    assert not target.exists()


def test_local_fs_hidden_detection(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    hidden = tmp_path / ".x"
    hidden.write_text("x", encoding="utf-8")
    assert fs.is_hidden(hidden) is True


def test_local_fs_case_sensitivity_on_tmp(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    assert isinstance(fs.is_case_sensitive(tmp_path), bool)
    assert not (tmp_path / ".renomate_caseprobe_a").exists()


def test_local_fs_case_insensitive_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 Windows/NTFS：同名不同大小写被视为同一文件。"""
    fs = LocalFileSystem()

    def fake_stat(path: Path) -> os.stat_result:
        return os.stat(path)

    monkeypatch.setattr(Path, "stat", fake_stat)
    directory = Path("/fake")
    calls: list[Path] = []

    original_write = Path.write_bytes

    def fake_write(self: Path, data: bytes) -> int:  # noqa: ARG001
        calls.append(self)
        if self.name.endswith("_A") and any(c.name.endswith("_a") for c in calls):
            msg = "case-insensitive fs"
            raise OSError(msg)
        return 0

    monkeypatch.setattr(Path, "write_bytes", fake_write)
    assert fs.is_case_sensitive(directory) is False
    monkeypatch.setattr(Path, "write_bytes", original_write)


def test_local_fs_case_sensitive_detection(tmp_path: Path) -> None:
    fs: FileSystem = LocalFileSystem()
    assert fs.is_case_sensitive(tmp_path) is True


def test_fs_protocol_is_runtime_checkable() -> None:
    assert isinstance(LocalFileSystem(), FileSystem)


def test_default_fs_cache_can_be_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_module, "DEFAULT_FS", None)
    first = get_default_fs()
    assert isinstance(first, LocalFileSystem)
    assert get_default_fs() is first
