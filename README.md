# cpu-memory-cleanup

Windows CPU / 主机 RAM 安全巡检工具。长期目标是为实验机器提供有证据、可预览、可追溯的周期性内存维护；**当前阶段是一次性只读巡检，外加有界只读采样，没有任何清理执行能力**。

Python 3.10+，Windows 10/11；只用标准库 ctypes 调用 Windows PSAPI / PDH，无第三方依赖，不需要安装包。当前实测环境见 [验证记录](docs/validation.md)。

## 红线

1. 绝不结束任何进程，尤其 Python / 训练 / 评测进程。本阶段连 working-set 修剪等改变其他进程内存状态的动作也不执行。
2. 以后清理必须默认 dry-run / 先报告再执行并记录日志；宁可少清不能误伤。本版本根本没有清理函数或执行开关，传入 `--execute` / `--clean` 会被参数解析拒绝。
3. 不修改系统关键配置，不关闭服务，不改页面文件设置，不关闭内存压缩来“腾内存”。不自动申请管理员权限或调整令牌特权。

代码只绑定明确列出的查询接口；进程句柄只请求 `PROCESS_QUERY_LIMITED_INFORMATION (0x1000)`，读取后关闭句柄。测试覆盖 API 白名单和进程权限掩码。读取失败显示 warning / null / skipped，不尝试更高权限。一次性巡检只向 stdout/stderr 输出。采样可用 `--jsonl` 追加日志；写日志失败会停止，不继续采样。原始报告只含进程文件名和占用，不含 PID、命令行或完整路径。采样默认不排名进程。

## 运行

在 PowerShell 中：

```powershell
Set-Location D:\GitHub\DreamSoul\cpu-memory-cleanup
python -m cpu_memory_cleanup
python -m cpu_memory_cleanup --json --top 10
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
```

文本以 GiB 显示，JSON 容量为整数 bytes，速率为每秒浮点数，缺失指标为 null；0 是实际取得的零。退出码 0 表示本次巡检或整段采样完成（可能有缺失指标，须读 warnings），1 表示核心 API、平台或日志写入错误，2 表示参数错误。采样期间数据持续变化，不是原子快照。`--top` 排名只覆盖可读取的进程，同名项对应不同进程；working set 含共享页，不能相加或当成可回收量。

`--sample` 默认不枚举进程，以降低开销；需要排名时加 `--with-processes`。同一轮采样共用一个 `series.run_id`。JSONL 以追加方式写入，多次运行会接在同一文件后，用 `run_id` 区分。父目录必须已存在。某一拍耗时超过间隔时，下一拍带 overrun 警告并继续，不会补采样。

报告包含物理总量 / 已用 / 可用、commit 使用 / 限额 / 启动以来峰值、standby 三类计数器及 cache / modified / free-zero、页面文件总量 / 使用量。一次性巡检还包含 working-set 排名及跳过数量。序列另含硬缺页相关速率和磁盘读写字节速率：`page_reads_per_sec` 是为满足硬缺页而发生的磁盘读操作次数，一次读可以带回多页；`pages_input_per_sec` 是读入的页数。速率计数器要有上一拍才有值，所以第一拍为 null，并各有一条 warning。这些是主机侧基线。实验吞吐和尾延迟仍由实验日志记录，用 UTC 时间对齐，本工具不采集它们。

理论可复用 standby 上界为 `min(standby core + normal + reserve, available)`；任何分类缺失时为未知。这些页面已经计入 available，不是新增可用 RAM。保证新增可用量为 **0**，实际清理量为 **0**。不把 working set、modified、压缩、页面文件计入估算，不承诺性能提升。

## 非目标

当前不做 GUI、托盘、后台服务、定时清理、自动提权、GPU VRAM / CPU cache 清理、文件删除或安全擦除；也不负责定位所有内存泄漏。standby 缓存不是需要定期清掉的垃圾。后续是否值得实现自动清理，应由实验性能证据决定。

## 后续执行的验证门槛

详见 [调研、权限与风险](docs/research.md)。先建立只读时间序列基线，再在隔离测试环境重复对照官方工具操作前后：available / commit、硬缺页、磁盘 I/O、实验吞吐、尾延迟与冷缓存恢复。明确目标 Windows 构建和权限；拒绝权限时停止，不绕过或自动提权。

