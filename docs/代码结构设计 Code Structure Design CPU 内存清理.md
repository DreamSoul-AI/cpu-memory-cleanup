更新日期：2026年11月3日。本文从 [当前架构](<架构设计 Architecture Design CPU 内存清理.md>) 推导Rust源码职责，硬约束集中于 `src/`。外围目录由 [README](../README.md) 导航，允许按实际工具调整。迁移前实现和测试已保留在 `legacy/python/`，不参与正式运行。

# 一、源码结构

```text
src/
├── lib.rs                    公共库入口、版本和构建标识
├── main.rs                   CLI二进制入口
├── launcher.rs               无控制台原生网页入口，复用同一CLI/库
├── contracts.rs              Memory、Target、Sweep、Report和时间/运行标识
├── application/
│   ├── mod.rs
│   ├── service.rs            Application六能力、一次派发、报告和摘要
│   ├── comparison.rs         非默认feature的对照会话、证据与只读比较
│   └── policy.rs             Protection、Settings、Rule、Policy
├── system/
│   ├── mod.rs                System能力合同、平台装配及系统随机源
│   ├── windows.rs            Win32读取、权限映射及句柄所有权
│   ├── workingset.rs         共用串行工作集调度与默认/候选策略
│   ├── measurement.rs        阶段时钟与纯调用分布汇总
│   └── records.rs            原子报告、计时附件与新旧记录回看
└── interaction/
    ├── mod.rs
    ├── cli.rs                参数、六工具目录、JSON和退出码
    ├── web.rs                回环HTTP、会话、后台Job、按需报告
    └── web_assets/
        ├── index.html        紧凑主区与折叠详情
        ├── styles.css        环形、绿色按钮和响应式布局
        ├── app.js            用户事件、实时刷新、业务结果和视觉协调
        ├── memory-display.js 比例插值与绘制，不持有时钟或请求
        └── cleanup-flow.js   实时、保持起点、归零及动态回弹流程
```

模块按职责划分，避免HTTP、CLI各自实现一次清理。`contracts`仅保存跨模块事实；运行标识使用系统随机源，不触发内存采样。系统接口位于接入边界，`Application::new`可注入隔离实现，默认工厂选择真实平台。

# 二、架构到源码映射

| 标识 | 主要落点 | 责任和调用方向 |
| --- | --- | --- |
| AR-01 交互 | interaction/cli.rs、web.rs、web_assets/app.js | 参数和请求校验、目标选择、会话、输出；调用Application |
| AR-02 流程 | application/service.rs | 确认、实例内维护互斥、前后快照、记录及终态，不直接写界面 |
| AR-03 只读 | system/mod.rs、windows.rs | System::snapshot/targets，GetPerformanceInfo/EnumProcesses和进程身份 |
| AR-04 维护 | system/windows.rs、workingset.rs | 平台调用和串行调度分开，同一句柄核验并整理，逐项关闭；不创建终止/提权分支 |
| AR-05 记录 | application/service.rs、system/records.rs | 功能决定保存时机，records实际读写；新report.json及旧JSONL只读回看 |
| AR-06 呈现 | service.rs的presentation/brief、web.rs按需报告 | 摘要保留原终态，完整报告仅按需传递；不修改原始事实 |
| AR-07 显示 | app.js、cleanup-flow.js、memory-display.js | 后台/display独立读取，业务报告立即呈现，动效只拥有圆环显示 |

# 三、依赖和状态约束

- `main → interaction → Application → System/records`；系统实现不引用交互。CLI和HTTP共用Application，不互相启动子进程。库公开入口见 `lib.rs`。
- `System`提供只读快照、目标发现和一次全局/明确目标整理。平台资源和维护的不安全边界集中在system；launcher仅另含启动失败错误弹窗的UI调用，ProcessHandle持有并关闭自己取得的句柄，不伪造跨进程句柄或关闭外部句柄。
- 维护互斥属于Application实例。HTTP同一服务只保留一个业务Job，显示请求经独立只读通道处理。Job上限32，完成线程句柄回收；服务停止拒绝新任务并等业务收尾。
- `Target`以PID和创建身份关联，可选路径再核验。批次保护在业务派发前应用；路径未知时保守保护。最多4096个明确目标，逐项明细最多1024条，计数不截断，省略数单列。
- `Report`保存真实样本、analysis、action、errors、metadata和output_dir。维护前先保存calling/unknown意图，动作后保存已知事实，再取后置样本并封存；记录失败不能抹掉已发生动作。
- 新记录版本3，CLI工具目录版本1；Rust公开库不兼容旧Python导入API。旧记录仅只读兼容，不据相同工具名声称所有旧字段/CLI选项均兼容。

