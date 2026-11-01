更新日期：2026年11月2日。本文从 [当前架构](<架构设计 Architecture Design CPU 内存清理.md>) 推导Rust源码职责，硬约束集中于 `src/`。外围目录由 [README](../README.md) 导航，允许按实际工具调整。迁移前实现和测试已保留在 `legacy/python/`，不参与正式运行。

# 一、源码结构

```text
src/
├── lib.rs                    公共库入口和版本
├── main.rs                   CLI二进制入口
├── launcher.rs               无控制台原生网页入口，复用同一CLI/库
├── contracts.rs              Memory、Target、Sweep、Report和时间/运行标识
├── application/
│   ├── mod.rs
│   ├── service.rs            Application六能力、一次派发、报告和摘要
│   └── policy.rs             Protection、Settings、Rule、Policy
├── system/
│   ├── mod.rs                System能力合同、平台装配及系统随机源
│   ├── windows.rs            Win32读取、身份核验、工作集循环、句柄所有权
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
        └── memory-display.js 统一实时追踪与独立动效，不持有清理能力
```

模块按职责划分，避免HTTP、CLI各自实现一次清理。`contracts`仅保存跨模块事实；运行标识使用系统随机源，不触发内存采样。系统接口位于接入边界，`Application::new`可注入隔离实现，默认工厂选择真实平台。

# 二、架构到源码映射

| 标识 | 主要落点 | 责任和调用方向 |
| --- | --- | --- |
| AR-01 交互 | interaction/cli.rs、web.rs、web_assets/app.js | 参数和请求校验、目标选择、会话、输出；调用Application |
| AR-02 流程 | application/service.rs | 确认、实例内维护互斥、前后快照、记录及终态，不直接写界面 |
| AR-03 只读 | system/mod.rs、windows.rs | System::snapshot/targets，GetPerformanceInfo/EnumProcesses和进程身份 |
| AR-04 维护 | system/windows.rs | 同一句柄核验并SetProcessWorkingSetSize，逐项关闭；不创建终止/提权分支 |
| AR-05 记录 | application/service.rs、system/records.rs | 功能决定保存时机，records实际读写；新report.json及旧JSONL只读回看 |
| AR-06 呈现 | service.rs的presentation/brief、web.rs按需报告 | 摘要保留原终态，完整报告仅按需传递；不修改原始事实 |
| AR-07 显示 | app.js、memory-display.js | 后台/display独立读取，业务报告立即呈现，动效只拥有圆环显示 |

# 三、依赖和状态约束

- `main → interaction → Application → System/records`；系统实现不引用交互。CLI和HTTP共用Application，不互相启动子进程。库公开入口见 `lib.rs`。
- `System`提供只读快照、目标发现和一次全局/明确目标整理。平台资源和维护的不安全边界集中在system；launcher仅另含启动失败错误弹窗的UI调用，ProcessHandle持有并关闭自己取得的句柄，不伪造跨进程句柄或关闭外部句柄。
- 维护互斥属于Application实例。HTTP同一服务只保留一个业务Job，显示请求经独立只读通道处理。Job上限32，完成线程句柄回收；服务停止拒绝新任务并等业务收尾。
- `Target`以PID和创建身份关联，可选路径再核验。批次保护在业务派发前应用；路径未知时保守保护。最多4096个明确目标，逐项明细最多1024条，计数不截断，省略数单列。
- `Report`保存真实样本、analysis、action、errors、metadata和output_dir。维护前先保存calling/unknown意图，动作后保存已知事实，再取后置样本并封存；记录失败不能抹掉已发生动作。
- 新记录版本3，CLI工具目录版本1；Rust公开库不兼容旧Python导入API。旧记录仅只读兼容，不据相同工具名声称所有旧字段/CLI选项均兼容。

# 四、网页数据路径

`/api/session`不采样；`/api/display`读实际容量；`/api/action`提交明确业务，`/api/job?id=`返回状态和小摘要；`/api/report?id=`按需返回同一Job完整报告。HTTP绑定127.0.0.1，校验Host、Origin和会话令牌，输入体限1MiB。

