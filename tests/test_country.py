"""国别归一化的测试（country_aliases.toml）。"""

from __future__ import annotations

import pytest

from renomate.country import _load, country_zh, normalize_country


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CN", "CN"),
        ("cn", "CN"),
        ("中国", "CN"),
        ("国语", "CN"),
        ("CHN", "CN"),
        ("粤语", "HK"),
        ("HK", "HK"),
        ("TW", "TW"),
        ("台湾", "TW"),
        ("日本", "JP"),
        ("韩国", "KR"),
        ("English", "English"),  # 未收录且非缩写 → 原样返回
        ("xyz", "XYZ"),  # 未收录但像缩写 → 大写
        ("", ""),
    ],
)
def test_normalize_country(raw: str, expected: str) -> None:
    assert normalize_country(raw) == expected


def test_country_zh_lookup() -> None:
    assert country_zh("CN") == "中国"
    assert country_zh("HK") == "香港"
    assert country_zh("jp") == "日本"
    assert country_zh("ZZ") == "ZZ"  # 未收录 → 返回原代码


def test_data_file_is_parsable() -> None:
    aliases, zh = _load()
    assert aliases["中国"] == "CN"
    assert "国语" in aliases
    assert zh["US"] == "美国"
    assert len(aliases) > 20


def test_alias_targets_are_uppercase_codes() -> None:
    aliases, _ = _load()
    for value in aliases.values():
        assert value.isupper()
        assert len(value) == 2
