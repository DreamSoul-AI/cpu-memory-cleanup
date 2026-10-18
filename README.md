# cpu-memory-cleanup

> **Authorisation update (2026-10-19, Asia/Shanghai):** Enmao explicitly
> authorised a 360-style one-click memory release button with A/B
> validation, superseding the read-only red line **only** for
> `MemoryEmptyWorkingSets` and `MemoryPurgeStandbyList` behind the explicit
> `--release` flag. Still absolutely forbidden: terminating any process,
> changing system settings / pagefile / registry, and auto-elevation
> (a missing privilege is reported as its NTSTATUS; the tool never
> elevates). Honesty rule: standby already counts as available, so a
> standby purge alone guarantees ~0 additional available, and a rising
> free number alone is never claimed as a benefit ? benefit must survive
> the refault A/B in docs/mitigation-design.md section 7. All other modes
> remain read-only and still reject `--execute` / `--clean`.
>
> **Privilege update (2026-10-19, Asia/Shanghai):** Enmao authorised that an
> explicit `--release` may enable `SeProfileSingleProcessPrivilege` in the
> tool's own token **only if the token already holds it**, and must disable it
> again immediately after the release calls (`cpu_memory_cleanup/privscope.py`).
> No UAC elevation, no new privileges, no other token or process touched.

Windows CPU / 主机 RAM 安全巡检工具。长期目标是为实验机器提供有证据、可预览、可追溯的周期性内存维护。当前默认仍是一次性只读巡检和有界只读采样。2026-10-19 授权的清理只有显式 `--release` 这一条入口（`MemoryEmptyWorkingSets` 与 `MemoryPurgeStandbyList`），用于 A/B，不是常驻清理。

Python 3.10+，Windows 10/11；只用标准库 ctypes 调用 Windows PSAPI / PDH，无第三方依赖，不需要安装包。当前实测环境见 [验证记录](docs/validation.md)。

## 红线

1. 绝不结束任何进程，尤其 Python / 训练 / 评测进程。也不停服务。working-set 清空和 standby 清除只存在于显式 `--release`（2026-10-19 授权，仅这两个调用），默认不跑。
2. 清理默认不执行。唯一入口是显式 `--release`；A/B 协议默认 dry-run，要加 `--ab-live` 才采样并释放。传入 `--execute` / `--clean` 会被参数解析拒绝。宁可少清不能误伤。
3. 不修改系统关键配置，不改页面文件设置，不改注册表，不关闭内存压缩来“腾内存”。不 UAC 提权。`SeProfileSingleProcessPrivilege` 只在显式 `--release` 期间、且本进程令牌已经持有它时启用，调用结束立即恢复关闭；不加新特权，不碰其它令牌或进程。

只读路径只绑定明确列出的查询接口。显式 `--release` 另调用已授权的 `NtSetSystemInformation`（命令 2 与 4），不打开其它进程句柄。进程句柄只请求 `PROCESS_QUERY_LIMITED_INFORMATION (0x1000)`，读取后关闭句柄。测试覆盖 API 白名单和进程权限掩码。读取失败显示 warning / null / skipped，不尝试更高权限。一次性巡检只向 stdout/stderr 输出。采样可用 `--jsonl` 追加日志；写日志失败会停止，不继续采样。原始报告只含进程文件名和占用，不含 PID、命令行或完整路径。采样默认不排名进程。

## 运行

在 PowerShell 中：

