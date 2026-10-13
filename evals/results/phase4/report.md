# Phase 4 实测验收报告

本阶段交付 EnterpriseAgent 0.4.0 的单 LangGraph Agent、七个业务工具、企业知识问答、人工确认、PostgreSQL Checkpoint 和显式故障恢复。使用模拟电商数据，没有真实支付或退款。

## 最终完整检查

运行命令为 `scripts/check.ps1`，默认强制 `--require-postgres --require-rag`。记录生成于 **2026-10-13 09:48:22 UTC**，原始摘要见 [verification.json](verification.json)。

| 检查 | 实际结果 |
| --- | --- |
| pytest | 511 通过，0 失败、0 错误、0 跳过 |
| JUnit 套件时间 | 524.068 秒；CLI 显示 524.08 秒 |
| Ruff lint / format | 通过，114 个 Python 文件已符合格式 |
| pip check | No broken requirements found |
| PostgreSQL / RAG | 两项均为必需，没有排除或环境缺失跳过 |
| 迁移一致性 | `0004_agent_role`；Alembic check: No new upgrade operations detected |
| PowerShell 语法 | 11 个脚本，0 语法错误 |

存在 **81 条第三方 Chroma SDK DeprecationWarning**：`CollectionCommon.py:155` 的 `legacy embedding function config`。没有屏蔽这些警告；当前实际检索与索引测试通过，但依赖升级仍需复查兼容性。套件耗时包含模拟数据生成、索引和真实进程启动，不能称为 API 响应时间或模型性能基准。

完整 JUnit 位于本机忽略目录 `var/test-results/phase4.xml`，可用上述命令重新生成。Git 中保存小型摘要，不提交私有配置、模型、完整运行日志或数据库数据。

## 实际运行边界

测试使用 Windows/Python 3.13.15，真实 PostgreSQL 17 普通运行账号、官方 PostgreSQL Checkpoint saver、Chroma HTTP，以及已经校验的本地中文 BGE Embedding 和量化 Reranker CPU 推理。LangGraph 图实际执行；测试脚本模型只用于控制路线、参数和故障时点。

- 业务、API 和图测试验证订单归属、会话创建角色、参数白名单、审批前零退款、明确确认/拒绝、幂等、审计、多轮历史、有限重试、执行额度与错误终止。
- 新增真实授权回归验证：撤销 `knowledge_chunks` 的 SELECT，以及授予 Checkpoint 版本表列级 INSERT/UPDATE，均导致对应 readiness 不通过；每个用例恢复专用测试账号权限。
- 业务事务返回后注入 `CancelledError`，由 SDK 包装为 `NodeCancelledError`，验证审批与退款恢复重放、真实记录不重复，以及未知消费标记跨恢复保留。这不是操作系统恰在事务提交瞬间终止进程的实验。
- 知识来源验证包含当前授权、索引代数与引用原文；提交成功后引用失效不会抹去实际已创建的退款结果。

受控模型返回的测试 usage 元数据不能作为真实 Token 账单或模型质量成绩。**没有在线 DeepSeek 调用结果；实际供应商 Token 消耗记录为 null，未测，不填零。**

## 真实 HTTP 与进程重启

[http-restart.json](http-restart.json) 记录于 **2026-10-13 09:39:58 UTC**。完整套件内此项测试耗时 20.819 秒：

1. 真实 Uvicorn TCP 请求进入 `waiting_approval`，数据库中退款与工单均为零。
2. 停止该测试自己启动的进程树，确认原端口关闭。
3. 启动新的进程，使用 PostgreSQL Checkpoint 恢复并人工确认。
4. 最终只有一条待审核退款、一条工单及对应审计；重复确认返回同一退款，其他客户读取返回 404。

此路径仍使用明确标注的受控路线模型，JSON 包含 `controlled_model_only=true`、`online_deepseek_tested=false`、`provider_token_usage=null`。

[demo-http.json](demo-http.json) 记录于 **2026-10-13 09:40:47 UTC**，对应最终源码重启后的本地主服务 `http://127.0.0.1:8000`：

| 检查 | 实际结果 |
| --- | --- |
| 服务版本 | 0.4.0 |
| readiness | application / database / redis / knowledge / agent_checkpoint 全部 ok |
| 匿名读取会话 | 401 |
| 当前用户创建会话 | 201 |
| 其他客户、管理员读取该会话及其任务列表 | 404 |
| 伪造会话创建角色 | 422 |
| 模型未配置时发送消息 | 503 / agent_model_not_configured；新增任务数为零 |
| OpenAPI | 包含 resume 和 recover 接口 |

健康与 HTTP 检查没有验证在线 DeepSeek。本机未配置 API Key，模型功能保留关闭状态；会话与历史查询可用。

## 首轮发现与修正

首轮完整检查为 507 通过、1 失败，耗时 520.92 秒。失败是原数据库 readiness 测试的完整字典未包含新增的 `agent_checkpoint` 检查；更新预期后继续保留严格字典断言。随后复查补齐列级权限与知识 Chunk 读取检查，保留跨恢复未知消费标记，并按真实物流事件 `at/status/note` 展示时间与说明。

最终在这些修改之后重新启动完整检查，产生本目录 511 项通过的摘要。分组运行是套件的子集，不能额外累加为测试数量或在线 Agent 任务成功率。

## 未验收项与下一阶段

DeepSeek 在线意图判断、工具参数质量、引用语义充分性、实际供应商 Token 和模型响应耗时仍需有效本机 Key 后独立评估。原文抽取校验不能证明所有引用在语义上充分，也不能检测全部政策冲突。退款执行始终由结构化业务规则校验。

当前没有后台 Worker、自动任务重领、流式 UI、MCP、运营 SQL 或 Vue 管理后台。历史 Phase 3 检索对照保持原样，不重新标注为 Phase 4 Agent 效果。

下一阶段为官方 MCP Python SDK 的独立 Server 与 Client 示例，复用已有 Service，先提供只读订单、物流和政策工具，再验证工具发现、可信身份、参数与错误处理。
