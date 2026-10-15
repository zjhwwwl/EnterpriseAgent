# 测试与 Evaluation

## 真实结果的边界

Phase 9 / 0.9.0 增加 Linux 部署与 CI 检查，真实执行范围和结果见[Phase 9](phase-9.md)。已有严格 Agent 案例合同、真实证据采集、受控故障集及元数据追踪保持原实现。真实 RAG、人工 SQL、线上模型和受控流程分开呈现；[Phase 8](phase-8.md)评估、[Phase 7](phase-7.md)浏览器/前端及更早结果保留原始证据，不改写为容器交付阶段的新模型成绩。

```powershell
.\scripts\check.ps1
.\scripts\evaluate-rag.ps1
.\scripts\evaluate-analytics.ps1
.\scripts\frontend.ps1 -Mode Check
```

check 执行 Ruff、格式、pytest、pip check，默认强制 `--require-postgres --require-rag`，当前 Windows 产物为 `var/test-results/phase9.xml`、`phase9.json`，前端为 `phase9-frontend-unit.json`。`-SkipPostgres` / `-SkipRag` 明确排除对应环境检查；直接 pytest 未配置依赖可能跳过，不能当作完整验收。Linux 测试容器单独保存 `var/compose/test-output/test-results/phase9-linux.xml`，不覆盖 Windows 或历史成绩。浏览器必须注明连接真实后端还是受控接口，不能互相替代。

## Phase 9 部署与 CI 验证协议

Linux 哈希锁由目标 Python 3.13 重新解析，用共有版本约束保持兼容选择，而不是截取 Windows 锁。依赖安装、pip check、512 维 CPU Embedding、真实 Reranker、数据库迁移/权限、知识索引、六项 readiness 和静态前端代理分别记录。真正的容器构建与启动证明部署行为，不替代模型任务质量；没有发送消息的启动/HTTP 检查供应商请求为零。

```powershell
.\scripts\full-stack.ps1 -Mode Up
.\scripts\full-stack.ps1 -Mode Check
.\scripts\full-stack.ps1 -Mode Test
```

测试容器只使用 full 项目的 `enterprise_agent_test`、专用业务与 reader，初始化账号仅供测试夹具；不会重置应用库。Linux 后端脚本显式关闭 DeepSeek 和 LangSmith，固定 SQL 的独立基表参考与 16 条受控故障仍按各自协议记录，不冒充新的在线任务完成率。GitHub Actions YAML、本地执行命令和 GitHub 平台 workflow 结果分别核验；没有真实平台运行时不报告 GitHub CI 已通过。

当前 CI chain 包含强制 RAG 的完整 pytest、boundaries 与 SQL；60 条两模式相关性对照属于独立 `rag-evaluate` 命令，不自动加入 CI 或上传。其 `run_status=complete` 是执行完整性，引用来源匹配是结构验证，二者都不能替代知识答案语义充分性的人工判断。

本阶段售后前言文案改变，`evals/datasets/rag.json` v2 保存新 SHA256。检索评估匹配新原件、数据库 checksum 与活动索引；已有受保护政策来源不能通过修改 checksum 强行替换。新 full 库上已实际完成 60 条模式记录，test 两组 Recall@3 / 5、MRR、nDCG@5 均为 1，Recall@1=0.941176；拒答与引用结构有效率均为 100%。Vector / Hybrid + Rerank mean 为 86.385 / 680.383 ms、P95 为 92.746 / 713.196 ms，没有文档级质量增益，较高时延保留。dev 独立统计，单次服务耗时不作为性能基准；无聊天供应商调用，本地模型计费 usage 为 N/A。

最终 Linux 完整后端 **1097 / 1097 通过，0 失败/错误/跳过**，强制真实 PostgreSQL / RAG，JUnit 621.404 秒、控制台 624.08 秒；87 条 Chroma 弃用警告保留。Ruff、150 文件格式、pip check 与固定模型核验通过。后续受控故障 **16 / 16**（34 次 fixture 调用、222 已知 fixture Token、5 次 fixture 未知消费）与人工 SQL **24 / 24**（16 条独立基表参考、8 条拒绝）分别统计，供应商均无请求。SQL 的 500 订单 / 1016 明细 / 19 退款指纹稳定，问题日期仍为 2026-10-12，实际运行 UTC 为 2026-10-14，不据此宣称 NL SQL 正确率。

旧 Phase 3 / Phase 8 JSON / CSV / 报告与原 corpus 保留，历史指标不转移为新语料成绩。本次 HTTP 13 项、六项 readiness、容器权限、重建持久化、DNS 恢复、Linux 依赖与前端检查分别归档，前三轮失败与最终完整运行见[Phase 9](phase-9.md)与[验收报告](../evals/results/phase9/report.md)，不把 Windows 57 / 48 / 2 项重叠专项相加。

## 测试分层

