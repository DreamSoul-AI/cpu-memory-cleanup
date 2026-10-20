# CPU 内存清理 测试说明
更新日期：2026年10月21日。采用项目关联 DreamSoul 文档库中已读取的 [测试规范 代码](https://dreamsoul.feishu.cn/docx/KE4IdW6EWoKdvExSkdvcjTJFnsf)，源码职责和测试镜像见 [代码结构设计](<../docs/CPU 内存清理 代码结构设计.md>)。

## 一、范围与安全边界
产品测试使用合成样本、临时目录、假 Reader、假 Maintainer、假 DLL 或假 Tk。导入测试不采样、不加载真实产品 DLL、不创建窗口，不触发真实维护或特权调整。测试入口仅为自身创建的工作进程设置预算，在超时或收尾时回收自己的测试进程树，不接管其它 PID。

| **产品或脚本** | **镜像测试** | **重点信号** |
| --- | --- | --- |
| `src/cpu_memory_cleanup/__init__.py` | `src/cpu_memory_cleanup/test_package.py` | 无副作用导入与源码依赖方向 |
| `src/cpu_memory_cleanup/__main__.py` | `src/cpu_memory_cleanup/test_main.py` | 参数先解析、装配、GUI 按需导入 |
| `contracts.py` | `src/cpu_memory_cleanup/test_contracts.py` | 数据类型、数值边界、时区及动作合同 |
| `core.py` | `src/cpu_memory_cleanup/test_core.py` | 观察、回看与维护顺序，取消、并发、记录失败及未知状态 |
| `rules.py` | `src/cpu_memory_cleanup/test_rules.py` | CPU 差分、覆盖、压力、树、排名、对照及收益证据门槛 |
| `windows.py` | `src/cpu_memory_cleanup/test_windows.py` | 假原生调用、零调用拒绝、至多一次派发、权限恢复与资源关闭 |
| `cli.py` | `src/cpu_memory_cleanup/test_cli.py` | 参数合同、废弃入口、假接入端到端旅程 |
| `desktop.py` | `src/cpu_memory_cleanup/test_desktop.py` | 假 Tk、单工作线程、队列、忙碌、关闭与线程启动竞态 |
| 三个启动及采样脚本 | `scripts/test_desktop_memory_button.py`、`test_desktop_memory_ui.py`、`test_tree_series.py` | 仅检查转发、显式维护及有界参数，不运行脚本 |
| `scripts/run-tests.py` | `scripts/test_run_tests.py` | 选择后导入、预算、失败、参数展开、持久化、重建、恢复及依赖门控 |

表内测试路径相对于 `tests/`。产品文件名未列目录时均位于 `src/cpu_memory_cleanup/`。层级为 unit、integration、e2e 标签，不另建同名一级目录；e2e 只描述完整假接入旅程。

排除测试包标识（含 `tests/src/__init__.py`）的递归镜像；它们只有包标识作用。文档、`.gitignore`、清单、生成报告、缓存、虚拟环境与历史已删除文件不作为可执行源码复制镜像。清单自身的解析与验证由测试入口的镜像测试覆盖。最小 `pyproject.toml` 支持 src 包发现，构建元数据纳入源码指纹；wheel 另做离线构建与内容检查，不读取主机指标。

## 二、唯一清单与选择
唯一声明来源为 `tests/manifest.json`。全局 `defaults`、`file_defaults`、单项声明依次覆盖，字典按字段合并；选择和运行前展开为完整条目。每项有稳定 `test_id`、完整 `unittest_id`、文件、目标、标签、资源及预算，清单中的参数化用例另列 `subtests`，展开为独立标识与结果，父组不重复计数。

| **维度** | **取值与当前含义** |
| --- | --- |
| 层级 level | unit、integration、e2e；核心流程为 integration，CLI 假接入旅程为 e2e |
| 对象 type | location、content、physical；当前为源码关系或行为内容，未声明真实平台 physical 计划 |
| 重要性 priority | p1、p2、p3；安全与主路径优先，部分辅助统计为 p2 |
| 成本 cost_class | c1 至 c4；当前只采用有预算依据的 c1、c2 |
| 结果信号 result_type / result_detail | categorical 或 numeric，summary、metrics 或 samples；当前产品回归为有断言的 categorical |

先读取清单并选择，再加载所选方法。标签为英文小写 snake_case；同维度多个选择取并集，不同维度相交，重复 `--tag` 要同时满足。`--file`、`--method`、`--target` 和 `--test-id` 支持匹配。`--resource` 是筛选，`--allow-resource` 才是资源准入；当前两计划资源列表均为空。

```powershell
python scripts/run-tests.py --plan quick --list
python scripts/run-tests.py --plan quick
python scripts/run-tests.py --plan regression
python scripts/run-tests.py --plan quick --file test_core.py
```

筛选部分用例用于定位；计划必需项不会随筛选消失，缺少任何必需结果都不能放行。`--all` 也不抹掉原计划必需项。不要将子集通过写为整批通过。

## 三、预算与计划依赖
每个方法在独立 Python 工作进程中顺序执行。父入口先把工作进程加入匿名 Windows Job，再经标准输入允许其导入测试。墙钟预算包含进程启动、导入、执行与工作单元收尾；共享准备计入整批。收尾后发现单项越限也保留 `timeout`，已取得的断言、指标和原失败仍保留；整批实际墙钟越限时阻断放行，报告重建也不能绕过预算。

| **计划或用例** | **墙钟上限** | **内存上限** | **执行安排** |
| --- | --- | --- | --- |
| c1 单方法 | 1 秒 | 256 MiB | 假接入、合成数据及脚本静态验证 |
| c2 单方法 | 60 秒 | 2 GiB | 测试入口自检，部分自建嵌套工作进程 |
| 单独运行 quick 整批 | 60 秒 | 256 MiB | 187 个方法，参数展开后 192 项 |
| regression 整批 | 600 秒 | 2 GiB | 先执行 quick 阶段；其必需项通过后执行 48 个入口自检，合计 240 项，全阶段共用整批预算 |

内存口径是自身测试工作进程树的提交量；控制器和整机内存不在该硬上限内，也不把它称为 RSS。顺序执行时每个工作 Job 的上限取单项与批次预算的较小值，嵌套测试仍受外层 Job 限制。峰值无法取得时保留未知。该口径依据 [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) 和 [扩展限制信息](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information)。

预算为本项目的有界验证安排，预计耗时不是实测承诺；每次记录实际墙钟与自身 Job 峰值。组合 regression 不再给其中的 quick 阶段另设 60 秒总预算，单方法限制仍有效。非 Windows 无等价进程树限制时执行前阻断，仍可列出清单和重建报告。`stop_on_failure: false` 允许同阶段独立检查继续，不能越过 quick 到 regression 的依赖门槛。

## 四、结果、恢复与放行
每次运行独立保存到 `.test-results/<run_id>/`，已加入 Git 忽略，不重建历史 `logs/`。

| **产物** | **用途** |
| --- | --- |
| `manifest.json` | 运行前保存选择、完整元数据、必需项、参数、预算、环境、基线提交及源码与清单指纹；运行中更新尝试和未执行原因 |
| `events.jsonl` | 每项结果立即追加、flush 与 fsync，保留每次尝试 |
| `artifacts/*-attempt-N.json` | 单次状态、失败阶段、断言、度量及受限捕获输出 |
| `artifacts/*-progress.jsonl` | 尝试内进度，超时与中断时保留已取得的证据 |
| `report.md` | 从运行清单和事件重建的可读报告；入口同时输出 JSON 摘要 |

```powershell
python scripts/run-tests.py --rebuild-report .test-results/运行ID
python scripts/run-tests.py --resume .test-results/运行ID
```

恢复核对源码、兼容脚本与清单指纹，以及 Python 完整版本和系统类型；保留原环境元数据，但目前不以 Windows 构建、release 或架构差异拒绝恢复，跨这些环境变化时应新建运行。不覆盖旧失败或中断。重试通过单列 recovered，当前计划不允许其自动解除必需项阻断。带外部副作用的重跑另需隔离与安全声明。缺资源、超预算、前置失败和未选择都保留明确原因，未执行不能记为跳过或通过。

有效断言完成才记为质量通过；无断言空测试是执行错误。numeric 只记 measured 并保存单位、方法、样本量和明细，分类无质量映射只记 completed，均不混入质量通过率。setup、teardown 和 cleanup 失败保留原始失败；skip、xfail、xpass 保留其性质和依据，当前计划没有放宽必需项规则。清单、测试基础设施或事件写入失败也阻断放行。

## 五、迁移前回执与本轮验证
执行环境为 Windows、Python 3.13.9；基线为 `6938f10447b250958856a845fc5448aa1185b52d` 加本轮未提交变更。下列时间为北京时间2026年10月21日，运行 ID 使用 UTC。

| **运行 ID 与计划** | **结果** | **整批墙钟** | **说明** |
| --- | --- | --- | --- |
| `20261020T210157Z_7379ba2e`，quick | 186 / 186 项质量通过 | 23.65 秒 | 181 个方法，参数展开后计数；随后又补充了两项入口自检断言 |
| `20261020T210412Z_701cacea`，regression | 229 / 229 项质量通过 | 34.50 秒 | src 迁移前的源码与清单，包含 quick 阶段及 43 项入口自检；失败、错误、超时、跳过、缺失与 recovered 均为 0 |

src 迁移前 regression 的源码及展开清单指纹为 `93dcac81edcb27379e32ca445c3db7d7bd99d6dd8b139ee05ca25dcf57b5a152`。原始回执在 `.test-results/20261020T210412Z_701cacea/`；已另行使用 `--rebuild-report` 从磁盘重建，仍为 229 项通过、无阻断。产物仅保存在本地忽略目录，不会随 Git 提交上传。“按本计划放行”仅表示该离线计划满足，不能作为真实维护或发布验收。

保留失败的首轮证据：quick `20261020T210120Z_c5d17d93` 因 4 个参数化方法缺少展开声明被阻断；regression `20261020T210252Z_aa69b217` 因两项入口自检只有 mock 断言、未取得可计数的 unittest 质量断言被阻断。前者补齐清单，后者保留 mock 检查并补充退出码与阻断原因断言，未放宽判定规则。该阶段最终结果来自新的完整运行，没有把失败尝试覆盖成通过。

src 迁移后、PR 审查修复前的完整 regression 为 `20261020T210919Z_29757eba`，182 个 quick 方法和 46 个入口自检，共 228 个方法、参数展开后 233 项，全部首次通过，整批墙钟 35.48 秒。失败、错误、超时、跳过、缺失和 recovered 均为 0；源码、脚本、打包元数据与展开清单指纹为 `4602061adaddf7a6a8f5e49b71acfa4fd65fae19b2c560c70ed0a2b26237ed20`，本地回执目录为 `.test-results/20261020T210919Z_29757eba/`。报告已从磁盘重建并核对源代码指纹及清单完整性，测试运行时仍是上述基线加待提交改动。

另使用当前环境已有的 setuptools 离线构建 wheel，核对包内仅含 8 个产品 Python 文件且内容与 src 一致，再安装到独立测试目录，隔离执行 `--help` 并确认正常退出。该验证不执行主机采样、GUI 或维护，产物与回执位于 `.test-results/src-package-check/`，不作为发布包上传。

## 六、PR #2 合并前审查
按单用户本地工具的运行场景审查维护权限、互斥、至多一次调用、历史记录、分析门槛、桌面线程、入口与测试执行器。修正 A/A 提交量噪声与 A/B 统计口径不一致、跨构建或格式版本校准仍被接受的问题；只读回看不再读取同目录无关动作文件，历史 warnings 也保留到样本和分析。测试工作单元收尾超限记为 timeout，整批超预算阻断放行，原断言、指标和失败证据保留。以上分别有定向回归。

完整 regression `20261020T211632Z_879933bd` 基于 `89ecc77366fbe708819b00ec62647f776bd7b146` 加本轮审查修复，235 个方法、参数展开后 **240 / 240 项首次通过**，整批墙钟 36.02 秒。失败、错误、超时、跳过、缺失和 recovered 均为 0，`budget_exceeded=false`。源码及展开清单指纹为 `9f45d70c25a146ae9e322ac3866724432df4dfc9932452af4c172e9160f23811`；原始回执位于 `.test-results/20261020T211632Z_879933bd/`，报告已从磁盘重建，指纹及全部测试方法与清单一致。

真实 Windows 产品采集、真实 GUI、特权 ABI 与恢复、跨会话互斥、各构建兼容和清理收益均未验证。测试工作进程的 Job 预算验证不代表产品维护能力通过。未配置或运行 CI/CD，未发布产品。提交和 PR 以实际 Git 及托管平台结果为准。
