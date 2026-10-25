# CPU 内存清理
360加速球式的轻量内存清理桌面工具。主窗口显示内存，绿色按钮执行一次工作集整理。Python 3.10+，使用标准库 ctypes、tkinter 和 unittest。

打开只读，点击才清理。桌面固定工作集方式，CPU 与历史分析命令属于辅助工具。可用内存上升不等于加速收益。

## 文档入口

- [项目协作约定](<AGENTS.md>)：关联资料、规范和操作边界
- [通用调研](<docs/CPU 内存清理 通用调研.md>)：360 产品对照、技术机制、历史证据及限制
- [架构设计 Architecture Design](<docs/CPU 内存清理 架构设计 Architecture Design.md>)：用户交互、功能、系统三层的结构视图与流程视图，以及部署和质量目标
- [代码结构设计 Code Structure Design](<docs/CPU 内存清理 代码结构设计 Code Structure Design.md>)：`src/cpu_memory_cleanup/` 内部的模块目录、文件职责、依赖与实现边界
- [开发计划 Development Plan](<docs/CPU 内存清理 开发计划 Development Plan.md>)：开发目标、任务、依赖和验收条件
- [开发记录](<docs/开发记录/README.md>)：按日期记录代码结构、模块职责和工程实现的演进
- [运行记录](<docs/运行记录/README.md>)：按日期归档主机观察、修复验证、测试与打包记录
- [缺陷记录 Bug Report](<docs/CPU 内存清理 缺陷记录 Bug Report.md>)：待处理与待验证问题，已解决项归档到运行记录
- [通用测试规范 Test Specification](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf)：直接采用飞书共用规范，正文同步于关联 DreamSoul 资料库

