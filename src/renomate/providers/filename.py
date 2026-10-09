"""文件名正则解析 Provider（SPEC 7.3）。

按优先级依次尝试模式，第一个完整命中（title + artist）即停；部分命中也保留已解析字段。
不要把 `(Live)`、`(Remix)`、`feat. xxx` 一律删掉——它们进入 `extra` 字段。
"""

from __future__ import annotations

import re
from pathlib import Path

from renomate.config import Config
from renomate.models import FieldSource, FieldValue, MetadataBundle
from renomate.providers import register_provider

FILENAME_PATTERNS: list[tuple[str, str, float]] = [
    # (名称, 正则, 置信度)
    ("artist-title", r"^(?P<artist>[^-–—]+?)\s*[-–—]\s*(?P<title>.+?)$", 0.80),
    ("title-artist", r"^(?P<title>[^-–—]+?)\s*[-–—]\s*(?P<artist>.+?)$", 0.60),
    (
        "brackets-album",
        r"^\[(?P<album>[^\]]+)\]\s*(?P<artist>[^-–—]+?)\s*[-–—]\s*(?P<title>.+?)$",
        0.75,
    ),
    ("track-title", r"^(?P<track>\d{1,3})[.\s、_-]+\s*(?P<title>.+?)$", 0.55),
    (
        "track-artist-title",
        r"^(?P<track>\d{1,3})[.\s_-]+\s*(?P<artist>[^-–—]+?)\s*[-–—]\s*(?P<title>.+?)$",
        0.75,
    ),
    ("paren-title", r"^(?P<title>.+?)\s*[（(](?P<extra>[^）)]*)[）)]\s*$", 0.45),
]

_COMPILED: list[tuple[str, re.Pattern[str], float]] = [
    (name, re.compile(pattern), confidence) for name, pattern, confidence in FILENAME_PATTERNS
]

# 噪声清洗（顺序固定）
_SITE_WATERMARK_RE = re.compile(
    r"(\[[^\]]*?(?:www\.|https?://|\.com|\.net|\.cn)[^\]]*\]"
    r"|【[^】]*?(?:资源|下载|音乐|无损)[^】]*】"
    r"|\s*-\s*[^-\[\]（）]{0,12}(?:音乐|无损音乐|酷狗|QQ音乐|网易云)\s*$)",
    re.IGNORECASE,
)
_QUALITY_RE = re.compile(
    r"\s*[\[（(]\s*(?:\d{2,4}\s*[kK](?:bps)?|FLAC|APE|HQ|SQ|MQA|DSD|LOSSLESS|320K?|128K?)\s*[\]）)]",
    re.IGNORECASE,
)
_LEADING_TRACK_RE = re.compile(r"^\s*(?P<track>\d{1,3})\s*[.\s、_-]+\s*")
_WHITESPACE_RE = re.compile(r"\s+")
_FULLWIDTH_SPACE = "\u3000"

FALLBACK_TITLE_CONFIDENCE = 0.40


def clean_noise(stem: str) -> tuple[str, dict[str, str]]:
    """清洗文件名噪声，返回 (清洗后的名字, {字段名: 值})。

    清洗顺序固定：站点水印 → 音质/格式标记 → 全角空格 → 空白压缩 → strip。
    """
    text = _SITE_WATERMARK_RE.sub("", stem)
    text = _QUALITY_RE.sub("", text)
    text = text.replace(_FULLWIDTH_SPACE, " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip(), {}


def parse_filename(path: Path) -> tuple[dict[str, str], float, str]:
    """解析文件名，返回 (字段字典, 置信度, 命中的模式名)。"""
    stem = path.stem
    cleaned, _ = clean_noise(stem)
    fields: dict[str, str] = {}
    confidence = 0.0
    matched = ""

    for name, pattern, score in _COMPILED:
        match = pattern.match(cleaned)
        if not match:
            continue
        group_fields = {
            key: value.strip()
            for key, value in match.groupdict().items()
            if value and value.strip()
        }
        if group_fields:
            fields.update(group_fields)
            confidence = max(confidence, score)
            matched = name
        if "title" in fields and "artist" in fields:
            break

    if not fields:
        # 没有任何模式命中：至少把清洗后的名字当作标题（低置信度）
        if cleaned:
            fields["title"] = cleaned
            confidence = FALLBACK_TITLE_CONFIDENCE
            matched = "fallback-whole-name"
        return fields, confidence, matched

    # 序号残留：模式没抓到 track 时，尝试从开头补一个（已被 track 捕获的情况无需处理）
    if "track" not in fields:
        lead = _LEADING_TRACK_RE.match(cleaned)
        if lead:
            fields["track"] = lead.group("track")

    # feat. / ft. 归入 extra，而不是丢掉
    extra_from_title = _extract_extra(fields.get("title", ""))
    if extra_from_title and not fields.get("extra"):
        fields["extra"] = extra_from_title

    if "title" not in fields:
        fields["title"] = cleaned

    return fields, confidence, matched


_FEAT_RE = re.compile(
    r"[\s]*(?:[（(](?:feat\.?|ft\.?|featuring)[^）)]*[）)]"
    r"|[\s]*(?:feat\.?|ft\.?)\s+[^-–—（）()]+)",
    re.IGNORECASE,
)


def _extract_extra(title: str) -> str:
    match = _FEAT_RE.search(title)
    return match.group(0).strip() if match else ""


class FilenameProvider:
    """从文件名解析字段，置信度 0.5 ~ 0.8（按命中的模式）。"""

    name = "filename"

    def supports(self, profile: str, path: Path) -> bool:
        return path.name.strip() != ""

    def fill(self, bundle: MetadataBundle, cfg: Config) -> None:
        fields, confidence, matched = parse_filename(bundle.path)
        if not fields:
            return
        for key, value in fields.items():
            if key not in bundle.fields:
                continue
            score = confidence
            if key == "track":
                score = max(confidence, 0.70)
            bundle.fields[key] = FieldValue(value, FieldSource.FILENAME, score)


register_provider(FilenameProvider())
