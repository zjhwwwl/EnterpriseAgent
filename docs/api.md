# API 契约

**当前版本 0.7.0 / Phase 7。** Vue 五页后台消费现有健康、认证、订单、退款、知识库、LangGraph 会话与审批，以及授权聚合运营分析 HTTP 接口；本阶段不新增绕过 Service 的业务写入口。MCP 保留独立 stdio 三个只读工具，不注册 HTTP 路由，OpenAPI 仅覆盖 HTTP。当前契约以实际代码和 OpenAPI 为准，本阶段验收见[Phase 7](phase-7.md)，历史测试与在线烟测不改写为本阶段成绩。

## 浏览器集成约定

Vue 路由为 `/agent`、`/orders`、`/knowledge`、`/analytics`、`/tasks`，登录页为 `/login`。开发服务通过同源 `/api/v1` 和 `/health` 代理请求，API Client 仅接受本站 API 路径，附带内存 Bearer Token，不向任意 URL 转发凭据。Token 不写入持久浏览器存储；401 或有效期到期后需重新登录。前端角色显示不替代后端认证、实时权限和归属校验。

Agent 深链接支持 `/agent?session=<UUID>`；`message` 只预填最多 2000 字符草稿，不能自动创建会话或发送消息。任务历史支持 `/tasks?session=<UUID>&task=<UUID>`，分别筛选账号所属会话和打开所属任务。业务 POST 不自动重试；网络结果不明先查询持久状态。订单退款手动重试保留原 approval_id、决定和幂等键，模块内 Map 只保障当前页面运行周期，不替代后端事务。

任务失败按响应体 status/error_code 显示；知识上传和重新索引同时核对 status/index_error_code。引用核验按 chunk_id 请求认证 `/knowledge/citations/{id}`，原文文本插值；历史引用快照不会自动获得当前可见性证明。所有失败提示可显示安全错误及 request ID，不回显请求密码或 Token。

## 基础接口与认证

默认地址 `http://127.0.0.1:8000`，`EA_DOCS_ENABLED=true` 时提供 `/docs` 和 `/openapi.json`。所有响应携带 `X-Request-ID`，外部输入的请求 ID 会校验。

| 方法 | 路径 | 成功码 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 200 | 名称、版本、phase=7、文档及健康入口 |
| GET | `/health/live` | 200 | HTTP 进程存活 |
| GET | `/health/ready` | 200 / 503 | 实际数据库与已配置 Redis 的就绪检查 |
| POST | `/api/v1/auth/login` | 200 | JSON 账号密码，返回 JWT |
| GET | `/api/v1/auth/me` | 200 | 需要 Bearer Token，返回当前用户 |

就绪响应的 `checks` 包含 application、database；配置 Redis 时包含 redis，启用 RAG 时包含 knowledge，建立 Agent 持久化时包含 agent_checkpoint，启用分析时包含 analytics。数据库检查 Alembic 当前 head、业务必需权限和普通运行账号；库/表 owner、超级用户、public CREATE、订单状态 UPDATE 或审计 UPDATE/DELETE 等超额权限被拒绝。knowledge 检查固定本地模型文件及 Chroma 心跳，agent_checkpoint 检查官方 schema 版本与受限权限，analytics 检查专用角色、聚合视图、schema marker 与严格授权。健康不调用 DeepSeek，不代表模型配置或在线可用、所有文档已索引或每个问题都有答案。

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

## Agent 会话与任务

全部需要 Bearer Token，各角色只能访问自己的会话、任务与审批。会话绑定服务器记录的创建角色，角色变化需创建新会话；恢复为相同角色后可再次访问原合法会话。客服/管理员也不能通过 UUID 访问另一用户的任务。路由不授予业务权限，分析员仍不能调用订单工具。客户端不能提交 thread_id、user_id、role、graph 配置或任选恢复节点。

| 方法 | 路径 | 成功码 | 用途 |
| --- | --- | --- | --- |
| POST | `/api/v1/agent/sessions` | 201 | 创建服务端私有 thread 关联会话，空请求 |
| GET | `/api/v1/agent/sessions` | 200 | 自己的分页会话 |
| GET | `/api/v1/agent/sessions/{session_id}` | 200 | 自己的会话详情 |
| POST | `/api/v1/agent/sessions/{session_id}/messages` | 200 | 执行一次有预算的 LangGraph 任务 |
| GET | `/api/v1/agent/tasks` | 200 | 自己的任务，可用 session_id 筛选 |
| GET | `/api/v1/agent/tasks/{task_id}` | 200 | 状态、步骤、结果和使用量 |
| POST | `/api/v1/agent/tasks/{task_id}/resume` | 200 | 明确确认/拒绝当前任务关联审批 |
| POST | `/api/v1/agent/tasks/{task_id}/recover` | 200 | 显式从服务器 Checkpoint 核对并继续中断执行，空请求 |