## 关联的 DreamSoul 资料
本仓库：[DreamSoul-AI/cpu-memory-cleanup](https://github.com/DreamSoul-AI/cpu-memory-cleanup)。每次从当前项目关联文件夹和资料链接定位 DreamSoul 文档库，读取相关规范与已有研究，再沿用既有飞书分享链接。使用资料库的逻辑目录，不写死机器盘符或用户名。

本地资料通常从 `文档/规范/` 与 `研发/研究/` 定位。未关联时，从 [飞书规范总索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 和 [工具与教程总索引](https://dreamsoul.feishu.cn/docx/VSQ5dlJl3o0hbIxrEhUc8qKEnBc) 查找，具体任务入口见 AGENTS.md。

## 仓库目录导航

下面记录当前仓库布局。`src/cpu_memory_cleanup/` 内部结构按代码结构设计约束，外围目录的位置和组织属于软约定，可以按实际需要调整，同时更新包发现、启动与测试入口及文档引用。

```text
cpu-memory-cleanup/
├── AGENTS.md               项目协作、资料定位和执行边界
├── README.md               使用说明与当前仓库导航
├── pyproject.toml           src 包发现与安装元数据
├── src/
│   └── cpu_memory_cleanup/  产品源码，内部设计见代码结构设计
├── scripts/
│   ├── desktop-memory-ui.pyw      无控制台桌面入口
│   ├── desktop-memory-ui.cmd      脱离终端的兼容入口
│   └── run-tests.py               测试选择、执行与结果保存
├── tests/
│   ├── src/cpu_memory_cleanup/  产品源码镜像
│   ├── scripts/                 脚本镜像
│   ├── fixtures.py              离线测试应用装配
│   └── manifest.json            全局测试计划与预算入口
└── docs/                    调研与设计、开发记录、运行记录、缺陷记录
```

用例统一负责观察、回看与单次维护。两个界面不直接调用原生维护，导入模块不采样、不打开界面。旧旁路实现已移除。

源码已按 `interaction/`、`application/`、`system/` 分包，具体操作系统接入收在 `system/platforms/`。记录访问和运行支撑由系统层提供，功能层通过启动注入使用。当前仅实现 Windows 实时接入，未适配系统会明确拒绝实时操作，离线回看不依赖具体接入加载。

相关测试目录按需设置局部 `manifest.json`，只描述该目录文件及子树的默认值和例外。测试代码与源码的对应关系见代码结构设计，辅助装配位于 `tests/fixtures.py`。测试编写和执行遵循 [通用测试规范 Test Specification](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf)。测试镜像当前沿用源码路径，调整目录时仍需保持目标可定位、测试发现与清单一致。产品运行记录写入调用方指定的位置，测试回执写入独立结果目录，均不属于产品源码。

## 产品定位与当前行为
当前按历史内存小窗口重新实现：大号占用百分比、占用条、可用量与提交量、绿色“一键清理”及前后数值。打开后只读刷新内存显示，点击绿色按钮才清理，不需要 CPU 诊断或方案选择。产品范围见 [开发计划](<docs/CPU 内存清理 开发计划 Development Plan.md#七内存清理插件的产品基线>)。

## 安装与启动
产品包位于 `src/`，本地开发先在仓库根目录安装可编辑包，再使用 `python -m cpu_memory_cleanup`。使用当前 Python 环境已有的构建工具，安装命令不拉取运行依赖；产品运行依赖仍为标准库。

```powershell
python -m pip install --no-build-isolation --no-deps -e .
```

桌面启动入口从自身位置定位源码。`.pyw` 使用窗口版 Python，直接启动不创建控制台；CMD 兼容入口改用 `start` 启动 `pythonw` 后退出，不再保留背后的终端。直接双击 `.pyw` 需要系统已有对应文件关联，CMD 直接把文件交给 `pythonw`，不依赖该关联，启动可能有短暂闪窗。统一测试入口自行定位源码与测试目录，命令仍为 `python scripts/run-tests.py`。

## 辅助只读与离线命令
下面是安装后的命令说明。安装包与离线验证的执行结果见运行记录，示例本身不表示已经执行。除 `--help` 和离线回看外，执行会读取当前 Windows 指标。

```powershell
python -m cpu_memory_cleanup --help
python -m cpu_memory_cleanup --json
python -m cpu_memory_cleanup --sample --count 2 --interval 1 --with-processes --top 10 --json
python -m cpu_memory_cleanup --review 保存的运行目录 --json
python -m cpu_memory_cleanup --ab-compare A运行目录 B运行目录 --json
```

`--output-dir` 指向输出基目录，运行在其中创建独立子目录，基目录可以已经存在。采样可通过 `--output-dir 目录` 保存独立运行，或通过 `--jsonl 新文件路径` 保存指定样本文件。目标 JSONL 必须不存在，不追加到旧运行。`--top` 限制解释中的工作集排名，完整进程样本保留给 CPU 差分，不能把共享工作集相加当成可回收量。

`--analyze`、`--pressure`、`--tree-summary` 是离线回看的兼容别名，输出统一为 RunReport。历史格式仍可读取，但缺少条件、版本、时区或终态时明确标为不完整。比较保留数值变化，缺少校准、任务指标或完整动作证据时只报告证据不足。

有界采样统一使用上面的 Python 命令。历史聚合文件只支持离线回看，当前进程父链不足时不猜测树归属。

## 显式单项维护与桌面
维护必须同时指定 `--release`、`--release-what working-set` 或 `--release-what standby`、`--output-dir 目录`。不再接受缺省组合或 `both`。一次请求至多一次原生调用，未满足权限或记录条件时零调用，后置失败保留已发生的调用事实。

使用 `scripts/desktop-memory-ui.pyw` 或原 CMD 入口打开小窗口。当前 Windows 接入使用轻量只读接口，窗口存活时约每500毫秒尝试刷新一次，清理期间继续更新，关闭后停止。不会自动清理。点击绿色“一键清理”即明确提交一次系统工作集整理，不清 Standby、不结束进程，也不要求先运行诊断。

百分比、占用条、可用量与提交量在真实读数之间以约400毫秒缓动过渡，不将动画值写入动作记录。桌面清理前后快照只取内存，不等待 PDH 或枚举进程，命令行完整采集保持原样。清理前后分别显示可用量与提交量，动作失败、结果未知或恢复未确认时保留原因。默认记录位置为当前用户主目录下的 `.cpu-memory-cleanup/runs/`，也可通过已有 `--output-dir` 指定。目录和记录在用户发起清理后由核心准备，无法保存则阻止动作。忙时不排队，不自动重试。真实采样与清理仍未在本轮执行。

程序不提权、不结束业务进程、不停服务、不改页面文件或系统设置。真实维护可能产生缺页和 I/O 代价，内部 NT 路线仍需按实际 Windows 构建验证。

旧的自动 A/B、`--ab-null`、日志跟随、独立预检和 CLI 自定义阈值入口已明确停用，解析时返回错误，不调用系统。实验编排需要另行固定方案与验证条件，不自动串联维护。

## 验证与结果

```powershell
python scripts/run-tests.py --plan quick
python scripts/run-tests.py --plan readonly_offline
python scripts/run-tests.py --plan regression
python scripts/run-tests.py --plan regression --list
python scripts/run-tests.py --rebuild-report .test-results/运行ID
```

日常完整离线检查执行 `python scripts/run-tests.py --plan regression`，其中已包含 quick 阶段。`tests/fixtures.py` 只复用合成运行信息与应用装配；根 `tests/manifest.json` 保留全局预算、计划和禁用实机验收草案，局部清单就近提供默认值与方法差异；普通方法自动发现，执行器统一展开，计划所选项全部为必需项。产品测试的完整模块名使用 `tests.src.cpu_memory_cleanup` 前缀；迁移前回执保留当时的路径和测试标识，当前布局的运行回执统一见 [运行记录](<docs/运行记录/README.md>)。默认计划全部使用合成数据、假 DLL、假界面或受控测试工作进程。结果保存在 `.test-results/<run_id>/`，包含运行清单、逐项事件和可从磁盘重建的报告。`--list` 只显示最终名单及 `config_sources` 来源链，不启动测试；每次运行的回执也保存来源链。未启用 GitHub Actions。

CFG-01 已修复，根入口恢复分层加载，并阻止旧根格式静默忽略子清单，验证见 [本轮运行记录](<docs/运行记录/2026-10-26 桌面诊断与配置修复.md>)。

真实 Windows 采集、GUI 显示、特权路径和清理效果均未做本轮平台验证。历史桌面 A/B 没有证明持续净收益，原始 `logs/` 已删除，不重建或以新数据冒充。

只读诊断验收安排见开发计划中的“只读诊断验收计划”，实际执行配置以 `tests/manifest.json` 为准。9 项实机检查、环境矩阵和资源预算已登记在根清单的 acceptance_plans 下，当前禁用，不会被 quick、regression 或 --all 自动执行。readonly_offline 只运行假接入准备检查，不代表实机验收通过。

构建 wheel 使用干净检出或隔离的源码目录，并核对产物与当前源码一致，避免旧构建缓存带入已删除的平铺模块。本轮仅验证本地安装包及离线启动，不作为发布验收。