| 层 | 当前证据 |
| --- | --- |
| 确定性单元 | 配置、错误、日志、Argon2/JWT、退款规则、真实文件解析、分块覆盖、BM25/RRF、指标计算、runtime 容量 |
| PostgreSQL 集成 | 迁移、普通账号、归属、事务、并发、幂等、确认、审核、政策版本/来源 trigger |
| 进程内 HTTP | Pydantic、认证、角色、订单与知识接口契约、错误、不回显输入 |
| 真实进程 HTTP | Uvicorn + TCP、数据库退款闭环与服务停止监听；新增知识路径按实际报告验收 |
| 真实模型/Chroma | 固定模型校验、真实向量及重排、官方 SDK/实际向量库；没有内置假向量回退 |
| Retrieval Evaluation | 固定五文档、同配置对照、文档级相关性、引用合法性和耗时 |
| Tool 契约 | 严格参数、禁止身份/确认注入、复用 Service、安全错误；单元控制不当数据库证明 |
| Chat HTTP 协议 | 受控 HTTP 响应验证 Tool Call、usage、非法 JSON、未知工具、响应上限和超时，不调用线上模型 |
| 官方 Checkpoint | 实际 PostgreSQL saver、runtime 重启、interrupt/resume、普通账号无 DDL、advisory lock 与取消释放 |
| 真实 Agent 流程 | ScriptedChatModel 只控制路线；业务数据库、CPU Embedding/Reranker、Chroma 与官方图状态实际执行 |
| MCP 单元协议 | 官方内存 transport 与受控 auth/tools 验证发现、strict 参数、错误脱敏、超时、日志隔离；不冒称数据库查询 |
| MCP SQL/RAG 集成 | 官方 SDK 请求、真实 AuthService / Service，实时角色/撤销/禁用、订单归属和限定政策文档 |
| MCP 实际 stdio | 生产 Server 子进程、官方 ClientSession、真实普通 PostgreSQL / CPU 模型 / Chroma、协议 stdout、秘密扫描与实际进程退出 |
| 分析 AST / 报告 | 精确节点、视图/列/函数白名单、限制改写、精确金额、NULL/截断与图表来源 |
| 分析真实 PostgreSQL | 原始订单/明细的 Python 基准、上海支付边界、退款 cohort、交易快照、专用 reader ACL、超时与连接恢复 |
| 分析 HTTP / 图 | 实时角色校验、严格 SQLPlan、相同 Service、实际只读执行与持久化；受控路由与真实在线模型分开 |
| Vue 单元/组件 | 金额精度、安全 Markdown、API/身份变化、引用核验、status 和显式人工确认；使用受控请求时不当作数据库或线上模型证明 |
| Vue 真实浏览器 | 实际页面、登录、导航、API 结果与错误、权限、知识索引及售后流程；具体执行/截图按本阶段记录 |
| Agent 案例合同 | 严格数据集、可信订单绑定、状态/工具/权限/引用/审批/SQL 证据检查；缺证据保留复核状态 |
| 故障 Evaluation | 生产 LangGraph 与参数校验，声明模型/Service/内存 Checkpoint fixture；不作为线上或数据库质量成绩 |
| 元数据追踪 | 本地白名单 JSONL、累计 usage、未知/中断消费、实际 SDK 与受控 requests transport；无真实云导出证明 |

控制流程测试可以注入明确测试替身来隔离故障，但不能用这些结果冒充真实模型或向量库成绩。事务/锁/权限测试使用 PostgreSQL，不使用 SQLite 替代。解析测试使用实际 PDF/DOCX bytes，不用粘贴文本充当 PDF 解析证明。

## 数据集

[rag.json](../evals/datasets/rag.json) 共 **30 条：25 可回答、5 无证据**；dev=10，test=20，其中 test 有 17 可回答和 3 无证据。依据当前五篇实际模拟文档人工标注 relevant_files、expected_status 与事实点，固定 SHA256。

这是文档级二元相关性集，不是生成答案评分集。多文档标签用于相关文档召回；引用存在与片段在语义上足以支持某个结论分开看待。详细协议见 [evals/README](../evals/README.md)。

dev 用于分块、检索和门槛调参；test 单独呈现为主结果。不把人工自编数据称为盲评，也不使用 test 反复选择阈值后宣称未参与调参。

## 真实 A/B 执行

`python -m enterprise_agent.evaluation --output var/evaluation/phase3` 使用受限应用账号、真实 ModelRuntime、ChromaStore 和 KnowledgeService。它不导入种子、不自动索引、不下载模型、不调用 DeepSeek。

固定语料来自五篇 `data/knowledge/*.md`，通过已登记文档 ID 限制 corpus，用户上传内容不进入本次对照。原件与 DB checksum、可见性、索引代数和 fingerprint 必须一致。

- A：纯 Vector。
- B：中文 BM25 + Vector + RRF + 真实 Reranker。
- 相同 Embedding、原件、Chunk、top_k 与运行环境；向量/BM25 上限各 30，RRF k=60，融合最多 30。
- 默认 top_k=5 个 Chunk；先按文档 ID 去重，可能少于五篇不同文档。
- 每模式同一 dev 查询 warmup 一次，记录但不计指标。
- 正式问题按数据集顺序执行，模式顺序交替。
- `apply_gate=False` 保留原始候选排名，status 仍按 Service 的真实相关性门槛判断，无需重复模型推理。

Reranker 返回 logit 经 sigmoid 得到的0–1未校准分数，不是概率。所有模式需向量相似度通过门槛，hybrid_rerank另需同时通过重排门槛；门控只检查相关性，不能证明语义充分、政策无冲突或生成答案忠实。

## 指标定义

