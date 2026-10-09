"""报告渲染：Rich 表格（TUI/终端）+ CSV + Markdown 导出（领域无关）。"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from renomate.journal import SessionSummary
from renomate.models import FieldSource, ItemStatus, Plan, RenameItem

STATUS_ICONS: dict[ItemStatus, str] = {
    ItemStatus.READY: "[OK]",
    ItemStatus.APPLIED: "[OK]",
    ItemStatus.SKIPPED: "[--]",
    ItemStatus.BLOCKED: "[!!]",
    ItemStatus.CONFLICT: "[??]",
    ItemStatus.FAILED: "[XX]",
    ItemStatus.PENDING: "[..]",
}

_SOURCE_LABELS: dict[FieldSource, str] = {
    FieldSource.AUDIOTAG: "标签",
    FieldSource.FILENAME: "文件名",
    FieldSource.PATH: "目录",
    FieldSource.NETWORK: "网络",
    FieldSource.MANUAL: "手工",
    FieldSource.DEFAULT: "缺省",
    FieldSource.UNKNOWN: "未知",
}

LOW_CONFIDENCE_THRESHOLD = 0.6


def status_icon(status: ItemStatus, *, ascii_only: bool = False) -> str:
    """状态图标；`ascii_only=True` 时给纯文本环境用。"""
    if ascii_only:
        return STATUS_ICONS[status].strip("[]")
    return STATUS_ICONS[status]


def source_label(source: FieldSource) -> str:
    """来源的中文标签。"""
    return _SOURCE_LABELS.get(source, source.value)


def short_path(path: Path, root: Path | None = None) -> str:
    """相对 root 的短路径（用于表格展示）。"""
    try:
        return str(path.relative_to(root)) if root is not None else str(path)
    except ValueError:
        return str(path)


def low_confidence_fields(item: RenameItem) -> list[str]:
    """置信度 < 0.6 的字段（TUI 里标黄）。"""
    result: list[str] = []
    for name, value in item.bundle.fields.items():
        if (
            value is not None
            and value.value.strip()
            and value.confidence < LOW_CONFIDENCE_THRESHOLD
        ):
            result.append(name)
    return result


def missing_fields(item: RenameItem) -> list[str]:
    """缺失字段（TUI 里标红，允许手工填）。"""
    return [
        name
        for name, value in item.bundle.fields.items()
        if value is None or not value.value.strip()
    ]


@dataclass
class ReportRow:
    """报告表格的一行。"""

    icon: str
    checked: str
    src: str
    dst: str
    reason: str
    resolved_by: str = ""

    def as_tuple(self) -> tuple[str, ...]:
        return (self.icon, self.checked, self.src, self.dst, self.reason, self.resolved_by)


HEADERS = ("状态", "选", "原文件名", "新文件名", "说明", "冲突消解")


def build_rows(
    plan: Plan,
    *,
    ascii_only: bool = False,
    max_display: int | None = None,
) -> list[ReportRow]:
    """把 Plan 转成表格行（按 src 路径排序，保证顺序稳定）。"""
    items = sorted(plan.items, key=lambda i: str(i.src))
    if max_display is not None:
        items = items[:max_display]
    rows: list[ReportRow] = []
    for item in items:
        rows.append(
            ReportRow(
                icon=status_icon(item.status, ascii_only=ascii_only),
                checked="[x]" if item.user_checked else "[ ]",
                src=short_path(item.src, plan.root),
                dst=short_path(item.dst, plan.root),
                reason=item.reason,
                resolved_by=item.conflict_resolved_by,
            )
        )
    return rows


def render_rich(plan: Plan, *, ascii_only: bool = False) -> object:
    """返回 Rich `Table` 对象（调用方负责放进 Console / Static）。"""
    from rich.table import Table

    table = Table(title=f"renomate plan · {plan.session_id}", expand=True)
    for header in HEADERS:
        table.add_column(header, overflow="fold")
    for row in build_rows(plan, ascii_only=ascii_only):
        table.add_row(*row.as_tuple())
    return table


def to_csv(plan: Plan, *, ascii_only: bool = False) -> str:
    """导出 CSV 字符串（含 BOM 无关，调用方负责编码）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for row in build_rows(plan, ascii_only=ascii_only):
        writer.writerow(row.as_tuple())
    return buffer.getvalue()


def to_markdown(plan: Plan, *, ascii_only: bool = False) -> str:
    """导出 Markdown 表格。"""
    lines = ["| " + " | ".join(HEADERS) + " |", "|" + "|".join(["---"] * len(HEADERS)) + "|"]
    for row in build_rows(plan, ascii_only=ascii_only):
        lines.append("| " + " | ".join(row.as_tuple()) + " |")
    return "\n".join(lines) + "\n"


def to_text(plan: Plan, *, ascii_only: bool = False) -> str:
    """纯文本表格（无 Rich 依赖时的降级渲染）。"""
    rows = build_rows(plan, ascii_only=ascii_only)
    lines = ["  ".join(HEADERS)]
    lines.extend("  ".join(row.as_tuple()) for row in rows)
    return "\n".join(lines) + "\n"


def summary_lines(plan: Plan, *, ascii_only: bool = False) -> list[str]:
    """汇总行：`OK 4 / -- 2 / !! 1 / ?? 0` 形式。"""
    counts = plan.counts()
    return [
        f"{status_icon(status, ascii_only=ascii_only)} {status.value}: {counts.get(status, 0)}"
        for status in (
            ItemStatus.READY,
            ItemStatus.APPLIED,
            ItemStatus.SKIPPED,
            ItemStatus.BLOCKED,
            ItemStatus.CONFLICT,
            ItemStatus.FAILED,
        )
    ]


def write_report(plan: Plan, target: Path, *, fmt: str = "csv", ascii_only: bool = False) -> Path:
    """把计划写到文件，格式支持 csv / md / txt。"""
    renderers = {
        "csv": to_csv,
        "md": to_markdown,
        "markdown": to_markdown,
        "txt": to_text,
        "text": to_text,
    }
    renderer = renderers.get(fmt.lower())
    if renderer is None:
        msg = f"不支持的报告格式: {fmt!r}（可选: {', '.join(sorted(renderers))}）"
        raise ValueError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(renderer(plan, ascii_only=ascii_only), encoding="utf-8")
    return target


def render_journals(sessions: Sequence[SessionSummary]) -> list[ReportRow]:
    """把 session 列表转成表格行，供 `renomate journals` 复用。"""
    rows: list[ReportRow] = []
    for summary in sessions:
        rows.append(
            ReportRow(
                icon=summary.status,
                checked=summary.session_id,
                src=str(summary.ts),
                dst=str(summary.path.name),
                reason=f"entries={summary.entries}",
                resolved_by=f"applied={summary.applied}/undone={summary.undone}",
            )
        )
    return rows


def format_errors(errors: Iterable[str]) -> str:
    """把错误列表拼成多行文本。"""
    lines = list(errors)
    return "\n".join(f"- {line}" for line in lines) if lines else "（无）"


__all__ = [
    "HEADERS",
    "ReportRow",
    "build_rows",
    "format_errors",
    "low_confidence_fields",
    "missing_fields",
    "render_journals",
    "render_rich",
    "short_path",
    "source_label",
    "status_icon",
    "summary_lines",
    "to_csv",
    "to_markdown",
    "to_text",
    "write_report",
]
