# Renomate —— 面向"强迫症"的安全批量重命名工具

> 项目规格书 + 技术实现指南（V0.1.0）
> 目标：**把本文档整段复制进 CodeBuddy CLI 即可让 Agent 完成实现，并最终推送到 GitHub。**
> 开发环境：Windows 笔记本 + WSL2 (Ubuntu) + VS Code + CodeBuddy CLI
> 编写日期：2026-10-07

---

## 0. 一句话定义

**Renomate** 是一个「先给方案、你点头、才动手、随时一键后悔」的批量文件重命名工具。
首个场景是音乐库：把 `01 稻香.mp3`、`周杰伦 - 稻香 [320K].mp3` 这类乱七八糟的名字，统一成
`稻香 - 周杰伦 - 魔杰座 - CN.mp3`。

三条不可妥协的产品底线：

1. **绝不碰危险文件**（系统目录、只读文件、符号链接、非白名单类型一律跳过）；
2. **改之前必须让你看见并确认**（结构化预览 + 显式确认，默认 dry-run）；
3. **改完之后必须能一键回退**（事务日志 + `undo` 命令，支持部分失败场景）。

---

## 1. 【可直接复制】给 CodeBuddy CLI 的执行指令

路径已写死为本机实际路径，直接整段复制即可。

```
你是一个资深 Python 工程师。请严格按
/home/tachikoma/projects/utilities/renomate/docs/SPEC.md 的规格，
从零实现项目 renomate，工作目录 /home/tachikoma/projects/utilities/renomate。

硬性要求：
1. 严格遵循规格书中的目录结构、数据模型、接口签名、风险控制规则与命名规范；
   规格书中已给出的代码片段必须原样落地，不要自行发明替代方案。
2. 环境与依赖：本地使用 conda 独立环境（Python 3.12），包安装统一用 pip；
   依赖声明只写在 pyproject.toml（含 [project.optional-dependencies]）；
   不要使用 uv、不要生成 uv.lock、不要在代码/文档/CI 中出现任何 uv 命令；
   另需生成 environment.yml 供 conda 用户复现；Python >= 3.11 兼容；
   所有代码带类型注解；通过 ruff(默认规则 + I 排序) 与 mypy(strict 可选，至少 basic) 检查。
3. 先写核心库与安全模块，再写 CLI，最后写 Textual TUI；每一层都要有对应 pytest 测试。
4. 网络相关（MusicBrainz）测试必须 monkeypatch，禁止真实联网；默认关闭联网补全。
5. 测试覆盖率目标 >= 85%（核心模块 safety/plan/executor/journal 必须 >= 95%）。
6. 全程 dry-run 优先：任何涉及真实文件系统写入的命令，默认只输出计划。
7. 每完成一个模块，运行 pytest 与 ruff，全绿后再进入下一个模块。
8. 最后生成 README.md、LICENSE(MIT)、.gitignore、CHANGELOG.md、CONTRIBUTING.md、
   environment.yml、GitHub Actions workflow，并在项目根目录初始化 git 仓库，
   产出符合 Conventional Commits 规范的提交。
9. LICENSE 必须是 MIT，著作权行写 Copyright (c) 2026 tochikoma777；
   mutagen 必须是可选依赖 + 运行时动态 import（见 SPEC 16.2 节）。
10. 禁止在未执行 git status / git diff 检查的情况下执行 commit 与 push；
    push 前必须向我展示将要提交的文件清单并等待我确认。
11. 项目根目录下已存在 docs/SPEC.md 与 docs/PROMPT.md，这两个文件不要修改、不要删除、
    也不要挪动位置；它们是给你的契约文档，保留在 docs/ 下即可。

执行顺序：按 SPEC 第 19 节的里程碑 M1 → M6 依次推进。
本轮只做【M1 骨架】，完成后停下，向我汇报产出文件清单与 pytest / ruff / mypy 的结果，
等我确认后再进入 M2。
```

---

## 2. 产品需求

### 2.1 目标用户与痛点

目标用户是**对命名一致性有强迫倾向的人**：同一批文件命名风格必须统一、排序必须整齐、字段顺序必须符合自己的审美，且极度害怕"一键操作把库搞乱且无法恢复"。

核心痛点：

| 痛点 | 对应设计 |
|---|---|
| 命名风格五花八门（`歌手-歌名` / `歌名 - 歌手 [320K]` / `01.歌名`） | 多模式文件名解析 + 内嵌标签优先 |
| 字段缺失（没有专辑、没有国别） | Provider 链降级 + MusicBrainz 可选补全 + 缺省值 |
| 怕误伤系统文件 / 怕误改其他目录 | 路径黑名单 + 扩展名白名单 + 五道安全闸门 |
| 怕批量操作不可逆 | 事务日志 + 一键回退 + 两阶段重命名 |
| 想要"我的格式我做主" | 模板引擎 + 预设持久化 |
| 一次性整理上千个文件，逐个改不现实 | 目录树批量勾选 + 实时预览 + 冲突自动消解 |

### 2.2 V1 功能范围（必须完成）

- **F1 扫描**：指定目录（可递归、可设扩展名白名单），收集候选文件。
- **F2 元数据提取**：内嵌标签 → 文件名解析 → （可选）MusicBrainz → 默认值兜底，带来源与置信度。
- **F3 模板渲染**：默认 `{title} - {artist} - {album} - {country}`，用户可自定义模板并实时预览。
- **F4 安全闸门**：危险路径拦截、类型白名单、冲突检测、Windows/WSL 文件名合规化、路径长度校验。
- **F5 预览与确认**：TUI 表格逐条列出「原文件名 → 新文件名」，可勾选、可编辑字段、可跳过。
- **F6 批量执行**：两阶段重命名（临时名中转），逐条记录事务日志，失败不中断、不静默。
- **F7 修改清单输出**：终端表格 + 可选的 CSV / Markdown 报告文件。
- **F8 一键回退**：`undo --session <id>` 或 `undo --last`，支持 dry-run，支持部分失败场景提示。
- **F9 预设持久化**：TOML 配置文件保存常用模板、Provider 开关、黑名单扩充。

### 2.3 V1.1 扩展（架构预留，V1 不实现业务逻辑）

**关键决策：V1 只做音频，但架构必须让"加一个新领域"的成本 < 150 行代码、且零改动核心。**

可扩展性靠三个抽象点实现（详见第 4、15 节）：

- **Profile（领域档案）**：定义"这一类文件有哪些字段、哪些扩展名、默认模板、用哪些 Provider"；
- **MetadataProvider（元数据提供者）**：插件式注册，只负责"给一个路径、填几个字段"；
- **Template / Safety / Executor**：完全领域无关，只认字段名字符串。

所以 V1.1 加 PDF 文献重命名，只需要：

1. 新增 `renomate/providers/pdf_meta.py`（读 PDF 元数据 / DOI，约 100 行）；
2. 在 `profiles.py` 里注册一个 `pdf` Profile（约 20 行配置）；
3. 加对应测试。