| 指标 | 当前计算口径 |
| --- | --- |
| Recall@1/3/5 | 前 K 篇去重文档中的相关文档数 / 标注相关文档数 |
| MRR | 第一篇相关文档排名倒数的均值 |
| nDCG@5 | 二元相关性、log2(rank+1) 折扣、归一化理想排序 |
| 门控 | 可回答接受率、无证据拒绝率、全部问题正确率 |
| 引用有效率 | 单独调用真实 citation API/Service 校验文本、位置、权限与当前代数 |
| 执行 | 完成率、错误数量，失败不从可回答指标分母删除 |
| latency | Service 真实墙钟 mean/P95，排除后续引用验证；P95 为 nearest-rank |
| 生成模型使用量 | Service 实际返回的调用与输入/输出 Token；当前路径没有生成调用 |
| 本地模型 API 使用量 | null / N/A，本地 Embedding/Reranker没有在线计费 usage |

无证据问题没有相关文档，Recall/MRR/nDCG 为 N/A，只参与门控与执行统计。一次运行每问题每模式一次，不能当作严谨性能基准；冷启动成本在 warmup 单列，不混入热查询平均数。

## 产物与版本

输出目录包含 results.json、metrics.csv、queries.csv、report.md。JSON 保存每查询完整排名、真实分数、引用检查、stage_ms、状态和失败；CSV 与 Markdown 分开 dev/test 汇总。

记录 Git commit/dirty、UTC、数据集 hash、代码 hash、模型仓库 fixed revision 与文件 hash、实际 manifest hash、依赖、CPU/系统、语料 checksum/version/generation 及配置。原件 checksum 在执行前校验；结束时核对源码、数据集、数据库语料版本与模型 manifest，变化时比较无效。部分、失败或中止运行不算完整成功，执行器保留测得记录。

文件 SHA256 记录运行时的原始字节；Git 换行归一化可能使检出文件的 SHA 不同。复现时同时核对 Git 版本、平台、模型与配置。

检查通过及引用合法不能替代开放式语义正确性。历史 DeepSeek 手选 5 场景烟测不能称完整在线任务/意图/参数/语义质量分数，也不能将 5/5 检查通过称模型任务完成率 100%。MCP 协议验收未引入模型；NL SQL 工程安全与实际模型规划需各自独立证据。

## Phase 4 受控图验收

tests/integration/test_agent_workflow.py 使用固定 seed UTC 2026-10-12、真实普通 PostgreSQL 连接、真实固定 CPU 模型、独立 Chroma 集合、适用退款政策索引与官方 PostgreSQL Checkpointer。ScriptedChatModel 给出明确 route_task / 业务工具规划，assistant/tool_calls JSON 符合兼容协议，脚本耗尽会失败。

覆盖待确认无退款/工单、task_id 准备重放、runtime 重启后一次确认、重复确认、跨用户拒绝、拒绝审计、同会话 busy、参数拒绝、工具/步数上限、有限临时错误重试与多轮历史。在真实 prepare/confirm 事务返回后注入 CancelledError 模拟 worker loss；SDK包装后的可恢复失败经 recover 重跑，实际审批、退款、工单和审计只有原记录。不宣称OS进程恰好在业务提交瞬间被杀的实测。独立Uvicorn TCP测试在waiting_approval阶段真实停止/重启进程后确认一次。重索引跨审批后仍保留已提交事实，并明确清除陈旧引用、记录 warnings。

这验证软件控制与业务一致性，不证明模型理解。受控usage仅验证计账，不列为实际消费。初验161项unit、37项真实PG/RAG/API、1项TCP预检及完整511项通过（JUnit524.068秒、81条Chroma warning）保留原始记录；TCP JSON仍controlled_model_only=true、online_deepseek_tested=false、provider_token_usage=null。后续在线修复110项核心回归通过，冻结源码[517项完整复验通过](../evals/results/phase4-live/verification.json)；旧511摘要不覆盖为新代码证明。

真实在线运行必须另外记录所用模型、供应商时间与usage、源码/数据版本、完整任务结果及人工质量标签。未报告usage的失败标记 unknown_usage_calls 与 usage_complete=false，已知Token和不能代替完整消费。中断的不完整标记跨 recover 保留，恢复后也不能把未知消费宣称完整或归零。缺Key或未实际运行时明确保留“未在线验收”，不补零Token或预填正确率。

Phase 4公共证据已归档到evals/results/phase4/：[verification.json](../evals/results/phase4/verification.json)、[http-restart.json](../evals/results/phase4/http-restart.json)、[demo-http.json](../evals/results/phase4/demo-http.json)、[report.md](../evals/results/phase4/report.md)。历史Phase3原始报告保持不变，最终图测试与HTTP证据不替代在线模型语义评估。

## Phase 5 MCP 验收

`tests/unit/test_mcp_server.py` 使用官方 SDK 内存 transport，对 auth/tools 使用明确单元控制对象，验证 manifest、严格类型/额外字段、身份不来自参数、逐次认证、未知/写入工具拒绝、超时、取消、序列化错误及安全日志。客户端单元测试验证本机登录边界、配置白名单、工具发现/结果处理和有限生命周期；这些分组结果不冒充真实数据库或 subprocess 证明。

`tests/integration/test_mcp_authorization.py` 使用真实 PostgreSQL、AuthService 和 BusinessTools。覆盖客户归属、客服读取、分析员拒绝、角色变化、Token 撤销/用户禁用，以及真实 CPU 模型和 Chroma 的限定政策文档引用。官方内存 transport 只替换 OS 管道，没有替换认证或业务事实；管理员用于准备政策索引，不冒称另有管理员 MCP 读取验收。

