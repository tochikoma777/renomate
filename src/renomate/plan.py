"""计划生成与「计划体检」闸门 3（SPEC 10 闸门 3、8.2）。

输入 scan 结果 + ProviderChain 结果，输出一份**绝不越界、自带原因**的 Plan。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from renomate.chain import FillStats, ProviderChain, make_bundle
from renomate.config import Config
from renomate.models import FieldSource, ItemStatus, MetadataBundle, Plan, RenameItem
from renomate.platform.fs import FileSystem, get_default_fs
from renomate.profiles import Profile, get_profile
from renomate.safety import (
    MAX_PATH,
    check_component_length,
    check_path_length,
    normalize_for_compare,
)
from renomate.scanner import FileCandidate
from renomate.template import RenderResult, TemplateError, build_filename

MAX_CONFLICT_SUFFIX = 99


def missing_detectable(bundle: MetadataBundle) -> list[str]:
    """「可判定为缺失」的字段：值为空，**或**只被 DEFAULT Provider 兜底过。

    缺省值的存在是为了让模板仍能渲染出名字（对应 TUI 线框里的 `未知标题`），
    但它依旧要被记为缺失，否则「缺失字段拦截」这道闸门永远不会触发。
    """
    missing: list[str] = []
    for name, field_value in bundle.fields.items():
        if field_value is None or not field_value.value.strip():
            missing.append(name)
        elif field_value.source is FieldSource.DEFAULT:
            missing.append(name)
    return missing


def new_session_id() -> str:
    """会话 ID：`YYYYmmdd-HHMMSS-<uuid4 前 8 位>`。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass
class PlanIssue:
    """一条体检问题（供 TUI 告警区与报告使用）。"""

    path: Path
    code: str
    message: str


@dataclass
class PlanResult:
    plan: Plan
    stats: FillStats = field(default_factory=FillStats)
    issues: list[PlanIssue] = field(default_factory=list)
    rendered_missing: dict[str, list[str]] = field(default_factory=dict)

    @property
    def actionable(self) -> list[RenameItem]:
        """可以真正执行的条目（READY）。"""
        return [item for item in self.plan.items if item.status is ItemStatus.READY]


def build_plan(
    session_id: str,
    root: Path,
    candidates: list[FileCandidate],
    cfg: Config,
    *,
    template: str | None = None,
    profile_name: str | None = None,
    fs: FileSystem | None = None,
    use_network: bool = False,
    manual: dict[str, str] | None = None,
    allow_missing_fields: bool | None = None,
    max_length: int | None = None,
) -> PlanResult:
    """按 SPEC 8.2 渲染流程生成计划。

    每个文件的检查顺序固定（先命中者即最终状态）：
    skipped(闸门 2) → template_error → empty_name → missing:* → no_op →
    path_too_long → name_too_long → cross_device → conflict → READY。
    """
    filesystem: FileSystem = fs if fs is not None else get_default_fs()
    profile = get_profile(profile_name, cfg)
    profile_cfg = cfg.profile(profile.name)
    template_text = template or profile.default_template
    allow_missing = (
        cfg.safety.allow_missing_fields if allow_missing_fields is None else allow_missing_fields
    )
    length_limit = max_length or cfg.sanitize.max_name_length

    bundles = [make_bundle(c.path, profile.name, profile.fields) for c in candidates]
    stats = _fill_bundles(
        candidates,
        bundles,
        cfg,
        profile_name=profile.name,
        providers=profile_cfg.providers,
        use_network=use_network,
        manual=manual,
        root=root,
    )

    planner = _Planner(
        cfg=cfg,
        filesystem=filesystem,
        profile=profile,
        template=template_text,
        root=root,
        allow_missing=allow_missing,
        length_limit=length_limit,
    )
    for candidate, bundle in zip(candidates, bundles, strict=True):
        planner.handle(candidate, bundle)

    return PlanResult(
        plan=Plan(
            session_id=session_id,
            root=root,
            profile=profile.name,
            template=template_text,
            items=planner.items,
        ),
        stats=stats,
        issues=planner.issues,
        rendered_missing=planner.rendered_missing,
    )


