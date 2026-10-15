# Phase 9 部署与持续集成验收

当前状态：**本机验收通过**。本报告只记录实际执行证据，不将 GitHub 托管运行计为完成。

本阶段新增独立的 Docker Compose 项目 `enterpriseagent-full`。API 和 Web 仅绑定本机 `127.0.0.1:18000`、`127.0.0.1:15173`，数据放在项目的 `var/compose` 目录。原有开发服务与容器保留。模型调用和远程 LangSmith 上报默认关闭；既有模型密钥不复制到新部署中。

## 已取得证据

- Linux `amd64`、Python 3.13.15 完整后端套件 **1097 项通过，0 失败、0 错误、0 跳过**，其中端到端 4 项、集成 241 项、单元 852 项。真实 PostgreSQL、Redis、Chroma 与 CPU 检索依赖均被要求启用；Ruff 检查、150 个文件格式检查、`pip check` 和模型清单校验通过，完整链路退出码 0。[JUnit 摘要](linux/python-summary.json)与[退出原件](linux/exit.json)分别记录原始来源哈希。
- 完整套件 JUnit 耗时为 **621.404 秒**，pytest 控制台总过程耗时为 **624.08 秒**，二者不混用。87 条 Chroma `legacy embedding function config` 弃用警告保留，没有计为失败，也未静默屏蔽。
- [事后测试镜像源码核对](linux/test-image-source-check.json)对最终测试镜像内 155 个 Python 源码、测试、脚本、迁移、评估数据集及纳入镜像的部署文件逐项计算 SHA256，与宿主文件全部匹配。这是执行后的镜像比较，不是运行前全仓快照；未将 Git 元数据或 README 纳入此证明。[最终 Windows 源码检查](native-source-checks.json)的 Ruff、188 个文件格式检查与 `pip check` 通过，不计为额外的 Windows 完整 pytest。
- API 基础集成与部署配置校验：57 项通过，0 失败、0 错误、0 跳过，JUnit 记录耗时 0.963 秒。该次执行包含 21 项 API 测试与 36 项部署配置测试，不与重复执行的部署子集合并计数。
- [初始测试摘要](python-initial.json)由实际 JUnit 生成，记录原始文件 SHA256。
- Linux `amd64` 前端使用 Node 24.19.0，实际 `npm ci` 后 6 个测试文件、43 项测试全部通过，格式与类型检查通过，构建耗时 9.28 秒；生产依赖审计报告 0 个漏洞。[执行结果](frontend/verification.json)、[JUnit 摘要](frontend/unit.json)、[审计原始 JSON](frontend/audit.json)均有来源哈希。
- 前端 Vitest 总过程耗时 16.38 秒与 JUnit 测试文件时长总和 0.432399204 秒属于不同口径，不混写为同一个性能指标。UI 构建块仍为 984.49 kB，gzip 316.59 kB，保留大于 500 kB 的构建提示。
- Linux `amd64`、Python 3.13.15 下重新解析并实际安装带哈希的依赖锁，基础依赖 107 项、开发依赖 119 项逐个版本匹配，运行时导入和 `pip check` 通过。与 Windows 共用包版本没有变动，排除 4 项 Windows 专用依赖。[依赖来源和验证](linux-locks/provenance.json)明确区分安装验证与完整业务测试。
- 最终锁由 pip-tools 7.6.2 在 Linux 解析，复用同版本发行物已有哈希，并以 `--require-hashes --only-binary=:all:` 实际安装验证。首次全新跨平台哈希下载尝试在输出前停止，不计为成功；其[原始状态](linux-locks/initial-attempt.json)也保留。
- 对明确测试作用域的固定 Compose 数据库地址增加平台兼容校验后，[部署与进程作用域专项](python-process-scope-unit.json)48 项通过；[Windows 真实 TCP 重启与 MCP stdio 专项](python-process-scope-windows-e2e.json)2 项通过。二者均为独立专项验证，不与最终完整套件测试数相加；端到端测试使用明确的受控模型，未调用 DeepSeek。
- 新增部署镜像的固定 RAG 评估入口，使用普通业务凭据和镜像内 v2 数据集并关闭聊天模型；[CLI 边界专项](python-rag-cli-unit.json)6 项通过，作为独立专项，不与最终完整套件计数相加；正式入口已另行实际复验。
- 完整 Compose 已实际启动，API 与 Nginx 代理返回 `0.9.0 / Phase 9`，6 项依赖就绪。[HTTP 检查](runtime/stack-smoke.json)13 项通过，覆盖静态产物与 SPA 路由、匿名和跨客户越权拒绝、真实订单查询、知识引用、混合检索与 Rerank、分析视图 SQL 查询及未授权数据集拒绝。该项不包含浏览器交互，模型调用与退款修改均为 0。
- [初始化记录](runtime/setup-report.json)包含真实迁移和模拟数据：25 个用户、30 个商品、500 个订单、1016 个订单项、456 条物流、19 个退款申请、19 个工单、5 个文档及 44 个分块；CPU 模型资产校验和索引完成，没有重置已有业务数据，也没有向模型服务发送请求。
- [容器配置检查](runtime/container-security.json)确认 API 与 Web 使用非 root 用户、只读根文件系统、移除 capabilities 并限制本机端口；API 没有 owner 或测试账号凭据挂载、Docker 配置中没有内联凭据，模型目录只读。PostgreSQL、Redis、Chroma 的实际用户和挂载按原始记录呈现，不泛称整个栈的根文件系统只读。
- [持久化检查](runtime/persistence.json)只替换本项目 API 容器，容器 ID 改变，业务数量、订单状态和文档版本快照一致，代理恢复。此次容器替换前后的 IP 相同，因此不用于证明地址变化后的恢复。
- [独立 DNS 恢复检查](runtime/dns-recovery.json)随后只将已验证 API 的自有 edge 网络地址从 `172.20.0.2` 改为 `172.20.0.254`，Nginx 保持运行并重新代理就绪。轮询耗时 3671.902 毫秒是单次本地观测，不作为性能基准或广义故障恢复结论。
- [原有开发环境就绪记录](runtime/native-readiness.json)确认 Windows API 已更新为 0.9.0，`8000 / 5173` 的 API 与 Vite 代理仍可访问，6 项依赖就绪，本次检查没有模型请求。
- [最终镜像就绪记录](runtime/final-readiness.json)确认运行 API 容器使用最终重建的镜像 ID，部署 CLI 源码与 v2 数据集在运行镜像内的实际 SHA256 与宿主文件相同；`18000 / 15173` 的 API 与代理均为 0.9.0、6 项依赖就绪。保留先前的启动、容器替换和 DNS 记录，不覆盖原件。
- [最终四端点复查](runtime/final-local-readiness.json)在恢复已退出的本项目原生 API/Vite 进程后，分别验证 `18000 / 15173 / 8000 / 5173` 四份真实响应均为 0.9.0、Phase 9，6 项依赖通过。完整 Compose 在此期间保持运行；该复查没有模型请求或浏览器交互，不覆盖先前原件。

