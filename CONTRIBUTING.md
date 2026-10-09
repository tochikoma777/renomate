# 贡献指南

感谢你愿意动手。这个项目有四条底线，任何 PR 都不得突破：

1. 绝不碰危险文件（系统目录、只读文件、符号链接、非白名单类型一律跳过）；
2. 改之前必须让用户看见并确认（默认 dry-run）；
3. 改完之后必须能一键回退（事务日志 + `undo`）；
4. 单个文件失败不能中断整批，也不能留下不可解释的中间状态。

## 环境

```bash
conda create -n renomate python=3.12 -y
conda activate renomate
pip install -e ".[dev,audio]"
pre-commit install          # 可选
```

**统一用 pip**，不要引入 `uv` / `uv.lock`，也不要在代码、文档、CI 里出现 `uv` 命令。

## 日常命令

```bash
pytest -q                              # 全量测试
pytest -q --cov=renomate --cov-report=term   # 覆盖率
ruff check .                           # lint
ruff format .                          # 格式化
mypy src/renomate                      # 类型检查（strict）
```

提交前请保证四者全绿。覆盖率门槛：总体 ≥ 85%，核心模块
`safety` / `plan` / `executor` / `journal` ≥ 95%。

## 代码约定

- **全部带类型注解**，函数签名写全；`mypy --strict` 必须过。
- 分层依赖方向铁律（有架构测试守护）：
  - `models / template / safety / executor / journal / plan / config / report / scanner /
    platform` 不得 import `mutagen` / `musicbrainzngs` / `textual`；
  - `providers/*` 不得 import `textual`；
  - `tui/*` 不得直接 import `mutagen` / `musicbrainzngs`。
- `mutagen` 与 `musicbrainzngs` 只能**运行时动态 import**（`importlib.import_module`），
  缺失时优雅降级。
- 新增 Provider 通过 `renomate.providers.register_provider()` 注册，不要在核心层写领域逻辑。
- 任何文件操作都走注入的 `FileSystem`（`renomate.platform.fs`），方便测试替身。

## 测试约定

- 网络相关（MusicBrainz）测试**必须 monkeypatch**，禁止真实联网。
- 需要 Windows 语义（大小写不敏感 / PermissionError / EXDEV）的场景用假 `FileSystem`，
  不必真的在 Windows 上跑。
- 涉及真实写盘的用例一律用 `tmp_path`。

## 提交规范

[Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(scope): 新功能
fix(scope): 修 bug
refactor(scope): 重构（无行为变化）
test:        测试
docs:        文档
chore:       构建/工具
ci:          CI 配置
```

`scope` 建议用模块名，如 `feat(plan): …`、`fix(executor): …`。

## PR 检查清单

- [ ] `pytest` / `ruff check` / `ruff format --check` / `mypy` 全绿
- [ ] 新增代码有对应测试，覆盖率不下降
- [ ] 没有引入新的可选依赖的硬 import
- [ ] 涉及安全闸门的行为变化，在 README「安全机制详解」里同步更新