`tests/e2e/test_mcp_stdio.py` 通过官方 stdio ClientSession 启动生产 Server 子进程，使用专用 enterprise_agent_test 和普通 ea_test_app、真实 CPU 模型/Chroma。验证三个真实读取、他人订单拒绝、参数/身份/写入工具拒绝、引用有效、stdout 只有 JSON-RPC、stderr 无秘密、退款/工单/审批数量不变，以及官方上下文退出后实际子进程返回 0。该测试不调用聊天模型，provider_token_usage=null，不能把本地模型推理或协议耗时改称模型调用/性能成绩。

本阶段原始结果使用 `var/test-results/phase5.json`、`phase5.xml`、`phase5-mcp-stdio.json`；[614 项完整测试通过](../evals/results/phase5/verification.json)，0 失败/错误/跳过、JUnit 548.312 秒、85 条 Chroma 弃用警告保留。[实际协议证据](../evals/results/phase5/mcp-stdio.json)与[报告](../evals/results/phase5/report.md)独立归档。[实际 Client Demo](../evals/results/phase5/mcp-demo.json)完成三个工具读取，政策 hybrid_rerank 返回三个引用，provider_calls=0 / token_usage=null；[主服务 HTTP](../evals/results/phase5/demo-http.json)记录 0.5.0 / phase=5 与五项就绪检查。不累计分组子集成新的测试总数。旧 Phase 3 检索对照与 Phase 4 在线轮次保持原 JSON，不作为本阶段新的质量指标。

## Phase 6 分析验收与评估协议

`test_analytics_sql.py` 覆盖授权单视图 SELECT、合法字段/函数/CAST、用户 LIMIT 与上限、畸形输入、非授权数据源、写操作、CTE/子查询/JOIN/UNION/任意函数等拒绝。schema 校验只保证严格参数，AST 白名单只保证执行能力，均不代替 SQL 是否回答了自然语言问题。

`test_analytics_views.py` 使用真实原始 Order、OrderItem、RefundRequest，在 Python 中独立计算上海支付日期、精确实付和完成退款 cohort，再比较实际视图行。另在回滚事务内准备支付跨午夜、取消/未支付、历史商品名称和退款状态变化。真实 reader 检查默认只读、角色属性、无成员关系和项目库隔离，并主动关闭默认只读，验证基表读取、业务 DML、DDL/TEMP 仍由数据库拒绝。测试只操作 enterprise_agent_test，不重置开发数据。

`test_analytics_runtime.py` 对实际专用连接池验证就绪拒绝、限额、超时、事务退出与后续可用；分析 API 与 `test_agent_analytics.py` 验证可信角色、实际查询结果和私有 Task / Checkpoint。受控 ChatModel 仅提供明确路线/SQL 或故障，数据库执行与报告行仍真实；受控 usage 不列为供应商消费。

固定 SQL 评估应记录每条输入 SQL、规范 SQL、实际行、独立事实基准、预期拒绝/成功、正确性检查和实际执行耗时。至少覆盖销售额、退款率、两个完整七天订单量、商品排行、零分母/空结果、未授权 SQL 和行数截断。角色/AST 安全通过率与业务查询正确率分开，不删失败项。直接 SQL 不调用聊天模型，供应商 Token 为 N/A，而不是假定存在模型调用却填零。

在线自然语言评估使用真实 DeepSeek、已有 Agent HTTP 会话和四类运营问题，保留模型实际计划、真实 SQL 结果、Task.steps、供应商 usage、耗时及失败。将生成结果与独立订单事实基准核对，不能仅用 SQL 能执行、答案 substring 或 AST 合法证明语义正确。未调用、无 Key、失败无 usage 的情况明确标记未验收/未知，不预填模型完成率。运行期间的可信日期与种子基准要保留，固定订单数据不会自动滚动到当前日期。

实际[SQL JSON](../evals/results/phase6/sql-results.json)及[SQL 报告](../evals/results/phase6/sql-report.md)已归档，source=human_authored_sql_fixtures、固定问题日期 2026-10-12 / Asia/Shanghai。**24/24 检查通过**，其中 16/16 查询与独立事实基准匹配、8/8 非法输入按预期拒绝。真实测试库为 500 订单、1016 明细、19 退款，执行前后 data_fingerprint 保持一致；模型调用 0，provider_token_usage=null。样例中的自然语言仅说明人工 SQL，不把 reference_match_rate / rejection_rate 当作自然语言规划质量或开放输入安全证明。逐条耗时受本机状态影响，不是性能基准。

对照使用实际固定数据集 [analytics-sql.json](../evals/datasets/analytics-sql.json)；在线开发集为 [analytics-live-smoke.json](../evals/datasets/analytics-live-smoke.json)。前者通过 `evaluate-analytics.ps1` 执行，后者要求 Python helper 的显式 `--allow-live`，完整命令与新输出目录约束见[部署](deployment.md)。

