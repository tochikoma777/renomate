"""端到端演练（SPEC 19.1）：真实目录 → apply → undo → 逐字节还原。

覆盖：中文名、重名、大小写仅不同、无标签、坏文件、只读文件、子目录。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from renomate.config import Config, default_config
from renomate.executor import apply_plan
from renomate.journal import Journal, undo_session
from renomate.models import ItemStatus
from renomate.plan import build_plan, new_session_id
from renomate.safety import BlockedPathError
from renomate.scanner import FileCandidate, ScanOptions, scan
from tests.conftest import tag_mp3

TEMPLATE = "{title} - {artist}"


def _fixture_lib(root: Path) -> Path:
    """造一个「什么破事都有」的目录。"""
    (root / "周杰伦" / "魔杰座").mkdir(parents=True)
    tag_mp3(root / "周杰伦" / "魔杰座" / "01 稻香.mp3", title="稻香", artist="周杰伦")
    tag_mp3(root / "周杰伦" / "晴天[320K].mp3", title="晴天", artist="周杰伦")
    # 重名：两个文件渲染后目标名相同
    tag_mp3(root / "a.mp3", title="同名", artist="周杰伦")
    tag_mp3(root / "b.mp3", title="同名", artist="周杰伦")
    # 大小写仅不同
    tag_mp3(root / "Case.MP3", title="Case", artist="周杰伦")
    tag_mp3(root / "case.mp3", title="case", artist="周杰伦")
    # 无标签
    (root / "unknown.mp3").write_bytes(
        b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90d" + b"\x00" * 400
    )
    # 坏文件
    (root / "broken.mp3").write_bytes(b"not an audio file")
    # 非白名单
    (root / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    # 只读文件
    readonly = root / "readonly.mp3"
    tag_mp3(readonly, title="只读", artist="周杰伦")
    readonly.chmod(0o444)
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    """记录所有文件名的**字节**（含大小写），用于逐字节比对。"""
    snapshot: dict[str, bytes] = {}
    for directory, _subdirs, files in os.walk(root):
        for name in files:
            path = Path(directory) / name
            snapshot[str(path.relative_to(root))] = os.fsencode(name)
    return snapshot


def test_end_to_end_apply_and_undo_restores_byte_identical(tmp_path: Path, cfg: Config) -> None:
    """apply 后 undo，文件名必须与操作前逐字节一致（SPEC 21.4）。"""
    if getattr(os, "geteuid", lambda: -1)() == 0:  # root 下「只读」不生效，跳过只读断言
        pytest.skip("root 用户下只读位不生效")
    root = _fixture_lib(tmp_path / "Music")
    cfg.safety.allow_missing_fields = True
    before = _snapshot(root)

    options = ScanOptions(extensions=list(cfg.profile("audio").extensions))
    candidates = scan(root, options).candidates
    result = build_plan(
        new_session_id(), root, candidates, cfg, template=TEMPLATE, allow_missing_fields=True
    )
    journal = Journal(result.plan.session_id)
    execution = apply_plan(result.plan, on_step=journal.append)

    assert execution.applied, execution.failed
    assert not execution.leftover_tmp

    after_apply = _snapshot(root)
    assert after_apply != before  # 确实改了一些名字

    undo = undo_session(result.plan.session_id)
    assert undo.needs_manual == [], undo.needs_manual
    assert _snapshot(root) == before


def test_end_to_end_keeps_dangerous_files_untouched(tmp_path: Path, cfg: Config) -> None:
    """只读 / 坏文件 / 非白名单文件不会被误改。"""
    if getattr(os, "geteuid", lambda: -1)() == 0:
        pytest.skip("root 用户下只读位不生效")
    root = _fixture_lib(tmp_path / "Music")
    options = ScanOptions(extensions=list(cfg.profile("audio").extensions))
    result = build_plan(
        new_session_id(),
        root,
        scan(root, options).candidates,
        cfg,
        template=TEMPLATE,
        allow_missing_fields=True,
    )
    reasons = {item.src.name: item.reason for item in result.plan.items}
    assert "not_writable" in reasons["readonly.mp3"]
    assert "白名单" in reasons["cover.jpg"]  # 扫描阶段就被闸门 2 拦下

    apply_plan(result.plan)
    assert (root / "readonly.mp3").exists()
    assert (root / "cover.jpg").exists()


def test_end_to_end_conflict_gets_suffix(tmp_path: Path, cfg: Config) -> None:
    """两个文件渲染出同名 → 自动加 (2)。"""
    root = tmp_path / "Music"
    root.mkdir()
    tag_mp3(root / "a.mp3", title="同名", artist="周杰伦")
    tag_mp3(root / "b.mp3", title="同名", artist="周杰伦")
    result = build_plan(
        new_session_id(),
        root,
        [FileCandidate(path=root / "a.mp3"), FileCandidate(path=root / "b.mp3")],
        cfg,
        template=TEMPLATE,
    )
    names = sorted(i.dst.name for i in result.actionable)
    assert names == ["同名 - 周杰伦 (2).mp3", "同名 - 周杰伦.mp3"]
    assert any(i.conflict_resolved_by == "auto-suffix (2)" for i in result.plan.items)


def test_end_to_end_no_net_works(tmp_path: Path) -> None:
    """断网（--no-net）下全部功能正常：网络 Provider 直接关闭。"""
    cfg = default_config()
    cfg.musicbrainz.enabled = True  # 配置里开着，但 CLI --no-net 会关掉
    root = tmp_path / "Music"
    (root / "周杰伦").mkdir(parents=True)
    tag_mp3(root / "周杰伦" / "01 稻香.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    from renomate.cli import main

    assert main(["--no-net", "preview", str(root)]) == 0
    assert main(["--no-net", "scan", str(root)]) == 0
    plan_result = build_plan(
        new_session_id(),
        root,
        scan(root, ScanOptions(extensions=["mp3"])).candidates,
        cfg,
        template="{title} - {artist} - {album}",
        use_network=False,
    )
    assert plan_result.stats.network_queries == 0
    assert plan_result.actionable[0].dst.name == "稻香 - 周杰伦 - 魔杰座.mp3"


def test_scan_on_windows_system_dir_is_blocked_with_reason() -> None:
    """对 `/mnt/c/Windows` 执行 scan 必须被拦截，并给出清晰原因（SPEC 19.1）。

    该用例仅在使用 WSL 且能访问 /mnt/c 的环境有意义；其余环境跳过。
    """
    if not Path("/mnt/c").exists():
        pytest.skip("WSL-only test: /mnt/c not available")
    with pytest.raises(BlockedPathError) as exc:
        scan(Path("/mnt/c/WINDOWS"))
    assert "黑名单" in exc.value.reason
    assert exc.value.path == Path("/mnt/c/WINDOWS")

    from renomate.safety import is_blocked_path

    assert is_blocked_path(Path("/mnt/c/Windows/System32")) is not None
    assert is_blocked_path(Path("C:\\Windows")) is not None


def test_cli_reports_blocked_root_with_exit_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI 层：黑名单目录 → 退出码 2 + 清晰原因。"""
    import renomate.cli as cli_module
    from renomate.cli import EXIT_ALL_FAILED, main

    def fake_scan(directory: Path, options: object) -> object:
        msg = "路径位于黑名单目录（/mnt/c/windows）"
        raise BlockedPathError(directory, msg)

    monkeypatch.setattr(cli_module, "scan", fake_scan)
    code = main(["scan", str(tmp_path)])
    assert code == EXIT_ALL_FAILED
    assert "黑名单" in capsys.readouterr().err


