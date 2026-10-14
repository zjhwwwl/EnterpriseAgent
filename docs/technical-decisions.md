# 技术决策

当前 Phase 6 / 0.6.0 在既有 LangGraph 单 Agent、Agentic RAG、MCP 和人工审批外增加受限运营分析。分析图分支与 HTTP 复用 AnalyticsService，使用专用数据库身份、聚合视图、SQL AST 与执行限额；确定性报告保留真实查询行和指标口径。当前验收见[Phase 6](phase-6.md)，此前阶段结果保持历史记录，工程安全和受控流程检查不替代模型 SQL 语义质量。

## 模块化单体，单 Agent 多工具

API、Service、Repository、Schemas、RAG、Agents、Tools、MCP 和 CLI 在同一包内维护。索引与图通过请求/CLI执行，人工等待持久化；MCP 为独立协议进程但复用同包 Service，不增加独立 Worker 框架、微服务、Kubernetes 或通用规则引擎。

当前一张 LangGraph 图按任务分支编排，分析只增加 planner / execution 节点，复用同一会话与 Checkpoint。MCP Client / Server 直接调用 BusinessTools，不额外设计 Agent 或 MCP 图分支；没有多 Agent 协商、自动任务调度或 tracing。

## Python 3.13 与哈希锁

pyproject 声明依赖，requirements/base.lock、dev.lock 固定版本及分发文件哈希；bootstrap 使用 --require-hashes，再 --no-deps --no-build-isolation 可编辑安装。当前在 Windows Python 3.13 验证，不将锁文件存在当作 Linux 应用兼容证明。

使用项目 .venv、D 盘缓存与 TEMP/TMP，不修改全局 Python。Phase 3 本地推理采用 ONNX/FastEmbed，避免为展示功能引入整套训练框架；不能据此声称已经有GPU验证或训练能力。

## 工厂、日志与生命周期

create_app 可注入配置，导入不创建全局运行实例或提前读取实际环境。main.py 通过项目 CLI 保留 JSON 日志并关闭原始 access log；Windows 使用 Selector event loop 兼容异步 psycopg。

日志不保存请求体、查询字符串、Authorization、秘密或全量环境。统一错误保留HTTP状态和request_id，输入校验不回显原始字段。健康区分存活与真实依赖就绪，启动不自动迁移、seed、模型下载或索引。

## 数据库权限与业务事务

普通非owner账号只获明确逐表/列权限；订单id UPDATE用于FOR UPDATE，不授权状态或金额。审计仅追加，政策只读。确认/退款/工单/幂等、agent_tasks/agent_sessions具有明确DML。知识文档INSERT/UPDATE与Chunk INSERT/DELETE由0002显式增加；官方Checkpoint权限由0003限制，创建角色不可变由0004保护。

整数分、交易快照、归属过滤、订单锁、同Key advisory lock、活动退款唯一约束和事务审计共同保护写入。客户确认不等同客服审核，模拟完成不连接支付或改订单状态。JWT每请求从DB重查角色、启用和撤销版本；Redis登录限流只使用可信连接来源。

## 知识文档与结构化政策分离

RAG负责解释和来源，可执行退款条件由Service与发布版本确定。新文档不会自动发布规则，相关性分数不能批准退款。

0001禁止发布政策UPDATE/DELETE；0002要求来源公开、存在且未删除，并锁源行防并发变更。已有发布政策的源文档不能改身份、内容版本或删除状态，但允许索引维护。业务申请保存适用版本与事实快照，历史判断不被新的文本覆盖。

## 真实文档解析与字符预算

采用pypdf、python-docx及严格UTF-8，测试实际文件bytes。PDF无OCR，DOCX只覆盖正文和表格。先校验格式、ZIP预算、重复/不安全路径、DTD与实体，解析错误不泄漏上传内容；PDF解码后的流大小检查不是操作系统内存沙箱。

中文按字符与句界分块，保持单一来源section及精确字符范围。默认500字符/80重叠，不能把字符预算写成Token预算。模型上限512Token，较长输入会被SDK截断，这对覆盖与召回有影响；后续改Token分块须通过新fingerprint和评估验证。

