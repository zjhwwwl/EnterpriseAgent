# Phase 2 验收记录

对应 EnterpriseAgent 0.2.0 / Phase 2，日期 2026-10-12。范围为数据库、认证、订单售后与本地基础设施；RAG / LangGraph / MCP / NL SQL / Vue 尚未实现。

## 已实现

- SQLAlchemy / psycopg 异步连接、15 张业务表、Alembic。
- 可复现模拟用户、商品、订单、物流、退款、工单、政策登记。
- Argon2、30 分钟 JWT、每请求读取数据库角色/启用状态/token_version。
- 订单查询、交易快照、物流、确定性退款资格和政策来源版本。
- 30 分钟确认，绑定用户、订单、金额、原因、政策、事实摘要。
- 幂等、事务级 advisory lock、订单行锁、有效退款唯一约束。
- 退款、工单、幂等、审计同事务；客服审核与模拟完成状态机。
- 发布政策不可修改，按支付时点选择最新生效版本，失效不回退。
- Redis 配置时登录限流，依赖故障安全拒绝。
- 基础设施 Compose、D 盘缓存/临时目录、安装/迁移/seed/检查/运行脚本。

## 已核实的环境与基础设施

| 项目 | 实际状态 |
| --- | --- |
| Python | D 盘项目虚拟环境，Python 3.13 |
| Docker Desktop | 4.95.0，D:\tools\DockerDesktop |
| Engine / Compose | 29.8.2 / 5.6.0，实际可调用 |
| Docker WSL 数据盘 | D:\DockerData，实际检查 |
| PostgreSQL / Redis | 17.11-bookworm / 8.4.7，healthy |
| 端口 | PostgreSQL 127.0.0.1:55432；Redis 127.0.0.1:56379 |
| 开发 / 测试库 | 独立数据库和普通非 owner 账号 |
| 跨数据库权限 | ea_app / ea_test_app 跨库 CONNECT 实测拒绝 |
| 迁移 | 开发、测试库 upgrade head：0001_ecommerce |
| 迁移一致性 | alembic check 实跑，无新 operations |
| 依赖 | 按哈希安装锁文件，pip check 通过 |

ea_local_admin 仅本地初始化，API 用 ea_app，测试用 ea_test_app。秘密在被 Git 忽略的 var，不在本记录保存。Redis 当前无 AUTH，仅 loopback，不能直接作为公网配置。

## 实际 seed 结果

AsOf=2026-10-12、Seed=42、Orders=500：

| 表 | 实际记录 |
| --- | --- |
| users | 25 |
| products | 30 |
| orders | 500 |
| order_items | 1016 |
| logistics | 456 |
| refund_requests | 19 |
| support_tickets | 19 |
| knowledge_documents | 5 |
| audit_logs | 41 |

5 文档为 uploaded，没有 Chunk、Embedding 或索引。历史退款为模拟事件，不是实际支付证明。重复 seed 不覆盖或重置已有记录。

同样参数重复运行种子后，25 / 30 / 500 / 1016 / 456 / 19 / 19 / 5 / 41 的记录统计保持一致，实测未重建或 reset。之后本地 Demo 登录新增一条 auth.login 审计，所以当前审计总数可大于初次 seed 的 41，不应据此认为 seed 发生变化。

## 自动化与 HTTP 验收

2026-10-12 的最终完整真实 PostgreSQL 运行：**191 项通过，0 失败，0 错误，0 跳过**，Ruff lint、62 文件格式检查与 pip check 通过。报告耗时 **176.147 秒**，生成时间 2026-10-12T15:06:43.2718068Z，required_postgres=true。该耗时是自动化总运行时间，不是请求性能或压力测试指标。

首次完整运行 185 项通过；权限审查后增加 6 项边界，再完整运行得到上述 191 项。新增验证包括必要权限缺失、列级审计修改权限、同库无关表权限与当前业务精确授权；最终结论以第二次完整检查为准。

其中 2 项真实 TCP / Uvicorn e2e 通过：空数据库服务存活但 readiness=503；真实独立测试库上的登录 → 客户归属 404 → 预览 → 确认 → 幂等重放 → 客服批准 → 模拟完成。最终断言订单交易状态仍为 delivered，没有直接改订单状态。

