# Phase 4：LangGraph Agent 与人工审批

当前版本0.4.0：初验511项工程检查与真实TCP重启通过；后续真实DeepSeek手选烟测最终5/5检查通过。在线修复后110项核心测试通过，最终冻结源码[517项完整复验通过](../evals/results/phase4-live/verification.json)。受控图测试、真实在线烟测和完整模型质量评估分开；5场景不足以证明模型泛化。

## 已写入的功能

- 单 LangGraph 图：Intent Router、知识检索、一次查询改写、业务 planner / Tool Execution、人工中断、提交、回答和验证。
- HTTPX DeepSeek OpenAI 兼容接口：Tool Call JSON、白名单、finish_reason、usage、有限响应和单请求超时。默认 deepseek-flash、Thinking disabled、非流式；EA_LLM_ENABLED=false。
- 七个 Pydantic 工具复用真实 Service；模型不能提供身份、金额、确认、审批编号、幂等键或任务绑定。
- 知识回答选择连续原文，再检查来源文本、当前索引与授权；订单报告由真实结构化结果构造，不采用模型自由文本。
- 官方 PostgreSQL Checkpoint，多轮历史、私有 thread、会话/任务归属、跨 worker advisory lock、显式 recover。
- 服务器审批摘要、task_id 幂等准备、人工确认/拒绝、固定任务幂等键、同事务退款/工单/审计；当前只创建待客服审核申请。
- 步数、工具次数、有限临时重试和超时；敏感操作预留后续步骤，提交后陈旧引用保留真实结果并列 warnings。
- 0003_agent 管理官方 schema/版本与受限权限，应用不 setup；0004_agent_role 绑定不可变创建角色，角色变化需要新会话。

## 工程与接口

关键代码位于 agents/{graph,llm,checkpoint}.py、tools/business.py、services/agent.py、repositories/agent.py、schemas/agent.py 与 api/routes/agent.py。业务规则沿用 RefundService，知识索引和检索沿用 KnowledgeService。

新增 /api/v1/agent/sessions 与 /tasks 查询；会话 messages 执行，任务 resume 显式确认/拒绝，recover 核对持久状态。空请求入口拒绝伪造字段；Agent JSON POST 有 16 KiB 上限。任务结果提供 answer、approval、refund_result、citations、facts、warning_codes、steps、usage、错误和耗时，HTTP成功不等于任务成功。

运行需按部署文档迁移已有本地数据库后重启。没有 Key 时会话历史接口可用，发消息或执行恢复明确返回模型未配置；健康检查不发送 DeepSeek 请求。Key 仅放本机忽略配置，模型、缓存、Docker数据继续使用 D 盘。

## 验收状态

最终运行命令：

```powershell
.\scripts\migrate.ps1
.\scripts\migrate.ps1 -TestDatabase
.\scripts\migrate.ps1 -Check
.\scripts\check.ps1
```

check默认--require-postgres --require-rag。初验511项全部通过，失败/错误/跳过均为0，JUnit elapsed_seconds=524.068、CLI524.08秒，摘要生成于2026-10-13T09:48:22.2579476Z，原始JSON保持不变。Ruff114文件、pip check、Alembic check与head=0004_agent_role已通过。后续在线修复改变了生产源码，110项核心回归通过；最终冻结源码[517项完整复验通过](../evals/results/phase4-live/verification.json)，不将初验或修改期间的中间通过记录代替新代码验收。

81条未屏蔽DeprecationWarning来自Chroma官方CollectionCommon.py:155的legacy embedding配置路径，不是测试失败。固定客户端/服务端1.5.9，升级时需继续验证超时兼容桥、集合配置与真实检索；没有据此宣称已验证后续SDK版本。套件524.068秒是测试执行时间，不是Agent/API性能或在线模型耗时。

首轮完整套件实测507项通过、1项失败、81条弃用warning、520.92秒。失败是旧readiness完整字典少新增agent_checkpoint项；补充正确新项且保留完整断言。后续补齐跨recover未知usage标记保留、物流真实at/status/note展示及3项权限边界测试，再执行最终完整检查。首轮未改称通过，也没有拿修改期间的中间运行作为最终冻结验收。

以下是此前独立分组预检的实测，均为完整套件子集；时间保留原观察，不额外累加到最终511项：

| 检查 | 实际结果 | 耗时 |
| --- | --- | --- |
| Phase 4 新增单元契约/协议/状态边界 | 161项通过 | 1.72秒 |
| 新增 PostgreSQL、真实 CPU RAG、Agent API | 37项通过 | 146.78秒 |
| Uvicorn TCP、真实进程重启与确认 | 1项通过 | 21.42秒 |
| Ruff lint/格式 | 114个Python文件通过 | 不作为性能指标 |
| 开发库/专用测试库迁移 | head=0004_agent_role | 实际执行完成 |
| Alembic check | No new upgrade operations detected | 模型/迁移一致 |

workflow 集成测试使用真实 PostgreSQL、CPU Embedding/Reranker、Chroma、适用政策索引、官方 LangGraph 与 Checkpoint；ScriptedChatModel 仅选择受控路线。包含审批前零退款、重启确认、重复决定、拒绝、权限、会话忙、准备/提交后故障恢复、参数/次数/步数限制、故障usage保留、多轮历史和跨审批重索引。测试替身与控制usage不用于在线模型正确率、消费或性能成绩。