def test_end_to_end_partial_failure_is_reported(tmp_path: Path, cfg: Config) -> None:
    """阶段二失败会回滚，失败项标记为 FAILED 且不中断其它文件。"""
    root = tmp_path / "Music"
    root.mkdir()
    first = root / "a.mp3"
    second = root / "b.mp3"
    tag_mp3(first, title="第一首", artist="周杰伦")
    tag_mp3(second, title="第二首", artist="周杰伦")

    result = build_plan(
        new_session_id(),
        root,
        [FileCandidate(path=first), FileCandidate(path=second)],
        cfg,
        template=TEMPLATE,
    )

    class FailSecondPlan:
        """让第二个文件在阶段二失败（模拟文件被播放器占用）。"""

    del FailSecondPlan
    from renomate.platform.fs import LocalFileSystem

    class BlockedFS(LocalFileSystem):
        def rename(self, src: Path, dst: Path) -> None:
            if dst.name == "第二首 - 周杰伦.mp3":
                msg = "device or resource busy"
                raise OSError(16, msg)
            super().rename(src, dst)

    execution = apply_plan(result.plan, fs=BlockedFS())
    assert len(execution.applied) == 1
    assert len(execution.failed) == 1
    assert (root / "第一首 - 周杰伦.mp3").exists()
    assert second.exists()  # 已回滚
    statuses = {i.src.name: i.status for i in result.plan.items}
    assert statuses["b.mp3"] is ItemStatus.FAILED
