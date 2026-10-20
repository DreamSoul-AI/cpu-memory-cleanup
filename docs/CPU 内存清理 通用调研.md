**CPU 内存清理 通用调研**
信息截止与更新日期：2026年10月21日。配套模块与测试方案见 [CPU 内存清理 架构设计](<CPU 内存清理 架构设计.md>)。源码与资料入口见 [项目协作约定](../AGENTS.md)。

# 一、研究目标、范围与技术全景
## （一）核心问题
本项目研究 Windows 上如何诊断并减少 CPU 与内存带来的资源压力。360 安全卫士 PC 端加速球和优化加速是主要产品对照，Windows 官方机制与可读开源实现用于解释技术路线。读者是项目开发者和使用者，输出用于选择方法、设计模块和确定验证方式。

| **问题** | **需要回答** | **主要证据** |
| --- | --- | --- |
| Q1 360 做了什么 | 展示、分类、程序关闭、启动管理分别怎样作用，哪些内部细节未知 | 360 官方产品页、2025隐私政策、历史官方产品说明 |
| Q2 CPU 怎样治理 | 如何采样归因，减工作量、优先级、EcoQoS、配额分别解决什么 | Microsoft CPU/调度文档及公开应用生命周期机制 |
| Q3 内存怎样清理 | 应用释放、工作集、Standby、Modified、提交量的区别和代价 | Microsoft 文档与固定提交的开源动作实现 |
| Q4 当前如何实现 | 职责与接口如何落位，已实现哪些诊断与维护约束，哪些能力仍缺实机证据 | 当前源码、测试、重构前基线及离线假调用核验 |
| Q5 怎样评估 | 指标下降是否改善响应，怎样对照、恢复、测试和阻断不完整结果 | 历史 A/A、A/B，计数器定义与研发规范 |

平台为 Windows 10/11 PC。CPU 研究包含负载治理，不将 CPU 硬件缓存作为清理对象。内存研究包含物理驻留与提交。磁盘垃圾、自启动、服务和应用关闭用于解释竞品及候选技术，本轮不扩展执行能力，不安装运行 360，不采样主机、不结束进程、不变更系统设置。
## （二）技术全景与主要结论
```mermaid
flowchart TB
    D["识别 CPU / 提交 / 驻留 / I/O 瓶颈"]
    W["减少或调节执行：轮询 / 协作暂停 / 限并发"]
    M["改变驻留：工作集裁剪 / Standby 维护"]
    O["由应用释放：缓存 / 去提交 / 正常退出"]
    C["CPU 需求或竞争变化"]
    R["驻留与缺页变化，提交量未必下降"]
    A["业务资源持有可能下降"]
    V["同负载对照：响应 / 吞吐 / 缺页 / 恢复与重建"]

    D -->|工作过多或竞争| W
    D -->|页面机制实验| M
    D -->|持有者可协作| O
    W --> C
    M --> R
    O --> A
    C --> V
    R --> V
    A --> V
```

图1：依据第二至五章一手来源绘制的分析示意，Mermaid 源码直接维护于本文。箭头表示方法到资源变化及复测的关系，逻辑框不代表独立进程，也不代表 360 的内部实现。

CPU 治理应先识别谁在持续执行什么，再减少不必要工作或调整竞争。内存页维护主要改变驻留，应用协作释放有机会减少业务持有资源。360 提供的是多类优化组合，不能仅用“清空 Standby”解释整个产品。当前已实现 CPU 时间差分、覆盖说明、内存分析和显式单项维护的模块边界，实施范围及证据限制见第六章。CPU 治理仍定位于诊断与建议。

收益必须同时看响应、吞吐、恢复和副作用。工作集下降、Free 上升、综合分下降或 CPU 百分比降低，单独都不足以证明用户任务更快。

# 二、360 的产品行为与可确认机制
## （一）产品与版本边界
研究对象是 PC 端 360 安全卫士的加速球及优化加速。安全浏览器、极速浏览器和手机卫士分别是其它产品，下载目录名称也不能用来确定研究对象。未安装或核验当前二进制构建，公开页面按自己的版本与日期使用。

| **产品能力** | **直接来源确认的行为** | **技术含义与未知** |
| --- | --- | --- |
| 资源看板 | 产品页展示 CPU、内存、温度与网速，极速版2022主帖描述看板和入口改版 | 是观察与交互，不直接证明使用某个清页 API。B01、B05 |
| 智能显示值 | 12.0说明将 CPU、内存、I/O、网络按场景组合，可切换内存值 | 2018历史显示契约，权重未公开，不等于内存占用百分比。B04 |
| 清理运行软件 | 2025政策说明加速球可关闭不用的进程，2019教程说明用户选择待结束程序 | 减少后台工作与资源持有；实际关闭 API、是否优雅退出未知。B02、B03 |
| 启动与持续负载管理 | 公开列有软件、自启、服务等管理及优化恢复记录 | 针对后续启动和常驻工作，不是一次页面整理。B01、B02 |
| 页面整理细节 | 上述来源未披露完整算法和调用路径 | 不把 EmptyWorkingSet、全局 NT 命令、申请大块内存等猜测写成 360 已确认实现 |

