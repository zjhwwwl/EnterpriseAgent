# 架构与实体关系

## 当前实现范围

本文件描述已确认的目标架构，并区分已经实现的基础层和后续设计。**Phase 1 已实现 FastAPI 应用工厂、配置、JSON 日志、统一错误、请求 ID、CORS 与基础测试。数据库、业务 Service、Agent、RAG、MCP、分析、Worker 和前端尚未实现。**

Phase 1 启动不建立 PostgreSQL、Redis、Chroma 或模型连接。`/health/ready` 仅反映应用的 lifespan 状态，不能作为这些依赖已就绪的证据。

## 设计目标

EnterpriseAgent 是个人求职作品，使用模拟电商数据，采用能解释、能测试的工程边界。首版为单企业、单 Agent、多工具，不引入 Kubernetes、多租户组织树或独立业务微服务。

主要业务闭环：客户查询订单与物流 → 检索适用售后政策 → Service 依据数据库事实判断 → 用户确认提交 → 创建待审核退款和工单 → 客服审核 → 标记模拟退款完成。知识问答和运营分析通过同一任务工作流处理。

## 模块化单体

```mermaid
flowchart TD
    UI[Vue 管理后台] --> API[API：认证、授权、参数校验]
    API --> JOBS[任务登记与同仓 Worker]
    JOBS --> GRAPH[LangGraph]
    GRAPH --> LLM[DeepSeek 兼容接口]
    GRAPH --> RAG[RAG 检索 Service]
    GRAPH --> TOOLS[工具适配层]
    API --> SERVICE[业务 Service]
    TOOLS --> SERVICE
    CLIENT[MCP Client] --> MCP[独立 MCP Server]
    MCP --> SERVICE
    SERVICE --> REPOSITORY[Repository]
    REPOSITORY --> DB[(PostgreSQL)]
    GRAPH --> ANALYTICS[分析 Service]
    ANALYTICS --> READONLY[独立只读连接池]
    READONLY --> VIEWS[analytics 授权视图]
    RAG --> CHROMA[(Chroma)]
    RAG --> BM25[BM25 索引]
    RAG --> RERANK[Reranker]
    JOBS --> REDIS[(Redis：缓存、限流、唤醒)]
```

API、Worker 和 MCP 是同仓的不同进程入口，共享业务逻辑和版本。初期只运行一个 Worker，负责文档索引和 Agent 任务。PostgreSQL 记录任务状态和执行租约，Redis 负责通知与缓存；Redis 数据丢失不应丢失审批或业务任务。

| 层 | 职责 | 边界 |
| --- | --- | --- |
| API | HTTP、认证上下文、参数和响应 | 不在路由内编写退款规则 |
| Schemas | Pydantic 输入输出与工具参数 | 不承担事务或模型调用 |
| Service | 归属、权限、政策、幂等、事务、审计 | 所有业务写入都经过此层 |
| Repository | SQLAlchemy 查询与持久化 | 不执行模型生成的任意业务写入 |
| Agent | 编排、工具选择、证据和确认流程 | 不持有直接修改订单状态的工具 |
| Tools | 将受校验的工具参数转为 Service 调用 | 用户身份来自认证上下文 |
| RAG | 解析、索引、检索、重排、引用 | 检索内容不能改变授权规则 |
| Analytics | SQL 生成约束、验证与只读执行 | 使用独立账号和连接池 |
| MCP | 协议与工具发现适配 | 直接调用共享 Service，避免重复业务逻辑 |

## 当前代码结构

包采用 `src/enterprise_agent` 布局，通过可编辑安装导入。`application.py` 提供 `create_app(settings=None, configure_logs=True)`；导入该工厂模块不会读取真实 `.env` 或创建应用，测试通过它注入配置并控制日志初始化。包内 `main.py` 创建并暴露 ASGI `app`，同时重新导出工厂。仓库根目录 `main.py` 调用 `cli.py`，使用配置启动 Uvicorn，并保留项目 JSON 日志配置、关闭原始 access log。

配置通过 Pydantic Settings 读取 `EA_` 环境变量。SecretStr 用于保护密钥的默认表示。生命周期启动后标记应用就绪，关闭后清除就绪状态。请求处理提供统一错误格式与 `X-Request-ID`，日志不采集请求体、查询字符串或 Authorization。