## 更新语料的真实检索评估

`enterprise-agent-rag-policies-v2` 语料重新执行 30 个问题、2 种配置，共 60 条真实检索记录，0 错误。dev 为 8 个可回答和 2 个无证据问题；test 为 17 个可回答和 3 个无证据问题。该小型模拟标注集已被开发者接触，不是盲评保留集。

| test 指标 | vector | hybrid_rerank |
|---|---:|---:|
| Recall@1 | 0.941176 | 0.941176 |
| Recall@3 / Recall@5 | 1 / 1 | 1 / 1 |
| MRR / nDCG@5 | 1 / 1 | 1 / 1 |
| 引用结构有效率 / 无证据拒绝率 | 100% / 100% | 100% / 100% |
| 平均延迟 / P95，毫秒 | 86.385 / 92.746 | 680.383 / 713.196 |
| 执行错误 | 0 | 0 |

dev 两种模式的 Recall@1 为 0.8125、MRR 为 0.9375、nDCG@5 为 0.953866，Recall@3 / Recall@5 均为 1。此次两种模式的检索准确率相同，混合检索与 Reranker 没有带来指标改善，耗时更高；这些延迟是本次本地运行记录，不是性能基准。引用结构校验不等于回答语义充分或生成答案忠实度校验。

[原始结果](rag/results.json)、[汇总 CSV](rag/metrics.csv)、[逐查询 CSV](rag/queries.csv)、[原始报告](rag/report.md)均原字节保留。评估模块复用既有入口，原始报告中的 Phase 3 标题保留；本次执行归属 Phase 9。[数据集与源码来源](rag/provenance.json)确认运行输入与仓库 v2 数据集字节相同，10 个相关源码文件的运行前后哈希稳定并与当前宿主源码匹配，模型清单与语料未变化。镜像内报告的 Git 元数据为 null，不回填为事后提交号。

随后在最终重建镜像中实际运行 `python -m enterprise_agent.deployment rag-evaluate`，独立完成 60 条记录，0 错误、退出码 0。其 [4 份原始产物](rag-cli/report.md)、[退出记录](rag-cli/exit.json)及[输入和相关源码证明](rag-cli/provenance.json)单独归档，首轮指标不改写、不合并。准确率与首轮相同；正式入口本次 test 平均 / P95 为 vector `92.830 / 99.218 ms`、hybrid_rerank `731.951 / 866.334 ms`。这一复验验证入口能实际运行，不计为新的准确率优化实验。

