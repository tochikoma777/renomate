"""`FileSystem` 协议 + 本地实现。

所有文件系统操作都通过注入的 FileSystem 完成，测试中用 tmp_path + 假实现即可覆盖
Windows 语义（大小写不敏感、PermissionError、EXDEV）而无需真实 Windows。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

DEFAULT_FS: "LocalFileSystem | None" = None


@runtime_checkable
class FileSystem(Protocol):
    """测试可替换的文件系统抽象（SPEC 第 3 节）。"""

    def exists(self, path: Path) -> bool:
        """路径是否存在（跟随符号链接）。"""
        ...

    def lexists(self, path: Path) -> bool:
        """路径是否存在（不跟随符号链接，断链也返回 True）。"""
        ...

    def is_dir(self, path: Path) -> bool:
        """是否为目录（跟随符号链接）。"""
        ...

    def is_file(self, path: Path) -> bool:
        """是否为常规文件。"""
        ...

    def is_symlink(self, path: Path) -> bool:
        """是否为符号链接。"""
        ...

    def is_hidden(self, path: Path) -> bool:
        """是否为隐藏文件（posix 点开头 / Windows FILE_ATTRIBUTE_HIDDEN）。"""
        ...

    def stat(self, path: Path) -> os.stat_result:
        """stat（跟随符号链接）；不存在时抛 OSError。"""
        ...

    def iterdir(self, path: Path) -> list[Path]:
        """列出目录条目（不递归）。"""
        ...

    def rename(self, src: Path, dst: Path) -> None:
        """重命名；失败抛 OSError（含 EXDEV / EACCES）。"""
        ...

    def access(self, path: Path, mode: int) -> bool:
        """`os.access` 语义，用于可写性预检。"""
        ...

    def resolve(self, path: Path) -> Path:
        """绝对路径（尽可能解析符号链接）。"""
        ...

    def same_device(self, a: Path, b: Path) -> bool:
        """两个路径是否在同一个设备/文件系统上。"""
        ...

    def is_case_sensitive(self, directory: Path) -> bool:
        """目标目录所在文件系统是否大小写敏感（结果不缓存，由调用方缓存）。"""
        ...

    def mkdir(self, path: Path, *, parents: bool = True) -> None:
        """创建目录。"""
        ...

    def unlink(self, path: Path) -> None:
        """删除文件（仅 recover 清理自有临时文件时使用）。"""
        ...


class LocalFileSystem:
    """默认实现：直接落到 os / pathlib。"""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def lexists(self, path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def is_hidden(self, path: Path) -> bool:
        if path.name.startswith("."):
            return True
        try:
            attrs = getattr(os.stat(path), "st_file_attributes", 0)
        except OSError:
            return False
        hidden_flag = getattr(os.stat, "FILE_ATTRIBUTE_HIDDEN", 2) if os.name == "nt" else 2
        return bool(attrs & hidden_flag)

    def stat(self, path: Path) -> os.stat_result:
        return path.stat()

    def iterdir(self, path: Path) -> list[Path]:
        try:
            return sorted(path.iterdir())
        except OSError:
            return []

    def rename(self, src: Path, dst: Path) -> None:
        os.rename(src, dst)

    def access(self, path: Path, mode: int) -> bool:
        return os.access(path, mode)

    def resolve(self, path: Path) -> Path:
        return Path(os.path.realpath(path))

    def same_device(self, a: Path, b: Path) -> bool:
        try:
            return os.stat(a).st_dev == os.stat(b).st_dev
        except OSError:
            return False

    def is_case_sensitive(self, directory: Path) -> bool:
        """在目录里创建两个仅大小写不同的临时文件探测。"""
        probe_a = directory / ".renomate_caseprobe_a"
        probe_b = directory / ".renomate_caseprobe_A"
        try:
            probe_a.write_bytes(b"")
            try:
                probe_b.write_bytes(b"")
            except OSError:
                return False
            try:
                return not probe_b.exists() or probe_a.stat().st_ino != probe_b.stat().st_ino
            except OSError:
                return False
        except OSError:
            return True
        finally:
            for probe in (probe_a, probe_b):
                try:
                    probe.unlink()
                except OSError:
                    pass

    def mkdir(self, path: Path, *, parents: bool = True) -> None:
        path.mkdir(parents=parents, exist_ok=True)

    def unlink(self, path: Path) -> None:
        path.unlink()


def get_default_fs() -> FileSystem:
    """进程级默认 FileSystem（测试可 monkeypatch）。"""
    global DEFAULT_FS  # noqa: PLW0603 - 单例缓存
    if DEFAULT_FS is None:
        DEFAULT_FS = LocalFileSystem()
    return DEFAULT_FS