def _fill_bundles(
    candidates: list[FileCandidate],
    bundles: list[MetadataBundle],
    cfg: Config,
    *,
    profile_name: str,
    providers: list[str],
    use_network: bool,
    manual: dict[str, str] | None,
    root: Path,
) -> FillStats:
    """并发填充元数据。

    直接躺在扫描根目录里的文件不使用 `path` Provider——把 `~/Downloads`、`tmp_path`
    这类目录名当成艺术家/专辑名是纯粹的污染（见 README 的「目录推断」说明）。
    """
    groups: dict[tuple[str, ...], list[MetadataBundle]] = {}
    for candidate, bundle in zip(candidates, bundles, strict=True):
        effective = providers
        if "path" in effective and candidate.path.parent == root:
            effective = [name for name in effective if name != "path"]
        groups.setdefault(tuple(effective), []).append(bundle)

    merged = FillStats(total=len(bundles))
    for group_providers, group in groups.items():
        partial = ProviderChain(
            cfg,
            profile=profile_name,
            providers=list(group_providers),
            use_network=use_network,
            manual=manual,
        ).fill_all(group)
        merged.network_queries += partial.network_queries
        merged.network_failed += partial.network_failed
        merged.cache_hits += partial.cache_hits
        merged.errors.extend(partial.errors)
    return merged


class _Planner:
    """计划构造的内部状态机（逐个文件处理，保持逻辑集中）。"""

    def __init__(
        self,
        *,
        cfg: Config,
        filesystem: FileSystem,
        profile: Profile,
        template: str,
        root: Path,
        allow_missing: bool,
        length_limit: int,
    ) -> None:
        self.cfg = cfg
        self.fs = filesystem
        self.profile = profile
        self.template = template
        self.root = root
        self.allow_missing = allow_missing
        self.length_limit = length_limit
        self.items: list[RenameItem] = []
        self.issues: list[PlanIssue] = []
        self.rendered_missing: dict[str, list[str]] = {}
        self._claimed: dict[str, RenameItem] = {}
        self._case_sensitive: dict[Path, bool] = {}

    # ------------------------------------------------------------------
    def handle(self, candidate: FileCandidate, bundle: MetadataBundle) -> None:
        if candidate.skip_reason:
            self._add(candidate, bundle, candidate.path, ItemStatus.SKIPPED, candidate.skip_reason)
            return

        try:
            filename, rendered = build_filename(
                self.template,
                bundle,
                sanitize_cfg=self.cfg.sanitize,
                max_length=self.length_limit,
                lowercase_extension=self.cfg.sanitize.lowercase_extension,
                known_fields=self.profile.fields + ["country_zh"],
            )
        except TemplateError as exc:
            self.issues.append(PlanIssue(candidate.path, "template_error", str(exc)))
            raise

        self.rendered_missing[str(candidate.path)] = sorted(set(rendered.missing))

        if not filename.strip():
            self._block(candidate, bundle, candidate.path, "empty_name", "渲染结果为空")
            return

        dst = candidate.path.parent.joinpath(filename)

        missing_fields = missing_detectable(bundle)
        missing_required = [f for f in self.profile.required_fields if f in missing_fields]
        if missing_required and not self.allow_missing:
            self._block(
                candidate,
                bundle,
                dst,
                "missing:" + ",".join(missing_required),
                f"缺少必填字段: {', '.join(missing_required)}",
            )
            return

        if dst == candidate.path:
            self._add(candidate, bundle, dst, ItemStatus.SKIPPED, "no_op")
            return

        if not check_path_length(dst, platform=self.cfg.sanitize.platform):
            self._block(candidate, bundle, dst, f"path_too_long:>{MAX_PATH}", "完整路径超过上限")
            return

        if not check_component_length(dst.name):
            self._block(candidate, bundle, dst, "name_too_long:>255", "文件名超过 255 字符")
            return

        if dst.parent != candidate.path.parent and not self.fs.same_device(
            dst.parent if dst.parent.exists() else self.root, candidate.path.parent
        ):
            self._block(candidate, bundle, dst, "cross_device", "目标路径跨设备/挂载点")
            return

        self._place(candidate, bundle, dst)

    # ------------------------------------------------------------------
    def _add(
        self,
        candidate: FileCandidate,
        bundle: MetadataBundle,
        dst: Path,
        status: ItemStatus,
        reason: str,
    ) -> RenameItem:
        item = RenameItem(src=candidate.path, dst=dst, status=status, bundle=bundle, reason=reason)
        self.items.append(item)
        return item

    def _block(
        self,
        candidate: FileCandidate,
        bundle: MetadataBundle,
        dst: Path,
        reason: str,
        message: str,
    ) -> None:
        self._add(candidate, bundle, dst, ItemStatus.BLOCKED, reason)
        self.issues.append(PlanIssue(candidate.path, reason.split(":")[0], message))

    def _case_insensitive(self, directory: Path) -> bool:
        if directory not in self._case_sensitive:
            self._case_sensitive[directory] = self.fs.is_case_sensitive(directory)
        return not self._case_sensitive[directory]

    def _key(self, path: Path, directory: Path) -> str:
        if self._case_insensitive(directory):
            return normalize_for_compare(path)
        return str(path)

    def _exists_on_disk(self, path: Path) -> bool:
        directory = path.parent
        if not self.fs.is_dir(directory):
            return False
        if self._case_insensitive(directory):
            target = normalize_for_compare(path.name)
            return any(normalize_for_compare(s.name) == target for s in self.fs.iterdir(directory))
        return self.fs.lexists(path)

    def _place(self, candidate: FileCandidate, bundle: MetadataBundle, dst: Path) -> None:
        """冲突消解：目标被占用就自动加 ` (2)`…` (99)`；仍冲突则标记 CONFLICT。"""
        directory = dst.parent
        src_key = self._key(candidate.path, directory)
        resolved_by = ""

        final = dst
        if self._occupied(dst, src_key):
            suffix = dst.suffix
            stem = dst.name[: -len(suffix)] if suffix else dst.name
            for index in range(2, MAX_CONFLICT_SUFFIX + 1):
                attempt = dst.with_name(f"{stem} ({index}){suffix}")
                if not self._occupied(attempt, src_key):
                    final = attempt
                    resolved_by = f"auto-suffix ({index})"
                    break
            else:
                self._add(candidate, bundle, dst, ItemStatus.CONFLICT, "conflict:exists")
                return

        item = self._add(candidate, bundle, final, ItemStatus.READY, "")
        self._claimed[self._key(final, directory)] = item
        if resolved_by:
            item.conflict_resolved_by = resolved_by
        elif (
            self._case_insensitive(directory)
            and final.name != candidate.path.name
            and final.name.casefold() == candidate.path.name.casefold()
        ):
            # Windows / /mnt/c 上的「仅大小写改名」需要两步 rename（执行器处理）
            item.conflict_resolved_by = "case-only-rename"

    def _occupied(self, path: Path, src_key: str) -> bool:
        """目标是否已被占用。

        排除三类「假占用」：
        1. 本批次自己正在改名的源文件（两阶段 rename 会先让位）；
        2. 大小写不敏感文件系统上「其实就是自己」的目标；
        3. 本批次其它条目已申领的目标 -> 仍算占用（必须换名）。
        """
        directory = path.parent
        key = self._key(path, directory)
        if key == src_key:
            return False
        if key in self._claimed:
            return True
        return self._exists_on_disk(path)


