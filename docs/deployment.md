# 本地运行与部署

## 实际支持范围

Phase 9 / 0.9.0 保留 **Windows 本机 FastAPI / LangGraph / CPU ONNX / 独立 MCP stdio / 只读运营分析 / Vue 管理后台 / 评估与元数据追踪 + Linux 基础设施容器**，新增独立 Linux 全栈 Compose、API / web Dockerfile、Linux 哈希锁与 GitHub Actions 工作流。实际容器与测试验收按[Phase 9](phase-9.md)记录；历史浏览器、MCP、SQL 和在线 Agent 结果保留。交付 workflow 文件不代表已在 GitHub 平台执行，也没有公网发布或生产部署证明。

默认仅绑定 loopback，使用虚构数据与公开演示账号。没有真实支付、退款或付费云资源。

## D 盘与依赖

| 内容 | 本工作区位置 |
| --- | --- |
| 项目、.venv | D:\Python\PycharmProjects\EnterpriseAgent |
| Docker Desktop 程序 | D:\tools\DockerDesktop |
| 实际 Docker / WSL 数据盘 | D:\DockerData |
| PostgreSQL / Redis / Chroma 数据 | D:\DockerData\enterpriseagent 下分目录 |
| 模型 | 项目 var/models |
| pip/HF 缓存、TEMP/TMP | 项目 var/cache、var/tmp |
| 原件、秘密、报告 | 项目 var，Git 忽略 |
| 前端依赖/构建 | 项目 frontend/node_modules、frontend/dist，Git 忽略 |
| npm / 浏览器测试缓存 | 项目 var/cache 下；具体安装与检查以脚本为准 |

Docker 程序与实际数据盘分别配置，少量 Windows 用户配置仍可能在 C 盘；本项目不另装 Ubuntu。基础设施镜像为 PostgreSQL 17.11-bookworm、Redis 8.4-alpine、Chroma 1.5.9。原开发方式在 Windows 运行 API 和本地推理；全栈方式使用 Linux Python 3.13.15-slim-bookworm、Node 24.19.0 构建与 Nginx 静态站点。基础设施心跳、应用依赖安装和真实推理分别验证，不相互替代。

Windows Python 3.13 项目虚拟环境须先可用。bootstrap 按 requirements/dev.lock 的哈希安装依赖，再以 --no-deps --no-build-isolation 可编辑安装并运行 pip check，不安装 Python 或 Docker、不覆盖已有 .env。Linux 镜像使用 requirements/linux-base.lock 与 linux-dev.lock，通过 --require-hashes --only-binary=:all: 安装，版本与实际安装证据见本阶段记录。

Phase 5 固定官方 mcp 1.30.0，Windows stdio 子进程所需 pywin32 保留在 Windows 锁。Linux 锁在真实目标平台重新解析，同一 MCP SDK 按平台条件选择依赖，不把 Windows lock 直接用于 Linux 镜像。

Phase 6 固定 SQLGlot 30.22.0，使用 PostgreSQL AST 白名单；显式依赖 tzdata 以支持 Windows 的 ZoneInfo / Asia/Shanghai。依赖锁保持已固定版本，新增依赖仍通过哈希校验；不能仅据锁文件声称完成 Linux 应用安装。

## 首次启动

以下命令保留 Windows 开发方式；独立全栈容器方式见后面的“Linux 全栈 Compose”说明，两套项目的秘密、端口和数据分别管理。

```powershell
Set-Location 'D:\Python\PycharmProjects\EnterpriseAgent'
.\scripts\bootstrap.ps1
.\scripts\init-infra.ps1
.\scripts\init-analytics.ps1
.\scripts\migrate.ps1
.\scripts\migrate.ps1 -TestDatabase
.\scripts\seed.ps1
.\scripts\prepare-rag.ps1
.\scripts\index-knowledge.ps1
.\scripts\evaluate-rag.ps1
.\scripts\check.ps1
.\scripts\run.ps1
```

| 脚本 | 实际行为 |
| --- | --- |
| bootstrap | 校验 Python 3.13、哈希安装、可编辑安装、pip check |
| init-infra | 创建未存在的本地随机秘密，启动基础设施；保留原配置，仅同步两个分析连接字段 |
| init-analytics | 从容器挂载秘密初始化/旋转两个专用 reader；不重置数据库或修改业务/owner 密码 |
| migrate | 开发库升级 head；-TestDatabase 处理专用测试库；-Check 检查模型差异 |
| seed | 虚构业务数据、默认当天 UTC/seed42/500订单；不覆盖已 seed 库 |
| prepare-rag | 显式准备固定模型文件与 manifest，首次可联网下载公开文件 |
| index-knowledge | 普通应用连接+模拟 admin，复用 Service 索引已登记文档；兼容活动索引跳过 |
| evaluate-rag | 普通应用连接+模拟 customer，固定 corpus 的真实检索对照 |
| evaluate-analytics | 专用测试 reader 真实执行人工 SQL，迁移账号只读取得独立基表参考；不调用模型 |
| evaluate-analytics-live.py | 显式 --allow-live 后通过 loopback API 跑四个模型规划开发烟测，保存实际 Task / 参考行 / usage |
| check | 默认 require-postgres / require-rag，Ruff、pytest、pip check、JSON/JUnit |
| run | 注入应用及专用分析配置，显式启用 RAG/analytics、移除迁移/测试 URL，再运行 main.py |
| mcp-demo | 注入普通业务配置，在 loopback API 登录公开模拟客户，经官方 stdio Client 调用三个只读工具 |

当前索引脚本没有选择单文档参数，处理所有未删除的登记文档；要强制重索引某篇已兼容文档，可用 admin 的 reindex API。

## Vue 后台启动与检查

需要 Node.js 24，前端 package.json 限定 `>=24.15.0 <25`，依赖由 `frontend/package-lock.json` 固定。本工作区复用现有 PyCharm Node.js 运行时，不另装 Node；新增依赖和缓存位于 D 盘项目。先确保 FastAPI 已在本机运行，再在新的 PowerShell 终端执行：

