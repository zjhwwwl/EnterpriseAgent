# API 契约

**本页已实现接口仅为 Phase 1 基础 HTTP API。** 登录、订单、退款、知识库、任务和分析接口暂未提供，后面的业务接口内容是设计方向。

## 启动与 OpenAPI

默认地址为 `http://127.0.0.1:8000`。`EA_DOCS_ENABLED=true` 时提供 `/docs` 和 `/openapi.json`；关闭后这些文档入口不可用。运行服务的实际请求和 OpenAPI 是当前契约的直接来源。

所有接口返回 `X-Request-ID`。服务对外部传入的请求 ID 做校验，不直接信任任意文本；客户端可用响应中的 ID 关联问题。

## 已实现接口

| 方法 | 路径 | 成功码 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 200 | 服务名称、版本、阶段与文档/健康入口 |
| GET | `/health/live` | 200 | 应用进程能响应请求 |
| GET | `/health/ready` | 200 / 503 | 应用 lifespan 已启动为 200，未启动/已关闭为 503 |

这些基础接口暂不需要身份认证。当前没有受保护的业务资源，不能由此推断后续订单接口会公开。

### 服务信息

`GET /` 在默认配置下返回：

```json
{
  "name": "EnterpriseAgent",
  "version": "0.1.0",
  "phase": 1,
  "docs_url": "/docs",
  "health_url": "/health/ready"
}
```

关闭文档时 `docs_url` 为 `null`。这里的 `phase` 用于说明当前交付范围，不表示后续业务能力已存在。

### 存活和就绪

正常启动后的健康响应：

```json
{
  "status": "ok",
  "service": "EnterpriseAgent",
  "version": "0.1.0",
  "phase": 1,
  "checks": {
    "application": "ok"
  }
}
```

`/health/ready` 在应用生命周期尚未启动或已经关闭时返回 HTTP 503：

```json
{
  "status": "not_ready",
  "service": "EnterpriseAgent",
  "version": "0.1.0",
  "phase": 1,
  "checks": {
    "application": "not_ready"
  }
}
```

Phase 1 只检查应用生命周期。配置中即使填写了数据库或模型地址，也不会连接这些服务。后续增加依赖健康项时会同步更新本契约。

## 错误格式

HTTP 错误、输入校验错误和未处理异常采用同一外层结构：

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "请求的资源不存在",
    "details": []
  },
  "request_id": "响应中的请求关联 ID"
}
```

`details` 对请求校验错误只提供 `loc`（字段位置）和 `type`（错误类型），不包含原始输入和可能带敏感内容的校验消息。其他错误默认空数组。未知异常不会把栈、文件路径、密钥或数据库连接内容返回给用户。

| HTTP 状态 | `error.code` | 含义 |
| --- | --- | --- |
| 404 | `NOT_FOUND` | 资源或路由不存在 |
| 405 | `METHOD_NOT_ALLOWED` | 请求方法不支持 |
| 422 | `REQUEST_VALIDATION_ERROR` | 请求参数不符合 schema |
| 500 | `INTERNAL_SERVER_ERROR` | 服务内部错误，使用请求 ID 定位 |
| 其他 HTTP 异常 | `HTTP_ERROR` | 标准 HTTP 异常 |

就绪检查的 503 使用上面的健康响应模型。CORS 预检由协议中间件处理；不允许的 Origin、方法或 Header 可返回 HTTP 400 文本响应，不使用业务 API 的错误 envelope。

## 请求与日志

- 日志采用 JSON，用请求 ID 关联请求和响应。
- 不记录请求体、查询字符串、Authorization 或整份配置。
- 日志提供定位问题所需的安全信息，不能成为敏感输入的副本。
- CORS 通过 `EA_CORS_ORIGINS` 的 JSON 数组配置，使用精确 Origin；浏览器跨域限制不替代认证与授权。
- 合法 Origin 的安全 500 响应也携带对应 CORS 头，前端可以读取错误和 `X-Request-ID`；未授权 Origin 不得到此许可。

## 本机调用示例

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/'
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/ready'
$response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health/live' -UseBasicParsing
$response.Headers['X-Request-ID']
```

## 后续业务 API 设计

以下只表示能力分组，**当前不能调用**。落地时统一使用 `/api/v1`，再明确路径、请求模型、状态码和错误 code。

| 分组 | 计划能力 | 权限与约束 |
| --- | --- | --- |
| 认证 | 登录、当前用户 | 密码哈希、短时效 JWT |
| 订单 | 查询订单、明细、物流 | 当前客户归属；客服按权限访问 |
| 退款 | 资格检查、待审核申请、状态、审核 | 用户确认、事务复查、幂等、审计 |
| 知识库 | 上传、列表、删除、重新索引 | 管理员写入，检索按可见性过滤 |
| 会话/任务 | 多轮消息、任务状态、步骤详情 | 所有者过滤，线程串行执行 |
| 人工确认 | 确认或拒绝待执行操作 | 绑定用户、任务、操作摘要与有效期 |
| 分析 | 自然语言查询、结果与图表数据 | 角色授权、只读连接、AST 白名单 |

退款提交计划使用幂等键；同键相同参数返回原结果，同键不同参数返回 409。确认接口接收审批记录标识与决策，由服务端构造 LangGraph resume 内容，客户端不能直接提交任意图命令。

响应来源和工具步骤将通过结构化模型提供给前端；模型原始文本不能替代业务状态。敏感数据修改不能通过 GET 执行。
