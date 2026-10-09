"""报告导出的测试（Rich 表格 / CSV / Markdown / 纯文本）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import tag_mp3

from renomate.config import Config
from renomate.journal import SessionSummary
from renomate.models import FieldSource, FieldValue, ItemStatus
from renomate.plan import build_plan, new_session_id
from renomate.report import (
    HEADERS,
    build_rows,
    format_errors,
    low_confidence_fields,
    missing_fields,
    render_journals,
    render_rich,
    short_path,
    source_label,
    status_icon,
    summary_lines,
    to_csv,
    to_markdown,
    to_text,
    write_report,
)
from renomate.scanner import FileCandidate


def _plan(tmp_path: Path, cfg: Config) -> object:
    source = tmp_path / "01 稻香.mp3"
    tag_mp3(source, title="稻香", artist="周杰伦")
    return build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template="{title} - {artist}",
        allow_missing_fields=True,
    ).plan


def test_build_rows_and_headers(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    rows = build_rows(plan)
    assert len(rows) == 1
    assert rows[0].src.endswith("01 稻香.mp3")
    assert "稻香 - 周杰伦.mp3" in rows[0].dst
    assert len(rows[0].as_tuple()) == len(HEADERS)


def test_rows_are_sorted_by_src(tmp_path: Path, cfg: Config) -> None:
    for index, name in enumerate(["b.mp3", "a.mp3"]):
        tag_mp3(tmp_path / name, title=f"t{index}", artist="a")
    plan = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=tmp_path / n) for n in ("b.mp3", "a.mp3")],
        cfg,
        template="{title} - {artist}",
        allow_missing_fields=True,
    ).plan
    rows = build_rows(plan)
    assert [row.src for row in rows] == sorted(row.src for row in rows)


def test_max_display_limits_rows(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    assert build_rows(plan, max_display=0) == []
    assert len(build_rows(plan, max_display=1)) == 1


def test_csv_and_markdown_and_text(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    csv_text = to_csv(plan)
    md_text = to_markdown(plan)
    text = to_text(plan)
    assert csv_text.splitlines()[0].startswith("状态")
    assert len(csv_text.splitlines()) == 2
    assert md_text.startswith("| 状态 |")
    assert md_text.count("\n") >= 3
    assert "稻香 - 周杰伦.mp3" in text


def test_write_report_formats(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    for fmt, suffix in (("csv", "csv"), ("md", "md"), ("markdown", "md"), ("txt", "txt")):
        target = tmp_path / f"out.{suffix}"
        written = write_report(plan, target, fmt=fmt)
        assert written.exists()
        assert written.read_text(encoding="utf-8").strip() != ""


def test_write_report_rejects_unknown_format(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    with pytest.raises(ValueError, match="不支持的报告格式"):
        write_report(plan, tmp_path / "x.pdf", fmt="pdf")


def test_status_icons(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    item = plan.items[0]
    item.status = ItemStatus.BLOCKED
    assert status_icon(ItemStatus.BLOCKED) == "[!!]"
    assert status_icon(ItemStatus.CONFLICT, ascii_only=True) == "??"
    assert "OK" in status_icon(ItemStatus.READY).strip("[]")


def test_render_rich_table(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    table = render_rich(plan)
    assert table.title is not None and "renomate" in table.title


def test_summary_lines(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    lines = summary_lines(plan)
    assert any("ready: 1" in line for line in lines)


def test_short_path_relative(tmp_path: Path) -> None:
    child = tmp_path / "a" / "b.mp3"
    assert short_path(child, tmp_path) == "a/b.mp3"
    assert short_path(child, Path("/elsewhere")) == str(child)
    assert short_path(child) == str(child)


def test_source_labels() -> None:
    assert source_label(FieldSource.AUDIOTAG) == "标签"
    assert source_label(FieldSource.NETWORK) == "网络"
    assert source_label(FieldSource.MANUAL) == "手工"


def test_low_confidence_and_missing_fields(tmp_path: Path, cfg: Config) -> None:
    plan = _plan(tmp_path, cfg)
    item = plan.items[0]
    item.bundle.set("album", FieldValue("", FieldSource.DEFAULT, 0.1))
    item.bundle.set("genre", FieldValue("Pop", FieldSource.FILENAME, 0.4))
    assert "genre" in low_confidence_fields(item)
    assert "album" in missing_fields(item)


def test_render_journals() -> None:
    summary = [
        SessionSummary(session_id="s1", ts="2026-01-01T00:00:00", path=Path("s1.jsonl"), entries=2)
    ]
    rows = render_journals(summary)
    assert rows[0].checked == "s1"
    assert rows[0].reason == "entries=2"


def test_format_errors() -> None:
    assert format_errors([]) == "（无）"
    assert format_errors(["a", "b"]) == "- a\n- b"


def test_report_shows_blocked_reason(tmp_path: Path, cfg: Config) -> None:
    plan = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=tmp_path / "gone.mp3", skip_reason="not_writable")],
        cfg,
    ).plan
    rows = build_rows(plan)
    assert rows[0].reason == "not_writable"
    assert rows[0].icon.strip("[]") == "--"
