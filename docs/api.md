# API 契约

**当前版本 0.2.0 / Phase 2。** 已实现基础健康、认证、订单和退款接口。RAG、Agent、任务、MCP、知识库管理和分析 API 尚未提供。OpenAPI 与实际代码是当前契约来源。

## 基础接口与认证

默认地址 `http://127.0.0.1:8000`，`EA_DOCS_ENABLED=true` 时提供 `/docs` 和 `/openapi.json`。所有响应携带 `X-Request-ID`，外部输入的请求 ID 会校验。

| 方法 | 路径 | 成功码 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 200 | 名称、版本、phase=2、文档及健康入口 |
| GET | `/health/live` | 200 | HTTP 进程存活 |
| GET | `/health/ready` | 200 / 503 | 实际数据库与已配置 Redis 的就绪检查 |
| POST | `/api/v1/auth/login` | 200 | JSON 账号密码，返回 JWT |
| GET | `/api/v1/auth/me` | 200 | 需要 Bearer Token，返回当前用户 |

就绪响应的 `checks` 包含 application、database；配置 Redis 时包含 redis。数据库检查连通、Alembic head、业务所需读取/写入权限和普通运行账号。数据库 owner、表 owner、超级用户、public CREATE、订单状态 UPDATE 或审计 UPDATE / DELETE 等超额权限会被拒绝，缺少业务必需权限也返回 503。未配置、未迁移或依赖故障不算就绪，不暴露连接信息，也不检查尚未实现的 Chroma / 模型。

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

知识库上传/删除/重新索引、Agent 会话与执行步骤、LangGraph 中断恢复、MCP 和自然语言分析在对应阶段实现。本阶段的业务确认尚不是 LangGraph interrupt，也不提供可任意提交 resume 命令的 HTTP 接口。
