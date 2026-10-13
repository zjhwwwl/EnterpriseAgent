# Phase 5：独立 MCP Server 与业务工具复用

当前版本 0.5.0。已实现官方 MCP Python SDK 1.30.0 的独立 stdio Server、Client 发现/调用示例及三个只读业务工具。协议不调用模型，不另写电商业务规则，不增加 LangGraph 图分支。此前 Phase 4 的真实 DeepSeek 烟测和原始消费记录完整保留，不能用本阶段协议检查替代在线模型质量。

## 已实现的功能

- 官方低层 Server、stdio transport 和 ClientSession 初始化、工具发现与调用。
- query_order → get_order、query_logistics → get_logistics、query_refund_policy → get_refund_policy，复用现有 BusinessTools、OrderService、RefundService 和 KnowledgeService。
- manifest 来自共享 Pydantic schema；order_no 为 5–30 位 ASCII 数字字符串，reason 使用既有三个业务枚举，strict / extra forbid 阻止类型转换、额外字段和身份注入。
- 每个 Server 进程绑定现有 JWT；每次调用 AuthService 重新检查签名、期限、数据库启用状态、token_version、角色，Service 再检查订单归属。
- 订单事实和整数分金额来自真实业务库；政策查询返回付款时适用资格与限定政策文档检索，保留当前有效引用，不创建退款或工单。
- 单次认证与业务总预算默认 15 秒，安全错误通过 isError 和结构化 error 返回；错误不回显未验证参数及底层异常，日志仅记录必要工具名、状态、耗时与错误码。
- stdout 仅 JSON-RPC，日志写 stderr；SDK 原始请求日志隔离。Client / Server 均不读取 .env，子进程配置白名单排除模型 Key、管理员/测试连接和 Redis。
- 模块入口和两个安装命令，scripts/mcp-demo.ps1 支持公开模拟用户本机登录及订单选择；Client 可用进程环境 Token，支持脱敏 JSON 报告。
- 官方异步上下文关闭 Session/stdin/实际子进程；Windows Client 使用 Proactor，数据库 Server 使用 Selector。

## 代码与启动

关键代码：`src/enterprise_agent/mcp/server.py`、`mcp/client.py`、`tools/business.py`、`services/auth.py`。包版本、服务信息与脚本结果切换为 0.5.0 / phase=5，两份哈希锁固定 mcp 1.30.0；本阶段没有新增表、数据库角色或迁移，schema head 保持 0004_agent_role。

API、数据库、Chroma 和已准备模型/政策索引就绪后：

```powershell
.\scripts\mcp-demo.ps1
.\scripts\mcp-demo.ps1 -OrderNo '10086'
```

脚本在 loopback API 登录公开 demo_customer，将 JWT 经白名单子进程环境传递。它不把凭据作为命令行参数，也不输出 Token；读取成功不等于退款批准。

同一 PowerShell 中已经准备好普通业务环境后，可直接保存 Client 结果：

```powershell
.\.venv\Scripts\python.exe -m enterprise_agent.mcp.client --demo-login --order-no '10086' --output 'var/test-results/mcp-client.json'
```

已有 Token 的调用设置进程环境 EA_MCP_ACCESS_TOKEN 并省略 --demo-login；同样需要普通 DB/JWT/RAG 环境，模块不读取 .env。Token 过期/撤销需重新取得凭证并启动进程。参数、错误及配置说明见[API](api.md)和[部署](deployment.md)。

## 验收记录

已完成分组预检：MCP Server 单元 43 项通过、Client 单元 46 项通过，真实 PostgreSQL/RAG/stdio 集成 4 项通过。单元使用明确控制对象；SQL、模型推理、向量库和实际子进程分别以真实依赖检查，不将分组子集累计为完整测试总数。

最终完整检查启用 --require-postgres / --require-rag：**614 项通过，0 失败、0 错误、0 跳过**，JUnit 548.312 秒（CLI 548.32 秒）。摘要生成于 2026-10-13T15:46:41.3904727Z，Ruff / 格式检查 128 个文件及 pip check 通过；85 条 Chroma legacy embedding function config 弃用警告未屏蔽。测试期间选取的 19 个实现、测试与依赖文件哈希保持不变。[完整摘要](../evals/results/phase5/verification.json)、[源码记录](../evals/results/phase5/source-freeze.json)与[可读报告](../evals/results/phase5/report.md)已归档。

