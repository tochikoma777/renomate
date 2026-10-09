"""执行器的测试：两阶段 rename、大小写陷阱、失败隔离与残留检测。"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import tag_mp3

from renomate.config import Config, default_config
from renomate.executor import (
    ExecutionResult,
    StepResult,
    apply_plan,
    case_tmp_name,
    find_leftovers,
    now_iso,
    recover_leftovers,
    rename_case_safe,
    same_ignoring_case,
    tmp_name,
)
from renomate.models import ItemStatus, Plan, RenameItem
from renomate.plan import build_plan, new_session_id
from renomate.platform.fs import FileSystem, LocalFileSystem
from renomate.safety import RENOMATE_CASE_PREFIX, RENOMATE_TMP_PREFIX


class FailingRenameFS(LocalFileSystem):
    """让指定路径 rename 失败的文件系统（模拟 EXDEV / EACCES）。"""

    def __init__(self, fail_on: set[str] | None = None, *, fail_all: bool = False) -> None:
        self.fail_on = fail_on or set()
        self.fail_all = fail_all
        self.calls: list[tuple[Path, Path]] = []

    def rename(self, src: Path, dst: Path) -> None:
        self.calls.append((src, dst))
        if self.fail_all or src.name in self.fail_on:
            msg = "boom"
            raise OSError(18, msg)  # EXDEV


def _plan(root: Path, cfg: Config, template: str = "{title} - {artist}") -> Plan:
    from renomate.scanner import ScanOptions, scan

    options = ScanOptions(extensions=list(cfg.profile("audio").extensions))
    candidates = scan(root, options).candidates
    result = build_plan(
        new_session_id(),
        root,
        candidates,
        cfg,
        template=template,
        allow_missing_fields=True,
    )
    return result.plan


def _music(tmp_path: Path, name: str = "a.mp3", **tags: str) -> Path:
    path = tmp_path / name
    tag_mp3(path, **tags)
    return path


def test_two_phase_rename_avoids_chain_overwrite(tmp_path: Path, cfg: Config) -> None:
    """A → B、B → C 同时存在时也不能丢文件（A 的 dst 就是 B 的 src）。"""
    from renomate.scanner import FileCandidate

    tail = _music(tmp_path, "晴天.mp3", title="稻香", artist="周杰伦")  # → 稻香 - 周杰伦
    head = _music(tmp_path, "01 稻香.mp3", title="晴天", artist="周杰伦")  # → 晴天.mp3 的位置
    del tail
    source_b = tmp_path / "晴天.mp3"
    source_a = tmp_path / "01 稻香.mp3"
    del head
    plan = build_plan(
        "session-1",
        tmp_path,
        [FileCandidate(path=source_a), FileCandidate(path=source_b)],
        cfg,
        template="{title} - {artist}",
        allow_missing_fields=True,
    ).plan
    # head 的目标名字正好是 tail 的当前名字 → 构成链式依赖
    targets = {item.dst.name for item in plan.items}
    assert "晴天 - 周杰伦.mp3" in targets
    plan.items[0].dst = source_b

    result = apply_plan(plan)
    assert result.ok, result.failed
    assert len(result.applied) == 2
    assert source_b.exists()
    assert (tmp_path / "稻香 - 周杰伦.mp3").exists()


def test_apply_plan_renames_files(tmp_path: Path, cfg: Config) -> None:
    source = _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    result = apply_plan(plan)
    assert result.ok
    assert not source.exists()
    assert (tmp_path / "稻香 - 周杰伦.mp3").exists()


def test_user_unchecked_items_are_skipped(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    plan.items[0].user_checked = False
    result = apply_plan(plan)
    assert result.applied == []
    assert (tmp_path / "01 稻香.mp3").exists()


def test_blocked_and_skipped_items_are_reported(tmp_path: Path, cfg: Config) -> None:
    from renomate.scanner import FileCandidate

    source = _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = build_plan(
        "s1",
        tmp_path,
        [
            FileCandidate(path=source),
            FileCandidate(path=tmp_path / "gone.mp3", skip_reason="not_writable"),
        ],
        cfg,
        template="{title} - {artist}",
    ).plan
    result = apply_plan(plan)
    assert len(result.skipped) >= 1


def test_stage1_failure_keeps_original_name(tmp_path: Path, cfg: Config) -> None:
    source = _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    fs = FailingRenameFS(fail_on={source.name})
    result = apply_plan(plan, fs=fs)
    assert len(result.failed) == 1
    assert "stage1_failed" in result.failed[0][1]
    assert source.exists()


def test_stage2_failure_rolls_back(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)

    final_name = plan.items[0].dst.name

    class Stage2Only(LocalFileSystem):
        """只让阶段二（临时名 → 最终名）失败，回退仍能成功。"""

        def rename(self, src: Path, dst: Path) -> None:
            if src.name.startswith(RENOMATE_TMP_PREFIX) and dst.name == final_name:
                msg = "boom"
                raise OSError(18, msg)
            super().rename(src, dst)

    fs = Stage2Only()
    result = apply_plan(plan, fs=fs)
    assert len(result.failed) == 1
    assert result.failed[0][1].startswith("stage2_failed")
    assert (tmp_path / "01 稻香.mp3").exists()  # 已回滚
    assert not result.leftover_tmp


def test_stage2_failure_that_cannot_rollback_needs_manual(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)

    class FailEverythingAfterFirst(LocalFileSystem):
        """阶段一成功，阶段二与回退都失败 → needs_manual。"""

        def __init__(self) -> None:
            self.calls = 0

        def rename(self, src: Path, dst: Path) -> None:
            self.calls += 1
            if self.calls > 1:
                msg = "boom"
                raise OSError(13, msg)
            super().rename(src, dst)

    result = apply_plan(plan, fs=FailEverythingAfterFirst())
    assert len(result.failed) == 1
    assert result.failed[0][1].startswith("needs_manual")
    assert result.leftover_tmp


def test_leftover_tmp_files_are_detected(tmp_path: Path, cfg: Config) -> None:
    """执行结束后仍有 `.renomate_tmp_` 残留 → 必须被检测出来以便报警。"""
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    leftover = tmp_path / f"{RENOMATE_TMP_PREFIX}s1_1.mp3"
    leftover.write_bytes(b"x")
    plan = _plan(tmp_path, cfg)
    found = find_leftovers(plan.items, LocalFileSystem())
    assert leftover in found


def test_recover_leftovers_dry_run(tmp_path: Path) -> None:
    leftover = tmp_path / f"{RENOMATE_TMP_PREFIX}20261007-153012-8f3a_1.mp3"
    leftover.write_bytes(b"x")
    actions = recover_leftovers(tmp_path, dry_run=True)
    assert len(actions) == 1
    assert leftover.exists()


def test_recover_leftovers_renames_back(tmp_path: Path) -> None:
    leftover = tmp_path / f"{RENOMATE_TMP_PREFIX}20261007-153012-8f3a_1.mp3"
    leftover.write_bytes(b"x")
    actions = recover_leftovers(tmp_path, dry_run=False)
    assert len(actions) == 1
    assert not leftover.exists()


def test_case_only_rename_uses_intermediate(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    source = tmp_path / "Song.MP3"
    source.write_bytes(b"x")
    rename_case_safe(source, tmp_path / "song.mp3", fs=fs)
    assert (tmp_path / "song.mp3").exists()


def test_case_rename_on_insensitive_fs(tmp_path: Path) -> None:
    """大小写不敏感 FS：先落到 case 临时名再改到目标。"""
    seen: list[Path] = []

    class InsensitiveFS(LocalFileSystem):
        def exists(self, path: Path) -> bool:
            if not path.parent.is_dir():
                return False
            target = path.name.casefold()
            return any(s.name.casefold() == target for s in path.parent.iterdir())

        def rename(self, src: Path, dst: Path) -> None:
            seen.append(dst)
            super().rename(src, dst)

    source = tmp_path / "Song.MP3"
    source.write_bytes(b"x")
    rename_case_safe(source, tmp_path / "song.mp3", fs=InsensitiveFS())
    assert any(name.name.startswith(RENOMATE_CASE_PREFIX) for name in seen)
    assert (tmp_path / "song.mp3").exists()


def test_same_ignoring_case_helper() -> None:
    assert same_ignoring_case(Path("/a/B.mp3"), Path("/a/b.MP3")) is True
    assert same_ignoring_case(Path("/a/b.mp3"), Path("/a/c.mp3")) is False


def test_tmp_name_and_case_tmp_name() -> None:
    assert tmp_name(Path("x.mp3"), "s1", 3) == f"{RENOMATE_TMP_PREFIX}s1_3.mp3"
    generated = case_tmp_name(Path("x.MP3"))
    assert generated.startswith(RENOMATE_CASE_PREFIX)
    assert generated.endswith(".MP3")


def test_subdirectory_target_is_created(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    plan = _plan(tmp_path, cfg, template="{album}/{title} - {artist}")
    result = apply_plan(plan)
    assert result.ok
    assert (tmp_path / "魔杰座" / "稻香 - 周杰伦.mp3").exists()


def test_on_step_logger_records_both_stages(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    steps: list[StepResult] = []
    apply_plan(plan, on_step=steps.append)
    assert [s.op for s in steps] == ["stage1", "rename"]
    assert all(s.status == "applied" for s in steps)
    assert steps[0].dst.name.startswith(RENOMATE_TMP_PREFIX)
    assert steps[1].dst.name == "稻香 - 周杰伦.mp3"


def test_sizes_and_mtimes_are_snapshotted(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    steps: list[StepResult] = []
    apply_plan(plan, on_step=steps.append)
    assert steps[0].src_size is not None and steps[0].src_size > 0
    assert steps[0].src_mtime_ns is not None and steps[0].src_mtime_ns > 0
    assert steps[1].dst_size == steps[0].src_size


def test_now_iso_format() -> None:
    assert "T" in now_iso()
    assert len(now_iso()) >= 19


def test_execution_result_ok_flag() -> None:
    empty = ExecutionResult()
    assert empty.ok is True
    assert isinstance(empty.applied, list)


def test_apply_plan_on_empty_plan(tmp_path: Path) -> None:
    plan = Plan(session_id="s", root=tmp_path, profile="audio", template="{title}", items=[])
    result = apply_plan(plan)
    assert result.ok
    assert result.applied == []


def test_item_status_is_updated(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    item = plan.items[0]
    assert item.status is ItemStatus.READY
    apply_plan(plan)
    assert item.status is ItemStatus.APPLIED


@pytest.mark.parametrize("fs_class", [LocalFileSystem])
def test_filesystem_protocol_satisfied(fs_class: type[FileSystem]) -> None:
    assert isinstance(fs_class(), FileSystem)


def test_default_config_used_when_plan_reused(tmp_path: Path) -> None:
    cfg = default_config()
    assert isinstance(cfg.safety.extra_blocked, list)
    rename_item = RenameItem(
        src=tmp_path / "a.mp3",
        dst=tmp_path / "b.mp3",
        status=ItemStatus.READY,
        bundle=__import__("renomate.models", fromlist=["MetadataBundle"]).MetadataBundle(
            path=tmp_path / "a.mp3", profile="audio"
        ),
    )
    assert rename_item.user_checked is True


def test_rename_case_safe_on_same_file_via_symlink(tmp_path: Path) -> None:
    """src 与 dst resolve 到同一文件时直接 rename（不产生中间名）。"""
    fs = LocalFileSystem()
    source = tmp_path / "a.mp3"
    source.write_bytes(b"x")
    link = tmp_path / "b.mp3"
    link.symlink_to(source)
    rename_case_safe(source, link, fs=fs)
    assert not source.exists()
    assert link.is_file()


def test_snapshot_missing_file_returns_none(tmp_path: Path) -> None:
    from renomate.executor import ExecutionResult, StepResult, _snapshot

    size, mtime = _snapshot(LocalFileSystem(), tmp_path / "nope.mp3")
    assert size is None and mtime is None
    del ExecutionResult, StepResult


def test_subdirectory_created_logs_mkdir_step(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    plan = _plan(tmp_path, cfg, template="{album}/{title} - {artist}")
    steps: list[StepResult] = []
    result = apply_plan(plan, on_step=steps.append)
    assert result.ok
    assert any(step.op == "mkdir" and step.status == "applied" for step in steps)


def test_mkdir_failure_is_reported(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    plan = _plan(tmp_path, cfg, template="{album}/{title} - {artist}")

    class NoMkdirFS(LocalFileSystem):
        def mkdir(self, path: Path, *, parents: bool = True) -> None:
            msg = "read-only"
            raise PermissionError(13, msg)

    steps: list[StepResult] = []
    result = apply_plan(plan, fs=NoMkdirFS(), on_step=steps.append)
    assert len(result.failed) == 1
    assert any(step.op == "mkdir" and step.status == "failed" for step in steps)


def test_stage1_failure_is_logged(tmp_path: Path, cfg: Config) -> None:
    source = _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    steps: list[StepResult] = []
    apply_plan(plan, fs=FailingRenameFS(fail_on={source.name}), on_step=steps.append)
    assert any(step.op == "rename" and step.status == "failed" for step in steps)


def test_stage2_failure_is_logged(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, "01 稻香.mp3", title="稻香", artist="周杰伦")
    plan = _plan(tmp_path, cfg)
    final_name = plan.items[0].dst.name

    class Stage2Only(LocalFileSystem):
        def rename(self, src: Path, dst: Path) -> None:
            if src.name.startswith(RENOMATE_TMP_PREFIX) and dst.name == final_name:
                msg = "boom"
                raise OSError(18, msg)
            super().rename(src, dst)

    steps: list[StepResult] = []
    apply_plan(plan, fs=Stage2Only(), on_step=steps.append)
    assert any(step.status == "failed" for step in steps)


def test_recover_ignores_non_temp_files(tmp_path: Path) -> None:
    (tmp_path / "normal.mp3").write_bytes(b"x")
    leftover = tmp_path / f"{RENOMATE_TMP_PREFIX}s1_2.mp3"
    leftover.write_bytes(b"x")
    actions = recover_leftovers(tmp_path, dry_run=False)
    assert [Path(action[0]).name for action in actions] == [leftover.name]
    assert (tmp_path / "normal.mp3").exists()


def test_recover_on_missing_directory(tmp_path: Path) -> None:
    assert recover_leftovers(tmp_path / "nope") == []
