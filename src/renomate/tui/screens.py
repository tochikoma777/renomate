"""确认屏与回退屏（SPEC 12.3 / 12.4）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, OptionList, Static

from renomate.config import Config
from renomate.journal import SessionSummary, list_sessions
from renomate.plan import PlanIssue


class ConfirmScreen(ModalScreen[bool]):
    """执行前的人机闸门：必须输入确认令牌才允许执行（默认聚焦「取消」）。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(
        self,
        *,
        root: Path,
        counts: dict[str, int],
        issues: list[PlanIssue],
        token: str,
    ) -> None:
        super().__init__()
        self._root = root
        self._counts = counts
        self._issues = issues
        self._token = token

    def compose(self) -> ComposeResult:
        counts = self._counts
        text = (
            f"目录：{self._root}\n"
            f"将修改 {counts.get('ready', 0)} 个文件，"
            f"跳过 {counts.get('skipped', 0)} 个，"
            f"拦截 {counts.get('blocked', 0)} 个，"
            f"冲突 {counts.get('conflict', 0)} 个"
        )
        with Vertical(id="confirm-dialog"):
            yield Static("确认执行", classes="title")
            yield Static(text)
            yield Static(self._issue_text(), id="confirm-issues")
            yield Input(placeholder=f"请输入 {self._token} 以确认", id="token-input")
            with Horizontal(id="confirm-buttons"):
                yield Button("取消（默认聚焦）", id="cancel", variant="default")
                yield Button("确认执行", id="confirm", variant="error")

    def on_mount(self) -> None:
        """默认聚焦取消按钮（SPEC 12.4）。"""
        self.query_one("#cancel", Button).focus()

    def _issue_text(self) -> str:
        if not self._issues:
            return "⚠ 拦截项：无"
        lines = ["⚠ 拦截项："]
        lines.extend(
            f"  · {Path(issue.path).name}  ({issue.message})" for issue in self._issues[:5]
        )
        if len(self._issues) > 5:
            lines.append("  查看全部 ▸")
        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(False)
            return
        typed = self.query_one("#token-input", Input).value.strip()
        self.dismiss(typed == self._token)

    def action_cancel(self) -> None:
        """Esc 取消。"""
        self.dismiss(False)


class UndoScreen(Screen[None]):
    """`u` 键打开的回退屏：列出可回退 session 并实时预览反向 diff。"""

    BINDINGS = [
        ("escape", "close", "关闭"),
        ("u", "undo_selected", "回退选中"),
    ]

    def __init__(self, cfg: Config, *, on_undo: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._cfg = cfg
        self._sessions: list[SessionSummary] = []
        self._on_undo = on_undo

    def compose(self) -> ComposeResult:
        self._sessions = list_sessions()
        with Vertical(id="undo-dialog"):
            yield Static("回退（u 键执行，Esc 关闭）")
            yield OptionList(
                *[
                    f"{summary.session_id}  {summary.ts}  {status_icon_label(summary)}"
                    for summary in self._sessions
                ],
                id="session-list",
            )
            yield Static("", id="undo-preview")
            with Horizontal():
                yield Button("预览反向 diff", id="preview", variant="default")
                yield Button("执行回退", id="undo", variant="warning")
                yield Button("关闭", id="close", variant="default")

    def on_option_list_option_highlighted(self, event: object) -> None:
        """选中变化时刷新预览。"""
        self._refresh_preview()

    def _selected_session(self) -> str | None:
        option_list = self.query_one("#session-list", OptionList)
        index = option_list.highlighted
        if index is None or index >= len(self._sessions):
            return None
        return self._sessions[index].session_id

    def _refresh_preview(self) -> None:
        from renomate.journal import Journal

        session = self._selected_session()
        preview = self.query_one("#undo-preview", Static)
        if session is None:
            preview.update("")
            return
        entries = [e for e in Journal(session).read() if e.op == "rename" and e.status == "applied"]
        lines = [f"将回退 {len(entries)} 个文件："]
        lines.extend(f"  {Path(e.dst).name} ← {Path(e.src).name}" for e in entries[:10])
        preview.update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.app.pop_screen()
            return
        if event.button.id == "preview":
            self._refresh_preview()
            return
        if event.button.id == "undo":
            session = self._selected_session()
            if session and self._on_undo is not None:
                self._on_undo(session)
            self.app.pop_screen()

    def action_close(self) -> None:
        self.app.pop_screen()

    def action_undo_selected(self) -> None:
        session = self._selected_session()
        if session and self._on_undo is not None:
            self._on_undo(session)
        self.app.pop_screen()


def status_icon_label(summary: SessionSummary) -> str:
    """session 状态的人类可读标签。"""
    return {
        "applied": "已执行",
        "undone": "已回退",
        "partially_undone": "部分回退",
        "partially_failed": "部分失败",
    }.get(summary.status, summary.status)


__all__ = ["ConfirmScreen", "UndoScreen", "status_icon_label"]