`app.js`点击后先给忙碌状态两帧绘制机会再POST，不等待系统完成才反馈。任务正常结束先展示结果并恢复业务按钮，再通知cleanup-effect下降。到零的刷新只读，网络超时2.5秒是失败界限，不是固定等待。旧周期的异步回复通过周期身份隔离。完整报告展开才获取与格式化，实时读数不会改写本次报告。

`#gauge`的 `data-flow` 和 `data-max-frame-gap-ms`记录有界阶段及动效帧间隔，用于本地浏览器验收，不作为系统释放量或CPU性能计数。当前常规显示按250毫秒目标间隔请求一次，已隐藏页面停止显示请求。

# 五、测试和交付映射

`tests/rust/application.rs`核对公共合同、隔离调用次数、记录阻断/回看、保护与压力规则；`tests/rust/web.rs`核对HTTP会话、启动无副作用、明确目标、维护中读取、互斥和摘要/完整报告。测试System不连接Win32，测试文件仅写自己的临时目录。`tests/system/measurement.rs`核对阶段、分位和旧记录覆盖；`tests/interaction/web_assets/memory-display.test.cjs`核对连续显示与固定回弹目标；`tests/interaction/web_assets/app.test.cjs`核对业务与视觉分离、回弹刷新及旧回复隔离。

Cargo只包含当前Rust源码与资源、Rust合同测试、README和许可证，不把legacy、工具链或运行证据装入crate。`scripts/build.ps1`与run-tests.ps1复用rust-environment.ps1；正式入口为memory-cleanup.cmd→memory-cleanup.exe→同一Rust库；保留独立控制台CLI cpu-memory-cleanup.exe。旧manifest执行器和Tk脚本归档，未重跑旧测试，不把原571项验收改称Rust覆盖。

# 六、实现与剩余范围

上述Rust结构已落地，Package、CLI六工具、HTTP与Win32正式网页贯通，细节见 [验收记录](<运行记录 Run Record/2026-11-02 Rust原生网页验收.md>)。本轮只进行一次真实全局整理；定向/批次、自动规则的实机验证及其他系统版本未执行。历史CPU分析、Standby和Tk窗口不在本轮移植范围，不建立空目录冒充实现。后续改动从 [开发计划](<开发计划 Development Plan CPU 内存清理.md>)进入。


# 七、流畅显示与阶段计量的源码映射
本节对应架构第七章和UX-01/02、NATIVE-01，已按本次代码与验收落地。本轮不实施NATIVE-02/03。

- interaction/web_assets/memory-display.js：纯显示驱动，拥有实时追踪、下降、刷新、回弹及连续交接，一个RAF持有者写入统一显示比例。替代cleanup-effect.js，既不发送维护也不计算业务收益
- interaction/web_assets/app.js：保留真实快照、样本时间、报告与用户请求，原始容量立即呈现。显示驱动只接收比例及归零后的只读刷新回调。250毫秒轻量读取不重叠，保留有界请求/帧诊断供验收。主操作通过目标范围容器的disabled状态改变控件，不重建进程列表
- interaction/web_assets/index.html、styles.css：细条使用单次scaleX显示，去除width的CSS叠加缓动，百分比等宽数字及固定占位。目标列表用fieldset统一忙碌状态，原始结果详情增加阶段/分布说明
- system/measurement.rs：共享的阶段计时与纯分布汇总，返回版本化数据，统计总样本、P50/P95/P99、最大值和前20项，不查询应用名称或系统指标
- contracts.rs：Sweep增量携带可选timing，旧数据缺项保持None；system/windows.rs包围既有原生路径收集互斥阶段，使用同一句柄，原生方法、访问权限及调用次数不变
- application/service.rs：协调功能阶段，保留封存前窗口及完整业务窗口，摘要仍不携带全量明细；system/records.rs保存/核对同一run_id的timing.json附件，回看补充现有真实记录分布，不修改原件
- interaction/web.rs：嵌入新显示资源，服务就绪时段与功能计时分别表达，不把浏览器时间写成系统调用时间

新增检查按源职责组织在tests/system/measurement.rs与tests/interaction/web_assets/，已有Rust公共/HTTP合同测试继续使用Cargo入口。旧真实Run作为只读测试材料在忽略目录独立执行，不把机器路径写死进公开测试。不在Cargo测试或浏览器验收中调用真实清理。

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
