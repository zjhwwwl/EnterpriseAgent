# 本地运行与部署

## 实际支持范围

Phase 5 / 0.5.0 支持 **Windows 本机 FastAPI / LangGraph / CPU ONNX / 独立 MCP stdio + Linux PostgreSQL / Redis / Chroma 容器**。当前 MCP 验证与边界见[Phase 5](phase-5.md)。Phase 4 [517 项完整复验](../evals/results/phase4-live/verification.json)与真实 DeepSeek 5 场景烟测保留历史结果，仍无完整模型质量评估。当前 Compose 为基础设施，没有应用 Dockerfile、全栈前端镜像、GitHub Actions 执行、公网发布或生产部署证明。

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

Docker 程序与实际数据盘分别配置，少量 Windows 用户配置仍可能在 C 盘；本项目不另装 Ubuntu。基础设施镜像为 PostgreSQL 17.11-bookworm、Redis 8.4-alpine、Chroma 1.5.9，API 和本地模型运行在 Windows，不把容器运行当作 Linux 应用安装证明。

Python 3.13 项目虚拟环境须先可用。bootstrap 按 requirements/dev.lock 的哈希安装依赖，再以 --no-deps --no-build-isolation 可编辑安装并运行 pip check，不安装 Python 或 Docker、不覆盖已有 .env。当前锁文件已在 Windows 验证，Linux 应用依赖尚待交付阶段验证。

Phase 5 固定官方 mcp 1.30.0，并安装 Windows stdio 子进程所需 pywin32。两份 lock 是本机 Windows 解析结果，其中 pywin32 没有平台 marker；不能直接拿该 lock 安装 Linux 应用镜像，Phase 9 需重新解析并验证目标平台依赖。

## 首次启动

```powershell
Set-Location 'D:\Python\PycharmProjects\EnterpriseAgent'
.\scripts\bootstrap.ps1
.\scripts\init-infra.ps1
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
| init-infra | 创建未存在的本地随机秘密，启动基础设施；保留已有配置 |
| migrate | 开发库升级 head；-TestDatabase 处理专用测试库；-Check 检查模型差异 |
| seed | 虚构业务数据、默认当天 UTC/seed42/500订单；不覆盖已 seed 库 |
| prepare-rag | 显式准备固定模型文件与 manifest，首次可联网下载公开文件 |
| index-knowledge | 普通应用连接+模拟 admin，复用 Service 索引已登记文档；兼容活动索引跳过 |
| evaluate-rag | 普通应用连接+模拟 customer，固定 corpus 的真实检索对照 |
| check | 默认 require-postgres / require-rag，Ruff、pytest、pip check、JSON/JUnit |
| run | 注入应用配置、显式启用 RAG、移除迁移/测试 URL，再运行 main.py |
| mcp-demo | 注入普通业务配置，在 loopback API 登录公开模拟客户，经官方 stdio Client 调用三个只读工具 |

当前索引脚本没有选择单文档参数，处理所有未删除的登记文档；要强制重索引某篇已兼容文档，可用 admin 的 reindex API。

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

HTTPX发送OpenAI兼容Chat Completions，Thinking disabled、非流式、temperature=0；三个控制工具使用named tool_choice，业务工具auto，不装OpenAI SDK。[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)是协议来源。本机已用忽略的本地配置启用模型，真实有限烟测见[在线报告](../evals/results/phase4-live/report.md)；模板仍默认禁用，受控图测试不冒称在线结果。

迁移包括 0003_agent 的官方 Checkpoint 与 0004_agent_role 的不可变会话创建角色。角色变化需新会话，恢复原相同角色后可访问原合法会话；服务端私有 thread 不能通过 HTTP 指定。Agent 随请求运行，无后台 Worker 或自动任务重领；中断后显式调用 recover 核对已提交业务。

Chroma SDK 1.5.9 使用窄适配桥，将实际 HTTP session timeout 在初始网络调用前设置；connect/pool 最多3秒，read/write 使用配置秒数。它是各阶段超时，不是整次多请求操作总耗时保证；外层还有 RAG 时间预算。客户端升级需要复查适配和真实 HTTP 测试，不用不存在的 SDK 配置键假装限制请求。

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

Client 子进程环境白名单仅包含必要普通数据库、JWT、用户 Token、RAG、D 盘临时目录与 SDK 精选系统变量，不继承模型 Key、管理员/测试连接或 Redis。Server 显式禁用聊天模型、Agent Checkpoint 与上述非必要连接。MCP 使用已有受限业务账号，没有新增数据库角色或迁移；三工具只读并不表示该业务账号整体只有 SELECT 权限。

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

ready 要求 application、database、已配置 Redis、已启用 knowledge 和已建立 agent_checkpoint 通过。数据库验证当前迁移 head、必需权限及账号边界；knowledge 校验本地模型与 Chroma 心跳，checkpoint 检查官方 SDK 版本和运行账号权限。健康不验证每个语料活动索引、模型配置或 DeepSeek 在线状态。

完整检查结果保存本阶段var/test-results JSON/JUnit；Phase 4初验511项通过，零失败/错误/跳过，Ruff与pip check通过，公共证据见[验收记录](phase-4.md)及[验证摘要](../evals/results/phase4/verification.json)。检索评估输出路径仍由evaluate-rag配置，历史Phase 3原始JSON/CSV/报告保留，不作为Phase 4 Agent效果分数。依赖缺失的跳过不属于通过，SkipPostgres/SkipRag只用于局部开发。

应用升级后必须重启。[初验主服务HTTP](../evals/results/phase4/demo-http.json)保留启用模型前的五项健康、会话201、越权404、非法角色422、缺模型503/零任务；其onlinefalse历史不改。后来真实在线结果独立归档于[phase4-live](../evals/results/phase4-live/report.md)，健康仍不发送模型请求。

## 关闭与后续交付

交互式进程用 Ctrl+C；登记的项目后台服务可用 scripts/stop.ps1 检查 PID/项目身份并结束对应树，不能按8000端口终止未知程序。

```powershell
.\scripts\stop.ps1
docker compose --env-file var/infra-secrets.env -f deploy/compose.yaml stop
```

stop 保留数据。不自动使用 down -v、drop database、prune 或删除其他项目文件。

API/前端应用镜像、Linux应用依赖、全栈 Compose、CI、浏览器及公网部署属于后续阶段。当前没有独立 Worker、自动维护清理工具或部署到公网的记录。
