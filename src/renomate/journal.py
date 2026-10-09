"""事务日志：append-only JSONL + 断电安全 + 一键回退（SPEC 6.1、11.3）。"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from renomate.config import journal_dir
from renomate.executor import StepResult, now_iso, rename_case_safe
from renomate.platform.fs import FileSystem, get_default_fs

JOURNAL_SUFFIX = ".jsonl"
DEFAULT_KEEP_SESSIONS = 50


@dataclass
class JournalEntry:
    """JSONL 里的一行（字段名与 SPEC 6.1 完全一致）。"""

    session_id: str
    ts: str
    seq: int
    op: str  # rename | mkdir
    src: str
    dst: str
    status: str  # applied | failed | skipped | undone | needs_manual
    src_size: int | None = None
    src_mtime_ns: int | None = None
    dst_size: int | None = None
    dst_mtime_ns: int | None = None
    error: str | None = None
    template: str = ""
    profile: str = ""

    def to_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> JournalEntry:
        known = {
            key: data.get(key)
            for key in (
                "session_id",
                "ts",
                "seq",
                "op",
                "src",
                "dst",
                "status",
                "src_size",
                "src_mtime_ns",
                "dst_size",
                "dst_mtime_ns",
                "error",
                "template",
                "profile",
            )
        }
        return cls(
            session_id=str(known["session_id"] or ""),
            ts=str(known["ts"] or ""),
            seq=_opt_int(known["seq"]) or 0,
            op=str(known["op"] or "rename"),
            src=str(known["src"] or ""),
            dst=str(known["dst"] or ""),
            status=str(known["status"] or ""),
            src_size=_opt_int(known["src_size"]),
            src_mtime_ns=_opt_int(known["src_mtime_ns"]),
            dst_size=_opt_int(known["dst_size"]),
            dst_mtime_ns=_opt_int(known["dst_mtime_ns"]),
            error=None if known["error"] is None else str(known["error"]),
            template=str(known["template"] or ""),
            profile=str(known["profile"] or ""),
        )


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


@dataclass
class SessionSummary:
    """`renomate journals` 的一行。"""

    session_id: str
    ts: str
    path: Path
    entries: int
    applied: int = 0
    undone: int = 0
    failed: int = 0

    @property
    def status(self) -> str:
        if self.undone and self.undone >= self.applied:
            return "undone"
        if self.undone:
            return "partially_undone"
        if self.failed:
            return "partially_failed"
        return "applied"


class Journal:
    """一个 session 的日志文件句柄（append-only）。"""

    def __init__(self, session_id: str, *, path: Path | None = None) -> None:
        self.session_id = session_id
        self.path = path or (journal_dir() / f"{session_id}{JOURNAL_SUFFIX}")

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def append(
        self,
        step: StepResult,
        *,
        template: str = "",
        profile: str = "",
        seq: int | None = None,
    ) -> JournalEntry:
        """追加一行并立即 flush + fsync（崩溃/断电不丢日志）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = JournalEntry(
            session_id=self.session_id,
            ts=now_iso(),
            seq=seq if seq is not None else next_seq(self.path),
            op=step.op,
            src=str(step.src),
            dst=str(step.dst),
            status=step.status,
            src_size=step.src_size,
            src_mtime_ns=step.src_mtime_ns,
            dst_size=step.dst_size,
            dst_mtime_ns=step.dst_mtime_ns,
            error=step.error,
            template=template,
            profile=profile,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_line() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def read(self) -> list[JournalEntry]:
        if not self.path.exists():
            return []
        entries: list[JournalEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data: object = json.loads(line)
            except ValueError:  # pragma: no cover - 损坏行跳过
                continue
            if isinstance(data, dict):
                entries.append(JournalEntry.from_dict(data))
        return entries

    def summarize(self) -> SessionSummary:
        entries = self.read()
        summary = SessionSummary(
            session_id=self.session_id,
            ts=entries[0].ts if entries else "",
            path=self.path,
            entries=len(entries),
        )
        for entry in entries:
            if entry.status == "applied":
                summary.applied += 1
            elif entry.status == "undone":
                summary.undone += 1
            elif entry.status in {"failed", "needs_manual"}:
                summary.failed += 1
        return summary


def next_seq(path: Path) -> int:
    """下一个自增序号（按现有行数 +1，不依赖全部解析成功）。"""
    if not path.exists():
        return 1
    count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return count + 1


def list_sessions(*, directory: Path | None = None) -> list[SessionSummary]:
    """列出所有 session（最新在前）。"""
    base = directory or journal_dir()
    if not base.is_dir():
        return []
    summaries = [
        Journal(path.stem, path=path).summarize() for path in base.glob(f"*{JOURNAL_SUFFIX}")
    ]
    return sorted(summaries, key=lambda s: (s.ts, s.session_id), reverse=True)


def latest_session(*, directory: Path | None = None) -> str | None:
    sessions = list_sessions(directory=directory)
    return sessions[0].session_id if sessions else None


def cleanup_sessions(*, keep: int = DEFAULT_KEEP_SESSIONS, directory: Path | None = None) -> int:
    """保留最近 keep 个 session，其余删除，返回删除数量。"""
    if keep <= 0:
        return 0
    sessions = list_sessions(directory=directory)
    removed = 0
    for summary in sessions[keep:]:
        try:
            summary.path.unlink()
        except OSError:  # pragma: no cover - 权限问题忽略
            continue
        removed += 1
    return removed


@dataclass
class UndoResult:
    """一次回退的结果清单。"""

    undone: list[JournalEntry] = field(default_factory=list)
    skipped: list[JournalEntry] = field(default_factory=list)
    needs_manual: list[tuple[JournalEntry, str]] = field(default_factory=list)
    planned: bool = False

    @property
    def ok(self) -> bool:
        return not self.needs_manual


def _matches(entry: JournalEntry, pattern: str | None) -> bool:
    if not pattern:
        return True
    src_name = Path(entry.src).name
    dst_name = Path(entry.dst).name
    return fnmatch.fnmatch(src_name, pattern) or fnmatch.fnmatch(dst_name, pattern)


def verify_entry(entry: JournalEntry, fs: FileSystem) -> str | None:
    """回退前的可行性校验；返回 None 表示可以安全回退，否则给出人工处理原因。"""
    dst = Path(entry.dst)
    src = Path(entry.src)
    if not fs.exists(dst):
        return f"目标文件已不存在: {dst}"
    if fs.exists(src):
        return f"原路径已被占用: {src}"
    try:
        info = fs.stat(dst)
    except OSError as exc:
        return f"无法读取 {dst}: {exc}"
    if entry.dst_size is not None and info.st_size != entry.dst_size:
        return f"大小不一致（日志 {entry.dst_size} ≠ 实际 {info.st_size}）"
    if entry.dst_mtime_ns is not None and info.st_mtime_ns != entry.dst_mtime_ns:
        return "修改时间不一致（文件可能被改过）"
    return None


def undo_session(
    session_id: str | None = None,
    *,
    dry_run: bool = False,
    only: str | None = None,
    fs: FileSystem | None = None,
    directory: Path | None = None,
) -> UndoResult:
    """回退一个 session（SPEC 11.3）：逆序 → 校验 → 反向 rename → 逐条写回日志。

    Args:
        session_id: 目标 session；None 表示最近一次。
        dry_run: 只输出计划，不改动任何文件。
        only: 文件名 glob，用于部分回退。
        fs: 文件系统抽象（测试注入）。
        directory: 日志目录覆盖（测试用）。
    """
    filesystem: FileSystem = fs if fs is not None else get_default_fs()
    resolved = session_id or latest_session(directory=directory)
    if resolved is None:
        return UndoResult(planned=dry_run)

    journal = Journal(resolved, path=(directory or journal_dir()) / f"{resolved}{JOURNAL_SUFFIX}")
    result = UndoResult(planned=dry_run)
    all_entries = journal.read()
    # 已回退的条目按「反转后的 src/dst」登记，第二次 undo 据此跳过
    undone_keys = {(e.dst, e.src) for e in all_entries if e.status == "undone"}
    entries = [e for e in all_entries if e.op == "rename"]

    for entry in reversed(entries):
        if entry.status != "applied":
            continue
        if (entry.src, entry.dst) in undone_keys:
            continue
        if not _matches(entry, only):
            continue

        problem = verify_entry(entry, filesystem)
        if problem is not None:
            result.needs_manual.append((entry, problem))
            continue
        if dry_run:
            result.undone.append(entry)
            continue

        src, dst = Path(entry.src), Path(entry.dst)
        size, mtime = entry.dst_size, entry.dst_mtime_ns
        try:
            rename_case_safe(dst, src, fs=filesystem)
        except OSError as exc:
            result.needs_manual.append((entry, str(exc)))
            continue

        info_size, info_mtime = None, None
        try:
            stat = filesystem.stat(src)
        except OSError:  # pragma: no cover
            stat = None
        if stat is not None:
            info_size, info_mtime = stat.st_size, stat.st_mtime_ns

        journal.append(
            StepResult(
                op="undo",
                src=dst,
                dst=src,
                status="undone",
                src_size=size,
                src_mtime_ns=mtime,
                dst_size=info_size,
                dst_mtime_ns=info_mtime,
            ),
            template=entry.template,
            profile=entry.profile,
        )
        result.undone.append(entry)

    return result


def session_timestamp(session_id: str) -> str:
    """从 session id 里取时间戳部分（用于排序/展示）。"""
    head = session_id.split("-")[0]
    try:
        datetime.strptime(head, "%Y%m%d")
    except ValueError:
        return ""
    return head


__all__ = [
    "Journal",
    "JournalEntry",
    "SessionSummary",
    "UndoResult",
    "cleanup_sessions",
    "latest_session",
    "list_sessions",
    "next_seq",
    "session_timestamp",
    "undo_session",
    "verify_entry",
]
