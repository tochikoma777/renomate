"""TUI 复用组件（不含业务逻辑，只负责展示）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, Label, Static

from renomate.config import Config
from renomate.models import ItemStatus, RenameItem
from renomate.plan import PlanIssue
from renomate.report import low_confidence_fields, missing_fields, short_path, status_icon


class PlanTable(Static):
    """计划表格（可聚焦，让空格/回车等键位交给 App 绑定处理）。"""

    can_focus = True


class SummaryBar(Static):
    """底栏状态统计。"""

    def __init__(self, plan_counts: dict[ItemStatus, int], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._counts = plan_counts

    def compose(self) -> ComposeResult:
        yield Label(self._text(), id="summary-text")

    def _text(self) -> str:
        parts = [
            f"{status_icon(status)} {status.value}: {self._counts.get(status, 0)}"
            for status in ItemStatus
        ]
        return "  ".join(parts)

    def update_counts(self, counts: dict[ItemStatus, int]) -> None:
        """刷新统计。"""
        self._counts = counts
        self.query_one("#summary-text", Label).update(self._text())


class IssuePanel(Static):
    """告警区：展示被拦截/冲突的文件与原因。"""

    def __init__(self, issues: list[PlanIssue], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._issues = issues

    def compose(self) -> ComposeResult:
        yield Label(self._text(), id="issue-text")

    def _text(self) -> str:
        if not self._issues:
            return ""
        lines = [f"⚠ {len(self._issues)} 项需要注意："]
        lines.extend(
            f"  · {Path(issue.path).name}  ({issue.message})" for issue in self._issues[:8]
        )
        if len(self._issues) > 8:
            lines.append(f"  … 还有 {len(self._issues) - 8} 项")
        return "\n".join(lines)

    def refresh_issues(self, issues: list[PlanIssue]) -> None:
        """更新告警内容。"""
        self._issues = issues
        self.query_one("#issue-text", Label).update(self._text())
        self.set_class(bool(issues), "visible")


class TemplateBar(Horizontal):
    """顶部的模板编辑条。"""

    def compose(self) -> ComposeResult:
        yield Label("模板：", id="template-label")
        yield Input(value="", placeholder="{title} - {artist}", id="template-input")
        yield Button("预设", id="preset-button", variant="default")
        yield Button("重算", id="recalc-button", variant="primary")


class DetailLine(Static):
    """选中行的字段明细（来源 + 置信度）。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)

    def show_item(self, item: RenameItem, cfg: Config, root: Path) -> None:
        """展示某个条目的字段来源与低置信/缺失标记。"""
        del cfg
        parts: list[str] = [f"原文件：{short_path(item.src, root)}"]
        low = set(low_confidence_fields(item))
        missing = set(missing_fields(item))
        for name, value in item.bundle.fields.items():
            if value is None or not value.value.strip():
                parts.append(f"{name}=⟨缺失⟩")
                continue
            mark = "?" if name in low else ""
            parts.append(f"{name}={value.value}[{value.source.value}{mark}]")
        extra = f"｜状态：{item.status.value}"
        if item.reason:
            extra += f"｜{item.reason}"
        if missing:
            extra += f"｜缺失：{', '.join(sorted(missing))}"
        self.update("\n".join([*parts, extra]))


__all__ = ["DetailLine", "IssuePanel", "PlanTable", "SummaryBar", "TemplateBar"]
