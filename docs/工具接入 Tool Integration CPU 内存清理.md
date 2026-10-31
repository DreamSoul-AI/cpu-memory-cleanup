# 一、接入范围与固定版本
面向本地Python程序和能执行本地子进程的Agent宿主。产品能力由本仓库Package实现，Knowledge只提供说明入口，不含另一份清理脚本。此前单进程接入指南的固定功能提交为 `9c7d725c453ba6d5a3defe9e4ecf22a652605ef3`，含五个工具与单进程整理，已通过 [PR #11](https://github.com/DreamSoul-AI/cpu-memory-cleanup/pull/11) 合并到master，尚未发布新版本。已发布v0.2.0只有原三个工具，不能用该Release冒充本轮定向能力。Knowledge PR #21已合并；其链接固定到合并前的指南快照，安装与调用合同不变，当前集成状态以本说明及运行记录为准。

本地本轮候选版本0.3.0另含第六个工具memory_batch_cleanup和正式localhost网页，尚未push或发布。下述Git安装命令仍固定此前五工具版本，不包含本地新增能力；本轮只验证忽略目录中的wheel隔离安装，证据见 [本轮运行记录](<运行记录 Run Record/2026-10-31 正式网页与范围规则.md>)。

# 二、安装与静态发现
使用调用方选定的Python 3.10+环境，产品无第三方运行依赖。固定提交安装需已有Git与满足pyproject要求的setuptools，不自动升级依赖或改用户环境。安装将访问GitHub并写入选定环境，命令仅供准备环境时使用，本轮未执行网络安装：

```powershell
python -m pip install --no-deps --no-build-isolation "git+https://github.com/DreamSoul-AI/cpu-memory-cleanup.git@9c7d725c453ba6d5a3defe9e4ecf22a652605ef3"
python -m cpu_memory_cleanup --tool-info
```

静态目录不装配系统能力，不读取指标或整理内存。可先只运行第二条核对安装版本的实际工具数，不能靠包名或版本号判断功能提交。本轮验证用隔离目录的wheel，见运行记录。

# 三、宿主调用合同
| **工具** | **作用与参数** | **执行边界** |
| --- | --- | --- |
| memory_status | 单次内存读取，无必填参数 | 真实采样，需本次允许读取设备 |
| memory_cleanup | `--confirm --output-dir 记录基目录` | 全局工作集动作，宿主落实真实授权 |
| memory_result | `--run-dir 原运行目录` | 离线回看，不重新采样或整理 |
| memory_targets | 按需列出可读进程和原始创建身份 | 真实进程枚举，不自动整理；部分覆盖仍可有返回数据 |
| memory_process_cleanup | `--pid … --created-at … --confirm --output-dir …` | 一次指定进程动作，创建身份须来自列表，不能仅凭名称或PID猜测 |
| memory_batch_cleanup（本地0.3.0候选） | `--request-file 请求.json --confirm --output-dir …` | 固定身份列表与可选保护，串行逐项执行；取消不撤销已派发项 |

命令行形式均为 `python -m cpu_memory_cleanup --tool 工具名 参数`，默认JSON。`tool_schema_version=1`、`tool`与RunReport字段共同解释结果。退出码0表示整体success，1表示非完整成功，2表示参数错误；退出1时仍需读取JSON中的样本、动作与错误，不能把动作已发生解释为未执行。进程列表部分覆盖可能有可用目标，同时仍是partial。

宿主必须使用参数数组、指定Python解释器和shell=False，模型输入不拼接成shell命令。以下例子只读取静态目录，可作为无采样的接入检查：

```python
import json
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "cpu_memory_cleanup", "--tool-info"],
    capture_output=True, check=True, timeout=10,
)
catalog = json.loads(result.stdout)
assert catalog["tool_schema_version"] == 1
assert {"memory_targets", "memory_process_cleanup"} <= {
    tool["name"] for tool in catalog["tools"]
}
```

同进程调用从 `cpu_memory_cleanup.api` 导入 `create_application` 和 `ProcessTarget`，使用同名方法，返回对象而非JSON。离线回看用 `create_application(offline=True)`，结束时shutdown。不得在模块导入、工具注册或静态发现时自动调用读取或维护。

# 四、授权、目标与结果
宿主首先区分只读许可、全局维护许可与指定进程许可，再决定开放哪些调用。模型传入true或--confirm不能自证授权。已有用户“不要真实采样和清理”的要求会阻止memory_status、memory_targets及全部维护入口，仍可读取工具目录、离线回看和运行受控假接口测试。不得因为安装了工具就周期调用它。

只使用本次列表中的PID和created_at。目标退出、身份不符、不可访问或状态未知时保留失败，不更换同名进程、不转全局整理、不自动重放维护。定向操作仅作用于选中的一个进程，不代表整个多进程应用，系统可用量与目标工作集是不同指标。

执行结果返回output_dir。回看顶层run_id是新回看请求，metadata.header.run_id、target与原样本保留原动作身份。schema1旧system记录和schema2process记录语义分开，旧读者拒绝新范围不能伪造兼容通过。工作集差额不代表永久释放或速度改善。

# 五、验收与当前状态
CLI适配已用合成进程贯通目标发现→明确确认→单次动作→离线回看，包、CLI和假窗口及安装验证见 [单进程整理运行记录](<运行记录 Run Record/2026-10-30 单进程定向整理实现.md>)。这证明本地命令接口的受控链路，不代表任意Agent平台都能注册工具。当前没有MCP服务、特定平台SDK、跨机器RPC或自动任务。

INT-01的本轮交付是固定版本说明、真实调用合同与Knowledge导航，不安装到用户环境、不运行真实接口、不在Knowledge复制核心。特定宿主如只接受MCP，须另立具体接入任务，不先加网络服务。

# 六、本地批次与正式网页候选
批次请求JSON为`{"targets":[{"pid":101,"created_at":"原始创建身份","name":"显示名","image_path":"完整程序文件路径"}],"protections":[]}`，其中targets必须来自本次发现，不使用示例值发真实动作。API同名memory_batch_cleanup接受ProcessTarget序列，保护规则支持image_path或pid/created_at。记录是独立批次索引加逐项原记录，memory_result只离线回看，不重放。

正式网页使用`python -m cpu_memory_cleanup --web`，或同进程`create_web_server`装配同一Package，HTTP不经过CLI子进程，也不复制维护实现。创建服务不采样；可见网页每秒调用轻量显示接口，工作集主按钮直接提交一次明确动作，高级/批次仍确认。实时显示不等于自动整理，启动后不会默认启用规则；真实请求仍受宿主/用户执行边界约束。服务仅绑定回环地址，默认端口0由系统分配并输出实际URL，显式固定端口失败不自动替换；默认记录与偏好位置见README。假数据入口与真实模式独立，不将假数据通关作为真实平台证据。

API_VERSION=1、tool_schema_version=1仍兼容旧工具；系统/进程记录schema和batch_schema_version分别管理。静态目录当前候选含六项，宿主按实际目录发现，不假设旧Release已包含所有能力。本轮未改Knowledge仓库的固定旧指南，未安装到用户环境，也未发布新包。