分页默认 limit=20，范围 1–100，offset 范围 0–100000。SessionResponse 仅包含 id、current_task_id、created_at、updated_at，不暴露内部 thread。创建会话和 recover 只接受无请求体或空 JSON 对象，额外字段拒绝。Agent JSON POST 请求体限制 16 KiB。

消息请求仅接受 2–2000 字符的 message：

```json
{"message":"订单 10086 的商品不想要了，请申请无理由退款。"}
```

TaskResponse 包括 id、session_id、task_type、status、message、result、steps、token_usage、error_code、elapsed_ms 与时间。status 支持 running / waiting_approval / completed / failed / rejected / expired / cancelled；具体失败以 error_code 为准，不能把 HTTP 200 当作任务成功。result 包括 answer、approval、refund_result、citations、facts、warning_codes、analysis；初始或中断结果可能缺少部分字段。未配置模型时返回 503，不创建伪造回答。

Agent 退款参数只允许订单号、reason 与 notes；订单号限定 5–30 位 ASCII 数字。服务器先判断真实资格并限定适用政策文档检索，再准备审批。waiting_approval 时 result.approval 包含服务器计算的订单、金额、原因、期限、policy 和 requires_confirmation=true；尚未创建退款或工单。

人工核对后只提交：

```json
{"approval_id":"<result.approval.approval_id>","confirmed":true}
```

confirmed 必须是 JSON 布尔值，false 也记录拒绝与审计。Agent 确认幂等键由服务器根据 task_id 生成，不能由模型/请求体提供。确认记录须属于当前用户、当前任务并匹配持久化中断，服务器重查有效期、摘要和订单事实；成功后仅创建 pending_review。后续仍经现有客服审核和模拟完成接口处理。

同会话正在执行或待确认时返回 409。真实业务提交后若请求或 Checkpoint 中断，recover 核对持久状态：审批准备绑定 task_id，退款具有事务幂等保护；没有自动恢复 Worker。已完成任务重复相同确认返回原任务；改变决定或审批编号拒绝。提交后陈旧引用被移除并列入 warning_codes，数据库申请结果保留。

知识回答来自模型选择且验证过的连续原文；证据不足仅改写一次。订单回答由结构化事实生成，不采用模型自由文本。steps 记录节点/工具、耗时与安全错误；token_usage 累计 provider 报告的 prompt/completion/total 和 model_calls。未知消费记录 unknown_usage_calls、usage_complete=false，不伪造为零；中断的不完整标记跨 recover 保留，不因任务恢复成功而变为完整消费。

| 情况 | 状态/示例 code |
| --- | --- |
| 会话/任务不存在、归属或创建角色不匹配 | 404，agent_session_not_found / agent_task_not_found |
| 同会话已有任务或跨 worker 并发 | 409，agent_session_busy |
| 不匹配审批、非确认节点、任务已结束 | 409，approval_changed / agent_not_waiting_approval / agent_task_unavailable |
| 工作流、Checkpoint 或模型未配置 | 503，agent_unavailable / agent_model_not_configured |
| 图/工具/模型已执行但受规则终止 | Task.status=failed 与 error_code，如 invalid_tool_arguments / agent_tool_limit / insufficient_policy_evidence |
| 中断需恢复 | Task.status=failed 与 agent_timeout / agent_checkpoint_error，并明确提示恢复核对 |

运营分析接口和 MCP 契约见下文；Vue Agent、历史页面展示相同 TaskResponse。


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

Reranker 输出为 logit 经 sigmoid 得到的 0–1 未校准分数，不能当作概率。所有模式需通过向量相似度门槛；hybrid_rerank 还需同时通过重排分数门槛。evidence_found 只表示有候选通过相关性启发式，不能证明语义足够、政策无冲突或生成答案正确。该直接检索接口不调用聊天模型；查询改写、原文选择与回答验证仅在 Agent 会话路径执行。

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

## 运营分析接口

两个接口都需要 Bearer Token，当前数据库角色必须为 analyst / admin。customer / support 返回 `analytics_forbidden`，JWT 中的声称不能代替实时数据库角色。接口与 Agent 分析分支复用 AnalyticsService；以下直接查询不发送聊天模型请求，也不修改业务记录。

| 方法 | 路径 | 成功码 | 返回 |
| --- | --- | --- | --- |
| GET | `/api/v1/analytics/catalog` | 200 | 视图列与类型、指标口径、上海日期及服务端时间窗口 |
| POST | `/api/v1/analytics/query` | 200 | 实际 SQL 结果、确定性 Markdown 报告和图表规范 |

