"""事务日志与回退的测试（SPEC 6.1、11.3）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import tag_mp3

from renomate.config import Config, journal_dir
from renomate.executor import StepResult, apply_plan
from renomate.journal import (
    Journal,
    JournalEntry,
    SessionSummary,
    cleanup_sessions,
    latest_session,
    list_sessions,
    next_seq,
    session_timestamp,
    undo_session,
    verify_entry,
)
from renomate.plan import build_plan, new_session_id
from renomate.platform.fs import LocalFileSystem
from renomate.scanner import FileCandidate

TEMPLATE = "{title} - {artist}"


def _step(src: Path, dst: Path, **kwargs: object) -> StepResult:
    return StepResult(
        op="rename",
        src=src,
        dst=dst,
        status=str(kwargs.get("status", "applied")),
        src_size=int(kwargs.get("src_size", 1024)),  # type: ignore[arg-type]
        src_mtime_ns=int(kwargs.get("src_mtime_ns", 1_000_000)),  # type: ignore[arg-type]
        dst_size=int(kwargs.get("dst_size", 1024)),  # type: ignore[arg-type]
        dst_mtime_ns=int(kwargs.get("dst_mtime_ns", 2_000_000)),  # type: ignore[arg-type]
        error=kwargs.get("error"),  # type: ignore[arg-type]
    )


def _apply(tmp_path: Path, cfg: Config, name: str = "01 稻香.mp3") -> tuple[str, Path]:
    """真实执行一次重命名，返回 (session_id, 目标文件)。"""
    source = tmp_path / name
    tag_mp3(source, title="稻香", artist="周杰伦")
    plan = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template=TEMPLATE,
        allow_missing_fields=True,
    )
    journal = Journal(plan.plan.session_id)
    steps: list[StepResult] = []
    apply_plan(plan.plan, on_step=steps.append)
    for step in steps:
        journal.append(step, template=plan.plan.template, profile=plan.plan.profile)
    return plan.plan.session_id, plan.plan.items[0].dst


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def test_append_writes_valid_jsonl(tmp_path: Path, cfg: Config) -> None:
    session, dst = _apply(tmp_path, cfg)
    del dst
    path = journal_dir() / f"{session}.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["session_id"] == session
    assert lines[0]["seq"] == 1 and lines[1]["seq"] == 2
    assert lines[0]["op"] == "stage1"  # 阶段一：中间态，回退时忽略
    assert lines[1]["op"] == "rename"  # 阶段二：真正的重命名
    assert lines[0]["status"] == "applied"
    assert lines[1]["dst"].endswith("稻香 - 周杰伦.mp3")


def test_entry_fields_match_spec(tmp_path: Path, cfg: Config) -> None:
    session, _ = _apply(tmp_path, cfg)
    entry = Journal(session).read()[0]
    assert sorted(entry.__dict__) == sorted(
        [
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
        ]
    )
    datetime.fromisoformat(entry.ts)  # ISO-8601 可解析


def test_journal_path_uses_xdg(tmp_path: Path, cfg: Config) -> None:
    session, _ = _apply(tmp_path, cfg)
    assert (journal_dir() / f"{session}.jsonl").exists()


def test_read_on_missing_file(tmp_path: Path) -> None:
    assert Journal("nope").read() == []
    assert Journal("nope").summarize().entries == 0


def test_next_seq_counts_lines(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    assert next_seq(path) == 1
    path.write_text('{"a":1}\n{"a":2}\n\n', encoding="utf-8")
    assert next_seq(path) == 3


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text('{"session_id":"s"}\nnot json\n', encoding="utf-8")
    entries = Journal("s", path=path).read()
    assert len(entries) == 1
    assert entries[0].session_id == "s"


def test_entry_from_dict_coerces_types() -> None:
    entry = JournalEntry.from_dict(
        {
            "session_id": "s",
            "ts": "2026-10-07T15:30:12.345+08:00",
            "seq": "7",
            "op": "rename",
            "src": "/a",
            "dst": "/b",
            "status": "applied",
            "src_size": 10,
            "error": None,
        }
    )
    assert entry.seq == 7
    assert entry.template == ""
    assert "session_id" in entry.to_line()


# ---------------------------------------------------------------------------
# 会话列表
# ---------------------------------------------------------------------------


def test_list_sessions_newest_first(tmp_path: Path, cfg: Config) -> None:
    first, _ = _apply(tmp_path, cfg, "01 a.mp3")
    second, _ = _apply(tmp_path, cfg, "02 b.mp3")
    sessions = list_sessions()
    assert len(sessions) == 2
    timestamps = [summary.ts for summary in sessions]
    assert timestamps == sorted(timestamps, reverse=True)
    assert {summary.session_id for summary in sessions} == {first, second}


def test_latest_session(tmp_path: Path, cfg: Config) -> None:
    assert latest_session() is None
    session, _ = _apply(tmp_path, cfg)
    assert latest_session() == session


def test_cleanup_keeps_recent(tmp_path: Path, cfg: Config) -> None:
    for index in range(3):
        _apply(tmp_path, cfg, f"{index:02d} song.mp3")
    assert len(list_sessions()) == 3
    removed = cleanup_sessions(keep=2)
    assert removed == 1
    assert len(list_sessions()) == 2


def test_cleanup_disabled_when_keep_not_positive(tmp_path: Path, cfg: Config) -> None:
    _apply(tmp_path, cfg)
    assert cleanup_sessions(keep=0) == 0


def test_summary_statuses(tmp_path: Path, cfg: Config) -> None:
    session, _ = _apply(tmp_path, cfg)
    summary = Journal(session).summarize()
    assert summary.status == "applied"
    assert summary.applied == 2
    as_failed = SessionSummary(session_id="s", ts="", path=Path("x"), entries=1, failed=1)
    assert as_failed.status == "partially_failed"


def test_session_timestamp() -> None:
    assert session_timestamp("20261007-153012-8f3a") == "20261007"
    assert session_timestamp("garbage") == ""


# ---------------------------------------------------------------------------
# 回退
# ---------------------------------------------------------------------------


def test_undo_restores_original_names(tmp_path: Path, cfg: Config) -> None:
    session, dst = _apply(tmp_path, cfg)
    assert dst.exists()
    result = undo_session(session)
    assert len(result.undone) == 1
    assert not dst.exists()
    assert (tmp_path / "01 稻香.mp3").exists()
    assert Journal(session).summarize().undone == 1


def test_undo_dry_run_changes_nothing(tmp_path: Path, cfg: Config) -> None:
    session, dst = _apply(tmp_path, cfg)
    result = undo_session(session, dry_run=True)
    assert len(result.undone) == 1
    assert dst.exists()
    assert Journal(session).summarize().undone == 0


def test_undo_without_session_is_noop() -> None:
    assert undo_session(None).undone == []


def test_undo_checks_size_and_mtime(tmp_path: Path, cfg: Config) -> None:
    session, dst = _apply(tmp_path, cfg)
    dst.write_bytes(b"modified content")
    result = undo_session(session)
    assert len(result.needs_manual) == 1
    assert "大小不一致" in result.needs_manual[0][1]
    assert dst.exists()


def test_undo_skips_missing_target(tmp_path: Path, cfg: Config) -> None:
    session, dst = _apply(tmp_path, cfg)
    dst.unlink()
    result = undo_session(session)
    assert len(result.needs_manual) == 1
    assert "不存在" in result.needs_manual[0][1]


def test_undo_skips_when_source_reoccupied(tmp_path: Path, cfg: Config) -> None:
    session, dst = _apply(tmp_path, cfg)
    (tmp_path / "01 稻香.mp3").write_bytes(b"new file")
    result = undo_session(session)
    assert len(result.needs_manual) == 1
    assert "已被占用" in result.needs_manual[0][1]
    assert dst.exists()


def test_undo_respects_only_pattern(tmp_path: Path, cfg: Config) -> None:
    source_a = tmp_path / "01 a.mp3"
    source_b = tmp_path / "02 b.mp3"
    tag_mp3(source_a, title="稻香", artist="周杰伦")
    tag_mp3(source_b, title="晴天", artist="周杰伦")
    plan = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source_a), FileCandidate(path=source_b)],
        cfg,
        template=TEMPLATE,
        allow_missing_fields=True,
    )
    journal = Journal(plan.plan.session_id)
    apply_plan(plan.plan, on_step=lambda step: journal.append(step))
    session = plan.plan.session_id

    result = undo_session(session, only="稻香*")
    assert len(result.undone) == 1
    assert (tmp_path / "01 a.mp3").exists()
    assert (tmp_path / "晴天 - 周杰伦.mp3").exists()


def test_undo_twice_is_idempotent(tmp_path: Path, cfg: Config) -> None:
    session, _ = _apply(tmp_path, cfg)
    undo_session(session)
    second = undo_session(session)
    assert second.undone == []
    assert second.needs_manual == []


def test_verify_entry_on_healthy_file(tmp_path: Path) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"x")
    fs = LocalFileSystem()
    entry = JournalEntry(
        session_id="s",
        ts=datetime.now(timezone.utc).isoformat(),
        seq=1,
        op="rename",
        src=str(tmp_path / "src.mp3"),
        dst=str(path),
        status="applied",
        dst_size=1,
        dst_mtime_ns=path.stat().st_mtime_ns,
    )
    assert verify_entry(entry, fs) is None


def test_partially_failed_session_summary(tmp_path: Path, cfg: Config) -> None:
    session, dst = _apply(tmp_path, cfg)
    del dst
    journal = Journal(session)
    journal.append(_step(Path("/a"), Path("/b"), status="failed"))
    assert journal.summarize().failed == 1


def test_journal_is_append_only(tmp_path: Path, cfg: Config) -> None:
    session, _ = _apply(tmp_path, cfg)
    before = journal_dir().joinpath(f"{session}.jsonl").read_text(encoding="utf-8")
    undo_session(session)
    after = journal_dir().joinpath(f"{session}.jsonl").read_text(encoding="utf-8")
    assert after.startswith(before)
    assert len(after.splitlines()) > len(before.splitlines())


def test_journal_survives_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "s.jsonl"
    journal = Journal("s", path=target)
    journal.append(_step(Path("/a"), Path("/b")))
    assert target.exists()


def test_summary_status_undone_and_partial() -> None:
    done = SessionSummary(
        session_id="s", ts="", path=Path("x.jsonl"), entries=2, applied=2, undone=2
    )
    assert done.status == "undone"
    partial = SessionSummary(
        session_id="s", ts="", path=Path("x.jsonl"), entries=3, applied=2, undone=1
    )
    assert partial.status == "partially_undone"


def test_journal_exists_property(tmp_path: Path, cfg: Config) -> None:
    journal = Journal("missing")
    assert journal.exists is False
    session, _ = _apply(tmp_path, cfg)
    assert Journal(session).exists is True


def test_cleanup_survives_unremovable_files(tmp_path: Path, cfg: Config, monkeypatch) -> None:
    for index in range(2):
        _apply(tmp_path, cfg, f"{index:02d} song.mp3")

    class LockedPath(type(Path())):  # type: ignore[misc]
        def unlink(self, *args: object, **kwargs: object) -> None:
            msg = "locked"
            raise PermissionError(13, msg)

    monkeypatch.setattr(Path, "unlink", LockedPath.unlink)
    assert cleanup_sessions(keep=1) == 0
    assert len(list_sessions()) == 2


def test_undo_result_ok_flag() -> None:
    from renomate.journal import UndoResult

    assert UndoResult().ok is True
    manual = UndoResult(needs_manual=[(JournalEntry.from_dict({}), "x")])
    assert manual.ok is False


def test_verify_entry_mtime_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "a.mp3"
    path.write_bytes(b"abc")
    fs = LocalFileSystem()
    entry = JournalEntry(
        session_id="s",
        ts="2026-01-01T00:00:00",
        seq=1,
        op="rename",
        src=str(tmp_path / "src.mp3"),
        dst=str(path),
        status="applied",
        dst_size=3,
        dst_mtime_ns=1,
    )
    assert "修改时间不一致" in (verify_entry(entry, fs) or "")


def test_verify_entry_stat_failure(tmp_path: Path) -> None:
    class BrokenStatFS(LocalFileSystem):
        def stat(self, path: Path) -> object:
            msg = "gone"
            raise OSError(2, msg)

    path = tmp_path / "a.mp3"
    path.write_bytes(b"x")
    entry = JournalEntry(
        session_id="s",
        ts="2026-01-01T00:00:00",
        seq=1,
        op="rename",
        src=str(tmp_path / "src.mp3"),
        dst=str(path),
        status="applied",
    )
    assert "无法读取" in (verify_entry(entry, BrokenStatFS()) or "")


def test_undo_skips_non_applied_entries(tmp_path: Path, cfg: Config) -> None:
    session, _ = _apply(tmp_path, cfg)
    journal = Journal(session)
    journal.append(_step(Path("/no/such"), Path("/no/dst"), status="skipped"))
    result = undo_session(session)
    assert len(result.undone) == 1
    assert result.skipped == []


def test_undo_rename_error_is_recorded(tmp_path: Path, cfg: Config) -> None:
    session, _ = _apply(tmp_path, cfg)

    class FailRenameFS(LocalFileSystem):
        def rename(self, src: Path, dst: Path) -> None:
            msg = "busy"
            raise OSError(16, msg)

    result = undo_session(session, fs=FailRenameFS())
    assert len(result.needs_manual) == 1
    assert "busy" in result.needs_manual[0][1]


def test_opt_int_edge_cases() -> None:
    from renomate.journal import _opt_int

    assert _opt_int(None) is None
    assert _opt_int(True) is None
    assert _opt_int(7) == 7
    assert _opt_int("9") == 9
    assert _opt_int("x") is None
    assert _opt_int(object()) is None