## 固定本地中文模型与CPU推理

Embedding：Qdrant仓库的BAAI/bge-small-zh-v1.5固定ONNX导出，512维CLS/L2，检索提示只用于query。Reranker：Xenova/bge-reranker-base固定量化ONNX，返回logit经sigmoid得到的0–1未校准分数。revision、文件大小与SHA固定，prepare显式下载或可信离线复制，推理只用本地校验文件。

模型载入是惰性的，第一次推理成本需要记录。解析、分块及模型/向量工作都使用Runtime容量与工作线程；外层超时不提前释放容量，避免用超时请求制造无界线程。它限制本地演示资源，不代表高吞吐队列或可抢占的进程隔离。

## 混合检索与启发式证据门槛

Vector与真实BM25各最多30候选，中文字符/二元组兼顾短词、业务标识和未知词；RRF k=60降低分数尺度差异，最多30候选再真实rerank。BM25按当前可见SQL Chunk构建，没有独立持久倒排系统。

单独返回模型/阶段分数与来源。所有模式需向量相似度通过门槛，hybrid_rerank还需sigmoid重排分数通过门槛。分数只是相关性筛选，不是概率校准、语义蕴含或政策冲突证明。直接接口保留evidence_only；Agent可改写一次查询，再由模型选择连续原文，服务器校验权限、当前索引和substring后构造回答，限制编造但不能证明语义充分。

## 索引发布代数与SQL权威

先写尝试代数/job ID，再解析、嵌入和写向量，最后锁文档验证同一次尝试后发布SQL Chunk与活动index。失败保留原件、错误和旧活动索引，过期工作不能覆盖新代数。

检索前后和引用详情均依据SQL可见性、活动代数与fingerprint。SQL/Chroma没有分布式事务，清理失败可能留下不可检索条目。数据库持久化开始后，未知提交结果下保留原件，避免已提交记录失去文件；仅文件写失败清理，故可能遗留未登记原件。当前没有维护清理工具或自动任务恢复。

## Chroma官方SDK兼容桥

固定chromadb-client 1.5.9与同版server。官方HTTP传输默认无限timeout，虚构chroma_server_request_timeout_seconds字段不会让请求受限，因此使用官方System/ServerAPI/Collection的窄桥，在首次网络请求前设置真实httpx session各阶段timeout，并在close释放资源。

桥没有自写REST协议或修改SDK全局对象，但依赖一个私有session字段；升级必须验证初始身份/租户/数据库请求、集合距离和实际延迟HTTP测试，不能只验证配置值存在。

## 可复现对照与诚实证据

30条中文dev/test人工数据、固定五文档checksum、同Embedding与top_k、每模式warmup1次、交替查询顺序，保存真实排名/引用/错误/耗时。文档级去重后Recall/MRR/nDCG与门控分别计算，失败不从分母删掉。

本地Embedding/Reranker不提供API计费Token字段，记null/N/A；Agent累计实际供应商usage，未知消费单独记录unknown_usage_calls与usage_complete=false。历史Phase3评估没有生成调用。受控Chat脚本只验证图边界与计账代码，不能充当真实DeepSeek效果或消费。dev用于调参，test单独报告，人工小集不称盲评，优化不保证改善，也不冒充性能压测。

## HTTPX 兼容接口与有限执行

LangGraph 固定1.2.14、Checkpoint固定4.2.0、PostgreSQL Saver固定3.1.2，通过哈希锁安装；官方schema的迁移序列与版本检查需要同步审阅。无需引入LangChain的通用Agent或OpenAI SDK，使用HTTPX发送兼容Chat Completions协议，严格校验请求、tool白名单、JSON参数、finish_reason和usage。

默认deepseek-flash、thinking.type=disabled。真实烟测暴露控制节点漏工具调用，改为仅route_task/rewrite_query/select_knowledge_quotes使用named tool_choice，业务工具auto。请求/响应、30秒模型、90秒主动执行、24步、8工具、2次重试预算保持。缺Key仍禁用，不用脚本替代生产模型；协议见[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)，[5场景烟测](../evals/results/phase4-live/report.md)不能支持泛化质量结论。