实际[在线 JSON](../evals/results/phase6/live-results.json)、[在线报告](../evals/results/phase6/live-report.md)与[逐场景 CSV](../evals/results/phase6/live-cases.csv)保留四个手选、带 schema 输出合同的开发检查，**4/4 通过**，controlled_model_only=false。执行通过本机 HTTP Agent 使用真实配置模型，每个场景与独立发起的 canonical 只读 API 请求逐列逐行比较；这些请求不共享数据库快照，期间业务日期未变化。8 次实际模型调用尝试、prompt 7427 / completion 1019 / total **8446 Token**，consumption_complete=true、未知/不完整任务数 0。操作者标签 deepseek-flash 不等于 DTO 暴露的真实供应商版本或身份；JSON 如实记录 provider_identity 未暴露。运行起止为 2026-10-13T20:09:51.113992Z–20:09:59.665929Z，仅是一次开发观察，不推导模型正确率、统计提升或性能基准。

本次在线烟测使用允许配置范围内的 `EA_AGENT_MAX_RETRIES=0`，运行后恢复项目默认 2 次有限重试，并在最终安全日志修复代码下重启主服务；不把该场景样例当作失败重试模型效果评估。真实[HTTP 快照](../evals/results/phase6/http.json)记录主服务 0.6.0 / phase=6、六项就绪和直接 SQL 返回 991510 分；其 model_calls=0 仅描述健康/直接查询，不能覆盖以上在线消费。历史 Phase 4 的失败轮次与消费保持原文件。

最终完整 `scripts/check.ps1` **883 项通过、0 失败/错误/跳过**，要求真实 PostgreSQL 与 RAG，JUnit 697.865 秒 / CLI 697.89 秒；Ruff lint、153 文件格式及 pip check 通过。85 条 Chroma legacy embedding function config 的 DeprecationWarning 未隐藏或修复。[完整摘要](../evals/results/phase6/verification.json)、[源码冻结记录](../evals/results/phase6/source-freeze.json)及[可读报告](../evals/results/phase6/report.md)已归档；143 个选定源码、脚本、测试、锁和数据集文件在本次完整检查前后哈希一致，记录不声称覆盖所有文档/部署文件。

首轮 878/879 的唯一失败为旧 JWT fixture 在 collection 时计算未来签发时间，长套件后该时间不再在未来；仅把测试改为执行时计算，生产认证规则未放宽。随后修复规范化 SQL 长度验证与宽数值图表首列遗漏并新增四个回归；122 项聚焦检查为最终套件子集，不叠加为新总数。详情见[阶段记录](phase-6.md)。原始失败记录、历史检索 A/B 和在线失败轮次保留。

上述在线四场景发生在最终两项边界修复前；最终代码以实际归档数据和最终 renderer 离线复核四个报告/参考结果，4/4 保持一致、数据集与 evaluator hash 匹配，新增模型调用 0。这是归档结果校验，不是第二次在线模型评估，记录保存在完整摘要的 archived_live_recheck。

## Phase 7 前端验收协议

前端检查与已有后端/检索/SQL评估分开归档。严格 TypeScript、Vite 构建、Vitest 单元和组件检查覆盖金额精度、认证/身份变化、Markdown 安全、引用和明确确认。组件注入受控 API 响应时，只证明 UI 行为，不当作数据库或线上模型事实。

真实浏览器流程连接本机实际 FastAPI，注明数据库和模型是否受控。覆盖登录、五页导航、权限拒绝、知识检索/管理、直接只读分析、任务状态、敏感操作核对及错误/request ID。实际截图只反映对应执行，不能替代在线模型任务完成率。最终数量、构建与截图证据见[Phase 7](phase-7.md)和[验证报告](../evals/results/phase7/report.md)。

实际结果为 **43 项前端单元/组件测试、21 项本阶段 API 回归，以及真实 Edge 一条串行全流程的 14 个检查点通过**。浏览器使用生产构建、独立测试业务数据库/reader、真实 CPU Embedding/Reranker、Chroma 和配置聊天模型；没有用 API Mock 或受控 ChatModel 替换该流程。单元/组件测试的受控响应仍单独标记，不列为在线模型证据。本阶段没有重跑历史 883 项完整后端套件。

通过流程的三个 Task 为引导式开发案例：退款准备与跨重启显式确认、知识原文与当前引用核验、带 schema/输出约束的自然语言分析。四个模板及自然语言 SQL 的实际行与另行发起的 canonical API 对照；不共享数据库快照，不等同独立基表事实参考，不能把“API 结果一致”解释成 NL SQL 盲评正确率。没有根据一次流程耗时推导性能或负载结论。

保留全部浏览器轮次：首次登录选择器失败未调用模型；多意图退款提示仅查询政策、未准备审批（5516 Token）；更明确的退款请求生成待审批，但测试点击隐藏 checkbox 失败（5232 Token）；修正测试定位后读取此前持久任务，完成后续业务检查。多意图业务期望失败仍属于真实模型限制，本阶段没有改业务路由掩盖它。

最终通过流程按三个 Task 的最新 DTO 统计七次累计模型尝试、prompt 8129 / completion 715 / total **8844 Token**，包含复用任务此前的 **5232 Token**，当轮新增仅 **3612 Token**。所有轮次按 Task ID 去重共四个 Task、十次尝试、prompt 13129 / completion 1231 / total **14360 Token**；unknown_usage_calls=0，实际已调用任务的 usage 完整。重复 resume DTO 替换原快照，不再相加；首轮未提交模型消息不伪填模型 usage。Task DTO 未暴露供应商具体身份/版本，配置名称不能代替响应身份验证。

