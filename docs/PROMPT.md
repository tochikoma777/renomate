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