实际 stdio 测试覆盖：官方初始化与三工具发现、真实订单/物流/适用政策、三个工具他人订单拒绝、_meta 身份注入拒绝、严格参数、未知/写入工具拒绝、当前合法引用、stdout 仅协议、stderr 秘密扫描、退款/工单/审批计数不变，以及 SDK 上下文退出后的实际子进程结束。它使用 enterprise_agent_test、普通 ea_test_app 和独立 Chroma 集合；没有受控 LLM 或在线供应商调用。

真实 SQL 授权测试还覆盖当前角色变化、启用状态与 token_version 撤销。单元测试覆盖未知工具、安全异常/序列化失败、超时、取消、日志隔离和启动配置拒绝。

实际生产 Client [Demo JSON](../evals/results/phase5/mcp-demo.json)已归档：stdio / Server 0.5.0，发现三个工具并完成三次真实读取，政策检索为 hybrid_rerank、三个引用，provider_calls=0 / token_usage=null。该运行通过本机 API 登录普通模拟客户，无控制 LLM；单次命令耗时 6.871 秒仅是本次观察，不作为性能基准。[主服务 HTTP](../evals/results/phase5/demo-http.json)记录 0.5.0 / phase=5、五项就绪通过，健康不调用聊天模型。全量中的[实际 stdio 证据](../evals/results/phase5/mcp-stdio.json)为协议 2025-11-25、15 个 JSON-RPC stdout 帧、子进程退出码 0；退款/工单/审批数量分别保持 19/19/0。

原始产物为 `var/test-results/phase5.xml`、`phase5.json`、`phase5-mcp-stdio.json`，报告不包含 Token、DB URL 或密钥。Alembic check 实际返回 `No new upgrade operations detected`；本阶段没有新增迁移。检查运行命令：

```powershell
.\scripts\migrate.ps1 -Check
.\scripts\check.ps1
.\scripts\mcp-demo.ps1
```

本阶段云模型调用为零，provider Token 统计为 null / N/A；真实 CPU Embedding/Reranker 推理不作为聊天模型消费。协议/引用检查不代表模型意图、工具选择或语义正确率；Phase 4 [真实在线烟测](../evals/results/phase4-live/report.md)和 Phase 3 [检索对照](../evals/results/phase3/report.md)保留原记录，不重写为本阶段成绩。

## 实现边界与未验收项

- MCP 仅本机 stdio，每个进程一个 JWT 身份；没有 HTTP/SSE MCP、OAuth、多用户共享进程或 Token 自动续期。
- 仅暴露三个只读工具，没有 MCP 写入/退款确认能力，也没有 LangGraph MCP 分支；敏感操作继续经过原人工确认流程。
- 复用普通受限业务账号，并非新增数据库只读账号；工具 manifest 注解不替代白名单与 Service 授权。
- 存在适用政策但 RAG 未启用时返回 rag_not_enabled；知识无证据时返回实际状态，相关性/引用存在不证明完整政策语义充分。
- stdio 仅作为本机接口验收；没有生产负载或公网部署验收。
- 两份 lock 在当前 Windows 解析，含未带平台 marker 的 pywin32==312；Linux 应用镜像需在 Phase 9 重编与验证，不直接使用该锁声称跨平台兼容。
- 没有 NL SQL、Vue、tracing、应用镜像、全栈 Compose、GitHub Actions 执行或真实支付退款。

版本选择参考官方[维护分支说明](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v1.x/README.md)、[PyPI 1.30.0](https://pypi.org/project/mcp/1.30.0/)及[发布记录](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0)。维护版仍接受关键错误与安全修复，2.x 是当前主线，不把本阶段固定版本称为最新主线。

## 下一阶段与验收标准

Phase 6 实现只读运营分析：明确销售额/退款率等指标口径，建立授权分析视图与独立数据库只读账号，模型生成 SQL 后通过 AST/白名单、超时和结果上限再执行，并根据真实结果生成报告。安全边界须覆盖非授权表、写操作、异常 SQL 与结果限制；确定性查询和真实数据库集成均有测试，结果和在线模型证据分开记录。阶段结束执行相关检查、更新真实文档并提交本地 Git Commit，不自动公开部署。