核心的模板引擎、安全闸门、事务执行器、TUI **一行不用改**。

> 回答你的疑问：初版聚焦歌曲是明智的——音频场景有高质量的结构化标签（mutagen）作为主力数据源，
> 而 PDF 的元数据质量差、依赖 DOI 查询，做进 V1 会拖慢主线。但"只做歌曲 ≠ 写死歌曲"，
> 本规格用 Profile + Provider 把领域知识隔离在插件层，PDF / 照片 / 视频都是后续插件。

---

## 3. 非功能需求

| 维度 | 要求 |
|---|---|
| 性能 | 1000 个文件的扫描 + 元数据提取（不开网络）< 10s；元数据读取用线程池（I/O 密集） |
| 内存 | 不把文件内容读进内存；只处理路径与元数据 |
| 正确性 | 任何情况下不产生"文件丢失"：跨设备重命名必须显式拒绝，绝不静默 copy+delete |
| 可测试性 | 所有文件系统操作通过注入的 `FileSystem` 协议抽象，便于 tmp_path 测试 |
| 可观测 | `--verbose` 输出每个文件的字段来源；跳过项必须给出**人类可读的原因** |
| 跨平台 | 命名合规化按 `platform` 参数显式区分 `windows` / `posix`，不依赖 `sys.platform` 隐式行为 |
| 离线可用 | 断网时一切功能正常，仅 MusicBrainz 补全不可用，并给出降级提示 |

---

## 4. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│  交互层   renomate/tui/  (Textual)      renomate/cli.py      │
│          预览表格 / 字段编辑 / 确认      子命令式 CLI          │
└───────────────▲──────────────────────────▲──────────────────┘
                │                          │
┌───────────────┴──────────────────────────┴──────────────────┐
│  编排层   renomate/plan.py                                   │
│    Scanner → ProviderChain → Template → SafetyGuard → Plan   │
│    Plan = list[RenameItem]  (纯数据，可序列化，可预览)         │
└───────────────▲─────────────────────────────────────────────┘
                │
┌───────────────┴─────────────────────────────────────────────┐
│  领域插件层                                                   │
│   profiles.py                providers/                      │
│    audio (V1)                 audiotag.py   (mutagen)        │
│    pdf   (V1.1 预留)           filename.py   (正则解析)       │
│                                musicbrainz.py (可选网络)      │
│                                manual.py     (用户兜底)       │
└───────────────▲─────────────────────────────────────────────┘
                │
┌───────────────┴─────────────────────────────────────────────┐
│  核心层（领域无关，绝对稳定）                                  │
│   models.py    template.py    safety.py                      │
│   executor.py  journal.py     config.py   report.py          │
└───────────────┬─────────────────────────────────────────────┘
                │
