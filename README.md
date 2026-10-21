# CPU 内存清理
Windows CPU 与内存观察、离线分析和显式单项维护工具。Python 3.10+，使用标准库 ctypes、tkinter 和 unittest。

默认只读。CPU 只做诊断，不自动结束进程或调整调度策略。工作集与 Standby 维护一次只选一种，可用内存上升不等于加速收益。

## 文档入口

- [项目协作约定](AGENTS.md)：关联资料、规范和操作边界
- [通用调研](<docs/CPU 内存清理 通用调研.md>)：360 产品对照、技术机制、历史证据及限制
- [架构设计](<docs/CPU 内存清理 架构设计.md>)：用户交互、功能、系统三层的结构视图与流程视图，以及部署和质量目标
- [代码结构设计](<docs/CPU 内存清理 代码结构设计.md>)：`src/cpu_memory_cleanup/` 内部的模块目录、文件职责、依赖与迁移状态
- [测试说明](tests/README.md)：测试计划、元数据、报告与未验证范围

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
│   ├── desktop-memory-ui.cmd      桌面兼容入口
│   ├── desktop-memory-button.cmd  按钮命令兼容入口
│   ├── tree-series.ps1            有界采样兼容入口
│   └── run-tests.py               测试选择、执行与结果保存
├── tests/
│   ├── src/cpu_memory_cleanup/  产品源码镜像
│   ├── scripts/                 脚本镜像
│   ├── README.md                测试映射、计划与回执
│   └── manifest.json            唯一测试声明
└── docs/                    通用调研、架构设计、代码结构设计
```

用例统一负责观察、回看与单次维护。两个界面不直接调用原生维护，导入模块不采样、不打开界面。旧旁路实现已移除。

源码已按 `interaction/`、`application/`、`system/` 分包，具体操作系统接入收在 `system/platforms/`。记录访问和运行支撑由系统层提供，功能层通过启动注入使用。当前仅实现 Windows 实时接入，未适配系统会明确拒绝实时操作，离线回看不依赖具体接入加载。

测试文件的具体映射与辅助数据组织见 [测试说明](tests/README.md)。测试镜像当前沿用源码路径，调整目录时仍需保持目标可定位、测试发现与清单一致。产品运行记录写入调用方指定的位置，测试回执写入独立结果目录，均不属于产品源码。

## 安装与启动
产品包位于 `src/`，本地开发先在仓库根目录安装可编辑包，再使用 `python -m cpu_memory_cleanup`。使用当前 Python 环境已有的构建工具，安装命令不拉取运行依赖；产品运行依赖仍为标准库。

```powershell
python -m pip install --no-build-isolation --no-deps -e .
```

三个兼容启动脚本从自身位置定位仓库，将 `src` 加入 `PYTHONPATH` 并保留已有值，因此也可从检出的工作区启动。统一测试入口自行定位源码与测试目录，命令仍为 `python scripts/run-tests.py`。

## 只读与离线使用
下面是安装后的命令说明。本轮仅做安装包的 `--help` 启动检查，未执行真实采样或维护命令。除 `--help` 和离线回看外，执行会读取当前 Windows 指标。

```powershell
python -m cpu_memory_cleanup --help
python -m cpu_memory_cleanup --json
python -m cpu_memory_cleanup --sample --count 2 --interval 1 --with-processes --top 10 --json
python -m cpu_memory_cleanup --review 保存的运行目录 --json
python -m cpu_memory_cleanup --ab-compare A运行目录 B运行目录 --json
```

采样可通过 `--output-dir 目录` 保存独立运行，或通过 `--jsonl 新文件路径` 保存指定样本文件。目标 JSONL 必须不存在，不追加到旧运行。`--top` 限制解释中的工作集排名，完整进程样本保留给 CPU 差分，不能把共享工作集相加当成可回收量。

`--analyze`、`--pressure`、`--tree-summary` 是离线回看的兼容别名，输出统一为 RunReport。历史格式仍可读取，但缺少条件、版本、时区或终态时明确标为不完整。比较保留数值变化，缺少校准、任务指标或完整动作证据时只报告证据不足。

`scripts/tree-series.ps1` 保留 IntervalSeconds、Count、JsonlPath、TopOwners 参数，转发到 Python 有界采样。Count 上限为1000，输出为新记录格式。它不再采集旧版 owner、Codex 树和年龄桶，旧聚合文件只支持离线回看。当前进程父链不足时，不猜测树归属。

## 显式单项维护与桌面
维护必须同时指定 `--release`、`--release-what working-set` 或 `--release-what standby`、`--output-dir 目录`。不再接受缺省组合或 `both`。一次请求至多一次原生调用，未满足权限或记录条件时零调用，后置失败保留已发生的调用事实。

`scripts/desktop-memory-ui.cmd` 转发到 `python -m cpu_memory_cleanup --gui`，打开时只读。桌面以三张卡片显示 CPU、可用物理内存和系统提交量，同时显示采样时间、覆盖与缺失原因。只读刷新和取消位于诊断区，维护位于独立区域，需选择机制和保存目录。关闭窗口会停止接单，并等待已有工作收尾。当前仅验证假界面行为，实际字体、缩放和键盘呈现待验收。

`scripts/desktop-memory-button.cmd` 无参数只读，显式 `release 保存目录` 才请求一次工作集维护。程序不提权、不结束业务进程、不停服务、不改页面文件或系统设置。真实维护可能产生缺页和 I/O 代价，内部 NT 路线仍需按实际 Windows 构建验证。

旧的自动 A/B、`--ab-null`、日志跟随、独立预检和 CLI 自定义阈值入口已明确停用，解析时返回错误，不调用系统。实验编排需要另行固定方案与验证条件，不自动串联维护。

## 验证与结果

```powershell
python scripts/run-tests.py --plan quick
python scripts/run-tests.py --plan readonly_offline
python scripts/run-tests.py --plan regression
python scripts/run-tests.py --rebuild-report .test-results/运行ID
```

唯一声明来源为 `tests/manifest.json`。产品测试的完整模块名使用 `tests.src.cpu_memory_cleanup` 前缀；迁移前回执保留当时的路径和测试标识，当前布局的运行回执统一见 [测试说明](tests/README.md)。默认计划全部使用合成数据、假 DLL、假界面或受控测试工作进程。结果保存在 `.test-results/<run_id>/`，包含运行清单、逐项事件和可从磁盘重建的报告。未启用 GitHub Actions。

真实 Windows 采集、GUI 显示、特权路径和清理效果均未做本轮平台验证。历史桌面 A/B 没有证明持续净收益，原始 `logs/` 已删除，不重建或以新数据冒充。

只读诊断验收计划见 [测试说明](tests/README.md) 第七节。9 项实机检查、环境矩阵和资源预算已登记在唯一清单的 acceptance_plans 下，当前禁用，不会被 quick、regression 或 --all 自动执行。readonly_offline 只运行假接入准备检查，不代表实机验收通过。

构建 wheel 使用干净检出或隔离的源码目录，并核对产物与当前源码一致，避免旧构建缓存带入已删除的平铺模块。本轮仅验证本地安装包及离线启动，不作为发布验收。