# 四、网页数据路径

`/api/session`不采样；`/api/display`读实际容量；`/api/action`提交明确业务，`/api/job?id=`返回状态和小摘要；`/api/report?id=`按需返回同一Job完整报告。HTTP绑定127.0.0.1，校验Host、Origin和会话令牌，输入体限1MiB。

`app.js`点击后先给忙碌状态两帧绘制机会再POST，不等待系统完成才反馈。任务正常结束先展示结果并恢复业务按钮，再通知 cleanup-flow 流程协调器下降。到零的刷新只读，网络超时2.5秒是失败界限，不是固定等待。旧周期的异步回复通过周期身份隔离。完整报告展开才获取与格式化，实时读数不会改写本次报告。

`#gauge`的 `data-flow` 和 `data-max-frame-gap-ms`记录有界阶段及动效帧间隔，用于本地浏览器验收，不作为系统释放量或CPU性能计数。当前常规显示按250毫秒目标间隔请求一次，已隐藏页面停止显示请求。

# 五、测试和交付映射

`tests/rust/application.rs`核对公共合同、隔离调用次数、记录阻断/回看、保护与压力规则；`tests/rust/web.rs`核对HTTP会话、启动无副作用、明确目标、维护中读取、互斥和摘要/完整报告。测试System不连接Win32，测试文件仅写自己的临时目录。`tests/system/measurement.rs`核对阶段、分位和旧记录覆盖；`tests/interaction/web_assets/memory-display.test.cjs`核对纯绘制，`cleanup-flow.test.cjs`核对保持、动态回弹与交接；`tests/interaction/web_assets/app.test.cjs`核对业务与视觉分离、回弹刷新及旧回复隔离。

Cargo只包含当前Rust源码与资源、Rust合同测试、README和许可证，不把legacy、工具链或运行证据装入crate。`scripts/build.ps1`与run-tests.ps1复用rust-environment.ps1；正式入口为memory-cleanup.cmd→memory-cleanup.exe→同一Rust库；保留独立控制台CLI cpu-memory-cleanup.exe。旧manifest执行器和Tk脚本归档，未重跑旧测试，不把原571项验收改称Rust覆盖。

# 六、实现与剩余范围

上述Rust结构已落地，Package、CLI六工具、HTTP与Win32正式网页贯通，细节见 [验收记录](<运行记录 Run Record/2026-11-02 Rust原生网页验收.md>)。已完成多轮明确授权的本机全局整理及网页验收；定向/批次和自动规则尚缺完整实机验证，其他系统版本未验证。历史CPU分析、Standby和Tk窗口不在本轮移植范围，不建立空目录冒充实现。后续改动从 [开发计划](<开发计划 Development Plan CPU 内存清理.md>)进入。


# 七、流畅显示与阶段计量的源码映射
本节对应架构第七章和UX-01/02、NATIVE-01，已按本次代码与验收落地。NATIVE-02 后续实施见第八章，NATIVE-03 未实施。