收尾增强 Windows 测试服务器的进程树清理和端口释放断言后，相关 **2 项重新运行全部通过**，pytest 输出 11.42 秒，JUnit 精确时间 11.413 秒，0 失败/错误/跳过，报告为 var/test-results/phase2-http-cleanup.xml。两项均明确确认测试服务器已停止监听，不仅确认 launcher 退出。该增强后的改动按影响范围重跑 e2e，没有把两项重跑虚构为又一次完整 191 项运行。

最初的增强探测使用 0.2 秒连接超时，两个用例因未及时收到连接拒绝而失败；独立诊断显示 Windows 已关闭端口约 2.022 秒后才返回 WSAECONNREFUSED / 10061。探测预算调整为 5 秒、每次使用剩余预算后，上述两项通过。问题是探测等待条件，未发现测试服务残留监听。Ruff 与 62 文件格式检查随后再次通过。

项目 PowerShell 脚本静态解析均通过。stop.ps1 另外核实了实际登记 PID 的项目虚拟环境 / main.py 身份检查，但本轮没有执行该脚本停止保留的 Demo API；不能将静态检查或测试服务器清理结果作为 stop.ps1 实际停止成功的证明。

入口 scripts/check.ps1 默认使用独立真实 PostgreSQL 并传 --require-postgres；结果位于 var/test-results/phase2.xml 和 phase2.json。-SkipPostgres 或缺库跳过的直接 pytest 不作为完整验收。

## 实际开发库 Demo HTTP

2026-10-12T15:05:12.8058316Z 在本机 127.0.0.1:8000 验证，原始记录为 var/test-results/phase2-demo-http.json：

| 检查 | 实际结果 |
| --- | --- |
| 服务版本 / 阶段 | 0.2.0 / 2 |
| readiness | application / database / redis 全部 ok |
| demo_customer 登录 | 200 |
| 订单 10086 | delivered，实付 13700 分，物流 delivered |
| 退款资格 | eligible=true，可申请 12900 分，不退 800 分运费 |
| 跨用户查询 10087 | 404 |
| Swagger | 200 |

本次开发库只执行读取和模拟账号登录，没有自动创建退款。退款写入闭环在独立测试库真实 HTTP 测试中验证，保持演示订单可供用户自行确认操作。

## 本地运行

```powershell
.\scripts\bootstrap.ps1
.\scripts\init-infra.ps1
.\scripts\migrate.ps1
.\scripts\migrate.ps1 -TestDatabase
.\scripts\seed.ps1 -AsOf 2026-10-12
.\scripts\check.ps1
.\scripts\run.ps1
```

run.ps1 注入本地应用配置并清除迁移/测试 URL。直接 main.py 需要自行配置环境，空示例 readiness=503。Swagger 业务流程见 [API](api.md)。

本次工作区在 8000 保留后台 Demo API。需要另起实例时先执行 scripts/stop.ps1，它读取 var/runtime/api.pid，验证进程仍为当前项目虚拟环境的 main.py 后停止其进程树；不按端口杀任意进程。交互式 run.ps1 用 Ctrl+C 关闭。

## 限制与未验证范围

已执行的 Phase 2 核心验证没有未解决失败；下列内容是明确的验证边界或后续阶段范围。

- Docker 只运行基础设施，API 仍为 Windows 本机；无应用 Linux 镜像和全栈验收。
- 没有 GitHub Actions 执行、GitHub 推送、公网部署或付费云资源。
- 业务确认不是 LangGraph interrupt，无模型 Tool Calling、Checkpoint 恢复或 Token 报告。
- 无 RAG、MCP、NL SQL、Vue 浏览器流程或 Evaluation 指标。
- 固定日期数据会过期；测试固定时钟，新空库演示可指定新日期。
- 只支持整单退款、一单一包裹，无真实支付/退款。
- stop.ps1 已验证静态解析和实际 PID 身份检查，尚未实际运行停止本次保留的 Demo API。

## 提交与下一阶段

阶段变更通过检查后提交到本地仓库，当前提交可执行 `git log -1 --oneline` 查看。本记录不写入自身提交哈希，避免更新记录改变该哈希。

Phase 3 实现四类文件解析、Chunk、Embedding、Chroma、BM25 + Vector、RRF、Reranker、引用、证据不足、删除与重索引。验收要求真实文档/模型/索引及相应测试，不编造效果或性能。
