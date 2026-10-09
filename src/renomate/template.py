"""模板渲染引擎（SPEC 8.3）。

领域无关：只认字段名的字符串，不 import 任何 Provider/CLI/TUI。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable

from renomate.config import SanitizeConfig
from renomate.models import MetadataBundle
from renomate.safety import EmptyNameError, sanitize_component

PLACEHOLDER_RE = re.compile(r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\|(?P<filters>[^}]*))?\}")

FilterFunc = Callable[[str, str | None], str]

_HONORIFICS = frozenset({"feat", "ft", "featuring", "vs", "with"})
_FEAT_RE = re.compile(
    r"\s*[\(（]\s*(?:feat\.?|ft\.?|featuring)\b[^)）]*[)）]"
    r"|\s*\b(?:feat\.?|ft\.?)\s+[^-–—(（)）]+",
    re.IGNORECASE,
)
# 字面量里的「分隔符」定义为：空白 + 各类连字符 + 点 + 下划线。其余字符视为正文。
_SEP_SPLIT_RE = re.compile(r"([\s\-–—._]+)")


class TemplateError(ValueError):
    """模板语法错误（未知占位符 / 未知过滤器）。"""


@dataclass
class RenderResult:
    """一次渲染的产物（接口见 SPEC 8.3）。"""

    name: str  # 不含扩展名（可能含 `/`，表示子目录）
    missing: list[str] = field(default_factory=list)  # 缺失字段名
    truncated: bool = False
    replaced: dict[str, str] = field(  # 被替换掉的非法字符 → 替换成什么
        default_factory=dict
    )


def _title_case(value: str) -> str:
    """标题化：分词后首字母大写，保留 `feat`/`vs` 等惯用小写词。"""
    parts = re.split(r"(\s+|-|–|—)", value)
    fixed: list[str] = []
    for part in parts:
        if not part or part.isspace() or part in {"-", "–", "—"}:
            fixed.append(part)
        elif part.casefold() in _HONORIFICS:
            fixed.append(part)
        else:
            fixed.append(part[:1].upper() + part[1:].lower())
    return "".join(fixed)


def _pad(value: str, arg: str | None) -> str:
    width = int(arg) if arg and arg.isdigit() else 2
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return value
    return digits.zfill(width)


FILTERS: dict[str, FilterFunc] = {
    "upper": lambda value, arg: value.upper(),
    "lower": lambda value, arg: value.lower(),
    "title_case": lambda value, arg: _title_case(value),
    "pad": lambda value, arg: _pad(value, arg),
    "strip_feat": lambda value, arg: _FEAT_RE.sub("", value).strip(),
    "default": lambda value, arg: value if value.strip() else (arg or ""),
}


def list_filters() -> list[str]:
    """可用过滤器（按名字排序，用于报错信息与文档）。"""
    return sorted(FILTERS)


def _apply_filters(value: str, expression: str | None) -> str:
    """按 `|` 从左到右应用过滤器（SPEC 8.2 步骤 2）。"""
    if not expression:
        return value
    for token in expression.split("|"):
        token = token.strip()
        if not token:
            continue
        name, _, arg = token.partition(":")
        name = name.strip()
        func = FILTERS.get(name)
        if func is None:
            msg = f"未知过滤器 {name!r}（可用: {', '.join(list_filters())}）"
            raise TemplateError(msg)
        value = func(value, arg.strip() if arg else None)
    return value


def template_fields(template: str) -> list[str]:
    """提取模板里用到的全部字段名（去重、保序）。"""
    names: list[str] = []
    for match in PLACEHOLDER_RE.finditer(template):
        name = match.group("name")
        if name not in names:
            names.append(name)
    return names


def validate_template(template: str, known_fields: list[str]) -> list[str]:
    """返回模板中引用了、但 Profile 里不存在的字段名。"""
    return [name for name in template_fields(template) if name not in known_fields]


def _sanitize_value(
    value: str, sanitize_cfg: SanitizeConfig, platform: str
) -> tuple[str, dict[str, str]]:
    """逐个字段 sanitize（SPEC 8.2 步骤 3）。"""
    try:
        return sanitize_component(
            value,
            platform=platform,
            strategy=sanitize_cfg.strategy,
            char_map=sanitize_cfg.replacement_map,
        )
    except EmptyNameError:
        return "", {}


def _tokenize(template: str) -> list[tuple[str, str, str]]:
    """把模板切成 token 序列：("field", 字段名, 过滤器) / ("literal", 文本, "")。"""
    tokens: list[tuple[str, str, str]] = []
    cursor = 0
    for match in PLACEHOLDER_RE.finditer(template):
        if match.start() > cursor:
            tokens.append(("literal", template[cursor : match.start()], ""))
        tokens.append(("field", match.group("name"), match.group("filters") or ""))
        cursor = match.end()
    if cursor < len(template):
        tokens.append(("literal", template[cursor:], ""))
    return tokens


def _assemble(tokens: list[tuple[str, str, str]], values: dict[int, str]) -> str:
    """按 token 粒度拼装（SPEC 8.2 步骤 4 + 5）。

    空字段不输出，其两侧的分隔符合并成一个；首尾分隔符不输出。
    只处理**字面量**带来的分隔符，绝不动字段内容本身。
    """
    output = ""
    pending_separator = ""

    def flush() -> None:
        nonlocal output, pending_separator
        if pending_separator and output:
            output += pending_separator
        pending_separator = ""

    for index, (kind, name, _filters) in enumerate(tokens):
        if kind == "field":
            value = values.get(index, "")
            if not value.strip():
                continue  # 丢弃悬空分隔符由 pending 合并逻辑处理
            flush()
            output += value
            continue

        for chunk in _SEP_SPLIT_RE.split(name):
            if not chunk:
                continue
            if _SEP_SPLIT_RE.fullmatch(chunk):
                if not pending_separator:
                    pending_separator = chunk
                continue
            flush()
            output += chunk

    return output


def render(
    template: str,
    bundle: MetadataBundle,
    *,
    platform: str = "windows",
    max_length: int = 180,
    sanitize_cfg: SanitizeConfig,
    known_fields: list[str] | None = None,
) -> RenderResult:
    """把模板渲染成合法的文件名**主干**（不含扩展名，可能含子目录 `/`）。

    Args:
        template: 形如 `{title} - {artist} - {album|default:未知}`。
        bundle: 已由 ProviderChain 填好的元数据。
        platform: `windows` | `posix`，决定非法字符集。
        max_length: 完整文件名（含扩展名）上限，默认 180。
        sanitize_cfg: 来自配置的清洗参数（strategy / 替换表）。
        known_fields: 若给出，未知占位符会抛 `TemplateError`。

    Raises:
        TemplateError: 使用了未声明的占位符或未知过滤器。
    """
    tokens = _tokenize(template)

    if known_fields is not None:
        unknown_field_names = [
            name for kind, name, _ in tokens if kind == "field" and name not in known_fields
        ]
        if unknown_field_names:
            msg = f"模板引用了未知字段: {', '.join(unknown_field_names)}"
            raise TemplateError(msg)

    missing: list[str] = []
    replaced: dict[str, str] = {}
    values: dict[int, str] = {}

    for index, (kind, name, filters) in enumerate(tokens):
        if kind != "field":
            continue
        raw_value = bundle.get(name)
        if not raw_value.strip():
            missing.append(name)
        value = _apply_filters(raw_value, filters)
        cleaned, local_replaced = _sanitize_value(value, sanitize_cfg, platform)
        replaced.update(local_replaced)
        values[index] = cleaned

    assembled = _assemble(tokens, values)
    if not assembled.strip():
        return RenderResult(name="", missing=sorted(set(missing)), replaced=replaced)

    # 拼好后对每个路径分量做兜底合规（`by_album` 这类模板带有 `/`）
    components: list[str] = []
    for component in assembled.split("/"):
        try:
            safe, final_replaced = sanitize_component(
                component,
                platform=platform,
                strategy=sanitize_cfg.strategy,
                char_map=sanitize_cfg.replacement_map,
            )
        except EmptyNameError:
            return RenderResult(name="", missing=sorted(set(missing)), replaced=replaced)
        components.append(safe)
        replaced.update(final_replaced)

    del max_length  # 渲染阶段不截断，截断在 build_filename 里统一处理
    return RenderResult(
        name="/".join(components),
        missing=sorted(set(missing)),
        replaced=replaced,
    )


def truncate_name(
    name: str,
    suffix: str,
    *,
    max_length: int = 180,
    truncate_suffix: str = "…",
) -> tuple[str, bool]:
    """按「完整文件名（含扩展名）」长度上限截断，并保证唯一性。

    截断时追加内容的 sha1 前 6 位，避免不同长名字截断后撞名。
    """
    full = name + suffix
    if len(full) <= max_length:
        return name, False

    budget = max(1, max_length - len(suffix))
    short_hash = hashlib.sha1(full.encode("utf-8")).hexdigest()[:6]
    tail = f"{truncate_suffix}_{short_hash}" if truncate_suffix else f"_{short_hash}"
    keep = max(1, budget - len(tail))
    truncated = name[:keep].rstrip(" -–—.") + tail
    return truncated, True


def build_filename(
    template: str,
    bundle: MetadataBundle,
    *,
    sanitize_cfg: SanitizeConfig,
    max_length: int | None = None,
    extension: str | None = None,
    lowercase_extension: bool | None = None,
    known_fields: list[str] | None = None,
) -> tuple[str, RenderResult]:
    """渲染 + 截断 + 补扩展名（SPEC 8.2 步骤 6/7），返回 (完整文件名, RenderResult)。"""
    limit = sanitize_cfg.max_name_length if max_length is None else max_length
    suffix = extension if extension is not None else bundle.path.suffix
    lower_ext = (
        sanitize_cfg.lowercase_extension if lowercase_extension is None else lowercase_extension
    )
    if lower_ext:
        suffix = suffix.lower()

    result = render(
        template,
        bundle,
        platform=sanitize_cfg.platform,
        max_length=limit,
        sanitize_cfg=sanitize_cfg,
        known_fields=known_fields,
    )
    if not result.name.strip():
        return "", result

    stem_path = result.name.split("/")
    stem = stem_path[-1]
    prefix = stem_path[:-1]

    name, truncated = truncate_name(
        stem, suffix, max_length=limit, truncate_suffix=sanitize_cfg.truncate_suffix
    )
    result.truncated = truncated
    result.name = "/".join([*prefix, name])
    return result.name + suffix, result
