# cpu-memory-cleanup

Windows CPU / 主机 RAM 安全巡检工具。长期目标是为实验机器提供有证据、可预览、可追溯的周期性内存维护；**当前阶段只有一次性只读巡检，没有任何清理执行能力**。

Python 3.10+，Windows 10/11；只用标准库 ctypes 调用 Windows PSAPI / PDH，无第三方依赖，不需要安装包。当前实测环境见 [验证记录](docs/validation.md)。

## 红线

1. 绝不结束任何进程，尤其 Python / 训练 / 评测进程。本阶段连 working-set 修剪等改变其他进程内存状态的动作也不执行。
2. 以后清理必须默认 dry-run / 先报告再执行并记录日志；宁可少清不能误伤。本版本根本没有清理函数或执行开关，传入 `--execute` / `--clean` 会被参数解析拒绝。
3. 不修改系统关键配置，不关闭服务，不改页面文件设置，不关闭内存压缩来“腾内存”。不自动申请管理员权限或调整令牌特权。

代码只绑定明确列出的查询接口；进程句柄只请求 `PROCESS_QUERY_LIMITED_INFORMATION (0x1000)`，读取后关闭句柄。测试覆盖 API 白名单和进程权限掩码。读取失败显示 warning / null / skipped，不尝试更高权限。CLI 本身只向 stdout/stderr 输出；要留日志由用户在仓库内重定向。原始报告只含进程文件名和占用，不含 PID、命令行或完整路径。

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
```

文本以 GiB 显示，JSON 容量为整数 bytes，缺失指标为 null；0 是实际取得的零。退出码 0 表示核心巡检成功（可能有缺失指标，须读 warnings），1 表示核心 API 或平台错误，2 表示参数错误。采样期间数据持续变化，不是原子快照。`--top` 排名只覆盖可读取的进程，同名项对应不同进程；working set 含共享页，不能相加或当成可回收量。

报告包含物理总量 / 已用 / 可用、commit 使用 / 限额 / 启动以来峰值、standby 三类计数器及 cache / modified / free-zero、页面文件总量 / 使用量、working-set 排名及跳过数量。

理论可复用 standby 上界为 `min(standby core + normal + reserve, available)`；任何分类缺失时为未知。这些页面已经计入 available，不是新增可用 RAM。保证新增可用量为 **0**，实际清理量为 **0**。不把 working set、modified、压缩、页面文件计入估算，不承诺性能提升。

## 非目标

当前不做 GUI、托盘、后台服务、定时清理、自动提权、GPU VRAM / CPU cache 清理、文件删除或安全擦除；也不负责定位所有内存泄漏。standby 缓存不是需要定期清掉的垃圾。后续是否值得实现自动清理，应由实验性能证据决定。

## 后续执行的验证门槛

详见 [调研、权限与风险](docs/research.md)。先建立只读时间序列基线，再在隔离测试环境重复对照官方工具操作前后：available / commit、硬缺页、磁盘 I/O、实验吞吐、尾延迟与冷缓存恢复。明确目标 Windows 构建和权限；拒绝权限时停止，不绕过或自动提权。

执行代码加入之前，必须具备默认 dry-run、明确单次执行入口、可靠的作用范围、操作前后日志、日志失败阻止执行、冷却 / 频率上限、失败停止且无激进回退。不能仅靠进程名识别实验进程；全局 standby 操作也会影响实验缓存。没有可重复的持续净收益就不启用。终止进程、关闭服务、修改关键配置等红线在后续阶段仍有效。

建议下一步：增加有界周期性**只读**采样和 JSONL 日志，先观察实验负载下的真实压力；暂不加清理执行。

## 交接状态（2026-10-18）

- 当前只有一次性只读巡检，支持文本、`--json` 和 `--top`；未实现周期采样、后台服务或任何清理执行。2026-10-17 已在本机实跑两种格式并通过 9 项测试，原始输出和局限见 [验证记录](docs/validation.md)，不代表长期性能或清理收益已验证。
- [360 专项调研](docs/360_memory_cleanup.md)：官方资料确认加速球具有关闭进程的能力；具体是否修剪工作集、清 standby 及各自贡献比例仍未证实。standby 已计入 Available；工作集修剪不释放应用 commit，并会带来重新调页代价。绝不能结束任何进程，尤其训练 / 评测 / Python 进程。
- [GitHub 同类项目调研](docs/github_survey.md)：优先参考 System Informer 的分类和状态处理、MemListMgr 的单项动作拆分，以及 Mem Reduct 的冷却 / 日志思路；源码事实、README 声称、维护日期限制和许可证分别标注。尚未引入任何上游清理实现，也不采用自动提权或全局组合清理。
- 接手后先做有界只读采样和 JSONL 日志，建立 Available、commit、缺页、I/O、实验吞吐与尾延迟基线；再决定是否有必要进入隔离环境的执行验证。清理执行阶段等待 Enmao 审阅后另定，不能将文档中的候选视为执行授权。
- 本仓库目前仅有本地 Git 提交，没有 remote，也没有 push；等待 Enmao 明确指示后再创建或配置远程仓库。