```powershell
Set-Location D:\GitHub\DreamSoul\cpu-memory-cleanup
python -m cpu_memory_cleanup
python -m cpu_memory_cleanup --json --top 10
# Authorised 2026-10-19 (see below): explicit opt-in 360-style release, one-click default both.
# ONLY this flag performs a release; every other mode stays read-only.
python -m cpu_memory_cleanup --release
python -m cpu_memory_cleanup --release --release-what standby --json

# A/B 协议。不加 --ab-live 就是 dry-run，不采样、不释放。
# --ab-what 默认 both；working-set 或 standby 只跑那一个已授权调用。
python -m cpu_memory_cleanup --ab-protocol logs\ab-out --ab-what standby
python -m cpu_memory_cleanup --ab-protocol logs\ab-out --ab-live --ab-what standby
python -m unittest discover -s tests -v

# 可选：保存只读报告；logs 已加入 gitignore
New-Item -ItemType Directory -Force logs | Out-Null
python -m cpu_memory_cleanup --json | Out-File -Encoding utf8 logs\snapshot.json

# 有界只读序列。必须同时给出间隔和次数；至少保留 JSONL 或 --json。
# 间隔 1..3600 秒，次数 1..10000。到次数即停止，不是后台服务。
python -m cpu_memory_cleanup --sample --interval 10 --count 360 --jsonl logs\series.jsonl

# 离线分析已有 JSONL（纯文件解析，不巡检、不调用系统接口）：
python -m cpu_memory_cleanup --analyze logs\idle-baseline.jsonl
python -m cpu_memory_cleanup --analyze logs\idle-baseline.jsonl --json

# 只读压力报告（不做任何动作；阈值是提案）：离线评估已有 tree-series 日志，
# 或有界跟随另一个窗口里 scripts\tree-series.ps1 正在追加的日志：
python -m cpu_memory_cleanup --pressure logs\tree-series.jsonl
python -m cpu_memory_cleanup --pressure-follow logs\tree-series.jsonl --polls 30 --poll-interval 60

# 离线汇总 tree-series 日志（进程树水位、成员年龄分桶、commit 残差；纯文件解析，不做任何动作）：
python -m cpu_memory_cleanup --tree-summary logs\tree-series-age.jsonl
```

文本以 GiB 显示，JSON 容量为整数 bytes，速率为每秒浮点数，缺失指标为 null；0 是实际取得的零。退出码 0 表示本次巡检或整段采样完成（可能有缺失指标，须读 warnings），1 表示核心 API、平台或日志写入错误，2 表示参数错误。采样期间数据持续变化，不是原子快照。`--top` 排名只覆盖可读取的进程，同名项对应不同进程；working set 含共享页，不能相加或当成可回收量。

`--sample` 默认不枚举进程，以降低开销；需要排名时加 `--with-processes`。同一轮采样共用一个 `series.run_id`。JSONL 以追加方式写入，多次运行会接在同一文件后，用 `run_id` 区分。父目录必须已存在。某一拍耗时超过间隔时，下一拍带 overrun 警告并继续，不会补采样。

报告包含物理总量 / 已用 / 可用、commit 使用 / 限额 / 启动以来峰值、standby 三类计数器及 cache / modified / free-zero、页面文件总量 / 使用量。一次性巡检还包含 working-set 排名及跳过数量。序列另含硬缺页相关速率和磁盘读写字节速率：`page_reads_per_sec` 是为满足硬缺页而发生的磁盘读操作次数，一次读可以带回多页；`pages_input_per_sec` 是读入的页数。速率计数器要有上一拍才有值，所以第一拍为 null，并各有一条 warning。这些是主机侧基线。实验吞吐和尾延迟仍由实验日志记录，用 UTC 时间对齐，本工具不采集它们。

理论可复用 standby 上界为 `min(standby core + normal + reserve, available)`；任何分类缺失时为未知。这些页面已经计入 available，不是新增可用 RAM。只读巡检报告里的保证新增可用量为 **0**，`cleanup_performed` 为 false。不把 working set、modified、压缩、页面文件计入估算，不承诺性能提升。`--release` 之后可用内存上升本身也不是收益，要看 A/B 的硬缺页、pages input 和磁盘 I/O。

## 非目标

当前不做 GUI、托盘、后台服务、定时清理、自动提权、GPU VRAM / CPU cache 清理、文件删除或安全擦除；也不负责定位所有内存泄漏。standby 缓存不是需要定期清掉的垃圾。后续是否值得实现自动清理，应由实验性能证据决定。

## 后续执行的验证门槛

详见 [调研、权限与风险](docs/research.md)。先建立只读时间序列基线，再在隔离测试环境重复对照官方工具操作前后：available / commit、硬缺页、磁盘 I/O、实验吞吐、尾延迟与冷缓存恢复。明确目标 Windows 构建和权限；拒绝权限时停止，不绕过或自动提权。

执行代码加入之前，必须具备默认 dry-run、明确单次执行入口、可靠的作用范围、操作前后日志、日志失败阻止执行、冷却 / 频率上限、失败停止且无激进回退。不能仅靠进程名识别实验进程；全局 standby 操作也会影响实验缓存。没有可重复的持续净收益就不启用。终止进程、关闭服务、修改关键配置等红线在后续阶段仍有效。

建议下一步：见下方待办第 9、10 项（两种机制拆开的 A/B，以及特权「持有但未启用」路径尚未实测的注明）。实验负载基线仍是第 5 项，没实验时不阻塞。

