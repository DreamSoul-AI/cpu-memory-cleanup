# 一、首轮实现与分发范围

首轮新增 cpu-memory-cleanup Skill，本地源码在 Knowledge 的 skills/cpu-memory-cleanup，完整 ZIP 在同目录旁。以下保留首轮验证事实，当前职责分离见第四节。当前 Codex 技能目录已安装同内容副本，下一轮可按宿主发现机制使用。产品仍为官方 Windows x64 0.5.0 Rust CLI，构建 8ce5e82d505e8422，没有复制原生清理实现或引入 MCP 服务。

Skill 安装本身没有通用自动执行钩子。安装请求明确包含准备运行时，或首次使用时，运行附带初始化脚本，从官方固定 Release 下载并校验 ZIP SHA256，再只部署 CLI、LICENSE 和 THIRD_PARTY_NOTICES。工具独立放在用户 LocalAppData 的 agent-runtime/v0.5.0，不覆盖已安装应用。初始化只调用静态 --tool-info，不读取内存或执行维护。

# 二、真实初始化与验证

- 在线首次初始化成功：实际从官方 GitHub Release 下载，ZIP SHA256 5957b6d9a30f0e3dfdecb50e2f2f0b089d9e44ff0e803a99a1d8b101561d38ba；CLI摘要2dc891b1d4e203f42126d625384c51bbd95f91eb1b756964a70a608ff4f0d38b，版本和构建匹配。
- PowerShell 7.6.5 隔离验证五项通过：全新目录、复用且不依赖下载、错误ZIP拒绝、已装文件篡改拒绝且不覆盖、独立只读运行时准备。真实 memory_status 返回 success、物理容量大于0、action=null。
- installed-skill-init.json 证明从用户技能目录的独立副本调用初始化，reused=true、sampling_performed=false、maintenance_dispatched=false，不需要原源码路径。
- Skill creator格式验证通过。完整ZIP逐文件与源码及已安装技能副本一致，文档本地链接核对通过，没有捆绑EXE、工具链、测试输出或个人资料。
- Windows PowerShell 5.1 首次运行被本机执行策略拒绝，未改策略、未使用Bypass或内联绕过，改用已允许的PowerShell7验证。初始化采用兼容5.1的语法，但未取得5.1运行通过证据，说明中如实标注。
- 首轮子进程读取静态目录时按Windows默认代码页解码，JSON解析失败；初始化改为ProcessStartInfo显式UTF-8读取CLI后验证通过，失败原件保留。

# 三、结果与执行边界

Skill ZIP摘要：618a41ef480c04194d015a56ef06b7908b0a78e14fcdf4995d07f76c1fcd62f0，大小 10778 bytes。原件位于 .test-results/skill-20261106/，包括pwsh-utf8-validation/summary.json、readonly-status.json、network-init.json、installed-skill-init.json及skill-package.json。

没有执行真实清理、进程终止或自动规则。Skill可以指导后续用户明确指定的一次全局/定向/批次整理；跨入口不要并行维护，partial按真实报告解释，不自动重试或扩大范围。当前能力是工作集整理，不是CPU清理、压缩算法或永久释放保证。同机具备文件和命令权限的Agent可采用，受限云端Agent不能凭Skill远程操作主机；Linux/macOS不安装此Windows运行时。

Knowledge与本项目只有本地修改，未commit、push、PR或发布新版本。其他人现在可拿完整Skill ZIP手动安装，不能把尚未推送的GitHub技能路径当成已公开安装地址。

# 四、代码职责分离后的验收

按本次确认调整分发：Knowledge 技能目录和 ZIP 仅保留 SKILL.md、agents/openai.yaml、references/README.md、references/README_zh.md 四个文件；安装说明调用产品仓库 scripts/agent-skill/install-runtime.ps1，开发验证脚本同目录保存。脚本按字节核对后迁出，Knowledge 及当前 Codex 已安装技能内的两份脚本副本均已移除。

迁移后从产品仓库调用初始化成功，reused=true，版本 0.5.0、构建 8ce5e82d505e8422，sampling_performed=false、maintenance_dispatched=false。初始化与验证脚本的 SHA256 与迁移前一致；没有重新执行完整安装测试或真实读取，沿用第二节原始证据，不把静态复用写成新的真实整理验收。

元数据原文件采用 GBK 编码，本次修正为 UTF-8 并解析核对中文及技能调用名。Skill 格式验证、四文件源码/ZIP/已安装副本逐字节比较、职责分离和受影响本地链接检查通过。

当前纯说明 ZIP：SHA256 7093f9e459c7e464a09c9512102a95ddd4e4791b0eb08df783fe1f4895063ae1，大小 7578 bytes。第一版含脚本 ZIP 的历史摘要保留在第三节，新回执位于 .test-results/skill-20261106/skill-package-separated.json、source-separation.json、product-source-init.json 和 separation-validation.json。

两个仓库新增内容仍未提交、推送或发布。当前本地技能可通过已有产品源码初始化；其他人首次远程使用需等待产品脚本和 Knowledge 同步，旧 v0.5.0 标签不含新增脚本。本次没有运行真实清理或启用自动规则。

# 五、通用版本与更新验收

## （一）实际执行

在 PowerShell 7.6.5 中运行更新后的 test-runtime.ps1，传入从官方 GitHub latest API 取得的 Release 元数据和现有正式 ZIP。七项通过：不同版本元数据选择、排除预发布、全新目录安装、指定版本复用、损坏 ZIP 拒绝、篡改文件拒绝并保留、独立真实只读安装。memory_status 返回 success，物理总量大于零且 action=null。测试未调用整理 API。不同版本用例只验证隔离的元数据解析，没有创建或执行未来版本程序，也不表示该版本已发布。

实际联网执行默认初始化，不传 Version、ArchivePath 或 ReleaseMetadataPath。脚本查询官方 latest 并下载真实 ZIP，取得版本 0.5.0、构建 8ce5e82d505e8422，附件 SHA256 5957b6d9a30f0e3dfdecb50e2f2f0b089d9e44ff0e803a99a1d8b101561d38ba。这次版本来自查询结果，不是初始化常量。随后使用该实际 tag 再联网查询指定版本，同时传入不存在的 ZIP 路径；返回 reused=true，证明从已验证目录复用，没有依赖本地 ZIP。两次初始化均 sampling_performed=false、maintenance_dispatched=false。

## （二）一致性与证据

源码、技能 ZIP 和当前安装副本四文件逐字节一致；元数据为 UTF-8，Skill 格式与本地链接检查通过。Knowledge 的 Skill、工具说明及产品初始化源码均不包含固定 0.5.0 号，历史记录的具体版本不改写。当前 ZIP SHA256 6e545f7ebf3a8f57e34bf0115de689f15a66dd6bd760d299f5cfeb245c46cf23，大小 7989 bytes；此前包的摘要保留为各阶段历史回执。

原始文件：.test-results/skill-20261106/release-latest.json、version-update-validation/summary.json 及逐项 stdout/stderr、auto-latest-init.json、auto-explicit-reuse.json、version-update-skill-package.json、version-update-validation.json。

尚未实际安装一个未来发布的新版本；本轮验证现有官方发布和通用选择逻辑。安装版本以 tag/附件摘要分别部署，旧固定版目录未删除或覆盖。没有真实清理、进程终止、自动规则、提交、推送或发布。新增接入仍待两个仓库同步后公开使用。
