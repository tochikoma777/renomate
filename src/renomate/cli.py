"""命令行入口（SPEC 13）：默认 dry-run，退出码严格约定。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from renomate import __version__
from renomate.config import (
    Config,
    config_path,
    init_config,
    load_config,
    set_value,
)
from renomate.executor import StepResult, apply_plan
from renomate.journal import (
    Journal,
    latest_session,
    list_sessions,
    undo_session,
)
from renomate.models import FieldValue, ItemStatus, Plan, RenameItem
from renomate.plan import PlanResult, build_plan, conflicts, new_session_id
from renomate.profiles import UnknownProfileError, get_profile, list_profiles
from renomate.report import (
    to_csv,
    to_markdown,
    to_text,
    write_report,
)
from renomate.safety import BlockedPathError
from renomate.scanner import ScanOptions, TooManyFilesError, scan

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ALL_FAILED = 2
EXIT_USAGE = 3
EXIT_INTERRUPTED = 130


class CliError(Exception):
    """CLI 层可预期的错误（携带退出码）。"""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器（子命令见 SPEC 13）。"""
    parser = argparse.ArgumentParser(
        prog="renomate",
        description="安全、预览优先、可一键回退的批量重命名工具",
    )
    parser.add_argument("--version", action="version", version=f"renomate {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试细节")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出结果，不输出过程")
    parser.add_argument("--config", type=Path, default=None, help="指定配置文件路径")
    parser.add_argument("--no-net", action="store_true", help="本次运行禁用网络补全")
    parser.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        help="唯一的黑名单绕过方式（会写日志）",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    scan_cmd = sub.add_parser("scan", help="扫描目录并输出候选文件")
    scan_cmd.add_argument("directory", type=Path)
    scan_cmd.add_argument("--profile", default=None)
    scan_cmd.add_argument("--recursive", action="store_true", default=True)
    scan_cmd.add_argument("--no-recursive", dest="recursive", action="store_false")
    scan_cmd.add_argument("--depth", type=int, default=None)
    scan_cmd.add_argument("--ext", default=None, help="逗号分隔的扩展名，覆盖 Profile")
    scan_cmd.add_argument("--out", type=Path, default=None, help="写出 plan.json")

    preview_cmd = sub.add_parser("preview", help="生成并展示重命名计划（只读）")
    preview_cmd.add_argument("directory", type=Path)
    preview_cmd.add_argument("--profile", default=None)
    preview_cmd.add_argument("--template", default=None)
    preview_cmd.add_argument("--preset", default=None)
    preview_cmd.add_argument("--json", action="store_true")
    preview_cmd.add_argument("--out", type=Path, default=None)

    apply_cmd = sub.add_parser("apply", help="执行重命名（默认 dry-run）")
    apply_cmd.add_argument("directory", type=Path, nargs="?")
    apply_cmd.add_argument("--plan", type=Path, default=None)
    apply_cmd.add_argument("--profile", default=None)
    apply_cmd.add_argument("--template", default=None)
    apply_cmd.add_argument("--preset", default=None)
    apply_cmd.add_argument("--dry-run", action="store_true", default=True)
    apply_cmd.add_argument("--yes", dest="dry_run", action="store_false", help="真正执行")
    apply_cmd.add_argument("--token", default=None, help="确认令牌，默认 APPLY")

    undo_cmd = sub.add_parser("undo", help="回退最近一次或指定 session")
    undo_cmd.add_argument("--session", default=None)
    undo_cmd.add_argument("--last", action="store_true")
    undo_cmd.add_argument("--dry-run", action="store_true")
    undo_cmd.add_argument("--only", default=None, help="按文件名 glob 部分回退")

    journals_cmd = sub.add_parser("journals", help="列出所有 session")
    journals_cmd.add_argument("--incomplete", action="store_true")

    recover_cmd = sub.add_parser("recover", help="清理残留的 .renomate_tmp_* 文件")
    recover_cmd.add_argument("directory", type=Path)
    recover_cmd.add_argument("--dry-run", action="store_true", default=True)
    recover_cmd.add_argument("--yes", dest="dry_run", action="store_false")

    report_cmd = sub.add_parser("report", help="导出计划/结果报告")
    report_cmd.add_argument("directory", type=Path, nargs="?")
    report_cmd.add_argument("--session", default=None)
    report_cmd.add_argument("--format", default="table", choices=["table", "csv", "markdown"])
    report_cmd.add_argument("--out", type=Path, default=None)

    config_cmd = sub.add_parser("config", help="查看/初始化/修改配置")
    config_cmd.add_argument("--init", action="store_true")
    config_cmd.add_argument("--show", action="store_true")
    config_cmd.add_argument("--set", dest="set_value", default=None, metavar="KEY=VALUE")
    config_cmd.add_argument("--force", action="store_true")

    sub.add_parser("profiles", help="列出可用 Profile 与字段定义")

    sub.add_parser("tui", help="打开 Textual 界面")
    return parser


