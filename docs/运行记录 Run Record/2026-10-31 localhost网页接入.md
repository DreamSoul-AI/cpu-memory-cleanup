执行日期：2026年10月31日，Asia/Shanghai。Python 3.13.9；工作区分支codex/experience-preview-plan。运行ID的UTC日期保持原义。

# 一、执行范围
全部业务输入为合成数据。HTTP测试和预览脚本注入ScenarioBackend、FakeRuntime，禁止ctypes.WinDLL和真实平台装配；真实回环连接不等于真实内存读取。只使用隐藏内置浏览器的DOM控制，不调用Windows Computer Use、不激活原生窗口、不操作用户鼠标键盘。

# 二、自动化验证
最初 `python -m unittest tests.src.cpu_memory_cleanup.interaction.test_web -q` 的5项HTTP旅程通过。完整回归首次运行 `20261030T205555Z_e5e26edc` 有4项失败：工具目录两项顺序断言、核心物理依赖及层间依赖；quick依赖失败使后续项未执行，不能算完整计划通过。保留原回执于 `.test-results/localhost-regression/`。

修复共同原因后，相关41项定向检查通过。最终统一命令为 `python -X utf8 scripts/run-tests.py --plan regression --output .test-results/localhost-regression-final`，运行 `20261030T205846Z_86b42829`，494/494 passed，84.168秒，完整且按计划放行。记录入口改变后补跑 `--plan readonly_offline --output .test-results/localhost-readonly`，运行 `20261030T210019Z_af2a439a`，167/167 passed，23.206秒。两份回执的源码指纹均为 `3cfe77d0064ef96eff2360872f3098778a8afd35d7260a1fd3c70621a25b9d3b`，回执提交字段11b964b表示运行时HEAD，dirty工作树指纹才对应实际测试内容，不将旧提交本身说成包含实现。

另在 `.test-results/localhost-build/` 离线构建当前快照wheel，核对3个网页资源与源码字节相同，pip以--no-index、--no-deps安装到独立目录，python -I在仓库外运行5项HTTP旅程全部通过，未导入tkinter。wheel SHA256为 `b89ee6f367c29f2f90b18d47571fab9e6b6d54075ac79e32a7497e4cacdd3099`。这只是当前0.2.0元数据的本地验证快照，不是原已发布产物或新Release。命令和回执均在上述目录，未安装到用户环境。

# 三、内置浏览器验收
运行 `python -X utf8 scripts/preview-memory-web.py --port 8766`，IAB隐藏标签访问http://127.0.0.1:8766/。先确认未读状态，再显式读取76%合成占用及4项进程；选择PID 101、确认后显示0.94→0.56 GiB工作集；离线回看没有新增动作；全局按钮经独立确认显示系统可用量前后变化；搜索meeting、刷新和选择保留正常，状态读取没有覆盖整理结果。点击“停止服务”并确认，页面禁用动作，服务进程正常退出。原始记录在 `.test-results/localhost-preview/`，session仅有process/PID101和一次system合成动作。

最终修正版本再次启动到 `.test-results/localhost-preview-final/`，验证初始零读取页面、显式状态和目标、多选PID101与301、逐项工作集结果及离线回看。session.json只有这两个process动作，回看不重放；浏览器错误与警告日志为空。通过页面停止服务，命令退出0。截图selected-result.jpg及stopped.jpg、DOM证据browser-evidence.json、源码与网页资源哈希source.json保存于同目录。取消和单工作单元时序由HTTP集成测试的可控阻塞验证，未将快速假动作下的浏览器按钮观察当作取消时序证明。

# 四、未执行与限制
未运行真实采样、真实进程枚举、权限调整、清理、真实桌面GUI或托盘；未启用规则、公开部署、发布tag/Release/PyPI或推送远端。网页当前只提供仓库内假数据启动脚本，轮询仅查询业务任务状态，不循环采样。验收服务已正常停止；关闭标签不会停止服务的说明保留在页面及README。原桌面原型不属于本次已完成验收。

最终文档静态检查通过：67份Markdown、224处相对引用，未发现编码、围栏、链接或锚点错误，git diff --check为0。回执为 `.test-results/package-tool-final/checks.json`。源码与资源验证后仅继续修改文档并保存本地commit，没有再改产品代码，因此不重复运行相同测试。
