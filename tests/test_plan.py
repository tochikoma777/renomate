"""计划生成与冲突消解 / 体检闸门的测试（SPEC 8.2、10 闸门 3）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from renomate.config import Config, default_config
from renomate.models import ItemStatus
from renomate.plan import (
    PlanResult,
    blocked,
    build_plan,
    conflicts,
    missing_detectable,
    new_session_id,
    render_one,
    skipped,
)
from renomate.platform.fs import FileSystem, LocalFileSystem
from renomate.scanner import FileCandidate, ScanOptions, scan
from tests.conftest import tag_mp3
from tests.test_musicbrainz import FakeMusicBrainz

TEMPLATE = "{title} - {artist} - {album} - {country}"


class CaseInsensitiveFS:
    """模拟 /mnt/c：同名不同大小写视为同一个文件。"""

    def __init__(self) -> None:
        self._delegate: FileSystem = LocalFileSystem()

    def exists(self, path: Path) -> bool:
        return self._delegate.exists(path)

    def lexists(self, path: Path) -> bool:
        return self._case_exists(path)

    def is_dir(self, path: Path) -> bool:
        return self._delegate.is_dir(path)

    def is_file(self, path: Path) -> bool:
        return self._delegate.is_file(path)

    def is_symlink(self, path: Path) -> bool:
        return self._delegate.is_symlink(path)

    def is_hidden(self, path: Path) -> bool:
        return self._delegate.is_hidden(path)

    def stat(self, path: Path) -> object:
        return self._delegate.stat(path)

    def iterdir(self, path: Path) -> list[Path]:
        return self._delegate.iterdir(path)

    def rename(self, src: Path, dst: Path) -> None:
        self._delegate.rename(src, dst)

    def access(self, path: Path, mode: int) -> bool:
        return self._delegate.access(path, mode)

    def resolve(self, path: Path) -> Path:
        return self._delegate.resolve(path)

    def same_device(self, a: Path, b: Path) -> bool:
        return self._delegate.same_device(a, b)

    def is_case_sensitive(self, directory: Path) -> bool:
        return False

    def mkdir(self, path: Path, *, parents: bool = True) -> None:
        self._delegate.mkdir(path, parents=parents)

    def unlink(self, path: Path) -> None:
        self._delegate.unlink(path)

    def _case_exists(self, path: Path) -> bool:
        if self._delegate.lexists(path):
            return True
        if not self._delegate.is_dir(path.parent):
            return False
        target = path.name.casefold()
        return any(s.name.casefold() == target for s in self._delegate.iterdir(path.parent))


def _scan(root: Path, cfg: Config, **kwargs: object) -> list[FileCandidate]:
    profile = cfg.profile("audio")
    options = ScanOptions(extensions=list(profile.extensions), **kwargs)  # type: ignore[arg-type]
    return scan(root, options).candidates


def _plan(root: Path, cfg: Config, template: str = TEMPLATE, **kwargs: object) -> PlanResult:
    return build_plan(
        new_session_id(),
        root,
        _scan(root, cfg),
        cfg,
        template=template,
        **kwargs,  # type: ignore[arg-type]
    )


def _single(root: Path, cfg: Config, path: Path, **kwargs: object) -> PlanResult:
    return build_plan(
        new_session_id(),
        root,
        [FileCandidate(path=path)],
        cfg,
        **kwargs,  # type: ignore[arg-type]
    )


def _music_file(path: Path, *, title: str, artist: str, album: str) -> Path:
    tag_mp3(path, title=title, artist=artist, album=album, date="2008", tracknumber="1")
    return path


# ---------------------------------------------------------------------------
# 渲染与状态
# ---------------------------------------------------------------------------


def test_plan_renders_default_library(music_lib: Path, cfg: Config) -> None:
    result = _plan(music_lib, cfg)
    names = {item.dst.name for item in result.actionable}
    assert "稻香 - 周杰伦 - 魔杰座 - CN.mp3" in names
    assert "K歌之王 - 陈奕迅 - 打得火热 - CN.flac" in names


def test_noop_items_are_skipped(tmp_path: Path, cfg: Config) -> None:
    path = _music_file(
        tmp_path / "稻香 - 周杰伦 - 魔杰座 - CN.mp3", title="稻香", artist="周杰伦", album="魔杰座"
    )
    result = _single(tmp_path, cfg, path, template=TEMPLATE)
    assert [(i.status, i.reason) for i in skipped(result.plan)] == [(ItemStatus.SKIPPED, "no_op")]


def test_missing_required_fields_are_blocked(tmp_path: Path, cfg: Config) -> None:
    """无标签且文件名无信息 → artist 只能来自缺省值 → BLOCKED。"""
    target = tmp_path / "unknown.mp3"
    target.write_bytes(b"x")
    result = _single(tmp_path, cfg, target, template=TEMPLATE)
    item = blocked(result.plan)[0]
    assert item.status is ItemStatus.BLOCKED
    assert item.reason == "missing:artist"


def test_default_values_are_flagged_as_missing() -> None:
    """缺省值让模板仍能渲染，但必须依旧记为缺失。"""
    from renomate.chain import make_bundle
    from renomate.models import FieldSource, FieldValue

    cfg = default_config()
    bundle = make_bundle(Path("/tmp/unknown.mp3"), "audio", cfg.profile("audio").fields)
    bundle.set("title", FieldValue("稻香", FieldSource.FILENAME, 0.8))
    bundle.set("artist", FieldValue("未知歌手", FieldSource.DEFAULT, 0.1))
    missing = missing_detectable(bundle)
    assert "artist" in missing
    assert "album" in missing  # 仍为 None
    assert "title" not in missing


def test_allow_missing_fields_unblocks(tmp_path: Path, cfg: Config) -> None:
    target = tmp_path / "unknown.mp3"
    target.write_bytes(b"x")
    cfg.safety.allow_missing_fields = True
    result = _single(tmp_path, cfg, target, template=TEMPLATE)
    assert blocked(result.plan) == []
    assert [i.dst.name for i in result.actionable] == ["unknown - 未知歌手 - 未知专辑 - CN.mp3"]


def test_pure_noise_name_falls_back_to_defaults(tmp_path: Path, cfg: Config) -> None:
    target = tmp_path / "[320K].mp3"
    target.write_bytes(b"x")
    cfg.safety.allow_missing_fields = True
    result = _single(tmp_path, cfg, target, template=TEMPLATE)
    assert [i.dst.name for i in result.actionable] == ["未知标题 - 未知歌手 - 未知专辑 - CN.mp3"]


# ---------------------------------------------------------------------------
# 冲突消解
# ---------------------------------------------------------------------------


def test_conflict_gets_automatic_suffix(tmp_path: Path, cfg: Config) -> None:
    first = _music_file(tmp_path / "a.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    second = _music_file(tmp_path / "b.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    existing = tmp_path / "稻香 - 周杰伦 - 魔杰座 - CN.mp3"
    existing.write_bytes(b"x")

    result = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=first), FileCandidate(path=second)],
        cfg,
        template=TEMPLATE,
    )
    ready = result.actionable
    assert [i.dst.name for i in ready] == [
        "稻香 - 周杰伦 - 魔杰座 - CN (2).mp3",
        "稻香 - 周杰伦 - 魔杰座 - CN (3).mp3",
    ]
    assert [i.conflict_resolved_by for i in ready] == [
        "auto-suffix (2)",
        "auto-suffix (3)",
    ]
    assert conflicts(result.plan) == []


def test_exhausted_suffixes_become_conflict(tmp_path: Path, cfg: Config) -> None:
    """` (2)` … ` (99)` 全被占用 → CONFLICT（交给用户手工指定）。"""
    source = _music_file(tmp_path / "a.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    base = "稻香 - 周杰伦 - 魔杰座 - CN"
    (tmp_path / f"{base}.mp3").write_bytes(b"x")
    for index in range(2, 100):
        (tmp_path / f"{base} ({index}).mp3").write_bytes(b"x")
    result = _single(tmp_path, cfg, source, template=TEMPLATE)
    item = conflicts(result.plan)[0]
    assert item.status is ItemStatus.CONFLICT
    assert item.reason == "conflict:exists"


def test_case_insensitive_conflict_detection(tmp_path: Path, cfg: Config) -> None:
    source = _music_file(tmp_path / "a.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    (tmp_path / "稻香 - 周杰伦 - 魔杰座 - CN.MP3").write_bytes(b"x")
    result = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template=TEMPLATE,
        fs=CaseInsensitiveFS(),  # type: ignore[arg-type]
    )
    assert [i.dst.name for i in result.actionable] == ["稻香 - 周杰伦 - 魔杰座 - CN (2).mp3"]


def test_case_only_rename_is_flagged(tmp_path: Path, cfg: Config) -> None:
    """大小写不敏感 FS 上仅改大小写 → 标记为 `case-only-rename`（执行器做两步 rename）。"""
    source = _music_file(
        tmp_path / "稻香 - 周杰伦 - 魔杰座 - CN.mp3",
        title="稻香",
        artist="周杰伦",
        album="魔杰座",
    )
    result = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template="{title} - {artist} - {album} - {country|lower}",
        fs=CaseInsensitiveFS(),  # type: ignore[arg-type]
    )
    item = result.actionable[0]
    assert item.dst.name == "稻香 - 周杰伦 - 魔杰座 - cn.mp3"
    assert item.conflict_resolved_by == "case-only-rename"


def test_same_batch_sources_do_not_block_each_other(tmp_path: Path, cfg: Config) -> None:
    """`01.mp3` → `02.mp3`、`02.mp3` → `03.mp3` 这类轮转不应被判冲突。"""
    first = _music_file(tmp_path / "01 稻香.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    second = tmp_path / "稻香 - 周杰伦 - 魔杰座 - CN.mp3"
    second.write_bytes(b"x")
    result = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=first), FileCandidate(path=second)],
        cfg,
        template=TEMPLATE,
    )
    assert len(result.actionable) == 2


# ---------------------------------------------------------------------------
# 闸门：长度 / 跨设备 / skipped
# ---------------------------------------------------------------------------


def test_path_too_long_is_blocked(tmp_path: Path, cfg: Config) -> None:
    deep = tmp_path / ("-long-" * 40)
    deep.mkdir()
    target = _music_file(deep / "a.mp3", title="稻香", artist="周杰伦", album="魔杰座")
    result = _single(tmp_path, cfg, target, template=TEMPLATE)
    assert any(i.reason.startswith("path_too_long") for i in blocked(result.plan))


def test_name_too_long_is_blocked(tmp_path: Path, cfg: Config) -> None:
    """配置把 max_name_length 设到 255 以上时，255 兜底校验必须拦住。"""
    # MAX_PATH=260 的校验在前，因此用 posix 模式隔离出 255 这条兜底线
    target = tmp_path / "[320K].mp3"
    target.write_bytes(b"x")
    cfg.sanitize.platform = "posix"
    cfg.sanitize.max_name_length = 400
    cfg.defaults.values["title"] = "题" * 300
    cfg.safety.allow_missing_fields = True
    result = _single(tmp_path, cfg, target, template="{title}", max_length=400)
    assert [i.reason for i in blocked(result.plan)] == ["name_too_long:>255"]


def test_skipped_candidates_keep_their_reason(tmp_path: Path, cfg: Config) -> None:
    target = tmp_path / "song.jpg"
    target.write_bytes(b"x")
    result = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=target, skip_reason="扩展名不在白名单")],
        cfg,
    )
    assert skipped(result.plan)[0].reason == "扩展名不在白名单"


def test_template_error_propagates(tmp_path: Path, cfg: Config) -> None:
    from renomate.template import TemplateError

    target = tmp_path / "a.mp3"
    target.write_bytes(b"x")
    with pytest.raises(TemplateError):
        _single(tmp_path, cfg, target, template="{nope}")


def test_empty_render_is_blocked(tmp_path: Path, cfg: Config) -> None:
    target = tmp_path / "[320K].mp3"
    target.write_bytes(b"x")
    cfg.defaults.values["title"] = ""
    cfg.defaults.values["artist"] = ""
    cfg.defaults.values["album"] = ""
    result = _single(tmp_path, cfg, target, template="{title}", allow_missing_fields=True)
    item = blocked(result.plan)[0]
    assert item.reason == "empty_name"
    assert result.issues[0].code == "empty_name"


# ---------------------------------------------------------------------------
# 计划元数据 / 统计 / 序列化
# ---------------------------------------------------------------------------


def test_plan_counts_and_serialization(tmp_path: Path, cfg: Config) -> None:
    target = tmp_path / "a.mp3"
    target.write_bytes(b"x")
    result = _single(tmp_path, cfg, target, template=TEMPLATE)
    payload = result.plan.to_dict()
    assert payload["session_id"] == result.plan.session_id
    assert payload["items"][0]["src"].endswith("a.mp3")
    assert payload["profile"] == "audio"
    assert sum(result.plan.counts().values()) == 1


def test_plan_stats_report_total(tmp_path: Path, cfg: Config) -> None:
    for name in ("a.mp3", "b.mp3"):
        (tmp_path / name).write_bytes(b"x")
    result = _plan(tmp_path, cfg)
    assert result.stats.total == 2


def test_network_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMusicBrainz()
    monkeypatch.setitem(sys.modules, "musicbrainzngs", fake)
    cfg = default_config()
    cfg.musicbrainz.enabled = True
    target = _music_file(tmp_path / "a.mp3", title="稻香", artist="周杰伦", album="")
    del target
    path = tmp_path / "a.mp3"
    path.write_text("[320K]", encoding="utf-8")
    path.rename(tmp_path / "稻香 - 周杰伦.mp3")
    source = tmp_path / "稻香 - 周杰伦.mp3"
    without = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template="{title} - {artist} - {album} - {country}",
        allow_missing_fields=True,
    )
    assert without.stats.network_queries == 0
    with_net = build_plan(
        new_session_id(),
        tmp_path,
        [FileCandidate(path=source)],
        cfg,
        template="{title} - {artist} - {album} - {country}",
        use_network=True,
        allow_missing_fields=True,
    )
    assert with_net.stats.network_queries >= 1
    assert isinstance(with_net, PlanResult)


def test_render_one_matches_plan(tmp_path: Path, cfg: Config) -> None:
    target = tmp_path / "周杰伦 - 稻香 [320K].mp3"
    target.write_bytes(b"x")
    result = _single(tmp_path, cfg, target, template=TEMPLATE, allow_missing_fields=True)
    item = result.actionable[0]
    filename, rendered = render_one(
        result.plan.template, item.bundle, cfg, fields=cfg.profile("audio").fields
    )
    assert filename == item.dst.name
    assert set(rendered.missing) <= set(result.rendered_missing[str(item.src)])


def test_session_id_format() -> None:
    session = new_session_id()
    date_part, time_part, tail = session.split("-")
    assert len(date_part) == 8 and date_part.isdigit()
    assert len(time_part) == 6 and time_part.isdigit()
    assert len(tail) == 8


def test_subdirectory_template_is_used(tmp_path: Path, cfg: Config) -> None:
    target = tmp_path / "周杰伦 - 稻香 [320K].mp3"
    target.write_bytes(b"x")
    result = _single(
        tmp_path,
        cfg,
        target,
        template="{album|default:未知专辑}/{track|pad:2} - {title} - {artist}",
        allow_missing_fields=True,
    )
    item = result.actionable[0]
    assert item.dst.parent.name == "未知专辑"
    assert item.dst.name.endswith(".mp3")


def test_path_provider_is_disabled_at_scan_root(tmp_path: Path, cfg: Config) -> None:
    """根目录��的文件不能把目录名当成艺术家/专辑名。"""
    (tmp_path / "周杰伦").mkdir()
    target = _music_file(
        tmp_path / "周杰伦" / "a.mp3", title="稻香", artist="周杰伦", album="魔杰座"
    )
    root_level = build_plan(
        new_session_id(),
        tmp_path / "周杰伦",
        [FileCandidate(path=target)],
        cfg,
        template=TEMPLATE,
    )
    assert root_level.actionable[0].dst.name == "稻香 - 周杰伦 - 魔杰座 - CN.mp3"


def test_path_provider_applies_to_nested_files(music_lib: Path, cfg: Config) -> None:
    """嵌套文件从不吃亏：album 来自父目录、artist 来自祖父目录。"""
    target = music_lib / "陈奕迅" / "unknown.mp3"
    result = build_plan(
        new_session_id(),
        music_lib,
        [FileCandidate(path=target)],
        cfg,
        template="{title} - {artist} - {album} - {country}",
        allow_missing_fields=True,
    )
    item = result.actionable[0]
    assert item.dst.name == "unknown - Music - 陈奕迅 - CN.mp3"