## 可信身份、会话角色与可恢复审批

Principal从HTTP认证/数据库取得，放在可信AgentContext，不从State或模型决定身份。thread由服务器生成，API只暴露session/task。会话保存创建角色且数据库禁止变更，查询同时校验用户和当前角色，防止降权后旧history或任务结果成为内部知识/订单读取入口。角色变化需要新会话，恢复原角色后可重新访问原合法会话。

同thread跨worker使用PostgreSQL advisory lock，等待人工时释放执行资源。官方AsyncPostgresSaver仅保存允许的基础数据，禁用pickle fallback/任意类重建；0003负责DDL和schema版本，应用不调用setup、普通账号不能建表或删检查点。[官方持久化](https://docs.langchain.com/oss/python/langgraph/persistence)与[中断文档](https://docs.langchain.com/oss/python/langgraph/interrupts)解释了恢复契约。

模型create_refund_request只准备操作摘要，先读适用结构化政策与对应知识文档。task_id可信绑定确保重复准备只有一审批；用户确认后固定服务端幂等键重用退款、工单、审计事务。业务提交和Checkpoint没有共同事务，显式recover通过原记录重放，不能将请求取消当作业务未提交。

订单报告依据结构化事实，不采用模型最终自由文本；知识答案采用验证原文抽取。敏感操作提前保留后续步数，提交后陈旧引用只作为warning并保留真实申请结果。这里没有自动任务重领或后台维护系统，这一取舍使个人项目的失败边界明确且可测试。

## 官方 MCP 维护版本与协议边界

固定 `mcp==1.30.0` 并更新两份哈希锁。官方说明 v1.x 仍接受关键错误与安全修复，2.x 为当前主线；本阶段选择已有稳定 Server / ClientSession 接口的维护版本，不声称它是最新主线。1.30.0 支持 Python 3.13，且修复客户端 outputSchema 外部引用读取、OAuth issuer/凭据绑定和跨源重定向问题，见[维护说明](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v1.x/README.md)、[固定版本](https://pypi.org/project/mcp/1.30.0/)与[发布记录](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0)。

使用官方低层 Server，工具 manifest 从共享 Pydantic schema 产生；关闭 SDK 的默认输入错误回显，在 raw arguments 入口由 BusinessTools 进行 strict / extra forbid 校验，再返回安全错误。FastMCP 的默认动态参数模型不自动提供同样的严格边界；SDK 未捕获异常可能返回 str(e)，因此在业务 handler 内处理异常和序列化错误，不把数据库或 ValidationError 细节传到客户端。实际代码无需依赖或修改 FastMCP 私有注册对象。[官方低层源码](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v1.30.0/src/mcp/server/lowlevel/server.py)是协议错误行为的来源。

stdio 面向可信本机 Client，每个 Server 进程绑定一个现有 JWT，每次调用仍由 AuthService 重查数据库角色、启用状态和 token_version。Client 不向工具参数或 `_meta` 填身份，启动配置不传给模型。仅暴露三个读取工具，退款写入继续经过 HTTP / LangGraph 的人工确认规则；没有 HTTP MCP、OAuth 或动态多用户会话。MCP 沿用普通业务数据库账号，不使用 Phase 6 的专用分析 reader，不能把工具只读声明等同数据库权限隔离。

Client / Server 不读取 `.env`，子进程配置白名单排除 DeepSeek、迁移/测试连接与 Redis；每次服务端预算默认 15 秒，示例客户端总预算 60 秒。stdout 仅协议、日志到 stderr，SDK 原始请求日志被隔离，业务日志只记安全工具名/错误码/耗时。Windows Client 的 Proactor 支持异步 subprocess，Server 的 Selector 兼容 psycopg；官方上下文负责退出实际子进程。[官方 stdio 源码](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v1.30.0/src/mcp/client/stdio/__init__.py)与真实退出测试同时保留。

当前 Windows lock 含 `pywin32==312` 且没有平台 marker，不能直接用于 Linux 应用镜像。Phase 9 要为目标平台重新解析并验证依赖；本阶段不把本机锁安装通过当作跨平台部署证明。

## 专用分析身份与聚合视图

分析不能复用拥有退款/知识库 DML 的业务账号。两个固定 reader 各获一个项目数据库 CONNECT，不允许项目开发/测试库交叉连接；仅显式授分析 schema USAGE 与两个聚合视图 SELECT。0005 创建视图及 schema marker，初始化脚本单独管理专用密码和角色，迁移中没有密码。runtime 拒绝 owner、角色成员关系、危险建表权限、业务表列读写或 sequence 权限，并核对视图列类型。

视图保持 owner 权限访问基表，security_invoker=false；security_barrier=true 限制用户谓词与视图条件的执行顺序。它不是 SECURITY DEFINER 函数，也不代替 SQL 白名单，参考 PostgreSQL 17 [CREATE VIEW](https://www.postgresql.org/docs/17/sql-createview.html)。两个视图只暴露聚合订单/商品事实，避免把客户、订单号或地址交给模型规划器。支付日期使用上海时区，退款先按订单聚合，防止 JOIN 放大销售额；当前完成退款率采用支付日期 cohort，未实现历史 as-of 退款快照。

默认只读事务可以被会话覆盖，并非不可绕过的账号属性；因此同时限制 ACL、每次查询显式 READ ONLY、statement / lock / 外层超时、行数与负载。两项目库撤销 PUBLIC TEMP，保留已有 PUBLIC public-schema USAGE 与系统数据库默认权限，避免扩展为整个 cluster 的权限重构。系统元数据仍可能默认可读，模型查询由 AST 限定到两个视图。PostgreSQL [只读事务规则](https://www.postgresql.org/docs/17/sql-set-transaction.html)解释了此边界。

## SQLGlot 白名单与可核对报告

固定 SQLGlot 30.22.0：该稳定分发声明 Python >=3.9，支持当前 Python 3.13；版本和哈希随锁文件审阅，不因存在更新就改动既有依赖。版本信息见[PyPI](https://pypi.org/project/sqlglot/30.22.0/)，AST 能力见[官方文档](https://sqlglot.com/sqlglot.html)。解析器负责语法树，不作为数据库权限隔离或业务语义证明。

采用精确节点白名单，接受单个授权视图上的 SELECT；未知节点和函数拒绝。首版主动限制 JOIN、CTE、子查询、窗口函数、动态来源和用户函数，使审计面与个人项目测试范围清晰。有限聚合、算术、条件、CAST 足以覆盖当前四类运营例子。规划器返回严格 SQLPlan，后端不拼接模型自由文本，也不将 SQL 关键字黑名单作为安全保障。

SQLGlot 部分解析警告包含完整原始 SQL，因此隔离其 logger、停止传播并用 NullHandler 接收；应用仅返回静态 analytics_sql_rejected 等错误码，不保存原始解析异常或未验证 SQL，避免第三方诊断绕过项目日志脱敏。

结果金额使用 NUMERIC 与精确十进制字符串，默认 100 行、20 列和有限单元格/JSON 预算；上限加一行判断截断。分析报告只从执行行、登记指标和规范 SQL 渲染，图验证再核对一致性，不追加另一次自由文本生成。这避免为解释数据编造原因，但不能保证模型选择的日期、分母和指标符合问题，必须另做 SQL/在线评估。图表返回 query_rows 数据规范，交互展示留给 Phase 7。

## 后续边界

- Phase7：Vue五页、真实浏览器流程、引用和确认。
- Phase8：生成忠实度/任务/工具/SQL评估与可选Langfuse；目前没有tracing集成。
- Phase9：应用镜像、Linux锁验证、全栈Compose、CI与干净启动。

当前只验证本地模拟系统，不推送密钥、不接真实支付、不未经授权公开部署。
