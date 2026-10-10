# CODEBUDDY.md — 入口指针

> 本文件是 renomate 项目的 **AI 协作入口**。人类读者请直接看 `README.md`；本文件面向 CodeBuddy（Agent）说明「从哪里读、动什么、别动什么」。

## 1. 契约文档（**只读，禁止修改、移动、删除**）

| 文件 | 作用 |
|------|------|
| `docs/SPEC.md` | 规格书，实现的唯一来源：目录结构 / 数据模型 / 接口签名 / 风险控制 / 命名 / 里程碑 M1–M6。 |
| `docs/PROMPT.md` | 用户的执行指令与硬性约束（conda+pip 禁 uv、mutagen 可选、覆盖率门槛、push 前清单确认等）。 |

> 这两份文件是契约，**不要改写、不要挪位置**。偏离它们前必须先问用户。

## 2. 进度与状态（每次会话先看这里）

- **`docs/process.md`** — 当前里程碑进度、测试/覆盖率、已落地的关键决策（偏离 SPEC 的判断）、待办与阻塞项。**新会话开始先读它**。

## 3. 跨会话记忆（自动载入）

- `~/.codebuddy/projects/home-tachikoma-projects-utilities-renomate/memory/MEMORY.md` 是索引，新会话自动进入上下文。
- 需要细节时按关键词 `grep` 该目录下的 `*.md`。
- 进度/决策的事实真相以 **`git log` + `docs/process.md` + `CHANGELOG.md`** 为准（任务列表是会话级的，新会话看不到）。

## 4. 硬约束速查（详见 `docs/PROMPT.md`）

- 依赖只在 `pyproject.toml`（含 `[project.optional-dependencies]`），**禁止 uv / uv.lock / uv 命令**。环境用 conda（py3.12），包用 `pip install -e .`。
- `mutagen` 必须是 **可选依赖 + 运行时动态 import**；MusicBrainz 默认关闭，联网测试一律 monkeypatch。
- 全程 **dry-run 优先**：真实写盘命令默认只输出计划。
- 测试覆盖率 ≥ 85%，核心模块（safety/plan/executor/journal）≥ 95%。
- **commit 前必须 `git status`/`git diff`；push 前必须展示文件清单并等用户确认**。
- 代码全部带类型注解，过 `ruff`(默认+I) 与 `mypy`(strict)。

## 5. 当前交付态（一句话）

M1–M6 全部完成，13 个 Conventional Commits，工作区干净；**未配置 remote、未 push，等用户确认后推送**。
