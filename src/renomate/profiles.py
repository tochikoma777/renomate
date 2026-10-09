"""Profile（领域档案）注册表。

Profile 定义「这一类文件有哪些字段、哪些扩展名、默认模板、用哪些 Provider」。
V1 只注册 audio；V1.1 加 PDF 只需新增一个 Provider + 一条注册，核心零改动。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from renomate.config import Config, ProfileConfig


class UnknownProfileError(KeyError):
    """请求的 Profile 不存在。"""


@dataclass
class Profile:
    name: str
    extensions: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    default_template: str = ""
    providers: list[str] = field(default_factory=list)

    def matches_extension(self, suffix: str) -> bool:
        """扩展名是否在白名单内（比较时忽略前导点与大小写）。"""
        clean = suffix[1:] if suffix.startswith(".") else suffix
        return clean.casefold() in {e.casefold() for e in self.extensions}


PROFILES: dict[str, Profile] = {}


def register_profile(profile: Profile) -> Profile:
    """注册一个 Profile（同名覆盖）。"""
    PROFILES[profile.name] = profile
    return profile


def unregister_profile(name: str) -> None:
    """仅供测试使用：从注册表移除。"""
    PROFILES.pop(name, None)


def profile_from_config(cfg: ProfileConfig) -> Profile:
    return Profile(
        name=cfg.name,
        extensions=list(cfg.extensions),
        fields=list(cfg.fields),
        required_fields=list(cfg.required_fields),
        default_template=cfg.default_template,
        providers=list(cfg.providers),
    )


register_profile(profile_from_config(ProfileConfig(name="audio")))


def get_profile(name: str | None = None, cfg: Config | None = None) -> Profile:
    """按名字取 Profile：注册表 → 配置 → 内置 audio。

    找不到时抛 `UnknownProfileError`（CLI 映射为退出码 3）。
    """
    key = name or (cfg.general.default_profile if cfg else "audio")
    if key in PROFILES:
        return PROFILES[key]
    if cfg is not None and key in cfg.profiles:
        return profile_from_config(cfg.profiles[key])
    if key == "audio":
        return profile_from_config(ProfileConfig(name="audio"))
    msg = f"未知 Profile: {key!r}（可用: {', '.join(list_profiles(cfg))}）"
    raise UnknownProfileError(msg)


def list_profiles(cfg: Config | None = None) -> list[str]:
    """列出所有可用 Profile 名（注册表 ∪ 配置），顺序稳定。"""
    names = list(PROFILES)
    if cfg is not None:
        for name in cfg.profiles:
            if name not in names:
                names.append(name)
    return sorted(names)