## 交接状态（2026-10-19）

- 只读能力仍在：一次性巡检（文本、`--json`、`--top`）、有界采样（`--sample`、`--interval`、`--count`、`--jsonl`）、压力报告、`--tree-summary`。没有后台服务。2026-10-17 的一次性巡检、2026-10-18 的两拍采样和 2026-10-18 20:09–20:24（Asia/Shanghai）的 90 样本 × 10 秒空闲基线（无实验负载）都已在本机跑过，原始输出和局限见 [验证记录](docs/validation.md)。
- 显式 `--release` 已实现（Enmao 2026-10-19 授权）：`NtSetSystemInformation` 的 `MemoryEmptyWorkingSets` 与 `MemoryPurgeStandbyList`，`--release-what working-set|standby|both`，默认 `both`。其它模式仍只读。`SeProfileSingleProcessPrivilege` 只在这次显式释放期间、且令牌已经持有时于本进程令牌启用，用完立即恢复（`cpu_memory_cleanup/privscope.py`）。不 UAC 提权。
- A/B 协议已实现：`python -m cpu_memory_cleanup --ab-protocol OUTDIR` 默认 dry-run；`--ab-live` 才采样，并在每个 AB 对的 B 窗开始前释放一次。默认形态 `AA,AB,AA,AB`，每窗 30 样本 × 10 秒，共 8 窗。
- 33baefb 的现场结论，口径如下：日常桌面负载、无实验负载；`AA,AB,AA,AB`；8 窗 × 30 样本 × 10 s；2026-10-19 05:20:44–05:59:40 Asia/Shanghai；两种机制合并为一次 `both` 释放（不是分开对照）。两次释放均 `STATUS_SUCCESS`。B 窗可用内存尾段中位数相对 A 约 +21.6 GiB 和 +5.0 GiB，commit 中位数变化约 +0.45 GiB 和 −0.16 GiB（没有达到 ≥0.5 GiB 的下降）。硬缺页与磁盘读超过本次 A/A 噪声上限。窗口级判定为 cosmetic-or-harmful。可用内存上升本身不算收益。细节见 [docs/validation.md](docs/validation.md) 的 Live A/B protocol 一节。这不是实验负载下的结论。
- [360 专项调研](docs/360_memory_cleanup.md)：官方资料确认加速球具有关闭进程的能力；具体是否修剪工作集、清 standby 及各自贡献比例仍未证实。standby 已计入 Available；工作集修剪不释放应用 commit，并会带来重新调页代价。绝不能结束任何进程，尤其训练 / 评测 / Python 进程。
- [GitHub 同类项目调研](docs/github_survey.md)：优先参考 System Informer 的分类和状态处理、MemListMgr 的单项动作拆分，以及 Mem Reduct 的冷却 / 日志思路；源码事实、README 声称、维护日期限制和许可证分别标注。尚未引入任何上游清理实现，也不采用自动提权或全局组合清理。
- 主机侧 Available、commit、硬缺页和磁盘 I/O 已能写入 JSONL。实验负载下与吞吐、尾延迟按 UTC 对齐仍未做（待办第 5 项）。2026-10-19 的授权只覆盖上面两个调用加 A/B，不把文档里的其它候选变成执行授权。
- 远程为私有仓库 DreamSoul-AI/cpu-memory-cleanup（不加 CI）。2026-10-18 已推送采样、空闲基线、离线分析器与本待办；以 `git ls-remote origin master` 与本地 HEAD 一致为准，不再等待逐次推送确认。

## 有序待办（5 分钟例行按此推进，不空等）

例行每轮取本清单第一个未完成的动作项推进；没有实验负载不是停工理由，
第 5 项只在真实实验恰好运行时顺带做。下一项是第 9 项，然后第 10 项。
只读巡检仍然是默认；清理只走已授权的 `--release`。

1. （已完成 2026-10-18 22:14，见 [docs/desktop-pressure-summary.md](docs/desktop-pressure-summary.md)；分析器已能按进程名汇总排名）(a) 日常桌面压力下的有界 `--with-processes` 只读采样：例如
   `--sample --interval 10 --count 90 --with-processes --jsonl logs\desktop-pressure.jsonl`，
   再用 `--analyze` 汇总，并从带进程排名的样本中列出 working-set 最高的
   所有者（同名多进程分开看；working set 含共享页，不相加、不当可回收量）。
   2026-10-18 晚间桌面应用本身已使可用内存约由 24 GiB 降到 10 GiB，
   这就是可用压力，不必只等实验负载。
