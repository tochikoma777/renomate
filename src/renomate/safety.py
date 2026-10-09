"""文件名合规化 + 路径黑名单 + 风险判定（SPEC 第 9、10 节）。

闸门 1（路径黑名单）与闸门 2（类型/属性白名单）在此实现；
`sanitize_component` 为 SPEC 9.2 给出的参考实现，原样落地。
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path, PurePath, PureWindowsPath

from renomate.platform.fs import FileSystem, get_default_fs

ILLEGAL_CHARS = set('<>:"/\\|?*')
DEFAULT_MAP = {
    "/": "／",
    "\\": "＼",
    ":": "：",
    "?": "？",
    "*": "＊",
    '"': "＂",
    "<": "＜",
    ">": "＞",
    "|": "｜",
}
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{i}" for i in range(10)},
    *{f"LPT{i}" for i in range(10)},
}
CONTROL_RE = re.compile(r"[\x00-\x1f]")


class EmptyNameError(ValueError):
    """文件名清洗后为空（SPEC 9.1：渲染结果为空 → BLOCKED，原因 empty_name）。"""


class BlockedPathError(Exception):
    """目标路径命中黑名单，或为挂载/盘符根目录。

    Attributes:
        path: 被拒绝的路径。
        reason: 人类可读原因。
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


