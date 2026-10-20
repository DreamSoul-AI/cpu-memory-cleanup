**CPU 内存清理 代码结构设计**
更新日期：2026年10月21日。英文名称：Code Structure Design。状态：8个产品源码文件、脚本转发和镜像测试已落位；真实平台、GUI 显示与维护收益尚未验证。设计依据为 [CPU 内存清理 架构设计](<CPU 内存清理 架构设计.md>)，技术机制与现状证据见 [CPU 内存清理 通用调研](<CPU 内存清理 通用调研.md>)。

# 一、范围与目录原则
本文记录当前代码位置、每个 Python 文件的职责、允许依赖及对应测试位置。架构设计负责质量目标、运行合同、状态与部署，本文集中维护具体目录树和文件职责。

产品使用 src 布局，单个包位于 `src/cpu_memory_cleanup/`，保留 `python -m cpu_memory_cleanup` 启动方式。根目录 `pyproject.toml` 仅配置包发现和最小安装元数据，产品运行依赖仍为标准库。四项业务职责为交互、用例、分析规则和 Windows 接入，另设包入口及共享合同文件。产品模块与测试文件使用英文小写与下划线，启动脚本沿用既有或架构指定名称。

按实际职责落位，先使用单层包。只有同一文件内出现需要独立修改、独立验证的稳定职责时才拆成子包，并同步测试镜像。文件数量不是永久上限，不按旧文件逐一改名或搬迁。

# 二、已落位文件结构
下列为本轮重构后的源码与测试骨架。产品保留单层包，旧的释放、权限、桌面及分析旁路模块和对应平铺测试已移除；运行缓存与生成产物不列入源码骨架。

```text
cpu-memory-cleanup/
├── AGENTS.md                              # 项目协作、资料定位和执行边界
├── README.md                              # 项目说明、实际功能与文档入口
├── pyproject.toml                         # src 包发现与最小安装元数据
├── src/
│   └── cpu_memory_cleanup/
│       ├── __init__.py                    # 包标识和必要元信息
│       ├── __main__.py                    # 启动、装配与入口选择
│       ├── contracts.py                   # 共享数据和读取、维护合同
│       ├── core.py                        # 观察、回看、单次维护及记录
│       ├── rules.py                       # CPU、内存解释与纯比较
│       ├── windows.py                     # Windows 采集、维护与资源管理
│       ├── cli.py                         # 命令行解析和结果呈现
│       └── desktop.py                     # Tk 界面、工作线程与结果投递
├── scripts/
│   ├── desktop-memory-ui.cmd              # 桌面启动兼容入口
│   ├── desktop-memory-button.cmd          # 既有按钮命令兼容入口
│   ├── tree-series.ps1                    # 既有采样脚本兼容入口
│   └── run-tests.py                       # 测试选择、执行与结果保存
├── tests/
│   ├── __init__.py                        # 固定 tests 包命名空间
│   ├── README.md                          # 执行说明、映射和排除项
│   ├── manifest.json                      # 唯一测试元数据与计划入口
│   ├── src/
│   │   ├── __init__.py                    # 测试镜像命名空间
│   │   └── cpu_memory_cleanup/
│   │       ├── __init__.py                # 测试镜像包标识
│   │       ├── test_package.py            # 产品包导入边界
│   │       ├── test_main.py               # 启动与装配
│   │       ├── test_contracts.py          # 共享数据与合同
│   │       ├── test_core.py               # 用例、状态、记录及失败路径
│   │       ├── test_rules.py              # CPU、内存与比较规则
│   │       ├── test_windows.py            # 假原生调用及合同验证
│   │       ├── test_cli.py                # 参数、输出及命令行旅程
│   │       └── test_desktop.py            # 界面交互及桌面旅程
│   └── scripts/
│       ├── __init__.py                    # 脚本测试镜像包标识
│       ├── test_desktop_memory_ui.py
│       ├── test_desktop_memory_button.py
│       ├── test_tree_series.py
│       └── test_run_tests.py
└── docs/
    ├── CPU 内存清理 通用调研.md
    ├── CPU 内存清理 架构设计.md
    └── CPU 内存清理 代码结构设计.md
```

