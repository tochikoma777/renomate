# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)。

## [Unreleased]

## [0.1.0] - 2026-10-09

首个可用版本：音乐库批量重命名 + TUI + 一键回退。

### Added

- **核心数据模型**：`FieldValue`（值 / 来源 / 置信度 / 锁定）、`MetadataBundle`、
  `RenameItem`、`Plan`（含状态统计与 JSON 序列化）。
- **配置**：TOML 配置（XDG 路径）+ 内置默认值；`[profiles]` / `[presets]` / `[sanitize]` /
  `[safety]` / `[musicbrainz]` / `[defaults]` 全部分段可配。
- **扫描**：递归扫描 + 闸门 1（路径黑名单）+ 闸门 2（类型与属性白名单），
  每个被跳过的文件都带人类可读原因。
- **元数据 Provider 链**：`manual → audiotag(mutagen) → path → filename → network → default`，
  先到先得 + 高置信度可升级 + `locked` 保护。
  - `audiotag`：`mutagen` 可选依赖 + 运行时动态 import，缺失时自动降级。
  - `filename`：6 条解析模式 + 噪声清洗（站点水印 / 音质标记）。
  - `network`：MusicBrainz **默认关闭**，限流 1 req/s + 本地 30 天缓存。
- **模板引擎**：`{field}` / `{field|filter:arg}`，过滤器
  `upper` `lower` `title_case` `pad` `strip_feat` `default`；逐字段 sanitize、空折叠、长度截断。
- **计划体检**：`no_op` / `empty_name` / `missing:*` / `path_too_long` / `name_too_long` /
  `cross_device` / 冲突自动加 ` (2)`…` (99)`，大小写不敏感冲突单独识别。
- **执行**：两阶段 rename（防链式覆盖）、大小写安全 rename（Windows / `/mnt/c`）、
  逐条 try/except + 失败回滚，残留临时文件可被 `renomate recover` 清理。
- **回退**：append-only JSONL 事务日志（`flush` + `fsync`），`undo --last/--session/--only`，
  回退前校验 `size` + `mtime_ns`，不一致标 `needs_manual`。
- **CLI**：`scan` / `preview` / `apply` / `undo` / `journals` / `recover` / `report` /
  `config` / `profiles` / `tui`，默认 dry-run，退出码 `0/1/2/3/130`。
- **TUI（Textual）**：预览表格、模板编辑、预设切换、勾选/全选/反选、告警区、
  确认屏（输入 `APPLY`）、回退屏（`u` 键）。
- **测试**：pytest + monkeypatch（网络全部打桩，零真实联网），覆盖率 ≥ 93%，
  核心模块 `safety` / `plan` / `executor` / `journal` ≥ 98%。

### Security

- 路径黑名单覆盖 `/etc` `/proc` `/mnt/c/Windows` `C:\Program Files` 等系统目录，
  盘符/挂载根目录一律拒绝。
- 符号链接、隐藏文件、非白名单扩展名、只读文件默认跳过。
- 未显式确认前绝不写盘：CLI 需 `--yes --token APPLY`，TUI 需输入 `APPLY`。

[Unreleased]: https://github.com/tochikoma777/renomate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tochikoma777/renomate/releases/tag/v0.1.0
