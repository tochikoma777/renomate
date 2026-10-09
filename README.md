# Renomate

> **先给方案、你点头、才动手、随时一键后悔** 的安全批量重命名工具（TUI + CLI）。

首个场景是音乐库：把 `01 稻香.mp3`、`周杰伦 - 稻香 [320K].mp3` 这类乱七八糟的名字，
统一成 `稻香 - 周杰伦 - 魔杰座 - CN.mp3`。

![CI](https://github.com/tochikoma777/renomate/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 1. 安全承诺（不可妥协的四条）

| # | 承诺 | 落地方式 |
|---|---|---|
| 1 | **默认 dry-run**：任何写操作都要显式 `--yes` | `apply` 默认只打印计划，不动文件 |
| 2 | **路径黑名单**：系统目录、挂载根目录一律拒绝 | `safety.is_blocked_path`，进入目录时立即生效 |
| 3 | **执行前确认**：必须看见并点头 | TUI 确认屏要求输入 `APPLY`；CLI 需要 `--token APPLY` |
| 4 | **一键回退**：改完随时后悔 | 事务日志 `$XDG_DATA_HOME/renomate/journals/*.jsonl` + `renomate undo` |

另外：符号链接、隐藏文件、非白名单类型、只读文件**默认全部跳过**；
单个文件失败绝不中断整批，且会尽力回滚。

## 2. 效果预览

<!-- TODO: 放一张 TUI 的 GIF（preview 表格 → 确认屏 → 执行 → undo） -->

```
┌─ renomate v0.1.0 ── profile: audio ── /mnt/d/Music ────────────────────┐
│ 模板：{title} - {artist} - {album} - {country}                          │
│ [x] 01 稻香.mp3      → 稻香 - 周杰伦 - 魔杰座 - CN.mp3                  │
│ [x] 晴天[320K].mp3   → 晴天 - 周杰伦 - 叶惠美 - CN.mp3                  │
│ [ ] unknown.mp3      → 未知标题 - 未知歌手 - 未知专辑 - CN.mp3   ▲缺失   │
│ [!!] CON.mp3         → （Windows 保留名，已拦截）                       │
│ [??] 重名.mp3        → （冲突未消解，需手工指定）                       │
│ OK 2 / -- 1 / !! 1 / ?? 1        Space 勾选  Enter 执行  u 回退  q 退出 │
└────────────────────────────────────────────────────────────────────────┘
```

## 3. 安装

```bash
# 直接用（含 mutagen 音频标签支持）
pip install "renomate[audio]"

# 开发（conda + pip）
conda create -n renomate python=3.12 -y
conda activate renomate
pip install -e ".[dev,audio]"
```

也提供 conda 复现文件：

```bash
conda env create -f environment.yml && conda activate renomate
```

> `mutagen` 是**可选依赖**且运行时动态 import：没装也能跑，只是读不出内嵌标签
> （自动降级为文件名/目录推断）。

## 4. 30 秒上手

```bash
renomate scan    ~/Music                       # 看看扫到了什么
renomate preview ~/Music --preset sorted       # 只看方案，绝不改文件
renomate apply   ~/Music --yes --token APPLY   # 真正执行（默认 dry-run）
renomate undo --last                           # 后悔了，一键还原
```

TUI：

```bash
renomate tui
```

## 5. 模板语法速查

| 写法 | 含义 |
|---|---|
| `{title}` | 取字段值 |
| `{field\|filter}` | 应用过滤器 |
| `{field\|filter:arg}` | 带参过滤器 |
| `{album\|default:未知专辑}` | 字段为空时的缺省值 |
| `{track\|pad:2}` | 数字补零 → `01` |
| `{title\|upper}` / `{lower}` / `{title_case}` | 大小写转换 |
| `{artist\|strip_feat}` | 去掉 `feat.` / `ft.` |
| `{country_zh}` | 国别代码转中文（`CN` → `中国`） |
| `{album}/{track\|pad:2} - {title}` | `/` 表示创建子目录 |

渲染流程（顺序固定）：取值 → 过滤器 → **逐字段 sanitize** → 拼接 → 空折叠
→ 长度截断 → 补扩展名。

内置预设：`simple` / `sorted` / `by_album` / `with_year`（`[presets]` 可自行添加）。

## 6. 安全机制详解（五道闸门）

| 闸门 | 时机 | 行为 |
|---|---|---|
| 1 路径黑名单 | 进入目录时 | `/etc`、`/mnt/c/Windows`、`C:\Program Files`、挂载根目录 → 拒绝 |
| 2 类型/属性白名单 | 扫描时 | 符号链接、隐藏文件、非白名单扩展名、只读文件 → 跳过并记原因 |
| 3 计划体检 | 生成计划时 | `no_op` / `empty_name` / `missing:*` / `path_too_long` / `name_too_long` / `cross_device` / 冲突 → 跳过或拦截 |
| 4 执行前确认 | 执行前 | TUI 确认屏输入 `APPLY`；CLI `--token APPLY` |
| 5 执行期防御 | 执行时 | 两阶段 rename、大小写安全 rename、逐条 try/except |

文件名合规化（Windows/WSL 重点）：非法字符替换、控制字符删除、保留设备名
（`CON.txt` → `_CON.txt`）、末尾空格与点删除、NFC 归一化、`MAX_PATH=260` 与 255 字符上限校验。

## 7. 回退怎么用

```bash
renomate journals                       # 列出所有 session
renomate undo --last                    # 回退最近一次
renomate undo --session 20261007-153012-8f3a
renomate undo --last --dry-run          # 只看会做什么
renomate undo --last --only "稻香*"      # 只回退匹配的文件
renomate recover ~/Music                # 清理崩溃残留的 .renomate_tmp_*
```

回退前会校验 `size` + `mtime_ns`：与日志不一致就标 `needs_manual` 并跳过，
**绝不基于猜测乱改名**。

## 8. 配置

配置文件：`$XDG_CONFIG_HOME/renomate/config.toml`（默认 `~/.config/renomate/config.toml`）。

```bash
renomate config --init      # 生成带注释的默认配置
renomate config --show
renomate config --set general.max_batch=8000
renomate profiles           # 列出 Profile 与字段定义
```

要点：

- `[profiles.audio]`：扩展名白名单、字段列表、`required_fields`、默认模板、Provider 顺序；
- `[presets]`：命名预设；
- `[sanitize]`：平台（`windows`/`posix`）、策略（`replace`/`drop`/`underscore`）、长度上限、替换表；
- `[safety]`：额外黑名单、`allow_missing_fields`、`require_confirm`、`confirm_token`；
- `[musicbrainz]`：**默认关闭**（`enabled = false`），开启后限流 1 req/s 且本地缓存 30 天；
- `[defaults]`：字段兜底值（`未知标题` / `未知歌手` / `CN` …）。

> 目录推断（`path` Provider）只对**嵌套**文件生效：躺在扫描根目录里的文件不会把
> `~/Downloads` 这种目录名当成艺术家。

## 9. 扩展新领域（以 PDF 为例）

1. 写 Provider（实现 `name` / `supports` / `fill`）：

```python
from renomate.models import FieldSource, FieldValue
from renomate.providers import register_provider


class PdfTagProvider:
    name = "pdftag"

    def supports(self, profile: str, path) -> bool:
        return path.suffix.casefold() == ".pdf"

    def fill(self, bundle, cfg) -> None:
        bundle.set("title", FieldValue("论文标题", FieldSource.AUDIOTAG, 0.9))


register_provider(PdfTagProvider())
```

2. 注册 Profile（零代码，只改配置）：

```toml
[profiles.pdf]
extensions = ["pdf"]
fields = ["title", "author", "year"]
required_fields = ["title"]
default_template = "{title} - {author} ({year})"
providers = ["pdftag", "filename", "default"]
```

3. 加测试：`pytest tests/test_providers_pdf.py`。核心层（`models` / `template` / `plan` /
   `executor` / `journal`）**零改动**。

## 10. WSL / Windows 注意事项

- `/mnt/c`、`/mnt/d` 继承 Windows 的**大小写不敏感**语义：`A.mp3 → a.mp3` 必须先改到临时名，
  本工具已实现（`rename_case_safe`）。
- 盘符/挂载根目录本身（`C:\`、`/mnt/c`）拒绝批量重命名。
- Windows 保留设备名带扩展名同样非法（`CON.txt`）。
- 跨设备（EXDEV）不做 move，直接 `BLOCKED: cross_device`。
- 完整路径按 Windows 传统 `MAX_PATH = 260` 校验。

## 11. License

本项目 **MIT**，见 [LICENSE](LICENSE)。

> 关于 `mutagen`：它是 GPL-2.0-or-later，仅作为**可选依赖** + 运行时动态 import 使用，
> 本项目本身不链接、不分发它的代码，仍为 MIT；不安装 `[audio]` 额外依赖时完全不会加载它。

## 12. Roadmap

- [ ] PDF / 照片 / 视频 Profile（架构已预留，见第 9 节）
- [ ] undo 支持跨目录移动（当前仅同目录重命名）
- [ ] 重名智能合并策略（保留更高音质版本）
- [ ] 模板条件段 `{album?[...]}`
- [ ] TUI 单元格就地编辑
