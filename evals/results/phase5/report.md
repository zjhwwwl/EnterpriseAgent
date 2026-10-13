# Phase 5 实际验收报告

版本 0.5.0：官方 MCP SDK 1.30.0、独立本机 stdio Server / Client，复用已有业务工具与 Service。只开放订单、物流和退款政策读取；每次调用重新认证当前用户，工具参数不能设置身份。敏感修改继续使用既有人工确认工作流。

## 完整工程检查

2026-10-13T15:37:32.251883Z 开始的完整 pytest 启用真实 PostgreSQL 与 RAG 必选检查：**614 passed，0 failures / errors / skipped**。JUnit 548.312 秒，CLI 548.32 秒；Ruff lint、128 文件格式检查和 pip check 通过。85 条 Chroma `legacy embedding function config` 弃用警告保留，没有隐藏或改称错误已修复。

分组预检为 Server 单元 43 项、Client 单元 46 项、真实 SQL/RAG/stdio 集成 4 项；这些子集包含在完整测试中，不相加生成新总数。单元控制对象只验证边界，真实业务、模型资产与进程验证另列。Alembic check 返回 `No new upgrade operations detected`；本阶段没有新迁移。

[完整摘要](verification.json)记录真实运行时间和数量。[源码记录](source-freeze.json)为选取的 19 个实现、测试和依赖文件；最后修改均早于完整检查开始，检查中段与结束哈希一致。未把当前未提交源码回写成历史干净提交。

## 实际协议与业务

[stdio 证据](mcp-stdio.json)使用官方 ClientSession 启动生产 Server，真实依赖为专用 `enterprise_agent_test` 普通业务账号、Chroma HTTP 与已校验的本地 CPU Embedding / Reranker。初始化协议为 2025-11-25，服务版本 0.5.0。

- 发现并调用 `query_order`、`query_logistics`、`query_refund_policy`，订单与物流结果匹配共享 Service，政策引用可在当前权限下逐条取得。
- 三个工具拒绝他人订单；严格参数拒绝类型转换与身份字段，伪造 `_meta` 不改变用户，未注册写入工具不可调用。
- 实际 stdout 的 15 帧全部为 JSON-RPC，stderr 私密配置扫描通过。
- SDK 退出后实际子进程返回码为 0，Windows Job Object 已关联。
- 退款申请、工单、审批记录数量前后均为 19、19、0。

另有真实 SQL 集成检查同一连接中的角色变化、用户禁用与 token_version 撤销；工具注解不是权限依据，实际归属仍由 Service 决定。

## 本机客户端示例

[Client Demo](mcp-demo.json)通过 loopback HTTP 登录公开模拟客户，再启动独立 stdio Server。三个工具调用均成功，订单 10086 及模拟物流来自业务库，适用退款政策返回 3 条 hybrid_rerank 引用。单次客户端命令观察耗时 6.871 秒，运行时另有回归测试，不属于性能基准。[主 API 健康](demo-http.json)记录五项就绪、0.5.0 / phase=5。

本阶段没有聊天模型或付费云调用，provider Token 为 null / N/A；本地 CPU 推理不计作 DeepSeek 消费。协议、来源与数据库测试不提供模型意图、Tool Calling 语义或任务完成率分数。历史 Phase 3 检索对照和 Phase 4 在线消费记录原样保留。

## 边界与下一阶段

MCP 仅本机 stdio、单进程单 JWT，没有 HTTP/OAuth、自动续期、写入工具或 LangGraph MCP 分支。使用现有受限业务数据库账号；只读工具不等于数据库只读账号。当前依赖锁只验证 Windows，含没有平台 marker 的 pywin32；Linux 应用镜像需在 Phase 9 重编与验收。

Phase 6 将建立授权分析视图与独立只读账号，结合 SQL AST 白名单、超时和行数上限实现运营分析，明确指标口径并以真实数据库结果验证。
