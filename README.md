# CPU 内存清理
Windows CPU 与内存观察、离线分析和显式单项维护工具。Python 3.10+，使用标准库 ctypes、tkinter 和 unittest。

默认只读。CPU 只做诊断，不自动结束进程或调整调度策略。工作集与 Standby 维护一次只选一种，可用内存上升不等于加速收益。

## 文档入口

- [项目协作约定](AGENTS.md)：关联资料、规范和操作边界
- [通用调研](<docs/CPU 内存清理 通用调研.md>)：360 产品对照、技术机制、历史证据及限制
- [架构设计](<docs/CPU 内存清理 架构设计.md>)：职责、合同、状态与验证约束
- [代码结构设计](<docs/CPU 内存清理 代码结构设计.md>)：Python 文件和测试镜像分布
- [测试说明](tests/README.md)：测试计划、元数据、报告与未验证范围

## 关联的 DreamSoul 资料
本仓库：[DreamSoul-AI/cpu-memory-cleanup](https://github.com/DreamSoul-AI/cpu-memory-cleanup)。每次从当前项目关联文件夹和资料链接定位 DreamSoul 文档库，读取相关规范与已有研究，再沿用既有飞书分享链接。使用资料库的逻辑目录，不写死机器盘符或用户名。

本地资料通常从 `文档/规范/` 与 `研发/研究/` 定位。未关联时，从 [飞书规范总索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 和 [工具与教程总索引](https://dreamsoul.feishu.cn/docx/VSQ5dlJl3o0hbIxrEhUc8qKEnBc) 查找，具体任务入口见 AGENTS.md。

## 代码结构

```text
cpu-memory-cleanup/
├── pyproject.toml           src 包发现与安装元数据
├── src/
│   └── cpu_memory_cleanup/  8个文件：包标识、装配、合同、用例、规则、Windows、CLI、桌面
├── scripts/                 三个兼容入口及统一测试入口
├── tests/
│   ├── src/cpu_memory_cleanup/  产品源码镜像
│   ├── scripts/                 脚本镜像
│   └── manifest.json            唯一测试声明
└── docs/                    通用调研、架构设计、代码结构设计
```

用例统一负责观察、回看与单次维护。两个界面不直接调用原生维护，导入模块不采样、不打开界面。旧旁路实现已移除。

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

`scripts/desktop-memory-ui.cmd` 转发到 `python -m cpu_memory_cleanup --gui`，打开时只读。桌面维护需选择机制和保存目录。关闭窗口会停止接单，并等待已有工作收尾。

`scripts/desktop-memory-button.cmd` 无参数只读，显式 `release 保存目录` 才请求一次工作集维护。程序不提权、不结束业务进程、不停服务、不改页面文件或系统设置。真实维护可能产生缺页和 I/O 代价，内部 NT 路线仍需按实际 Windows 构建验证。

旧的自动 A/B、`--ab-null`、日志跟随、独立预检和 CLI 自定义阈值入口已明确停用，解析时返回错误，不调用系统。实验编排需要另行固定方案与验证条件，不自动串联维护。

## 验证与结果

```powershell
python scripts/run-tests.py --plan quick
python scripts/run-tests.py --plan regression
python scripts/run-tests.py --rebuild-report .test-results/运行ID
```

唯一声明来源为 `tests/manifest.json`。产品测试的完整模块名使用 `tests.src.cpu_memory_cleanup` 前缀；迁移前回执保留当时的路径和测试标识，当前布局的运行回执统一见 [测试说明](tests/README.md)。默认计划全部使用合成数据、假 DLL、假界面或受控测试工作进程。结果保存在 `.test-results/<run_id>/`，包含运行清单、逐项事件和可从磁盘重建的报告。未启用 GitHub Actions。

真实 Windows 采集、GUI 显示、特权路径和清理效果均未做本轮平台验证。历史桌面 A/B 没有证明持续净收益，原始 `logs/` 已删除，不重建或以新数据冒充。
