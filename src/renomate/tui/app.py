"""Textual 主程序（SPEC 12）。

键位：`Space` 勾选、`Enter` 编辑单元格、`p` 切换预设、`a` 全选、`s` 反选、
`t` 聚焦模板、`/` 搜索、`Enter` 执行（弹确认屏）、`u` 回退、`q` 退出。
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Button, Footer, Header, Input, Static

from renomate.config import Config, default_config
from renomate.executor import StepResult, apply_plan
from renomate.journal import Journal, undo_session
from renomate.models import ItemStatus, Plan, RenameItem
from renomate.plan import PlanResult, build_plan, new_session_id
from renomate.report import HEADERS, build_rows
from renomate.scanner import ScanOptions, scan
from renomate.tui.screens import ConfirmScreen, UndoScreen
from renomate.tui.widgets import DetailLine, IssuePanel, PlanTable, SummaryBar, TemplateBar

CSS_PATH = Path(__file__).parent / "styles.tcss"


class RenomateApp(App[None]):
    """主界面。"""

    CSS_PATH = CSS_PATH
    BINDINGS = [
        ("space", "toggle_row", "勾选/取消"),
        ("a", "select_all", "全选"),
        ("s", "invert_selection", "反选"),
        ("t", "focus_template", "编辑模板"),
        ("p", "cycle_preset", "切换预设"),
        ("slash", "focus_search", "搜索"),
        ("enter", "run", "执行（弹确认屏）"),
        ("u", "open_undo", "回退"),
        ("q", "quit", "退出"),
    ]

    def __init__(
        self,
        directory: Path,
        cfg: Config | None = None,
        *,
        template: str | None = None,
    ) -> None:
        super().__init__()
        self.directory = directory
        self.cfg = cfg or default_config()
        self._template_override = template
        self._result: PlanResult | None = None
        self._selected_index = 0
        self._search = ""

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._header_text(), id="header")
        yield TemplateBar()
        yield PlanTable("", id="table")
        yield IssuePanel([], id="issues")
        yield DetailLine(id="detail")
        yield SummaryBar({}, id="footer")
        yield Footer()

    def on_mount(self) -> None:
        """首次挂载时生成计划，并把焦点放在表格上（键位交给 App 绑定）。"""
        self.rebuild()
        self.query_one("#table", PlanTable).focus()

    # ------------------------------------------------------------------
    # 计划
    # ------------------------------------------------------------------
    def rebuild(self) -> None:
        """重新扫描并生成计划（模板/预设变更后调用）。"""
        profile = self.cfg.profile(self.cfg.general.default_profile)
        options = ScanOptions(
            recursive=True,
            extensions=list(profile.extensions),
            include_hidden=self.cfg.general.include_hidden,
            follow_symlinks=self.cfg.general.follow_symlinks,
            extra_blocked=self.cfg.safety.extra_blocked,
        )
        candidates = scan(self.directory, options).candidates
        self._result = build_plan(
            new_session_id(),
            self.directory,
            candidates,
            self.cfg,
            template=self._template_override or profile.default_template,
            profile_name=self.cfg.general.default_profile,
            use_network=self.cfg.musicbrainz.enabled,
        )
        self._selected_index = 0
        self._refresh()

    def _header_text(self) -> str:
        profile = self.cfg.general.default_profile
        return f"renomate · profile: {profile} · {self.directory}"

    def _visible_items(self) -> list[RenameItem]:
        if self._result is None:
            return []
        items = self._result.plan.items
        if self._search:
            needle = self._search.casefold()
            items = [i for i in items if needle in str(i.src).casefold()]
        return items

    def _refresh(self) -> None:
        """把计划渲染到表格/告警/底栏。"""
        if self._result is None:
            return
        plan = self._result.plan
        rows = build_rows(plan)
        lines = ["  ".join(HEADERS)]
        lines.extend("  ".join(self._decorate(row, item)) for row, item in zip(rows, plan.items))
        self.query_one("#table", PlanTable).update("\n".join(lines))

        issues_widget = self.query_one("#issues", IssuePanel)
        issues_widget.refresh_issues(self._result.issues)

        counts_widget = self.query_one("#footer", SummaryBar)
        counts_widget.update_counts(plan.counts())

        template_input = self.query_one("#template-input", Input)
        template_input.value = plan.template

        self._refresh_detail()

    def _decorate(self, row: object, item: RenameItem) -> tuple[str, ...]:
        """按状态给行加语义标记（低置信 / 缺失）。"""
        values = row.as_tuple()  # type: ignore[attr-defined]
        suffix = ""
        if item.status is ItemStatus.BLOCKED:
            suffix = " ▲拦截"
        elif item.status is ItemStatus.CONFLICT:
            suffix = " ▲冲突"
        return (*values[:-1], values[-1] + suffix)

    def _refresh_detail(self) -> None:
        items = self._visible_items()
        detail = self.query_one("#detail", DetailLine)
        if not items:
            detail.update("")
            return
        self._selected_index = min(self._selected_index, len(items) - 1)
        detail.show_item(items[self._selected_index], self.cfg, self.directory)

    # ------------------------------------------------------------------
    # 键位动作
    # ------------------------------------------------------------------
    def action_toggle_row(self) -> None:
        """空格：切换当前行勾选。"""
        items = self._visible_items()
        if not items:
            return
        item = items[self._selected_index]
        item.user_checked = not item.user_checked
        self._refresh()

    def action_select_all(self) -> None:
        for item in self._visible_items():
            item.user_checked = True
        self._refresh()

    def action_invert_selection(self) -> None:
        for item in self._visible_items():
            item.user_checked = not item.user_checked
        self._refresh()

    def action_focus_template(self) -> None:
        self.query_one("#template-input", Input).focus()

    def action_focus_search(self) -> None:
        self._search = ""
        self.notify("搜索：请输入关键字后按 Enter（简化实现）")

    def action_cycle_preset(self) -> None:
        """p 键：在配置预设之间循环。"""
        presets = sorted(self.cfg.presets)
        if not presets:
            return
        current = self._template_override or ""
        index = 0
        if current in self.cfg.presets.values():
            for i, name in enumerate(presets):
                if self.cfg.presets[name] == current:
                    index = (i + 1) % len(presets)
                    break
        self._template_override = self.cfg.presets[presets[index]]
        self.rebuild()
        self.notify(f"预设：{presets[index]}")

    def action_open_undo(self) -> None:
        self.push_screen(UndoScreen(self.cfg, on_undo=self._perform_undo))

    def action_run(self) -> None:
        """Enter：弹出确认屏，输入令牌后才真正执行。"""
        if self._result is None:
            return
        plan = self._result.plan
        counts = {status.value: count for status, count in plan.counts().items()}

        def on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                self.notify("已取消")
                return
            self._execute(plan)

        self.push_screen(
            ConfirmScreen(
                root=plan.root,
                counts=counts,
                issues=self._result.issues,
                token=self.cfg.safety.confirm_token,
            ),
            on_confirm,
        )

    # ------------------------------------------------------------------
    def _execute(self, plan: Plan) -> None:
        journal = Journal(plan.session_id)

        def log_step(step: StepResult) -> None:
            journal.append(step, template=plan.template, profile=plan.profile)

        result = apply_plan(plan, on_step=log_step)
        message = f"已修改 {len(result.applied)} 个文件"
        if result.failed:
            message += f"，失败 {len(result.failed)} 个"
        if result.leftover_tmp:
            message += f"，残留 {len(result.leftover_tmp)} 个临时文件"
        self.notify(message)
        self.rebuild()

    def _perform_undo(self, session: str) -> None:
        result = undo_session(session)
        self.notify(f"已回退 {len(result.undone)} 个文件（{session}）")
        self.rebuild()

    # ------------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """模板输入回车 → 重算；搜索框回车 → 过滤。"""
        if event.input.id == "template-input":
            self._template_override = event.value
            self.rebuild()
        else:
            self._search = event.value
            self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "recalc-button":
            self._template_override = self.query_one("#template-input", Input).value
            self.rebuild()
        elif event.button.id == "preset-button":
            self.action_cycle_preset()


def run_tui(directory: Path, cfg: Config | None = None) -> None:
    """CLI `renomate tui` 的入口。"""
    RenomateApp(directory, cfg).run()


__all__ = ["RenomateApp", "run_tui"]