catalog 的静态目录不代表分析数据库就绪。它提供 `as_of_utc`、`today`、`business_timezone=Asia/Shanghai` 和左闭右开日期窗口：最近七天含今天、本月截至今天，以及用于比较的最近两个连续完整七天。今天不是完整自然日；完整七天窗口以今天为右端点，不是按周一划分的自然周。

query 请求使用 SQLPlan：sql 为 1–8000 字符，metric_ids 为 1–4 个 `paid_sales` / `refund_rate` / `order_count` / `product_sales`，label 可省略、最长 120 字符。strict / extra forbid 拒绝类型转换、额外参数和身份字段。label 不授予权限或改变 SQL 结果。

以下是固定日期的请求格式示例，**不是预填执行结果**；相对日期应使用当前 catalog 窗口：

```json
{
  "sql": "SELECT SUM(gross_revenue_cents) AS sales_cents FROM ea_analytics.daily_orders WHERE business_date >= CAST('2026-10-08' AS DATE) AND business_date < CAST('2026-10-15' AS DATE)",
  "metric_ids": ["paid_sales"],
  "label": "七天毛实付销售额"
}
```

授权来源只有 `ea_analytics.daily_orders` 与 `ea_analytics.daily_product_sales`，完整字段见 catalog。daily_orders 的支付订单计数应使用 `SUM(paid_order_count)`；`COUNT(*)` 统计视图行数，不能替代订单数。退款率示例表达式为 `SUM(completed_refund_order_count)::numeric / NULLIF(SUM(paid_order_count), 0)`，保留零分母 NULL。金额原始单位为人民币分，gross 包含运费且不扣退款；商品销售仅商品实付。退款率按所选支付日期订单群的当前模拟完成状态计算，不是期间退款发生次数 / 期间订单数。

SQL 必须是一个授权视图上的单条 PostgreSQL SELECT，显式写出合法字段。允许条件、有限算术、CASE、GROUP BY / HAVING / ORDER BY / LIMIT，以及 SUM、COUNT、AVG、MIN、MAX、ROUND、NULLIF、COALESCE 和有限 DATE / NUMERIC / 整数 CAST。聚合与计算结果须有独立有效别名。禁止其他表或系统目录、JOIN、CTE、子查询、UNION、窗口函数、多语句、写操作、锁、任意函数、SELECT *、SQL 注释；COUNT(*) 是唯一星号例外。AST 检查通过只证明受限执行边界，指标和时间范围是否符合问题仍需核对。

成功返回：

| 字段 | 实际含义 |
| --- | --- |
| answer | 从真实行、SQL 与登记口径构造的 Markdown；不新增自由文本模型分析 |
| query.normalized_sql | AST 验证后实际执行的 SQL，含服务端行数限制 |
| query.columns / rows | 原始结果列与受限数据；NUMERIC 为精确十进制字符串，日期为 ISO 字符串 |
| query.row_count / truncated | 返回行数与是否检测到超过上限；更小的用户 LIMIT 不推断完整总量 |
| query.elapsed_ms | 本次数据库执行路径耗时，不是模型或端到端耗时 |
| metric_ids / metric_definitions | 选择的登记指标及其口径；不自动证明 SQL 与指标语义匹配 |
| chart | 可为空；type 为 line / bar，labels、series、source=query_rows 来自实际 rows |

默认最多返回 100 行、20 列，配置可将行数调至最多 500；服务端最多读取上限加一行以判断截断。字符串单元格最多 512 字符，行数据 JSON 预算 100 KiB，超限明确拒绝。报告正文最多展示 20 行，完整受限结果保留在 query.rows。空结果不推断销量为零，截断不作为完整汇总。图表选首个非数值列或列名以 `_id` 结尾的列作为维度，维度可在后续列；所有 `_id` 列不加入数值 series。纯数值宽表保留全部可用 series，标签使用返回行序号；ISO 日期标签生成 line，其余为 bar，没有可用数值时 chart 为 null。Vue SVG 组件只消费该实际结果规范，图表显示前 30 行并保留原值提示；不追加业务原因或语义指标识别。

| HTTP 状态 | code | 场景 |
| --- | --- | --- |
| 403 | analytics_forbidden | 当前用户不具备 analyst/admin 角色 |
| 422 | analytics_sql_rejected / analytics_metric_invalid | SQL 执行边界或指标不合法 |
| 422 | analytics_result_too_large | 单元格、列数或负载超出展示预算 |
| 503 | analytics_unavailable / analytics_query_failed | 未启用、权限/视图未就绪或安全执行失败 |
| 504 | analytics_query_timeout | statement / lock / 总时间预算耗尽 |

