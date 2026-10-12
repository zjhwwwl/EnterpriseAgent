# 技术决策

本页记录已确认的设计取舍。**Phase 2 已落地基础服务、真实 PostgreSQL、认证、订单退款业务和基础设施 Compose；Agent / RAG / MCP / 分析 / 前端仍为设计。** 后续方案按阶段安装、测试和修订，设计决定不替代运行证据。

## 1. 模块化单体与单 Agent

一个代码仓库维护 API、Worker、MCP 和业务 Service。Agent 使用一张 LangGraph 图按任务分支调用工具。这样可以直接展示授权、事务、恢复和评估，不需要维护跨微服务协议或多个 Agent 的协商流程。

初期一个 Worker 和线程串行执行；只有吞吐或故障证据要求时，再增加任务并行。当前不引入 Kubernetes、多租户组织模型或通用规则引擎。

## 2. Python 3.13 与分阶段依赖锁定

Python 3.13 是目标运行版本。Phase 1 固定基础依赖，Phase 2 增加 SQLAlchemy、psycopg、Alembic、Redis、PyJWT 与 Argon2；LangGraph、本地模型和 MCP 在其阶段验证后锁定，避免提前下载大量未用依赖。

`pyproject.toml` 声明包和工具配置，`requirements/base.lock` 与 `requirements/dev.lock` 固定安装版本和分发文件哈希。当前锁文件在 Windows Python 3.13 下生成，Linux 尚未安装验证；后续 CI 和容器阶段分别生成并验证适用的 Linux 约束，不能把当前锁定视作跨平台证明。开发脚本先验证解释器版本，用 `--require-hashes` 安装锁文件，再以 `--no-deps --no-build-isolation` 可编辑安装包，减少隐式解析。

采用项目内 `.venv`，安装、测试与运行都显式使用该解释器。缓存、TEMP/TMP 放 D 盘项目目录，不修改全局 Python 依赖。

## 3. 可注入应用工厂

Phase 1 在 `application.py` 实现 `create_app(settings=None, configure_logs=True)`。导入工厂模块不会实例化全局应用或读取真实 `.env`，测试可传入配置并控制日志初始化，避免每个用例读取真实配置或重复改变全局日志。独立子进程用非法 `EA_` 环境检查导入边界；实际创建应用时仍按要求校验配置。

包内 `main.py` 创建并暴露 `enterprise_agent.main:app`，并重新导出工厂。仓库 `main.py` 通过 `cli.py` 提供简便启动：使用项目 JSON 日志并关闭原始 URL access log。直接 Uvicorn CLI 有自己的日志配置，文档示例关闭 access log，但整体 JSON 输出以项目启动入口为准。除进程内 ASGI 测试，真实 CLI 子进程和 loopback HTTP 测试验证启动与网络响应。应用生命周期负责就绪状态，存活和就绪分别表达进程响应与当前应用是否完成启动。

Phase 2 就绪检查包含实际 PostgreSQL 连通、Alembic head、必要业务权限、普通应用账号和已配置 Redis PING。缺少必需权限，或者具有库/表 owner、超级用户、public CREATE、订单状态/审计超额写权限时均拒绝就绪。无业务库仍可查看存活和 OpenAPI，但 readiness=503；启动不自动迁移或 seed。

psycopg 异步连接在 Windows 使用 Selector event loop；项目 CLI 与种子入口明确设置，避免默认 Proactor 不兼容。直接 Uvicorn 启动也应使用已支持的运行入口，相关本机检查单独记录。

## 4. 配置、错误与日志契约

Phase 1 用 Pydantic Settings 校验 `EA_` 环境变量，密钥使用 SecretStr。统一错误外层为 `error` 与 `request_id`，HTTP 状态仍表达错误类别。

请求 ID 校验后使用，并通过 `X-Request-ID` 返回。JSON 日志不记录请求体、查询字符串、Authorization 或完整配置。日志保护需要测试，不能只依靠开发者记得不打印 Key。

当前提供 JSON 登录与受保护的业务 API，默认绑定本机。Argon2 验证在工作线程执行，JWT 默认 30 分钟；每请求从数据库加载启用状态、token_version 与角色，不把过期角色声明当作授权来源。CORS 采用精确 Origin，不能承担用户授权。

配置 Redis 时登录原子计数限流，默认来源 IP 10 次 / 60 秒；key 使用 HMAC 隐藏 IP。不接受任意 X-Forwarded-For 来绕过限制，Redis 故障时登录 503。未配置 Redis 的本地演示没有限流保障，公开账号不能直接作为公网配置。

## 5. PostgreSQL 与确定性退款规则

Phase 2 使用 PostgreSQL、SQLAlchemy 2.x、psycopg 3、Alembic。涉及事务、部分唯一约束、行锁、advisory lock 和权限时用真实 PostgreSQL，不以 SQLite 替代。开发和测试数据库分离，普通非 owner 应用账号只获取必要权限；本地初始化账号仅执行迁移与 seed。

PostgreSQL 的 FOR UPDATE 需要更新权限，因此订单表只授权 id 列 UPDATE 以取得行锁，不授权状态或金额列更新；Service 本身没有更改主键的业务路径。当前不开放知识库、会话、任务、商品和用户的通用写权限，避免为未来功能预先给运行账号全表 DML。

金额使用整数分，订单明细存交易快照。Service 校验归属、资格、退款金额和状态；Agent 获取解释并发起确认，不自行改订单状态。