```powershell
.\scripts\frontend.ps1 -Mode Install
.\scripts\frontend.ps1 -Mode Run
```

访问 `http://127.0.0.1:5173`，使用 README 中的公开模拟账号。`Install` 根据锁文件安装依赖；`Run` 启动 Vite；`Check` 执行格式检查、Vitest、TypeScript/Vite 构建和生产依赖 audit，测试 JSON 写入 `var/test-results/phase9-frontend-unit.json`：

```powershell
.\scripts\frontend.ps1 -Mode Check
```

Vite dev/preview 均仅绑定 `127.0.0.1:5173`，默认代理到 `http://127.0.0.1:8000`。PowerShell 包装脚本使用 `-ApiTarget`，例如 `-ApiTarget 'http://127.0.0.1:18000'` 可指定已经运行的专用本机 API；它注入 `EA_WEB_API_TARGET`。直接运行 Vite 时也可显式设置该变量。两层配置都只接受无用户名、密码、路径、query 或 fragment 的 HTTP loopback origin，不允许公网代理。该配置只提供 API origin，不填写 Key、JWT 或数据库 URL。`frontend/dist` 是本地构建产物，不能当作已发布站点或生产部署证明。

### 真实浏览器业务验收

先完成测试库迁移、真实 RAG 模型准备和本机模型配置，再执行：

```powershell
.\scripts\frontend.ps1 -Mode Check
.\scripts\verify-frontend.ps1 -AllowLive
```

`-AllowLive` 显式接受配置模型的实际供应商调用，可能消耗余额。验证脚本只接受本机 `enterprise_agent_test` 和指定测试业务/reader 角色，数据准备另验证测试迁移角色；增补模拟数据并真实索引，不重置开发库。测试 Chroma 前缀为 `ea_frontend_test`，数据目录为 `var/frontend-test`，Redis 使用 DB 1。

脚本启动 API `127.0.0.1:8001` 和生产构建预览 `127.0.0.1:5174`，拒绝覆盖已有监听；结束只停止记录且核对 executable/参数的子进程。Playwright 使用现有 Edge，1440 × 960、Asia/Shanghai，不下载额外浏览器；npm/TEMP/浏览器缓存配置仍位于 D 盘。原始摘要、截图和日志写入 `var/test-results/phase7-browser`，上一轮产物按摘要哈希保存在 `phase7-browser-trials`，专用 JWT 秘密不提交。

`-Prepared` 仅在已有准备摘要通过隔离检查时跳过数据准备；`-ResumeTask <UUID>` 用于测试同一库中仍有效、属于模拟客户的待审批任务，脚本检查实际持久状态后才允许显式确认。重新运行会创建或修改虚构测试记录；模型数据日期及审批期限需核对。真实结果见[Phase 7](phase-7.md)和[验证报告](../evals/results/phase7/report.md)，不能把测试库流程当作生产部署证明。

完整流程已留下持久任务后，可不调用模型重拍页面：

```powershell
.\scripts\verify-frontend.ps1 -ScreenshotsOnly
```

该模式仍核对专用测试环境，但不执行数据准备、seed 或重新索引；要求此前的准备摘要以及知识、退款、分析历史任务已存在。测试 API 显式关闭聊天模型，只允许 GET、模拟账号登录和授权只读 SQL；禁止消息/resume/recover/业务修改，不需要 `-AllowLive`。本次实际保存八张截图，1280/1366/1440 宽度检查无横向溢出且新增模型请求为零，证据见[screenshots.json](../evals/results/phase7/screenshots.json)。退款回执保留提交时状态，当前审核状态从订单页读取；来源核验另有 citation.png。

前端 Token 只保存在运行时内存；刷新页面、有效期到期或 401 后重新登录。订单售后原幂等键保存在按账号区分的模块内 Map，支持同一页面运行周期的跨路由核对，不是持久数据库。关闭/刷新页面后从后端列表核对实际审批和退款记录，不能通过反复创建预览当作恢复。普通订单/知识/分析接口不调用聊天模型；Agent 显式发送或执行恢复才可能消耗 Token。

从已有 Phase 5 工作区升级，先用 `scripts/stop.ps1` 或 Ctrl+C 停止本项目 API，再执行 init-infra → init-analytics → migrate（开发与测试库），随后重启。新 Compose secret 挂载可能重建 PostgreSQL 容器，持久数据目录保留；不要使用 down -v 或删除数据目录。reader 在迁移前已存在时，0005 自动授予两个视图的读取权限；若先迁移，可再幂等运行 init-analytics 补授已存在视图。脚本不自动回滚业务数据或降级迁移。

## 模型准备与离线使用

资产由 backends.py 固定：

| 组件 | 仓库 | revision |
| --- | --- | --- |
| 中文 Embedding | Qdrant/bge-small-zh-v1.5 | 46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59 |
| 量化 Reranker | Xenova/bge-reranker-base | 280bcc27a84e0b898c251e06fddb25171bd9b101 |

模型文件与 tokenizer 等资产合计 396,800,683 字节，manifest 另外保存准备时间及推理参数。目录包含 embedding/、reranker/ 和 manifest.json，每项大小与 SHA256 必须符合源码固定值。没有把完整 Hugging Face 缓存或真实 Key 提交到仓库。

```powershell
# 只检查已准备文件，不下载
.\.venv\Scripts\python.exe scripts\prepare-rag-models.py --verify-only

# 从可信已下载目录复制并校验
.\.venv\Scripts\python.exe scripts\prepare-rag-models.py --offline-source 'D:\dev-cache\enterpriseagent-models'
```

默认目的地读取 EA_RAG_MODEL_DIR 或项目 var/models，可用 --destination 显式指定。prepare-rag.ps1 设置本项目 HF_HOME；直接运行 prepare Python 脚本仍使用指定本地目录。

查询/启动不下载模型。准备后通过 FastEmbed / ONNX Runtime 的 CPU provider、本地文件模式加载；第一次推理包含模型载入成本。评估每模式 warmup 单独记录，不能把 warmup 时间藏进“零冷启动”。

## 服务与秘密