故障恢复测试在真实业务事务返回后注入 CancelledError 模拟 worker loss；SDK 包装为 NodeCancelledError 后任务记录可恢复失败，恢复重放原记录。它不代表 OS 进程恰在事务提交瞬间被终止的实测。另有独立真实进程测试，在 waiting_approval 暂停期停止已验证身份的 Uvicorn 进程并确认端口关闭，再启动新的进程、人工确认、重复确认和检查越权404：最终只有一条待审核退款、一条工单与对应审计。

最终TCP报告时间为2026-10-13T09:39:58.257469+00:00，对应独立重启测试20.819秒，明确controlled_model_only=true、online_deepseek_tested=false、provider_token_usage=null。不要把前述初次21.42秒预检或最终20.819秒当成模型/服务查询性能。

启用聊天模型前的主HTTP初验时间为2026-10-13T09:40:47.632514+00:00：0.4.0/phase4五项健康通过，未登录401、创建会话201、其他客户/管理员404、伪造角色422、缺模型503且零新增任务。该历史JSON的onlinefalse不回写；随后本机模型启用与在线烟测单独记录。

公开归档：[完整检查摘要](../evals/results/phase4/verification.json)、[TCP重启证据](../evals/results/phase4/http-restart.json)、[主服务HTTP证据](../evals/results/phase4/demo-http.json)与[可读报告](../evals/results/phase4/report.md)。JSON记录实际时间、依赖和验证边界，不包含秘密或伪造模型成绩。

历史 Phase 3 的30问题检索对照及较差初轮完整保留于 evals/results/phase3*，不回写为新源码或在线 Agent 分数。本阶段没有重新声称 RAG 优化指标改善，也未虚构 DeepSeek 任务成功率。

## 真实在线补充

在线修复后的最终冻结源码完整复验：517项通过、零失败/错误/跳过，require-postgres与require-rag均启用；JUnit527.975秒（CLI528.04秒），摘要生成于2026-10-13T14:44:22.6518030Z。Ruff121文件、格式、pip check通过；81条Chroma旧弃用warning未屏蔽。[新验证摘要](../evals/results/phase4-live/verification.json)与[源码冻结记录](../evals/results/phase4-live/source-freeze.json)单独归档，5个生产源文件hash在测试期间未变；初验511摘要和日期不改。

脚本scripts/evaluate-agent-live.py必须显式--allow-live，访问本机API，由普通后端调用真实模型；没有受控模型替代。最终运行于2026-10-13T14:29:42–14:30:00 UTC，手选物流明确问、物流追问、七天政策、企业资料没有天气预报、他人订单5场景检查5/5通过：前4个任务completed，最后一个按预期failed/order_not_found。所有返回引用经实际HTTP核验。标识deepseek-flash来自操作者配置标签，不冒称供应商返回的版本名。

最终一轮12次模型调用，实际报告prompt11315、completion875、total12190 Token，无未知使用量；Task.elapsed_ms分别3299、3026、4188、4128、1407。首探针3672 Token/3次调用与此前失败轮4/5（8780/10）、3/5（10592/11）、4/5（10209/10）均保留；所有已知在线消费累计45443 Token、46次调用，不能只报告最终一轮消费。

在线问题促成三个控制工具named tool_choice、Router解析历史而planner仅接收当前original_request/resolved_query、排除仅标题候选、quotes允许空列表拒答。未改RAG阈值。检查器区分原检索不足和候选语义拒答，首三轮失败在新标准下仍失败。样本手选并用于修复，不是盲评、统计提升或性能基准；数据集SHA为414262ebfdafafe8717dfe283a1924a8b5c67f0c72210c994652139fc7e60ab7。

原始证据：[首探针](../evals/results/phase4-live/first-probe.json)、[初轮](../evals/results/phase4-live/initial-results.json)、[控制修复轮](../evals/results/phase4-live/control-fix-results.json)、[上下文修复轮](../evals/results/phase4-live/context-fix-results.json)、[最终JSON](../evals/results/phase4-live/results.json)、[最终CSV](../evals/results/phase4-live/results.csv)、[报告](../evals/results/phase4-live/report.md)。受控退款/工单重启证据仍属于此前初验，当前5场景没有验证在线退款创建质量。

## 限制与未验收项

- DeepSeek仅有手选5场景在线烟测；没有独立盲评、完整质量/负载评估，也没有在线退款创建质量结论。
- 原文抽取防止改写政策文字，不能证明引用语义充分或检测所有政策冲突；结构化退款规则始终是执行权威。
- 图随请求运行，人工等待持久化；没有流式对话 UI、后台 Worker、自动任务重领或自动恢复调度。
- 同会话串行，角色变化要求新会话，恢复原相同角色可访问原合法会话。
- 官方 SDK schema/serializer固定版本，升级必须同步审阅迁移、权限与恢复测试。
- 没有 MCP、NL SQL、Vue、Langfuse、应用镜像、全栈 Compose、CI、公网或真实支付退款。

## 下一阶段

Phase 5 使用官方 MCP Python SDK，提供独立 Server、Client 工具发现和调用示例，复用既有 OrderService / RefundService / KnowledgeService。先暴露只读订单、物流与政策工具；参数、可信身份、错误及协议边界有对应测试，退款确认继续使用当前可信后端工作流。完成实际调用、阶段检查、文档和本地 Git Commit 后再进入只读运营分析。
