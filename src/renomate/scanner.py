"""目录扫描（F1）：收集候选文件并就地应用闸门 1 / 闸门 2。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from renomate.platform.fs import FileSystem, get_default_fs
from renomate.safety import BlockedPathError, classify_file, is_blocked_path


class TooManyFilesError(Exception):
    """命中文件数超过 `max_batch`，需要用户二次确认。"""

    def __init__(self, limit: int, found: int) -> None:
        super().__init__(f"扫描到 {found} 个文件，超过 max_batch={limit}，需显式确认后才能继续")
        self.limit = limit
        self.found = found


@dataclass
class FileCandidate:
    """一个被扫描到的文件。`skip_reason` 非空表示被闸门拦截/跳过。"""

    path: Path
    size: int = 0
    mtime_ns: int = 0
    skip_reason: str = ""

    @property
    def usable(self) -> bool:
        return not self.skip_reason


@dataclass
class ScanOptions:
    recursive: bool = True
    max_depth: int | None = None  # None = 无限深度
    extensions: list[str] | None = None  # None = 不限制
    include_hidden: bool = False
    follow_symlinks: bool = False
    extra_blocked: list[str] = field(default_factory=list)
    allow_override: bool = False  # 仅 --i-know-what-im-doing
    max_files: int | None = None


@dataclass
class ScanResult:
    root: Path
    candidates: list[FileCandidate]
    skipped_dirs: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def usable(self) -> list[FileCandidate]:
        return [c for c in self.candidates if c.usable]

    @property
    def skipped(self) -> list[FileCandidate]:
        return [c for c in self.candidates if not c.usable]


def scan(
    root: Path,
    options: ScanOptions | None = None,
    *,
    fs: FileSystem | None = None,
) -> ScanResult:
    """扫描目录并返回候选文件。

    Raises:
        BlockedPathError: 根目录命中黑名单或为挂载根目录。
        TooManyFilesError: 文件数超过 `options.max_files`。
    """
    opts = options or ScanOptions()
    filesystem: FileSystem = fs if fs is not None else get_default_fs()

    resolved = filesystem.resolve(root)
    if not filesystem.is_dir(resolved):
        msg = f"不是目录或不存在: {root}"
        raise NotADirectoryError(msg)

    blocked = is_blocked_path(
        resolved,
        extra_blocked=opts.extra_blocked,
        allow_override=opts.allow_override,
    )
    if blocked:
        raise BlockedPathError(resolved, blocked)

    candidates: list[FileCandidate] = []
    skipped_dirs: list[tuple[Path, str]] = []
    queue: list[tuple[Path, int]] = [(resolved, 0)]

    while queue:
        directory, depth = queue.pop(0)
        for entry in filesystem.iterdir(directory):
            if opts.max_files is not None and len(candidates) > opts.max_files:
                raise TooManyFilesError(opts.max_files, len(candidates))

            if filesystem.is_dir(entry) and not (
                filesystem.is_symlink(entry) and not opts.follow_symlinks
            ):
                reason = is_blocked_path(
                    entry,
                    extra_blocked=opts.extra_blocked,
                    allow_override=opts.allow_override,
                )
                if reason:
                    skipped_dirs.append((entry, reason))
                    continue
                if opts.recursive and (opts.max_depth is None or depth < opts.max_depth):
                    queue.append((entry, depth + 1))
                continue

            reason = classify_file(
                entry,
                allowed_extensions=opts.extensions,
                fs=filesystem,
                include_hidden=opts.include_hidden,
                follow_symlinks=opts.follow_symlinks,
            )
            size = 0
            mtime_ns = 0
            try:
                info = filesystem.stat(entry)
            except OSError:
                if reason is None:
                    reason = "stat_failed: 无法读取文件属性"
            else:
                size = info.st_size
                mtime_ns = info.st_mtime_ns
            candidates.append(
                FileCandidate(
                    path=entry,
                    size=size,
                    mtime_ns=mtime_ns,
                    skip_reason=reason or "",
                )
            )

    candidates.sort(key=lambda c: str(c.path))
    return ScanResult(root=resolved, candidates=candidates, skipped_dirs=skipped_dirs)
