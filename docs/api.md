# API 契约

**当前版本 0.3.0 / Phase 3。** 已实现健康、认证、订单、退款、知识库管理和证据检索接口。LangGraph、聊天生成、任务、MCP 和分析 API 尚未提供。OpenAPI 与实际代码是当前契约来源。

## 基础接口与认证

默认地址 `http://127.0.0.1:8000`，`EA_DOCS_ENABLED=true` 时提供 `/docs` 和 `/openapi.json`。所有响应携带 `X-Request-ID`，外部输入的请求 ID 会校验。

| 方法 | 路径 | 成功码 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 200 | 名称、版本、phase=3、文档及健康入口 |
| GET | `/health/live` | 200 | HTTP 进程存活 |
| GET | `/health/ready` | 200 / 503 | 实际数据库与已配置 Redis 的就绪检查 |
| POST | `/api/v1/auth/login` | 200 | JSON 账号密码，返回 JWT |
| GET | `/api/v1/auth/me` | 200 | 需要 Bearer Token，返回当前用户 |

就绪响应的 `checks` 包含 application、database；配置 Redis 时包含 redis，启用 RAG 时包含 knowledge。数据库检查 Alembic `0002_knowledge`、业务必需权限和普通运行账号；库/表 owner、超级用户、public CREATE、订单状态 UPDATE 或审计 UPDATE/DELETE 等超额权限被拒绝。knowledge 检查固定本地模型文件及 Chroma 心跳。未配置、未迁移或依赖故障不算就绪，健康通过也不证明所有文档已索引或每个问题都有答案。

登录请求：

```json
{"username": "demo_customer", "password": "DemoOnly!2026"}
```

用户名 3–64 字符，使用字母、数字、点、下划线、连字符，按小写查找；拒绝额外字段。密码为公开模拟数据，不是私人凭据。返回 `access_token`、`token_type=bearer`、`expires_in`（秒）和不含密码哈希的 `user`。随后使用 `Authorization: Bearer <token>`。

默认 Token 30 分钟。每请求从数据库重查启用状态、角色和 token_version，JWT 不授予独立于数据库的角色权限。配置 Redis 后按来源 IP 默认 10 次 / 60 秒限流；仅信任连接来源，不从任意转发头取地址。超过限制 429，Redis 故障 503；未配置 Redis 不启用该限流。

## 订单接口

全部需要 Bearer Token。客户只能访问自己的订单，客服和管理员可查询订单；分析员无订单明细权限。跨客户资源与不存在资源均为 404。

| 方法 | 路径 | 成功码 | 用途 |
| --- | --- | --- | --- |
| GET | `/api/v1/orders` | 200 | 分页订单列表，客户按归属过滤 |
| GET | `/api/v1/orders/{order_no}` | 200 | 订单状态与金额 |
| GET | `/api/v1/orders/{order_no}/items` | 200 | 商品交易快照 |
| GET | `/api/v1/orders/{order_no}/logistics` | 200 | 模拟数据库运单、状态、事件 |
| GET | `/api/v1/orders/{order_no}/refund-eligibility` | 200 | 确定性规则检查，不创建申请 |
| POST | `/api/v1/orders/{order_no}/refund-preview` | 201 | 创建 30 分钟待确认操作，仅所属客户 |

订单号为 1–40 字符安全标识。列表 `offset` 为 0–100000，`limit` 为 1–100，默认 20；返回 items、total、offset、limit。

金额字段全部是**整数分**：goods_paid_cents、shipping_paid_cents、paid_total_cents；明细保留商品名、单价、数量、优惠后实付快照。

资格检查 `reason` 默认 change_mind，可选 change_mind / quality_issue / not_received。返回 eligible、code、message、amount_cents、policy。eligible=true 只表示可以提交待审核申请，不表示客服已批准。

预览请求：

```json
{"reason": "change_mind", "notes": "模拟申请，商品未使用"}
```

notes 最长 500 字符，拒绝额外字段。返回 approval_id、订单、金额、原因、notes、expires_at、政策来源和 requires_confirmation=true。金额、政策、订单事实和操作摘要由 Service 生成，不由客户端传入。修改操作内容需要重新预览。

