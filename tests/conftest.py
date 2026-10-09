"""pytest 公共 fixture：带标签的音频文件、小型音乐库、隔离的 XDG 目录。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from renomate.config import Config, default_config

# ---------------------------------------------------------------------------
# 最小合法音频容器（mutagen 只关心 tag 区，不需要真实音频数据）
# ---------------------------------------------------------------------------

_MPEG_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * (417 - 4)


def _flac_streaminfo() -> bytes:
    bits: list[int] = []

    def write(value: int, width: int) -> None:
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    write(4096, 16)  # min block size
    write(4096, 16)  # max block size
    write(0, 24)  # min frame size
    write(0, 24)  # max frame size
    write(44100, 20)  # sample rate
    write(1, 3)  # channels - 1 → 2 channels
    write(15, 5)  # bits per sample - 1 → 16bit
    write(0, 36)  # total samples
    for _ in range(16):  # md5
        write(0, 8)

    payload = bytearray()
    acc = 0
    count = 0
    for bit in bits:
        acc = (acc << 1) | bit
        count += 1
        if count == 8:
            payload.append(acc)
            acc = 0
            count = 0
    return bytes(payload)


def write_mp3(path: Path) -> None:
    """写一个最小合法 MP3（ID3v2 空 tag + 两个 MPEG 帧）。"""
    path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + _MPEG_FRAME * 2)


def write_flac(path: Path) -> None:
    """写一个最小合法 FLAC（fLaC + STREAMINFO）。"""
    info = _flac_streaminfo()
    path.write_bytes(b"fLaC" + b"\x80" + len(info).to_bytes(3, "big") + info)


def tag_mp3(path: Path, **tags: Any) -> None:
    """给 MP3 写内嵌标签（easy 模式键名）。"""
    import mutagen

    write_mp3(path)
    audio = mutagen.File(path, easy=True)
    assert audio is not None
    if audio.tags is None:
        audio.add_tags()
    for key, value in tags.items():
        audio[key] = value
    audio.save()


def tag_flac(path: Path, **tags: Any) -> None:
    """给 FLAC 写内嵌标签。"""
    import mutagen

    write_flac(path)
    audio = mutagen.File(path)
    assert audio is not None
    if audio.tags is None:
        audio.add_tags()
    for key, value in tags.items():
        audio[key] = value if isinstance(value, list) else [value]
    audio.save()


@pytest.fixture(autouse=True)
def isolate_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 XDG 配置/数据/缓存目录重定向到 tmp_path，避免污染真实 HOME。"""
    base = tmp_path / "xdg"
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(base / name.split("_")[1].lower()))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(base / "home"))
    (base / "home").mkdir(parents=True, exist_ok=True)
    return base


@pytest.fixture
def cfg() -> Config:
    """干净的默认配置（不读磁盘）。"""
    return default_config()


@pytest.fixture
def music_lib(tmp_path: Path) -> Path:
    """小型音乐库：带标签文件、无标签文件、坏文件、非音频、隐藏文件、符号链接。"""
    root = tmp_path / "Music"
    (root / "周杰伦" / "魔杰座").mkdir(parents=True)
    (root / "周杰伦" / "叶惠美").mkdir(parents=True)
    (root / "陈奕迅").mkdir(parents=True)

    tag_mp3(
        root / "周杰伦" / "魔杰座" / "01 稻香.mp3",
        title="稻香",
        artist="周杰伦",
        album="魔杰座",
        date="2008",
        tracknumber="1",
        genre="Pop",
    )
    tag_mp3(
        root / "周杰伦" / "叶惠美" / "晴天[320K].mp3",
        title="晴天",
        artist="周杰伦",
        album="叶惠美",
        date="2003",
        tracknumber="3",
    )
    tag_flac(
        root / "陈奕迅" / "K歌之王.flac",
        title="K歌之王",
        artist="陈奕迅",
        album="打得火热",
        date="2000",
    )
    # 无标签文件
    write_mp3(root / "陈奕迅" / "unknown.mp3")
    # 坏文件（内容不是音频）
    (root / "broken.mp3").write_bytes(b"not an audio file")
    # 非白名单类型
    (root / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (root / "notes.txt").write_text("playlist", encoding="utf-8")
    # 隐藏文件
    write_mp3(root / ".hidden.mp3")
    # 符号链接
    target = root / "周杰伦" / "魔杰座" / "01 稻香.mp3"
    link = root / "link.mp3"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows 无权限时
        pass
    return root