另行执行的 `verify-frontend.ps1 -ScreenshotsOnly` 已通过，八张实际页面截图及浏览器版本记录见[screenshots.json](../evals/results/phase7/screenshots.json)。测试 API 模型关闭，不 seed/索引或创建任务，只读取完整流程留下的持久任务，允许登录和授权只读 SQL；messages、resume、recover、business_mutations 均为零。八次捕获分别检查 1280/1366/1440 宽度，页面无横向溢出；这不是新一轮模型任务评价或 Token 消费。`approval.png` 为历史提交回执，`citation.png` 为当前来源核验，不能把前者当作当前待确认状态或最新退款审核状态。

前端检查可以不调用 DeepSeek；本地 SVG/Markdown 渲染和 SQL 直接执行不计入 Token 消耗。新增在线调用必须单独记录 actual usage、未知消费和失败轮次，历史 Phase 4/6 文件不覆盖或重记为本阶段。

## 后续评估

真实在线烟测独立归档于[phase4-live报告](../evals/results/phase4-live/report.md)、[最终JSON](../evals/results/phase4-live/results.json)与[CSV](../evals/results/phase4-live/results.csv)。最终5场景检查5/5通过，其中越权按预期任务failed；12次调用、prompt11315/completion875/total12190 Token，无未知消费。首探针和4/5、3/5、4/5失败轮均保留，已知累计45443 Token/46次调用；不得仅列最终消费。模型标签为操作者提供，非供应商响应版本名。

5个问题手选用于开发修复，所有轮次均非盲评，不能推导统计提升或泛化质量。检查器新增区分检索不足与候选拒答，旧三轮仍有真实失败；RAG阈值未变。实际源引用用HTTP验证，耗时仅本次观察，不是性能基准，Token是供应商实际报告的已知用量。在线运行需脚本显式--allow-live；当前天气问题拒答、七天政策等少样本观察不替代独立领域集。

Phase 8 新增的 Agent validation 问题在首次在线运行前冻结并与 development、历史 regression 分开。它们由开发助手已知项目代码/语料编写，不能称独立外部标注或盲评。政策冲突识别、开放式语义相关性和泛化效果仍需要新数据与人工复核，不能仅靠 schema 或 substring 匹配证明。

线上模型缺 Key 或未经实际调用时明确标记未运行；Token 从实际 API usage 获取。优化没有改善也保留原始结果，不预填分数、伪造压测或部署成绩。

## Phase 8 Agent 评估合同

[agent-evaluation.json](../evals/datasets/agent-evaluation.json) 包含 18 条自编案例，development=3、regression=2、validation=13。覆盖订单读取/归属、退款准备、政策知识/无证据拒答、四类运营分析、分析授权、禁止 SQL 和领域外问题。regression 保留既有多意图退款及引导式 SQL 开发场景；validation 是本阶段新写的自然语言，并在首次执行前冻结 SHA256，未在跑后调题或改期望。编写者不是外部标注员，validation 不据此称人工盲评。

| 检查 | 证据与口径 |
| --- | --- |
| Task 合同 | 实际 TaskResponse 严格结构、intent/status/error_code；按案例预期，拒绝可以是预期成功 |
| 工具选择 | 实际 facts/steps/采集工具调用中的必需与禁止工具；completed 不能掩盖未完成需求 |
| 工具参数 | 实际采集原始模型参数时调用现有 BusinessTools schema；DTO 只能证明暴露部分，证据缺失标复核 |
| 订单事实 | 另行授权查询的 Service/API 事实与工具结果精确比较，绑定可信本机测试订单 |
| 人工审批 | 必须停在 pending 预览，订单/原因/金额/政策与独立资格事实一致；评估不 confirm/resume |
| 业务不变 | 运行前后真实退款 ID、工单 ID、订单状态一致；会话、任务与待确认预览登记属于评估操作 |
| 当前引用 | 每个 Chunk UUID/固定 citation URL、文档/版本/代数/文本与真实认证来源记录一致 |
| 结构忠实度 | 所有渲染陈述都是各自已验证来源中的连续原文，无额外自由文本事实 |
| 语义相关性 | 原文是否回答问题、是否足以解释政策须人工标签；未执行复核保留 `manual_review` |
| SQL 结果 | 生成 SQL 实际行与独立基表事实结果精确 Decimal/NULL 比较，不用相同 API 自对照当独立证据 |
| SQL 口径 | 指标 IDs、Shanghai 日期边界、cohort 分母/聚合、排序/数量与保守 AST 合同；未知等价表达式保留复核 |
| 分析报告 | 原始 query、facts、答案、指标定义和 chart 与确定性 renderer 一致，不推断原因 |

`checks.py` 对每项给出 `pass` / `fail` / `manual_review`。案例任一机器项失败为 fail；没有失败但有未决复核为 manual_review；只有全部已通过才为 pass。报告必须分别列机器检查通过、待复核与失败，不能把 pending 语义标签算成已完成质量成绩。Tool schema 和引用原文连续性只是各自结构合同，不推导工具意图或答案语义必然正确。

运行前冻结数据集和选定源码哈希，保留可信日期、种子基准、业务快照、真实任务/步骤、实际 usage、执行时间和错误。模型消费按 Task ID 选择最新累计值，失败和未知消费保留；同一任务的 resume/recover DTO 不重复计账。默认不开在线执行，显式允许后才通过专用 loopback 测试 API 调用供应商；没有 Key 或未运行不预填指标。