## 目标目录

以下为后续逐步建立的目录，不能据此推断对应模块当前已实现。

```text
src/enterprise_agent/
├─ application.py
├─ main.py
├─ cli.py
├─ config.py
├─ logging_config.py
├─ request_context.py
├─ api/
│  ├─ dependencies.py
│  └─ routes/                   # auth/orders/refunds/knowledge/agent/analytics
├─ schemas/
├─ models/
├─ repositories/
├─ services/
├─ agents/
│  ├─ graph.py
│  ├─ state.py
│  ├─ nodes/
│  └─ prompts/
├─ tools/
├─ rag/
│  ├─ parsers/
│  ├─ chunking.py
│  ├─ embeddings.py
│  ├─ retrieval.py
│  ├─ reranking.py
│  └─ indexing.py
├─ analytics/
│  ├─ sql_validator.py
│  └─ metrics.py
├─ mcp/
│  ├─ server.py
│  └─ client_example.py
└─ worker.py

alembic/
data/knowledge/
data/fixtures/
evals/datasets/
evals/configs/
evals/results/
frontend/src/{api,components,views,router,types}/
deploy/
.github/workflows/
```

## 数据库实体

以下实体在 Phase 2 开始落地迁移；当前未创建数据库表。客户作为 `users` 中的角色表示，不再维护一套重复的客户账号实体。

| 表 | 关键字段与约束 |
| --- | --- |
| `users` | 登录名唯一、密码哈希、角色、启用状态 |
| `products` | SKU 唯一、名称、类目、价格、售后属性 |
| `orders` | 订单号唯一、客户 FK、状态、商品金额/运费/实付、支付和签收时间 |
| `order_items` | 订单 FK、商品 FK、交易名称/价格快照、数量、优惠分摊后实付 |
| `logistics` | 订单 FK 唯一、模拟运单号、物流状态、事件；首版一单一包裹 |
| `refund_requests` | 订单 FK、申请人、金额、原因、状态、适用政策版本 FK、审核信息 |
| `support_tickets` | 订单 FK、可选退款 FK、负责人、状态；退款关联唯一 |
| `knowledge_documents` | 标题、类型、文件摘要、版本、可见性、索引状态 |
| `agent_tasks` | 会话 FK、任务类型、状态、步骤、耗时、Token、失败原因、执行租约 |
| `audit_logs` | 操作者、动作、资源、请求 ID、必要变更摘要、时间 |
| `knowledge_chunks` | 文档 FK、索引版本、文本、页码/段落位置、Chunk ID |
| `refund_policy_versions` | 有效期、适用类目、期限、运费规则、来源文档、规则版本 |
| `agent_sessions` | 会话所有者、LangGraph thread ID、创建时间 |
| `approval_requests` | 任务 FK、确认人、操作快照及摘要、有效期、确认/消费状态 |
| `idempotency_records` | 操作者、操作、幂等键、参数摘要、结果引用；组合唯一 |

```mermaid
erDiagram
    users ||--o{ orders : owns
    orders ||--|{ order_items : contains
    products ||--o{ order_items : references
    orders ||--o| logistics : ships
    orders ||--o{ refund_requests : receives
    refund_requests ||--o| support_tickets : creates
    refund_policy_versions ||--o{ refund_requests : governs
    knowledge_documents ||--o{ knowledge_chunks : contains
    knowledge_documents ||--o{ refund_policy_versions : supports
    users ||--o{ agent_sessions : owns
    agent_sessions ||--o{ agent_tasks : contains
    agent_tasks ||--o{ approval_requests : requests
    users ||--o{ audit_logs : performs
    users ||--o{ idempotency_records : submits
```

LangGraph Checkpointer 使用其自己的 PostgreSQL 持久化表。业务任务摘要不替代图状态，图状态也不替代退款事务或审计记录。

### 数据一致性

