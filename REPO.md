# cpu-memory-cleanup 仓库约定

CPU 内存读取、整理及 CLI、桌面与网页入口。按 README 的平台能力与验证边界执行。纯文档核对采用离线检查。

## 研发入口

- [项目首页](README.md) 维护用途、安装和使用方式。
- [研发文档导航](docs/README.md) 定位现行设计、计划与历史记录。

## 分支与集成

日常开发使用 `dev`，任务从其建立工作分支并通过 PR 汇集；已验收的阶段经 PR 合入发布主线 `main`。合并后清理本任务分支，保留长期分支和未合并的研发工作。

## 平台和验证

平台能力、正式命令与打包验收以 [README](README.md) 为准。离线测试、真实主机检查和清理操作分别记录。源仓维护可执行代码，Knowledge Skill 维护接入与分发说明。

## Workspace 采用记录

- 来源 [Workspace 098bd2e](https://github.com/DreamSoul-AI/workspace/tree/098bd2eccf23eb1e6d77a3eec6db51e6bc387daf)。采用范围为Learnings 命名与分发、文档规范配置、长作业检查点与恢复、发布前分支对齐。项目专用流程见本文件。
