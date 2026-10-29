# CPU 内存清理
360加速球式的轻量内存清理桌面工具。紧凑主窗口以环形仪表显示内存状态，绿色按钮执行一次工作集整理，结果区呈现本次可用内存变化。Python 3.10+，使用标准库 ctypes、tkinter 和 unittest。

打开只读，点击才清理。桌面固定工作集方式，CPU 与历史分析命令属于辅助工具。可用内存上升不等于加速收益。

## 项目约定与执行边界

开始研发或执行命令前，结合 [AGENTS.md](AGENTS.md) 读取本节。AGENTS.md 维护通用协作、资料定位和迭代流程，本节与专项文档维护 CPU 内存清理的具体约定。入口通用化不放宽以下限制。

### 实现与设计约定

- 当前交付形态为独立内存小窗口，以 360 加速球式快捷操作为参考。CPU 看板、排名和诊断前置不是主功能，未约定宿主协议前不声称已有浏览器或编辑器插件集成
- 源码位于 `src/cpu_memory_cleanup/`，产品运行依赖保持 Python 标准库。`interaction/`、`application/`、`system/` 对应用户交互、功能和系统，具体平台接入位于 `system/platforms/`
- 架构使用用户交互、功能、系统三层的结构与流程视图，统一表述操作系统能力。平台名称用于实现与验证现状，不能推定已具备多平台支持。AR-01 至 AR-07 在架构和代码结构设计中关联职责、实现与验证
- 代码结构设计的硬约束集中于 `src/cpu_memory_cleanup/` 的模块职责、文件、入口、状态和依赖。外围目录由本文导航，属于可调整的软约定，变化时同步相关装配、脚本、配置、测试与文档
- 测试镜像位于 `tests/src/cpu_memory_cleanup/`，脚本及其测试位于 `scripts/` 和 `tests/scripts/`，共享装配在 `tests/fixtures.py`，包发现由 `pyproject.toml` 配置。`tests/` 不设 README，镜像例外登记在代码结构设计中
- 测试规则直接采用关联 DreamSoul 资料库中的飞书共用测试规范，不另建副本。根 `tests/manifest.json` 维护全局计划与预算，局部清单提供当前目录及子树元数据，执行器静态发现并展开 Python 方法。独立网页演示测试另行登记，不冒充 Python 计划覆盖
- 架构任务先复用资料库中 `研发/研究/软件开发 Software Engineering/软件架构设计 Software Architecture Design/` 的通用调研、相关案例和来源范围。涉及 Codex 工具生命周期时，先读同一软件开发课题下《Codex 闲置工具进程安全回收调研》，保留其版本与阅读范围

### 运行与验证限制

- 不结束进程、不停服务、不修改系统设置、页面文件或注册表，不自动提权。Windows 内存维护与工具生命周期回收分别分析，聊天闲置、低 CPU、Node 名称和进程年龄不能证明可以关闭
- 真实释放、GUI、实时巡检、持续采样和后台任务必须有本次明确执行指令。当前先不运行真实采样和清理的要求继续有效，文档命令、历史实验和未来计划不构成执行授权
- 释放与权限调整测试注入假调用，禁止测试触发真实释放。当前 quick、regression 和 readonly_offline 只执行离线与假接入验证，不代表真实平台或清理效果验收
- 读取失败保留未知和错误，不补零。调用结果未知不自动重试，后置失败保留已发生事实。目录存在、离线通过和源码分析均不能代替行为验证
- 已删除的历史 `logs/` 不自动重建。新验证使用当前计划的独立输出，旧回执保留原路径、版本和标识，不改写成新布局通过

