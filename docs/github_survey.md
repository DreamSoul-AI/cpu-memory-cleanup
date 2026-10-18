# GitHub Windows 内存工具调研

资料查阅与结论日期：2026-10-17；本文落盘此前对话已完成的查证，不宣称本轮重新抓取了最新 HEAD。没有下载或运行二进制，没有复制上游代码，没有实现清理执行。

维护信息区分提交日期、发行日期、更新日志日期。部分 GitHub 页面由网页检索缓存返回，且部分提交列表无法取得；表内“本次可见”只表示可核实的近期活动，**不保证是实时最近提交**。取不到的日期明确保留未知，不以抓取时间或版权年份代替维护日期。

## 项目总览

| 项目 / 仓库 | 最近维护信息的证据与限制 | 机制 | 命令行 / 定时能力 | 许可证 |
| --- | --- | --- | --- | --- |
| [henrypp/memreduct](https://github.com/henrypp/memreduct) | [提交页](https://github.com/henrypp/memreduct/commits/master/)本次可见 2026-08-29，d6ecbc4；未实时确认 HEAD | Native 内存列表：工作集、standby、低优先 standby、modified；另有系统文件缓存、页面合并等 | 源码确认 -clean、-clean:full，以及间隔、阈值、冷却与可选日志 | [GPLv3](https://github.com/henrypp/memreduct/blob/master/LICENSE) |
| [winsiderss/systeminformer](https://github.com/winsiderss/systeminformer) | [提交页](https://github.com/winsiderss/systeminformer/commits/master/)本次可见 2026-09-11，e675522；未实时确认 HEAD | 系统观察、Native 列表命令、缓存、页面合并；部分高级压缩操作依赖驱动 | 已确认 GUI 内存命令；未确认专用定时清理 / 清理 CLI，不将监控刷新算成定时清理 | 当前主仓库 [MIT](https://github.com/winsiderss/systeminformer/blob/master/LICENSE.txt)，仍需核对所取文件、依赖与历史版本 |
| [fafalone/MemListMgr](https://github.com/fafalone/MemListMgr) | README / 源码更新日志为 2024-06-29，v2.1.4；[发行页](https://github.com/fafalone/MemListMgr/releases)显示 30 Jun 上传；最近源码提交日期未核实 | 轻量 Native 调用：standby、低优先 standby、工作集、modified、系统文件缓存、注册表缓存、页面合并 | /clearstdby、/clearlpstdby、/flushmod、/emptyws 等；有自动监测 / 优化功能 | [MIT](https://github.com/fafalone/MemListMgr/blob/main/LICENSE) |
| [IgorMundstein/WinMemoryCleaner](https://github.com/IgorMundstein/WinMemoryCleaner) | [发行页](https://github.com/IgorMundstein/WinMemoryCleaner/releases)可见 v3.0.8：2025-12-13；最近源码提交日期未核实 | Native standby / modified / 全局工作集；有排除列表时逐进程 EmptyWorkingSet；另有系统缓存、卷缓存等 | 按区域 CLI、周期 / 阈值自动优化、Windows 服务模式 | [GPLv3](https://github.com/IgorMundstein/WinMemoryCleaner/blob/main/LICENSE) |
| [stefanpejcic/EmptyStandbyList](https://github.com/stefanpejcic/EmptyStandbyList) | README 明确“不再维护”；最近提交日期未核实 | 上传的 EXE + BAT/CMD + 任务 XML；没有原程序实现源码，不能确认该二进制内部 API | 包装 workingsets、modifiedpagelist、standbylist、priority0standbylist；支持任务计划 / 周期脚本 | 仓库未见 LICENSE；EXE 来源、修改和再分发授权未核实，不视为开源实现 |
| [Sordum Reduce Memory](https://www.sordum.org/9197/reduce-memory-v1-7/) | 官网 v1.7 更新日志为 2024-11-27；页面另有 2026-09-14 修改时间，不将它当成新版发布 | 官网明确提到 EmptyWorkingSet；没有证据据此宣称其清 standby | 官网确认 CLI、周期、阈值及进程排除 | freeware；本次未找到官方开源仓库 / 开源许可证 |

“免费”“在 GitHub 上能下载 EXE”和“有可复用开源源码”是不同条件。Sordum 工具作为对照样本保留，不能冒称 GitHub 开源项目；同名第三方 ReduceMemory 项目也不等于 Sordum 原作。

## 源码级发现与可借鉴部分

### 1. System Informer：内存分类与命令边界的优先参考

已查阅 [SystemInformer/memlists.c](https://github.com/winsiderss/systeminformer/blob/master/SystemInformer/memlists.c) 和 [phnt/include/ntexapi.h](https://github.com/winsiderss/systeminformer/blob/master/phnt/include/ntexapi.h)。前者署名包含 wj32（2010–2011）和 dmex（2017–2026），为用户提到的 wj32 系统工具提供可核实的源码脉络；不把旧 Process Hacker、wj32 原小工具和当前 System Informer 的所有版本许可证混为一谈。

源码将 MemoryEmptyWorkingSets、MemoryFlushModifiedList、MemoryPurgeStandbyList、MemoryPurgeLowPriorityStandbyList 分开处理；phnt 将 SystemMemoryListInformation 标为内部接口并注明相关特权。内存菜单另有页面合并、注册表 / 文件缓存和压缩存储操作，压缩存储路径检查驱动连接。

值得借鉴：分类计数、明确动作范围、逐项状态报告、接口与 Windows 版本 / 权限的对应。不照搬：驱动、服务提权、“Empty all”、压缩存储操作。本次查看的取消提权分支返回 STATUS_SUCCESS，说明本工具应自行区分取消、跳过、失败和真正成功，不能仅因项目成熟就复制其全部状态语义。它可作为 RAMMap 部分观察与内存操作的开源替代参考，不意味着完整复刻 RAMMap 的物理页面分析能力。

### 2. MemListMgr：最直接的小型命令示例

[modMemListMgr.twin](https://github.com/fafalone/MemListMgr/blob/main/modMemListMgr.twin) 直接展示每个命令如何选择 SYSTEM_MEMORY_LIST_COMMAND；文件缓存使用 SystemFileCacheInformationEx，另有页面合并和注册表缓存分支。作者说明多数动作需要 SeProfileSingleProcessPrivilege，文件缓存需要 SeIncreaseQuotaPrivilege。

值得借鉴：每种区域分开命名、分开调用、保留返回状态。MIT 是许可较宽松的候选，但复用仍须保留声明、核对所取代码与上游来源。README 称代码参考 System Informer；这不是忽略上游版权的理由。README 与模块注释对多参数 CLI 的描述存在版本差异，未来实际使用时应锁定提交验证，本文不承诺其参数组合行为。

不采用自动组合“工作集 → modified → standby”的策略，也不把作者关于性能异常的经验性判断当成普遍性能证明。

### 3. Mem Reduct：冷却与日志思路比清理范围更值得借鉴

[src/main.c](https://github.com/henrypp/memreduct/blob/master/src/main.c) 的清理分支明确使用 Native 内存列表命令。_app_timercallback 具有占用阈值、间隔判断以及冷却保护，注释说明冷却用于避免持续超阈值引起循环清理；_app_parseargs 支持 -clean / -clean:full；清理结果可写日志。

值得借鉴：冷却、防重复触发、按动作配置和日志。不能把系统前后差值直接当成动作因果收益：采样期间应用也会分配 / 释放内存。不采用自动提权、全局默认清理或“释放百分比”承诺。GPLv3 源码可用于理解实现；直接复制或翻译改编再分发时须按许可证处理，不能当作 MIT 或自行原创代码。

### 4. WinMemoryCleaner：排除列表改变了实际调用路径

[src/Service/ComputerService.cs](https://github.com/IgorMundstein/WinMemoryCleaner/blob/main/src/Service/ComputerService.cs) 的 OptimizeWorkingSet 在排除列表非空时枚举进程并调用 EmptyWorkingSet，且尝试启用调试特权；列表为空时调用全局 MemoryEmptyWorkingSets。standby 分支选择全 / 低优先 purge。系统文件缓存同时涉及 Native 缓存结构和 SetSystemFileCacheSize；卷缓存分支还有设备控制和 FlushFileBuffers。

可借鉴 CLI 与 GUI 共用动作划分、自动化结果可观察的思路；不复制按进程名排除作为安全保证，也不引入其卷缓存、服务安装、重置和提权行为。README 宣称所用 API 都有文档，并把 standby 变 free 作为效果展示；源码确实包含内部 Native 命令，且分类改变不证明新增 Available 或应用性能提升，不能照搬这些表述。GPLv3 约束与 Mem Reduct 同样需要处理。

### 5. EmptyStandbyList / ReduceMemory 包装器：不当作实现源头

wj32 的[历史 Empty Standby List 产品地址](https://wj32.org/wp/software/empty-standby-list/)本次未能取得正文，不能确认最新维护时间、原始源码仓库或许可证。stefanpejcic 仓库只证明提供了一组二进制调用与调度包装，不能证明上传的 EXE 可从公开源代码重建。其建议放入 System32、使用最高权限任务和固定周期运行的做法不采用。

[Atmeg23/ReduceMemory](https://github.com/Atmeg23/ReduceMemory)也是额外核查的包装项目：README 依赖 EmptyStandbyList.exe 并配置任务 / 注册表，不是 Sordum Reduce Memory 的源码。未充分核实其许可证与最新维护日期，不列为代码复用候选。

Sordum 官网直接说明 EmptyWorkingSet 与周期 / 排除配置，足以证明工作集修剪并非 360 独有；但 freeware 不等于允许复制实现。

### 6. 额外线索：WinMemClean

[nusu-github/WinMemClean](https://github.com/nusu-github/WinMemClean) 的 README 描述 Rust core / CLI / service 分离、只读 /Stats、按区域操作，以及默认排除部分易卡顿区域的自动配置；声明 MIT OR Apache-2.0。它还描述 Native 全局修剪与带排除列表时的逐进程 EmptyWorkingSet 路径。

本次未能取得其核心源码、独立许可证正文和最近提交日期，因此这些仅记作 **README 声称**，不推荐直接复制，也不把它视为已完成源码安全审查的项目。可留待后续核验“只读入口与执行入口分开”的思路，其默认自动阈值不能直接照搬。

## 与 360 对比：共用机制，不虚构独有技术

工作集修剪、standby purge、脏页回写、系统缓存修剪、页面合并均能在上述项目中找到公开实现；它们依赖 Windows 内存管理机制，不是厂商独有的 RAM 创造技术。

360 [当前隐私政策](https://weishi.360.cn/privacy.html)和[官方教程](https://www.360.cn/n/10778.html)公开说明了关闭用户所选进程的能力。其具体是否采用上述 Native 命令、工作集修剪各占多少作用，仍无本次找到的公开证据；不能因别人采用就推定 360 也采用，不能因没有公开代码就推定其没有其他实现。

“一键加速”“智能优化”属于产品表述，不能证明独有算法或稳定收益；开源项目也有类似宣传。尤其 standby 转 free 的可见变化，并不意味着新增可用 RAM，因为 standby 已经计入 Available：[官方结构定义](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information)。详见 [360 专项调研](360_memory_cleanup.md)。

## 复用结论与下一步

1. 优先参考当前 System Informer 的分类、结构定义和状态处理，以及 MemListMgr 的小型动作拆分；这两者当前主仓库为 MIT。真正复制前锁定提交，核对文件头、第三方来源与依赖，保留版权和许可声明。
2. 参考 Mem Reduct 的冷却 / 日志设计及 WinMemoryCleaner 的动作入口划分；不在本项目许可证未明确、合规义务未处理时直接引入 GPL 派生代码。学习 Windows API 事实和高层思路，与复制可受保护的实现表达不同；改语言不是规避许可的方法。
3. 没有许可证的仓库、来源不明的 EXE、freeware 产品不作为可直接复用源码。[GitHub 官方许可说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)说明，未授予许可证不等于允许复制、分发或制作派生作品。本轮没有作出对任意代码的完整法律或来源审计。
4. 无论许可证是否允许，本工具都不采用杀进程、停服务、改关键配置 / 页面文件、自动提权、驱动清压缩、全局组合清理或固定周期无条件清理。按名字排除 Python 不足以保护训练 / 评测，全局 standby 清理也会影响实验缓存。
5. 有界只读采样与 JSONL 日志已经实现，可记录 Available、commit、硬缺页和磁盘 I/O。实验吞吐与尾延迟仍须由实验自身记录并按 UTC 对齐。有真实压力和可重复对照净收益后，才讨论隔离测试环境中的单项候选。没有实现或授权任何清理执行，等 Enmao 审阅后另定。

相关：[基础机制与红线](research.md)、[360 专项调研](360_memory_cleanup.md)。


## Fresh re-check 2026-10-18 (360-style one-click releaser goal)

Date: 2026-10-18 (Asia/Shanghai). Method: `gh repo view` / `gh search repos`
and `gh search code` as diaoenmao on DreamSoulDEM, plus source reading of
shallow clones in C:\Temp\gh-survey-20261018 (read only; nothing was run, no
cleanup API was called). This section extends, and where noted corrects, the
2026-10-17 survey above. Stars / pushed-at are as of 2026-10-18 evening
Asia/Shanghai (GitHub timestamps are UTC).

### Verified candidates (fresh)

| Repo | Stars | Last push (UTC) | Licence (repo metadata / files) | Language | Verified mechanism (source evidence) |
| --- | ---: | --- | --- | --- | --- |
| henrypp/memreduct | 10858 | 2026-10-07 | GPL-3.0 | C | Native memory lists via NtSetSystemInformation(SystemMemoryListInformation): working sets, standby, low-priority standby, modified; plus system file cache / page combining. Prior survey: src/main.c, -clean / -clean:full, threshold+interval+cooldown. Mechanism unchanged in this re-check; HEAD source not re-read line-by-line. |
| IgorMundstein/WinMemoryCleaner | 5167 | 2025-12-19 | GPL-3.0 | C# | Same native lists; per-process EmptyWorkingSet when an exclusion list is given, otherwise global MemoryEmptyWorkingSets (prior survey: src/Service/ComputerService.cs). Timer/threshold/service modes in README. |
| winsiderss/systeminformer | 16268 | 2026-10-18 | MIT (repo) | C | Observation plus native memory-list commands (memlists.c / phnt ntexapi.h, prior survey). Not a one-click cleaner; reference for classification and status handling, not a fork target for a releaser. |
| Y-ASLant/MemoryCleaner | 44 | 2026-10-12 | MIT (LICENSE file and Cargo.toml) | Rust | NEW in this survey. src/optimize.rs calls NtSetSystemInformation for EmptyWorkingSets, FlushModifiedList, PurgeStandbyList, PurgeLowPriorityStandbyList, system file cache and combined pages; src/win32/process.rs uses K32EmptyWorkingSet per process. Tray GUI, auto UAC elevation (README_EN FAQ). Its TerminateProcess use is kill_process_by_name("explorer.exe") in icon_cache.rs - an icon-cache helper, NOT a memory-release feature; do not cite it as process-killing cleanup. |
| nusu-github/WinMemClean | 0 | 2026-08-08 | MIT OR Apache-2.0 (LICENSE-MIT + LICENSE-APACHE files, Cargo.toml; corrects the 2026-10-17 note that licence text was unverified) | Rust | Now source-verified. crates/winmemclean-core: README table and lib.rs/nt.rs show NtSetSystemInformation memory-list commands (working set, modified, standby, low-priority standby, file cache, combined pages, registry cache) and per-process EmptyWorkingSet when exclusions given (process.rs). CLI + optional Windows service; /Auto default 15 min / 80% load, excludes freeze-prone standby/modified purge unless /AllowFreezeProne. No tray by default. |
| H3d9/memory_cleaner | 375 | 2022-10-01 | GPL-3.0 | C++ | Chinese Memory Cleaner remake. memclean/win32utility.cpp: per-process EmptyWorkingSet, MemoryEmptyWorkingSets, MemoryPurgeStandbyList, MemoryFlushModifiedList, SystemFileCacheInformation, SystemCombinePhysicalMemoryInformation via NtSetSystemInformation. Stale since 2022; description claims it replicates the closed original's internal logic - that provenance claim is unverified. |
| fafalone/MemListMgr | 26 | 2024-06-30 | MIT | VB6 | Smallest direct native-call demo (prior survey: modMemListMgr.twin): standby / low-priority standby / working set / modified / file cache, separate commands. Old VB6 codebase. |
| middaysan/windows-ram-cleaner | 9 | 2025-07-30 | UNVERIFIED: README claims MIT but there is no LICENSE file and GitHub licenseInfo is null - do not treat as MIT-licensed code | Go | internal/windows_api/actions.go: MemoryPurgeStandbyList via NtSetSystemInformation(SystemMemoryListInformation=0x50), SetProcessWorkingSetSize(-1,-1) and EmptyWorkingSet per process. Tray app, admin required, 65% threshold in tooltip.go. Mechanism verified; licence is not. |
| luojia65/memory_free_windows | 4 | 2018-11-04 | none shown | Rust | Anti-pattern, do not use: allocates ~75% of RAM to force others out, sleeps, drops it, and reports the delta as freed (src/main.rs; README says "for fun"). This pressures the pagefile and other processes; it releases no commit belonging to anyone else. |
| stefanpejcic/EmptyStandbyList | 92 | 2024-10-08 | none | Batchfile | Binary wrapper only (EmptyStandbyList.exe + bat/cmd/task XML: workingsets, modifiedpagelist, standbylist, priority0standbylist). No source for the EXE; not reusable code. Same conclusion as 2026-10-17. |

### Checked and NOT found as open source on GitHub

- Wagnardsoft ISLC (Intelligent Standby List Cleaner): `gh api users/wagnardsoft/repos` returns 404 and no official repo was found. Search hits such as zeptotherapistbend/ISLC (7 stars, licence "Other", no primary language, description is a download/install guide) are unofficial download-guide mirrors with no verifiable source. ISLC's real mechanism (timer/threshold purge of standby list, from its official site description) is therefore UNVERIFIED from source in this survey.
- Sordum Reduce Memory: no official GitHub repo found (`gh search repos "Reduce Memory sordum"` empty). It remains sordum.org freeware, v1.7 changelog 2024-11-27, documented EmptyWorkingSet use on the vendor page (prior survey). Not forkable code.
- Microsoft Sysinternals RAMMap: not GitHub open source; official distribution is the Sysinternals download page only. Its Empty menu is a reference for categories, not reusable code.
- wj32 original EmptyStandbyList: only wrapper/fork repos and download links were found (e.g. duducorvao/EmptyStandbyListTimer points to wj32.org); no original source repo or licence verified.
- Chinese 360-style clones: `gh search repos` for the Chinese terms for memory release / one-click acceleration found no Windows 360-style one-click RAM releaser clone with source. Hits were Android cleaners, a 360 acceleration-ball animation demo (Mario0o/Animation360, Java, 8 stars, 2016, UI only), Tencent lemon-cleaner (macOS/Objective-C, disk cleaning), and the H3d9 remake above. Speculation that a ready-made 360 clone exists on GitHub is not supported by this search.

### Per-mechanism verdict for THIS machine

Measured here (docs/mitigation-design.md, 2026-10-18): 47.1 GiB RAM, commit
limit 95.1 GiB; desktop pressure commit median 64.70 GiB, standby median
6.73 GiB (idle 18.54 GiB), available median 8.23 GiB, largest single process
under 2 GiB, ~1000 processes, hard-fault median low (pages input/s 52.5).

| Mechanism | Releases commit? | What it actually does here | Cost | Verdict for this host |
| --- | --- | --- | --- | --- |
| Purge standby (all or priority-0) | No | Turns up to ~6.7 GiB of standby into free under pressure; Available = standby + free + zero, so new Available is ~0. The number that visibly moves is Free, not usable headroom. | Cold file cache, later disk re-reads | Cosmetic for this host. Reject as the headline 360 effect. |
| EmptyWorkingSet, all processes (MemoryEmptyWorkingSets or per-process loop) | No | Evicts resident pages to standby/modified; working-set and "In use" numbers can fall without any allocation being freed. Dirty pages must be written before reuse. | Refaults across ~1000 processes when touched again; latency jitter | Cosmetic plus harmful risk. Already rejected in mitigation-design.md; this survey does not change that. |
| EmptyWorkingSet, single self-owned test process only | No (for that process's allocation) | Same eviction, contained to one test target | Refaults in that process only | Only candidate worth an isolated A/B if Enmao ever authorises execution research; not a system releaser. |
| Flush modified page list | No | Writes dirty pages out; modified list was ~0.12 GiB at 22:19 | Disk writes, I/O contention | Negligible target here. Reject. |
| System file cache trim / registry cache / page combining | No | Cache bookkeeping; no evidence of a cache problem on this host | Cache cold start; combining costs CPU | Reject on current evidence. |
| Terminate selected processes (what 360 publicly evidences: closing user-chosen programs) | YES - the only one that does | Frees that tree's private commit and working set. On this host the big lever is whole trees (e.g. Codex desktop tree ~8-12.5 GiB private), not any single process (<2 GiB) | Lost work / unsaved state if done by a tool | Effective but forbidden for this tool by red line. The lawful 360-like equivalent here is M1: the report names the tree, the human closes it in its own UI. |
| Allocate-and-release pressure (luojia65 style) | No | Forces paging of other processes, then frees its own allocation | Thrashing, pagefile pressure, can OOM bystanders | Reject outright; never implement. |
| Read-only pressure report (current M0) | No, by design | Names which owner trees grew when headroom crosses a threshold | None to the system | Keep as the default until Enmao decides otherwise. |

Blunt summary: every open-source "one-click RAM release" mechanism above
except process termination only moves pages between lists. On a host whose
problem is commit (64.7/95.1 GiB) from process breadth, with standby already
down to ~6.7 GiB and low hard faults, a standby/working-set releaser would
make the free-memory number prettier and the machine, if anything, slightly
slower afterwards. That is a testable claim, not a certainty - section 4 of
mitigation-design.md defines the A/B that could refute it (hard faults,
pages input, disk I/O, throughput, p95/p99 latency; not the free number).

### Licence / reuse conclusion (updated)

- Legally borrowable code, if code were ever wanted: MemListMgr (MIT, but
  VB6), WinMemClean (MIT OR Apache-2.0, Rust, 0 stars, service mode we do
  not want), Y-ASLant/MemoryCleaner (MIT, Rust, but tray GUI + auto-elevate
  + explorer-kill helper we do not want). Taking any of them still means
  taking their execution model, not just an API call.
- GPL-3.0 (memreduct, WinMemoryCleaner, H3d9): readable for mechanism facts;
  copying/translating code into this repo would impose GPL obligations on
  distribution. This private repo has no licence decision yet; do not import
  GPL code casually.
- No licence / binary only / unverified: stefanpejcic wrappers, middaysan
  (README-claimed MIT, no LICENSE file), luojia65, Sordum freeware, ISLC.
  Not reusable as source.
- Recommendation: do NOT fork a cleaner. If Enmao authorises a releaser at
  all, borrow the mechanism (documented Windows API facts, which are not
  copyrightable expression) and write a small single-mechanism version inside
  this repo's existing ctypes allowlist architecture, behind the gates in
  mitigation-design.md section 4. Until then keep M0 read-only.

Nothing in this section authorises implementation or execution. No candidate
tool was run; no EmptyStandbyList, purge, trim or kill was executed.
