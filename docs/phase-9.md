# Phase 9：全栈部署、CI 与交付验证

Phase 9 / 0.9.0 提供 Linux Python 3.13 应用镜像、独立全栈 Docker Compose、静态 Vue 镜像和 GitHub Actions 工作流。本机实际启动与检查结果分别登记，工作流文件不冒称 GitHub 平台已运行。使用模拟电商数据，仅绑定本机地址；不发布公网，不创建付费云资源。

## 部署边界

保留已有 `deploy/compose.yaml` 基础设施项目与数据。新的全栈项目名为 `enterpriseagent-full`，部署文件为 `deploy/compose.full.yaml`，包含 PostgreSQL、Redis、Chroma、一次性 setup、API 与 web 六个服务，以及仅 test profile 使用的独立 tests 服务。API 绑定 `127.0.0.1:18000`，web 绑定 `127.0.0.1:15173`；PostgreSQL / Redis / Chroma 不映射宿主端口。现有 Windows 开发端口与数据不被新项目覆盖。

setup 负责迁移、2026-10-12 / seed42 / 500 订单虚构数据、512 维真实 CPU Embedding / Reranker 及知识索引，不发送聊天模型请求。API 使用业务和只读分析账号，不能继承初始化 / 测试 / demo 密码。模型资产三个目录/文件从宿主只读挂载，不打入镜像，也不在启动时自动下载；运行缓存使用独立 tmpfs。

API / setup 为非 root 运行，根文件系统只读，数据与日志使用项目 `var/compose/app`。容器设置入口不读取宿主 `.env`，只允许显式公开参数和小型白名单 secret 文件；拒绝内联数据库凭据或远端追踪。默认不启用聊天模型或 Swagger，需要时显式配置 full profile，不自动导入已有 Key。

## 执行入口

已准备固定模型后，项目根目录执行：

```powershell
.\scripts\full-stack.ps1 -Mode Prepare
.\scripts\full-stack.ps1 -Mode Up
.\scripts\full-stack.ps1 -Mode Check
.\scripts\full-stack.ps1 -Mode Test
```

`Prepare` 只创建未存在的本项目目录/随机秘密并验证 Compose，不覆盖已有配置。`Up` 构建 API / web，等待基础设施健康和 setup 完成；`Check` 验证六项 readiness 和前端同源代理。`Test` 使用独立 test profile，强制 PostgreSQL / RAG、运行 Ruff / pip check / pytest 与固定 SQL / 明确故障注入，聊天模型及远端追踪关闭。登录密码为本机 `var/compose/secrets/demo-password` 的随机值，账号名保持一致。

所有包装调用明确指定 full 项目名、`var/compose/compose.env` 与部署文件，避免读取根 `.env` 或误用已有 Compose 项目。Prepare / build / Up / Check 已实际完成，Test 所调用的完整 Linux 后端检查链也已实际退出 0；本机启动和业务验收证据在下方登记。默认模型 Key 文件为空、公共开关为 false，未复制原工作区 Key。

健康产物位于 `var/test-results/phase9/readiness.json`，Linux JUnit 为 `var/compose/test-output/test-results/phase9-linux.xml`。`full-stack.ps1 -Mode Stop` 先核对容器项目归属再停服务，不删除数据。

## 实际验收记录

Linux Node 24.19.0 容器已实际完成 npm ci、Prettier、Vitest **43 / 43（6 文件）**、vue-tsc / Vite 和生产 audit，全部退出码 0，生产依赖漏洞 0。Vite build 9.28 秒是一次构建观察；UI chunk 984.49 kB / gzip 316.59 kB 的 >500 kB 警告保留。JUnit 的 0.4324 秒是测试文件记录时长之和，Vitest 全部墙钟为 16.38 秒，不能把两者替换。原始证据位于 `var/linux-frontend-check`，公开[前端摘要](../evals/results/phase9/frontend/verification.json) / [JUnit 摘要](../evals/results/phase9/frontend/unit.json)保留来源哈希。

Linux amd64 / Python 3.13.15 使用 pip-tools 7.6.2 重新解析平台依赖图，基础锁 **107 项**、开发锁 **119 项**。两套独立 Linux 虚拟环境实际通过 `--require-hashes --only-binary=:all:` 安装、逐项版本检查、pip check、15 个 runtime 导入及 ONNX CPU provider 校验。相对 Windows 的 111 / 123 项，仅排除 colorama、pyreadline3、pywin32、win32-setctime，共有包没有升级。最终解析复用同版本发行物的既有公开哈希；首次全新跨平台哈希下载被中止，保留原状态，不算成功。详见[依赖来源](../evals/results/phase9/linux-locks/provenance.json)。