- interaction/web_assets/memory-display.js：纯位置计算与绘制。cleanup-flow.js拥有实时追踪、保持起点、下降、刷新、动态回弹和连续交接，一个RAF持有者写入统一比例，既不发送维护也不计算业务收益
- interaction/web_assets/app.js：保留真实快照、样本时间、报告与用户请求，原始容量立即呈现。显示驱动只接收比例及归零后的只读刷新回调。250毫秒轻量读取不重叠，保留有界请求/帧诊断供验收。主操作通过目标范围容器的disabled状态改变控件，不重建进程列表
- interaction/web_assets/index.html、styles.css：细条使用单次scaleX显示，去除width的CSS叠加缓动，百分比等宽数字及固定占位。目标列表用fieldset统一忙碌状态，原始结果详情增加阶段/分布说明
- system/measurement.rs：共享的阶段计时与纯分布汇总，返回版本化数据，统计总样本、P50/P95/P99、最大值和前20项，不查询应用名称或系统指标
- contracts.rs：Sweep增量携带可选timing，旧数据缺项保持None；system/windows.rs包围既有原生路径收集互斥阶段，使用同一句柄，原生方法、访问权限及调用次数不变
- application/service.rs：协调功能阶段，保留封存前窗口及完整业务窗口，摘要仍不携带全量明细；system/records.rs保存/核对同一run_id的timing.json附件，回看补充现有真实记录分布，不修改原件
- interaction/web.rs：嵌入新显示资源，服务就绪时段与功能计时分别表达，不把浏览器时间写成系统调用时间

新增检查按源职责组织在tests/system/measurement.rs与tests/interaction/web_assets/，已有Rust公共/HTTP合同测试继续使用Cargo入口。旧真实Run作为只读测试材料在忽略目录独立执行，不把机器路径写死进公开测试。不在Cargo测试或浏览器验收中调用真实清理。

# 八、最小权限串行候选的源码映射
对应架构第八章与 NATIVE-02，按以下映射完成实现与隔离检查，设计提交 033e694，实现提交 1d1d735。

- `system/workingset.rs`：拥有单轮枚举、权限意图、身份核验、一次原生调用、关闭与阶段计量。小范围平台接口仅覆盖本次调用链，不扩展成通用插件框架。默认和候选共用同一个循环
- `system/windows.rs`：实现平台接口，将只读、核验维护、仅配额维护三种权限意图分别映射到 Win32 掩码。进程句柄继续由 RAII 对象持有；原生调用内部记录自身耗时，调度与测试不伪造系统耗时
- Cargo 的 `experimental-quota` 非默认 feature 暴露开发用候选装配，显式注入 `Application` 才生效。默认 `system::native()`、CLI、HTTP、规则和网页不选择候选，不新增用户模式开关
- 候选只有全局分支省略身份查询。明确目标仍走默认核验分支。逐项 `created_at` 缺失保留 null，计时记录策略与身份覆盖，不能拿本次 PID 补查结果回填身份
- `tests/system/workingset.rs`：按职责验证串行顺序、两种权限、默认身份、候选不查询、定向身份与路径不匹配阻断、原生失败、取消及关闭未知停止。用隔离句柄覆盖维护逻辑，不调用主机维护 API

feature 关闭与打开均通过格式、严格 Clippy 和 Cargo 检查，两种配置均独立构建库、控制台及 GUI 二进制。正式运行服务保持原版本，本轮不重启为候选、不运行真实清理。实际速度和覆盖比较未验证。

# 九、性能对照源码映射
对应架构第九章与 COMPARE-01 至03，设计提交47a9030，实现提交0df3537。隔离验证及构建完成，实机探索已执行但不能决定采用，见[真实对照](<运行记录 Run Record/2026-11-03 真实工作集对照.md>)。非默认 feature 下新增 `application/comparison.rs`，负责会话、单次门控、比较证据与只读汇总。方法枚举和固定清单校验位于 contracts.rs，系统接入不依赖 application。`system/windows.rs` 增加开发用固定 PID 枚举装配，两种方法仍调用 `workingset.rs` 的同一串行循环。它拒绝明确目标接口，避免把只按句柄的候选伪装成定向身份核验。

`examples/workingset-compare.rs` 是独立开发入口，静态计划与离线比较不读系统。仅显式开启会话后分配预算内负载与真实读取，每次原生动作还需单独输入方法确认。该入口不进入网页、六工具目录或规则。`tests/application/comparison.rs` 验证门控、证据、只读比较和受控负载内容，系统接入使用隔离实现。Cargo 包包含例程及对应测试源，构建不运行维护。

# 十、圆环显示流程的重新划分
状态：按架构第十章实施完成，主体实现94be940、样本时间线及读取回归f7b8aad，已做一次真实网页验收。

