更新日期：2026年11月6日。Rust 0.5.0 已提供 GitHub Windows 包，未发布包索引。库、CLI 工具和网页共用 Application，不由宿主复制清理脚本，不要求安装 Python。Knowledge 本地已增加可安装 Skill 与说明，首次初始化代码由本仓库 scripts/agent-skill 维护，两仓库新增内容均尚未推送；版本、构建和边界见 [README](../README.md)。

# 一、静态发现与调用

构建后执行 `cpu-memory-cleanup.exe --tool-info`，返回工具目录版本1、Rust版本、build_id、描述、input_schema、参数数组模板和退出码。该调用不读取系统。宿主使用绝对可执行路径和参数数组，不将模型输入拼成shell命令。stdout是一份JSON，stderr为入口错误；退出0为整体success，1为partial/failed/unknown/cancelled，2为参数或入口错误。

| 工具 | 参数 | 副作用 |
| --- | --- | --- |
| memory_status | 无 | 一份真实容量快照 |
| memory_targets | 无 | 真实进程枚举、创建身份及可读取工作集 |
| memory_cleanup | --confirm [--no-record | --output-dir DIR] | 一轮全局可访问进程工作集整理 |
| memory_process_cleanup | --pid PID --created-at ID --confirm [--no-record | --output-dir DIR] | 指定身份的一个目标 |
| memory_batch_cleanup | --request-file FILE --confirm [--no-record | --output-dir DIR] | 固定目标列表及可选保护 |
| memory_result | --run-dir DIR | 只读回看，不连接当前内存、不重放 |

形式为 `cpu-memory-cleanup.exe --tool 工具名 参数`。没有 `--tool` 时使用 `--web` 启动回环网页，默认端口14800，可显式指定 `--port`。同一宿主不要并发启动维护CLI和网页维护，互斥是应用实例内，未提供跨进程全机锁。

# 二、目标与确认

先取得本次memory_targets返回的目标，再传PID及原始created_at。单进程CLI只接受PID和创建身份，没有image_path参数；需要额外路径核验时，使用批次请求的Target.image_path或库接口，不自行添加CLI选项。不得按程序名称猜PID、换同名进程或在失败后改全局范围。批次请求为UTF-8 JSON，最多1MiB：

```json
{"targets":[{"pid":123,"created_at":"从本次发现复制","name":"显示名","image_path":null,"working_set_bytes":null}],"protections":[]}
```

以上是格式示例，不可直接拿示例身份执行。targets为1..4096个不重复身份。保护规则为完整image_path，或pid加created_at；保护在逐进程批次应用，全局整理不支持排除。规则注册和工具目录读取不产生维护授权。模型传入true或--confirm也不能自证用户许可。

# 三、结果与兼容

报告含run_id、mode、completion、phase、samples、analysis、action、errors、metadata和output_dir。物理量单位bytes；analysis.available_change_bytes是原始前后系统可用量差额，不是独占释放量或提速比例。action.process_sweep保存覆盖、返回、未知、关闭状态和有界明细；跳过与失败不是成功。整体非success仍可能已经发生维护，宿主不得自动重试。

新整理的 `action.process_sweep.timing.strategy` 和 `identity_scope` 标明策略及身份覆盖。最小权限全局候选的逐项 `created_at` 为 null，不可按 PID 跨运行关联或补查回填。旧记录缺项不推定候选身份。

新记录为版本3的report.json，另有版本1的timing.json计时附件。附件保存同一运行的最终写盘时长，回看校验身份与版本后合并，原记录缺少阶段仍保持未知。metadata.native_summary给出已覆盖调用数、分位和最慢项，完整分布与截断覆盖分别标明。维护仍不自动重试。report.json流程为：动作前先写calling/unknown意图，动作后保存原始事实，再封存。memory_result只接受已封存记录，支持新文件及既有samples.jsonl、operation.jsonl与可选diagnostics.json。回看使用新run_id，metadata.source_run_id指原运行；不回写原件。旧Python批次索引、CPU分析等格式不在本轮兼容承诺内。

六个工具名称及目录版本保留，Python模块导入API、旧CLI其他选项和Python发行方式已不兼容，不能只按名称推断全部合同相同。当前未提供MCP、远程RPC或特定Agent平台注册器。

# 四、Rust同进程接入