自然语言入口沿用 `POST /api/v1/agent/sessions/{id}/messages`，例如 `{"message":"本月退款率是多少？"}`。模型路由到 analytics 后通过 named `plan_analytics_sql` 返回严格 SQLPlan；后端授权、验证并执行，再将报告同步到 Task 的 `result.analysis`。普通消息中的用户/角色/当前日期声明不能替代服务端身份与时间。该路径需要实际聊天模型配置；直接 SQL 查询不需要 Key。分析是只读，不进入退款人工确认节点，仍使用同一私有会话、官方 Checkpoint、步数和有限重试规则。

## MCP stdio 契约

独立入口为 `python -m enterprise_agent.mcp.server` 或安装后的 `enterprise-agent-mcp`。官方 SDK 1.30.0 通过 stdin/stdout 传输 JSON-RPC，客户端先 initialize，再 tools/list 与 tools/call；该入口没有 HTTP 地址、OpenAPI 页面或 OAuth 服务。Server 启动需要进程环境中的普通 `EA_DATABASE_URL`、`EA_JWT_SECRET`、`EA_MCP_ACCESS_TOKEN`；不读取 `.env`，不传递分析数据库凭据或启用分析 runtime。政策证据还需要启用并准备 RAG。

| MCP 工具 | arguments | 共享入口 | 返回 data |
| --- | --- | --- | --- |
| query_order | order_no | BusinessTools.get_order / OrderService | 订单状态、整数分金额及现有字段 |
| query_logistics | order_no | BusinessTools.get_logistics / OrderService | 数据库已有物流状态、运单与事件 |
| query_refund_policy | order_no、可选 reason | BusinessTools.get_refund_policy / RefundService / KnowledgeService | eligibility 与限定政策文档的 knowledge |

order_no 必须为 JSON 字符串，包含 5–30 位 ASCII 数字；数字类型、全角数字或额外字段拒绝。reason 默认 change_mind，可选 quality_issue / not_received。工具发现包含共享 inputSchema、outputSchema 和只读/非破坏性注解；任何未知工具或写入工具都不会分派。注解不替代白名单、Service 权限或事务规则。

调用参数示例：

```json
{"name":"query_refund_policy","arguments":{"order_no":"10086","reason":"change_mind"}}
```

这是 tools/call 的 params，完整 JSON-RPC 请求由 SDK 生成。成功结果 `isError=false`，structuredContent 为 `{"data": ...}`，content 同时包含该对象的 JSON 文本。data 使用实际 Service 返回；eligible 只表示是否可申请待审核退款。knowledge 保留 status、citations、来源位置与耗时，证据不足不会编造政策；没有适用政策时 knowledge 可为 null。存在政策但未启用 RAG 时返回 rag_not_enabled。

每次调用由 AuthService 重新认证启动 JWT，核对数据库当前角色、启用状态及 token_version。客户只能查自己的订单；客服/管理员使用既有查询权限，分析员不能读订单明细。arguments 或 `_meta` 的角色、用户、token 和确认字段不被信任；修改订单 ID 不会绕过归属。

业务错误以 `isError=true` 返回，structuredContent 与 content 均使用以下安全结构；status_code 为业务语义，不是 HTTP 响应：

```json
{"error":{"code":"order_not_found","message":"订单不存在。","status_code":404}}
```

| 业务状态 | 示例 code | 含义 |
| --- | --- | --- |
| 401 | unauthenticated | JWT 无效、到期、撤销或用户不可用；沿用 AuthService |
| 403 | forbidden | 当前角色不允许订单查询 |
| 404 | order_not_found | 不存在或无权访问的订单，统一返回 |
| 422 | invalid_tool_arguments / unknown_tool | 参数类型、格式、额外字段或工具白名单不符 |
| 503 | rag_not_enabled / mcp_request_timeout / mcp_tool_unavailable | 政策证据不可用、认证/业务超时或安全内部错误 |

客户端应先检查 isError，再读 data；JSON-RPC 协议错误与读取超时可能由 SDK 抛 McpError，不是成功业务结果。业务 handler 不回显原始参数、Pydantic 输入、SQL/DSN 异常或未知工具名。默认服务端每次认证和查询共 15 秒，客户端示例总预算 60 秒。MCP 不创建退款、工单或审批；敏感写入继续使用已有人工确认接口。

可运行 Client 示例与环境设置见[部署](deployment.md)，实际协议、权限、真实子进程退出验证见[Phase 5](phase-5.md)。