- app.js 保留业务请求、原始结果与采样接入，开始/结束发出流程事件，不直接决定下降或回弹数值
- memory-display.js 负责比例绘制节奏与插值，只接受明确的显示指令。实时目标和动效目标不得同时争用圆环
- 已提取 cleanup-flow.js，拥有实时、保持起点、下降、零位刷新、回弹及结束交接，持有周期标识。后台采样只更新原始状态，执行期间不得驱动圆环；容量数字照常更新
- 零位刷新与常规读取在同一读取协调入口管理，保留样本身份/时序，不把另一份未标识快照当作本次刷新结果。动作终态与显示终态独立
- tests/interaction/web_assets/ 对应新增流程测试：点击后后台读数下降但圆环保持起点、只播一次下降、回弹目标来源可定位、旧回复不接管新周期、原始结果立即出现且一次派发

本次不改变系统清理方法或权限。真实页面验收前固定服务产物与资源哈希，不把旧服务或离线状态机通过当成新页面已验证。

零位与周期读取共享单一在途入口，以样本时间及接收序号判旧。回弹记录来源样本、目标及最新实际值，采用第十一章的低目标与步进合同，不再按固定截止时刻重算速度。绘制、显示流程、原始读数与业务结果分别拥有状态，旧回复不能接管新周期，动作仍只派发一次。

# 十一、回弹按360已核实方法收敛
本节维护当前回弹合同，历史固定截止方案已停用。下降仍750毫秒。视觉周期从下降开始保存单周期目标，期间只按更低的有效样本下调，原始实际数据继续上下更新。回弹使用20毫秒名义步进，每步目标的1/15，更新目标时更新步长和下一次名义步进时点，不通过剩余时间反推速度。下一步到达或超过目标即结束，解除视觉低目标约束，连续恢复实际读数。后台功能与原始结果不改，不以视觉目标宣称当前实际占用。

实现b1cb4bf仍集中CleanupFlow，MemoryDisplay只绘制，MOTION-01至03已实现并完成一次真实浏览器验收。时间线记录目标样本、步长及阶段，测试覆盖早中晚改目标、回升不抬视觉目标、下一步钳位、单周期重置和真实数据保持。阶段文字按用户要求暂留。浏览器RAF为绘制调度，与原生消息定时器不承诺逐帧相同，长停顿不补发无限积压。

# 历史方案定位
原章节和旧执行约定归档到 [Rust迁移前源码结构基线](<开发记录 Development Record/2026-11-02 Rust迁移前源码结构基线.md>)。历史记录中的提交、路径和验收状态保留原义。

<!-- 历史章节锚点仅供旧记录定位，不表示旧合同继续适用。 -->
<a id="一设计范围与约束强度"></a>
<a id="二模块目录"></a>
<a id="一产品源码结构"></a>
<a id="二架构职责到源码的映射"></a>
<a id="三流程与质量要求的关联"></a>
<a id="三各模块内部的文件职责"></a>
<a id="一启动与共享合同"></a>
<a id="二用户交互模块-interaction"></a>
<a id="三功能模块-application"></a>
<a id="四系统模块-system"></a>
<a id="四源码依赖硬约束"></a>
<a id="五结构变更与开发记录"></a>
<a id="六实施状态与验证边界"></a>
<a id="一实际完成范围"></a>
<a id="二验证与剩余限制"></a>
<a id="七测试与兼容脚本的实现映射"></a>
<a id="一测试目录职责"></a>
<a id="二关键验证路径"></a>
<a id="三兼容脚本"></a>
<a id="四配置树的展开合同"></a>
<a id="五独立网页演示的验证例外"></a>
<a id="八内存清理小窗口的实现映射"></a>
<a id="一显示刷新与平滑过渡"></a>
<a id="二桌面启动"></a>
<a id="九包公共入口与接入映射"></a>
<a id="一最小公共结构"></a>
<a id="二依赖与兼容"></a>
<a id="三验证与剩余限制"></a>
<a id="十定向工作集整理的源码映射设计"></a>
<a id="一最小文件划分"></a>
<a id="二公开合同与调用边界"></a>
<a id="三记录命令和前端映射"></a>
<a id="四必要验证与实现差距"></a>
<a id="十一范围与规则扩展映射"></a>
<a id="十二本地网页入口的目标映射"></a>
<a id="本次web-01至03实际落点"></a>
<a id="real-01及后续接入落点"></a>
<a id="正式启动与真实验收修正"></a>
<a id="网页轻量显示映射"></a>
<a id="十三两端共用呈现与详情映射"></a>
<a id="十四功能恢复与动效隔离的目标结构"></a>
<a id="十五网页主圆环与真实读数分工"></a>
<a id="十六计时与工作集核验的源码映射"></a>
<a id="十七同目标比较与独立观察的源码映射"></a>
<a id="十八一键逐进程工作集路径"></a>
<a id="十九读数观察与网页反馈收敛"></a>
<a id="二十rust正式源码结构"></a>