2. （已完成 2026-10-18 22:19，计数器级只读交叉核对，见 [docs/validation.md](docs/validation.md)
   的 Memory gap cross-check；任务管理器 / RAMMap 人工对照未做，保留为可选）(b) 结论：
   缺口来自进程数量而不是单个大进程。1012 个进程私有 commit 合计约 48.1 GiB，
   分页 + 非分页池约 6.7 GiB，另有约 9.7 GiB commit 未被这些计数器归属；
   Codex 桌面应用进程树 546 个进程、私有 commit 约 9.9 GiB，其中 78 个
   computer-use `server.mjs` node 进程约 5.4 GiB，且全部是近几小时内新起的。
3. （已完成 2026-10-18 23:00，见 [docs/validation.md](docs/validation.md) 的 Per-owner-tree series）
   (e) 按进程树归属的有界只读时间序列：只读脚本 `scripts/tree-series.ps1`
   （CIM 只读 `Win32_Process` / `Win32_OperatingSystem`，每拍只写聚合，不写 PID、
   命令行或路径，到次数即停），日常桌面负载下 60 秒 × 30 样本，
   22:30:52–22:59:52（Asia/Shanghai）。结论：这段时间内不是单调累积。
   Codex 子树先从 484 个进程 / 9.18 GiB 降到 401 / 7.94 GiB，再随使用回升到
   593 / 10.69 GiB；期间新生 407 个、退出 298 个，退出多为 13 个一组，
   说明子进程确实会被回收，水位跟着使用活动走。
   22:19 单点快照里“累积不回收”的猜测不成立；单个 `server.mjs`
   能活多久仍未知（序列未记录每个成员的年龄）。
4. （脚本部分已完成 2026-10-18 23:12：`scripts/tree-series.ps1` 每拍新增 `age_buckets` 聚合，按 `server_mjs` / `cua_repl` / `node_repl` 分 lt10m、10to60m、1to3h、gt3h、unknown 五档写进程数与私有 commit，另写各类最老年龄；补静态测试和单拍只读冒烟测试，30 测试过。60 秒 × 120 的序列 23:12:06 起在跑，写 gitignored 的 `logs\tree-series-age.jsonl`，约 01:11 结束后离线分析写入 docs/validation.md。离线汇总工具 2026-10-19 已完成：`python -m cpu_memory_cleanup --tree-summary <JSONL>`（cpu_memory_cleanup/treesummary.py，纯文件解析、不调用系统接口、不做任何动作；旧日志缺字段时记为 null，不推测；新增 7 个测试，共 60 测试过）。中期读数（仅前 48/120 拍，23:12:06–23:59:06 Asia/Shanghai，60 秒间隔，日常桌面、无实验负载，序列未结束不作结论）：Codex 子树 687→208 个进程、私有 commit 12.50→4.73 GiB，server.mjs 92→24 个（6.41→1.67 GiB），说明大部分成员会退出；但有一小部分长期存活：末拍 >3 小时的 server.mjs 12 个约 0.84 GiB、cua_repl 6 个约 0.12 GiB、node_repl 18 个约 0.10 GiB，最老约 223.5 分钟。进程私有 commit 不等于可回收内存，这里只是取证）(f) 成员年龄分桶：给 `scripts/tree-series.ps1` 加 `server.mjs` / `cua-repl.mjs` /
   `node_repl.exe` 的年龄分桶计数与私有 commit（例如 <10 分钟、10–60 分钟、
   1–3 小时、>3 小时，只写聚合），补静态测试，再在日常桌面负载下跑一段
   更长的有界序列（如 60 秒 × 120）。回答：老实例是否长期不退、
   长期活着的部分占多少 commit。只读，不结束任何进程，只写证据。
5. (c) 负载基线：真实实验运行时顺带跑一段有界只读序列，并与实验
   吞吐、尾延迟按 UTC 对齐；没实验时跳过本项继续下一项，不阻塞。
6. （初稿完成 2026-10-18 23:30，见 [docs/mitigation-design.md](docs/mitigation-design.md)；第 4 项年龄分桶序列结束后据结果修订。2026-10-19 起仅对已授权的两个 `--release` 调用放宽「不实现清理」，见交接状态）(d) 基于证据的缓解方案设计文档：只写设计与验证门槛；没有可重复的持续净收益就不把清理当成默认行为。初稿结论：压力来自 commit 与进程数量，
   不是缓存；清 standby、修剪工作集等不符合现有证据，均不采用或推迟。