| 服务 | loopback 地址 |
| --- | --- |
| API | 127.0.0.1:8000 |
| PostgreSQL | 127.0.0.1:55432 |
| Redis | 127.0.0.1:56379 |
| Chroma | 127.0.0.1:58000 |

秘密保存在被 Git 忽略的 var/infra/、var/infra-secrets.env；.env 也不提交。初始化脚本不覆盖既有秘密。运行账号 ea_app 非 owner/超级用户，ea_test_app 只能连接独立测试库；初始化账号仅用于迁移/seed。不要把测试 URL 指向开发库，测试夹具会重置专用测试记录。

API 没有订单状态/金额写权限，政策只能读，审计仅追加。确认、退款、工单、幂等、agent_tasks/agent_sessions 具有迁移明确授予的 DML。Phase 3 另授权知识登记 INSERT/UPDATE 和 Chunk INSERT/DELETE，政策来源仍被独立 trigger 保护。0003_agent 由迁移账号建立官方 Checkpoint schema 与版本记录；运行账号不能 DDL、删除检查点或变更 SDK 版本，应用不调用 saver.setup()。

Redis 与 Chroma 当前没有公网认证方案，仅 loopback 隔离。需要公网部署时必须另行完成生产配置与验证，不能直接使用公开模拟账号。文档、脚本及日志不打印真实 URL、JWT Secret、模型 Key 或完整环境。

## 运营分析配置与账号

`EA_ANALYTICS_ENABLED` 在 Settings/模板中默认 false，`run.ps1` 显式启用。启用时必须配置专用 `EA_ANALYTICS_DATABASE_URL`；业务数据库、reader 视图或授权不满足要求时，就绪失败并拒绝查询，不回退到普通业务/迁移账号。直接分析 API 无需 DeepSeek，Agent 自然语言规划仍需真实模型配置。

| 变量 | 默认值 / 范围 |
| --- | --- |
| EA_ANALYTICS_ENABLED | false，建立专用分析 runtime |
| EA_ANALYTICS_DATABASE_URL | 空，开发库 ea_analytics_reader 的 postgresql+psycopg URL |
| EA_TEST_ANALYTICS_DATABASE_URL | 空，专用测试库 ea_test_analytics_reader；不注入运行 API/MCP |
| EA_ANALYTICS_STATEMENT_TIMEOUT_MS | 5000，范围 100–10000 ms |
| EA_ANALYTICS_MAX_ROWS | 100，范围 1–500 |
| EA_ANALYTICS_MAX_SQL_CHARS | 8000，范围 128–16000；API/Agent SQLPlan 另有固定 8000 输入上限 |

`init-infra.ps1` 在 D 盘项目 `var/infra/` 创建 postgres-analytics-password / postgres-test-analytics-password，只在文件不存在时生成 64 字符随机 hex；同步 ignored `var/infra-secrets.env` 的两个分析 DSN，保留其余已有字段与注释。Compose 把密码文件只读挂载给 PostgreSQL，`init-analytics.ps1` 通过 psql stdin 与 pg_read_file 读取，密码不进入 CLI 参数或输出。

专用账号分别获得两个项目库的 CONNECT，不允许交叉连接开发/测试库；只显式授权 ea_analytics schema USAGE 和 daily_orders / daily_product_sales SELECT。它们无角色成员关系、owner、超级用户、建库/建角色、schema CREATE、基表/列和 sequence 权限。默认事务只读、statement_timeout=5s、lock_timeout=1s、idle_in_transaction_session_timeout=10s；查询 runtime 再施加本次限制。初始化撤销这两个项目库 PUBLIC TEMP，保留既有 public schema PUBLIC USAGE 和系统数据库默认权限。系统元数据仍有 PostgreSQL 的默认可见性，应用层 AST 只接受两个授权聚合视图。

旋转专用密码时仅替换对应 ignored hex 文件，再运行 init-infra 同步 DSN/挂载、init-analytics 更新专用角色并重启 API；不要修改业务/owner 密码文件或打印配置。新旧已有数据库都不清空，重复初始化按当前密码文件与明确 ACL 幂等执行。

分析输出按上海支付日期统计；退款为查询时模拟完成状态，不是历史时间点快照。普通报表金额精确保留十进制字符串。Vue 分析及任务详情渲染返回的图表 spec，原始受限结果保留在表格；实际浏览器验收状态见本阶段记录。

## 运营分析评估命令

先完成专用测试库迁移及 `check.ps1` 的固定模拟数据准备，再运行人工 SQL 对照；它不 seed、不清空数据库、不读项目 .env 或调用聊天模型：

```powershell
.\scripts\evaluate-analytics.ps1 -Output 'var/evaluation/sql-review'
```

脚本仅保留本次需要的测试 reader 与迁移参考连接，参考读取也受事务/超时限制，退出时恢复原进程 EA 配置。产物为指定目录的 results.json / report.md，包含实际结果、独立基表参考、数据指纹、拒绝和耗时。人工 SQL 的匹配率不是自然语言转 SQL 分数。