三个启动与采样脚本从自身位置定位仓库，将 src 加入 PYTHONPATH 并保留已有值，再转发到 Python 用例；参数和退出码由脚本交接，业务行为由包统一管理。测试入口是研发工具，不属于产品运行依赖。兼容与弃用边界如下：

| **入口** | **当前行为** | **兼容限制** |
| --- | --- | --- |
| desktop-memory-ui.cmd | 转发到 --gui，打开时只读 | 维护需选择单一机制及记录目录 |
| desktop-memory-button.cmd | 无参数只读；release 保存目录请求一次工作集维护 | 维护不再省略记录目录，不接受多余参数 |
| tree-series.ps1 | IntervalSeconds、Count、JsonlPath、TopOwners 转发到有界采样 | Count 上限1000；写入新样本格式，不再实时采集旧 owner、Codex 树及年龄桶 |
| 旧 CLI 与历史文件 | --analyze、--pressure、--tree-summary 作为离线回看别名 | both、自动 A/B、--ab-null、日志跟随、独立预检及 CLI 自定义阈值已显式停用；历史文件缺元数据时不宣称完整 |

完整命令契约见 [README](../README.md)，统一测试入口、选择方式和实际回执见 [测试说明](../tests/README.md)。

# 三、Python 文件职责
## （一）包入口与装配
| **文件** | **主要内容** | **边界** |
| --- | --- | --- |
| `__init__.py` | 包说明及实际需要的版本元信息 | 导入时不启动流程，不聚合导入 GUI、Windows 接入或全部公共函数 |
| `__main__.py` | 受启动保护的 `main(argv=None)`，选择 CLI 或桌面入口，创建并连接用例与接入对象 | 装配代码不处理指标、执行维护或判断最终结果，不在模块导入时启动程序 |

装配创建的用例对象在一次 CLI 运行或一个桌面实例中共享。Windows 接入对象的创建不打开查询、互斥或权限作用域，原生资源在用例明确打开会话后取得。离线回看不打开 Windows 会话。Tk 仅在选择桌面入口时加载。
## （二）共享合同 contracts.py
| **内容** | **放在本文件中的定义** |
| --- | --- |
| `Sample` | 样本身份、时刻、原始指标、单位、CPU 覆盖及缺失原因 |
| `ActionRequest` | 用户确认的单一维护机制、实际范围和确认来源 |
| `RunReport` | 观察、解释、调用事实、恢复与记录状态、整体完成度 |
| `Reader` / `Maintainer` | 读取和单次维护需要的最小接口及资源责任，行为语义沿用架构设计 |
| `validate_action` | 实时及历史共用的动作事实校验，检查 attempted、调用结果、状态码及特权恢复字段的一致性 |

使用小型数据类或明确字段定义。与持久化版本有关的共享常量放在这里，序列化、文件读取和规则执行分别留在其使用方。模块不导入其它产品模块，不包含 Windows 句柄、DLL 类型、UI 控件或业务编排。
## （三）用例 core.py
Application 提供 `inspect`、`review`、`maintain` 三个业务入口，并通过 `request_cancel` 与 `shutdown` 管理取消和关闭生命周期；同一个用例实例持有运行上下文。实现内部的辅助函数使用私有名称，调用方不直接调用记录函数或阶段转换。

| **文件内职责** | **具体内容** |
| --- | --- |
| 观察 `inspect` | 打开 Reader 会话、有界采样、调用规则、按请求记录、关闭资源并返回报告 |
| 回看 `review` | 校验并读取已保存运行，保留原条件，执行明确要求的比较或新版重算 |
| 维护 `maintain` | 检查确认和范围、协调会话、保存意图、至多一次派发、必要后观测与收尾 |
| 运行状态 | 运行 ID、活动流程、派发前取消与整体完成度；shutdown 后停止接单，已有工作继续必要收尾 |
| 记录读写 | 运行头、样本及动作事实先刷新；关闭记录流后，以已关闭临时文件替换提交终态，保存失败不抹去已知调用 |

