"""TUI 冒烟测试（Textual `run_test()`，不真实启动终端）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import tag_mp3
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Label, OptionList, Static

from renomate.config import Config, default_config
from renomate.executor import apply_plan
from renomate.journal import Journal, undo_session
from renomate.models import FieldSource, FieldValue, ItemStatus, RenameItem
from renomate.plan import PlanIssue, build_plan, new_session_id
from renomate.scanner import FileCandidate
from renomate.tui.app import RenomateApp
from renomate.tui.screens import ConfirmScreen, UndoScreen
from renomate.tui.widgets import DetailLine, IssuePanel, PlanTable, SummaryBar


@pytest.fixture
def cfg() -> Config:
    return default_config()


def _music(root: Path, name: str = "01 稻香.mp3", **tags: str) -> Path:
    path = root / name
    tag_mp3(path, **tags)
    return path


class HostApp(App[None]):
    """承载单个组件/屏幕的最小宿主 App。"""

    def __init__(self, child: object) -> None:
        super().__init__()
        self.child = child
        self.result: bool | None = None

    def compose(self) -> ComposeResult:
        yield self.child  # type: ignore[misc]

    def show_child_screen(self, screen: object, callback: object = None) -> None:  # noqa: ANN001
        """推入一个屏幕并把结果记到 self.result。"""

        def remember(value: bool | None) -> None:
            self.result = value

        self.push_screen(screen, remember)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------


async def test_app_mounts_and_renders_plan(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦", album="魔杰座")
    app = RenomateApp(tmp_path, cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#table", PlanTable)
        assert "稻香" in str(table.content)
        assert app.query_one("#footer", SummaryBar) is not None


async def test_template_bar_shows_current_template(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    app = RenomateApp(tmp_path, cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "{title}" in app.query_one("#template-input", Input).value


async def test_toggle_row_changes_selection(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    app = RenomateApp(tmp_path, cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._result is not None
        item = app._result.plan.items[0]
        assert item.user_checked is True
        await pilot.press("space")
        await pilot.pause()
        assert item.user_checked is False


async def test_select_all_and_invert(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    app = RenomateApp(tmp_path, cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")  # 反选
        await pilot.pause()
        assert app._result is not None
        assert app._result.plan.items[0].user_checked is False
        await pilot.press("a")  # 全选
        await pilot.pause()
        assert app._result.plan.items[0].user_checked is True


async def test_preset_cycle(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦", tracknumber="1")
    app = RenomateApp(tmp_path, cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert app._result is not None
        assert app._result.plan.template in set(cfg.presets.values())


async def test_template_edit_rebuilds_plan(tmp_path: Path, cfg: Config) -> None:
    _music(tmp_path, title="稻香", artist="周杰伦")
    app = RenomateApp(tmp_path, cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#template-input", Input).value = "{title}"
        await app.query_one("#template-input", Input).action_submit()
        await pilot.pause()
        assert app._result is not None
        assert [i.dst.name for i in app._result.plan.items] == ["稻香.mp3"]


async def test_app_executes_after_confirmation(tmp_path: Path, cfg: Config) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    app = RenomateApp(tmp_path, cfg, template="{title} - {artist}")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # 弹出确认屏
        await pilot.pause()
        app.screen.query_one("#token-input", Input).value = "APPLY"
        await pilot.click(app.screen.query_one("#confirm", Button))
        await pilot.pause()
        assert not source.exists()
        assert (tmp_path / "稻香 - 周杰伦.mp3").exists()


async def test_app_notify_on_cancel(tmp_path: Path, cfg: Config) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    app = RenomateApp(tmp_path, cfg, template="{title} - {artist}")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#cancel", Button))
        await pilot.pause()
        assert source.exists()


# ---------------------------------------------------------------------------
# 确认屏
# ---------------------------------------------------------------------------


async def test_confirm_screen_requires_token(tmp_path: Path) -> None:
    app = HostApp(Label("host"))
    screen = ConfirmScreen(root=tmp_path, counts={"ready": 3}, issues=[], token="APPLY")
    async with app.run_test() as pilot:
        app.show_child_screen(screen)
        await pilot.pause()
        await pilot.pause()
        assert app.screen.query_one("#cancel", Button).has_focus
        app.screen.query_one("#token-input", Input).value = "NOPE"
        await pilot.click(app.screen.query_one("#confirm", Button))
        await pilot.pause()
        assert app.result is False


async def test_confirm_screen_accepts_token(tmp_path: Path) -> None:
    app = HostApp(Label("host"))
    screen = ConfirmScreen(root=tmp_path, counts={"ready": 3}, issues=[], token="APPLY")
    async with app.run_test() as pilot:
        app.show_child_screen(screen)
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#token-input", Input).value = "APPLY"
        await pilot.click(app.screen.query_one("#confirm", Button))
        await pilot.pause()
        assert app.result is True


async def test_confirm_screen_cancel(tmp_path: Path) -> None:
    app = HostApp(Label("host"))
    screen = ConfirmScreen(root=tmp_path, counts={}, issues=[], token="APPLY")
    async with app.run_test() as pilot:
        app.show_child_screen(screen)
        await pilot.pause()
        await pilot.click(app.screen.query_one("#cancel", Button))
        await pilot.pause()
        assert app.result is False


async def test_confirm_screen_shows_issues(tmp_path: Path) -> None:
    app = HostApp(Label("host"))
    issues = [PlanIssue(path=tmp_path / "CON.mp3", code="blocked", message="Windows 保留名")]
    screen = ConfirmScreen(root=tmp_path, counts={"blocked": 1}, issues=issues, token="APPLY")
    async with app.run_test() as pilot:
        app.show_child_screen(screen)
        await pilot.pause()
        assert "CON.mp3" in str(app.screen.query_one("#confirm-issues", Static).content)


async def test_confirm_screen_escape_cancels(tmp_path: Path) -> None:
    app = HostApp(Label("host"))
    screen = ConfirmScreen(root=tmp_path, counts={}, issues=[], token="APPLY")
    async with app.run_test() as pilot:
        app.show_child_screen(screen)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.result is False


# ---------------------------------------------------------------------------
# 回退屏
# ---------------------------------------------------------------------------


async def test_undo_screen_lists_sessions(tmp_path: Path, cfg: Config) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    plan = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template="{title} - {artist}",
        allow_missing_fields=True,
    )
    journal = Journal(plan.plan.session_id)
    apply_plan(plan.plan, on_step=lambda step: journal.append(step))

    app = HostApp(Label("undo host"))
    async with app.run_test() as pilot:
        app.show_child_screen(UndoScreen(cfg))
        await pilot.pause()
        options = app.screen.query_one("#session-list", OptionList)
        assert options.option_count >= 1
        options.highlighted = 0
        await pilot.click(app.screen.query_one("#preview", Button))
        await pilot.pause()
        preview = app.screen.query_one("#undo-preview", Static)
        assert "将回退" in str(preview.content)


async def test_undo_screen_executes_undo(tmp_path: Path, cfg: Config) -> None:
    source = _music(tmp_path, title="稻香", artist="周杰伦")
    plan = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template="{title} - {artist}",
        allow_missing_fields=True,
    )
    journal = Journal(plan.plan.session_id)
    apply_plan(plan.plan, on_step=lambda step: journal.append(step))

    app = HostApp(Label("undo host"))
    async with app.run_test() as pilot:
        app.show_child_screen(UndoScreen(cfg, on_undo=lambda session: undo_session(session)))
        await pilot.pause()
        app.screen.query_one("#session-list", OptionList).highlighted = 0
        await pilot.click(app.screen.query_one("#undo", Button))
        await pilot.pause()
        assert source.exists()


async def test_undo_screen_close(tmp_path: Path, cfg: Config) -> None:
    app = HostApp(Label("undo host"))
    async with app.run_test() as pilot:
        app.show_child_screen(UndoScreen(cfg))
        await pilot.pause()
        await pilot.click(app.screen.query_one("#close", Button))
        await pilot.pause()
        assert not isinstance(app.screen, UndoScreen)


# ---------------------------------------------------------------------------
# 组件
# ---------------------------------------------------------------------------


async def test_issue_panel_renders_issues(tmp_path: Path) -> None:
    issues = [PlanIssue(path=tmp_path / "CON.mp3", code="blocked", message="保留名")]
    app = HostApp(IssuePanel(issues, id="issues"))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "CON.mp3" in str(app.query_one("#issue-text", Label).content)


async def test_issue_panel_empty_is_hidden() -> None:
    app = HostApp(IssuePanel([], id="issues"))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert str(app.query_one("#issue-text", Label).content) == ""


async def test_detail_line_shows_sources(tmp_path: Path, cfg: Config) -> None:
    from renomate.chain import make_bundle

    bundle = make_bundle(tmp_path / "a.mp3", "audio", ["title", "artist"])
    bundle.set("title", FieldValue("稻香", FieldSource.AUDIOTAG, 0.95))
    item = RenameItem(
        src=tmp_path / "a.mp3",
        dst=tmp_path / "b.mp3",
        status=ItemStatus.READY,
        bundle=bundle,
    )
    app = HostApp(DetailLine(id="detail"))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#detail", DetailLine).show_item(item, cfg, tmp_path)
        text = str(app.query_one("#detail", DetailLine).content)
        assert "audiotag" in text
        assert "缺失" in text