┌───────────────┴─────────────────────────────────────────────┐
│  平台层   platform/fs.py  (FileSystem 协议 + 本地实现)         │
└──────────────────────────────────────────────────────────────┘
```

**依赖方向铁律**：核心层不 import 任何 Provider；Provider 不 import TUI/CLI；TUI/CLI 只依赖 Plan 与核心层。
用 `ruff` 的 import 分层无法强制，请在测试中加一条「架构测试」：`test_architecture.py` 用 `ast` 解析 import，
断言 `renomate/core/**` 与 `renomate/models.py` 不出现 `mutagen`、`musicbrainzngs`、`textual`。

### 4.1 一次完整操作的数据流

```
scan(dir) ──► [FileCandidate] ──► ProviderChain.fill() ──► [MetadataBundle]
                                                                │
                       Template.render(bundle) ─────────────────┤
                       SafetyGuard.check(name, path) ───────────┤
                                                                ▼
                                                          [RenameItem]  ← Plan
                                                                │
                          ┌──────────── 用户确认 ───────────────┤
                          ▼                                     │
              Executor.apply(plan)  ──►  Journal(JSONL)  ──►  Report
                                                 │
                                        undo(session_id) ──► 反向 rename
```

---

## 5. 目录结构

```
renomate/
├── pyproject.toml                # hatchling 构建，ruff + pytest + mypy 配置
├── environment.yml               # conda 用户复现用（--no-builds 导出）
├── README.md                     # 徽标位 + 快速上手 + 截图位 + 安全承诺
├── LICENSE                       # MIT（见第 16.2 节的许可权衡说明）
├── CHANGELOG.md
├── CONTRIBUTING.md               # 简短：如何新增一个 Provider
├── .gitignore
├── .pre-commit-config.yaml       # ruff + ruff-format（可选但推荐）
├── .github/
│   └── workflows/ci.yml          # ruff + pytest，ubuntu + windows 双矩阵
├── src/renomate/
│   ├── __init__.py               # __version__
│   ├── models.py                 # 数据模型（第 6 节）
│   ├── config.py                 # TOML 配置加载 / 默认值 / XDG 路径
│   ├── profiles.py               # Profile 注册表（audio / 未来的 pdf）
│   ├── template.py               # 模板引擎
│   ├── safety.py                 # 文件名合规化 + 路径黑名单 + 风险判定
│   ├── scanner.py                # 目录扫描
│   ├── chain.py                  # Provider 链与字段合并策略
│   ├── plan.py                   # Plan 构建、冲突消解、状态标记
│   ├── executor.py               # 两阶段重命名执行器
│   ├── journal.py                # 事务日志（JSONL）与 undo
│   ├── report.py                 # 清单输出（Rich 表格 / CSV / Markdown）
│   ├── cli.py                    # argparse 子命令
│   ├── providers/
│   │   ├── __init__.py           # PROVIDER_REGISTRY + 协议定义
│   │   ├── audiotag.py           # mutagen
│   │   ├── filename.py           # 文件名正则解析
│   │   ├── musicbrainz.py        # 可选网络补全（默认关闭）
│   │   └── manual.py             # 用户手工值 / 缺省值
│   └── tui/
│       ├── __init__.py
│       ├── app.py                # RenomateApp
│       ├── screens.py            # 确认屏 / 字段编辑屏 / 回退屏
│       ├── widgets.py            # FileTree / DiffTable / TemplateBar
│       └── styles.tcss
└── tests/
    ├── conftest.py               # 生成带 tag 的音频 fixture、tmp 音乐库
    ├── test_safety.py
    ├── test_template.py
    ├── test_chain.py
    ├── test_audiotag.py
    ├── test_filename.py
    ├── test_musicbrainz.py       # monkeypatch，禁真网络
    ├── test_plan.py
    ├── test_executor.py
    ├── test_journal_undo.py
    ├── test_cli.py
    ├── test_config.py
    └── test_architecture.py
```

---

## 6. 数据模型（`models.py`）

```python
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

    def counts(self) -> dict[ItemStatus, int]: ...  # 供 TUI 底栏与报告使用
    def to_dict(self) -> dict: ...  # 供 JSON 导出
```

### 6.1 事务日志格式（`journal.py`，JSONL 每行一个对象）

```json
{"session_id":"20261007-153012-8f3a","ts":"2026-10-07T15:30:12.345+08:00","seq":1,
 "op":"rename","src":"/mnt/d/Music/a.mp3","dst":"/mnt/d/Music/稻香 - 周杰伦 - 魔杰座 - CN.mp3",
 "status":"applied","src_size":8388608,"src_mtime_ns":1759824612123456789,
 "dst_size":8388608,"dst_mtime_ns":1759824612123456789,"error":null,
 "template":"{title} - {artist} - {album} - {country}","profile":"audio"}
```

设计要点：

- **每行独立追加（append-only）**，写完立即 `flush()` + `os.fsync()`，保证断电/崩溃后日志不丢；
- 记录 `size` + `mtime_ns` 作为**回退可行性校验**依据（回退前比对，不一致则标 `needs_manual`）；
- `status` 取值：`applied` / `failed` / `skipped` / `undone`；
- 日志路径：Linux/WSL 用 `$XDG_DATA_HOME/renomate/journals/`（默认 `~/.local/share/renomate/journals/`），
  保留最近 50 个 session，超过自动清理（可在配置中关闭）。

---

## 7. 元数据获取：Provider 链

### 7.1 协议定义

```python
class MetadataProvider(Protocol):
    name: str

    def supports(self, profile: str, path: Path) -> bool: ...
    def fill(self, bundle: MetadataBundle, cfg: Config) -> None: ...

    # 只填 bundle.fields 中为 None 且未被 locked 的字段；写值时附带 source / confidence
```

Provider 链顺序（`chain.py` 硬编码优先级，允许配置文件调整）：

| 顺序 | Provider | 说明 | 典型置信度 |
|---|---|---|---|
| 0 | `manual` | 用户在 TUI/CLI 手工指定的值，`locked=True` | 1.0 |
| 1 | `audiotag` | mutagen 读内嵌标签 | title/artist/album 0.95，country 0.7 |
| 2 | `path` | 父目录名推断（如 `周杰伦/魔杰座/01.mp3`） | 0.6 |
| 3 | `filename` | 文件名正则解析 | 0.5 ~ 0.8（按命中的模式） |
| 4 | `network` | MusicBrainz（默认关闭，需显式开启） | 0.85（仅补专辑/国别/年份） |
| 5 | `default` | 配置文件缺省值 | 0.1 |

**合并策略**：按上表顺序依次执行，先到先得；只有「当前字段为空且未锁定」才被覆盖。
最终把 `confidence < 0.6` 的字段在 TUI 里标黄（低置信），缺失字段标红并允许手工填。

### 7.2 `audiotag.py` —— mutagen 字段映射

用 `mutagen.File(path, easy=True)` 拿到统一 key；`country` 这类非标准字段需要回退到 raw tag：

| 统一字段 | MP3/ID3 (easy) | FLAC/OGG (Vorbis) | MP4/M4A (easy) | 非标准回退 |
|---|---|---|---|---|
| `title` | `title` (TIT2) | `title` | `©nam` | — |
| `artist` | `artist` (TPE1) | `artist` | `©ART` | `albumartist` 兜底 |
| `album` | `album` (TALB) | `album` | `©alb` | — |
| `year` | `date` (TDRC) | `date` | `©day` | 取前 4 位数字 |
| `track` | `tracknumber` (TRCK) | `tracknumber` | `trkn` | 文件名前导数字 |
| `genre` | `genre` (TCON) | `genre` | `©gen` | — |
| `country` | — | — | — | ① `TXXX:COUNTRY` / `COUNTRY` / `----:com.apple.iTunes:COUNTRY`；② `LANGUAGE` 推断；③ 走 network 或 default |

实现注意：

- `mutagen.File()` 对不支持的格式返回 `None`，对损坏文件抛异常——**必须 try/except 并记录原因**，不能让单个坏文件中断整批扫描；
- 多值标签（`artist` 可能是 `["A", "B"]`）→ 用配置里的 `multi_value_join`（默认 `"、"`）连接；
- 用 `ThreadPoolExecutor(max_workers=8)` 并发提取，注意 mutagen 是纯 Python，GIL 下 I/O 等待仍能提速。

### 7.3 `filename.py` —— 文件名解析模式

按优先级依次尝试，第一个完整命中（title+artist）即停；部分命中也保留已解析字段。

```python
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
```

解析前的**噪声清洗**（顺序固定）：

1. 去掉扩展名；
2. 去掉站点水印：`[www.xxx.com]`、`【xxx资源】`、`- 某某音乐`；
3. 去掉音质/格式标记：`[320K]`、`[FLAC]`、`[HQ]`、`(128kbps)`；
4. 去掉序号残留（若已被 `track` 捕获）；
5. 全角空格 `\u3000` → 半角；连续空白压缩为一个空格；首尾 strip。

**不要把 `(Live)`、`(Remix)`、`feat. xxx` 一律删掉**——它们是标题的一部分，放入 `extra` 字段，
默认模板不使用，但用户可在模板里用 `{extra}` 引用。

### 7.4 `musicbrainz.py` —— 可选网络补全

默认 **关闭**。启用条件：配置 `musicbrainz.enabled = true` 且命令行未传 `--no-net`。

硬性合规要求（MusicBrainz 服务条款）：

```python
musicbrainzngs.set_useragent("renomate", __version__, cfg.musicbrainz.contact)  # 必须，含联系方式
musicbrainzngs.set_rate_limit(limit_or_interval=1.0, new_requests=1)  # 1 req/s
```

工程要求：

- 只用于**补全缺失字段**（主要是 `album` / `country` / `year`），已有高置信度值不覆盖；
- 本地缓存：`$XDG_CACHE_HOME/renomate/mb/<sha1(query)>.json`，TTL 默认 30 天，命中缓存不发请求；
- 网络异常（超时 / 503）→ 降级为"该字段缺失"，**绝不中断整批流程**，仅在报告里统计 `network_failed` 数量；
- 所有网络调用走 `asyncio.to_thread()` 或线程池，不阻塞 TUI；
- 提供 `--no-net` 与配置开关双保险；测试必须 monkeypatch `musicbrainzngs`。

`country` 归一化：MusicBrainz 返回 ISO 3166-1 alpha-2（如 `CN`/`US`/`JP`）。维护一张
`country_aliases.toml`（内置在包内）做中文/常见写法映射：`中国/内地/国语 → CN`、`香港/粤语 → HK`、
`台湾 → TW`、`日本/日语 → JP`、`韩国 → KR`、`欧美 → EU`… 模板里可用 `{country}`（代码）或
`{country_zh}`（中文）。

---

## 8. 模板引擎（`template.py`）

### 8.1 语法

```
{field}                     取字段值
{field|filter}              单个过滤器
{field|filter:arg}          带参过滤器
{field|default:XX}          字段为空时的缺省值（也可用于整个占位符）
{track|pad:2}               数字补零 → 01
{title|upper} / {lower} / {title_case}
{artist|strip_feat}         去掉 feat. / ft. 部分
{album?[...]}               条件段：album 非空才输出方括号及内容（V1 可选实现）
```

分隔符是模板里的字面量（`-`、`_`、` `），不属于语法。

### 8.2 渲染流程（顺序不可变）

1. **取值**：字段缺失 → 用 `default`，仍为空 → 记入 `missing_fields`；
2. **应用过滤器**（按 `|` 从左到右）；
3. **合规化**：对**每个字段值单独**做 `sanitize`（见第 9 节），而不是对拼接后的整串做，避免误伤分隔符；
4. **拼接**；
5. **空折叠**：把连续重复的 `[separator]` 压成一个，去掉首尾分隔符（避免出现 `稻香 -  - 周杰伦`）；
6. **长度截断**：按字符数截断到 `max_name_length`（默认 180，**含扩展名预留**），截断处加 `…` 可配置；
7. **补扩展名**：保留原始扩展名（小写化可配置）。

默认模板（音频 Profile）：

```
{title} - {artist} - {album} - {country}
```

进阶示例（用户可存为预设 `sorted`）：

```
{track|pad:2}. {title} - {artist}
{album|default:未知专辑}/{track|pad:2} - {title} - {artist}
```

### 8.3 接口

```python
def render(
    template: str,
    bundle: MetadataBundle,
    *,
    platform: str = "windows",
    max_length: int = 180,
    sanitize_cfg: SanitizeConfig,
) -> RenderResult: ...


@dataclass
class RenderResult:
    name: str  # 不含扩展名
    missing: list[str]  # 缺失字段名
    truncated: bool
    replaced: dict[str, str]  # 被替换掉的非法字符 → 替换成什么（供报告展示）
```

---

## 9. Windows / WSL 文件名合规化（`safety.py`）

这是在 WSL 里操作 `/mnt/d/...` 时**最容易翻车**的地方，必须严格实现并测试。

### 9.1 规则表

| 类别 | 规则 | 处理 |
|---|---|---|
| 非法字符 | `< > : " / \ \| ? *` | 按 `illegal_char_map` 替换（默认：`/`→`／`、`\`→`＼`、`:`→`：`、`?`→`？`、`*`→`＊`、`"`→`＂`、`<`→`＜`、`>`→`＞`、`\|`→`｜`）；可配置 `strategy = replace \| drop \| underscore` |
| 控制字符 | ASCII 0–31 | 一律删除 |
| 保留设备名 | `CON PRN AUX NUL COM0–COM9 LPT0–LPT9`（**不区分大小写，带扩展名同样非法**，如 `CON.txt`） | 名前加 `_`（→ `_CON`） |
| 末尾字符 | 以空格或 `.` 结尾 | 删除末尾的空格与点（Windows 会静默吞掉，导致回退时路径对不上！） |
| 开头字符 | 以空格开头 | strip |
| 文件名长度 | ≤ 255 字符 | 模板阶段截到 180，此处兜底再校验 |
| 完整路径 | Windows 传统 `MAX_PATH = 260` | 超长 → `BLOCKED`，原因 `path_too_long`，建议用户缩短目录层级 |
| 空文件名 | 渲染结果为空 | `BLOCKED`，原因 `empty_name` |
| Unicode | 中文 / emoji / NFC-NFD | 统一 `unicodedata.normalize("NFC", name)`；emoji 允许（NTFS 用 UTF-16，安全） |

### 9.2 参考实现（必须原样落地）

```python
import re
import unicodedata
from pathlib import PurePath, PureWindowsPath

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
```

---

## 10. 风险控制：五道安全闸门

**设计原则：默认拒绝（deny by default）。任何一条不通过，该文件就是 `BLOCKED` 或 `SKIPPED`，
绝不带病执行；每条拦截必须给出人类可读原因。**

### 闸门 1 —— 路径黑名单（进入目录时立即生效）

```python
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
```

附加规则：

- **盘符/挂载根目录本身**（`/`、`/mnt/c`、`/home`、`C:\`）直接拒绝——批量重命名根目录是灾难；
- WSL 的 `/mnt/c/Users/<name>/` 下的**非用户目录**（`AppData`、`Documents` 之外仍需谨慎）→ 若目标目录的
  文件数量 > `max_batch`（默认 5000）强制二次确认；
- 比对统一用 `resolve()` + `casefold()`，覆盖 Windows 大小写不敏感；
- 用户可在配置 `safety.extra_blocked` 追加，`safety.allow_override`（危险开关）需显式 `--i-know-what-im-doing` 才生效，且日志留痕。

### 闸门 2 —— 类型与属性白名单

- 扩展名必须在当前 Profile 的 `extensions` 白名单内（音频默认 `mp3 flac m4a ogg opus wav ape wma aiff aif`）；
- **跳过符号链接**（`follow_symlinks = false`），原因 `is_symlink`；
- **跳过隐藏文件**（以 `.` 开头，或 Windows `FILE_ATTRIBUTE_HIDDEN`），原因 `is_hidden`；
- 跳过**不可写/只读**文件（预检 `os.access(path, W_OK)`），原因 `not_writable`；
- 跳过**非常规文件**（目录、设备、socket、fifo）；
- 保留工具的**自身产物**：以 `.renomate_tmp_` 开头的临时文件、journal 目录，永不处理。

### 闸门 3 —— 计划体检（`plan.py`）

对每个 `RenameItem` 做：

| 检查 | 处理 |
|---|---|
| `src == dst` | `SKIPPED`，原因 `no_op` |
| 目标已存在（且不是自身） | 自动加后缀 ` (2)`、` (3)`… 直到不冲突；记录 `conflict_resolved_by` |
| **大小写不敏感冲突**（Windows/`/mnt/c`） | 同上，用 `casefold()` 比较；并用 **临时名两步法** 执行（见 11.2） |
| 批次内互相冲突（两个文件抢同一个目标名） | 后者自动加后缀；若加后缀仍冲突 → `CONFLICT` 并提示 |
| 目标路径跨越文件系统/设备 | `BLOCKED`，原因 `cross_device`（`os.rename` 会 `EXDEV`，绝不静默 copy） |
| 渲染结果含被替换字符 / 被截断 | 放行，但在报告中列出（让用户知道名字被改过形） |
| 缺失关键字段（如 title 为空） | 默认 `BLOCKED`（配置 `allow_missing_fields` 可放宽为警告），原因 `missing:title` |
| 路径总长度 > 260（Windows） | `BLOCKED`，原因 `path_too_long` |

### 闸门 4 —— 执行前确认（人机闸门）

- **CLI**：`apply` 默认先打印计划表，要求输入 `yes` 或精确文件数（如 `120`）才执行；`--dry-run` 是默认行为；
- **TUI**：确认屏显示「将修改 N 个文件 / 跳过 M 个 / 拦截 K 个」，按钮必须**先聚焦"取消"**，
  且需手动输入 `APPLY` 四字（防手滑回车）；
- 批量 > `max_batch` 时额外警告；
- 任何 `BLOCKED` 项在确认屏单独列出并高亮。

### 闸门 5 —— 执行期防御（`executor.py`）

- 每个文件 rename 前**重新 stat 一次**，确认 src 仍存在且大小/mtime 未变（防止扫描后被外部改动）；
- 捕获 `PermissionError`（Windows 上文件被播放器占用最常见）→ 记 `failed`，**继续处理下一个**，不中断；
- 捕获所有 `OSError`，记录 `errno`；
- 执行中途崩溃（Ctrl+C / 断电）→ 日志已 `fsync`，下次启动 `renomate journals --incomplete` 可发现未完成 session 并提示回退；
- **绝不删除任何文件**：只有 `os.rename`，没有 `os.remove`。

---

## 11. 执行与回退

### 11.1 两阶段重命名（防链式覆盖）

问题场景：`A.mp3 → B.mp3` 且 `B.mp3 → C.mp3`。直接顺序 rename 会导致覆盖丢失。

解法：**阶段一**先把本批次所有待改文件重命名为唯一临时名，
**阶段二**再从临时名改为最终名。

```python
tmp = src.with_name(f".renomate_tmp_{session_id}_{seq}{src.suffix}")
```

- 临时名以 `.` 开头（隐藏），且在扫描白名单中被显式排除；
- 任一文件的阶段一失败 → 该文件记 `failed` 并保持原名，不进入阶段二；
- 阶段二失败 → 尝试回退到原名（从临时名改回），失败则记 `needs_manual` 并在报告高亮；
- 执行结束后若仍有 `.renomate_tmp_` 残留，报告必须报警并提供 `renomate recover` 命令。

### 11.2 Windows / `/mnt/c` 大小写不敏感陷阱

在 `/mnt/c` 下 `os.rename("A.MP3", "a.mp3")` 会被视为同一文件而失败（WSL 继承 Windows 大小写不敏感语义）。

必须实现：

```python
def _rename_case_safe(src: Path, dst: Path) -> None:
    """目标与源仅大小写不同时，先改到临时名再改到目标。"""
    if src.exists() and dst.exists() and src.resolve() == dst.resolve():
        # 同一文件：直接 os.rename 即可
        os.rename(src, dst)
        return
    if _same_ignoring_case(src, dst):
        tmp = src.with_name(f".renomate_case_{uuid.uuid4().hex}{src.suffix}")
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        os.rename(src, dst)
```

检测"是否大小写敏感文件系统"：在目标目录创建两个仅大小写不同的临时文件探测（结果缓存到配置），
或按路径前缀判断（`/mnt/[a-z]` → 视为 Windows → 大小写不敏感）。

### 11.3 回退（`journal.py`）

```bash
renomate undo --last                 # 回退最近一次 session
renomate undo --session 20261007-153012-8f3a
renomate undo --last --dry-run       # 只看会做什么
renomate journals                    # 列出所有 session（时间、文件数、状态）
```

回退流程：

1. 读取 session 日志，**逆序**处理 `status == applied` 的条目；
2. 校验：`dst` 当前存在，且 `size`/`mtime_ns` 与日志一致 → 可安全回退；
   不一致 → 标 `needs_manual`，跳过并汇总告知用户（绝不基于猜测乱改名）；
3. 执行反向 rename（同样走大小写安全逻辑），逐条写回日志 `status = undone`；
4. 输出回退清单（成功 / 跳过 / 需人工）；
5. 支持 `--only <pattern>` 部分回退（按文件名 glob 过滤）。

TUI 中绑定 `u` 键打开回退屏，列出可回退 session，选中后实时预览反向 diff。

---

## 12. TUI 设计（Textual）

### 12.1 主界面线框

```
┌─ renomate v0.1.0 ── profile: audio ── /mnt/d/Music ─────────────────────────┐
│ 模板 [{title} - {artist} - {album} - {country}        ] [预设▾] [预览]       │
│ 范围 [✓递归 深度:∞] [扩展名 mp3,flac,m4a ▾] [✓联网补全] 命中 128 个          │
├──────────────────────┬─────────────────────────────────────────────────────┤
│ 目录树                │  ✓  原文件名 → 新文件名                    状态      │
│ ▼ ♪ Music            │ ─────────────────────────────────────────────────── │
│   ▼ 周杰伦            │  ✓  01 稻香.mp3                                     │
│     ▼ 魔杰座          │     → 稻香 - 周杰伦 - 魔杰座 - CN.mp3        ●tag   │
│       ▣ 01 稻香.mp3   │  ✓  晴天[320K].mp3                                  │
│       ▣ 晴天[320K].mp3│     → 晴天 - 周杰伦 - 叶惠美 - CN.mp3        ●tag   │
│   ▼ 陈奕迅            │  ☐  unknown.mp3                                     │
│                       │     → 未知标题 - 未知歌手 - 未知专辑 - CN     ▲缺失 │
├──────────────────────┴─────────────────────────────────────────────────────┤
│ 共 128 │ 待改 120 │ 跳过 5 │ 拦截 3 │ 冲突 0      [Enter 预览] [u 回退]     │
└────────────────────────────────────────────────────────────────────────────┘
```

### 12.2 颜色语义（强迫症友好）

| 标记 | 含义 |
|---|---|
| `●` 绿 | 字段全部来自内嵌标签，置信度高 |
| `●` 蓝 | 含网络补全字段 |
| `▲` 黄 | 有字段来自文件名推断（低置信），建议核对 |
| `▲` 红 | 有字段缺失，需手工补充或被拦截 |
| 删除线灰 | 被用户取消勾选 / 跳过 |

### 12.3 键位

| 键 | 动作 |
|---|---|
| `↑↓` / `jk` | 移动光标 |
| `Space` | 勾选 / 取消当前项 |
| `a` | 全选 / 全不选切换 |
| `e` | 打开字段编辑屏（编辑当前文件的 title/artist/album/country，编辑后 `locked=True`） |
| `t` | 聚焦模板输入框（**改模板即时全表重算预览**） |
| `p` | 切换 Profile / 预设 |
| `/` | 搜索过滤文件名 |
| `Enter` | 生成/刷新预览 |
| `Ctrl+Enter` | 打开确认屏 |
| `u` | 打开回退屏 |
| `g` | 生成报告文件（CSV/Markdown） |
| `?` | 帮助浮层 |
| `q` | 退出 |

### 12.4 确认屏（不可省略）

```
┌─ 确认执行 ────────────────────────────────────────────┐
│ 目录：/mnt/d/Music/周杰伦/魔杰座                        │
│ 将修改 120 个文件，跳过 5 个，拦截 3 个                 │
│ ⚠ 拦截项：                                             │
│   · system.ini        (扩展名不在白名单)                │
│   · CON.mp3           (Windows 保留名)                  │
│   查看全部 ▸                                           │
│ 请输入 APPLY 以确认：[____]                            │
│              [ 取消(默认聚焦) ]  [ 确认执行 ]           │
└────────────────────────────────────────────────────────┘
```

---

## 13. CLI 设计

```bash
renomate scan    DIR [--profile audio] [--recursive/--depth N] [--ext mp3,flac] [--out plan.json]
renomate preview DIR [--template "..."] [--preset NAME] [--no-net] [--json]
renomate apply   --plan plan.json | DIR  [--yes] [--dry-run]        # 默认 dry-run
renomate undo    [--session ID | --last] [--dry-run] [--only GLOB]
renomate journals [--incomplete]
renomate recover [--session ID]        # 清理残留的 .renomate_tmp_*
renomate report  --session ID [--format table|csv|markdown] [--out FILE]
renomate config  [--init] [--show] [--set key=value]
renomate profiles                       # 列出可用 Profile 与字段定义
```

全局参数：`--verbose/-v`、`--quiet`、`--config PATH`、`--no-net`、`--i-know-what-im-doing`（唯一绕过黑名单的方式，会写日志）。

**退出码约定**：`0` 成功；`1` 部分失败；`2` 全部失败/被拦截；`3` 参数或配置错误；`130` 用户中断。

---

## 14. 配置与预设持久化

路径：`$XDG_CONFIG_HOME/renomate/config.toml`（WSL 默认 `~/.config/renomate/config.toml`）。

```toml
[general]
default_profile = "audio"
max_batch = 5000
follow_symlinks = false
include_hidden = false

[profiles.audio]
extensions = ["mp3", "flac", "m4a", "ogg", "opus", "wav", "ape", "wma", "aiff"]
fields = ["title", "artist", "album", "country", "year", "track", "genre", "extra"]
required_fields = ["title", "artist"]
default_template = "{title} - {artist} - {album} - {country}"
providers = ["audiotag", "path", "filename", "network", "default"]

[presets]                       # 用户保存的命名预设，TUI 下拉可选
simple    = "{title} - {artist}"
sorted    = "{track|pad:2}. {title} - {artist}"
by_album  = "{album|default:未知专辑}/{track|pad:2} - {title} - {artist}"
with_year = "{title} - {artist} ({year}) - {country_zh}"

[sanitize]
platform = "windows"            # windows | posix
strategy = "replace"            # replace | drop | underscore
max_name_length = 180
truncate_suffix = "…"
lowercase_extension = false
replacement_map = { "/" = "／", ":" = "：" }

[safety]
extra_blocked = []
allow_missing_fields = false
require_confirm = true
confirm_token = "APPLY"

[musicbrainz]
enabled = false                 # 默认关闭
contact = "you@example.com"     # 必须，作为 User-Agent 一部分
rate_limit = 1.0
cache_ttl_days = 30
timeout_seconds = 8
```

`renomate config --init` 生成带注释的默认配置；读不到配置时用内置默认值，**不报错**。

---

## 15. 可扩展性：如何加一个新领域（以 PDF 为例）

V1 只需保证下面三步可行，**不需要真的实现 PDF**。请把这三条作为架构验收标准写进 `CONTRIBUTING.md`：

### 步骤 1：写 Provider（约 100 行）

```python
# src/renomate/providers/pdf_meta.py
class PdfMetaProvider:
    name = "pdfmeta"

    def supports(self, profile, path):
        return profile == "pdf" and path.suffix.lower() == ".pdf"

    def fill(self, bundle, cfg):
        # pypdf 读 /Title /Author /CreationDate；正文首行提取 DOI → Crossref 补全
        bundle.set("title", FieldValue(t, FieldSource.PDFMETA, 0.8))
        bundle.set("author", FieldValue(a, FieldSource.PDFMETA, 0.85))
        bundle.set("year", FieldValue(y, FieldSource.PDFMETA, 0.8))
        bundle.set("venue", FieldValue(v, FieldSource.NETWORK, 0.7))  # Crossref，可选
```

字段设计（PDF Profile）：`title / author / year / venue(会议或期刊) / doi / publisher`，
默认模板：`{year} - {title} - {author} - {venue}`。

### 步骤 2：注册 Profile（约 20 行配置，零代码）

```python
PROFILES["pdf"] = Profile(
    name="pdf",
    extensions=["pdf"],
    fields=["title", "author", "year", "venue", "doi", "publisher"],
    required_fields=["title", "author"],
    default_template="{year} - {title} - {author} - {venue}",
    providers=["pdfmeta", "filename", "default"],
)
```

### 步骤 3：加测试

新增 `tests/test_pdf_provider.py`，其余（模板、安全、执行、回退、TUI）**复用现有测试与代码**。

> 同理，照片（EXIF：`datetime / camera / lens / location`）、视频（mediainfo）、
> 代码文件（按 git 信息）都可以用同样的方式接入。

---

## 16. 依赖、环境与许可

### 16.1 依赖清单

| 依赖 | 版本 | 用途 | 备注 |
|---|---|---|---|
| Python | ≥ 3.11 | 运行时 | 用 `Path.walk()`（3.12+）或自行递归以兼容 3.11 |
| `textual` | 最新稳定版 | TUI | 与 `rich` 一并引入；DataTable / Tree / Input / Select 均内置 |
| `rich` | 随 textual | 表格与日志渲染 | |
| `mutagen` | ≥ 1.47 | 音频标签 | **GPL-2.0-or-later**，见下 |
| `musicbrainzngs` | ≥ 0.7 | 网络补全 | 可选依赖 |
| `tomli` / `tomllib` | — | 配置读取 | 3.11+ 用标准库 `tomllib` |
| `pypdf` | — | **V1.1 才引入**，不要放进 V1 依赖 | |
| 开发依赖 | `pytest`、`pytest-cov`、`ruff`、`mypy`、`pytest-asyncio`（若测 TUI） | | |

### 16.2 许可权衡（重要，请务必注意）

`mutagen` 是 **GPL-2.0-or-later**。这是一个真实的合规点，两种处理方案：

### ✅ 已决策：本项目采用 **MIT**（方案 A）

执行要求（Agent 必须照做）：

1. `LICENSE` 文件用 **MIT License**，著作权行写 `Copyright (c) 2026 tochikoma777`；
2. `pyproject.toml` 中 `license = {text = "MIT"}`，并声明 `classifiers = ["License :: OSI Approved :: MIT License"]`；
3. `mutagen` 必须是**可选依赖**：`[project.optional-dependencies] audio = ["mutagen", "musicbrainzngs"]`，
   核心包**动态 import**（`importlib.import_module`），缺失时降级为「文件名解析模式」并给出清晰提示，
   不得因此崩溃；
4. README 的 License 小节必须保留这段声明：

   > Renomate is MIT-licensed. The **optional** audio-tag provider depends on
   > [mutagen](https://github.com/quodlibet/mutagen) (GPL-2.0-or-later), which is installed only
   > with the `audio` extra (`pip install renomate[audio]`) and imported lazily at runtime.
   > The core (template engine, safety guards, executor, journal, TUI) has no GPL dependency.

> 备选方案（未采用，留档）：整体改用 GPL-3.0-or-later 以与依赖许可完全一致。

### 16.3 环境搭建命令（WSL 内执行）

**决策：本地用 conda 独立环境 + pip 装包；CI 用 `actions/setup-python` + pip。**
两者都从同一份 `pyproject.toml` 读依赖声明，保证本地与 CI 装的包一致。

```bash
# 代码放 WSL 原生文件系统（ext4），性能与权限都更好；但要专门测 /mnt/c 场景
mkdir -p ~/projects/utilities/renomate && cd ~/projects/utilities/renomate

# 建独立环境（不要复用已有的 llm 环境：重装 llm 会带走 renomate 的依赖）
conda create -n renomate python=3.12 -y
conda activate renomate
which python      # 必须指向 .../envs/renomate/bin/python，不是 base 或 llm

pip install -e ".[dev,audio]"

# 生成 conda 复现文件（提交进仓库；--no-builds 避免 Windows 上 solve 失败）
conda env export --no-builds -n renomate | grep -v "^prefix:" > environment.yml

# 验证
renomate --version
pytest -q
```

> 注意：conda 环境里只用 `pip` 装包，**不要 `conda install` 同名包**。
> conda 不知道 pip 装了什么，后续 `conda install` 可能覆盖它，这是 conda 环境出事的经典原因。

WSL 注意事项（写进 README 的 Troubleshooting）：

1. **代码放 `~/projects/`（ext4），不要放 `/mnt/c`**：后者 I/O 慢、权限语义怪、大小写不敏感；
2. 但要**专门在 `/mnt/d/Music` 之类的挂载目录做一次真实演练**，验证大小写不敏感与 PermissionError 处理；
3. 用 **Windows Terminal** 跑 TUI（支持 TrueColor 与鼠标），默认 Ubuntu 控制台可能显示异常；
4. `git config --global core.autocrlf input`，避免 Windows/WSL 混用导致换行符污染；
5. 若 TUI 里 emoji/中文宽度错位，配置里提供 `ui.ascii_icons = true` 降级为 ASCII 图标。

---

## 17. 测试计划

### 17.1 必测清单

| 文件 | 用例要点 |
|---|---|
| `test_safety.py` | 非法字符替换/丢弃/下划线三种策略；`CON.txt` → `_CON.txt`；末尾点与空格；控制字符；NFC 归一化；空名抛错；`path_too_long`；**用 `platform="windows"` 在 Linux 上也能测** |
| `test_template.py` | 缺省值、过滤器链、`pad`、`strip_feat`、空字段折叠（`A - - B` → `A - B`）、首尾分隔符裁剪、截断 |
| `test_filename.py` | 7+ 种常见命名模式命中正确；噪声（`[320K]`、站点水印、序号）清洗；中英文混合；`feat.` 保留 |
| `test_audiotag.py` | 用 conftest 生成的带 tag 的 mp3/flac/m4a fixture；损坏文件不抛异常；多值 artist 合并；`country` 回退 TXXX |
| `test_chain.py` | Provider 优先级；`locked` 不被覆盖；低置信度不覆盖高置信度；全部缺失时走 default |
| `test_musicbrainz.py` | monkeypatch `musicbrainzngs`，**禁止真实网络**；缓存命中不发请求；超时降级不抛；`--no-net` 生效 |
| `test_plan.py` | 同名冲突自动加 `(2)`；批次内互抢；大小写不敏感冲突；`cross_device` 拦截；`no_op` 跳过 |
| `test_executor.py` | 两阶段重命名解决链式 A→B→C；中途失败不中断；PermissionError 记 failed；日志 fsync |
| `test_journal_undo.py` | apply 后 undo 完全还原；undo dry-run 不动文件；文件被外部改动后 undo 标 `needs_manual`；逆序回退正确性 |
| `test_cli.py` | 各子命令退出码；`--dry-run` 默认；缺 `--yes` 时不执行 |
| `test_config.py` | 配置文件缺失用默认值；TOML 解析；`presets` 读取 |
| `test_architecture.py` | 用 `ast` 断言核心层不 import mutagen/textual/musicbrainzngs |

### 17.2 conftest 关键 fixture

```python
@pytest.fixture
def music_lib(tmp_path) -> Path:
    """生成一个小型音乐库：含带标签文件、无标签文件、坏文件、非音频文件、隐藏文件、符号链接。"""
```

生成带标签的音频文件：`mutagen.File(path, easy=True)` 后直接赋值 `save()`；
`flac` 可用 `mutagen.flac.FLAC()`，缺失真实音频数据时用最小合法头 + tag 亦可（mutagen 只关心 tag 区）。
坏文件：写一个内容为 `b"not an audio file"` 的 `.mp3`。

### 17.3 CI（`.github/workflows/ci.yml`）

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      # CI 用官方 setup-python + pip（比 setup-miniconda 快得多）；
      # 依赖源与本地 conda 环境完全相同，都来自 pyproject.toml
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev,audio]"
      - run: ruff check .
      - run: ruff format --check .
      - run: pytest -q --cov=renomate --cov-report=xml --cov-fail-under=85
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev,audio]" mypy
      - run: mypy src/renomate
```

Windows job 能真实覆盖 Windows 命名规则与保留名行为，是本项目的关键价值，**不要删**。

---

## 18. GitHub 上传流程

### 18.1 `.gitignore`（要点）

```
__pycache__/  *.py[cod]  .venv/  dist/  build/  *.egg-info/
.pytest_cache/  .ruff_cache/  .coverage  coverage.xml  htmlcov/
.idea/  .vscode/  *.swp  .DS_Store
# 不要把用户的音乐库或日志提交上来
journals/  *.log  plan.json
```

### 18.2 初始化与推送（WSL 内）

```bash
cd ~/projects/utilities/renomate

git init -b main
git config --global core.autocrlf input     # 只此一次，防 Windows/WSL 换行符污染
git config user.name  "tochikoma777"
git config user.email "brother.777.lei@gmail.com"

# 方式一：gh CLI（推荐）
gh auth login            # 浏览器完成授权
gh repo create renomate --public --source=. --remote=origin --push \
  --description "Safe, preview-first batch file renamer with one-click undo (TUI + CLI)"

# 方式二：手动
# 在 GitHub 网页新建空仓库后：
git remote add origin git@github.com:tochikoma777/renomate.git
git branch -M main
git add -A
git commit -m "feat: initial release of renomate (scan/preview/apply/undo + TUI)"
git push -u origin main
```

> 若 `renomate` 这个名字在 GitHub 上已被占用，`gh repo create` 会直接报错，
> 换一个名字即可（只影响仓库名与包名，规格正文无需改动）。
> 备选：`renomate-cli`、`tidyname`、`renamex`。

SSH key（WSL 内）：

```bash
ssh-keygen -t ed25519 -C "brother.777.lei@gmail.com"
cat ~/.ssh/id_ed25519.pub    # 粘到 GitHub → Settings → SSH and GPG keys
ssh -T git@github.com        # 看到 "Hi tochikoma777!" 即成功
```

### 18.3 README 必备结构

1. 标题 + 一句话 + 徽标位（CI / Python 版本 / License / PyPI 占位）
2. **安全承诺**（四条：默认 dry-run、路径黑名单、执行前确认、一键回退）
3. 效果截图位（GIF 位，先占位）
4. 安装（`pip install "renomate[audio]"`；开发用 `conda create -n renomate python=3.12 && pip install -e ".[dev,audio]"`）
5. 30 秒上手（4 条命令示例）
6. 模板语法速查表
7. 安全机制详解（对应第 10 节）
8. 回退怎么用
9. 配置说明
10. 如何扩展新领域（Provider + Profile）
11. WSL / Windows 注意事项
12. License（含 mutagen 的 GPL 说明）
13. Roadmap（PDF / 照片 / 视频；undo 快照跨目录移动；重名智能合并）

### 18.4 提交规范

Conventional Commits：`feat:` / `fix:` / `refactor:` / `test:` / `docs:` / `chore:` / `ci:`。
建议的初始提交序列（让仓库历史好看、可回溯）：

```
chore: scaffold project with pyproject + ruff + pytest
feat(models): define MetadataBundle / RenameItem / Plan
feat(safety): windows filename sanitizer + path blocklist
feat(providers): audiotag (mutagen) + filename parser
feat(template): render with filters, sanitize and conflict folding
feat(plan): build plan with conflict resolution and guard checks
feat(executor): two-phase rename + journal + undo
feat(tui): textual preview table, confirm screen, undo screen
test: reach 85% coverage incl. windows/mnt-c cases
docs: README with safety promises and roadmap
ci: ruff + pytest matrix on ubuntu/windows
```

---

## 19. 里程碑与验收（Definition of Done）

| 里程碑 | 内容 | 产出 | DoD |
|---|---|---|---|
| **M1** 骨架 | pyproject / 目录 / models / config / scanner | 能 `scan` 出文件列表 | `ruff` + `mypy` 通过；`test_config`、`test_scanner` 绿 |
| **M2** 元数据 | safety.sanitize + audiotag + filename + chain | 能输出每个文件的字段与来源 | `test_safety`、`test_filename`、`test_audiotag`、`test_chain` 绿 |
| **M3** 计划 | template + plan + 冲突消解 + 五道闸门 | `preview` 能打印完整 diff 表与跳过原因 | `test_template`、`test_plan` 绿；黑名单用例全过 |
| **M4** 执行回退 | executor 两阶段 + journal + undo + report | `apply` 后 `undo` 能 100% 还原 | `test_executor`、`test_journal_undo` 绿；含 tmp 残留与 needs_manual 场景 |
| **M5** TUI | app / screens / widgets / tcss | 能完成"选目录→看预览→确认→应用→回退"全流程 | 手工在 `/mnt/d/Music` 演练通过；键位齐全 |
| **M6** 工程化 | 测试补齐 85%+、CI、README、LICENSE、git 初始化 | 仓库具备开源形态 | Windows CI 绿；README 无 TODO 占位；`git status` 干净 |

### 19.1 最终验收清单（Agent 完成前逐条自检）

- [ ] `pytest` 全绿，覆盖率 ≥ 85%，核心模块 ≥ 95%
- [ ] `ruff check` 与 `ruff format --check` 零告警；`mypy` 无 error
- [ ] **真实演练**：在一个复制出来的测试目录（含中文名、重名、大小写仅不同、无标签、坏文件、只读文件、子目录）跑完整流程并成功回退
- [ ] 断网状态下（`--no-net`）全部功能正常
- [ ] 对 `/mnt/c/Windows` 执行 scan 时被拦截并给出清晰原因
- [ ] TUI 在 Windows Terminal 下渲染正常，中文与图标无错位
- [ ] README / LICENSE / .gitignore / CI 齐全
- [ ] `git status` 干净，历史提交符合 Conventional Commits
- [ ] 已向我展示待提交文件清单并得到确认后，才执行 `git push`

---

## 20. 已知坑与风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| `/mnt/c` 大小写不敏感 | 仅大小写不同的重命名失败；回退时路径对不上 | 大小写安全 rename（11.2）+ 探测缓存 |
| Windows 文件被占用（播放器/索引器） | `PermissionError` 批量失败 | 逐条捕获、记 failed、报告汇总；提示"关闭播放器后重试" |
| 跨设备 rename（`EXDEV`） | 数据丢失风险 | 显式拦截 `cross_device`，禁止静默 copy+delete |
| 中文 NFD/NFC（macOS 拷贝来的文件） | 回退时文件名字符串比对失败 | 统一 NFC 归一化后再比对 |
| MusicBrainz 限流 / 封 IP | 补全失败 | 默认关闭；1 req/s；本地缓存；失败降级不中断 |
| mutagen GPL 许可 | 许可传染争议 | 可选依赖 + 动态 import + README 声明（16.2） |
| 用户自定义模板导致超长路径 | 写入失败 | 260 长度预检 + 180 字符截断 + `path_too_long` 拦截 |
| 日志丢失导致无法回退 | 不可逆 | append-only + `fsync`；`journals --incomplete` 巡检 |
| TUI 在部分终端渲染错乱 | 体验差 | 提供 `--no-tui` 纯 CLI 路径与 `ui.ascii_icons` 降级开关 |
| 误对系统目录执行 | 灾难 | 黑名单 + 根目录拒绝 + `--i-know-what-im-doing` 唯一后门且留痕 |

---

## 21. 附：给强迫症用户的默认体验约定

1. **默认模板**必须是四段式 `{title} - {artist} - {album} - {country}`，且默认排序稳定（同名字段顺序一致）；
2. 预览表默认按**目标名排序**展示（方便一眼看出是否整齐），可切换为按原路径排序；
3. 任何"被工具静默改动"的地方（字符替换、截断、加 `(2)`）都必须在报告里**显式列出**，
   绝不默默把 `A/B.mp3` 变成 `A／B.mp3` 而不告诉用户；
4. 回退后**文件名必须与操作前逐字节一致**（含大小写），这一条要有专门测试。
整齐），可切换为按原路径排序；
3. 任何"被工具静默改动"的地方（字符替换、截断、加 `(2)`）都必须在报告里**显式列出**，
   绝不默默把 `A/B.mp3` 变成 `A／B.mp3` 而不告诉用户；
4. 回退后**文件名必须与操作前逐字节一致**（含大小写），这一条要有专门测试。
