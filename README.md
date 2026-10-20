# CPU 内存清理

Windows 主机内存观察与显式单次释放工具。Python 3.10+，Windows 10/11，使用标准库 ctypes 和 tkinter，无第三方运行依赖。

默认只读；历史桌面 A/B 未证明持续净收益，不能把可用内存上升视为加速成功。用户当前要求先不运行清理，以下仅为功能与命令说明，本轮未执行。

## 文档入口

- [项目协作约定](AGENTS.md)：中文要求、飞书参考、执行边界与文档维护规则。
- [通用调研](<docs/CPU 内存清理 通用调研.md>)：以 360 PC 加速为主要产品对照，研究 CPU 治理、内存清理方法、现有技术、实现机制与验证边界。
- [架构设计](<docs/CPU 内存清理 架构设计.md>)：依据关联资料库的软件架构设计研究，从质量场景定义状态归属、接口合同、源码依赖、运行部署与验证，不受旧代码框架约束。
- [代码结构设计](<docs/CPU 内存清理 代码结构设计.md>)：目标目录树、各 Python 文件职责、允许依赖和对应测试位置，尚未实施。

## 关联的 DreamSoul 资料
本仓库地址：[DreamSoul-AI/cpu-memory-cleanup](https://github.com/DreamSoul-AI/cpu-memory-cleanup)。源码位置以当前项目实际关联的仓库为准。

每次从当前项目关联文件夹和资料链接定位 DreamSoul 文档库，读取其资料入口、相关规范与已有调研，再沿用已有飞书分享链接。文档库通常通过 `文档/规范/` 和 `研发/研究/` 定位，不依赖固定盘符、用户名或机器目录。

未关联本地文档库时，从 [飞书规范总索引](https://dreamsoul.feishu.cn/docx/UmU1d2EwLoeET5xCJChcOKPknyb) 和 [工具与教程总索引](https://dreamsoul.feishu.cn/docx/VSQ5dlJl3o0hbIxrEhUc8qKEnBc) 查找。代码、协作、测试、日志、CI/CD 与调研等规范的任务入口统一维护在 [项目协作约定](AGENTS.md)。

项目文档按 [飞书文档规范](https://dreamsoul.feishu.cn/docx/DdmxdrNvBo9AMXxkAYmc4EJ6nwe) 组织，通用调研另依照 [通用调研规范](https://dreamsoul.feishu.cn/docx/YcWIdE4INoLqoLxR5O8cbl7hnTf)，2026年10月21日已读取对应飞书正文。规范读取不代表历史实验或当前系统状态已重新验证。

## 文件结构

```text
cpu-memory-cleanup/
├── AGENTS.md
├── README.md
├── cpu_memory_cleanup/  Python 源码
├── scripts/             桌面启动与进程树采样脚本
├── tests/               测试
└── docs/
    ├── CPU 内存清理 通用调研.md
    ├── CPU 内存清理 架构设计.md
    └── CPU 内存清理 代码结构设计.md
```

## 只读功能

以下为命令示例，执行会查询当前机器或读取指定文件；本轮未运行。

```powershell
python -m cpu_memory_cleanup
python -m cpu_memory_cleanup --json --top 10
python -m cpu_memory_cleanup --release-preflight --json
```

有界采样使用 `--sample --interval 秒数 --count 次数`，并通过 `--jsonl 文件` 或 `--json` 保留输出；默认不枚举进程，需要排名时加 `--with-processes`。离线分析支持 `--analyze`、`--pressure`、`--tree-summary`、`--ab-compare`；`--pressure-follow` 只跟随日志，不自行采样。

容量输出使用 bytes 或 GiB，缺失值为 `null`。速率需要前一拍，第一拍没有速率值。工作集包含共享页，不能相加当作可回收量。

## 显式释放与桌面界面

CLI 的 `--release` 才触发直接单次释放，`--release-what` 可选 `working-set`、`standby` 或 `both`，默认两者；其它只读与分析模式不释放。A/B 流程 `--ab-protocol 输出目录` 默认只生成计划与检查结果，加入 `--ab-live` 才真实采样并在 B窗前释放。

`scripts/desktop-memory-ui.cmd` 打开单窗口，打开时只读；点击“一键加速”才释放工作集。`scripts/desktop-memory-button.cmd` 无参数只读，显式参数 `release` 才释放工作集。两个桌面入口都不调用 standby 清除。

不结束进程、不停服务、不修改系统配置、页面文件或注册表，不自动提权。真实释放可能引起重新调页、磁盘回写和响应代价。代码已知的失败处理、部分成功判定及记录缺口见通用调研第六章，尚未修复。

## 验证与历史输出

2026年10月21日按用户要求删除整个 `logs/` 和历史样例附件。保留的历史结果已汇入通用调研；原始数据不在本地，不能直接复算，旧读数也不代表当前状态。不要为文档示例自动重建历史日志。

释放与权限调整测试必须注入假调用。全量测试含实时系统采样和界面测试，运行前须检查范围；本轮仅做文档与引用检查，没有运行完整测试或任何系统清理。