记录读写保留为本文件的私有实现。程序/规则版本、源码构建指纹、环境、时间、间隔及样本归属进入记录检查；元数据或新版时刻缺失则标为不完整。维护运行缺失或空白动作文件保留 unknown，历史动作不重放。关闭未确认的资源保留引用，并阻断新的系统流程，没有自动解除此阻断的入口。

诊断使用标准库 logging，运行目录由调用参数提供，不新增独立存储框架。用例不创建窗口、不加载 DLL，也不通过全局可变对象保存当前运行。终态文件替换与原生动作不是同一事务，不承诺崩溃后恰好一次或自动补做维护。
## （四）规则 rules.py
| **函数分组** | **处理内容** |
| --- | --- |
| CPU 分析 | 稳定进程身份、累计时间差、间隔、单核或全机口径、处理器组与覆盖缺口 |
| 内存分析 | 提交、工作集、Standby、可用量、缺页与 I/O 的解释及缺失处理 |
| 历史进程树与窗口汇总 | 对旧文件已提供的聚合数据进行离线汇总，保留缺字段、重复、缺拍与范围不一致 |
| `analyze` / `compare` | 产生解释和对照结论，分开数值变化、证据完整度及收益判断 |

各组使用同一文件内的普通函数。输入为已校验的数据和显式条件，输出为分析结果，不读取文件、查询系统、读取真实时钟或自动生成维护确认。CPU 差分使用全部可读进程输入，top 只截断工作集展示排名；范围未知、多组覆盖不全、首拍和身份变化保留缺口。CPU 与内存规则通过同一用例入口使用。
## （五）Windows 接入 windows.py
| **文件内分组** | **处理内容** |
| --- | --- |
| 原生定义 | `ctypes` 结构、ABI、状态码和 DLL 绑定，在打开相关会话时取得实际能力 |
| 只读接入 | 主机、进程、CPU 与内存数据读取，PDH 查询及缺失原因，统一转换成合同数据 |
| 单项维护 | 单机制范围检查、互斥、只读预检、临时特权、至多一次原生调用及恢复 |
| 资源收尾 | 查询、句柄和互斥释放，分别返回调用、恢复和关闭事实 |

WindowsBackend 实现两个窄合同，导入和构造不取得原生资源。只读会话采集内存、PDH、CPU 累计时间与处理器组覆盖；with_processes 启用时保留全部可读进程的 PID、精确 FILETIME 创建身份、CPU 累计时间和内存量，不在接入层按 top 截断。父链没有可靠输入时保留未知，不猜测归属。

单项操作会话使用进程内非递归锁与当前 Windows 会话内的命名互斥；预检和动作令牌均由会话持有。启用失败零原生维护调用，调用异常保留未知，恢复或关闭未确认时保留资源并阻止后续维护。原生句柄不传给核心或界面，缺权限、不支持和未知分别报告，不自动提升权限或改换机制。
## （六）命令行 cli.py 与桌面 desktop.py
| **文件** | **主要内容** | **状态归属** |
| --- | --- | --- |
| `cli.py` | 参数解析、输入转换、调用用例、中文或 JSON 呈现、退出码映射 | 同步使用共享用例，最终状态和动作事实来自 `RunReport` |
| `desktop.py` | Tk 控件、明确选择、工作线程创建、结果投递、主线程 `after` 更新与窗口关闭 | 拥有控件和展示缓存，业务活动状态、取消是否生效及是否可接单由核心决定 |

GUI 使用单个非守护工作线程执行用例，通过队列将结果交回主线程，不从工作线程更新 Tk 控件。关闭窗口调用 Application.shutdown()，撤销界面待派发请求，停止接单并等待当前工作收尾后再销毁窗口。取消是否生效及安全上下文是否可接单由核心判定，线程结束或按钮变灰不能代替业务终态。两个入口不导入具体 Windows 接入，不重新计算成功或加速结论。

# 四、文件间依赖约定
架构设计中的源码依赖图为关系依据，具体允许方向如下。源码依赖包含导入和类型引用，不表示运行时只能沿图单向返回数据。