政策文档负责解释，已发布结构化版本负责执行。当前 5 份模拟文档已登记为 uploaded，没有向量检索。新文档不自动发布退款规则，两者用来源关联与版本快照保持追溯。首版只做整单退款、一单一包裹，集中验证并发、幂等与确认闭环。

按订单支付时点选最新已生效版本，再检查有效截止，不在最新版本失效后自动退回旧规则。版本及有效起点唯一，发布后 PostgreSQL trigger 禁止 UPDATE / DELETE；应用角色只能读取政策。调整规则通过新版本，历史申请保留快照。

## 6. 用户确认与事务幂等

Phase 2 的业务确认绑定用户、订单、操作快照、摘要和 30 分钟有效期。客户端仅提供 approval_id 与严格布尔决策，不能自行提交金额或政策。确认后锁定记录及订单并重新检查，事实变化、过期、摘要损坏或跨用户请求被拒绝。只有客户可以提交自己的申请；客服审核是另一权限与状态转换。

同一操作者/操作/幂等键先加事务级 advisory lock，再读取幂等结果，避免相同 Key 并发都执行。相同 Key 相同请求重放原结果，不同请求 409；不同 Key 还通过订单行锁和有效退款部分唯一约束避免重复。退款、工单、幂等、审计在同一事务提交，审计权限只追加。

Phase 4 再绑定任务、thread 和 LangGraph interrupt。图恢复可能重复执行节点，采用至少一次执行与幂等副作用，审批中断与写入分开。需要故障测试覆盖业务事务已提交而 Checkpoint 未保存；当前幂等测试不代表这项图恢复验收已经完成。

## 7. DeepSeek 与本地 Embedding 分离

DeepSeek 通过 OpenAI 兼容聊天接口接入，模型与 Thinking 配置化。先验证非 Thinking Tool Calling，避免把旧教程中的强制 `tool_choice` 无条件用于所有模式。

Embedding 使用中文 BGE 小模型，Reranker 使用 BGE Reranker，分别配置并验证。聊天模型不承担索引向量生成的隐式职责。CPU 性能、模型资源和兼容性是 Phase 3 的实测事项。

默认不在线调用；缺 Key 的在线用例标记跳过。模型使用量从真实响应获取，不用预设数字填报告。

## 8. 混合检索与引用

Chroma 在 Linux 容器运行，应用通过 HTTP Client 使用。中文分词后的 BM25 和向量召回通过 RRF 融合，再 Rerank。Chunk 保留源文档和位置，并由 SQL 登记约束可见性与当前索引版本。

文档删除或重索引后旧向量不能成为有效证据。工作流允许一次查询改写，仍不足时说明限制。Embedding、Chunk 和参数用开发集调优，在保留集进行纯向量与混合检索对照，不能预先假设优化必然提高指标。

## 9. SQL 多层只读边界

运营分析采用授权脱敏视图、单独账号和连接池。应用使用 PostgreSQL AST、关系/列/函数白名单限制 SQL，数据库使用权限、只读事务、statement timeout 和结果限制提供独立边界。

不依赖关键词黑名单，因为危险查询可隐藏在 CTE、子查询或函数中。角色授权在生成 SQL 前执行，模型不能选择更高权限连接。报告输出指标口径和查询结果，不推断未观察到的原因。

## 10. MCP 复用 Service

使用官方 MCP Python SDK，首版 stdio Server 暴露订单、物流和政策只读工具。Client 子进程完成真实发现和调用，身份来自可信启动上下文。

MCP 只负责协议适配，业务规则调用同一个 Service。Agent 接入一条真实 MCP 只读路径，证明协议集成；写入仍通过本地确认。具体 SDK 版本在 Phase 5 锁定，不混用旧版接口。

## 11. 前端与可观测性

Vue 3 + TypeScript + Element Plus 提供五个必要业务页面。Markdown 禁用原始 HTML，引用、工具步骤、确认和错误通过结构化数据展示。前端不持有模型密钥或直连业务数据库。

Python JSON 日志始终启用。Langfuse 是可选依赖，默认关闭；即使不接追踪服务，也需要记录任务步骤、实际耗时、Token 和失败。追踪不能泄露密码、Key 或不必要用户信息。

## 12. 真实结果与渐进交付

每阶段完成可运行增量、相应测试、问题报告和本地 Git Commit。Phase 1 历史结果保留，Phase 2 实际 PostgreSQL、Redis、认证和退款验证单独记录。后续在线模型、浏览器、全栈 Docker 检查分别保存证据。

不提交未运行的 Evaluation 分数、占位 Demo 截图或虚构性能结果。环境无法运行的检查标明原因；只读或单元验证不能冒充端到端验证。

## 13. 本地基础设施与可复现数据

Docker Desktop 程序和真实 WSL 数据盘分别放在 D 盘，本阶段仅 PostgreSQL / Redis 容器，API 在 Windows 本机。固定镜像版本、loopback 绑定和独立测试库使演示易复现，不增加 Kubernetes 或独立业务微服务。

随机本地秘密位于 Git 忽略的 var，脚本不会覆盖已有秘密，不将密码放命令参数。seed 使用固定随机种子和 UTC 基准日，事务串行、重复运行不 reset；演示数据会随当前时间超期，测试注入时钟。可复现目标是交易和政策事实，密码 salt 保留安全随机性。
