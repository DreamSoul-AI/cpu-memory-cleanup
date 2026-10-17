# Windows 内存安全调研

日期：2026-10-17。只查阅资料，没有运行清理工具或验证特权操作。风险分级是针对实验机器的工程判断，不是安全保证。

## 机制

- Available 包括 standby、free 和 zero lists，standby 已经可被 Windows 复用，不等于内存泄漏。清空 standby 通常只改变可用内存内部分类。[官方结构定义](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information)、[内存分类](https://learn.microsoft.com/en-us/windows-hardware/test/assessments/results-for-the-memory-footprint-assessment)。
- Working set 是进程当前驻留的可分页页面，含共享页，不能跨进程简单求和。修剪不释放应用分配或 commit；再次访问会缺页，可能从磁盘读取；脏页面复用前需要回写。[Working Set](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)。
- Commit 是系统承诺的虚拟内存后备容量使用，不是页面文件实际使用。GetPerformanceInfo 的各容量页数乘 PageSize 得字节。SystemCache 是复合缓存口径，结构文档描述为 standby 加 system working set，不能全部算可清内存。[指标对照](https://learn.microsoft.com/en-us/windows/win32/memory/memory-performance-information)。
- 内存压缩把部分闲置页面压缩留在 RAM，减少磁盘分页，并非垃圾。2015 官方介绍与 2017 官方进程说明反映不同历史实现，不应按固定进程名估算压缩量。[压缩机制](https://blogs.windows.com/windows-insider/2015/08/18/announcing-windows-10-insider-preview-build-10525/)、[后续实现说明](https://blogs.windows.com/windows-insider/2017/12/19/announcing-windows-10-insider-preview-build-17063-pc/)。本工具不关闭压缩。
- 页面文件支持 commit 和崩溃转储，修改它不是本工具的清理路线。[官方页面文件说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/introduction-to-the-page-file)。

## 路线、风险与权限

| 方法 | 相对风险 | 权限 / 限制 | 后续取舍 |
| --- | --- | --- | --- |
| PSAPI / PDH 只读观察 | 低 | 普通令牌尝试；ACL、保护进程或计数器策略可能拒绝 | 当前唯一实现 |
| RAMMap 只读快照 | 低 | 完整观察可能需要管理员，未在本机验证 | 人工交叉核验优先 |
| Empty Priority 0 Standby List | 中 | 全局影响；Native 实现通常需 SeProfileSingleProcessPrivilege，通常为管理员令牌 | 首个研究候选，仍须证明净收益 |
| Empty Standby List | 中至高 | 同上；更大范围冷缓存、额外磁盘读取 | 后于低优先 standby |
| 定向 EmptyWorkingSet / SetProcessWorkingSetSizeEx | 高（性能） | 前者需 QUERY_INFORMATION 或 QUERY_LIMITED_INFORMATION，且需 SET_QUOTA；后者需 SET_QUOTA，增大工作集另涉及 SeIncreaseWorkingSetPrivilege | 本阶段禁止，未来最多在明确同意的自有测试进程研究 |
| 全局 Empty Working Sets、system cache 修剪、modified list flush | 很高（性能 / I/O） | Native 特权操作；系统文件缓存还涉及 SeIncreaseQuotaPrivilege，具体构建待验证 | 不建议首个执行版本采用 |
| 杀进程、停服务、改关键配置或页面文件 | 禁止 | 无论权限如何 | 永不作为腾内存手段 |

[RAMMap 官方入口](https://learn.microsoft.com/en-us/sysinternals/downloads/rammap)提供物理内存、working set 和 standby 优先级分析。官方产品页没有完整的稳定清理自动化契约；以后从此入口取得签名工具，核对版本、实际菜单和签名，再做人工对照。不从第三方镜像推导命令行用法。

[EmptyWorkingSet](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-emptyworkingset)有公开 Win32 契约。[SetProcessWorkingSetSizeEx](https://learn.microsoft.com/zh-cn/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsizeex) 将最小/最大设为 SIZE_T(-1) 也可修剪；不代表释放 commit 或没有性能损失。

Native 路线是 NtSetSystemInformation(SystemMemoryListInformation, SYSTEM_MEMORY_LIST_COMMAND)。本次没有找到此内存列表 setter 的公开稳定 Win32 支持契约，不能把 ntdll 导出当作稳定 API。官方对 [NtQuerySystemInformation](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntquerysysteminformation) 也提示内部接口随系统版本改变。优先官方工具人工验证，本轮不绑定 Native setter。

开源原始实现交叉检查（只作证据，不作安全背书，没有复制代码）：

- [Mem Reduct README](https://github.com/henrypp/memreduct) 明确使用 undocumented Native API 并要求管理员。[main.c](https://github.com/henrypp/memreduct/blob/master/src/main.c) 使用 SystemMemoryListInformation，区分 MemoryEmptyWorkingSets、MemoryFlushModifiedList、MemoryPurgeStandbyList、MemoryPurgeLowPriorityStandbyList。其释放比例宣传与自动提权行为不采用。
- [MemListMgr 原始模块](https://github.com/fafalone/MemListMgr/blob/main/modMemListMgr.twin) 说明多数动作需 SeProfileSingleProcessPrivilege，文件缓存需 SeIncreaseQuotaPrivilege，并实现低优先 / 全 standby 分支。其组合自动清理不采用。
- [Win32MemHelpers 原始头文件](https://github.com/azarkevich/Win32MemHelpers/blob/master/MemHelpers.h) 同样使用类 80 和令牌特权调整。权限判断来自这些开源实现，需在目标构建复核；[官方特权常量](https://learn.microsoft.com/en-us/windows/win32/secauthz/privilege-constants)解释特权含义。管理员身份不等于相应特权已启用。

## 本轮实现口径

GetPerformanceInfo 读取物理内存、commit 与复合 SystemCache。[EnumPageFilesW](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-enumpagefilesw) 的 [TotalSize / TotalInUse](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-enum_page_file_information) 以页计，汇总为字节；不是用 commit 减 RAM 推导页面文件使用。

EnumProcesses + OpenProcess(仅 QUERY_LIMITED_INFORMATION=0x1000) + GetProcessMemoryInfo + QueryFullProcessImageNameW，输出仅文件名及 working set。不请求 VM_READ、SET_QUOTA、TERMINATE、ALL_ACCESS 或调试特权；访问拒绝和已退出进程跳过并计数。[官方读取权限](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo)。

PDH 读取 Standby Cache Core / Normal Priority / Reserve Bytes、Modified Page List Bytes、Free & Zero Page List Bytes、Cache Bytes、System Cache Resident Bytes；用 [PdhAddEnglishCounterW](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhaddenglishcounterw) 兼容本地化系统，检查 [格式值返回与 CStatus](https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhgetformattedcountervalue)。这些是瞬时容量计数器；缺失为 null，不冒充零。

理论可复用 standby 区间 = 0 到 min(三类 standby 总和, Available)。任一分类缺失则上界未知。它已经包含于 Available，不是新增 RAM；不包括 working sets、modified、压缩、内核池或页面文件。保证新增可用量为 0，本次实际清理为 0。采样非原子，不能据此保证性能收益；清缓存也不等于安全擦除。

## 执行前门槛

先持续只读采样，观察 available / commit、硬缺页、磁盘 I/O、实验吞吐和尾延迟。未来候选须在隔离环境做重复有/无操作对照，证明持续净收益，覆盖权限拒绝、目标 Windows 构建、冷缓存影响及停止条件；不能在实验机器上首次验证。

默认 dry-run、单次显式执行、操作前后日志、冷却和频率限制、失败即停止必须先就绪；日志失败阻止执行，不能回退到更激进动作。仅靠 python.exe 排除不可靠，全局 standby 操作也影响实验缓存。终止进程和修改关键配置永不允许。特权验证留待适当环境和明确授权，本轮不提权。