统一入口 `scripts/evaluate-phase8.ps1` 默认是 `-Mode Boundaries`；`-Mode Prepare` 准备已迁移的专用测试库与真实索引，不调用聊天供应商；`-Mode Live -AllowLive [-Prepared]` 启动隔离 API 并运行冻结案例。`-Prepared` 要求已有 `preparation.json` 与数据隔离条件有效，不自动清库。Live 产物保存在新的 `var/evaluation/phase8/live/<UTC>`，保留 `results.json`、`cases.csv`、`report.md`、运行日志及单独忽略的 JWT 秘密。模块入口为 `python -m enterprise_agent.evaluations.live`，运行参数见[部署](deployment.md)。

每条消息付费调用前，先创建会话并用专用测试库独立查询确认 session 存在；HTTP 客户端只接受 loopback、不跟随重定向/环境代理，操作白名单禁止确认/恢复。请求返回未知时只查询已持久 Task，不自动重发消息。退出码 0 表示基础设施与记录完成，报告仍可有 fail/manual_review；不是线上质量全部通过的 gate。

源码冻结覆盖 `src/enterprise_agent/**/*.py`、两份 Agent 数据集、pyproject 与 Python base/dev 哈希锁，不声称覆盖全部 tests、frontend、PowerShell wrapper、秘密配置或第三方 SDK 源码。独立 SQL 参考连接仅在只读事务内使用，评估 API 子进程不继承 owner/reference 凭据；运行期间可信日期、基表事实与选定源码稳定性另行校验。

## Phase 8 受控故障与追踪验收

[agent-boundaries.json](../evals/datasets/agent-boundaries.json) 的 16 条声明故障案例直接调用生产 `build_graph` 与真实参数校验，使用显式 ChatModel/Service fixture 及 InMemorySaver。覆盖额外字段/身份注入、非法订单参数、工具与步数预算、临时故障重试、无 usage 未知消费、非临时错误终止、人工 interrupt/replay 和拒绝/重放边界。实际报告标明 `evaluation_kind=controlled_fault_injection`，provider requests 不适用；`fixture_usage` 只检查计账，绝不加入真实 DeepSeek Token 或任务质量分数。数据库授权和 PostgreSQL Checkpoint 仍由独立集成测试证明。

追踪测试核对真实 JSONL 序列化、严格白名单/秘密脱敏、累计快照与 unknown usage、关闭开关、错误隔离、应用 lifecycle 和官方 LangSmith SDK 受控 requests transport。SDK 构造/序列化测试不能当作真实云端网络发送；没有凭据时明确“未云验收”。步骤 span 为执行后累计快照，无真实起止时序，trace 性能也不作为 API/模型负载指标。

本阶段 RAG 两模式和 24 条人工 SQL 参考可重新执行，但既有案例仍属于开发已知集；历史 Phase 3/6 产物保持原文件，不能重跑后冒称新盲测集。真实数量、失败、复核状态与消费在[Phase 8](phase-8.md)按最终执行结果登记。

## Phase 8 本次真实执行结果

[完整后端记录](../evals/results/phase8/python-regression.json)为 **1034/1034 通过**、0 failure/error/skipped，JUnit 712.590 秒，要求实际 PostgreSQL 与 RAG，87 条 Chroma 弃用警告保留。完整套件之后评估器 SQL 商品参照修正未投影 ID 的列角色，[104 项定向复验](../evals/results/phase8/evaluation-oracle-unit.json)通过、JUnit 2.909 秒；在线运行后再修正物流选样，最终四文件[112 项通过](../evals/results/phase8/evaluation-unit.json)、JUnit 2.898 秒（CLI 2.92 秒），包含新增八个选样边界。完整套件没有再跑，子集不能相加或当作最终源码的全量复验；Ruff、176 文件格式和 pip check 通过。最终修复只涉及评估 references.py/live.py，源范围比对见[验收报告](../evals/results/phase8/report.md)。

前端本阶段重跑 **43 项/6 文件**，类型、Vite 构建和生产 audit 通过；构建 516 ms 是一次观察，UI chunk 984.49 kB/gzip 316.59 kB 的 >500 kB 警告保留。本阶段没有新一轮五页付费浏览器流程或负载基准，Phase 7 的浏览器证据仍为历史记录。

[本次 RAG 产物](../evals/results/phase8/rag/report.md)为 30 问题 × 两模式的 60 条真实记录；test 17 可回答/3 无证据，dev 8/2 单列。源码、数据集、模型 manifest 和语料前后稳定。报告模板标题仍为 Phase 3，实际 UTC 为 2026-10-14，本次原件单独归档，不复用历史成绩。

| test 指标 | Vector | Hybrid + Rerank |
| --- | ---: | ---: |
| Recall@3 | 0.970588 | 1.000000 |
| nDCG@5 | 0.977244 | 1.000000 |
| 检索错误 | 0 | 0 |
| 无证据拒绝 | 3/3 | 3/3 |
| 引用结构有效率 | 100% | 100% |
| mean / P95 ms | 38.298 / 40.979 | 966.886 / 1073.442 |

300 个候选 Chunk 经过实际来源版本/原文/位置/权限核验。这个已经参与历史 QA 的自编语料集不能称新盲评；本次文档级增益同时增加时延，无法推导开放式语义或高吞吐性能。供应商调用 0，本地 Embedding/Reranker Token 账单 N/A。

