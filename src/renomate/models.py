"""核心数据模型。

本模块是「核心层」，绝对领域无关：只认字段名字符串，不 import 任何 Provider。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class FieldSource(str, Enum):
    AUDIOTAG = "audiotag"  # 内嵌标签，置信度最高
    FILENAME = "filename"  # 文件名解析
    PATH = "path"  # 从父目录名推断（专辑/歌手目录）
    NETWORK = "network"  # MusicBrainz
    MANUAL = "manual"  # 用户手工输入（最高优先级，锁定）
    DEFAULT = "default"  # 配置缺省值
    UNKNOWN = "unknown"  # 未解析出来


@dataclass(frozen=True)
class FieldValue:
    value: str
    source: FieldSource
    confidence: float  # 0.0 ~ 1.0
    locked: bool = False  # True = 不被后续 Provider 覆盖


@dataclass
class MetadataBundle:
    """一个文件的全部字段。字段名由 Profile 决定，核心层不关心具体名字。"""

    path: Path
    profile: str
    fields: dict[str, FieldValue | None] = field(default_factory=dict)

    def get(self, name: str) -> str:
        fv = self.fields.get(name)
        return fv.value if fv else ""

    def set(self, name: str, fv: FieldValue) -> None:
        self.fields[name] = fv

    def missing(self) -> list[str]:
        return [k for k, v in self.fields.items() if v is None or not v.value.strip()]


class ItemStatus(str, Enum):
    PENDING = "pending"  # 待执行
    SKIPPED = "skipped"  # 跳过（no-op、被拦截、用户取消）
    READY = "ready"  # 校验通过
    BLOCKED = "blocked"  # 被安全闸门拦截
    CONFLICT = "conflict"  # 目标冲突（未自动消解）
    APPLIED = "applied"
    FAILED = "failed"


@dataclass
class RenameItem:
    src: Path
    dst: Path
    status: ItemStatus
    bundle: MetadataBundle
    reason: str = ""  # 跳过/拦截/失败的人类可读原因
    user_checked: bool = True  # TUI 中是否被勾选
    conflict_resolved_by: str = ""  # 如 "auto-suffix (2)"


@dataclass
class Plan:
    session_id: str
    root: Path
    profile: str
    template: str
    items: list[RenameItem]

    def counts(self) -> dict[ItemStatus, int]:
        """统计各状态数量，供 TUI 底栏与报告使用（含 0 值，顺序稳定）。"""
        result: dict[ItemStatus, int] = {s: 0 for s in ItemStatus}
        for item in self.items:
            result[item.status] = result.get(item.status, 0) + 1
        return result

    def to_dict(self) -> dict:
        """序列化为 JSON 友好的 dict（供 --out plan.json 与报告导出）。"""
        return {
            "session_id": self.session_id,
            "root": str(self.root),
            "profile": self.profile,
            "template": self.template,
            "items": [
                {
                    "src": str(item.src),
                    "dst": str(item.dst),
                    "status": item.status.value,
                    "reason": item.reason,
                    "user_checked": item.user_checked,
                    "conflict_resolved_by": item.conflict_resolved_by,
                    "fields": {
                        name: (
                            None
                            if fv is None
                            else {
                                "value": fv.value,
                                "source": fv.source.value,
                                "confidence": fv.confidence,
                                "locked": fv.locked,
                            }
                        )
                        for name, fv in item.bundle.fields.items()
                    },
                }
                for item in self.items
            ],
        }