## 网页演示
打开 [在线交互演示](https://dreamsoul-ai.github.io/cpu-memory-cleanup/)。源码位于 [demo/index.html](<demo/index.html>)，用合成数据展示绿色按钮、工作集整理和再次使用后的占用回升。它不读取访客设备，也不执行真实清理。Agent 的周期监测与分级干预仍是候选计划，见开发计划的候选研究方向。

本地可运行 `python -m http.server 8765 --bind 127.0.0.1 --directory demo`，再访问 `http://127.0.0.1:8765/`。演示逻辑测试单独执行 `node --test tests/demo/app.test.cjs`，使用 Node 内置测试能力，不是桌面 Python 测试计划的一部分。产品运行依赖仍为 Python 标准库。

## Package 与工具入口
当前已实现“Package → CLI/Tool → 前端接入”的源码：`cpu_memory_cleanup.api` 提供公共创建入口，包内提供一次内存状态、一次明确工作集整理和离线回看。CLI 与现有桌面使用同一套能力，当前改动已通过完整离线与隔离安装验收，证据见 [包与工具接入运行记录](<docs/运行记录 Run Record/2026-10-30 包与工具接入.md>)。后续安排见 [当前开发计划](<docs/开发计划 Development Plan CPU 内存清理.md#二下一步开发任务>)。MCP 和持续监控仍是后续选项，当前没有网络服务或后台 Agent。

安装后可直接使用包，创建实例本身不采样、不维护、不打开界面。下面示例仅离线回看已有记录，`保存的运行目录` 替换为实际位置：

```python
from cpu_memory_cleanup.api import API_VERSION, create_application

app = create_application(offline=True)
try:
    report = app.memory_result("保存的运行目录")
    print(API_VERSION, report.completion, report.action)
finally:
    app.shutdown()
```

实时实例使用 `create_application()`。`app.memory_status()` 返回一次内存专用 `RunReport`，原始容量位于 `samples[].metrics.physical.total_bytes / available_bytes` 和 `commit.used_bytes / limit_bytes`，时间带时区，平台能力在 `metadata.capabilities`。已用量按总量减可用量计算，占用百分比按已用量除总量计算，包不添加重复派生字段。`app.memory_cleanup(记录基目录, confirmed=True, confirmation_source="可信宿主的授权来源")` 固定执行一次工作集整理，并返回前后原始快照、动作事实和 `output_dir`。调用这两个方法会读取当前系统，后者还会尝试维护，必须遵守本次执行授权。包只返回对象，JSON、终端退出码和控件呈现由外层负责。

跨进程工具使用下列接口。`--tool-info` 只输出静态目录，可在未连接实时能力时读取，目录包含参数 schema、命令模板与维护确认要求。

```powershell
python -m cpu_memory_cleanup --tool-info
python -m cpu_memory_cleanup --tool memory_status
python -m cpu_memory_cleanup --tool memory_cleanup --confirm --output-dir 记录基目录
python -m cpu_memory_cleanup --tool memory_result --run-dir 保存的运行目录
```

三个 `--tool` 调用默认输出 JSON，在 `RunReport` 字段上增加 `tool_schema_version: 1` 和 `tool`，整体 `completion == "success"` 时退出码为 0，其余为 1，参数错误为 2。工具 JSON 使用 ASCII 转义，解析后的中文值不变，便于 Windows 重定向读取。`--confirm` 表达当前请求的确认，真实授权由可信宿主核对，模型填写布尔值不自动取得权限。状态与清理命令不是本轮执行指令。回看使用离线装配，顶层 `run_id` 标识本次回看请求，原清理身份保留在 `metadata.header.run_id` 和原样本中，不再执行动作。旧 CLI 参数与普通 `--json` 表示保持兼容。

## 文档入口

当前独立文档统一将类型放在前、项目名放在后。每类维护一个现行入口，历史执行按日期分目录归档。

| **文件或入口** | **维护内容** |
| --- | --- |
| [AGENTS.md](<AGENTS.md>) | 通用协作规则、资料定位、文档分工和自动化开发流程 |
| [通用调研 General Research CPU 内存清理.md](<docs/通用调研 General Research CPU 内存清理.md>) | 360 产品对照、内存机制、方案、来源和证据限制；保留支撑结论所需的证据摘要，执行与迁移详细经过归日期记录 |
| [架构设计 Architecture Design CPU 内存清理.md](<docs/架构设计 Architecture Design CPU 内存清理.md>) | 三层职责、能力、结构与流程、部署和质量目标，不写 Python 文件或具体类型 |
| [代码结构设计 Code Structure Design CPU 内存清理.md](<docs/代码结构设计 Code Structure Design CPU 内存清理.md>) | 架构到源码、模块、文件、公开入口、状态、依赖和测试的映射，区分当前实现与目标 |
| [开发计划 Development Plan CPU 内存清理.md](<docs/开发计划 Development Plan CPU 内存清理.md>) | 后续目标、优先级、依赖和验收条件，探索与已决定任务分开，不产生运行授权 |
| [缺陷记录 Bug Report CPU 内存清理.md](<docs/缺陷记录 Bug Report CPU 内存清理.md>) | 活动缺陷待办，验证关闭后将经过和证据归档到日期记录 |
| [开发记录 Development Record/](<docs/开发记录 Development Record/README.md>) | 按日期记录实现、职责和工程组织的演进，不重复完整执行回执 |
| [运行记录 Run Record/](<docs/运行记录 Run Record/README.md>) | 按日期记录实际命令、环境、版本、结果和限制，不冒充当前验证 |
| [通用测试规范 Test Specification](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf) | 关联 DreamSoul 资料库中的共用正文，不在本仓库复制一份规范 |

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
当前主窗口围绕“看内存—点击清理—看本次变化”组织：环形仪表与大号占用百分比、已用量与总量、绿色“一键清理”，以及独立的本次结果区。打开后只读刷新，点击才清理。提交量、更新时间、技术说明、刷新和取消操作收进可展开详情。产品范围见 [开发计划](<docs/开发记录 Development Record/2026-10-30 阶段完成与计划整理.md#七内存清理插件的产品基线>)。

## 安装与启动
产品包位于 `src/`，本地开发先在仓库根目录安装可编辑包，再使用 `python -m cpu_memory_cleanup`。使用当前 Python 环境已有的构建工具，安装命令不拉取运行依赖；产品运行依赖仍为标准库。

```powershell
python -m pip install --no-build-isolation --no-deps -e .
```

桌面启动入口从自身位置定位源码。`.pyw` 使用窗口版 Python，直接启动不创建控制台；CMD 兼容入口改用 `start` 启动 `pythonw` 后退出，不再保留背后的终端。直接双击 `.pyw` 需要系统已有对应文件关联，CMD 直接把文件交给 `pythonw`，不依赖该关联，启动可能有短暂闪窗。统一测试入口自行定位源码与测试目录，命令仍为 `python scripts/run-tests.py`。

## 辅助只读与离线命令
下面是安装后的命令说明。安装包与离线验证的执行结果见运行记录，示例本身不表示已经执行。`--help`、`--tool-info` 和离线回看不读取当前系统，状态读取及采样命令会读取当前 Windows 指标。

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

环形仪表、百分比和内存数值在真实读数之间以约400毫秒缓动过渡，不将动画值写入动作记录。按钮忙碌状态与动作结果连续呈现；本次可用内存增加、减少或不变由清理前后快照计算，结果保留在独立区域，实时刷新不覆盖它。数值变化不是永久释放量或提速证明。提交量前后值和技术信息放在详情中。桌面前后快照只取内存，不等待 PDH 或枚举进程，命令行完整采集保持原样。动作失败、结果未知或恢复未确认时仍保留原因。默认记录位置为当前用户主目录下的 `.cpu-memory-cleanup/runs/`，也可通过已有 `--output-dir` 指定。目录和记录在用户发起清理后由核心准备，无法保存则阻止动作。忙时不排队，不自动重试。真实采样与清理仍未在本轮执行。

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

日常完整离线检查执行 `python scripts/run-tests.py --plan regression`，其中已包含 quick 阶段。`tests/fixtures.py` 只复用合成运行信息与应用装配；根 `tests/manifest.json` 保留全局预算、计划和禁用实机验收草案，局部清单就近提供默认值与方法差异；普通方法自动发现，执行器统一展开，计划所选项全部为必需项。产品测试的完整模块名使用 `tests.src.cpu_memory_cleanup` 前缀；迁移前回执保留当时的路径和测试标识，当前布局的运行回执统一见 [运行记录 Run Record](<docs/运行记录 Run Record/README.md>)。默认计划全部使用合成数据、假 DLL、假界面或受控测试工作进程。结果保存在 `.test-results/<run_id>/`，包含运行清单、逐项事件和可从磁盘重建的报告。`--list` 只显示最终名单及 `config_sources` 来源链，不启动测试；每次运行的回执也保存来源链。未启用 GitHub Actions。

CFG-01 已修复，根入口恢复分层加载，并阻止旧根格式静默忽略子清单，验证见 [本轮运行记录 Run Record](<docs/运行记录 Run Record/2026-10-26 桌面诊断与配置修复.md>)。

已用实际 Tk 与合成数据核对紧凑窗口、绿色按钮、结果摘要和详情展开，见 [本轮界面与验证记录](<docs/运行记录 Run Record/2026-10-29 紧凑内存窗口.md>)。真实 Windows 采集、特权路径、清理效果及未检查的显示配置仍未验证。历史桌面 A/B 没有证明持续净收益，原始 `logs/` 已删除，不重建或以新数据冒充。

只读诊断验收安排见开发计划中的“只读诊断验收计划”，实际执行配置以 `tests/manifest.json` 为准。9 项实机检查、环境矩阵和资源预算已登记在根清单的 acceptance_plans 下，当前禁用，不会被 quick、regression 或 --all 自动执行。readonly_offline 只运行假接入准备检查，不代表实机验收通过。

构建 wheel 使用干净检出或隔离的源码目录，并核对产物与当前源码一致，避免旧构建缓存带入已删除的平铺模块。本轮仅验证本地安装包及离线启动，不作为发布验收。

## 入口维护与研究范围

用途、当前能力、技术栈、目录、文档入口、命令或执行边界变化时，同步核对本 README 与源码、配置和实际文档。七类开发文档当前入口见上表，历史命名与日期记录保持可追溯。持续资源管理 Agent 方案仍为候选，尚未形成独立文献调研与研究计划，与当前一次性包/工具调用分开，不以工程测试或网页演示代替研究证据。

文档职责按 AGENTS.md 维护：通用调研维护机制、方案、来源与当前判断，正文保留支撑结论所需的证据摘要、条件与限制，并引用相关记录；执行和迁移的详细经过归日期记录。架构保留当前关键设计取舍及理由，代码结构设计说明划分依据、必要约束及实现差距，计划维护后续工作与判据。按任务范围核对受影响内容，本次入口适配不代表专项正文已全面重审。

## 许可证与版本分发
本项目采用 [MIT License](LICENSE)。第三方代码如有引入，仍须分别保留其许可与来源要求；通用调研引用的外部源码未作为产品代码复制进本项目。

GitHub Release 是当前分发渠道，暂不上传 PyPI。0.2.0 作为预览版本分发，真实平台采样、清理效果和特定 Agent 宿主尚未完成验收，离线通过不等于性能保证。版本入口为 [v0.2.0](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/tag/v0.2.0)，创建完成前以本次发布记录为准。

从 Release 下载 wheel 后，在选定的 Python 3.10+ 环境安装。以下命令仅安装包和读取静态工具目录，不采样或清理：

```powershell
python -m pip install --no-index --no-deps ./cpu_memory_cleanup-0.2.0-py3-none-any.whl
python -m cpu_memory_cleanup --tool-info
```

安装后的桌面入口为 `python -m cpu_memory_cleanup --gui`，会启动窗口并实时只读刷新，须在允许读取当前设备时执行。源码仓库的 `.pyw` 与 CMD 启动脚本不包含在 wheel 中。源码分发包用于构建该 Python 包，完整设计、测试与脚本从 GitHub 对应 tag 查看。当前没有独立 EXE 产物，不会安装 Windows 服务或自动提权。