# fmt: off  —— SPEC 9.2 参考实现，逐行原样落地，禁止格式化改写
def sanitize_component(
    name: str,
    *,
    platform: str = "windows",
    strategy: str = "replace",
    char_map: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """把单个路径分量（文件名，不含目录）变成目标平台合法的名字。
    返回 (新名字, {原字符: 替换字符})，替换记录用于报告与日志。"""
    char_map = char_map or DEFAULT_MAP
    replaced: dict[str, str] = {}

    name = unicodedata.normalize("NFC", name)

    if platform == "windows":
        name = CONTROL_RE.sub("", name)
        buf = []
        for ch in name:
            if ch in ILLEGAL_CHARS:
                if strategy == "drop":
                    replaced.setdefault(ch, "")
                    continue
                if strategy == "underscore":
                    buf.append("_")
                    replaced.setdefault(ch, "_")
                    continue
                sub = char_map.get(ch, "_")
                buf.append(sub)
                replaced.setdefault(ch, sub)
            else:
                buf.append(ch)
        name = "".join(buf)
        name = name.rstrip(" .")  # 末尾空格与点
        name = name.lstrip(" ")
        stem = PureWindowsPath(name).stem.upper()
        if stem in RESERVED:  # CON.txt 也要拦
            name = "_" + name
    else:
        name = name.replace("\x00", "").replace("/", "／")
        if name in (".", ".."):
            name = "_" + name

    if not name.strip():
        raise EmptyNameError(name)
    return name, replaced


# fmt: on


BLOCKED_POSIX_PREFIXES = {
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/proc",
    "/root",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
    "/snap",
    "/opt",
    "/mnt/c/WINDOWS",
    "/mnt/c/Program Files",
    "/mnt/c/Program Files (x86)",
    "/mnt/c/ProgramData",
    "/mnt/c/System32",
    "/mnt/c/Users/Default",
    "/mnt/c/Recovery",
    "/mnt/c/$Recycle.Bin",
    "/mnt/c/Windows",  # 大小写不敏感比对
    "/mnt/wsl",
    "/mnt/wslg",
}
BLOCKED_WINDOWS_PREFIXES = {
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
    "C:\\System32",
    "C:\\Users\\Default",
    "C:\\Recovery",
    "C:\\$Recycle.Bin",
}

MAX_PATH = 260  # Windows 传统 MAX_PATH
MAX_COMPONENT = 255  # 单个文件名长度上限

_WSL_MOUNT_ROOT_RE = re.compile(r"^/mnt/[a-z]/?$")
_DRIVE_ROOT_RE = re.compile(r"^[a-z]:[\\/]?$")

RENOMATE_TMP_PREFIX = ".renomate_tmp_"
RENOMATE_CASE_PREFIX = ".renomate_case_"


def normalize_for_compare(path: Path | str) -> str:
    """统一为小写、正斜杠、无末尾斜杠的形式，用于大小写不敏感比对。"""
    text = str(path).replace("\\", "/")
    if len(text) > 1:
        text = text.rstrip("/")
    return text.casefold()


def is_mount_root(path: Path | str) -> bool:
    """盘符/挂载根目录本身（`/`、`/mnt/c`、`/home`、`C:\\`）→ True。"""
    text = normalize_for_compare(path)
    if text in ("", "/"):
        return True
    if _DRIVE_ROOT_RE.match(text):
        return True
    if _WSL_MOUNT_ROOT_RE.match(text):
        return True
    parts = PurePath(text).parts if not _DRIVE_ROOT_RE.match(text) else ()
    # `/home`、`/usr`、`/mnt` 这类一级目录同样视为根级，批量重命名是灾难
    if text.startswith("/") and len(parts) <= 2:
        return True
    return False


def is_blocked_path(
    path: Path,
    *,
    extra_blocked: list[str] | None = None,
    allow_override: bool = False,
) -> str | None:
    """闸门 1：路径黑名单。返回人类可读原因，通过时返回 None。"""
    if allow_override:
        return None
    if is_mount_root(path):
        return "拒绝在挂载根目录/盘符根目录上批量重命名"

    text = normalize_for_compare(path)
    blocked = {normalize_for_compare(p) for p in BLOCKED_POSIX_PREFIXES}
    blocked |= {normalize_for_compare(p) for p in BLOCKED_WINDOWS_PREFIXES}
    blocked |= {normalize_for_compare(p) for p in (extra_blocked or [])}

    for prefix in sorted(blocked, key=len, reverse=True):
        if text == prefix or text.startswith(prefix + "/"):
            return f"路径位于黑名单目录（{prefix}）"
    return None


def check_path_length(path: Path, *, platform: str = "windows") -> bool:
    """闸门 3：完整路径长度校验（Windows 传统 MAX_PATH=260）。"""
    if platform != "windows":
        return True
    return len(str(path)) <= MAX_PATH


def check_component_length(name: str) -> bool:
    """单个文件名长度 ≤ 255 字符（UTF-16 码元计数，NTFS 的真实上限）。"""
    return len(name) <= MAX_COMPONENT


def classify_file(
    path: Path,
    *,
    allowed_extensions: list[str] | None = None,
    fs: FileSystem | None = None,
    include_hidden: bool = False,
    follow_symlinks: bool = False,
) -> str | None:
    """闸门 2：类型与属性白名单。返回跳过原因（人类可读），通过时返回 None。

    检查顺序与 SPEC 10 闸门 2 一致：自身产物 → 符号链接 → 隐藏 → 非常规文件
    → 扩展名白名单 → 可写性。
    """
    filesystem: FileSystem = fs if fs is not None else get_default_fs()

    name = path.name
    if name.startswith(RENOMATE_TMP_PREFIX) or name.startswith(RENOMATE_CASE_PREFIX):
        return "renomate 自有临时文件，永不处理"

    if filesystem.is_symlink(path) and not follow_symlinks:
        return "is_symlink: 符号链接（默认不跟随）"

    if not include_hidden and filesystem.is_hidden(path):
        return "is_hidden: 隐藏文件"

    if not filesystem.is_file(path):
        return "not_regular_file: 非常规文件（目录/设备/socket/fifo）"

    if allowed_extensions is not None:
        suffix = path.suffix[1:] if path.suffix.startswith(".") else path.suffix
        if suffix.casefold() not in {e.casefold() for e in allowed_extensions}:
            return "扩展名不在白名单"

    if not filesystem.access(path, os.W_OK):
        return "not_writable: 文件不可写（只读或权限不足）"

    return None