[SQL 产物](../evals/results/phase8/sql/report.md)为 **24/24**，16 个人工查询与独立基表事实一致、8 个按预期拒绝，500 订单/1016 明细/19 退款指纹稳定；固定问题日期 2026-10-12，与在线可信当前日期分开。没有聊天模型调用，不能称 NL SQL 24/24。

[受控故障](../evals/results/phase8/boundaries/report.md) **16/16 声明期望符合**，34 fixture 模型调用、222 个已知 fixture Token、5 次 fixture 未知消费用于软件计账边界。实际供应商无请求，provider usage N/A；这既不是数据库权限总分，也不是模型任务完成率。

首次[真实在线冻结运行](../evals/results/phase8/live/report.md)保留全部 18 条：

| 分组 | 总数 | pass | fail | manual_review | unobserved |
| --- | ---: | ---: | ---: | ---: | ---: |
| development | 3 | 1 | 0 | 1 | 1 |
| regression | 2 | 0 | 1 | 1 | 0 |
| validation | 13 | 4 | 2 | 7 | 0 |
| 合计 | 18 | 5 | 3 | 9 | 1 |

17 个实际 Task、36 次实际模型调用尝试，prompt 35626 / completion 3814 / total **39440 Token**，usage_complete=true；未观察首物流案例在发消息前因独立物流参考 404 停止，没有付费消息。源码与业务基表指纹在该次在线运行期间稳定，未确认退款或修改订单状态。之后 live.py 只修正后续选样：最多 100 个拥有订单验证真实物流 200、404 跳过、其他错误立即失败。该修复有专项测试，没有新模型调用、改题或改原始成绩，首次观察不冒称最终源码重新在线验收。

复杂退款 regression 未调用 create_refund_request/准备审批，优惠券 validation 规划无效，两项真实模型失败保留。退款率 validation 返回四舍五入 ratio `0.02`，冻结参照为 percent `2.33`，但题目“比例保留两位”存在单位歧义；该冻结检查仍为 fail，不能直接断言数据库事实错误，也未事后换口径调分。周比较实际两行的数值 30/13 与单行参考一致，保守 judge 无法映射同义形状，仍为 manual_review，不按机器通过处理。

原始模型 JSON 没有通过 Task 公共投影完整暴露，引用回答相关性没有独立人工标签，这些复核项维持未知。上线 Task completed 不等于需求/语义全部通过，本次不报告人工未完成的语义正确率或推广完成率。失败、待复核、未观察和消费原件均保留，离线诊断不改分。

[真实本地 trace 核验](../evals/results/phase8/observability/verification.json)观察到 17 Task/17 快照，request hash、状态、安全错误和累计 usage 均与已采集 Task DTO 一致，严格白名单元数据通过；39440 Token/36 次调用与同一在线账本相同，不重复记消费。LangSmith 配置关闭且没有云端凭据，未发送真实云 trace 的配置边界与 SDK 受控 transport 检查分别记录；没有网络抓包或真实远端连通证明。


## 已归档的真实对照

初轮 hybrid_rerank 的 test 无证据拒绝率为 2/3，`test-18` 被单独重排门槛放行。[初轮报告](../evals/results/phase3-initial/report.md) 与 JSON/CSV 完整保留。查看 test 结果后，把所有模式的向量门槛与 hybrid_rerank 的重排门槛合并为 AND，原 0.45/0.05 数值保持不变。

**test参与了QA修正，因此修正后的结果不能称为盲评或未参与开发的保留成绩。** 两次结果保留以便审查，泛化评估需新独立问题。

源码冻结后的最终运行（开始于 2026-10-13T02:28:23 UTC），真实 DB seed 为 2026-10-12：

| test 模式 | 可回答/无证据 | R@1 | R@3 | R@5 | MRR | nDCG@5 | 无证据拒绝 | 引用有效 | mean/P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vector | 17/3 | 0.941 | 1 | 1 | 1 | 1 | 3/3 | 100% | 38.529/42.376 | 0 |
| hybrid_rerank | 17/3 | 0.941 | 1 | 1 | 1 | 1 | 3/3 | 100% | 1008.561/1136.540 | 0 |

这是一次真实Service查询时间的观察，不是HTTP或生成答案性能。该集上混合重排没有改善文档级召回/排序，延迟更高；没有据此宣称优化有效。LLM实际调用及输入/输出Token均为0，本地模型计费usage为N/A。

dev 8/2 单独统计：两组 R@1=0.812、R@3/5=1、MRR=0.938、nDCG@5=0.954；vector mean/P95 37.390/39.510 ms，hybrid_rerank 990.552/1067.304 ms。最终运行没有并行本项目 pytest，但没有控制全机负载，不能称为严格性能基准。

最终 [报告](../evals/results/phase3/report.md)、[JSON](../evals/results/phase3/results.json)、[指标 CSV](../evals/results/phase3/metrics.csv)、[逐查询 CSV](../evals/results/phase3/queries.csv) 已归档。运行结束源码、数据与 manifest hash 稳定；完整测试及真实 HTTP 证据分别见 [verification.json](../evals/results/phase3/verification.json) 与 [http.json](../evals/results/phase3/http.json)。评估时 Git 为 Phase 2 提交且 dirty=true，准确反映 Phase 3 尚未提交的工作区；不回写为后续提交或伪造干净状态。