通过本仓库Cargo库引用 `cpu_memory_cleanup::{Application, Target}`。Application::default选择实际系统，Application::new接受System隔离实现，构造本身不读取。Windows 开发对照可显式启用 Cargo feature `experimental-quota`，调用 `system::windows::quota_only_global_candidate()` 并注入 `Application::new`。构造不读取或维护，只有全局路径省略查询，明确目标仍核验创建身份和可选路径。仅启用 feature 不会改变默认工厂、CLI、HTTP 或规则装配，没有新增工具或网页模式。候选已做真实探索对照，但全局覆盖不足、顺序未平衡，不能据此判断更快或切换默认，详见[真实工作集对照](<运行记录 Run Record/2026-11-03 真实工作集对照.md>)。

公开方法为memory_status、memory_targets、memory_cleanup、memory_process_cleanup、memory_batch_cleanup及memory_result，维护传记录基目录、明确确认和取消标志。HTTP调用同一库，不经过CLI子进程。

# 五、验证与采用限制

本轮静态目录、旧真实记录只读回看、隔离库与HTTP检查、正式网页一次真实全局整理已执行，见 [Rust验收记录](<运行记录 Run Record/2026-11-02 Rust原生网页验收.md>)。定向/批次与规则真实动作未执行。工具可本地调用不代表已安装到其他项目、Knowledge固定链接已更新或已发布新包。宿主采用前固定实际提交和产物校验值，按用户范围开放读取与维护。


# 六、开发性能对照入口
非默认 `experimental-quota` feature 提供独立例程，不是第七个 Agent 工具，不进入正式网页。仅打印静态计划的命令如下，不读取系统或分配实验负载：

```powershell
cargo run --release --locked --features experimental-quota --example workingset-compare -- plan
```

离线比较用 `compare VERIFIED_JSON QUOTA_JSON` 取代末尾 `plan`，读取同一会话保存的两份证据，输出 JSON，不连接 System、不重放整理。缺失指标不补零，没有自动胜者。对照输出中业务墙钟、系统墙钟、原生调用合计、前置工作集总量、可用量差额、负载读回及动作返回到读回的间隔分别列出。

未来获准真实对照时，`session owned|global MIB NEW_DIRECTORY` 才是实验入口。需要新目录及1..256 MiB预算。输入 `PREPARE owned` 或 `PREPARE global` 后才建立范围、分配自有负载。`owned` 只包含例程自身，同一进程保留负载；`global` 捕获当时完整 PID 清单，不加入后续新进程。两者都通过开发用固定全局式串行装配调用同一个 Package，不走定向接口来偷偷恢复查询权限。

会话就绪后仍不维护。每次必须输入 `RUN verified` 或 `RUN quota`，每方法至多一次，允许任意顺序，不自动连跑、重试或恢复中断动作。`quit` 或 EOF 退出。确认文本不代替宿主取得用户授权。本轮只验证静态计划、未确认退出和隔离接入，没有输入 PREPARE 或 RUN 执行真实会话。

会话目录包含 session.json、每方法的 started/before/结果附件及 runs/ 原始报告。方法在准备前已登记消耗，意图写入失败不会派发。before观测在负载恢复后取得，after观测在负载读回前取得。后观测和报告记账可能使页面重新驻留，读回不是紧接系统调用的精确缺页成本，间隔单列。

全局前后观测不能证明候选句柄的原子身份、其他应用负载恢复或系统背景活动相同。起点差异超过5%的探索容差、身份缺失或变化、覆盖不全、原始非完整终态、计时缺口、负载核验失败都会列入 issues。总量接近也不证明每项状态相同。一次自有进程对照不能解释全局三秒长尾，没有配对重复和顺序平衡前不据此切换默认。实现与实际执行范围见 [性能对照准备验收](<运行记录 Run Record/2026-11-03 性能对照准备验收.md>)。

# 历史方案定位
原章节和旧执行约定归档到 [Rust迁移前工具接入基线](<开发记录 Development Record/2026-11-02 Rust迁移前工具接入基线.md>)。历史记录中的提交、路径和验收状态保留原义。

<!-- 历史章节锚点仅供旧记录定位，不表示旧合同继续适用。 -->
<a id="一接入范围与固定版本"></a>
<a id="二安装与静态发现"></a>
<a id="三宿主调用合同"></a>
<a id="四授权目标与结果"></a>
<a id="五验收与当前状态"></a>
<a id="六本地批次与正式网页候选"></a>
<a id="七维护诊断的增量字段"></a>

# 七、本地入口与构建一致性

`cpu-memory-cleanup.exe --version`不采样，显示库版本及BUILD_ID。静态工具目录、HTTP会话和新报告metadata引用同一源码构建标识。同版本不同构建不会被启动器复用；请先在旧网页停止服务，再打开新程序。构建标识用于识别实现，不是签名或系统授权。

