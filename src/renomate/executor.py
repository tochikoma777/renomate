"""执行器：两阶段重命名 + 大小写安全 rename + 逐条错误隔离（SPEC 11）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from renomate.models import ItemStatus, Plan, RenameItem
from renomate.platform.fs import FileSystem, get_default_fs
from renomate.safety import RENOMATE_CASE_PREFIX, RENOMATE_TMP_PREFIX

StepLogger = Callable[["StepResult"], None]


def tmp_name(src: Path, session_id: str, seq: int) -> str:
    """阶段一临时名（隐藏 + 被扫描白名单显式排除）。"""
    return f"{RENOMATE_TMP_PREFIX}{session_id}_{seq}{src.suffix}"


def case_tmp_name(src: Path) -> str:
    """大小写两步 rename 用的中间名。"""
    return f"{RENOMATE_CASE_PREFIX}{uuid.uuid4().hex}{src.suffix}"


def same_ignoring_case(a: Path, b: Path) -> bool:
    """仅大小写不同（含完全相同）→ True。"""
    return str(a).casefold() == str(b).casefold()


def rename_case_safe(src: Path, dst: Path, *, fs: FileSystem | None = None) -> None:
    """Windows / `/mnt/c` 上「仅大小写改名」必须先落到临时名（SPEC 11.2）。"""
    filesystem: FileSystem = fs if fs is not None else get_default_fs()

    if filesystem.exists(src) and filesystem.exists(dst):
        try:
            same_file = filesystem.resolve(src) == filesystem.resolve(dst)
        except OSError:  # pragma: no cover - 竞态
            same_file = False
        if same_file:
            filesystem.rename(src, dst)
            return

    if same_ignoring_case(src, dst):
        mid = src.with_name(case_tmp_name(src))
        filesystem.rename(src, mid)
        filesystem.rename(mid, dst)
        return

    filesystem.rename(src, dst)


@dataclass
class StepResult:
    """阶段一/阶段二的单步结果，供 journal 逐条 append。"""

    op: str  # rename | mkdir
    src: Path
    dst: Path
    status: str  # applied | failed | needs_manual | skipped
    error: str | None = None
    src_size: int | None = None
    src_mtime_ns: int | None = None
    dst_size: int | None = None
    dst_mtime_ns: int | None = None


@dataclass
class ExecutionResult:
    """一次执行的汇总结果。"""

    applied: list[RenameItem] = field(default_factory=list)
    failed: list[tuple[RenameItem, str]] = field(default_factory=list)
    skipped: list[RenameItem] = field(default_factory=list)
    leftover_tmp: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """没有任何失败与残留才算干净。"""
        return not self.failed and not self.leftover_tmp


def _snapshot(fs: FileSystem, path: Path) -> tuple[int | None, int | None]:
    try:
        info = fs.stat(path)
    except OSError:
        return None, None
    return info.st_size, info.st_mtime_ns


def _ensure_parent(directory: Path, fs: FileSystem, on_step: StepLogger | None = None) -> None:
    """模板含 `/` 时需要建子目录。"""
    if fs.is_dir(directory):
        return
    try:
        fs.mkdir(directory, parents=True)
    except OSError as exc:
        if on_step is not None:
            on_step(
                StepResult(
                    op="mkdir", src=directory, dst=directory, status="failed", error=str(exc)
                )
            )
        raise
    if on_step is not None:
        on_step(StepResult(op="mkdir", src=directory, dst=directory, status="applied"))


def _rollback(fs: FileSystem, item: RenameItem, tmp: Path) -> str | None:
    """阶段二失败时把文件改回原名；失败则返回需要人工处理的说明。"""
    try:
        rename_case_safe(tmp, item.src, fs=fs)
    except OSError as exc:
        item.reason = f"needs_manual: {exc.strerror or type(exc).__name__}"
        return item.reason
    item.reason = "stage2_failed: rolled back to original"
    return None


def find_leftovers(items: list[RenameItem], fs: FileSystem) -> list[Path]:
    """残留的 `.renomate_tmp_*` 文件（必须报警并提供 `renomate recover`）。"""
    found: list[Path] = []
    directories = {item.src.parent for item in items} | {item.dst.parent for item in items}
    for directory in directories:
        if not fs.is_dir(directory):
            continue
        found.extend(
            entry for entry in fs.iterdir(directory) if entry.name.startswith(RENOMATE_TMP_PREFIX)
        )
    return sorted(found)


def recover_leftovers(
    directory: Path, *, fs: FileSystem | None = None, dry_run: bool = True
) -> list[tuple[Path, str]]:
    """`renomate recover`：把残留临时文件改回去掉前缀的原名。

    Returns:
        [(临时文件路径, 说明)]，dry_run=True 时只返回计划、不做任何改动。
    """
    filesystem: FileSystem = fs if fs is not None else get_default_fs()
    actions: list[tuple[Path, str]] = []
    if not filesystem.is_dir(directory):
        return actions
    for entry in filesystem.iterdir(directory):
        if not entry.name.startswith(RENOMATE_TMP_PREFIX):
            continue
        restored = entry.with_name(entry.name[len(RENOMATE_TMP_PREFIX) :])
        # 去掉 `_{seq}` 与 session id 之后的部分，恢复为最原始的名字
        stem = restored.stem
        for marker in ("_",):
            if marker in stem:
                stem = stem.split(marker)[0]
        target = restored.with_name(f"{stem}{restored.suffix}")
        actions.append((entry, str(target)))
        if not dry_run:
            rename_case_safe(entry, target, fs=filesystem)
    return actions


def apply_plan(
    plan: Plan,
    *,
    fs: FileSystem | None = None,
    on_step: StepLogger | None = None,
) -> ExecutionResult:
    """执行计划：阶段一全部改临时名 → 阶段二改最终名（SPEC 11.1）。

    任一文件阶段一失败 → 记 failed 并保持原名，不进入阶段二；
    阶段二失败 → 尽力回退到原名，回退失败记 needs_manual。
    全程逐条 try/except，绝不因为单个文件中断整批。
    """
    filesystem: FileSystem = fs if fs is not None else get_default_fs()
    result = ExecutionResult()
    staged: list[tuple[RenameItem, Path]] = []

    ready_items = [i for i in plan.items if i.status is ItemStatus.READY and i.user_checked]
    for item in ready_items:
        item.status = ItemStatus.PENDING
    for item in plan.items:
        if item.status is ItemStatus.SKIPPED or item.status is ItemStatus.BLOCKED:
            result.skipped.append(item)

    for seq, item in enumerate(ready_items, start=1):
        size, mtime = _snapshot(filesystem, item.src)
        tmp = item.src.with_name(tmp_name(item.src, plan.session_id, seq))
        try:
            _ensure_parent(item.dst.parent, filesystem, on_step)
            filesystem.rename(item.src, tmp)
        except OSError as exc:
            item.status = ItemStatus.FAILED
            item.reason = f"stage1_failed: {exc.strerror or type(exc).__name__}"
            result.failed.append((item, item.reason))
            if on_step is not None:
                on_step(
                    StepResult(
                        op="rename",
                        src=item.src,
                        dst=tmp,
                        status="failed",
                        error=str(exc),
                        src_size=size,
                        src_mtime_ns=mtime,
                    )
                )
            continue
        staged.append((item, tmp))
        if on_step is not None:
            new_size, new_mtime = _snapshot(filesystem, tmp)
            # op="stage1" 的中间态只做取证，回退时忽略（undo 只处理 op="rename"）
            on_step(
                StepResult(
                    op="stage1",
                    src=item.src,
                    dst=tmp,
                    status="applied",
                    src_size=size,
                    src_mtime_ns=mtime,
                    dst_size=new_size,
                    dst_mtime_ns=new_mtime,
                )
            )

    for item, tmp in staged:
        src_size, src_mtime = _snapshot(filesystem, tmp)
        try:
            rename_case_safe(tmp, item.dst, fs=filesystem)
        except OSError as exc:
            item.status = ItemStatus.FAILED
            item.reason = f"stage2_failed: {exc.strerror or type(exc).__name__}"
            rollback_error = _rollback(filesystem, item, tmp)
            result.failed.append((item, rollback_error or item.reason))
            if on_step is not None:
                on_step(
                    StepResult(
                        op="rename",
                        src=tmp,
                        dst=item.dst,
                        status="failed" if rollback_error is None else "needs_manual",
                        error=str(exc),
                        src_size=src_size,
                        src_mtime_ns=src_mtime,
                    )
                )
            continue
        item.status = ItemStatus.APPLIED
        result.applied.append(item)
        if on_step is not None:
            dst_size, dst_mtime = _snapshot(filesystem, item.dst)
            on_step(
                StepResult(
                    op="rename",
                    src=item.src,
                    dst=item.dst,
                    status="applied",
                    src_size=src_size,
                    src_mtime_ns=src_mtime,
                    dst_size=dst_size,
                    dst_mtime_ns=dst_mtime,
                )
            )

    result.leftover_tmp = find_leftovers(ready_items, filesystem)
    return result


def now_iso() -> str:
    """带时区的时间戳（日志 `ts` 字段）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


__all__ = [
    "ExecutionResult",
    "StepLogger",
    "StepResult",
    "apply_plan",
    "case_tmp_name",
    "find_leftovers",
    "now_iso",
    "recover_leftovers",
    "rename_case_safe",
    "same_ignoring_case",
    "tmp_name",
]
