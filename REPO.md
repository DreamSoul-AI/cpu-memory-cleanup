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
- 单项更新：[Workspace 65657c5](https://github.com/DreamSoul-AI/workspace/tree/65657c50cf6d98ac1898ae0183cb9ca98498e0a9)。采用旧对话重读、新对话定位、文档主动补链接、浏览器收尾、飞书专节及 CLI 任务临时目录、共享资源主动检查；保留项目专有约定和原整版基线。共享 data/models 直接复用，仅 artifacts 按仓库隔离，接入示例见 [Workspace 教程](https://github.com/DreamSoul-AI/workspace/blob/main/templates/software-engineering/examples/shared-resources/README.md)。
- 单项更新：[Workspace f01fab8](https://github.com/DreamSoul-AI/workspace/tree/f01fab8598f570a45eca8320e546d636268b700a)。采用通用文档规范及飞书/GitHub 平台章节入口、首次接入默认值和已有选择保留、代码架构设计与代码结构设计并列命名；图表简洁要求随通用规范读取。保留本项目专有约定、既有文档路径及原整版基线。[规范分发副本](https://github.com/DreamSoul-AI/workspace/blob/f01fab8598f570a45eca8320e546d636268b700a/%E6%96%87%E6%A1%A3/%E8%A7%84%E8%8C%83/%E9%80%9A%E7%94%A8/%E9%80%9A%E7%94%A8%E6%96%87%E6%A1%A3%E8%A7%84%E8%8C%83.md)。