## 退款接口

| 方法 | 路径 | 成功码 | 用途 |
| --- | --- | --- | --- |
| GET | `/api/v1/refunds` | 200 | 归属过滤的分页退款列表 |
| POST | `/api/v1/refunds` | 200 | 明确确认或拒绝，需 Idempotency-Key |
| GET | `/api/v1/refunds/{refund_id}` | 200 | 申请、金额、政策快照、审核进度 |
| POST | `/api/v1/refunds/{refund_id}/review` | 200 | 客服 / 管理员审核 |
| POST | `/api/v1/refunds/{refund_id}/complete-simulated` | 200 | 客服 / 管理员标记模拟完成 |

refund_id 和 approval_id 使用 UUID。创建申请的请求仅接受：

```json
{"approval_id": "<预览返回的 UUID>", "confirmed": true}
```

confirmed 必须是 JSON 布尔值，不接受字符串或数字。HTTP Header `Idempotency-Key` 必填，长度 8–128，安全字符字母、数字、点、下划线、冒号、连字符，首位须字母或数字。

Service 在事务中锁定幂等键、确认记录和订单，重读归属、政策和订单事实。有效确认被一次消费；过期、跨用户、摘要损坏、事实变化和已消费状态不能写入。confirmed=false 消费拒绝决定但不创建退款或工单。

成功确认返回 approval_id、status、refund、replayed。同一操作者、同一 Key、同一请求重放原结果，replayed=true；同 Key 不同请求为 409。退款、工单、幂等记录和审计在同一事务提交，不同 Key 的并发还受订单锁和有效退款唯一约束保护。

审核请求：

```json
{"decision": "approve", "note": "模拟审核，已核对申请"}
```

decision 仅 approve / reject，note 1–500 字符；只能审核 pending_review。完成接口无请求体，只允许 approved → completed_simulated，不连接真实支付服务，也不更改订单支付状态。

当前没有工单独立管理 API、退款撤销 API、政策发布 API。数据库包含相关记录不代表这些接口存在。

## PowerShell 演示

以下演示仅对本地模拟数据执行。先查询和预览，人工核对后再运行确认命令。

```powershell
$apiBase = 'http://127.0.0.1:8000/api/v1'
$loginBody = @{username='demo_customer'; password='DemoOnly!2026'} | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "$apiBase/auth/login" -ContentType 'application/json' -Body $loginBody
$customerHeaders = @{Authorization="Bearer $($login.access_token)"}
Invoke-RestMethod -Uri "$apiBase/orders/10086" -Headers $customerHeaders
Invoke-RestMethod -Uri "$apiBase/orders/10086/logistics" -Headers $customerHeaders

$previewBody = @{reason='change_mind'; notes='本地模拟申请'} | ConvertTo-Json
$preview = Invoke-RestMethod -Method Post -Uri "$apiBase/orders/10086/refund-preview" -Headers $customerHeaders -ContentType 'application/json' -Body $previewBody
$preview | Select-Object order_no, amount_cents, reason, expires_at, policy
```

检查输出确认申请内容之后，再执行：

```powershell
$confirmationBody = @{approval_id=$preview.approval_id; confirmed=$true} | ConvertTo-Json
$confirmationHeaders = @{
    Authorization="Bearer $($login.access_token)"
    'Idempotency-Key'="demo-$([Guid]::NewGuid().ToString('N'))"
}
$result = Invoke-RestMethod -Method Post -Uri "$apiBase/refunds" -Headers $confirmationHeaders -ContentType 'application/json' -Body $confirmationBody
$result | Select-Object status, replayed, refund
```

客服使用 demo_support 登录，在 Swagger 对返回的 refund.id 执行 review；批准后才能 complete-simulated。订单 10086 已有有效退款时再次预览会被拒绝，可查询退款记录或选择其他符合条件的所属订单。固定日期种子会过期，不能把过期规则拒绝当成接口故障。

## 错误与日志

