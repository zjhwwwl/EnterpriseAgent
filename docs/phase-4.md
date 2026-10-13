# Phase 4：LangGraph Agent 与人工审批

当前版本为0.4.0，完整工程检查与真实TCP进程重启验收已完成：**511项通过、零失败/错误/跳过。DeepSeek在线模型效果尚未验收。** 受控模型脚本只验证流程，不替代在线模型。

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

check 默认 --require-postgres --require-rag，生成var/test-results/phase4.xml与phase4.json，执行Ruff lint/格式、pytest和pip check。最终511项全部通过，失败/错误/跳过均为0，JUnit elapsed_seconds=524.068，pytest CLI显示524.08秒；验证摘要生成于2026-10-13T09:48:22.2579476Z。Ruff114个Python文件和pip check通过，开发库与专用测试库head=0004_agent_role，Alembic check无新增升级操作。

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

主服务最终HTTP于2026-10-13T09:40:47.632514+00:00完成：127.0.0.1:8000运行0.4.0/phase4，application/database/redis/knowledge/agent_checkpoint五项健康通过。未登录401，所属客户创建会话201，其他客户及管理员越权404，伪造角色422，缺模型配置发消息503且新增任务数0。主进程使用真实配置，没有内置受控模型回退；这证明HTTP与本地依赖边界，不证明在线模型效果。

公开归档：[完整检查摘要](../evals/results/phase4/verification.json)、[TCP重启证据](../evals/results/phase4/http-restart.json)、[主服务HTTP证据](../evals/results/phase4/demo-http.json)与[可读报告](../evals/results/phase4/report.md)。JSON记录实际时间、依赖和验证边界，不包含秘密或伪造模型成绩。

历史 Phase 3 的30问题检索对照及较差初轮完整保留于 evals/results/phase3*，不回写为新源码或在线 Agent 分数。本阶段没有重新声称 RAG 优化指标改善，也未虚构 DeepSeek 任务成功率。

## 限制与未验收项

- DeepSeek 在线意图、工具参数、引用选择质量以及真实 Token/耗时，需要有效 Key 后独立实际验收。
- 原文抽取防止改写政策文字，不能证明引用语义充分或检测所有政策冲突；结构化退款规则始终是执行权威。
- 图随请求运行，人工等待持久化；没有流式对话 UI、后台 Worker、自动任务重领或自动恢复调度。
- 同会话串行，角色变化要求新会话，恢复原相同角色可访问原合法会话。
- 官方 SDK schema/serializer固定版本，升级必须同步审阅迁移、权限与恢复测试。
- 没有 MCP、NL SQL、Vue、Langfuse、应用镜像、全栈 Compose、CI、公网或真实支付退款。

## 下一阶段

Phase 5 使用官方 MCP Python SDK，提供独立 Server、Client 工具发现和调用示例，复用既有 OrderService / RefundService / KnowledgeService。先暴露只读订单、物流与政策工具；参数、可信身份、错误及协议边界有对应测试，退款确认继续使用当前可信后端工作流。完成实际调用、阶段检查、文档和本地 Git Commit 后再进入只读运营分析。