7. （已完成；离线评估部分 2026-10-18 23:24 完成：`python -m cpu_memory_cleanup --pressure <tree-series JSONL>`，阈值可配（`--free-below-gib` / `--commit-above-pct` / `--sustain`，默认值只是提案），只读文件、不做任何动作；新增 10 个测试，共 40 测试过；结果见 [docs/mitigation-design.md](docs/mitigation-design.md) 第 3 节。实时有界模式已完成 2026-10-18 23:34：`python -m cpu_memory_cleanup --pressure-follow <tree-series JSONL> --polls N --poll-interval S` 只读跟随另一个进程正在追加的 tree-series 日志（每轮只以 rb 打开这一个文件，不采样、不调用系统接口、不做任何动作，到轮数即停），逐拍报阈值状态，连续达标时报一次哪些进程树在增长；新增 13 个测试，共 53 测试过；对正在跑的 age 序列实跑 2 轮×61 秒，读到 22 拍、0 告警。本项全部完成）(g) 只读压力报告（方案文档 M0）：复用有界采样与 `scripts/tree-series.ps1` 的聚合，
   按阈值（可配置，如可用内存低、commit 接近上限且连续多拍）输出哪些进程树在增长；
   只写 stdout / JSONL，不做任何动作、不常驻；补阈值单元测试与静态白名单测试。
8. （第一点 2026-10-18 23:39 单点复测 + 脚本字段 2026-10-18 23:50 已完成，见 [docs/validation.md](docs/validation.md)）(h) 未归属 commit 的只读归因：
   22:19 快照约 9.7 GiB、23:38:55 复测约 9.28 GiB（占 commit 15.8%）不能由进程私有
   commit 与分页 / 非分页池 / 系统驱动解释；进程数 1012→769 时残差仍接近，GPU 共享
   仅 0.279 GiB 可排除。`scripts/tree-series.ps1` 现已每拍写入只读 `commit_residual`
   （process_private_gib、paged_pool_gib、nonpaged_pool_gib、system_driver_gib、
   residual_gib、residual_pct_of_commit；公式 residual = commit_used - private -
   pools - driver；Get-Counter 失败则字段为 null，不中断序列）。正在跑的 age 序列
   （run_id f813ba37）启动早于本改动，不含这些字段；下一轮序列可用来看残差是否随
   进程数变化。2026-10-19 00:04:56（Asia/Shanghai）起已用当前脚本另起一段 60 秒 × 120 的有界只读残差序列（run_id f9059e66，写 gitignored 的 `logs\tree-series-residual.jsonl`，约 02:04 结束；与 age 序列重叠的约一小时内两个只读采样器同时运行），首拍残差 8.466 GiB（占 commit 14.2%，696 个进程，日常桌面、无实验负载，单拍不作结论）；结束后用 `--tree-summary` 看残差与进程数的相关性。RAMMap 人工对照仍可选。只读，不做任何动作。
9. （选项已加，现场拆分未跑）把两种已授权机制拆开各做一次 A/B，只用 `MemoryPurgeStandbyList` 与 `MemoryEmptyWorkingSets`。`--ab-protocol --ab-what standby|working-set` 已能单选，默认仍是 `both`。协议和口径与 33baefb / `logs/ab-live-20261019-r2` 相同：日常桌面负载，`AA,AB,AA,AB`，8 窗 × 30 样本 × 10 s，脱离编辑器进程运行。各用新的日志目录。先 standby-purge-only，跑完把判定写入 [docs/validation.md](docs/validation.md)，测试通过后提交并推送，再跑 working-set-only。每次开跑前确认没有其它采样序列在跑。working-set-only 要记录 modified 列表和回写磁盘的量；磁盘写入或硬缺页明显失控就中止并如实记录。
10. （未做）在 [docs/validation.md](docs/validation.md) 注明：r2 两次释放前，token 里的 `SeProfileSingleProcessPrivilege` 已经是 enabled，所以「持有但未启用 → 在显式 `--release` 时启用 → 用完恢复」这条路径还没有实测。

空闲基线的离线汇总见 [docs/idle-baseline-summary.md](docs/idle-baseline-summary.md)
（空闲基线、非实验负载；其中约 20:11（Asia/Shanghai）的释放来自外部，
不是本工具的清理收益）。