def _load(args: argparse.Namespace) -> Config:
    cfg = load_config(args.config)
    if getattr(args, "no_net", False):
        cfg.musicbrainz.enabled = False
    return cfg


def _template_for(args: argparse.Namespace, cfg: Config, profile_name: str) -> str:
    if getattr(args, "template", None):
        return str(args.template)
    preset = getattr(args, "preset", None)
    if preset:
        if preset not in cfg.presets:
            msg = f"未知预设 {preset!r}（可用: {', '.join(sorted(cfg.presets))}）"
            raise CliError(msg)
        return cfg.presets[preset]
    return cfg.profile(profile_name).default_template


def _scan_options(args: argparse.Namespace, cfg: Config, profile_name: str) -> ScanOptions:
    extensions = None
    if getattr(args, "ext", None):
        extensions = [e.strip() for e in str(args.ext).split(",") if e.strip()]
    else:
        extensions = list(get_profile(profile_name, cfg).extensions)
    return ScanOptions(
        recursive=bool(getattr(args, "recursive", True)),
        max_depth=getattr(args, "depth", None),
        extensions=extensions,
        include_hidden=cfg.general.include_hidden,
        follow_symlinks=cfg.general.follow_symlinks,
        extra_blocked=cfg.safety.extra_blocked,
        allow_override=bool(getattr(args, "i_know_what_im_doing", False)),
        max_files=cfg.general.max_batch,
    )


def _make_plan(args: argparse.Namespace, cfg: Config, directory: Path) -> PlanResult:
    profile_name = getattr(args, "profile", None) or cfg.general.default_profile
    try:
        profile = get_profile(profile_name, cfg)
    except UnknownProfileError as exc:
        raise CliError(str(exc)) from exc

    template = _template_for(args, cfg, profile.name)
    options = _scan_options(args, cfg, profile.name)
    try:
        candidates = scan(directory, options).candidates
    except BlockedPathError as exc:
        raise CliError(f"{exc.reason}：{exc.path}", EXIT_ALL_FAILED) from exc
    except NotADirectoryError as exc:
        raise CliError(str(exc)) from exc
    except TooManyFilesError as exc:
        msg = f"{exc}；请缩小范围或调高 general.max_batch"
        raise CliError(msg, EXIT_ALL_FAILED) from exc

    return build_plan(
        new_session_id(),
        directory,
        candidates,
        cfg,
        template=template,
        profile_name=profile.name,
        use_network=cfg.musicbrainz.enabled,
    )


def _counts_text(plan: Plan) -> str:
    counts = plan.counts()
    ready = counts.get(ItemStatus.READY, 0) + counts.get(ItemStatus.APPLIED, 0)
    return (
        f"将修改 {ready} 个文件，跳过 {counts.get(ItemStatus.SKIPPED, 0)} 个，"
        f"拦截 {counts.get(ItemStatus.BLOCKED, 0)} 个，"
        f"冲突 {counts.get(ItemStatus.CONFLICT, 0)} 个"
    )


def _print_plan(plan: Plan, *, fmt: str = "table") -> None:
    if fmt == "csv":
        print(to_csv(plan))
        return
    if fmt == "markdown":
        print(to_markdown(plan))
        return
    print(to_text(plan))