| **文件** | **允许依赖的项目文件** | **限制** |
| --- | --- | --- |
| `__init__.py` | 原则上无 | 只保留必要包元信息 |
| `__main__.py` | `cli`、按需 `desktop`、`core`、`windows` | 只负责装配和入口选择 |
| `contracts.py` | 无 | 只依赖必要标准库 |
| `rules.py` | `contracts` | 无真实系统或文件副作用 |
| `core.py` | `contracts`、`rules` | 通过注入合同调用接入，不导入 `windows` 或 UI |
| `windows.py` | `contracts` | 不反向依赖核心、规则或 UI |
| `cli.py` / `desktop.py` | `core`，需要类型引用时使用 `contracts` | 不互相调用，不加载具体接入 |

跨文件复用只引用其公开入口。仅一个文件使用的小函数保留为该文件的私有函数。共享合同不引入流程，辅助代码不集中进没有明确责任的 `utils.py`。当前业务也不需要单独的 `worker.py`、服务容器或插件目录。

# 五、测试文件如何分布
## （一）源码镜像与测试内容
已建立产品包及脚本的镜像测试，`unit`、`integration`、`e2e` 作为标签。每个文件内的测试仍按单一信号划分，测试方法和元数据共同说明实际层级与路径。

| **目标代码** | **测试位置，位于 tests/ 下** | **主要验证内容** |
| --- | --- | --- |
| `src/cpu_memory_cleanup/__init__.py` | `src/cpu_memory_cleanup/test_package.py` | 导入不启动 GUI、采样或维护，必要元信息可读取 |
| `src/cpu_memory_cleanup/__main__.py` | `src/cpu_memory_cleanup/test_main.py` | 默认入口、装配与退出，选择离线模式不打开系统会话 |
| `src/cpu_memory_cleanup/contracts.py` | `src/cpu_memory_cleanup/test_contracts.py` | 数据边界、缺失语义、版本和最小合同 |
| `src/cpu_memory_cleanup/core.py` | `src/cpu_memory_cleanup/test_core.py` | 只读、维护、回看路径，取消、互斥、权限及保存失败，未知不重放 |
| `src/cpu_memory_cleanup/rules.py` | `src/cpu_memory_cleanup/test_rules.py` | CPU 与内存口径、合成窗口、进程树及收益证据 |
| `src/cpu_memory_cleanup/windows.py` | `src/cpu_memory_cleanup/test_windows.py` | 假 DLL 和合同，ABI 映射、原始状态、特权恢复和句柄关闭 |
| `src/cpu_memory_cleanup/cli.py` | `src/cpu_memory_cleanup/test_cli.py` | 参数到结果，默认零动作、中文路径、输出及退出码 |
| `src/cpu_memory_cleanup/desktop.py` | `src/cpu_memory_cleanup/test_desktop.py` | 主线程投递、重复点击、关闭取消及控件销毁后的结果处理 |
| `scripts/desktop-memory-ui.cmd` | `scripts/test_desktop_memory_ui.py` | 桌面启动目标、参数及退出码转发 |
| `scripts/desktop-memory-button.cmd` | `scripts/test_desktop_memory_button.py` | 无参数零维护、显式机制与目录转发、无效参数拒绝 |
| `scripts/tree-series.ps1` | `scripts/test_tree_series.py` | 采样参数转发，业务执行经过用例 |
| `scripts/run-tests.py` | `scripts/test_run_tests.py` | 清单选择、导入范围、逐项结果保存、失败和缺结果的退出行为 |

跨模块路径测试放在其入口对应的测试文件；单独维护场景时仍保留镜像位置。测试包标识文件不为自身生成递归测试。映射、排除项、执行条件与实际回执统一见 [测试说明](../tests/README.md)。
## （二）测试导入与执行入口
保留 `unittest`。统一入口以仓库根作为顶层导入目录，测试模块使用 `tests.src.cpu_memory_cleanup.test_core` 等完整名称；tests/、tests/src/ 与镜像包均保留 __init__.py。产品导入根为仓库 src/，测试的顶层导入目录仍为仓库根，不能把 tests/src/ 作为产品包搜索路径，否则镜像目录可能遮蔽真实 cpu_memory_cleanup。

