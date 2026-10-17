# 360 内存清理机制：公开证据与本工具的取舍

资料查阅与结论日期：2026-10-17；本文整理此前对话中已完成的调研。本轮仅落盘文档，没有运行 360、RAMMap 或任何清理操作，没有修改执行代码。这里的“CPU / 主机内存”指系统 RAM，不是 CPU cache 或 GPU VRAM。

## 先说结论

360 清理后占用明显下降可能是真实的，但不能仅凭一个百分比判断原因或性能收益。公开资料确认其加速球具有关闭进程的能力；本次未找到官方披露其具体工作集修剪、standby purge 调用链或各手段贡献比例的资料。

Windows 的 standby 已经计入 Available。单独清 standby 通常不会新增可用 RAM；工作集修剪能改变驻留量，但不释放应用分配或 commit，并会带来重新调页的代价。对实验机器，本工具不能通过杀进程、无差别修剪或改配置追求占用数字下降。

## 360：哪些有依据，哪些不能确认

| 判断 | 证据等级 | 依据与边界 |
| --- | --- | --- |
| 加速球可以关闭不再使用的进程 | 官方明确描述 | [当前隐私政策“优化加速”](https://weishi.360.cn/privacy.html)；说明产品能力，不证明本次操作一定关闭了进程 |
| 用户选择程序后，加速可以结束这些程序、进程 | 官方明确描述 | [官方《电脑卡慢优化方案》](https://www.360.cn/n/10778.html)，属于历史功能说明，不保证所有版本、按钮行为一致 |
| 优化加速还包括启动项、服务项、计划任务等管理 | 官方明确描述 | 当前隐私政策及[历史白皮书](https://www.360.cn/privacy/v2/360anquanweishi.html)；启动优化与本次即时 RAM 清理不能混为一谈 |
| 360 本次调用了 EmptyWorkingSet / SetProcessWorkingSetSize | 未证实 | 技术上能解释部分现象，但本次未找到官方 API 披露或本机调用追踪 |
| 360 本次调用了 NtSetSystemInformation 清 standby | 未证实 | Windows 与其他工具有此路线，不能反推 360 使用了它 |
| standby、工作集修剪、结束进程各贡献多少 | 未知 | 没有分动作对照、前后系统指标、进程生命周期或 API 跟踪，不能编造比例 |

360 的“一键清理”也可能指磁盘文件、痕迹等清理，与主机 RAM 不是同一对象。手机卫士、Android 清理大师的资料不能作为 Windows 安全卫士的实现证据。“一键加速”“智能优化”是产品功能描述，不足以证明独有的内存算法；同样不能据此断言所有功能都是营销。

## Windows 接口与 RAMMap Empty 选项对照

下表是行为语义对照。Native 命令对应关系由开源实现交叉佐证，**不是对 360 或当前 RAMMap 二进制实际调用链的反编译结论**。本次没有实操 RAMMap 菜单。[RAMMap 官方入口](https://learn.microsoft.com/en-us/sysinternals/downloads/rammap)提供分析工具；[官方 Defrag Tools #6](https://learn.microsoft.com/en-us/shows/defrag-tools/6-rammap)的时间轴在 30:20 列出 Empty 菜单与性能分析主题，本文没有把网页时间轴冒充已观看的视频实验证据。

| 接口 / 常见选项 | 改变什么 | 主要代价与权限 |
| --- | --- | --- |
| EmptyWorkingSet，或 SetProcessWorkingSetSize / Ex 的 min、max 均设为 SIZE_T(-1) | 从指定进程工作集中移走尽可能多的页面；不释放虚拟分配或 commit | 后续软 / 硬缺页、磁盘读取、延迟抖动；前者需查询权限和 PROCESS_SET_QUOTA，后者需 PROCESS_SET_QUOTA；增大工作集另有特权要求 |
| Empty Working Sets / MemoryEmptyWorkingSets | 全局工作集修剪，涉及其他进程；页面可能转入 standby 或 modified | 影响实验热页；共享页、脏页等决定实际可复用量，不能把所有工作集相加当收益 |
| Empty System Working Set | 修剪系统工作集 / 系统缓存的驻留页面 | 后续文件访问可能变慢；相关缓存设置接口需 SeIncreaseQuotaPrivilege；不能与单纯清 standby 混同 |
| Empty Modified Page List / MemoryFlushModifiedList | 请求脏页回写，使部分页面随后可复用 | 写盘、等待和 I/O 竞争；不是丢弃脏数据，不保证所有页面立即变为可用，也不释放 commit |
| Empty Standby List / MemoryPurgeStandbyList | 丢弃全部 standby 缓存，使其可转为 free 等状态 | 通常 Available 不增加；后续缓存命中减少，可能增加磁盘读取 |
| Empty Priority 0 Standby List / MemoryPurgeLowPriorityStandbyList | 更窄范围地处理最低优先 standby | 仍是全局缓存影响；通常不增加 Available，不能称为零风险 |

公开 Win32 契约：[EmptyWorkingSet](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-emptyworkingset)、[SetProcessWorkingSetSize](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsize)、[SetProcessWorkingSetSizeEx](https://learn.microsoft.com/zh-cn/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsizeex)、[SetSystemFileCacheSize](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-setsystemfilecachesize)。

Native 内存列表命令通过 NtSetSystemInformation(SystemMemoryListInformation, SYSTEM_MEMORY_LIST_COMMAND) 操作。[System Informer 的 phnt 定义](https://github.com/winsiderss/systeminformer/blob/master/phnt/include/ntexapi.h)将其标为内部定义，并标注 setter 需要 SeProfileSingleProcessPrivilege；[Mem Reduct 源码](https://github.com/henrypp/memreduct/blob/master/src/main.c)提供独立实现证据。这些不能代替目标 Windows 构建验证，也不构成稳定的公开 Win32 清理契约。管理员身份不代表特权已启用或保护进程可访问；本工具不自动提权。

## 数字下降与真实收益

按 [PERFORMANCE_INFORMATION 官方定义](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information)：

`Available = standby + free + zero`

因此其他条件不变时，将 8 GiB standby 转成 free，只改变分类，新增 Available 约为 0。它也不扩大 commit limit。通常缺 RAM 或缺 commit 的机器不能靠这一动作解决容量问题。若存在可复现的系统回收异常，可能需要单独诊断和受控验证；不能从“standby 很大”推定异常，更不能先许诺收益。

| 手段 | 能否降低已用物理内存 | 是否真正释放应用分配 / commit | 代价 |
| --- | --- | --- | --- |
| 清 standby | 通常不改变按 total - available 计算的已用量 | 否 | 冷缓存、额外读盘 |
| 工作集修剪 | 可能；取决于共享页、脏页与重新访问 | 否 | 重新调页，收益可能很快消失 |
| 系统工作集修剪、脏页回写 | 可能；回写需完成且页面可复用 | 不等于释放应用 commit | 系统级缓存影响与 I/O |
| 应用主动释放不再需要的数据，并由分配器实际归还操作系统 | 可以，具体取决于分配与驻留情况 | 对相应 decommit 等操作可以 | 需要应用配合，后续可能重建数据 |
| 结束进程 | 可以释放其资源，但共享资源不一定同步消失 | 可释放其相应分配 | 丢失任务、未保存状态；本工具永不采用 |

依据：[Windows 工作集与缺页机制](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)。压缩存储属于内存管理机制，不是垃圾；本工具不关闭压缩或修改页面文件。CPU cache 不在本项目清理范围内。

如果下降的是 Windows 的 In use，单独 purge standby 通常不足以解释；如果只下降了某个程序的工作集，也不能证明其释放了分配。若进程仍在且工作集普遍下降、commit 大致不变，现象与修剪相符，但仍不是调用链证明。若 commit 同时下降，还可能有应用释放分配或进程退出等原因，不能据此认定 360 杀了进程。

本仓库旧快照不是针对这次 360 操作的前后对照，不能拿它们算清理收益或机制贡献。未来若观察用户自行进行的操作，应记录具体版本、按钮和选项，统一采样 Available、commit、standby、modified、working set、缺页与 I/O；只有分动作实验或调用追踪才能更有把握地归因。本工具不会为取证而主动触发清理。

## 本工具取舍与触发门槛

应借鉴分类巡检、预览、日志、冷却、失败即停止。未来最多在明确授权的自有测试进程上研究定向工作集修剪；低优先 standby purge 仅作为有证据的异常场景候选，不是日常定时保养。

绝不采用杀进程、停服务、改页面文件 / 压缩 / 关键配置、自动提权、无差别全局组合清理；当前也不执行任何工作集修剪。仅按 python.exe 名字保护实验不可靠，全局缓存操作也无法靠进程排除名单隔离影响。

只有持续低 Available，并伴随与内存压力相关的缺页、I/O 或实验吞吐 / 尾延迟恶化，才值得进一步调查。commit 接近限额要另行定位应用分配，不能用修剪伪装解决。任何未来执行候选都必须在隔离环境重复做有 / 无操作对照，确认持续净收益、冷缓存恢复成本和失败停止条件；只看占用百分比或 Free 上升不合格。先报告、默认 dry-run、前后日志与权限边界必须就绪，等 Enmao 审阅后另定执行阶段。

相关：[基础调研](research.md)、[GitHub 项目调研](github_survey.md)。