def _cmd_scan(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    directory: Path = args.directory
    result = _make_plan(args, cfg, directory)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result.plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not args.quiet:
            out(f"已写出计划：{args.out}")
    if not args.quiet:
        out(_counts_text(result.plan))
        _print_plan(result.plan)
    return EXIT_OK


def _cmd_preview(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    result = _make_plan(args, cfg, args.directory)
    if args.json:
        print(json.dumps(result.plan.to_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.out:
        write_report(result.plan, args.out, fmt="csv")
    if not args.quiet:
        out(f"模板：{result.plan.template}")
        out(_counts_text(result.plan))
        _print_plan(result.plan)
        for item in conflicts(result.plan):
            out(f"冲突未消解：{item.src.name} → {item.dst.name}（{item.reason}）")
    return EXIT_OK


def _cmd_apply(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    if args.plan is None and args.directory is None:
        raise CliError("apply 需要 DIR 或 --plan")
    if args.plan is not None:
        plan = _plan_from_file(args.plan)
    else:
        plan = _make_plan(args, cfg, Path(args.directory)).plan

    if not plan.items:
        if not args.quiet:
            out("目录里没有可处理的文件")
        return EXIT_ALL_FAILED

    ready = [i for i in plan.items if i.status is ItemStatus.READY]
    if not ready:
        if not args.quiet:
            out("没有可执行的文件（全部被跳过或拦截）")
            _print_plan(plan)
        return EXIT_ALL_FAILED

    if args.dry_run:
        if not args.quiet:
            out("dry-run：只输出计划，不做任何改动（加 --yes 才真正执行）")
            out(_counts_text(plan))
            _print_plan(plan)
        return EXIT_OK

    if cfg.safety.require_confirm:
        token = args.token or cfg.safety.confirm_token
        if token and args.token != token:
            msg = f"需要先输入确认令牌：apply --yes --token {token}"
            raise CliError(msg, EXIT_ALL_FAILED)

    journal = Journal(plan.session_id)

    def log_step(step: StepResult) -> None:
        journal.append(step, template=plan.template, profile=plan.profile)

    result = apply_plan(plan, on_step=log_step)
    if not args.quiet:
        out(f"已修改 {len(result.applied)} 个文件")
        for item, reason in result.failed:
            out(f"失败：{item.src.name} → {item.dst.name}（{reason}）")
        if result.leftover_tmp:
            out(f"残留临时文件 {len(result.leftover_tmp)} 个，请执行：renomate recover {plan.root}")
        out(f"回退命令：renomate undo --session {plan.session_id}")

    if result.failed:
        return EXIT_PARTIAL if result.applied else EXIT_ALL_FAILED
    return EXIT_OK


def _plan_from_file(path: Path) -> Plan:
    """从 `scan --out plan.json` 的产物恢复 Plan（字段状态重建）。"""
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"无法读取计划文件 {path}: {exc}"
        raise CliError(msg) from exc
    if not isinstance(payload, dict) or "items" not in payload:
        msg = f"计划文件格式不正确：{path}"
        raise CliError(msg)
    from renomate.chain import make_bundle
    from renomate.models import FieldSource

    items: list[RenameItem] = []
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            continue
        bundle = make_bundle(
            Path(str(raw.get("src", ""))), str(payload.get("profile", "audio")), []
        )
        for name, value in (raw.get("fields") or {}).items():
            if isinstance(value, dict):
                bundle.set(
                    name,
                    FieldValue(
                        str(value.get("value", "")),
                        FieldSource(str(value.get("source", "unknown"))),
                        float(value.get("confidence", 0) or 0),
                        bool(value.get("locked", False)),
                    ),
                )
        status = ItemStatus(str(raw.get("status", "ready")))
        item = RenameItem(
            src=Path(str(raw.get("src", ""))),
            dst=Path(str(raw.get("dst", ""))),
            status=status,
            bundle=bundle,
            reason=str(raw.get("reason", "")),
        )
        item.user_checked = bool(raw.get("user_checked", True))
        item.conflict_resolved_by = str(raw.get("conflict_resolved_by", ""))
        items.append(item)
    return Plan(
        session_id=str(payload.get("session_id", new_session_id())),
        root=Path(str(payload.get("root", "."))),
        profile=str(payload.get("profile", "audio")),
        template=str(payload.get("template", "")),
        items=items,
    )


def _cmd_undo(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    session = args.session
    if args.last or session is None:
        session = latest_session()
        if session is None:
            if not args.quiet:
                out("没有可回退的 session")
            return EXIT_ALL_FAILED
    result = undo_session(session, dry_run=args.dry_run, only=args.only)
    prefix = "计划回退" if args.dry_run else "已回退"
    if not args.quiet:
        out(f"{prefix} {len(result.undone)} 个文件（session={session}）")
        for entry, reason in result.needs_manual:
            out(f"需人工处理：{Path(entry.dst).name}（{reason}）")
    if result.needs_manual:
        return EXIT_PARTIAL
    return EXIT_OK


def _cmd_journals(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    sessions = list_sessions()
    if args.incomplete:
        sessions = [s for s in sessions if s.status != "applied"]
    for summary in sessions:
        out(
            f"{summary.session_id}\t{summary.ts}\t{summary.status}\t"
            f"entries={summary.entries}\tapplied={summary.applied}\tundone={summary.undone}"
        )
    return EXIT_OK


def _cmd_recover(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    from renomate.executor import recover_leftovers

    actions = recover_leftovers(args.directory, dry_run=args.dry_run)
    for src, target in actions:
        out(f"{'计划恢复' if args.dry_run else '已恢复'}：{src.name} → {Path(target).name}")
    if not actions:
        out("没有残留的临时文件")
    return EXIT_OK


def _cmd_report(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    if args.session:
        plan = _plan_from_session(args.session)
    elif args.directory:
        plan = _make_plan(args, cfg, args.directory).plan
    else:
        raise CliError("report 需要 DIR 或 --session")
    if args.out:
        fmt = {"table": "txt", "csv": "csv", "markdown": "md"}[args.format]
        write_report(plan, args.out, fmt=fmt)
        if not args.quiet:
            out(f"已写出报告：{args.out}")
        return EXIT_OK
    _print_plan(plan, fmt={"table": "table", "csv": "csv", "markdown": "markdown"}[args.format])
    return EXIT_OK


def _plan_from_session(session: str) -> Plan:
    """把某个 session 的日志还原成 Plan（用于报告导出）。"""
    entries = Journal(session).read()
    items: list[RenameItem] = []
    for entry in entries:
        if entry.op != "rename":
            continue
        from renomate.chain import make_bundle

        bundle = make_bundle(Path(entry.src), entry.profile, [])
        item = RenameItem(
            src=Path(entry.src),
            dst=Path(entry.dst),
            status=ItemStatus.APPLIED if entry.status == "applied" else ItemStatus.FAILED,
            bundle=bundle,
            reason=entry.error or "",
        )
        items.append(item)
    return Plan(
        session_id=session,
        root=Path(entries[0].src).parent if entries else Path("."),
        profile=entries[0].profile if entries else "audio",
        template=entries[0].template if entries else "",
        items=items,
    )


def _cmd_config(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    if args.init:
        path = init_config(args.config, force=args.force)
        out(f"已写入配置：{path}")
        return EXIT_OK
    if args.set_value:
        if "=" not in args.set_value:
            raise CliError("格式应为 --set section.key=value")
        key, _, value = args.set_value.partition("=")
        try:
            set_value(cfg, key.strip(), value)
        except ValueError as exc:
            raise CliError(str(exc)) from exc
        out(f"{key.strip()} = {value}（仅本次生效，写盘请用 config --init 后手工编辑）")
        return EXIT_OK
    path = args.config or config_path()
    out(f"配置文件：{path}")
    out(f"default_profile = {cfg.general.default_profile}")
    out(f"musicbrainz.enabled = {cfg.musicbrainz.enabled}")
    out(f"sanitize.platform = {cfg.sanitize.platform}")
    return EXIT_OK


def _cmd_profiles(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    for name in list_profiles(cfg):
        profile = get_profile(name, cfg)
        out(f"{name}: ext={','.join(profile.extensions)}")
        out(f"  fields={','.join(profile.fields)}")
        out(f"  required={','.join(profile.required_fields)}")
        out(f"  template={profile.default_template}")
    return EXIT_OK


def _cmd_tui(args: argparse.Namespace, cfg: Config, out: Any) -> int:
    from renomate.tui.app import run_tui

    directory = Path.cwd() if not hasattr(args, "directory") else args.directory
    run_tui(directory, cfg)
    return EXIT_OK


COMMANDS = {
    "scan": _cmd_scan,
    "preview": _cmd_preview,
    "apply": _cmd_apply,
    "undo": _cmd_undo,
    "journals": _cmd_journals,
    "recover": _cmd_recover,
    "report": _cmd_report,
    "config": _cmd_config,
    "profiles": _cmd_profiles,
    "tui": _cmd_tui,
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口，返回退出码（SPEC 13）。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    verbose = bool(getattr(args, "verbose", False))

    def out(message: str) -> None:
        print(message)

    try:
        cfg = _load(args)
        handler = COMMANDS.get(str(args.command))
        if handler is None:  # pragma: no cover - argparse 已保证
            parser.print_help()
            return EXIT_USAGE
        return handler(args, cfg, out)
    except CliError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:  # pragma: no cover - 用户中断
        print("已中断", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - 兜底，避免 traceback 吓到用户
        if verbose:
            raise
        print(f"未预期的错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE


__all__ = [
    "CliError",
    "EXIT_ALL_FAILED",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_USAGE",
    "build_parser",
    "main",
]
