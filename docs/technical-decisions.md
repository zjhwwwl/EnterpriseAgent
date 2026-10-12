# 技术决策

本页记录已确认的设计取舍。**只有 Phase 1 基础工程已落地；后续方案必须在相应阶段安装、测试和修订，不能把设计决定当成运行证据。**

## 1. 模块化单体与单 Agent

一个代码仓库维护 API、Worker、MCP 和业务 Service。Agent 使用一张 LangGraph 图按任务分支调用工具。这样可以直接展示授权、事务、恢复和评估，不需要维护跨微服务协议或多个 Agent 的协商流程。

初期一个 Worker 和线程串行执行；只有吞吐或故障证据要求时，再增加任务并行。当前不引入 Kubernetes、多租户组织模型或通用规则引擎。

## 2. Python 3.13 与分阶段依赖锁定

Python 3.13 是目标运行版本。Phase 1 固定 FastAPI、Pydantic、日志、测试和开发工具版本；数据库、LangGraph、本地模型和 MCP 在其阶段分别验证后再锁定。

`pyproject.toml` 声明包和工具配置，`requirements/base.lock` 与 `requirements/dev.lock` 固定安装版本和分发文件哈希。当前锁文件在 Windows Python 3.13 下生成，Linux 尚未安装验证；后续 CI 和容器阶段分别生成并验证适用的 Linux 约束，不能把当前锁定视作跨平台证明。开发脚本先验证解释器版本，用 `--require-hashes` 安装锁文件，再以 `--no-deps --no-build-isolation` 可编辑安装包，减少隐式解析。

采用项目内 `.venv`，安装、测试与运行都显式使用该解释器。缓存、TEMP/TMP 放 D 盘项目目录，不修改全局 Python 依赖。

## 3. 可注入应用工厂

Phase 1 在 `application.py` 实现 `create_app(settings=None, configure_logs=True)`。导入工厂模块不会实例化全局应用或读取真实 `.env`，测试可传入配置并控制日志初始化，避免每个用例读取真实配置或重复改变全局日志。独立子进程用非法 `EA_` 环境检查导入边界；实际创建应用时仍按要求校验配置。

包内 `main.py` 创建并暴露 `enterprise_agent.main:app`，并重新导出工厂。仓库 `main.py` 通过 `cli.py` 提供简便启动：使用项目 JSON 日志并关闭原始 URL access log。直接 Uvicorn CLI 有自己的日志配置，文档示例关闭 access log，但整体 JSON 输出以项目启动入口为准。除进程内 ASGI 测试，真实 CLI 子进程和 loopback HTTP 测试验证启动与网络响应。应用生命周期负责就绪状态，存活和就绪分别表达进程响应与当前应用是否完成启动。

就绪响应目前只包含 `application`。后续依赖健康项由实际连接验证，避免无数据库阶段产生无意义失败，也避免把基础就绪伪装为全栈就绪。

## 4. 配置、错误与日志契约

Phase 1 用 Pydantic Settings 校验 `EA_` 环境变量，密钥使用 SecretStr。统一错误外层为 `error` 与 `request_id`，HTTP 状态仍表达错误类别。

请求 ID 校验后使用，并通过 `X-Request-ID` 返回。JSON 日志不记录请求体、查询字符串、Authorization 或完整配置。日志保护需要测试，不能只依靠开发者记得不打印 Key。

当前无登录和业务接口，默认绑定本机。CORS 采用精确 Origin，CORS 不承担用户授权。

## 5. PostgreSQL 与确定性退款规则

Phase 2 用 PostgreSQL、SQLAlchemy 2.x、psycopg 3 和 Alembic。测试涉及事务、部分唯一约束、只读权限和时间限制时使用真实 PostgreSQL，不以 SQLite 替代证明。

金额使用整数分，订单明细存交易快照。Service 校验归属、资格、退款金额和状态；Agent 获取解释并发起确认，不自行改订单状态。

政策文档负责解释，已发布的结构化版本负责执行。新文档上传不自动发布退款规则。两者用来源关联与版本快照保持可追溯。首版仅整单退款，先验证并发、幂等和审批闭环。

## 6. 用户确认与事务幂等

人工确认绑定用户、任务、线程、操作快照、摘要和有效期。确认后锁定订单并重新检查规则，避免页面或模型早先结果过期。

LangGraph 的恢复可能重复执行节点，系统采用至少一次执行配合幂等副作用。审批中断和写入分开；退款、工单、幂等和审计在同一数据库事务内提交。需要故障测试覆盖事务已提交而图状态尚未保存的情况。

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

每阶段完成可运行增量、相应测试、问题报告和本地 Git Commit。当前测试结果只证明 Phase 1 的边界。后续在线模型、浏览器、Docker 和数据库检查分别保存真实证据。

不提交未运行的 Evaluation 分数、占位 Demo 截图或虚构性能结果。环境无法运行的检查标明原因；只读或单元验证不能冒充端到端验证。
