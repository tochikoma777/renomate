"""Provider 链与字段合并策略（SPEC 7.1）。

链顺序（先到先得）：manual → audiotag → path → filename → network → default。
只有「当前字段为空且未锁定」才被覆盖；已存在的值允许被**更高置信度**的 Provider 升级。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from renomate.config import Config
from renomate.country import country_zh
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers import PROVIDER_ORDER, PROVIDER_REGISTRY, MetadataProvider

DEFAULT_ORDER: list[str] = list(PROVIDER_ORDER)
UPGRADE_EPSILON = 1e-6


@dataclass
class FillStats:
    """一次批处理的统计（供报告与 TUI 底栏）。"""

    total: int = 0
    network_queries: int = 0
    network_failed: int = 0
    cache_hits: int = 0
    errors: list[str] = field(default_factory=list)


def make_bundle(path: Path, profile: str, fields: list[str]) -> MetadataBundle:
    """按 Profile 字段列表初始化一个空 bundle（值一律为 None）。"""
    bundle = MetadataBundle(path=path, profile=profile)
    for name in fields:
        bundle.fields[name] = None
    return bundle


def merge_field(old: FieldValue | None, new: FieldValue | None) -> FieldValue | None:
    """字段合并：**先到先得 + 置信度更高的值可升级 + locked 保护**。

    - 当前值为空 → 采用新值；
    - 当前值 locked → 保留（除非新值也是 locked，即用户最后的手工覆盖）；
    - 新值置信度更高 → 升级（SPEC 固定顺序里 `path`(0.6) 先于 `filename`(0.5~0.8)，
      若不比较置信度，`周杰伦 - 稻香.mp3` 的歌手会被目录名抢走）；
    - 其余情况保留原值 → 「低置信度绝不覆盖高置信度」。
    """
    if new is None:
        return old
    if old is None or not old.value.strip():
        return new
    if new.locked and not old.locked:
        return new
    if old.locked:
        return old
    if new.confidence > old.confidence + UPGRADE_EPSILON:
        return new
    return old


class ProviderChain:
    """按固定优先级依次执行 Provider，并做字段合并。"""

    def __init__(
        self,
        cfg: Config,
        *,
        profile: str | None = None,
        providers: list[str] | None = None,
        use_network: bool | None = None,
        manual: dict[str, str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.profile_name = profile or cfg.general.default_profile
        self.stats = FillStats()
        self._providers: list[str] = list(providers) if providers else list(DEFAULT_ORDER)
        self._use_network = (
            cfg.musicbrainz.enabled
            if use_network is None
            else (use_network and cfg.musicbrainz.enabled)
        )
        self._manual = dict(manual or {})

    @property
    def use_network(self) -> bool:
        return self._use_network

    def order(self, providers: list[str] | None = None) -> list[str]:
        """本条链实际执行的 Provider 顺序。"""
        candidates = self._providers if providers is None else providers
        names: list[str] = []
        for name in candidates:
            if name == "network" and not self._use_network:
                continue
            if name == "manual" and not self._manual:
                continue
            if name in PROVIDER_REGISTRY:
                names.append(name)
        return names

    def fill(self, bundle: MetadataBundle, providers: list[str] | None = None) -> MetadataBundle:
        """就地填充一个 bundle 并返回它。"""
        for name in self.order(providers):
            if name == "manual":
                self._apply_manual(bundle)
                continue
            provider: MetadataProvider | None = PROVIDER_REGISTRY.get(name)
            if provider is None:
                continue
            if not provider.supports(bundle.profile, bundle.path):
                continue
            before = dict(bundle.fields)
            provider.fill(bundle, self.cfg)
            self._reconcile(bundle, before)
        self._derive_country_zh(bundle)
        return bundle

    def fill_all(
        self, bundles: list[MetadataBundle], providers: list[str] | None = None
    ) -> FillStats:
        """线程池并发填充（I/O 密集），返回本批次统计。"""
        self.stats = FillStats(total=len(bundles))
        baseline = self._network_baseline()
        if not bundles:
            return self.stats
        workers = max(1, self.cfg.general.max_workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda bundle: self.fill(bundle, providers), bundles))
        self._collect_network_stats(baseline)
        return self.stats

    # ------------------------------------------------------------------
    def _apply_manual(self, bundle: MetadataBundle) -> None:
        for field_name, value in self._manual.items():
            if not value.strip():
                continue
            bundle.fields[field_name] = FieldValue(
                value.strip(), FieldSource.MANUAL, 1.0, locked=True
            )

    def _reconcile(self, bundle: MetadataBundle, before: dict[str, FieldValue | None]) -> None:
        for field_name, previous in before.items():
            current = bundle.fields.get(field_name)
            if current is previous:
                continue
            bundle.fields[field_name] = merge_field(previous, current)

    def _derive_country_zh(self, bundle: MetadataBundle) -> None:
        """country 已知时派生 `{country_zh}`（模板 `{country_zh}` 可用）。"""
        country = bundle.get("country").strip()
        if not country:
            return
        value = country_zh(country)
        bundle.fields["country_zh"] = merge_field(
            bundle.fields.get("country_zh"),
            FieldValue(value, FieldSource.DEFAULT, 0.5),
        )

    def _network_stats(self) -> dict[str, int] | None:
        provider = PROVIDER_REGISTRY.get("network")
        stats = getattr(provider, "stats", None)
        if stats is None:
            return None
        return {
            "queries": int(getattr(stats, "queries", 0)),
            "failures": int(getattr(stats, "failures", 0)),
            "cache_hits": int(getattr(stats, "cache_hits", 0)),
            "errors": len(getattr(stats, "errors", [])),
        }

    def _network_baseline(self) -> dict[str, int] | None:
        """记录起始计数（Provider 是进程级单例，统计需按批次取增量）。"""
        return self._network_stats()

    def _collect_network_stats(self, baseline: dict[str, int] | None) -> None:
        current = self._network_stats()
        if current is None:
            return
        start = baseline or {key: 0 for key in current}
        self.stats.network_queries = current["queries"] - start.get("queries", 0)
        self.stats.network_failed = current["failures"] - start.get("failures", 0)
        self.stats.cache_hits = current["cache_hits"] - start.get("cache_hits", 0)
        provider = PROVIDER_REGISTRY.get("network")
        errors = list(getattr(getattr(provider, "stats", None), "errors", []))
        self.stats.errors = errors[start.get("errors", 0) :]