真实在线开发烟测使用已经启动并配置模型的本机 API，必须显式传 `--allow-live`；这会触发现有 Agent 的实际供应商调用。运行前核对 PyCharm 对 main.py 配置的 Python 3.13 项目 SDK，使用项目 `.venv`，不安装或切换全局解释器：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate-analytics-live.py --allow-live --model-label 'deepseek-flash' --output 'var/evals/analytics-live-review-1'
```

脚本只接受 loopback HTTP origin，不跟随重定向/环境代理，不通过 CLI 获取或打印 Key；在本机登录公开 demo_analyst，经 HTTP 创建自己的会话并调用现有 Agent。`--model-label` 只是操作者标签，不能证明 API 未暴露的供应商真实身份。输出必须是项目 var/evals 下新的目录，已有 results.json / cases.csv / report.md 会拒绝覆盖。实时 canonical SQL 与 Agent 请求不共享快照；业务日期漂移判失败，失败或不完整消费保留，不能补零 Token。

固定 SQL 数据集为 [analytics-sql.json](../evals/datasets/analytics-sql.json)，四个 schema 输出合同场景为 [analytics-live-smoke.json](../evals/datasets/analytics-live-smoke.json)。它们是开发检查，不能冒称盲评或开放式模型质量。当前已归档结果及默认重试恢复记录见[Phase 6](phase-6.md)和[Evaluation](evaluation.md)。

## 知识检索配置

操作系统环境覆盖 .env。完整模板为 [.env.example](../.env.example)，包括数据库、Redis、JWT、本地检索和 Agent 字段。EA_RAG_ENABLED 在 Settings/模板中默认 false；run、索引和评估脚本显式启用，直接 main.py 时需自行启用。

| 变量 | 默认值与含义 |
| --- | --- |
| EA_RAG_ENABLED | false；是否建立 RAG runtime |
| EA_RAG_MODEL_DIR | var/models，相对项目根解析，优先 D 盘 |
| EA_RAG_MODEL_THREADS | 2，配置范围1–8，runtime最多使用4 |
| EA_CHROMA_HOST / PORT | 127.0.0.1 / 58000 |
| EA_CHROMA_TIMEOUT_SECONDS | 10，范围0.1–30；SDK各传输阶段有限超时 |
| EA_RAG_COLLECTION_PREFIX | ea_knowledge，和模型 fingerprint 组成集合名 |
| EA_RAG_CHUNK_SIZE / OVERLAP | 500 / 80，单位为字符；overlap < size |
| EA_RAG_TIMEOUT_SECONDS | 120，模型等待及执行预算 |
| EA_RAG_UPLOAD_MAX_BYTES | 8388608，8 MiB，配置最多16 MiB |
| EA_RAG_MAX_CHUNKS | 1000，单文档预算 |
| EA_RAG_MIN_VECTOR_SIMILARITY | 0.45，相关性启发式 |
| EA_RAG_MIN_RERANK_SCORE | 0.05，sigmoid 后的未校准分数门槛，不是概率 |

直接检索与 retrieval evaluation 不发送聊天模型请求。Agent 配置如下。

## DeepSeek 与 Agent 配置

在本机被忽略的 .env 中填写自己的 EA_DEEPSEEK_API_KEY，再设置 EA_LLM_ENABLED=true 并重启。不要在 shell 命令、截图、日志或 Git 中暴露 Key。API 启用时未填写 Key 会在配置校验拒绝启动；默认 false 时业务和检索可运行，会话历史可查询，但 Agent 消息/执行恢复明确返回 503。健康不发送在线模型请求。

| 变量 | 默认值与用途 |
| --- | --- |
| EA_LLM_ENABLED | false，不在未配置时发送聊天请求 |
| EA_DEEPSEEK_BASE_URL | https://api.deepseek.com |
| EA_DEEPSEEK_API_KEY | 空；仅后端实际启用模型时需要 |
| EA_DEEPSEEK_MODEL | deepseek-flash，允许配置其他兼容模型标识 |
| EA_AGENT_ENABLED | true，建立持久化并启用会话/任务接口 |
| EA_AGENT_MODEL_TIMEOUT_SECONDS | 30，范围0.1–120，单次请求总预算 |
| EA_AGENT_MODEL_MAX_TOKENS | 1024，范围128–4096，模型输出上限 |
| EA_AGENT_TIMEOUT_SECONDS | 90，范围5–300，每次主动执行预算 |
| EA_AGENT_MAX_STEPS | 24，范围6–60，敏感操作提前预留后续步数 |
| EA_AGENT_MAX_TOOL_CALLS | 8，范围1–16，自动政策查询计入 |
| EA_AGENT_MAX_RETRIES | 2，范围0–2，明确临时失败的有限重试 |
| EA_AGENT_MAX_HISTORY_MESSAGES | 12，范围2–24，完整问答对 |

HTTPX 发送 OpenAI 兼容 Chat Completions，Thinking disabled、非流式、temperature=0；路由、改写、原文选择及 SQL 规划控制工具使用 named tool_choice，业务工具 auto，不装 OpenAI SDK。[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)是协议来源。本机已用忽略的本地配置启用模型，历史烟测见[在线报告](../evals/results/phase4-live/report.md)，当前 NL SQL 验收另行记录；模板仍默认禁用，受控图测试不冒称在线结果。

迁移包括 0003_agent 的官方 Checkpoint 与 0004_agent_role 的不可变会话创建角色。角色变化需新会话，恢复原相同角色后可访问原合法会话；服务端私有 thread 不能通过 HTTP 指定。Agent 随请求运行，无后台 Worker 或自动任务重领；中断后显式调用 recover 核对已提交业务。

Chroma SDK 1.5.9 使用窄适配桥，将实际 HTTP session timeout 在初始网络调用前设置；connect/pool 最多3秒，read/write 使用配置秒数。它是各阶段超时，不是整次多请求操作总耗时保证；外层还有 RAG 时间预算。客户端升级需要复查适配和真实 HTTP 测试，不用不存在的 SDK 配置键假装限制请求。

## Phase 8 评估入口

先完成本机基础设施、专用测试库迁移与本地模型准备。默认命令运行明确受控故障集，不连接供应商或数据库；`Prepare` 使用专用测试库准备虚构数据和真实 CPU/Chroma 索引，不发聊天请求：

```powershell
.\scripts\evaluate-phase8.ps1
.\scripts\evaluate-phase8.ps1 -Mode Boundaries
.\scripts\evaluate-phase8.ps1 -Mode Prepare
```

`Prepare` 调用 seed_demo，基准 UTC 2026-10-12、seed=42、500 订单；已有种子保持原记录，不 reset。实际 API 使用当前 UTC 判断退款期限，固定数据仍会随日期超期，须由执行前真实订单资格检查决定可绑定订单。隔离数据目录为 `var/evaluation-test`、Chroma 前缀 `ea_phase8_eval`，与前端验收集合分开；Redis 使用 DB 2。模型缓存继续共享经 SHA256 校验的 D 盘只读文件。

显式在线入口将使用已配置的真实 DeepSeek，可能消耗余额；不准备云资源、不确认退款：

```powershell
.\scripts\evaluate-phase8.ps1 -Mode Live -AllowLive
# 已有 preparation.json 且核对真实隔离条件后，可跳过本轮准备
.\scripts\evaluate-phase8.ps1 -Mode Live -AllowLive -Prepared
```

包装脚本拒绝已有 8002 监听，不停止未知进程；启动 `127.0.0.1:8002` 的当前 API，并在 finally 核对自身 executable/main.py 后结束记录的子进程。API 使用 enterprise_agent_test 的 ea_test_app 与 ea_test_analytics_reader，不继承 owner 或其他测试 DSN；参考采集另用受守卫的 ea_local_admin 连接，以只读事务核对基表事实。配置 LangSmith 强制关闭，付款仅来自明确的 Agent messages 调用。

Live 每轮创建 `var/evaluation/phase8/live/<UTC>` 新目录，保存 results.json、cases.csv、report.md 和日志。JWT Secret 另存 ignored `private-jwt-secret`，不要分享整个原始运行目录。模型返回的真实 Task 在继续核验前先持久到报告，消息异常时只查持久任务，不自动重发、recover 或确认退款。返回码 0 表示评估完成和结果已记录，fail/manual_review 不会被当作基础设施异常或隐藏。

本次首次 UTC 目录 `20261014T1904572830729Z` 的原始评估公开到[在线报告](../evals/results/phase8/live/report.md)：18 案例为 5 pass/3 fail/9 manual_review/1 unobserved，17 Task、36 次实际模型尝试、39440 Token。validation 13 条为 4/2/7；冻结结果没有改题、事后调分或删除失败，首物流样本因独立参考 404 在消息前停止。结果不代表全量模型质量通过或重试后的最终源码在线验收。

模块调用要求调用进程已经通过项目基础设施脚本配置所需专用测试 URL，且正确隔离 API 已运行；建议日常使用包装脚本。PyCharm 为 main.py 配置的项目 Python 3.13 可直接运行：

```powershell
.\.venv\Scripts\python.exe -m enterprise_agent.evaluations.live --mode live --allow-live --base-url 'http://127.0.0.1:8002' --output 'var/evaluation/phase8/live/manual-run-1'
```

模块仅接受原始产物位于项目 var 下；手动复跑请选新的输出目录，包装脚本自动创建新 UTC 路径。每条消息前验证新 session 确实存在于专用测试库，按真实数据选择自己/他人/可退款订单。最终选样最多验证 100 个拥有订单的物流：实际 200 才用于读取案例，404 跳过，其他错误立即停止；没有可用物流时在付费消息前拒绝。首次已归档运行发生在这项选样修正前，原未观察案例不改分或复跑。运行日期漂移、基表变化、源码变化、未知消费和未观察到任务均保留记录。更早手选烟测不能替代这套冻结合同，当前结果见[Phase 8](phase-8.md)。

## 本地元数据追踪与可选 LangSmith

应用创建 `TaskTraceRecorder`，默认在 `EA_DATA_DIR/observability/agent-traces.jsonl` 追加 UTF-8 JSONL；数据目录仍优先项目 D 盘并由 Git 忽略。设置 `EA_OBSERVABILITY_ENABLED=false` 可关闭本地写入，它与远端开关分别控制。执行后保存的快照包含累计 Token/耗时/steps，应按 Task ID 选择最新快照，不能把 send、resume 和 recover 的累计 DTO 相加。

| 配置 | 默认值与边界 |
| --- | --- |
| EA_OBSERVABILITY_ENABLED | true；本地白名单元数据 JSONL |
| EA_LANGSMITH_ENABLED | false；只开启显式摘要导出 |
| EA_LANGSMITH_API_KEY | 空；启用远端时必须在本地秘密配置填写 |
| EA_LANGSMITH_ENDPOINT | 空；启用时必须显式提供 origin |
| EA_LANGSMITH_PROJECT | 空；启用时必须提供 1–80 位 ASCII 项目名 |
| EA_LANGSMITH_TIMEOUT_SECONDS | 2；范围 0.1–5，SDK connect/read 有限超时 |
| EA_LANGSMITH_QUEUE_SIZE | 16；范围 1–64，满时安全记录丢弃 |

允许的 endpoint 为 loopback origin，或官方 HTTPS `api.smith.langchain.com` / `eu.api.smith.langchain.com` 的默认 443 origin；拒绝凭据、路径、query 和 fragment。项目名以 ASCII 字母/数字开头，后续仅字母/数字/点/下划线/短横线。SDK Client 不继承环境代理或 netrc，不跟随重定向、不开自动批量和请求重试，目的 origin 不允许改变。不要把 SDK 自动 tracing 环境变量当作此功能配置，LangGraph 主动调用保持 `tracing_context(enabled=False)`。

摘要不含问题、答案、工具参数、SQL、文档、用户/订单字段，root 记录任务累计元数据，segment 记录请求片段，steps 为执行后观察而非真实 span 起止时序。本阶段 LangSmith 默认关闭，SDK 合同使用受控 requests transport 检查；没有提供云凭据，也没有真实云导出或公网接收端成绩。是否向自己的 LangSmith 项目导出需要自行明确配置，不能根据健康接口推导出口在线。

本地写入失败和远端异常只记录安全错误，不使业务任务失败；有界 daemon worker 不提供 durable delivery，退出不等待队列清空。当前没有自动轮转、文件清理、跨进程日志锁或生产 trace collector。真实评估器的原始 Task 证据与这里的精简追踪是不同产物，分享原始评估文件前仍需检查内容和秘密。

本次 17 Task/17 本地快照的真实校验见[观测证据](../evals/results/phase8/observability/verification.json)，状态、错误、request hash 和累计 usage 与 DTO 相符。39440 Token/36 次调用属于同一线上账本，不再次计费汇总；LangSmith 配置关闭且没有云凭据，这不是网络抓包或远端发送验收。

## MCP Server 与 Client

MCP 采用本机 stdio，不开放端口。独立入口使用官方 mcp 1.30.0，三个工具查询普通业务数据库及已有政策知识；不发送 DeepSeek 请求。Python 模块与安装入口分别为：

| 入口 | 模块 / 安装命令 |
| --- | --- |
| Server | `python -m enterprise_agent.mcp.server` / `enterprise-agent-mcp` |
| Client | `python -m enterprise_agent.mcp.client` / `enterprise-agent-mcp-client` |

最简 Demo 在另一窗口先启动 API，再在项目根目录执行：

```powershell
.\scripts\mcp-demo.ps1
.\scripts\mcp-demo.ps1 -OrderNo '10086'
```

脚本加载本项目普通业务基础设施配置、显式启用已准备 RAG，并移除迁移/测试 URL、Redis、模型 Key 及旧 MCP Token。Client 使用公开 demo_customer 登录 loopback API，再启动独立 Server、发现三个工具、调用所选订单并检查 isError。使用虚构公开账号，不创建退款或付费资源；API 的登录仍受已有 Redis 限流控制。

同一 PowerShell 中已经通过脚本准备运行环境后，可以直接使用模块并保存 JSON：

```powershell
.\.venv\Scripts\python.exe -m enterprise_agent.mcp.client --demo-login --order-no '10086' --output 'var/test-results/mcp-client.json'
```

| Client 参数 | 默认 / 约束 |
| --- | --- |
| --demo-login | 默认不登录；启用时调用本机 API 的公开模拟客户登录 |
| --api-url | http://127.0.0.1:8000；仅接受无路径/查询/凭据的 HTTP loopback origin，不跟随重定向或环境代理 |
| --order-no | 10086；5–30 位 ASCII 数字字符串 |
| --output | 可选 .json 路径，写入脱敏结果；优先项目 D 盘 var 下 |

已有 JWT 的集成应在调用进程环境设置 `EA_MCP_ACCESS_TOKEN`，并省略 --demo-login。普通 `EA_DATABASE_URL`、`EA_JWT_SECRET` 和 RAG 配置也必须已经在环境中；Client 和 Server 都不读取 `.env`。不要把 Token 作为 CLI 参数、写入 .env.example 或打印到日志。脚本模式每次重新登录；Token 模式的到期/撤销不会自动刷新，重新取得凭证并启动进程。

| 配置 | 实际用途 |
| --- | --- |
| EA_MCP_ACCESS_TOKEN | 必需的现有用户 JWT，每个 Server 进程一个身份 |
| EA_MCP_REQUEST_TIMEOUT_SECONDS | 默认 15 秒，范围 1–120；覆盖单次认证和业务查询 |
| EA_RAG_ENABLED | 政策有来源文档时需要 true，且模型、Chroma 与政策索引就绪 |

Server 每次调用重新验证 JWT、启用状态、token_version 和当前角色；工具参数与 `_meta` 不授予身份。无权订单返回安全 404，新增写入工具名被拒绝。成功 data 由现有 BusinessTools / Service 产生；政策证据缺失仍显示 insufficient_evidence，存在适用政策但未启用 RAG 时返回 rag_not_enabled。

Client 子进程环境白名单仅包含必要普通数据库、JWT、用户 Token、RAG、D 盘临时目录与 SDK 精选系统变量，不继承模型 Key、管理员/测试/分析连接或 Redis。Server 显式禁用聊天模型、Agent Checkpoint、分析 runtime 与上述非必要连接。MCP 使用已有受限业务账号，不使用 Phase 6 的专用 reader；三工具只读并不表示该业务账号整体只有 SELECT 权限。

Client 默认整体预算 60 秒，内部 ClientSession 也配置有限读取超时。Windows Client 使用 Proactor 支持异步 subprocess，Server 使用 Selector 支持 psycopg。嵌套异步上下文先关闭 Session，再由官方 stdio transport 关闭 stdin、等待或终止实际子进程；服务日志写 stderr，stdout 只含 JSON-RPC。Demo 报告包含工具结果、成功状态及 provider_calls=0 / token_usage=null，不冒称线上 Token 用量。

当前没有 HTTP/SSE MCP、OAuth、共享多用户 MCP 进程、Token 自动续期、Agent 图内 MCP 路由或写入工具。stdio 为可信本机 Client 使用，尚无生产负载验收。运行证据与限制见[Phase 5](phase-5.md)，协议字段见[API](api.md)。

## 数据和政策

seed 默认取当天日期作为 UTC 零点，seed=42、500订单；已有 demo_customer 时返回现有统计，不覆盖或清空。固定基准能复现业务事实，密码 salt 不属于相同字节要求。

本工作区原有业务基准是 2026-10-12，五篇 Markdown 政策已索引为44个Chunk、活动代数1。API 退款使用当前 UTC，固定种子订单会逐渐超期。不同日期需要新的显式本地数据库，不自动删除已有卷重建。

知识原件按 checksum 保持版本一致。更新资料应上传新文档；重索引保留原件。已发布退款政策来源不能删、改或变成内部文档；新知识文档不自动替换可执行政策。

## 健康与运行验证

```powershell
docker compose --env-file var/infra-secrets.env -f deploy/compose.yaml ps
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/live'
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/ready'
```

ready 要求 application、database、已配置 Redis、已启用 knowledge、已建立 agent_checkpoint 以及启用时的 analytics 通过。业务库验证当前迁移 head，分析库验证固定 schema marker、两个视图、专用角色和授权边界；knowledge 校验本地模型与 Chroma 心跳，checkpoint 检查官方 SDK 版本和运行账号权限。健康不验证每个语料活动索引、模型配置、DeepSeek 在线状态或模型 SQL 语义。

Phase 8 [重启后的 HTTP 证据](../evals/results/phase8/demo-ready.json)记录后端 0.8.0/phase=8、六项 ready 检查通过、Vite 5173 返回 200 且健康代理一致。该检查新增模型请求 0，只证明对应本机启动状态，当时没有执行新一轮浏览器业务流程、Linux 应用镜像或公网部署。

完整检查结果保存本阶段var/test-results JSON/JUnit；Phase 4初验511项通过，零失败/错误/跳过，Ruff与pip check通过，公共证据见[验收记录](phase-4.md)及[验证摘要](../evals/results/phase4/verification.json)。检索评估输出路径仍由evaluate-rag配置，历史Phase 3原始JSON/CSV/报告保留，不作为Phase 4 Agent效果分数。依赖缺失的跳过不属于通过，SkipPostgres/SkipRag只用于局部开发。

应用升级后必须重启。[初验主服务HTTP](../evals/results/phase4/demo-http.json)保留启用模型前的五项健康、会话201、越权404、非法角色422、缺模型503/零任务；其onlinefalse历史不改。后来真实在线结果独立归档于[phase4-live](../evals/results/phase4-live/report.md)，健康仍不发送模型请求。

## 关闭与后续交付

交互式进程用 Ctrl+C；登记的项目后台 API 可用 scripts/stop.ps1 检查 PID/项目身份并结束对应树，登记的后台前端用 scripts/stop-frontend.ps1。脚本不会按端口终止未知程序。

`stop-frontend.ps1` 仅适用于后台启动时已记录 `var/runtime/frontend.pid` 的项目 Vite 进程；它同时核对当前 Node executable、项目 Vite 入口和 5173 端口参数。普通 `frontend.ps1 -Mode Run` 前台进程不登记该 PID，直接在原终端按 Ctrl+C 关闭。

```powershell
.\scripts\stop.ps1
.\scripts\stop-frontend.ps1
docker compose --env-file var/infra-secrets.env -f deploy/compose.yaml stop
```

stop 保留数据。不自动使用 down -v、drop database、prune 或删除其他项目文件。

## Linux 全栈 Compose

全栈文件为 [deploy/compose.full.yaml](../deploy/compose.full.yaml)，项目名 `enterpriseagent-full`，与原 `enterpriseagent` 基础设施分开。只暴露 `127.0.0.1:18000` 的 API 与 `127.0.0.1:15173` 的 web；PostgreSQL、Redis、Chroma 不映射宿主端口，使用内部网络。静态 Vue 站点由 Nginx 代理同源 `/api/` 与 `/health/`，直接刷新前端路由由 SPA fallback 处理。

确认固定模型已经位于 `var/models` 后，Windows PowerShell 执行：

```powershell
.\scripts\full-stack.ps1 -Mode Prepare
.\scripts\full-stack.ps1 -Mode Up
.\scripts\full-stack.ps1 -Mode Check
# 运行单独测试容器；它只重置 enterprise_agent_test 中的虚构测试记录
.\scripts\full-stack.ps1 -Mode Test
# 停止属于 enterpriseagent-full 的服务，保留所有持久数据
.\scripts\full-stack.ps1 -Mode Stop
```

`Prepare` 仅补齐本项目目录、未存在的数据库/JWT 随机 secret、默认演示密码和空模型 Key，检查模型资产目录存在并校验 Compose；不覆盖已有秘密或数据库。`Up` 构建 API / web 并等待依赖健康与 setup 成功，随后执行 loopback 检查；`Check` 保存 `var/test-results/phase9/readiness.json`，不发送聊天请求。`Test` 构建并运行 test profile 镜像，JUnit / 固定 SQL / 故障结果留在 `var/compose/test-output`。失败退出时保留原数据和日志，不自动删卷或重试付费模型。

包装脚本显式指定 `--project-name enterpriseagent-full --env-file var/compose/compose.env -f deploy/compose.full.yaml`，公共开关单独保存在 compose.env，不读取根 `.env`、已有开发 DSN 或继承其他 Compose 项目名。公共文件没有密码；所有 secret 文件只在本机生成，`.dockerignore` 也排除 `.env`、var、模型、构建缓存和本地日志。

账号名称与 README 相同，默认演示登录密码为 `DemoOnly!2026`，与登录页预填值一致。setup 从 `var/compose/secrets/demo-password` 读取密码；数据库和 JWT 秘密继续独立随机生成。已有自定义密码文件会被保留，重复 `Prepare` 不会旋转密码或重置 seed，也不会更新现有账号的密码散列。修改已有演示账号时须同步数据库散列、递增 token_version 撤销旧凭证并记录审计；不能只改密码文件。自定义凭据不提交 Git。

| 服务 | 运行职责 / 秘密 |
| --- | --- |
| postgres | 独立本地库与测试库、既有初始化 ACL；密码文件不进入镜像 |
| redis / chroma | 独立持久目录与内部网络，不监听公网 |
| setup | 一次性迁移、seed42 / 500 模拟订单 / 2026-10-12、真实 CPU 推理和知识索引；短期使用初始化账号 |
| api | `ea_app` 与 `ea_analytics_reader`、JWT、可选模型 Key；不挂载 owner / 测试 / demo 密码 |
| web | Nginx 静态站点及同源 API 代理，不持有后端秘密 |
| tests（test profile） | 独立测试业务 / reader / 初始化账号，只访问本项目测试库，运行强制 PostgreSQL / RAG 检查 |

新增数据和秘密位于项目 D 盘 `var/compose/{postgres,redis,chroma,app,test-output,secrets}`；Docker 镜像缓存仍使用已配置的 `D:\DockerData`。Compose 使用宿主 `var/models/embedding`、`reranker`、`manifest.json` 三个只读挂载，推理缓存独立放在可写 tmpfs。setup / API 使用非 root 用户、只读根文件系统与有限临时目录；检查数据挂载是否可写属于启动验证，不通过删除旧数据解决权限问题。

容器入口 `python -m enterprise_agent.deployment` 支持 `serve`、`setup`、`health`、`tests` 与 `rag-evaluate`。它从固定 `/run/secrets` 文件读取必要配置，不加载宿主 `.env` 或继承内联 DSN；收到内联数据库/密钥环境字段、setup/tests 开启聊天模型或远端 LangSmith 时拒绝运行。API 绑定容器 `0.0.0.0:8000` 是 Docker 内部监听，宿主映射仍为 loopback。

完整部署默认 `EA_FULL_LLM_ENABLED=false`、`EA_FULL_DOCS_ENABLED=false`，所以业务查询、直接 RAG / SQL 和持久任务历史可运行，Agent 消息明确返回模型未配置。DeepSeek 不从现有 `.env` 自动复制：需要时由用户将自己的 Key 写入被忽略的 `var/compose/secrets/deepseek-api-key`，在公共 `var/compose/compose.env` 显式启用 `EA_FULL_LLM_ENABLED=true` 后重新运行 Up 更新 API。不要把 Key 放进命令、Compose environment、前端或 Git。远端 LangSmith 在此部署 profile 中关闭。

初始化阶段先按通常的 3 秒期限检查业务数据库；若检查超时或连接尚未建立，最多再检查一次，第二次期限为 10 秒。角色安全属性、权限范围、必需授权和迁移版本仍须全部通过。运行中 API 的健康检查保留 3 秒期限。初始化状态只记录固定阶段和就绪布尔值到本机 `setup-status.json`，CI 通过白名单读取这些字段，不公开数据库连接、用户名、SQL 或异常正文。

Phase 9 修订了售后 Markdown 前言，`rag.json` v2 记录新 checksum。首次 full setup 使用新语料；已存在的旧开发库仍保护发布时来源，seed / 重索引不会替换该原件。旧库不应强改 checksum 以通过评估，v2 对照需使用对应新语料初始化的独立数据库。Phase 3 / Phase 8 旧 JSON / CSV / 报告保持原语料与指标，不能直接作为 v2 成绩。

在已经初始化且知识索引就绪的 full 项目重新执行对照：

```powershell
docker compose --project-name enterpriseagent-full --env-file var/compose/compose.env -f deploy/compose.full.yaml exec -T api python -m enterprise_agent.deployment rag-evaluate
```

该入口复用原检索 evaluator，加载 API 当前挂载的普通应用凭据和 v2 数据集，局部显式关闭聊天模型，不使用 owner 或宿主 `.env`。默认新产物写入宿主 `var/compose/app/evaluation/phase9/rag-<UTC>`；可显式追加 `--output /app/var/data/evaluation/phase9/my-run`，请自行选择尚未使用的目录保留历史。自定义输出不提供既有目录防覆盖校验。稳定 CLI 已实际复验 60 条记录，结果与初轮分别保存；真实执行和代码范围见[本阶段记录](phase-9.md)。

## GitHub Actions 与本地验证

[.github/workflows/ci.yml](../.github/workflows/ci.yml) 支持 push、pull_request 和手动触发，权限为 contents:read，Action 使用提交 SHA 固定。两个 job 都在 Ubuntu 24.04：frontend 使用固定 Node 24.19.0 跑 npm ci、格式、Vitest、TypeScript / Vite 与生产 audit；linux-stack 构建 full API / web / tests、显式准备和校验固定 ONNX 文件、启动 setup / readiness / 代理检查，再运行后端测试与离线评估。backend job 不注入真实 Key，模型缓存只包含公开资产。

上传产物是选定前端 JUnit、Linux JUnit、脱敏 stack-smoke JSON、固定 SQL 和故障 JSON / 报告，保留 7 天；不上传 var/compose/secrets、完整运行目录或容器环境。宿主 runner 的 HTTP 检查报告写入 `var/test-results/stack-smoke.json`；容器测试结果留在归 UID 10001 所有的 `var/compose/test-output`，避免宿主写入容器专用目录时出现权限错误。初始化前以容器非 root 身份检查秘密文件可读、配置有效、固定模型完整性和运行目录可写，仅输出固定检查名称与布尔状态，不输出秘密值、异常正文或容器原始日志。失败和取消时停止本次项目服务，保留数据及已生成证据。工作流没有发布镜像、推送站点、远端迁移或云部署步骤。

Linux 后端检查使用 `scripts/ci/check_backend.sh`：Ruff / 格式 / pip check、模型资产核验、强制真实 PostgreSQL / RAG 的 pytest，以及明确故障注入与固定 SQL 评估。模型 fixture 和真实供应商消费仍分开；CI 显式关闭聊天模型及云端追踪，不需要 DeepSeek Key。最终本机完整检查链退出 0：pytest **1097 / 1097**、受控故障 **16 / 16**、人工 SQL **24 / 24**；JUnit 621.404 秒、0 失败/错误/跳过，87 条 Chroma 弃用警告保留。

测试脚本每轮在 D 盘的专用测试输出挂载创建独立具名临时目录，用于约 397 MB 模型副本与 Ruff 缓存，执行前要求至少 2 GiB 可用空间。匿名输出捕获仍使用 Linux `/tmp`，避免 Docker Desktop 的 NTFS 绑定挂载对此类文件的兼容问题。旧轮次失败记录保留，不通过扩展宿主 C 盘缓存、取消真实模型校验或改用应用库来规避测试。

60 条两模式 RAG 对照由独立 `rag-evaluate` 入口手动执行，不在当前 CI chain 中自动运行或上传。CI 的强制 RAG 集成测试、smoke 单次检索与整套相关性对照是不同范围；`run_status=complete` 仅表示执行记录完成，引用校验通过也不能证明语义充分或政策冲突识别。

本次实际 Linux 依赖解析和哈希安装、API / web 镜像构建、setup / 索引、六项 readiness、13 项 HTTP smoke、API 重建持久化与 Nginx DNS 恢复均已保存[公开证据](../evals/results/phase9/report.md)。HTTP smoke 检查真实订单、混合重排/当前来源和分析视图，不包含新的浏览器交互。新的 v2 RAG 单独完成 60 条模式记录，质量与延迟分别呈现，不继承旧语料成绩。

工作流交付、实际本机容器运行和 GitHub 平台执行是分别核验的范围。没有执行平台上的 workflow 时只报告文件和本地检查，不填写绿色 CI 或公网部署成绩。最终命令、证据路径及数量以[Phase 9](phase-9.md)为准。

API / web 镜像、Linux 依赖与独立全栈 Compose 的当前交付和实际验证见[Phase 9](phase-9.md)。Vue 的历史浏览器记录单独见 Phase 7，不当作本阶段的新浏览器或模型验证。当前没有独立 Worker、自动维护清理工具、GitHub 平台执行成绩或公网部署记录。