直接依据：[产品页](https://weishi.360.cn/clear/)、[2025隐私政策第7节](https://weishi.360.cn/privacy.html)、[2019优化教程](https://www.360.cn/n/10778.html)、[12.0智能值说明](https://bbs.360.cn/thread-15621325-1-1.html)、[极速版看板说明](https://bbs.360.cn/thread-16074034-1-1.html)。
## （二）从行为理解实现路径
```mermaid
flowchart LR
    M[监测 CPU、内存等指标] --> C[呈现进程和优化项]
    C --> U[用户选择方案]
    U --> P[关闭选定程序或进程]
    U --> S[启动项等配置管理]
    P --> R[后台执行和资源持有变化]
    S --> F[后续启动和常驻负载变化]
    R --> V[观察多指标与实际体验]
    F --> V
```

图2：根据官方公开行为绘制的产品流程示意，覆盖范围分别由 B01 至 B04 支撑，未复原内部分类算法、结束方式、时序或清页实现。减少后台工作可能降低 CPU，进程退出可能释放其私有资源，具体变化须按负载测量。

当前政策记载优化功能会读取并上传部分启动、服务和任务信息，因此不能笼统沿用旧资料的“所有优化都仅在本地”结论。PC 当前保护白名单、自动选择规则、清页算法和各模式收益仍待版本核验。手机清理忽略名单不能填补 PC 证据缺口。[政策第7节](https://weishi.360.cn/privacy.html)
## （三）本项目可采用的产品思路
采用独立可见指标、诊断说明、候选方案、用户选择、实际结果和复测的闭环。每种动作明确作用范围、前提、权限、可逆性和预期信号。综合评分若无公开规则和校准就不加入首版。程序关闭、启动项、服务变更均保留为技术比较，当前项目不据此执行或新增通用进程清理器。

# 三、CPU 负载治理的方法与实现
## （一）先测量和归因
CPU 是单位时间内执行工作的能力。治理对象是计算需求、唤醒与调度竞争，没有一个对应 RAM 缓存页列表的“CPU 可清空容量”。进程占用先按稳定身份进行同窗测量：两次 `GetProcessTimes` 的内核加用户累计时间差，除以实际间隔。所有线程累计时间可以大于墙钟时间，需分别标注单逻辑处理器等价值和全机归一百分比。首拍、非正间隔、身份变化及读取失败保留未知。[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)

系统 `GetSystemTimes` 的 Kernel 包含 Idle，忙碌比例使用同窗增量 `(ΔKernel + ΔUser - ΔIdle) / (ΔKernel + ΔUser)`，时间单位相同且分母须大于0。超过64逻辑处理器时该 API 只覆盖调用线程所在的主处理器组，未确认覆盖范围不能标为全机。PID 加创建时间避免复用，同名进程不合并为单个所有者。[GetSystemTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemtimes)

CPU 排名提供消耗线索。热点、调用栈、Ready 等待、DPC/ISR 和用户关键路径需要 WPR/WPA 等进一步归因。高占用可能是有用计算，低 CPU 也可能正在等待 I/O，是否需要处理取决于任务和体验。[CPU Analysis](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/cpu-analysis)
## （二）方法比较
| **方法** | **如何实现** | **目标与代价** | **本项目定位** |
| --- | --- | --- | --- |
| 减少实际工作 | 降低无必要轮询、减少重复计算 | 可减少计算需求，但可能影响时效或增加缓存 | 首要诊断与建议路线，应用决定业务取舍 |
| 限制并发与工作节奏 | 减少同时执行任务数、分批调度 | 平滑资源竞争与峰值，不保证总计算量下降 | 应用协作的节奏控制，需复测完成时间 |
| 应用协作暂停/正常退出 | 持有者在可保存、可恢复边界停任务或释放实例 | 同时影响 CPU 与内存，需完整状态契约 | 取得受支持接口前为仅建议 |
| 调低优先级 | SetPriorityClass 改变相对调度顺序 | 可缓解前台竞争，空闲时仍可持续占用 CPU | 可研究的调度选项，不等于减少总工作 |
| EcoQoS | ProcessPowerThrottling 提示非关键工作按节能策略执行 | 影响选核与频率，可能延长后台任务 | Windows 11 的 EcoQoS 需版本准入，函数的 Windows 8 支持不能代替该能力支持 |
| Job CPU 配额 | 受管理 Job 的权重或 CPU 硬上限 | 上限可限制调度时间，代价是吞吐或延迟；既有 Job/嵌套关系要核对 | 只适合项目拥有的受管理任务，不接管任意业务进程 |
| 外部强制挂起 | SuspendThread 停止线程执行 | 可能停在持锁和未保存状态，官方主要定位调试器 | 不作为本项目通用优化路线 |

接口依据：[优先级](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass)、[EcoQoS及版本](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation)、[Job配额](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information)、[线程挂起](https://learn.microsoft.com/en-us/windows/win32/procthread/suspending-thread-execution)。这些方法未在当前项目实现或执行。
## （三）公开应用协作实例
Chromium 页面生命周期将冻结与丢弃分开。冻结限制可冻结队列中的定时器和回调工作，丢弃卸载页面，回来时需要恢复或重新加载。两者分别说明减少工作与释放资源的不同路径，页面持有者必须准备状态。这是公开技术实例，不能据此宣称 360 使用相同策略或当前浏览器工具连接可直接丢弃。[Page Lifecycle API](https://developer.chrome.com/docs/web-platform/page-lifecycle-api)

# 四、内存清理的对象、方法与代价
## （一）指标与机制
`PhysicalAvailable` 为可立即复用的 standby、free、zero 页之和。`CommitTotal` 在提交时变化，驻留与提交分开。容量字段按页大小换算，复合系统缓存不能重复相加。[Microsoft 指标定义](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information)

工作集描述当前驻留的可分页页。共享页面不能跨进程简单求和。移出工作集后的页面可能继续缓存在 RAM，脏页在复用前要回写。重新访问是否需要读盘取决于页面状态。[Microsoft 工作集说明](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)

| **路线** | **公开契约与操作范围** | **代价及采用条件** |
| --- | --- | --- |
| PSAPI/PDH 只读观察 | 读取主机容量与速率，当前已有实现 | 有采样时差与读取缺口，不提供工具所有权 |
| `EmptyWorkingSet` | 有公开文档，指定进程句柄需要查询权限与 `PROCESS_SET_QUOTA` | 是独立的逐进程路线，不能用它证明全局 NT 命令的权限或支持范围。本项目不新增逐 PID 释放 |
| 全局内存列表命令 | 当前调用类80及命令2、4，需要按构建和特权验证 | 对整个系统页面列表生效，没有业务进程排除保证，保留为显式实验后端 |
| 宿主正常释放连接 | 根据业务引用和正常关闭契约回收工具 | 需要宿主证据及接口，不由 Windows 内存列表代替 |

`NtSetSystemInformation` 函数本身存在 Microsoft 官方说明。该说明没有给出 `SystemMemoryListInformation=80`、命令2和4的稳定支持契约，因此准确边界是“函数有文档，当前内存列表信息类与命令不具备该页给出的 SDK 支持保证”。不能笼统称整个函数完全未文档化，也不能将代码注释中的“publicly documented commands”作为 ABI 依据。[函数说明](https://learn.microsoft.com/en-us/windows/win32/sysinfo/ntsetsysteminformation)、[逐进程接口](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-emptyworkingset)
## （二）应用释放与页面维护的差异
应用知道对象、任务和缓存是否还会使用。释放应用对象后，分配器可能保留内存池，因此系统提交量不保证立即下降。持有者调用 `VirtualFree(MEM_DECOMMIT)` 可明确把指定已提交页面变回保留状态，原数据丢失，访问前需重新提交。它不是外部工具扫描到一个 PID 后就能安全替应用执行的通用清理。[VirtualFree](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualfree)

以下是依据 Microsoft 页面状态说明绘制的分析示意，表示保留内容页从所有工作集移出后的典型简化路径。共享页仅从一个工作集移出时，可能仍驻留在其它工作集。箭头是状态变化，不是独立进程，也不代表 360 内部调用：

```mermaid
flowchart LR
    WS[工作集内的可分页页] -->|裁剪干净页| SB[Standby 缓存]
    WS -->|裁剪脏页| MD[Modified 脏页]
    MD -->|写回后可复用| SB
    SB -->|尚在 RAM 时重新引用：软缺页| WS
    SB -->|被复用或清除| RE[可用于其它需求的物理页]
    BK[后备存储] -->|需要后备存储内容时读入：硬缺页| WS
```

`Available` 已包括 Standby、Free 和 Zero。修剪会改变驻留状态，提交与地址空间另有状态体系，不能叠画为“释放同一种内存”。Standby 转成 Free 时，可用内存通常不会按 Free 的增量同幅增加。Modified 写回会产生 I/O，之后重新访问也可能付出缺页成本。[工作集](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)、[容量口径](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information)
## （三）权限与并发
`AdjustTokenPrivileges` 只能调整令牌已持有的特权，BOOL 非零仍须检查 `ERROR_NOT_ALL_ASSIGNED`，原状态可通过 `PreviousState` 保存并恢复。[官方返回与恢复语义](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-adjusttokenprivileges)

据此实施项目约束：启用失败零 NT 动作，工具临时启用后尝试恢复，原先已启用则保持原状。恢复失败与动作结果分别保留。GUI 与 CLI 共用核心维护入口，Windows 接入包含进程内互斥与会话级命名互斥，未确认恢复或资源关闭时拒绝后续系统流程。这些是源码及假接入验证范围，真实权限恢复与跨实例行为尚未实机验证。当前不承诺跨会话互斥或持久化冷却。
## （四）计数器、因果与收益
PDH 速率通常需要至少两次采集，首拍未知不能补0，记录实际间隔。进程实例名可能变化，当前逐进程 CPU 比较使用 PID 与创建时间，名称用于展示。[采集流程](https://learn.microsoft.com/en-us/windows/win32/perfctrs/collecting-performance-data)

`Page Reads/sec` 为满足硬缺页的读操作数，`Pages Input/sec` 为读入页数。全盘读写还包含普通文件及程序活动，无法单独归因到清理。旧比较器的0.5 GiB、1.5倍和绝对底数均是本项目提案，不是 Windows 的普适性能阈值。[计数器定义](https://learn.microsoft.com/en-us/biztalk/technical-guides/system-level-bottlenecks)、[磁盘计数器定义](https://learn.microsoft.com/en-us/previous-versions/aa394308(v=vs.85))

收益验证应采用同负载 A/A 噪声参照、分机制 A/B、恢复窗口及应用吞吐/延迟，保留每次动作与窗口完整性。仅 standby→free、瞬时容量变高、或全盘 I/O 变化均不足以证明加速。未测业务指标时结论收窄到主机侧观察。

# 五、现有工具与可见实现比较
## （一）按同一任务比较
| **对象** | **主路径与接口可见性** | **可借鉴内容** | **采用限制** |
| --- | --- | --- | --- |
| Windows 自身内存管理 | 根据页面访问与压力管理工作集和缓存，机制文档公开 | 保留正常管理作为对照，先确认是否真有压力 | 无需默认增加定时清空操作 |
| 360 安全卫士 PC | 监测、分类、选择运行软件、启动管理，具体底层未公开 | 产品闭环、作用对象、记录与恢复呈现 | 不推断同一清页 API，不直接复制关闭/系统设置功能 |
| Mem Reduct | 固定源码可见多种页面维护动作及自动选择规则 | 机制分开、按选项调用、记录调用失败状态 | NT 路线须独立核验，不复制自动或高权限路径 |
| System Informer | 固定源码将工作集、Modified、Standby等作为独立操作 | 原始状态码与不同动作边界 | 不照搬服务提权回退，工具实现不等于稳定系统契约 |
| 应用自身生命周期 | 正常退出、缓存释放、冻结/丢弃等由持有者协调 | 业务状态、所有权和可恢复性 | 接口与版本逐应用核查，不能由外部低 CPU 推断 |

本表比较职责和技术路径，不对未经同机同负载实测的产品排列性能名次。
## （二）开源程序大概怎样做
Mem Reduct 在 `_app_memoryclean` 中先按清理选项和运行模式筛选，再分别准备信息结构或枚举值，调用对应系统接口并处理状态。工作集、文件缓存、Modified、Standby、低优先级 Standby 和内存合并是独立分支。其自动模式默认剔除源码标记易产生停顿的 Standby/Modified 动作，用户可通过 `IsAllowStandbyListCleanup` 显式允许，不把所有选项无条件打包。实际阅读定位为固定提交 `04402de` 的315至319、390至466行。[源码](https://github.com/henrypp/memreduct/blob/04402de8b6a8ff9d1c5517c675d87c346655323c/src/main.c#L315)

System Informer 用统一的小函数传入不同内存列表命令，封装工作集、Modified、Standby和低优先级 Standby，分别呈现 NTSTATUS。其源码还包含提权服务回退，本项目不采用。实际阅读定位为提交 `2e1349b` 的196至299行。[源码](https://github.com/winsiderss/systeminformer/blob/2e1349b065e5cf187db390697062fc95c846de33/SystemInformer/memlists.c#L196)

这些源码解释了常见“内存整理”工具如何落到系统调用，但不能证明 360 采用相同实现。依照产品资料，本项目先把“减少后台工作”“持有者释放”“改变页面驻留”建成不同的诊断与方案类型，再决定是否有足够能力执行。

# 六、当前实现、缺口与验证输入
## （一）当前实施进展与证据状态
2026年10月21日按架构与代码结构设计完成模块落位。产品包维护在 src/cpu_memory_cleanup/，对应镜像测试在 tests/src/cpu_memory_cleanup/，脚本及其测试保留根目录布局。产品包现有8个 Python 文件：包标识、装配入口、共享合同、用例、规则、Windows 接入、CLI 和桌面交互。文件职责与依赖以 [代码结构设计](<CPU 内存清理 代码结构设计.md>) 为准。

| **实施范围** | **当前代码行为** | **证据边界** |
| --- | --- | --- |
| CPU 与内存分析 | 以 PID 和创建时间计算进程累计 CPU 差，分开单核与全机口径。首拍、处理器组及读取缺口保留未知 | 源码与合成样本验证，真实硬件与处理器组覆盖未验证 |
| 有界观察与离线回看 | `inspect` 保存版本化样本及条件，`review` 保留原分析或明确重算。读取进程全集后再按 `top` 排名，不截断 CPU 分析输入 | 历史文件与假 Reader 路径，未启动真实采样 |
| 单次维护与结果事实 | 明确确认、单一机制、保存意图后至多一次派发。调用、恢复、记录与整体完成度分开，后置失败保留已发生事实 | 假 Maintainer、假 DLL、权限与文件故障注入，不证明真实释放成功或有收益 |
| 入口与生命周期 | CLI 和 GUI 使用同一核心，Tk 按需加载，工作结果由主线程呈现。关闭先停止接单并请求取消，等待已派发工作收尾 | 假 Tk 与可控线程验证，未打开真实界面 |
| 测试与运行取证 | 测试按源码与脚本镜像组织，元数据、计划和逐项结果通过统一入口管理，诊断使用标准库日志 | 执行范围、实际回执与排除项统一见 [测试说明](../tests/README.md)，本文不另记易失效的测试总数 |

当前不采集旧 PowerShell 路线的 owner 归属、Codex 子树与成员年龄桶。旧记录中的这些字段可离线回看，新采样不能冒充旧进程树序列的等价替代。跨会话协调、真实 Windows ABI、采样、GUI、权限启用与恢复，以及用户任务收益均未完成实机验证。本轮产品测试使用假接入或静态检查，其通过不能代替这些证据。
src 布局采用根目录 pyproject.toml 配置包发现和可编辑安装，不增加产品运行依赖。迁移前回执保留当时的源码路径与测试标识，当前布局的验证范围及新回执见 [测试说明](../tests/README.md)；本次路径调整不重新验证或改写历史实验与固定提交证据。
## （二）重构前基线
以下状态对应重构前的 [6938f10 基线](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d)。当时有13个 `.py` 模块，入口、系统调用、编排和展示之间仍有交叉依赖。`release.py` 同时包含 NT 绑定、权限、快照、动作和报告，`abprotocol.py` 混合计划、文件与执行，尚无 CPU 时间采样与完整诊断链。该结构已被本章第一节的实现替代。

当时静态读取12个平铺测试文件，共131个 `test_` 方法。这是重构前源码计数，不是当前执行、收集或通过数。假 NT、假特权调整器、假时钟与临时文件的方法在新测试中继续使用。

| **规范要求** | **重构前实际状态** | **采用的设计方向** |
| --- | --- | --- |
| 测试完整镜像源码骨架 | tests 平铺，Windows 与 CLI 测试混在 inspection，脚本混在同文件 | 镜像包与脚本，按入口落位集成/E2E，用标签表达层级 |
| 五维声明与独立参数结果 | 没有统一元数据与计划，循环/subTest 常为方法级汇总 | 一份清单管理元数据与参数 ID，保留 unittest |
| 逐用例持久化与可重建报告 | 主要依赖终端输出，没有统一 manifest/events/report | 增加最小标准库运行入口和结果适配 |
| 数值、分类、执行状态分离 | A/B 动作成功和收益混用，测试尚无统一结果协议 | numeric 无质量阈值时为 measured、verdict=null |
| 日志不作为业务状态权威 | 异常路径会丢失动作事实 | 分开业务动作记录、测量数据和诊断日志 |
| 验证追溯到提交、目标基线及产物 | 没有已配置 workflow 和统一计划入口 | 先本地统一入口，CI 后续复用，不自动启用 Actions |

Python 3.10 `unittest.TestResult` 提供结果回调，`addSubTest` 可取得参数结果，但默认成功子测试不写独立报告。利用这些平台能力增加适配比更换框架更小。[Python 官方文档](https://docs.python.org/3.10/library/unittest.html)
## （三）日志与业务记录的边界
日志规范要求正常日志有运行和来源字段，未知可选字段省略，结果确认后再记录，敏感内容在源头排除。日志写不出不能改变已发生业务事实。[日志规范](https://dreamsoul.feishu.cn/docx/RBlFdcSSBo93q9x6p8kcdU6wnYb)

因此三种文件不混用：测量数据保存未知指标和采样缺口，动作记录保存意图及真实执行结果，诊断日志解释事件。前两者是业务数据，其关键持久化失败可阻止后续动作。普通诊断 sink 故障采取应急说明与受限退化，但不能把已完成 NT 调用改成“未执行”。详细数据协议见架构设计的记录与结果章节。
## （四）重构前离线核验与修复输入
P01 至 P04 是重构前旧实现的离线核验，对应 C01 与上述6938f10时代的行为。核验使用合成输入、假调用及当时的纯解析函数，没有调用真实系统接口。以下保留原观测结果，当前实现已按这些故障输入重构，不将其列为仍未修复的现状：

| **编号** | **重构前输入或路径** | **当时实际结果** | **设计影响** |
| --- | --- | --- | --- |
| P01 | both 中命令2返回拒绝，命令4假调用成功 | 两项都调用，`cleanup_performed=true` | 首项失败停止，完整成功不能用任一成功代替 |
| P02 | 假 NT 成功，后快照抛 OSError，经过 CLI 格式化 | JSON 显示未清理，捕获消息称操作前失败 | 保存动作事实，后置失败独立表示 |
| P03 | JSONL 的速率值为 NaN，经过 parse/extract | 非有限数进入指标 | 在输入边界拒绝非有限、布尔和错误形状 |
| P04 | A/B 预检返回空对象 | 当时 guard 通过 | 必需能力必须显式为真，缺字段即未知/拒绝 |

重构前静态检查还发现特权启用错误仍进入 body、恢复失败只给 warning、A/B 释放后才打开 B 窗日志，以及旧用例保护“首项异常后继续第二项”。当前组合机制已拒绝，空预检与非有限数被拒绝，动作事实在后置失败时保留，恢复失败阻止后续系统流程。旧 A/B 自动实验入口已停用，不能把它的历史行为当作新维护流程。

# 七、历史主机观察与验证边界
以下结果继承仓库旧验证记录，原始 `logs/` 已在21日按指令删除。既有记录可供研究取舍，当前无法直接复算。原始数据缺失不被补造或用新样本冒充。（来源 H01）

以下均为日常桌面场景，无训练或评测负载。容量单位 GiB，日期与时间为北京时间。历史 `PrivatePageCount` 统计的是私有提交量，不能解释为驻留量。

## （一）基础测试和压力观察
| **日期与窗口** | **方法** | **历史结果与限制** |
| --- | --- | --- |
| 10月17日 21:53 | 一次性只读查询，9 个单元测试 | 总内存 47.113、可用 18.087、提交 56.895/95.113。498 个进程中可读 258 个。查询排名不完整 |
| 10月18日 06:35 | 2 拍、间隔 1 秒。15 个测试 | 可用 2.549→2.679，提交 66.518→66.392。只验证启动、停止与速率输出，不是性能基线 |
| 10月18日 20:09至20:24 | 90 拍、间隔 10 秒，空闲桌面 | 全窗可用中位数 27.144、提交 46.463、standby 18.536。最初阶段不稳定，约20:11至20:14指标发生明显变化，触发原因未识别 |
| 上述后 60 拍 | 同一空闲窗较稳部分 | 可用中位数 27.151、提交 46.445。不是工具收益 |
| 10月18日 21:59至22:14 | 90 拍、间隔 10 秒，启用进程排名 | 可用中位数 8.226、最低 6.366。提交中位数 64.699。standby 6.727。读入页速率中位数 52.501、95分位约5187，有短时突发，未观察到持续换页 |
| 10月18日 22:19 | 单点进程与池计数交叉核对 | 1012 个进程私有提交合计约48.1，分页与非分页池约6.7，约9.7提交量仍未归属。不能据此指定清理目标 |
| 10月18日 22:30至22:59 | 30 拍、间隔60秒，进程树聚合 | Codex 子树401至593个进程、私有提交7.94至10.69。新生407、退出298，曲线非单调。否定此前单点“累积而不回收”的猜测 |

桌面压力排名按每个名称的最大单实例统计，不叠加同名实例：codex.exe 最大实例中位数1.813 GiB、ChatGPT.exe 1.592、T'OWEI.exe 0.585、blender.exe 0.558（仅10拍进入前十）、360se.exe 0.542。共享页不能相加，前十排名也不能解释全部物理占用。

## （二）年龄分桶与提交残差
- 10月18日23:12至19日01:11的120拍序列中，Codex 私有提交12.50降至4.46。多数成员较年轻，观测到部分成员退出，退出原因未核实。超过3小时的成员确实存在，峰值12个 `server.mjs`、6个 `cua_repl`、18个 `node_repl`，合计私有提交最高约1.063 GiB。末拍约0.71 GiB。没有逐进程身份，不能声称始终是同一批幸存者
- 19日00:04:56至02:03:57的120拍提交残差序列中，残差8.466至10.232 GiB，约占提交14.1%至16.4%。与进程数相关系数0.487。进程私有提交与主机提交相关系数0.994。残差由减法推算，不是可回收内存
- 该窗01:22至01:45，WMI 可用物理量下降约4.524 GiB，提交增加5.485 GiB，进程私有提交增加5.151 GiB，残差只增0.227。主要变化来自 ChatGPT 所有者树，其中 Codex 子树增加约3.184 GiB，以年轻 Node 成员为主。不能据此推定某个聊天或审批审查的所有权
- 19日01:42至01:46出现5拍低于4 GiB 的压力事件，随后自行恢复。4 GiB、85%、连续3拍是当时提案阈值，未完成通用校准。WMI 的 `FreePhysicalMemory` 与其它窗口 PDH 的 Available 分开记录，不直接混用
- 未归属提交的组成仍未知。历史 GPU 共享系统内存约0.279 GiB，不能解释全部残差。共享节、页表、内核栈等只是候选解释，未运行 RAMMap 交叉验证

## （三）A/A 噪声校准与 A/B 结果
19日00:24至00:33的无释放 A/A 中，一次自然突发就使原始规则误报负面代价。因此历史比较器改用同次实验至少两个 A/A 对的逐指标超限比例上界作为噪声参照。规则是工程提案，不能提供因果保证。

三次完成的实验都采用 `AA,AB,AA,AB`，8窗，每窗30拍、间隔10秒。只在每个 AB 的 B 窗开始前释放一次。

| **时间** | **机制** | **关键结果** | **历史判定** |
| --- | --- | --- | --- |
| 19日05:20:44至05:59:40 | 工作集与 standby 合并 | 两个 AB 的可用尾段中位数分别增加21.59、5.05 GiB。提交中位数变化+0.45、−0.16。硬缺页与磁盘读超噪声上界。合并调用不能直接分离两种机制的因果贡献 | 两对均为“仅数字改善或有负面代价” |
| 19日06:16:53至06:55:37 | 仅 standby | 即时可用仅增加0.24、0.30 GiB。提交中位数变化+0.33、+1.74。可用尾段变化−0.33、−1.49。硬缺页超过噪声上界 | 两对均为“仅数字改善或有负面代价” |
| 20日02:04:20至02:43:07 | 仅工作集 | modified 约增加27、12 GiB。单拍磁盘写约2732、1059 MB/s。提交中位数变化+1.38、−0.48。可用尾段增加24.82、5.14，硬缺页仍超上界 | 两对均为“仅数字改善或有负面代价” |

19日05:04开始的合并实验在首个 A 窗中断，未释放。19日07:13的工作集实验实际释放一次后因关机中断，B 窗仅14/30拍，不作完整判定。不能把中断实验当成完成实验。

已完成实验每次释放前特权都已启用，`enabled_by_tool` 为假。“已持有但未启用→临时启用→恢复关闭”的真实路径没有实测。历史单元测试记录增长到122项，不代表本轮重新通过。当前测试数量及结果需实际运行后另记。

# 八、应用协作回收案例：Codex 工具实例
## （一）已有证据能证明什么
DreamSoul《Codex 闲置工具进程安全回收调研》研究日期为2026年10月19日，桌面版本26.1013.40934、构建20770。它定位了部分 OS 进程身份、父链、桌面退订器、执行记录与强制中断代码，未验证本版 app-server 的 Rust transport 析构实现。内部报告定位见 N07。

原始台账30条中24条在历史采样匹配、6条未见。24条的连接 ID、owner、共享者和请求字段仍未知，正常释放入口未核实。限定生命周期元数据含9条退订事件。21日对这些存量文件的复核只证明其历史内容，没有读取当前进程或其它聊天载荷。

| **证据** | **可支持** | **不能支持** |
| --- | --- | --- |
| PID＋精确创建时间＋有效父链 | 匹配采样对象与宿主身份 | 当前存活、独占、无使用者 |
| 同一 PID 的短窗 CPU 差为0 | 当时短窗未观察到累计 CPU 增长 | 工具闲置、无等待或可关闭 |
| `active_execs` 正向匹配 | 部分执行与进程标识关联 | 空目录代表全部请求为空 |
| 退订事件和 `turn/completed` | 对应聊天或回合的生命周期事实 | 指定连接已关闭、业务状态已保存 |
| 文件已落盘 | 该文件的持久化事实 | JS 堆、Promise、浏览器控制权与下载过程均可恢复 |

## （二）公开协议的适用边界
21日重新针对性阅读 OpenAI 官方 App Server：`thread/unsubscribe` 作用于当前连接订阅，最后订阅者离开后经过无订阅且无活动的宽限期可卸载聊天。`mcpServerStatus/list` 可带 `threadId` 复用聊天连接。这些是聊天与状态读取契约，不能推导为任意工具 PID 的正常退出接口。[官方协议](https://learn.chatgpt.com/docs/app-server)

配置参考中的 `startup_timeout_sec` 与 `tool_timeout_sec` 分别限定启动与单次调用，不能解释为空闲 TTL。其是否适用于内置运行时需要单独核验。[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)

历史桌面3小时退订选择条件与服务端宽限期处于不同状态层。公开滚动协议与旧安装包实现分别成立，不能相加承诺某进程固定退出时间。没有取得本机构建的完整连接级受支持释放能力时，保持“证据不足，保留实例”。
## （三）目标契约及研究停止点
宿主应提供实例 ID、代次、PID 与创建时间、全部消费者、活跃/排队/挂起/待返回集合、保存状态和正常关闭确认。释放由同一个连接所有者在停止接入的协调范围内复核，共享实例只能释放当前使用者引用。

这是基于缺口的目标要求，不是假定已存在的 API。缺少能力时不实现外部注册表来猜所有权，不伪造通知、不调用强制中断，也不把自建假宿主测试称为真实 E2E。下一步首先是核对接口与版本，不是选择历史 PID 做试验。

# 九、方案取舍与后续验证
## （一）架构输入
| **决策** | **依据** | **目标与验收信号** |
| --- | --- | --- |
| D01 从驱动力推导最少边界 | Q4、产品行为、M01至M03 | 质量场景对应状态权威、黑盒合同与可检查源码依赖，旧代码框架不约束设计 |
| D02 唯一动作入口保留事实 | P01/P02/P04、权限契约 | 前置失败零动作，首错停止，后置失败不抹调用结果 |
| D03 三类记录语义分别管理 | 日志规范 | 测量与动作由流程保存，诊断复用 logging，区分权威与失败语义，不各建存储子系统 |
| D04 对照数据先准入 | Q5、P03、历史窗口 | 少拍、混 run、部分成功和未校准不支持收益 |
| D05 测试跟随模块与旅程 | 测试规范、现有 fake | CPU/策略/共享入口优先覆盖，元数据与逐项结果可定位 |
| D06 本地验证先行 | CI/CD规范 | 关联实际提交和产物，必需项未完成阻断 |
| D07 应用协作能力核验 | Q2/Q3、Codex证据缺口 | 无受支持接口不关闭实例，保留共享引用和状态 |
| D08 CPU 与内存规则分开 | Q2/Q3及机制比较 | 同一分析模块内部按问题处理，CPU 问题不自动触发清页，方法能力状态可见 |
| D09 以360产品闭环作主要对照 | Q1、B01至B05 | 观察、解释、选择、结果与效果可追溯，不把每个步骤都提升为独立模块，内部未知不补造 |

## （二）待验证事项
360 当前构建的进程关闭方式、内存整理 API、保护名单、综合评分权重和净收益尚未知。若继续做版本验证，应先固定产品、构建、模式和受控无业务状态环境，区分“只整理页面”和“确实退出进程”，记录 CPU 时间、Commit、工作集、Available、缺页及任务响应。当前没有安装或执行授权，本研究不启动该实验。

Windows 内部内存列表命令兼容性、真实权限恢复、跨会话协调、CPU 处理器组覆盖和应用动作接口仍待实机或接口核验。历史 A/B 无原始 logs，不能复算。模块化、CPU 差分、维护约束与统一测试入口已按第六章实施，应用协作治理和真实收益仍缺证据。后续验证须分别覆盖平台调用合同、资源恢复和同负载用户任务表现。

## （三）软件架构方法的采用
2026年10月21日读取项目关联DreamSoul文档库的软件架构设计课题。将其方法用于这个小型工具，而不把案例中的智能体运行时、模型路由或服务拆分带入产品。

| **资料方法与定位** | **本项目采用** | **检查方式** |
| --- | --- | --- |
| 主调研第十一章：从场景和质量目标出发 | 默认只读、结果真实、离线回看、接入替换和取消五个场景 | 对应架构Q01至Q05，不以笼统质量口号验收 |
| 第三、十三章：黑盒与行为合同 | 明确用例状态权威、Reader/Maintainer范围、错误、并发和资源责任 | 核对真实与假实现是否满足同一语义 |
| 第四、十四章：多视图分工 | 上下文、源码依赖、运行时序/状态和简短部署视图 | 不把运行箭头当import，不把模块当进程或权限沙箱 |
| 第十五章及案例第四至八章：设计到验证 | 目标代码集合、依赖规则和关键异常/变更场景 | 分开图语法、源码结构、合同及场景验证 |
| 第十七、十八章：简约和债务 | 本文保留少量决定、代价与升级触发条件 | 无工时/频率证据不计算虚假技术债，不以模块数量评价架构 |

这些是对本地研究的裁剪应用，不能称为重新精读全部原始标准或完成正式方法评估。具体来源与阅读范围见M01至M03，行为与约束维护于架构设计，文件落位维护于 [CPU 内存清理 代码结构设计](<CPU 内存清理 代码结构设计.md>)。

# 十、依据、方法与证据分级
## （一）规范基线
从当前项目关联的 DreamSoul 文档库读取实际文件，再沿其既有分享链接核对。路径相对于实际定位的文档库，不固定个人机器目录。

| **依据** | **读取范围与状态** | **用于本研究的要求** |
| --- | --- | --- |
| 通用调研规范 | 本地全文，飞书修订号2正文已读取 | 问题先行、直接来源、阅读深度、版本、比较轴和验证边界 |
| 飞书文档规范 | 本地全文，飞书修订号38正文已读取 | 中文混排、正文一级章节、表头加粗、就近引用、删除聊天痕迹 |
| 代码规范 | 本地与飞书修订号3全文 | 复用现有层次、最小防御、输入与持久化边界、清晰失败 |
| 测试规范 代码 | 本地921行全文，云端修订号53目录核对，未逐字比较云地全文 | 五维、目录镜像、稳定用例 ID、计划、状态协议、结果持久化 |
| CI CD 执行规范 | 本地全文，云端修订号30目录核对，未逐字比较云地全文 | 验证与提交/产物关联，必需项放行，允许本地验证策略 |
| 日志规范 | 本地全文，云端链接由现行总索引核对，未重新读完整云端正文 | 日志不作业务权威、未知可选字段省略、结果确认后记录、隐私与故障隔离 |

规范正文与入口见参考资料 N01 至 N06。目录核对不代表云地内容完全一致，也不代表当前程序已符合规范。
## （二）研究方法与停止条件
按“对象和状态 → 读取或调用路径 → 完成与异常 → 可观察证据 → 架构及测试约束”分析。先读取 DreamSoul 相关成果和本仓库历史研究，再按产品行为、CPU、内存及开源实现分别核查 360、Microsoft、Chromium 与固定提交源码。Codex 回收作为应用协作的一个案例。直接来源未覆盖的部分保留未知。

阅读与验证深度分为：公开文档规定、当前源码行为、历史实测摘要、离线假调用复核、工程建议。网页为滚动版本时记录访问日，不用抓取日冒充发布日期。主机和宿主构建未重新采集，历史版本均带日期。

同类实现按产品治理、页面维护、应用协作三条路线比较，以 360 为主要产品对照，以固定提交的 Mem Reduct 和 System Informer 解释可见实现。核心机制、失败路径和工程约束已足以支持设计时停止补查。宿主所有权与真实收益缺口需要新的受支持接口或另获授权的实验，不能靠增加阅读数量补齐。

# 十一、参考资料与定位
所有新外部阅读访问日期为2026年10月21日。Microsoft、OpenAI 及仓库滚动分支未固定文档提交，不能保证后续不变。规范和 DreamSoul 资料从项目关联文档库按名称及下列逻辑位置定位，避免个人盘符成为共享依赖。

| **编号** | **来源及定位** | **版本/日期、阅读范围与用途** |
| --- | --- | --- |
| M01 | DreamSoul文档库 `研发/研究/软件开发 Software Engineering/软件架构设计 Software Architecture Design/软件架构设计 Software Architecture Design 通用调研.md` | 2026年10月21日，本地主报告正文，重点第三至六、十一至十五、十七至十八章；驱动力、多视图、合同、验证与简约 |
| M02 | 同目录 `软件架构设计 Software Architecture Design 端侧智能体案例.md` | 同日，本地案例正文，重点第四至九章；状态、取消/恢复、部署与决策，非本项目实现证据 |
| M03 | 同目录 `软件架构设计 Software Architecture Design 来源索引.md`及`通用调研 大纲.md` | 同日，核对分类与相关条目阅读范围；原标准/论文未在本轮逐项重读 |
| N01 | [通用调研规范](https://dreamsoul.feishu.cn/docx/YcWIdE4INoLqoLxR5O8cbl7hnTf) | 修订号2，本地与云端全文，问题定义、来源、比较及验证边界 |
| N02 | [飞书文档规范](https://dreamsoul.feishu.cn/docx/DdmxdrNvBo9AMXxkAYmc4EJ6nwe) | 修订号38，本地与云端全文，正式文档结构与引用 |
| N03 | [代码规范](https://dreamsoul.feishu.cn/docx/FKrsdHO9bolq4YxV2KAcosghnme) | 修订号3，本地与云端全文，简单结构、输入和持久化边界 |
| N04 | [测试规范 代码](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf) | 本地全文，云端目录修订号53。重点第一至五章，含执行和状态协议 |
| N05 | [CI CD 执行规范](https://dreamsoul.feishu.cn/docx/NrJ4d0WVGo9t4uxAVwScFTBVnRb) | 本地全文，云端目录修订号30。重点总体流程、验证与产物追溯 |
| N06 | [日志规范](https://dreamsoul.feishu.cn/docx/RBlFdcSSBo93q9x6p8kcdU6wnYb) | 本地全文、现行索引链接核对，重点字段、结果事实、隐私与故障隔离 |
| N07 | DreamSoul 文档库 `研发/研究/软件开发 Software Engineering/Codex 闲置工具进程安全回收调研.md` | 19日研究，21日相关章节复读。代码清单、台账及元数据按报告原相对链接定位 |
| N08 | DreamSoul 文档库 `tmp/node-runtime-audit-20261019/` 的 `candidate-direct-evidence-20261019-014800.json`、`host-lifecycle-metadata-20261019-014500.json` | 历史身份与限定事件，不代表当前状态。已核对其字段和计数 |
| W01 | [PERFORMANCE_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information) | 页面更新2024年2月22日，容量成员与换算 |
| W02 | [Working Set](https://learn.microsoft.com/en-us/windows/win32/memory/working-set) | 页面更新2021年6月4日，驻留、共享、软硬缺页及脏页路径 |
| W03 | [EmptyWorkingSet](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-emptyworkingset) | 页面更新2022年8月23日，参数、权限与返回值 |
| W04 | [NtSetSystemInformation](https://learn.microsoft.com/en-us/windows/win32/sysinfo/ntsetsysteminformation) | 页面更新2023年2月16日，函数级契约与信息类子集限制 |
| W05 | [AdjustTokenPrivileges](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-adjusttokenprivileges) | 页面更新2021年10月13日，BOOL 与 LastError、持有范围及 PreviousState |
| W06 | [Collecting Performance Data](https://learn.microsoft.com/en-us/windows/win32/perfctrs/collecting-performance-data) | 页面更新2025年7月14日，两次采集及进程实例警告 |
| W07 | [System-Level Bottlenecks](https://learn.microsoft.com/en-us/biztalk/technical-guides/system-level-bottlenecks)、[PhysicalDisk 定义](https://learn.microsoft.com/en-us/previous-versions/aa394308(v=vs.85)) | 针对性读计数器定义，未采用其业务特定阈值 |
| W08 | [Kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces)、[CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw) | 针对性读会话与全局命名空间、互斥创建和访问规则；不能用用户本地状态承诺跨用户冷却 |
| O01 | [OpenAI App Server](https://learn.chatgpt.com/docs/app-server)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference) | 滚动文档，仅读订阅、MCP 状态及启动/调用超时相关条款，不证明本机构建内部实现 |
| T01 | [Python 3.10 unittest](https://docs.python.org/3.10/library/unittest.html) | TestResult 与 addSubTest，采用已在项目最低版本存在的能力，不升级依赖 |
| C01 | [重构前核验源码](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/a0afe76168d2d649b44d13e0cc74a02358c3c9c3)、[重构前合并基线6938f10](https://github.com/DreamSoul-AI/cpu-memory-cleanup/tree/6938f10447b250958856a845fc5448aa1185b52d) | 21日旧源码与测试静态分析、P01 至 P04 离线复核。两者运行逻辑相同，属于本轮重构之前的证据，真实系统边界全部使用假调用 |
| H01 | [旧验证正文](https://github.com/DreamSoul-AI/cpu-memory-cleanup/blob/00dbcb20f78fe9895121b25450b0c411527dbdc1/docs/validation.md) | 旧 Git 提交可复查文字，原始 logs 已删除，不代表可复算实验 |

开源来源现已固定到下列提交，针对性读取动作及自动筛选实现。未审查完整程序、完整许可证链、构建或驱动，不复制代码或服务提权路径。公开源码不能证明 Windows 内部 ABI 稳定，也不能反推 360 内部实现。

| **编号** | **新增来源及定位** | **日期、阅读范围与证据边界** |
| --- | --- | --- |
| B01 | [360 清理加速产品页](https://weishi.360.cn/clear/) | 访问2026年10月21日，产品功能段全文，页面未标具体构建号，不用宣传文案作性能测量 |
| B02 | [360 安全卫士隐私政策](https://weishi.360.cn/privacy.html) | 更新/生效2025年9月8日，针对性读第3、5、7节，确认进程关闭、启动项管理与数据处理范围 |
| B03 | [电脑卡慢优化方案](https://www.360.cn/n/10778.html) | 2019年7月23日，针对性读程序选择、进程结束和自启段落，属于历史产品说明 |
| B04 | [12.0 智能数值说明](https://bbs.360.cn/thread-15621325-1-1.html) | 2018年11月12日，编辑11月30日，主帖全文，确认指标组成，不提供算法权重 |
| B05 | [极速版加速球说明](https://bbs.360.cn/thread-16074034-1-1.html) | 2022年12月23日，主帖全文，界面与看板变化，不提供底层动作证据 |
| S01 | [Mem Reduct main.c](https://github.com/henrypp/memreduct/blob/04402de8b6a8ff9d1c5517c675d87c346655323c/src/main.c#L270) | 提交04402de，2026年10月7日，读取270至466行相关动作/筛选及 [main.h](https://github.com/henrypp/memreduct/blob/04402de8b6a8ff9d1c5517c675d87c346655323c/src/main.h)，未构建或运行 |
| S02 | [System Informer memlists.c](https://github.com/winsiderss/systeminformer/blob/2e1349b065e5cf187db390697062fc95c846de33/SystemInformer/memlists.c#L196) | 提交2e1349b，2026年10月20日，读取196至299行系统调用和包装，未构建或运行 |
| F01 | [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes) | 更新2022年11月1日，累计线程时间、创建时间和多核口径 |
| F02 | [GetSystemTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemtimes) | 更新2025年8月15日，Kernel 含 Idle 及处理器组限制 |
| F03 | [CPU Analysis](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/cpu-analysis) | 更新2021年3月25日，CPU 热点、关键路径、Ready 与归因方法 |
| F04 | [SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass) | 更新2022年11月1日，相对优先级、后台模式及访问边界 |
| F05 | [SetProcessInformation](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation) | 更新2024年10月31日，ProcessPowerThrottling 与 EcoQoS 版本边界 |
| F06 | [Job CPU rate control](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information) | 更新2021年4月2日，权重、硬上限、版本和 DFSS 限制 |
| F07 | [Suspending Thread Execution](https://learn.microsoft.com/en-us/windows/win32/procthread/suspending-thread-execution) | 更新2025年7月14日，挂起用途及同步风险 |
| F08 | [Page Lifecycle API](https://developer.chrome.com/docs/web-platform/page-lifecycle-api) | 页面更新2026年10月16日，针对性读 freeze/discard/恢复，不代表 360 实现 |
| W09 | [VirtualFree](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualfree) | 更新2024年2月5日，去提交、释放地址空间、数据与再访问边界 |