# 十二、构建身份与入口映射

`build.rs`从排序后的src源码、Cargo.toml、Cargo.lock及自身内容计算稳定构建标识；通过编译环境交给`src/lib.rs`统一导出。无需Git历史、个人路径、时间戳或联网，源码包重建采用相同算法。它是非密码学识别码，不作安全凭据。

`interaction/cli.rs`的版本查询、目录和服务复用核对，以及`interaction/web.rs`的会话响应，引用同一库常量。启动器仍仅装配网页参数，不复制业务或动效。`scripts/ci.ps1`负责串联原有测试、构建与Cargo源码包验证并保存阶段结果，不进入运行时依赖。

# 十三、安装打包与Agent实测映射
安装源码放在scripts/windows/install.nsi，构建包装放在scripts/build-installer.ps1，读取已构建的两份EXE而不复制业务。安装快捷方式传明确output-dir到用户数据路径，launcher继续只负责正式网页入口。

Agent端到端证据保存具体输入参数、CLI JSON及最终解释，先用现有六工具，不提前增加MCP实现。Linux平台适配进入system，现有Unix随机源已存在，计划中“随机源未实现”的描述需改为核对其目标平台运行证据。

# 十四、Linux只读适配映射
system/linux.rs负责/proc/meminfo与进程stat/status解析，mod.rs按target_os装配。System增加cleanup_supported默认方法保持已有实现兼容，Linux覆盖为false，HTTP机制目录及网页维护控件据此禁用。读取与维护能力分离，CLI不能通过通用工具名把只读平台解释成支持整理。

Linux便携候选由 `scripts/package-linux.sh` 在Linux原生构建，归档同一CLI、MIT、静态构建目录和启动入口；运行记录写入用户状态目录。该脚本不进入src硬约束，不隐式执行维护或发布。

# 十五、小窗口原型的源码落位

先在忽略目录证明依赖可构建，再落位正式src模块。目标为可选窗口入口及 `interaction/desktop/` 承载窗口事件、导航和生命周期；系统能力仍只在system，维护流程仍只在application。HTTP服务提供可克隆的停止控制对象，统一网页停止与窗口关闭的收尾语义。默认库/CLI/浏览器构建不强制依赖窗口运行时。

# 十六、macOS只读模块与指标映射

`contracts::Memory`维护可选提交字段、可用量口径和可选压缩驻留量，统一序列化；网页按实际字段显示，不在交互层推算缺失数据。`system/macos/mod.rs`装配只读能力，`memory.rs`封装sysctl/Mach容量及端口生命周期，`processes.rs`封装libproc身份、驻留与路径。通过固定版本libc使用平台定义，不复制内核实现。

sysctl读取总量，Mach按返回的实际字段数量和页大小转换，libproc读取前后微秒启动身份；所有维护请求保持未派发。自动化检查与原生实测分开，工具及HTTP仍进入同一Application。外围Mac打包和runner探针在scripts，不复制业务实现。

窗口入口新增显式后台启动与配置目录参数，作为同一窗口实现的启动选项，不另建维护通道。可选原生旅程测试落于tests/interaction/desktop，默认隔离测试脚本不自动运行它；真实执行需明确设置本轮可执行文件、调试驱动路径和自有负载授权。

# 十七、维护进度映射

contracts定义轻量CleanupProgress计数；Windows实例保存当前执行进度，workingset串行循环按实际完成步骤通知观察者，不另开清理循环。System默认返回无进度，HTTP任务只在维护running阶段读取本实例进度。网页复用原任务轮询显示计数，各维护入口共用完成收尾，不增加额外系统维护请求。