双击scripts/memory-cleanup.cmd仍启动无控制台网页，不恢复旧Tk窗口。CLI仅在明确调用时读取/整理；server与CLI共用库，动效仅属于网页。`scripts/ci.ps1`执行本地测试、构建及离线源码包验证，不启动系统维护、不部署、不发布。命令与产物位置以[README](../README.md)为准。

# 八、源码包与运行交付

Cargo源码包用于库接入及Rust构建，包含当前源码、嵌入资源、Rust测试、例程、README与MIT，不包含scripts、旧Python、运行证据或开发工具链。README中的仓库开发脚本应在完整仓库使用，不能假定解包crate后存在。Windows运行ZIP为另一种交付产物，v0.4.0已发布并有解压包验收，不能用Cargo包验证代替运行ZIP验收。

# 九、实际Agent调用与平台能力

Codex已按明确授权通过CLI完成128 MiB自有进程一次整理、内容校验与memory_result回看，见[真实工具验收](<运行记录 Run Record/2026-11-05 安装器与Agent跨平台验收.md>)。自定义工具目录不是MCP；当前不安装另一套适配服务。

Linux候选支持memory_status和memory_targets的真实读取，不支持主动整理。HTTP会话按读取与维护能力分别呈现；调用方不能从读取available推导维护可用。macOS arm64候选已接入真实只读；不提供主动整理。

本轮同构建10769c12f88e0dd2的CLI与内置浏览器各完成一次自有128 MiB负载维护、完整内容校验及回看，见[双入口真实验收](<运行记录 Run Record/2026-11-05 小窗口与双入口真实验收.md>)。这不扩大为其他应用或自动规则的授权，也不代替原生窗口可见体验验收。

# 十、跨平台指标候选合同

0.5.0的physical增加available_kind及可选compressed_bytes；Mac的free_and_inactive_estimate表示空闲与非活跃页估算，不等同Windows可用量。commit的used_bytes/limit_bytes允许null，status为measured/unavailable/partial，调用方不得把null转成0。Rust Memory结构体提交字段改为Option，新口径和压缩字段可由Default初始化；这是相对0.4.0的API变化。Mac仅只读，维护能力为空，不从读取成功推断可以整理。

静态目录新增platform_capabilities，不采样即可识别本平台reading与maintenance。只读平台的维护请求在Application及HTTP前置拒绝，不写入维护意图、不读取维护前快照、不派发系统调用；自动规则不能启用。Mac具体构建和真实验证见[产品只读验收](<运行记录 Run Record/2026-11-05 macOS产品只读验收.md>)。

# 十一、Agent Skill 首次准备与更新

Knowledge 的 cpu-memory-cleanup Skill 只保存指令、元数据和安装说明；所有代码在本仓库 [scripts/agent-skill](../scripts/agent-skill/)。普通用户仅需用户目录中安装的 install-runtime.ps1 及 release.ps1，无需 Git 或源码仓库；每项任务开始时调用初始化副本，默认从官方 latest 解析正式版，校验 Windows x64 附件 SHA256，仅部署轻量 CLI 与许可。可用 -Version 指定正式版本；离线参数为 -ReleaseMetadataPath 与 -ArchivePath，前者来自官方 Release API，后者对应同一发布。

版本、构建和摘要不写死。不同 tag/附件摘要的程序分别部署，保存 install-receipt.json 及文件摘要；复用先核对摘要，再静态核对身份和工具目录。任务内固定返回的可执行路径，新任务再检查更新，不终止旧任务或覆盖旧程序。接口与能力按实际 --tool-info 使用，不要求工具永远只有六项。

初始化与更新不采样、不清理、不启用规则。安装 Skill 不等于持续维护授权，同机命令能力也不等于远程控制。test-runtime.ps1 为开发验证入口，会执行真实只读，不在每次使用时运行。实际证据见[初始化验收](<运行记录 Run Record/2026-11-06 Agent Skill初始化验收.md>)。新增接入只在本地，公开首次初始化还需同步产品代码与 Knowledge。

本地新构建的普通调用默认只返回 stdout，不创建 report.json、timing.json 或运行目录。--no-record 明确禁止保存，与 --output-dir 互斥。仅用户主动要求保存时才传目录，此时仍执行动作前保存和失败阻断，既有网页/桌面也继续显式保存。无记录结果 output_dir 为 null，memory_result 仅用于实际保存过的结果；Run ID 不表示已持久化。初始化返回实际 result_persistence，不再提供诱导普通调用写记录的默认目录。旧正式版没有这项能力，Skill 应拒绝普通维护而不是去掉保证参数。
