# CPU 内存清理

Windows 本地内存整理工具，提供 Rust Package、CLI/Agent 工具和 localhost 网页。正式运行不需要 Python，网页直接调用同进程 Rust 功能库与 Win32 后端，不通过 CLI 子进程执行清理。

[GitHub 源码](https://github.com/DreamSoul-AI/cpu-memory-cleanup) · [历史 Release](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases) · [MIT 许可证](LICENSE)

当前源码版本 **0.4.0**，仅在本地构建和验证，尚未推送、打 tag 或发布。此前 v0.2.0 Release 和 GitHub Pages 静态演示保留各自版本，不包含本轮 Rust 运行时。正式网页使用真实系统读数，不提供合成数据回退。

## 启动和使用

Windows x64，需要现代浏览器。开发构建要求 Rust 1.85+；本轮实际使用 Rust/Cargo 1.99.1 GNU 工具链，其他工具链与系统版本尚未验证。网页资源嵌入 EXE，运行无需 Node 或 Python。

在仓库根目录构建：

```powershell
cargo build --release --locked
```

构建后双击 **`scripts/memory-cleanup.cmd`**，隐藏启动本地服务并在默认浏览器打开网页；默认地址为 **http://127.0.0.1:14800/**。再次打开会复用同版本的正式 Rust 服务。CMD直接启动无控制台的Rust程序，不依赖PowerShell脚本或改变系统执行策略。端口被旧版或其他程序占用时明确失败，不结束其他进程。当前验收服务已由 Rust 启动，自动规则关闭。

需要从命令行明确指定记录位置时：

```powershell
.\target\release\cpu-memory-cleanup.exe --web --port 14800 --output-dir .test-results/rust-runs
```

该命令不自动打开浏览器；需要时加 `--open`。仅绑定回环地址。启动不采样，网页可见时按250毫秒目标间隔读取真实容量（不重叠请求，实际间隔依响应而定），进程列表按需读取；点击绿色按钮执行一轮工作集整理。高级批次和规则启用仍需确认。关闭标签页不会停止服务，请使用网页“停止服务”。本轮验收使用内置浏览器，不占用桌面鼠标键盘。

## 当前能力和边界

| 能力 | 当前状态 |
| --- | --- |
| 真实物理容量与提交量 | Win32读取，未知或错误不补零；显示与整理任务独立 |
| 全局工作集整理 | 一轮枚举，对可访问进程调用 `SetProcessWorkingSetSize(-1,-1)`，关闭句柄；不结束应用 |
| 全局最小权限开发候选 | 非默认 `experimental-quota` feature，显式库装配才选择；候选和对照入口隔离验证完成，未实机比较，不接管绿色按钮 |
| 选定进程和批次 | PID＋创建身份核验，支持路径/实例保护；全局整理不应用保护名单 |
| 记录与回看 | 新版 `report.json`，维护前保存意图、维护后保存事实；只读兼容既有 JSONL/诊断记录 |
| Package、CLI/Tool、HTTP | 共用 Rust `Application`，六个工具见接入指南 |
| 定时和持续压力规则 | 已迁移、默认关闭；重启不自动武装，本轮未启用真实规则 |
| 本地桌面入口 | 统一启动网页工作台，旧 Tk 独立小窗口归档 |
| Linux/macOS、CPU分析、Standby、旧Python导入API | 未迁移或未支持，不由旧解释器静默补位 |

本次真实验收一轮691个目标：679成功、10跳过、2失败，未知0、资源关闭确认；前后可用量19.77→28.06 GiB，增加8.29 GiB，业务约3.23秒。原始 `partial` 保留。证据见 [Rust原生网页验收](<docs/运行记录 Run Record/2026-11-02 Rust原生网页验收.md>)。这证明功能生效，不证明与360同速、长期收益或多平台支持。

## 交互与效果

主区保留圆环、实际读数、绿色按钮和简短结果，进程选择、保护与原始报告收进详情。圆环、大号百分比和细条共用一个显示驱动，连续追踪最新真实目标，去掉重复CSS缓动和回弹结束时的直接跳值。实际容量与小号实际百分比立即采用原始读数。主操作只切换必要控件，不重建已读进程列表。点击先绘制忙碌反馈，后台整理时继续读取；业务结束立即显示原始前后快照结果，再播放约750毫秒归零，到零请求一次新读数，再约300毫秒回弹。没有额外3～8秒趋稳等待。动效不改真实数据、不触发第二次清理、不推迟业务结果。

工作集裁剪减少页面驻留，不删除程序数据，也不保证降低私有提交量。重新使用应用可能重新驻留和触发缺页。容量变化不等于提速，不能把圆环归零解释为系统内存清空。

## Package 与 Agent 工具

```powershell
.\target\release\cpu-memory-cleanup.exe --tool-info
.\target\release\cpu-memory-cleanup.exe --tool memory_result --run-dir 原记录目录
```

静态目录不读取或整理系统。`memory_status`、`memory_targets` 是真实读取；三个维护工具需要本次授权及 `--confirm`。输出JSON，退出0为完整成功、1为非完整成功、2为参数/入口错误。完整参数、批次格式和 Rust 库接入见 [工具接入](<docs/工具接入 Tool Integration CPU 内存清理.md>)。

Python导入API已发生不兼容变化；需要旧版时使用原版本或 [历史源码](legacy/python/README.md)。不要对当前仓库运行旧的pip安装命令。Cargo包使用MIT，当前未发布 crates.io/PyPI 或新的 GitHub Release。

## 开发与测试

开始研发先读 [AGENTS.md](AGENTS.md)，从已关联的本地DreamSoul资料库读取相关规范正文；本地缺失再沿其中飞书链接定位。测试直接采用共用 [测试规范 代码](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf)，不另建一份规范。

```powershell
./scripts/run-tests.ps1
```

独立检查分别为 `cargo fmt --all -- --check`、`cargo clippy --all-targets --locked -- -D warnings`、`cargo test --lib --test '*' --locked` 和 `node --test tests/interaction/web_assets/app.test.cjs tests/interaction/web_assets/memory-display.test.cjs`。统一脚本检查 Rust 格式、默认和候选配置的严格 Clippy、库及全部集成测试，以及网页 Node 检查。无测试用例的 GUI 启动器只检查编译，不执行空测试程序。需要 Rust 开发工具和 Node。统一脚本在允许本地脚本的PowerShell环境执行。Windows PowerShell策略禁止脚本时，可直接使用Cargo命令与Node检查，无需修改系统策略。

默认配置25项、`experimental-quota` 开发配置33项 Rust 检查，以及26项正式网页 JS 检查通过，测试隔离系统副作用；这些检查不执行真实清理，也不代替实机效果证据。原生与业务分段计时、全量调用分布及最终写盘计时附件已接入；旧真实记录只读回看可重算已覆盖分布，缺失阶段保留未知。只读页面与已有记录验收见 [流畅显示与计时记录](<docs/运行记录 Run Record/2026-11-02 流畅显示与计时验收.md>)。全局最小权限候选的合同、隔离验证及未覆盖范围见 [候选验收](<docs/运行记录 Run Record/2026-11-02 最小权限串行候选验收.md>)。性能对照的开发入口、范围与用法见 [工具接入第六章](<docs/工具接入 Tool Integration CPU 内存清理.md#六开发性能对照入口>)，准备阶段的执行证据见 [性能对照准备验收](<docs/运行记录 Run Record/2026-11-03 性能对照准备验收.md>)。构建和静态计划不执行真实清理。

旧Python树状manifest保留在归档，不再参与当前测试发现。当前未配置GitHub CI，不把本地通过称为CI通过。

## 文件与文档入口

```text
src/                 Rust库、CLI、HTTP及嵌入网页
  application/       共用业务、保护和规则
  system/            操作系统接入和记录
  interaction/       CLI、网页适配及资源
examples/            非默认feature的开发性能对照入口
scripts/             测试、构建和正式网页启动
tests/              Rust合同/HTTP测试、正式网页JS及静态Demo测试
demo/               历史GitHub Pages静态演示
docs/               当前设计、计划、调研与日期记录
legacy/python/      原Python源码、测试、配置和入口
```

源码职责硬约束以代码结构设计为准；外围目录为可调整约定。`target/`为构建产物，`.test-results/`为忽略的本地证据与隔离工具链，不提交原始主机路径清单或编译缓存。

- [通用调研 General Research](<docs/通用调研 General Research CPU 内存清理.md>)：机制、360和同类源码、证据及限制
- [架构设计 Architecture Design](<docs/架构设计 Architecture Design CPU 内存清理.md>)：语言无关的三层职责、结构与流程
- [代码结构设计 Code Structure Design](<docs/代码结构设计 Code Structure Design CPU 内存清理.md>)：架构到Rust源码的映射
- [开发计划 Development Plan](<docs/开发计划 Development Plan CPU 内存清理.md>)：本轮验收与剩余事项
- [缺陷记录 Bug Report](<docs/缺陷记录 Bug Report CPU 内存清理.md>)：活动问题
- [开发记录 Development Record](<docs/开发记录 Development Record/README.md>)、[运行记录 Run Record](<docs/运行记录 Run Record/README.md>)：按日期保存演进和真实执行证据

## 执行约定

真实读取、维护、持续后台任务和发布按当前用户确认执行。计划和示例不扩大授权。本轮允许真实读取与一次全局整理，该次已完成，不自动追加第二轮；规则关闭。测试原生副作用必须隔离。默认不提权、不结束其他进程、不改注册表/页面文件或系统设置、不关闭防护、不启用系统服务或开机任务。只读回看不访问当前内存、不重放动作。错误和未知保留，不自动换机制或重试。


# 历史方案定位
原章节和旧执行约定归档到 [Python版本项目说明基线](<docs/开发记录 Development Record/2026-11-02 Python版本项目说明基线.md>)。历史记录中的提交、路径和验收状态保留原义。

<!-- 历史章节锚点仅供旧记录定位，不表示旧合同继续适用。 -->
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