两次都只运行检索，没有聊天模型请求，生成输入和输出 Token 均为 0；本地 Embedding 与 Reranker 的 API 计费 Token 为 N/A。公开前对当前可用凭据进行了[精确匹配扫描](rag-credential-scan.json)，两次共 8 个评估输出和 1 个退出摘要文件 0 命中。历史指标和历史来源哈希保留。

## 工作流边界与 SQL 对照

完整 Linux 链路另行实际执行 [16 项工作流边界检查](boundaries/report.md)，16 项声明的受控预期均通过，耗时 813.748 毫秒。生产 LangGraph、参数校验、人工中断和回放逻辑真实执行，模型与业务 Service 使用明确的 fixture，检查范围包括参数错误、身份字段伪造、有限重试、步数预算、超时和审批回放。fixture 记账为 34 次调用、222 个已知 Token、5 次用量未知调用；没有模型服务请求，Provider Token 为 N/A。这不代表 DeepSeek 任务完成率，也不代替真实数据库授权与退款规则测试。其 [JSON](boundaries/results.json)、[CSV](boundaries/results.csv)与[匹配实际 SHA256 的输入数据集](boundaries/dataset.json)原字节保留。

[只读 SQL 对照](sql/report.md)实际执行 24 项，24 项通过：16 项由分析只读账号执行授权视图 SQL，与独立基表聚合结果比较；8 项不受支持的 SQL 在解析校验层拒绝。独立参考查询使用 owner 的只读事务，不将 owner 作为分析 API 的连接账号。固定问题日期为 `2026-10-12 / Asia/Shanghai`，实际执行时间为 `2026-10-14T21:01:34.113241+00:00`，测试数据库执行前后保持 500 个订单、1016 个订单项、19 个退款申请及相同指纹。模型调用为 0；这些人工编写的 SQL 不计为自然语言转 SQL 正确率。其[完整结果](sql/results.json)和[原输入](sql/dataset.json)单独保留。

上述 16 项与 24 项评估不加入 1097 项 pytest 计数；初始及各专项重复子集同样不相加。[历史原件校验](historical-preservation.json)确认 Phase 8 原始产物清单内 19 份文件的 SHA256 全部未变。

## 发布检查

[最终发布检查原件](release-checks.json)记录 `2026-10-15T08:52:31.7186450Z` 的实际扫描：16 个当前已配置凭据对 6681 个源码、前端产物、评估、测试与构建运行日志文件精确匹配，0 命中；45 个 Git 可见 Markdown 的项目定位措辞扫描 0 命中；386 个本地文件链接 0 缺失，`git diff --check` 通过。扫描不包含第三方依赖包、私有凭据原文件、远程链接或 Markdown 锚点，也不声明能发现未知凭据。Git 暂存区的归档字节保持检查由仓库提交前独立执行，避免把清单包含自身造成循环校验。

## 已保留的未完成尝试

前三次 Linux 后端链路没有计为通过。第一次以退出码 2 结束，原因是非 root 进程的 Ruff 缓存目录不可写；第二次在两个端到端 fixture 拒绝固定 Compose 测试数据库主机名后，停止了本次专用测试容器，退出码 137，未生成完整 pytest 报告；第三次在模型临时文件复制期间耗尽容器的 128 MiB `/tmp`，出现 `INTERNALERROR / OSError 28`，退出码 1，仅有不完整的 JUnit，不能计作完整套件结果。[尝试记录](linux-failed-attempts.json)保留实际退出文件与部分 JUnit 的哈希，原始日志和原始失败 XML 不公开。

资源修正保留 Linux `/tmp` 的匿名捕获文件，将大型 pytest fixture 和 Ruff 缓存放入项目 D 盘挂载上的新运行目录，检查至少 2 GiB 剩余空间，不删除旧目录。单独将 `TMPDIR` 指向 Windows 磁盘挂载时产生过[捕获文件 collection 失败](resources/disk-resource-attempt1.json)，该尝试不计为通过。最终[模型完整性资源专项](resources/disk-resource-verification.json)真实复制 396,804,133 字节模型，在源模型仍只读的条件下 1 项测试通过；[JUnit 摘要](resources/model-integrity-unit.json)耗时 12.09 秒，pytest 过程耗时 12.13 秒，不与完整套件计数相加。资源修正后的完整执行已通过，前三次尝试仍按各自失败或未完成状态保留。

## 证据范围

只公开脱敏 JSON、CSV、Markdown 和测试摘要，不归档凭据文件、认证响应或原始运行日志。已有历史评估指标及历史来源哈希保留。

GitHub Actions 工作流的本机等效执行不代表 GitHub 托管运行。本阶段不推送仓库、不部署公网、不创建云资源，且不把使用测试替身的单元测试计为真实模型调用。