def conflicts(plan: Plan) -> list[RenameItem]:
    """取出未自动消解的冲突项（需要用户手工指定名字或选自动消解）。"""
    return [item for item in plan.items if item.status is ItemStatus.CONFLICT]


def blocked(plan: Plan) -> list[RenameItem]:
    """取出被闸门拦截的项。"""
    return [item for item in plan.items if item.status is ItemStatus.BLOCKED]


def skipped(plan: Plan) -> list[RenameItem]:
    """取出被跳过的项（no_op 或闸门 2 属性不符）。"""
    return [item for item in plan.items if item.status is ItemStatus.SKIPPED]


def render_one(
    template: str,
    bundle: MetadataBundle,
    cfg: Config,
    *,
    fields: list[str],
) -> tuple[str, RenderResult]:
    """单个文件的渲染入口（TUI 实时预览用）。"""
    return build_filename(
        template,
        bundle,
        sanitize_cfg=cfg.sanitize,
        max_length=cfg.sanitize.max_name_length,
        lowercase_extension=cfg.sanitize.lowercase_extension,
        known_fields=fields + ["country_zh"],
    )


__all__ = [
    "PlanIssue",
    "PlanResult",
    "blocked",
    "build_plan",
    "conflicts",
    "missing_detectable",
    "new_session_id",
    "render_one",
    "skipped",
]