`scripts/run-tests.py` 从自身位置定位仓库根与 src/，读取唯一 `tests/manifest.json`，先完成选择及条件检查，再按完整名称加载所选用例。需要目录发现时，以仓库根作为 `top_level_dir`，不先全量导入再筛选。测试模块导入时也不得创建 GUI、采样或维护，平台能力在所选用例执行时按需取得。当前 quick 与 regression 计划使用假接入、假界面、合成文件及受控测试工作进程，不执行产品真实采集或维护。逐项结果保存为 manifest、events 与可重建报告，硬限制只施加于测试入口创建的工作进程。五维、预算和放行条件见架构设计第六章及 [测试说明](../tests/README.md)，具体通过数与回执不在本文重复维护。

假 Reader、Maintainer 和合成数据保留在实际使用它们的测试文件。后续出现真实复用时再提取到 tests/_helpers/ 或 tests/_data/，与产品镜像分开。测试辅助函数不以 `test_` 命名。真实 GUI、ABI 和平台实验仍需按明确计划验证，假路径通过不代表真实平台已通过。

# 六、源码、脚本与运行产物的界限
| **位置或文件** | **归属** | **规则** |
| --- | --- | --- |
| `src/cpu_memory_cleanup/` | 产品源码 | 包含运行逻辑和明确的模块边界 |
| `pyproject.toml` | 包发现与安装配置 | 发现 src 下的产品包，不把 tests 或 scripts 打进产品包 |
| `scripts/` | 启动兼容与研发入口 | 业务转发到包，测试入口只承担测试职责 |
| `tests/` | 测试代码、最小数据和测试计划 | 不进入产品运行依赖 |
| `docs/` | 三类维护文档 | 调研保存证据，架构保存行为约束，本文保存文件落位 |
| `samples.jsonl`、`operation.jsonl` | 实际运行目录中的业务数据 | 输出位置显式给定，不写入源码目录，不重建历史 `logs/` |
| 测试运行的 `manifest.json`、`events.jsonl`、`report.md` | 明确指定的独立测试结果目录 | 与 `tests/manifest.json` 的计划定义区分，不冒充源码或历史样本 |

产品运行依赖保持标准库，新增 pyproject.toml 用于标准 src 包发现和可编辑安装。开发环境先按 README 执行 `python -m pip install --no-build-isolation --no-deps -e .`，再运行模块；构建工具属于安装环境，不是新增产品运行依赖。三个兼容脚本及统一测试入口显式定位源码，不要求从仓库根隐式导入产品包。安装包分发、缓存、虚拟环境和生成产物不纳入此次源码镜像。

# 七、当前落位与后续维护
产品8个源码文件已迁入 src/cpu_memory_cleanup/，测试镜像位于 tests/src/cpu_memory_cleanup/；三个脚本转发入口及统一测试入口保留在根目录 scripts/。旧旁路实现已移除。现有依赖和关键失败路径有离线测试，实际执行范围与回执见 [测试说明](../tests/README.md)。迁移前回执保留旧路径和测试标识，当前 src 布局需由相应新回执确认。真实 Windows 采集、Tk 呈现、特权恢复及维护效果未进行本轮平台验证，不能由目录完整或假调用通过推定已经发布可用。

后续修改同时更新受影响代码、合同、源码与测试镜像、测试清单和文档。新增文件或子包需要实际职责和验证依据；改变命令、脚本或记录格式时明确兼容与弃用范围，保留单机制维护及未知不重放的边界。

本文的格式与目录要求采用关联 DreamSoul 文档库中已读取的《飞书文档规范》《代码规范》及《测试规范 代码》目录与映射章节。规范入口见 [项目协作约定](../AGENTS.md)，架构方法及原始资料阅读范围见架构设计第八章。当前结构已实施；测试结果以对应运行回执为准，平台与收益仍待明确计划验证。
