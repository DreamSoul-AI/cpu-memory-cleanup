# CPU 内存清理

Windows 本地内存整理工具，提供 Rust Package、CLI/Agent 工具和 localhost 网页。网页与 CLI 共用同一 Rust 功能库和 Win32 后端，正式运行不需要 Python。

[GitHub 源码](https://github.com/DreamSoul-AI/cpu-memory-cleanup) · [Release](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases) · [MIT 许可证](LICENSE)

## 当前状态

当前源码版本 **v0.4.0**。Rust 库、CLI、网页和工作集整理已贯通，最终 Windows 包已完成本机真实验收。GitHub 交付状态以 [v0.4.0 Release](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/tag/v0.4.0) 为准，不发布 PyPI 或 crates.io。历史 v0.2.0 和 Pages 静态演示保留各自版本。

下载 Windows ZIP 后完整解压，双击 scripts/memory-cleanup.cmd，无需安装 Rust、Node 或 Python。请保留包内目录结构。

## 日常使用

下载包完整解压或开发目录构建后，双击 **[scripts/memory-cleanup.cmd](scripts/memory-cleanup.cmd)**，打开 **http://127.0.0.1:14800/**，不保留控制台窗口。

1. 网页显示真实内存读数，打开网页不会自动清理
2. 点击绿色“一键整理工作集”，执行一轮真实整理，结果使用前后真实快照计算
3. 需要关闭服务时点击网页“停止服务”，关闭标签页本身不会停止服务

最新启动器仅复用相同版本与构建标识的正式服务。旧构建占用端口时，先在旧网页停止服务再启动新版，不强杀其他程序。Windows ZIP 保留同一相对结构，CMD 使用包内 target/release 产物。

## 本版能力与边界

| 能力 | 状态 |
| --- | --- |
| Windows x64 内存容量、提交量及目标发现 | 已实现，真实系统读取 |
| 全局工作集整理 | 默认串行核验，对可访问进程调用 SetProcessWorkingSetSize(-1,-1)，不结束应用 |
| 网页主流程 | 实时读取、绿色按钮、独立结果、750 毫秒下降及低目标回弹已做真实验收 |
| Package、CLI/Agent、HTTP | 共用 Application，六工具接口见接入指南 |
| 定向/批次、保护及自动规则 | 已实现并有隔离验证，完整实机验收未完成，规则默认关闭、重启不自动启用 |
| experimental-quota | 非默认开发候选，做过真实探索对照，覆盖不足以决定采用，不接管绿色按钮 |
| 独立桌面窗口、CPU 分析、Standby、Linux/macOS | 不属于本版，旧 Python/Tk 入口归档 |

整理减少页面驻留，不删除应用数据，不保证降低私有提交量或提高速度。应用再次访问时占用可能回升。圆环归零是视觉表达，不代表系统内存被清空。实际容量、业务结果与动画分别管理，结果不等待动效结束。

默认与候选对照尚未证明谁更快，也没有与 360 同条件同负载的动态速度证明。360 参考来自固定版本静态分析，不是完整源码复原或逐帧等同。

## 开发构建与检查

需要 Rust 开发工具，网页测试另需 Node。Cargo 声明最低 Rust 1.85，本轮实测 Rust/Cargo 1.99.1 GNU，其他工具链和最低版本尚未验证。运行构建产物无需这些开发工具。

```powershell
cargo build --release --locked
# 单独运行测试，不执行真实清理
./scripts/run-tests.ps1
# 本地完整检查：测试、release 构建、离线源码包验证
./scripts/ci.ps1
# 工作区尚未提交时显式允许本地打包，仍不发布
./scripts/ci.ps1 -AllowDirty
```

ci.ps1 使用独立 target，在 .test-results/local-ci/日期时间/ 保存阶段日志、summary.json、工具目录及 EXE SHA256。失败阻断后续阶段，不自动部署、启动清理或安装依赖。离线包验证需要已准备好的缓存。统一脚本本轮使用 PowerShell 7 验证，需要允许本地脚本执行的环境，不要求改变系统执行策略。

最近入口适配验证：默认 25 项 Rust、开发配置 33 项 Rust、31 项网页 JS 全部通过，release 与源码包构建验证通过。原仓库运行曾遇 Cargo 文件访问拒绝，成功结果来自逐字节核对的一致副本，详见[入口适配验收](<docs/运行记录 Run Record/2026-11-03 本地入口与包适配验收.md>)。采用本地验证策略，未配置 GitHub Actions。

打包命令：`./scripts/package.ps1`，默认读取 target/release，输出 dist 下的 Windows ZIP 和 SHA256SUMS.txt，已有产物不覆盖。该脚本不清理、不上传。

## Package 与 Agent 接入

```powershell
.\target\release\cpu-memory-cleanup.exe --version
.\target\release\cpu-memory-cleanup.exe --tool-info
.\target\release\cpu-memory-cleanup.exe --web --port 14800 --output-dir .test-results/rust-runs
```

--version 和静态目录不采样。--web 启动回环服务，加 --open 才打开浏览器。启动本身不采样，可见网页约每 250 毫秒请求一次真实读数，不重叠请求。CLI 的 memory_status、memory_targets 执行真实读取，三个维护工具需要 --confirm 和本次授权，memory_result 只读回看。

最新库、CLI、HTTP 会话及新报告共享 build_id，标识源码输入，不是签名。工具输出 JSON，退出码 0 为完整成功、1 为非完整结果、2 为参数或入口错误。参数与库用法见[工具接入](<docs/工具接入 Tool Integration CPU 内存清理.md>)。

Rust 版本不兼容旧 Python 导入 API 及部分旧 CLI 选项，不要使用旧 pip 安装命令。需要历史版本时见[归档说明](legacy/python/README.md)。

## 目录与文档

```text
src/             Rust 库、CLI、HTTP 与嵌入网页
scripts/         CMD 启动、测试、构建与本地完整检查
tests/           Rust 合同、HTTP、测量及网页 JS 测试
examples/        非默认 feature 的性能对照入口
docs/            当前设计、计划、调研与日期记录
demo/            历史 GitHub Pages 静态演示
legacy/python/   历史 Python 版本
```

src 内职责以代码结构设计为准，外围目录可调整。target 和 .test-results 为忽略的本地产物及证据。

- [通用调研 General Research](<docs/通用调研 General Research CPU 内存清理.md>)：机制、同类源码、360 静态依据与限制
- [架构设计 Architecture Design](<docs/架构设计 Architecture Design CPU 内存清理.md>)：职责、结构与流程
- [代码结构设计 Code Structure Design](<docs/代码结构设计 Code Structure Design CPU 内存清理.md>)：源码模块和状态映射
- [开发计划 Development Plan](<docs/开发计划 Development Plan CPU 内存清理.md>)：发布收尾与后续边界
- [缺陷记录 Bug Report](<docs/缺陷记录 Bug Report CPU 内存清理.md>)：活动缺陷
- [开发记录 Development Record](<docs/开发记录 Development Record/README.md>)、[运行记录 Run Record](<docs/运行记录 Run Record/README.md>)：历史调整与实际证据

## 执行约定

开始研发读取 [AGENTS.md](AGENTS.md)，从已关联 DreamSoul 资料库采用相关规范正文。用户要求交互及清理迭代做真实验收，不能以隔离测试冒充真实效果，具体动作遵守当前授权范围。普通开发检查不执行真实清理，自动规则默认关闭。

不默认提权、结束其他应用、改注册表/页面文件、关闭防护或启用开机任务。维护互斥属于应用实例，宿主不得并发启动 CLI 与网页两个实例执行维护。错误、未知及 partial 保留，不自动换机制或重试。发布准备不等于已经发布。

<!-- 历史锚点保留给已有记录。 -->
<a id="项目约定与执行边界"></a>
<a id="实现与设计约定"></a>
<a id="运行与验证限制"></a>
<a id="当前交互需求与网页实施"></a>
<a id="内部工作集候选与实验准备"></a>
<a id="正式localhost网页与独立假数据模式"></a>
<a id="网页演示"></a>
<a id="package-与工具入口"></a>
<a id="单进程定向整理"></a>
<a id="文档入口"></a>
<a id="关联的-dreamsoul-资料"></a>
<a id="仓库目录导航"></a>
<a id="产品定位与当前行为"></a>
<a id="安装与启动"></a>
<a id="辅助只读与离线命令"></a>
<a id="显式单项维护与桌面"></a>
<a id="验证与结果"></a>
<a id="入口维护与研究范围"></a>
<a id="许可证与版本分发"></a>
