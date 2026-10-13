# 本地运行与部署

## 实际支持范围

Phase 4 支持 **Windows本机FastAPI / LangGraph / CPU ONNX + Linux PostgreSQL / Redis / Chroma容器**。本地工程及真实HTTP重启验收已完成，DeepSeek在线效果尚未验收。当前Compose为基础设施，没有应用Dockerfile、全栈前端镜像、GitHub Actions执行、公网发布或生产部署证明。

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

HTTPX 发送 OpenAI 兼容 Chat Completions，显式 Thinking disabled、非流式、temperature=0；不装 OpenAI SDK，不强制 tool_choice。协议参考 [DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)。模型选择与工具效果需要在线实际调用验证，当前缺 Key，不能把受控图测试当作 DeepSeek 在线结果。

迁移包括 0003_agent 的官方 Checkpoint 与 0004_agent_role 的不可变会话创建角色。角色变化需新会话，恢复原相同角色后可访问原合法会话；服务端私有 thread 不能通过 HTTP 指定。Agent 随请求运行，无后台 Worker 或自动任务重领；中断后显式调用 recover 核对已提交业务。

Chroma SDK 1.5.9 使用窄适配桥，将实际 HTTP session timeout 在初始网络调用前设置；connect/pool 最多3秒，read/write 使用配置秒数。它是各阶段超时，不是整次多请求操作总耗时保证；外层还有 RAG 时间预算。客户端升级需要复查适配和真实 HTTP 测试，不用不存在的 SDK 配置键假装限制请求。

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

完整检查结果保存本阶段var/test-results JSON/JUnit；Phase 4最终511项通过，零失败/错误/跳过，Ruff与pip check通过，公共证据见[验收记录](phase-4.md)及[验证摘要](../evals/results/phase4/verification.json)。检索评估输出路径仍由evaluate-rag配置，历史Phase 3原始JSON/CSV/报告保留，不作为Phase 4 Agent效果分数。依赖缺失的跳过不属于通过，SkipPostgres/SkipRag只用于局部开发。

应用代码升级后必须重启，并实际检查版本、phase、ready和知识接口，不能将旧进程当作最新服务。[最新主服务HTTP](../evals/results/phase4/demo-http.json)确认0.4.0、五项健康、所属会话201、越权404、非法角色422、缺模型503且零新增任务；健康通过不代表DeepSeek在线成功。

## 关闭与后续交付

交互式进程用 Ctrl+C；登记的项目后台服务可用 scripts/stop.ps1 检查 PID/项目身份并结束对应树，不能按8000端口终止未知程序。

```powershell
.\scripts\stop.ps1
docker compose --env-file var/infra-secrets.env -f deploy/compose.yaml stop
```

stop 保留数据。不自动使用 down -v、drop database、prune 或删除其他项目文件。

API/前端应用镜像、Linux应用依赖、全栈 Compose、CI、浏览器及公网部署属于后续阶段。当前没有独立 Worker、自动维护清理工具或部署到公网的记录。
