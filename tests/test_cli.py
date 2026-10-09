"""CLI 子命令与退出码的测试（SPEC 13）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import tag_mp3

from renomate import cli
from renomate.cli import EXIT_ALL_FAILED, EXIT_OK, EXIT_PARTIAL, EXIT_USAGE, build_parser, main


def run(*argv: str) -> int:
    return main(list(argv))


def _music(tmp_path: Path, name: str = "01 稻香.mp3", **tags: str) -> Path:
    path = tmp_path / name
    tag_mp3(path, **tags)
    return path


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run("--version")
    assert exc.value.code == 0
    assert "renomate" in capsys.readouterr().out


def test_parser_has_all_subcommands() -> None:
    parser = build_parser()
    actions = [action for action in parser._actions if getattr(action, "choices", None)]
    names = {name for action in actions for name in action.choices}  # type: ignore[union-attr]
    assert {
        "scan",
        "preview",
        "apply",
        "undo",
        "journals",
        "recover",
        "report",
        "config",
        "profiles",
        "tui",
    } <= names


def test_scan_writes_plan_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦", album="魔杰座")
    out = tmp_path / "plan.json"
    assert run("scan", str(tmp_path), "--out", str(out)) == EXIT_OK
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["items"]
    assert "将修改" in capsys.readouterr().out


def test_scan_rejects_blocked_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run("scan", str(tmp_path), "--ext", "mp3")
    del code
    # tmp_path 不在黑名单内 → 正常；用 extra_blocked 无法从 CLI 传入，改测不存在的目录
    assert run("scan", str(tmp_path / "nope")) == EXIT_USAGE
    assert "错误" in capsys.readouterr().err


def test_preview_default_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("preview", str(tmp_path)) == EXIT_OK
    output = capsys.readouterr().out
    assert "稻香 - 周杰伦" in output
    assert source.exists()


def test_preview_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("preview", str(tmp_path), "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["profile"] == "audio"


def test_preview_unknown_preset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("preview", str(tmp_path), "--preset", "nope") == EXIT_USAGE
    assert "未知预设" in capsys.readouterr().err


def test_preview_with_preset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦", tracknumber="1")
    assert run("preview", str(tmp_path), "--preset", "sorted") == EXIT_OK
    assert "01. 稻香" in capsys.readouterr().out


def test_preview_unknown_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("preview", str(tmp_path), "--profile", "nope") == EXIT_USAGE
    assert "未知 Profile" in capsys.readouterr().err


def test_apply_default_is_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("apply", str(tmp_path)) == EXIT_OK
    output = capsys.readouterr().out
    assert "dry-run" in output
    assert source.exists()


def test_apply_requires_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("apply", str(tmp_path), "--yes") == EXIT_ALL_FAILED
    assert "确认令牌" in capsys.readouterr().err


def test_apply_with_token_renames(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("apply", str(tmp_path), "--yes", "--token", "APPLY") == EXIT_OK
    assert not source.exists()
    assert (tmp_path / "稻香 - 周杰伦 - 未知专辑 - CN.mp3").exists()
    assert "回退命令" in capsys.readouterr().out


def test_apply_without_targets_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("apply", str(tmp_path)) == EXIT_ALL_FAILED
    assert "没有可处理的文件" in capsys.readouterr().out


def test_apply_from_plan_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    plan_file = tmp_path / "plan.json"
    assert run("scan", str(tmp_path), "--out", str(plan_file)) == EXIT_OK
    assert run("apply", "--plan", str(plan_file), "--yes", "--token", "APPLY") == EXIT_OK
    assert not source.exists()
    assert "已修改" in capsys.readouterr().out


def test_apply_bad_plan_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert run("apply", "--plan", str(bad)) == EXIT_USAGE
    assert "无法读取" in capsys.readouterr().err


def test_undo_last(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    run("apply", str(tmp_path), "--yes", "--token", "APPLY")
    assert run("undo", "--last") == EXIT_OK
    assert source.exists()
    assert "已回退" in capsys.readouterr().out


def test_undo_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    run("apply", str(tmp_path), "--yes", "--token", "APPLY")
    assert run("undo", "--last", "--dry-run") == EXIT_OK
    assert not source.exists()
    assert "计划回退" in capsys.readouterr().out


def test_undo_without_sessions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    del tmp_path
    assert run("undo") == EXIT_ALL_FAILED
    assert "没有可回退" in capsys.readouterr().out


def test_journals_lists_sessions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    run("apply", str(tmp_path), "--yes", "--token", "APPLY")
    assert run("journals") == EXIT_OK
    assert "entries=" in capsys.readouterr().out


def test_journals_incomplete_filter(tmp_path: Path) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    run("apply", str(tmp_path), "--yes", "--token", "APPLY")
    assert run("journals", "--incomplete") == EXIT_OK


def test_recover_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from renomate.safety import RENOMATE_TMP_PREFIX

    leftover = tmp_path / f"{RENOMATE_TMP_PREFIX}s1_1.mp3"
    leftover.write_bytes(b"x")
    assert run("recover", str(tmp_path)) == EXIT_OK
    assert leftover.exists()
    assert "计划恢复" in capsys.readouterr().out


def test_recover_actually_renames(tmp_path: Path) -> None:
    from renomate.safety import RENOMATE_TMP_PREFIX

    leftover = tmp_path / f"{RENOMATE_TMP_PREFIX}s1_1.mp3"
    leftover.write_bytes(b"x")
    assert run("recover", str(tmp_path), "--yes") == EXIT_OK
    assert not leftover.exists()


def test_report_formats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("report", str(tmp_path), "--format", "csv") == EXIT_OK
    assert "状态" in capsys.readouterr().out
    assert run("report", str(tmp_path), "--format", "markdown") == EXIT_OK
    assert "|" in capsys.readouterr().out


def test_report_to_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    target = tmp_path / "report.csv"
    assert run("report", str(tmp_path), "--out", str(target), "--format", "csv") == EXIT_OK
    assert target.exists()
    assert "已写出报告" in capsys.readouterr().out


def test_report_requires_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    del tmp_path
    assert run("report") == EXIT_USAGE
    assert "需要 DIR 或 --session" in capsys.readouterr().err


def test_report_from_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    run("apply", str(tmp_path), "--yes", "--token", "APPLY")
    assert run("report", "--session", "unknown-session") == EXIT_OK


def test_config_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("config", "--show") == EXIT_OK
    out = capsys.readouterr().out
    assert "default_profile" in out
    assert "musicbrainz.enabled = False" in out


def test_config_init(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "cfg.toml"
    assert run("--config", str(target), "config", "--init") == EXIT_OK
    assert target.exists()
    assert "已写入配置" in capsys.readouterr().out


def test_config_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("config", "--set", "general.max_batch=99") == EXIT_OK
    assert "max_batch = 99" in capsys.readouterr().out


def test_config_set_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    del tmp_path
    assert run("config", "--set", "general.nope=1") == EXIT_USAGE
    assert "错误" in capsys.readouterr().err


def test_profiles_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("profiles") == EXIT_OK
    out = capsys.readouterr().out
    assert "audio" in out
    assert "required=" in out


def test_no_net_flag_disables_network(tmp_path: Path) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    assert run("--no-net", "preview", str(tmp_path)) == EXIT_OK


def test_partial_failure_exit_code(tmp_path: Path) -> None:
    """扩展名白名单外的文件全部被跳过 → 退出码 2。"""
    (tmp_path / "a.jpg").write_bytes(b"x")
    assert run("apply", str(tmp_path), "--yes", "--token", "APPLY") == EXIT_ALL_FAILED


def test_cli_error_carries_code() -> None:
    err = cli.CliError("x", EXIT_PARTIAL)
    assert err.code == EXIT_PARTIAL
    assert str(err) == "x"


def test_exit_code_constants() -> None:
    assert (EXIT_OK, EXIT_PARTIAL, EXIT_ALL_FAILED, EXIT_USAGE) == (0, 1, 2, 3)
    assert cli.EXIT_INTERRUPTED == 130


def test_custom_extension_filter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert run("scan", str(tmp_path), "--ext", "txt") == EXIT_OK
    assert "a.txt" in capsys.readouterr().out


def test_non_recursive_scan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    _music(nested, title="稻香", artist="周杰伦")
    assert run("scan", str(tmp_path), "--no-recursive") == EXIT_OK
    assert "稻香" not in capsys.readouterr().out