```json
{
  "error": {"code": "order_not_found", "message": "订单不存在。", "details": []},
  "request_id": "响应中的关联 ID"
}
```

| 状态 | 示例 code | 含义 |
| --- | --- | --- |
| 401 | invalid_credentials / unauthenticated | 登录失败、Token 缺失/过期/失效 |
| 403 | forbidden | 角色无操作权限 |
| 404 | order_not_found / approval_not_found / refund_not_found | 不存在或当前用户不可访问 |
| 409 | idempotency_conflict / approval_expired / refund_already_exists | 业务状态或幂等冲突 |
| 422 | REQUEST_VALIDATION_ERROR / invalid_idempotency_key | 非法输入 |
| 429 | login_rate_limited | 登录次数超限 |
| 503 | database_unavailable / rate_limiter_unavailable | 必要依赖不可用 |
| 500 | INTERNAL_SERVER_ERROR | 安全内部错误，凭请求 ID 排查 |

框架错误使用大写 code，业务拒绝使用明确的小写 code。输入校验 details 仅含 loc 和 type，不回显原始输入；未知异常不暴露栈、配置或连接信息。健康 503 使用健康响应模型；CORS 预检错误是中间件协议响应。

日志 JSON 记录必要字段和请求 ID，不记录请求体、查询字符串、Authorization 或全量配置。审计仅保存必要变更摘要。CORS 使用精确 Origin，不能代替认证与归属校验。

## 后续接口

Agent 会话与执行步骤、LangGraph 中断恢复、MCP 和自然语言分析在后续阶段实现。本阶段的业务确认尚不是 LangGraph interrupt，也不提供可任意提交 resume 命令的 HTTP 接口。知识库接口如下。


## 知识库管理

所有接口需要 Bearer Token。customer、analyst 仅可访问公开文档，support、admin 可访问公开及内部文档；写入只允许 admin。不存在和不可访问的文档/引用使用 404，客户端不能通过填写 user_id 或 role 改变权限。

| 方法 | 路径 | 成功码 | 权限与用途 |
| --- | --- | --- | --- |
| GET | `/api/v1/knowledge/documents` | 200 | 可见文档分页列表 |
| POST | `/api/v1/knowledge/documents` | 201 | admin 上传并尝试索引 |
| GET | `/api/v1/knowledge/documents/{document_id}` | 200 | 当前文档登记及索引状态 |
| GET | `/api/v1/knowledge/documents/{document_id}/download` | 200 | 受授权原件下载，attachment / nosniff |
| POST | `/api/v1/knowledge/documents/{document_id}/reindex` | 200 | admin 重新索引，同步等待 |
| DELETE | `/api/v1/knowledge/documents/{document_id}` | 204 | admin 软删除，退出检索 |
| POST | `/api/v1/knowledge/search` | 200 | 真实检索与证据门槛 |
| GET | `/api/v1/knowledge/citations/{chunk_id}` | 200 | 验证并取得当前合法 Chunk 引用 |

上传为 multipart/form-data：

| 字段 | 约束 |
| --- | --- |
| file | PDF、DOCX、TXT、.md 或 .markdown；普通文件名，拒绝路径字符 |
| title | 1–200 字符，不能全为空白 |
| visibility | public / internal，默认 public |

默认文件上限 8 MiB，入口另有请求体限制。内容必须真实符合声明格式；TXT/Markdown 仅严格 UTF-8，支持 BOM。损坏、空、加密 PDF、无可提取文本 PDF、非法 DOCX ZIP/XML 会拒绝，不执行 OCR。上传过程调用真实解析、Embedding 与索引，没有隐式模型下载。

201 表示原件已接受并登记；**不能仅根据 HTTP 成功码判断索引完成**。响应包含 id、title、file_type、checksum、version、visibility、status、index_generation、index_attempt_generation、chunk_count、index_error_code、indexed_at、created_at。上传后索引失败可以留下可重试的 failed 文档；重索引失败且已有发布代数时保留旧活动索引，并记录错误。路径及私有配置不会返回。