- 金额用整数分；数量为正，金额为非负，避免浮点舍入问题。
- 订单明细保留交易快照，商品改价不影响历史订单。
- 主外键与唯一约束保护实体关系；归属、时间、退款状态和任务状态建立合适索引。
- 退款申请、工单、幂等记录和审计在同一事务提交。
- 提交退款时锁定订单并再次读取资格，不能仅信任先前模型或页面结果。
- 同一订单最多一笔待审核、已批准或模拟完成的退款；拒绝/撤销后按规则允许重试。
- 同一个幂等键、相同参数返回原结果；参数不同返回冲突。
- 已发布政策不可原地修改，申请保留规则版本和计算快照。
- 删除知识文档只退出当前检索，历史业务保留必要政策快照。

### 模拟数据

种子脚本使用固定随机种子，可指定基准日期。计划覆盖约 90 天订单，并覆盖未支付、未发货、运输中、已签收、符合退款、超期、重复申请等场景。订单 `10086` 为可演示样例；另外配置其他客户订单验证越权拒绝。所有用户、联系方式、地址与运单均为模拟内容。

## 业务权限与退款边界

认证计划采用密码哈希与短时效 JWT。角色为客户、客服、运营分析员和管理员。客户只读取自己的订单和任务；客服处理售后；分析员读取脱敏视图；管理员管理知识库和用户。

归属过滤在 Service / Repository 读取时执行，不能先读取全部敏感数据再依赖模型隐藏。模型可提交订单号，不能提供替代认证身份的 `user_id`。

首版支持整单退款。用户确认意味着创建待审核申请；客服审核与模拟退款完成是独立操作，不连接真实退款服务。Agent 不可直接修改订单支付状态。

售后政策分两层：知识文档解释并提供引用，结构化政策字段供 Service 确定性判断。上传文档不自动发布新业务规则；缺少有效规则时不能通过模型推断规则来批准申请。

## RAG 与索引一致性

Phase 3 处理 PDF、DOCX、TXT 和 Markdown。PDF 先支持文本解析，扫描 PDF OCR 暂不包含。Chunk 保留页码、标题、段落或行号，初始大小约 300–400 个模型 Token，后续按开发集调优。

向量和中文 BM25 各自召回，通过 RRF 融合后交给 BGE Reranker。结果携带文档 ID、版本、Chunk ID 与来源位置。文档可见性、删除状态和当前索引版本以 SQL 登记为准，避免 Chroma 或 BM25 旧条目进入最终证据。

索引任务在解析、Embedding、向量与 BM25 构建完成后发布索引版本。失败保留原因并允许重建；不把部分索引成功显示为已完成。工作流检查证据并允许一次查询改写，最终仍不足时明确说明。

## 只读分析

Phase 6 建立脱敏 `analytics` 视图和独立数据库账号。SQL 生成只获得授权视图、列与指标定义。解析 PostgreSQL AST，递归检查 CTE、子查询和 JOIN；限定单条只读查询、关系、列和函数白名单。

拒绝写入 CTE、`SELECT INTO`、行锁、递归查询和未授权关系。查询使用只读事务、数据库超时、行数与字节上限。分析账号只获得指定视图的 `SELECT`，不获得业务基表修改权限。真实 PostgreSQL 测试才作为权限和超时成立的证据。

默认指标：销售额为支付订单的优惠后商品实付，排除运费且尚未扣减退款；订单量为期间创建订单数，包含取消订单；本月退款率为本月支付订单中截至查询时已模拟退款完成的订单数占比。数据库保存 UTC，业务区间按 `Asia/Shanghai` 解释。报告只描述数据支持的结果，不推断营销或竞争原因。

## 前端与可观测性

Vue 后台规划五页：Agent 对话、订单与售后、知识库、运营分析、任务详情。Markdown 禁用原始 HTML；展示引用、工具步骤、状态和人工确认内容。前端不保存模型密钥，也不直接访问数据库、Chroma 或 MCP。

Phase 1 已提供 HTTP 请求关联和 JSON 日志。后续补充任务步骤、工具耗时、模型耗时、实际 Token 与失败原因。Langfuse 为可选接入，默认关闭；涉及业务内容的 trace 先裁剪和脱敏。

## 执行与验证顺序

按数据库 → RAG → Agent → MCP → 分析 → 前端 → Evaluation → 全栈交付推进。每阶段使用对应真实基础设施验收必要边界；单元测试或配置验证不代替模型、数据库、浏览器和 Docker 的执行证据。
