# 进度与状态（process.md）

> 本文件记录 renomate 项目的**当前进度、状态、关键决策与待办**。新会话开始先读 `CODEBUDDY.md`，再读本文件。
> 契约细节仍在 `docs/SPEC.md`（只读）。本文件可随进展自由更新。

更新时间：2026-10-10

---

## 1. 里程碑状态

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 | 骨架：pyproject / models / config / scanner / platform / profiles | ✅ 完成 |
| M2 | 元数据：safety + providers(filename/audiotag/path/manual/musicbrainz/default) + chain + country | ✅ 完成 |
| M3 | 计划：template（渲染/过滤/折叠/截断）+ plan（冲突消解 + 五道闸门） | ✅ 完成 |
| M4 | 执行回退：executor（两阶段 rename + 大小写安全 + 回滚）+ journal（JSONL/fsync/undo）+ report | ✅ 完成 |
| M5 | 交互：CLI（scan/preview/apply/undo/journals/recover/report/config/profiles）+ Textual TUI | ✅ 完成 |
| M6 | 工程化：README / LICENSE(MIT) / .gitignore / CHANGELOG / CONTRIBUTING / environment.yml / CI / pre-commit / git init / 提交 | ✅ 完成 |

## 2. 质量门禁

| 项目 | 结果 |
|------|------|
| pytest | 全绿（13 个测试文件，含 TUI `run_test` 冒烟与端到端演练） |
| 覆盖率 | **93%** 总体；safety 100% / plan 99% / executor 99% / journal 98%（核心 ≥95% 达标） |
| ruff check + format --check | 零告警 |
| mypy --strict | 26 个源文件无 error |
| 构建 | `python -m build --wheel` 成功，`.tcss` / `data/country_aliases.toml` / `py.typed` 均入包 |
| 真实端到端演练 | `apply` → 改名、`unknown.mp3` 因 `missing:artist` 拦下、`CON.mp3`→`_CON`、`cover.jpg` 跳过；`undo` 后文件名**逐字节还原** |

## 3. 已落地但未在 SPEC 中明确、属于判断取舍的决策（ADR 速记）

> 这些偏离 SPEC 的判断散落在 commit body / README / 记忆里，集中列于此，便于回顾与审计。

1. **字段合并 = 先到先得 + 高置信度可升级**
   - 背景：`chain.py` 按固定顺序合并（manual > audiotag > path > filename > network > default）。
   - 决定：同字段先到者优先；但若新值置信度明显更高则升级。
   - 原因：纯「先到先得」会让 `周杰伦 - 稻香.mp3`（filename 0.8）被目录名（path 0.6）抢走歌手。
   - 影响：合并结果对 provider 顺序不敏感于"恰好谁在前"。

2. **`path` Provider 只对「嵌套」文件推断专辑/歌手**
   - 背景：扫描目录常常是 `~/Downloads`、`/tmp`，目录名并非艺术家。
   - 决定：文件位于扫描根目录时不吃目录名；只在父/祖父目录是真实专辑/艺术家层级时才采用。
   - 影响：平铺目录不会把 `Downloads` 当艺术家。

3. **缺省值能渲染，但仍记为 missing 且默认拦截**
   - 背景：SPEC 要求「缺失字段闸门」。若缺省值（`未知歌手` 等）不算缺失，该闸门永不触发。
   - 决定：缺省值让模板仍能出名字，但 `missing_detectable()` 仍把它算缺失；`allow_missing_fields=False` 时拦截。
   - 影响：用户能看到"用了兜底值"，而非被静默猜测。

4. **折叠分隔符集合包含 `.` 和 `_`**
   - 背景：`{track|pad:2}. {title}` 中 track 缺失时点号会残留成 `稻香.`。
   - 决定：字面量分隔符 = 空白 + 连字符(—–) + 点 + 下划线。
   - 影响：`01 稻香.mp3` 这类文件名解析与模板渲染都更干净。

5. **journal 阶段一分步记为 `op="stage1"`，undo 只处理 `op="rename"`**
   - 背景：两阶段 rename（src→tmp→dst）会产生中间日志。
   - 决定：阶段一写 `op="stage1"` 但 undo 时忽略；阶段二写 `op="rename"` 才是真正的反向目标。用反转键 `(dst,src)` 登记已回退，保证二次 undo 幂等。
   - 影响：日志清晰、undo 可重复执行不出错。

6. **`apply` 需 `--token APPLY`；CLI 对目录统一 `resolve()`**
   - 背景：真实写盘必须有显式确认；相对路径会让 `plan.root` 与候选文件不可比（path 推断失效）。
   - 决定：默认 dry-run；真正执行必须 `--yes --token APPLY`；进入计划前先 `directory.expanduser().resolve()`。
   - 影响：安全 + 路径推断正确。

## 4. 发布状态（已完成）

- [x] **已发布（GitHub）**：`gh repo create renomate --public --source=. --remote=origin --push` 成功。
  - 仓库地址：<https://github.com/tochikoma777/renomate>
  - 过程插曲：初次因沙箱 git(GnuTLS) 到 github.com 的 HTTPS 连接被掐断失败多次；用户切换网络后一次成功。本机无 SSH 密钥，故全程走 HTTPS。
- [x] **已上架 PyPI（v0.1.0）**：采用 **Trusted Publisher（OIDC，零密钥）** 方式发布，CI 在 GitHub Release 发布时自动上传到 pypi.org；另设 `release-test` 环境 + `workflow_dispatch` 可先发到 TestPyPI 验证链路。
  - 安装：`pip install "renomate[audio]"`；验证版同步于 TestPyPI（<https://test.pypi.org/project/renomate/>）。
  - README 安装章节已更新为「PyPI 安装（推荐）+ 源码安装（开发）」。
  - CI 测试矩阵当前仅 `ubuntu-latest`（py3.11/3.12）：部分测试含 Unix 假设（拦截 `/etc`、大小写敏感判定、路径分隔符），Windows 暂未在 CI 验证；安装仍兼容 Windows，后续可补测。
- [x] 初始发布标签 `v0.1.0`：随首次 PyPI 发布打 tag 并创建 GitHub Release 触发。

## 5. 如何本地复跑

```bash
conda activate renomate            # Python 3.12
pip install -e ".[full,dev]"       # 含 mutagen + textual + 测试依赖
pytest -q --cov=renomate --cov-report=term
ruff check . && ruff format --check . && mypy src/renomate
renomate preview <目录> --preset sorted   # dry-run
renomate apply   <目录> --preset sorted --yes --token APPLY
renomate undo --last
```

## 6. 提交历史（Conventional Commits，13 个）

`chore:`(脚手架) → `feat(models/safety/providers/template/plan/executor/cli/tui)` → `test:` → `docs:` → `ci:` → `fix(template)`。详见 `git log --oneline`。