API / web 镜像实际构建成功，独立 Compose 六个服务中 setup 退出码 0，其余五个服务 healthy。API 与 web 代理均返回 0.9.0 / phase=9，application / database / redis / knowledge / agent_checkpoint / analytics 六项 readiness 全为 ok。[HTTP smoke](../evals/results/phase9/runtime/stack-smoke.json) **13 / 13**，包括编译资源、SPA 路由、订单归属与权限拒绝、真实混合重排及当前引用、只读 SQL 和未授权数据集拒绝。该脚本验证实际 HTTP 与数据库，不包含浏览器交互，聊天模型调用和退款修改均为 0。

[setup 记录](../evals/results/phase9/runtime/setup-report.json)为 25 用户、30 商品、500 订单、1016 明细、456 物流、19 退款、19 工单、5 文档与 44 Chunk，真实迁移、模型资产校验、CPU 推理和索引完成。[容器检查](../evals/results/phase9/runtime/container-security.json)记录 API 10001:10001 / web nginx、只读根文件系统、capabilities 全部移除与 no-new-privileges；API 无 owner / 测试秘密挂载或内联 DSN，模型资产只读。该检查仅证明实际 Docker 配置，不泛称 PostgreSQL / Redis / Chroma 都是非 root 或整个栈只读。

[持久化检查](../evals/results/phase9/runtime/persistence.json)只重建本项目 API，前后业务数量、订单状态与文档版本快照一致，Nginx 代理恢复。另行[DNS 检查](../evals/results/phase9/runtime/dns-recovery.json)将 API 自有 edge 地址从 172.20.0.2 改为 172.20.0.254，保持 Nginx 运行后代理 readiness 恢复；3671.902 ms 为单次轮询观测，不是性能基准或通用恢复保证。

Windows 本阶段初始 **57 项专项**（21 API 元数据与 36 部署配置）通过，JUnit 0.963 秒；新增进程作用域 **48 项专项**与真实 TCP Agent 重启 / MCP stdio **2 项专项**也通过，后二者分别 JUnit 0.481 / 30.196 秒。专项互有重叠，不相加到 Linux 全量总数，也不声称完整 Windows 套件重跑。端到端专项使用明确受控模型，未调用 DeepSeek。

最终完整 Linux 后端检查链退出码 **0**：Ruff lint、**150 文件**格式检查、pip check、固定模型 manifest 核验通过，强制真实 PostgreSQL / RAG 的 pytest **1097 / 1097 通过，0 失败/错误/跳过**。JUnit 记录 **621.404 秒**，控制台记录 **624.08 秒**，87 条 Chroma legacy embedding 配置弃用警告保留。该完整运行覆盖最终业务代码和新增容器测试作用域、资源与 RAG CLI 检查，不与 Windows 专项累加，也不代表线上模型质量。

同一检查链随后完成受控故障 **16 / 16** 和固定 SQL **24 / 24**。故障运行耗时 813.748 ms，34 次 fixture 调用、222 个已知 fixture Token、5 次 fixture 未知消费仅用于计账边界，不是供应商账单。SQL 在 2026-10-14T21:01:34 UTC 实际执行，16 个人工查询与独立基表参考一致、8 个输入按预期拒绝；500 订单 / 1016 明细 / 19 退款的数据指纹前后稳定，聊天模型调用为 0，不称自然语言转 SQL 正确率。原件位于 `var/compose/test-output/evaluation/phase9`，公开摘要与来源见[验收报告](../evals/results/phase9/report.md)。

[前三轮记录](../evals/results/phase9/linux-failed-attempts.json)保留：Ruff 非 root 缓存不可写、两个端到端 fixture 拒绝固定 Compose 主机名、128 MiB `/tmp` 在复制测试模型文件时耗尽。第三轮是 INTERNALERROR 和不完整 JUnit，不能把 880 项局部进展计为完整通过或将控制台与部分 XML 分类混写。最终脚本为每轮在 D 盘测试输出挂载中创建独立目录承载约 397 MB 的具名模型副本，并保留 Linux `/tmp` 处理匿名捕获文件；执行前检查至少 2 GiB 可用空间。固定 Compose 测试主机仅在明确 Linux 标记和专用测试库/账号同时匹配时允许。GitHub Actions 文件存在与本地执行通过不能代替 GitHub 平台运行证明。

## 新语料 RAG 对照

使用普通 API 容器里的稳定入口重新执行：

```powershell
docker compose --project-name enterpriseagent-full --env-file var/compose/compose.env -f deploy/compose.full.yaml exec -T api python -m enterprise_agent.deployment rag-evaluate
```

它读取挂载的普通应用 secret 与 v2 数据集，不读取宿主 `.env` 或 owner，局部关闭聊天模型；默认使用新的 `var/compose/app/evaluation/phase9/rag-<UTC>` 目录。自定义 `--output` 请自行选择新目录，它没有既有目录防覆盖校验。稳定入口已实际复验 60 / 60、退出码 0；与下方初轮分别保留，不合并为一次执行或把重复问题当作新增独立数据。

