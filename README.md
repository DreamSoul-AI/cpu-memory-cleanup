# CPU 内存清理

Windows 本地内存整理工具，提供 Rust Package、CLI/Agent 工具和 localhost 网页。网页与 CLI 共用同一 Rust 功能库和 Win32 后端，正式运行不需要 Python。

[GitHub 源码](https://github.com/DreamSoul-AI/cpu-memory-cleanup) · [Release](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases) · [MIT 许可证](LICENSE)

## 当前状态

当前源码版本 **v0.5.1**。Windows x64 提供真实读取与工作集整理，保留网页、独立应用窗口和 CMD 脚本三个入口，共用 Rust Package。CLI/Agent 普通调用默认不保存运行记录，显式指定目录才保存。已发布 [v0.5.1](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/tag/v0.5.1)，安装包和源码可从该页下载；发布与正式安装验证见 [0.5.1 发布验收](<docs/运行记录 Run Record/2026-11-06 0.5.1发布验收.md>)。

Windows安装包创建开始菜单入口；便携ZIP完整解压后，双击 `scripts/memory-cleanup.cmd` 打开网页版，或双击 `target/release/memory-cleanup-window.exe` 打开应用窗口。运行不需要Rust、Node或Python；独立窗口需要Microsoft WebView2 Runtime，包内附加载器但不捆绑完整Runtime。安装包未签名。

Linux x86_64 和 macOS arm64 继续使用 [0.5.0 的只读试用包](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/tag/v0.5.0)；本次 0.5.1 仅更新 Windows 分发。这些包能显示真实容量与进程，不提供主动整理。Linux仅Ubuntu WSL、Mac仅托管15.8.2 arm64验证；普通桌面、旧libc、Finder/Gatekeeper、Intel Mac等未验证，Mac未签名或公证。

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
| 独立应用窗口 | Windows可选WebView2外壳，复用同一网页与后端，旧Python/Tk入口归档 |
| Linux/macOS | 有限环境验证的只读试用包；不提供主动整理 |
| CPU分析、Standby | 未提供 |

整理减少页面驻留，不删除应用数据，不保证降低私有提交量或提高速度。应用再次访问时占用可能回升。圆环归零是视觉表达，不代表系统内存被清空。实际容量、业务结果与动画分别管理，结果不等待动效结束。

默认与候选对照尚未证明谁更快，也没有与 360 同条件同负载的动态速度证明。360 参考来自固定版本静态分析，不是完整源码复原或逐帧等同。

## 安装与跨平台说明

0.5.0增加Windows当前用户安装器和Linux/macOS只读适配。Windows安装器创建开始菜单入口，卸载保留用户数据。未提供窗口构建时打开浏览器；显式带匹配窗口构建时提供独立WebView2窗口及浏览器备用入口。`scripts/build-installer.ps1 -Compiler 编译器路径`从两份release EXE构建安装候选，需要另行准备NSIS，不自动安装编译器。

Linux候选已实现真实容量和目标读取，维护按钮禁用，不提供主动整理。Ubuntu WSL已完成原生测试及内置浏览器真实只读、服务停止和解压包验收，不能代表独立Linux桌面已支持。macOS arm64只读候选已在托管Mac验证实际容量、原生进程身份、解压包、HTTP、真实页面渲染与停止；不提供主动整理，也未验证普通用户下载、Gatekeeper或原生Mac小窗口。v0.4.0的支持范围仍是Windows x64，不能用新版候选证据回签旧包。开发与验收进度见开发计划及[本轮完成核验](<docs/运行记录 Run Record/2026-11-05 本轮目标完成核验.md>)，其中列出逐项证据和未验证范围。

安装版从开始菜单“CPU Memory Cleanup”启动，程序位于当前用户LocalAppData的Programs目录，运行记录独立存放在LocalAppData的DreamSoul目录；卸载保留记录。安装器尚未签名，具体可下载产物以Release列表为准。 当前窗口候选已通过安装快捷入口、重复打开、自有进程整理、结果回看、动效收尾及停止重开旅程；后台调试验收未覆盖前台聚焦。整理期间显示真实处理目标数，计数不代表释放比例；首次原生等待仍取决于实际负载。见[窗口验收](<docs/运行记录 Run Record/2026-11-05 窗口完整旅程与整理进度.md>)。Linux原生开发环境可用 `sh scripts/package-linux.sh 新输出目录` 构建只读便携候选，解压后运行 `sh ./run.sh` 并在浏览器打开打印的本地地址；不自动安装依赖，不执行整理。

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

已发布入口适配验证：默认 25 项 Rust、开发配置 33 项 Rust、31 项网页 JS 全部通过，release 与源码包构建验证通过。原仓库运行曾遇 Cargo 文件访问拒绝，成功结果来自逐字节核对的一致副本，详见[入口适配验收](<docs/运行记录 Run Record/2026-11-03 本地入口与包适配验收.md>)。日常仍采用本地验证；本轮另已配置获授权的macOS只读专项Actions，不作为已通过的跨平台支持证据。

Windows完整打包命令：`./scripts/package.ps1 -WindowBuildDirectory target/release`，窗口先由 `./scripts/build-window.ps1` 构建。`./scripts/build-installer.ps1 -Compiler NSIS路径 -WindowBuildDirectory target/release`生成安装包。`./scripts/package.ps1`，默认读取 target/release，输出 dist 下的 Windows ZIP 和 SHA256SUMS.txt，已有产物不覆盖。该脚本不清理、不上传。

## Package 与 Agent 接入

```powershell
.\target\release\cpu-memory-cleanup.exe --version
.\target\release\cpu-memory-cleanup.exe --tool-info
.\target\release\cpu-memory-cleanup.exe --web --port 14800 --output-dir .test-results/rust-runs
```

--version 和静态目录不采样。--web 启动回环服务，加 --open 才打开浏览器。启动本身不采样，可见网页约每 250 毫秒请求一次真实读数，不重叠请求。CLI 的 memory_status、memory_targets 执行真实读取，三个维护工具需要 --confirm 和本次授权，memory_result 只读回看。

最新库、CLI、HTTP 会话及新报告共享 build_id，标识源码输入，不是签名。工具输出 JSON，退出码 0 为完整成功、1 为非完整结果、2 为参数或入口错误。参数与库用法见[工具接入](<docs/工具接入 Tool Integration CPU 内存清理.md>)。

Rust 版本不兼容旧 Python 导入 API 及部分旧 CLI 选项，不要使用旧 pip 安装命令。旧 Python 实现已从当前分支移除，可从[固定历史提交](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/41eb28bf8f4c8042bc7a120afce665b7353fd3ed/legacy/python)恢复。

## 目录与文档

```text
src/             Rust 库、CLI、HTTP 与嵌入网页
scripts/         CMD 启动、测试、构建与本地完整检查
tests/           Rust 合同、HTTP、测量及网页 JS 测试
experiments/     非默认 feature 的性能对照入口
docs/            当前设计、计划、调研与日期记录
demo/            历史 GitHub Pages 静态演示
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

## 独立应用与本地开发环境

0.5.0 已发布独立应用窗口。开始菜单“CPU Memory Cleanup”打开应用，“Browser workbench”打开网页版；CMD 脚本仍启动浏览器。窗口默认端口 14801，浏览器默认端口 14800，窗口退出会停止自身服务并等待在途动作收尾。

构建窗口用 `./scripts/build-window.ps1`。GNU 工具链需要汇编器，可通过 `-AssemblerDirectory 路径` 显式指定；本机已准备 `.local/mingw64/bin` 时默认采用。窗口需 WebView2 Runtime 及同目录加载器，默认 CLI/库不要求窗口依赖。`--smoke-test 新回执路径` 仅用于隐藏窗口真实只读检查，不执行整理。

开发脚本优先使用系统 Cargo；未安装时可使用已准备的 `.local/rust/native` 和 `.local/rust/cargo-home`。工具、NSIS 和窗口测试驱动放在 Git 忽略的 `.local/`，脚本不下载依赖、不全局安装工具、不再依赖测试记录目录中的工具链。新环境按工具官方说明准备，不能假定缓存随源码存在。

`target/` 保留 Cargo 缓存及当前 release 程序，`.test-results/` 保留测试和运行证据，`dist/` 保留已核对的运行包。发布附件按版本保存在 `dist/`，旧版附件独立保留，不改写已有 Release。

性能实验源码位于 `experiments/`，原命令 `cargo run --features experimental-quota --example workingset-compare -- plan` 继续可用。plan 是静态说明，真实实验不由构建或测试自动执行。`demo/` 是历史 Pages 静态演示，正式网页源码在 `src/interaction/web_assets/`。

Linux/macOS 为只读包，命令及已验证环境见本页当前状态。macOS 工作流只保留 `workflow_dispatch` 手动触发，无普通 push、PR 或定时触发；它构建、测试、真实读取，不执行维护。

## 0.5.0接口兼容变化

Rust `Memory` 的提交量/上限改为 `Option<u64>`，新增可用量口径和可选压缩驻留字段。直接构造结构体的调用方需适配，可用 `..Memory::default()` 初始化可选项。JSON在Windows/Linux仍保留已测数值，Mac未提供的提交统计为null及unavailable；不要把null当成0。静态目录新增platform_capabilities，读取可用不代表支持维护。

项目自有代码继续采用MIT，分发包附[第三方通知](THIRD_PARTY_NOTICES.txt)，涵盖固定依赖及WebView2加载器的原始通知；未包含完整WebView2 Runtime。

最终交付、附件摘要及只读验收见[0.5.0发布验收](<docs/运行记录 Run Record/2026-11-05 0.5.0发布验收.md>)。

## Agent Skill 接入

[Knowledge Skill](https://github.com/DreamSoul-AI/knowledge/tree/main/skills/cpu-memory-cleanup) 维护指令、元数据、安装说明和技能 ZIP，所有可执行代码由本仓库维护。初始化依赖见 [scripts/agent-skill](scripts/agent-skill/)，Windows ZIP 同时提供 install-runtime.ps1 和 release.ps1。普通使用不需要源码仓库、Git、Rust、Node 或 Python。

每项任务开始时默认解析官方 latest 稳定 Release，校验附件 SHA256 并安装或复用轻量 CLI；支持 -Version 指定正式版本，各版本分别保存，任务内不切换程序。初始化只读静态目录，不采样或清理。Skill 维护前核对实际无记录能力，不凭固定版本号判断。

普通 CLI/Skill 调用只通过 stdout 返回 JSON，不创建 report.json、timing.json、运行目录或 Markdown。--no-record 明确保证不保存，与 --output-dir DIR 互斥；只有用户主动选择保存时才落盘，并支持后续回看。无记录结果 output_dir 为 null；安装完整性回执与每次操作记录分开。网页和桌面继续使用既有保存方式。

0.5.0 及更早发布包没有无记录能力，不应通过删参数重试绕过。项目开发、打包和发布验证依照 AGENTS.md 整理必要证据，普通 Skill 使用不写仓库资料。