重索引有进行中冲突与有限执行预算。它不是后台任务提交接口，不返回可轮询 Agent Task，也没有独立 Worker 队列。更新原件要上传新文档，重索引不替换原件/checksum。

退款政策引用的来源文档不能删除，也不能替换标题、类型、路径、checksum、版本、可见性或删除状态；**允许重新索引**。Service 返回 policy_source_protected，数据库还通过独立 trigger 防止直接绕过。上传新政策文档不会改变现有结构化退款规则，也没有自动政策发布 API。

## 检索与引用

请求：

```json
{"query":"签收后的退款期限如何计算？","mode":"hybrid_rerank","top_k":5}
```

query 经清理后为 2–500 字符；拒绝额外字段与不合法控制字符。mode 支持 vector、hybrid、hybrid_rerank，默认 hybrid_rerank；top_k 取 1–10，默认 5，表示 Chunk 数量。

| 返回字段 | 含义 |
| --- | --- |
| status | evidence_found / insufficient_evidence |
| message | 证据可核对提示或无法确认说明，不是模型生成的政策答案 |
| answer_mode | 固定 evidence_only |
| citations | 通过当前权限、版本和相关性门槛的引用 |
| elapsed_ms / stage_ms | 实际 Service 耗时、向量/混合/重排阶段 |
| model_fingerprint | 当前索引与模型/分块兼容标识 |
| llm_calls / llm_input_tokens / llm_output_tokens | 当前路径为零，未调用生成模型 |

Citation 包含 document_id、document_title、document_version、index_generation、chunk_id、chunk_index、text、source_location、citation_url，以及实际可用的 vector_similarity、bm25_score、rerank_score。缺少某阶段的分数为 null，不填造结果。

source_location 保留一基页码、段落、表格位置或行号；char_start 为解析 section 内零基字符起点，char_end 为不包含的终点。TXT/Markdown Chunk 行号按实际范围细化。

Reranker 输出为 logit 经 sigmoid 得到的 0–1 未校准分数，不能当作概率。所有模式需通过向量相似度门槛；hybrid_rerank 还需同时通过重排分数门槛。evidence_found 只表示有候选通过相关性启发式，不能证明语义足够、政策无冲突或生成答案正确。当前没有查询改写、LLM 回答或 LangGraph 校验节点。

检索前后都用 SQL 权限和活动代数约束，引用详情再次校验。删除文档或发布新代数后，旧 chunk_id 返回 citation_not_found；无法访问的内部文档也不会通过引用路径泄露。

PowerShell 使用前文登录后的 customerHeaders：

```powershell
$searchBody = @{query='签收后的退款期限如何计算？'; mode='hybrid_rerank'; top_k=5} | ConvertTo-Json
$evidence = Invoke-RestMethod -Method Post -Uri "$apiBase/knowledge/search" -Headers $customerHeaders -ContentType 'application/json' -Body $searchBody
$evidence | Select-Object status, answer_mode, elapsed_ms, citations
```

Swagger 可用 admin 账号执行文件上传。生成真实文件并检查返回 status 与 chunk_count；不要将模拟文本粘贴后改扩展名当作 PDF/DOCX。

## 知识库错误

| 状态 | 示例 code | 含义 |
| --- | --- | --- |
| 403 | forbidden | 非 admin 执行维护 |
| 404 | document_not_found / citation_not_found | 不存在、不可访问或引用失效 |
| 409 | policy_source_protected / index_in_progress / document_source_changed | 政策来源、并发或原件冲突 |
| 413 | document_too_large | 上传/原件大小超过接口预算 |
| 422 | document_parse_failed / unsupported_document_type / document_chunk_limit | 格式、编码、内容或分块不合法 |
| 503 | rag_not_enabled / rag_models_unavailable / rag_timeout / vector_store_unavailable | 配置或真实依赖/执行不可用 |

底层解析错误不回显上传内容；上传、删除、索引开始、发布及失败记录必要审计。Chroma 客户端版本/距离配置不匹配时也会拒绝使用，不默默切到其他向量实现。
