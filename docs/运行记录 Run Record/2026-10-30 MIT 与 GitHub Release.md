执行日期：2026年10月30日，Asia/Shanghai。

# 一、发布对象
用户确认采用 MIT 并授权 tag 与 GitHub Release，明确不上传 PyPI、不运行真实采样或清理。tag `v0.2.0` 固定提交 `4c7c6492d8323c1e4041a1ee47dec65223a03998`，来自 PR #10 的功能分支，未合并 master。已发布 [0.2.0 预览版](https://github.com/DreamSoul-AI/cpu-memory-cleanup/releases/tag/v0.2.0)，isDraft=false、isPrerelease=true，不作为稳定版或实机效果验收。之后的文档回执提交不移动该 tag。

# 二、构建与安装证据
从该提交 git archive 到隔离 source，以当前 setuptools.build_meta 离线构建 sdist 和 wheel，源码包再经 pip wheel --no-index --no-deps --no-build-isolation 重建成功。Python3.13.9、setuptools80.9.0，未升级构建工具。元数据采用现有最低构建工具支持的 license文件字段，保留工具的弃用提示，不将提示当成构建失败。

检查版本、MIT全文、项目地址、16个产品.py字节与固定源码一致，无缓存及测试产物混入分发。产品与测试代码相对133a948没有变化，完整回归467/467与只读158/158沿用原记录，不声称重新执行。此次对实际安装包使用 python -I 在源码目录外运行4项API测试与1项合成Agent旅程，5/5通过，静态工具目录验证通过，不加载Windows平台实现或GUI。

实际命令、stdout、stderr和安装模块路径见 `.test-results/r020/commands.json`、`receipt.json`、`installed-contract-tests.json` 与 `installed-catalog-check.json`。所用构建脚本为该目录的 `build.py`，没有引入产品运行脚本或依赖。

# 三、附件与远端回读
| **文件** | **SHA-256** |
| --- | --- |
| cpu_memory_cleanup-0.2.0-py3-none-any.whl | 96267f98cfcfdd23a2864fedaab35621735f09dae5c32d0e12f22e324c07ec80 |
| cpu_memory_cleanup-0.2.0.tar.gz | 27b0044c3922609ec7f602900b5167e888829da31bea269e7374ddafcdd95f7c |

发布另附 SHA256SUMS.txt。通过 gh release download 下载三个附件到 `.test-results/r020/downloaded/`，全部与本地产物字节校验一致。先创建draft、上传附件，再发布为预览，未覆盖旧tag或Release。

# 四、完成与限制
REL-01 许可与元数据、REL-02 GitHub预览分发已完成，归档到 [MIT 与发布准备](<../开发记录 Development Record/2026-10-30 MIT 与发布准备.md>)。文档静态检查沿用当前检查工具回执，产品运行验证与文档检查分开。

没有真实采样、清理、GUI、性能或多平台实验，没有EXE、MCP、PyPI或飞书发布。Knowledge尚未接入。许可证覆盖本项目原创贡献，未来引用第三方代码仍须分别核对许可。