执行代码加入之前，必须具备默认 dry-run、明确单次执行入口、可靠的作用范围、操作前后日志、日志失败阻止执行、冷却 / 频率上限、失败停止且无激进回退。不能仅靠进程名识别实验进程；全局 standby 操作也会影响实验缓存。没有可重复的持续净收益就不启用。终止进程、关闭服务、修改关键配置等红线在后续阶段仍有效。

建议下一步：在真实实验负载下跑一段有界只读序列，并与实验吞吐、尾延迟按 UTC 对齐；暂不加清理执行。

## 交接状态（2026-10-18）

- 当前有一次性只读巡检（文本、`--json`、`--top`）和有界只读采样（`--sample`、`--interval`、`--count`、`--jsonl`）。没有后台服务，也没有清理执行。2026-10-17 的一次性巡检、2026-10-18 的两拍采样和 2026-10-18 20:09–20:24（Asia/Shanghai）的 90 样本 × 10 秒空闲基线（无实验负载）都已在本机跑过，原始输出和局限见 [验证记录](docs/validation.md)，不代表长期性能或清理收益已验证。
- [360 专项调研](docs/360_memory_cleanup.md)：官方资料确认加速球具有关闭进程的能力；具体是否修剪工作集、清 standby 及各自贡献比例仍未证实。standby 已计入 Available；工作集修剪不释放应用 commit，并会带来重新调页代价。绝不能结束任何进程，尤其训练 / 评测 / Python 进程。
- [GitHub 同类项目调研](docs/github_survey.md)：优先参考 System Informer 的分类和状态处理、MemListMgr 的单项动作拆分，以及 Mem Reduct 的冷却 / 日志思路；源码事实、README 声称、维护日期限制和许可证分别标注。尚未引入任何上游清理实现，也不采用自动提权或全局组合清理。
- 主机侧 Available、commit、硬缺页和磁盘 I/O 已能写入 JSONL。下一步是在实验负载下采样，并把实验吞吐、尾延迟按 UTC 对齐；再决定是否有必要进入隔离环境的执行验证。清理执行阶段等待 Enmao 审阅后另定，不能将文档中的候选视为执行授权。
- 远程为私有仓库 DreamSoul-AI/cpu-memory-cleanup（不加 CI）。2026-10-18 已推送采样、空闲基线、离线分析器与本待办；以 `git ls-remote origin master` 与本地 HEAD 一致为准，不再等待逐次推送确认。

## 有序待办（5 分钟例行按此推进，不空等）

例行每轮取本清单第一个未完成项推进；没有实验负载不是停工理由，
(c) 只在真实实验恰好运行时顺带做。全部只读，不实现清理执行。

1. (a) 日常桌面压力下的有界 `--with-processes` 只读采样：例如
   `--sample --interval 10 --count 90 --with-processes --jsonl logs\desktop-pressure.jsonl`，
   再用 `--analyze` 汇总，并从带进程排名的样本中列出 working-set 最高的
   所有者（同名多进程分开看；working set 含共享页，不相加、不当可回收量）。
   2026-10-18 晚间桌面应用本身已使可用内存约由 24 GiB 降到 10 GiB，
   这就是可用压力，不必只等实验负载。
2. (b) 一次人工交叉核对：同一时刻把一次性巡检快照与任务管理器 /
   RAMMap（若可用）对照，把差异写进 `docs/validation.md`。
3. (c) 负载基线：真实实验运行时顺带跑一段有界只读序列，并与实验
   吞吐、尾延迟按 UTC 对齐；没实验时跳过本项继续下一项，不阻塞。
4. (d) 基于证据的缓解方案设计文档：只写设计与验证门槛，仍不实现
   任何清理执行；没有可重复的持续净收益就不启用清理。

空闲基线的离线汇总见 [docs/idle-baseline-summary.md](docs/idle-baseline-summary.md)
（空闲基线、非实验负载；其中约 20:11（Asia/Shanghai）的释放来自外部，
不是本工具的清理收益）。
