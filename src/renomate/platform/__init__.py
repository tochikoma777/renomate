"""平台抽象层：把文件系统操作隔离到可注入的 `FileSystem` 协议后面。"""

from renomate.platform.fs import FileSystem, LocalFileSystem, get_default_fs

__all__ = ["FileSystem", "LocalFileSystem", "get_default_fs"]