v2 原件在本项目新 full 数据库和当前活动索引上实际完成 **30 问题 × 两模式 = 60 / 60**，见[原始报告](../evals/results/phase9/rag/report.md)。test 为 17 个可回答 / 3 个无证据，dev 为 8 / 2，分开统计；使用真实 PostgreSQL、Chroma、CPU Embedding 与 Reranker，无聊天供应商调用或 Token 消费。[来源核验](../evals/results/phase9/rag/provenance.json)确认 v2 输入与仓库字节相同，10 个相关源码和模型 manifest 稳定、语料未变；容器未提供 Git commit / dirty 元数据，其 null 保留，不回写成后续提交。

| test 指标 | Vector | Hybrid + Rerank |
| --- | ---: | ---: |
| Recall@1 | 0.941176 | 0.941176 |
| Recall@3 / 5 | 1.000000 / 1.000000 | 1.000000 / 1.000000 |
| MRR / nDCG@5 | 1.000000 / 1.000000 | 1.000000 / 1.000000 |
| 无证据拒绝 / 引用结构有效 | 100% / 100% | 100% / 100% |
| mean / P95 latency ms | 86.385 / 92.746 | 680.383 / 713.196 |

dev 两组 Recall@1=0.8125、MRR=0.9375、nDCG@5=0.9539，Recall@3 / 5 均为 1。两模式在本次小语料上没有文档级质量增益，混合重排时延更高。耗时只反映本轮 Service 调用，不是性能基准，也不与旧 Windows 语料运行直接比较平台性能；本地模型计费 usage 为 N/A。原始结果独立归档，旧 Phase 3 / Phase 8 分数保持原语料边界。

稳定 CLI 复验的文档级指标与初轮相同，test Vector / Hybrid + Rerank mean 为 **92.830 / 731.951 ms**、P95 为 **99.218 / 866.334 ms**，并非替换上表的初轮时间。两次 RAG 均与后端完整测试并行，观察时延受同时运行负载影响，不据此比较 Linux / Windows 性能或推导优化效果；供应商调用仍为 0。复验原件保存在 `var/compose/app/evaluation/phase9/rag-cli-validation`，公开归档目录为 `evals/results/phase9/rag-cli`。

## CI 工作流范围

`ci.yml` 的 frontend 与 linux-stack job 固定 Ubuntu 24.04、Action SHA 和 Node / Python 版本。backend 显式关闭聊天模型与云端追踪，构建 API / web / tests、准备公开固定模型、启动独立 full 项目并运行强制 PostgreSQL / RAG 的测试与离线评估。上传选定 JUnit / JSON / 报告，不上传 secret 文件或完整环境；结束停止本次项目，不删除数据。

后端 chain 的离线评估为 boundaries 与固定 SQL；60 条两模式 RAG 比较由独立 `rag-evaluate` 手动执行，不声称 CI 自动运行或上传该对照。完成所有执行记录不等于回答语义正确，当前引用校验也不证明片段足以回答问题。

本阶段未向 GitHub 提交或触发 workflow，因此不能报告平台 CI 通过；本机 Linux 命令实际结果单独列出。工作流不部署公网、不创建云资源、不发布镜像仓库。

## Markdown 文案修订

本阶段统一使用中性的业务系统说明，修改 README、售后政策前言、评估目录说明和 Phase 3 / Phase 7 / 技术决策记录中的项目定位句。售后政策前言改为“用于模拟电商业务，不属于真实商家的承诺”，退款期限、资格、金额等政策事实没有改变。

售后政策文件字节发生变化，新 SHA256 为 `d7b0a00c8a81007826a3158c1e856dfc1b168ef97449881f69e481779b680f9e`，`rag.json` 升为 v2。后续检索复现须使用对应的新 checksum 和新索引；full 项目新库按新语料初始化，旧发布来源仍受数据库保护，不强改旧原件。旧 Phase 3 / Phase 8 的评估 JSON、CSV、报告与来源哈希保留为当时语料证据，不把旧指标称为新文档版本的结果。Phase 8 `artifact-sources.json` 覆盖的报告 Markdown 未进行文案修改。

## 未验证范围

未执行公网部署、真实支付退款、负载基准或 LangSmith 云端验收。历史模型质量失败与 `manual_review` 保持原结果，部署阶段不据此推导 Agent 泛化能力。

## 后续工作

Phase 9 是原九阶段计划的最后阶段。后续在用户指定的已有 GitHub 仓库上实际运行 CI，再核验平台结果；依据保留的 fail / manual_review 补充独立问题与人工语义标签，复核退款比例口径，并针对多意图退款、知识规划失败改进任务正确性。上述维护不自动执行，不新增产品功能或另行声明开发阶段。
