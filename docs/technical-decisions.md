# 技术决策

当前 Phase 8 / 0.8.0 在既有 LangGraph 单 Agent、Agentic RAG、MCP、人工审批、受限分析与 Vue 五页外增加可复现证据检查和安全元数据追踪。执行状态、精确业务/SQL 行、工具参数和引用结构可以由程序核对，语义充分性仍需人工标签。当前验收见[Phase 8](phase-8.md)，此前阶段保持历史结果，工程检查和受控流程不替代数据库、浏览器或线上模型证据。

## 模块化单体，单 Agent 多工具

API、Service、Repository、Schemas、RAG、Agents、Tools、MCP 和 CLI 在同一包内维护。索引与图通过请求/CLI执行，人工等待持久化；MCP 为独立协议进程但复用同包 Service，不增加独立 Worker 框架、微服务、Kubernetes 或通用规则引擎。

当前一张 LangGraph 图按任务分支编排，分析只增加 planner / execution 节点，复用同一会话与 Checkpoint。MCP Client / Server 直接调用 BusinessTools，不额外设计 Agent 或 MCP 图分支；没有多 Agent 协商或自动任务调度。执行后追踪通过 AgentService 的独立记录器完成，不增加图内规划节点。

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

结果金额使用 NUMERIC 与精确十进制字符串，默认 100 行、20 列和有限单元格/JSON 预算；上限加一行判断截断。分析报告只从执行行、登记指标和规范 SQL 渲染，图验证再核对一致性，不追加另一次自由文本生成。这避免为解释数据编造原因，但不能保证模型选择的日期、分母和指标符合问题，必须另做 SQL/在线评估。图表返回 query_rows 数据规范，由 Phase 7 的 SVG 组件展示比例和原值。

## Vue 五页与轻量业务状态

选择 Vue 3、TypeScript、Vite、Element Plus 和 Vue Router，页面对应五个已实现业务模块，不引入额外微服务或庞大前端状态框架。API Client、认证响应式状态、确定性格式化和共享 TaskDetail/CitationList 组件足够覆盖当前页面。依赖按 package-lock.json 固定，Node.js 24；本地安装缓存、TEMP 与产物留在 D 盘。

固定 Vue 3.5.44、TypeScript 5.9.3、Vite 8.3.4、Element Plus 2.14.7，使用现有 Node.js 24.19.0；没有为了追最新主版本改用 TypeScript 7。Playwright 1.64.0 使用现有 Edge，避免额外安装浏览器到 C 盘；实际 npm ci、类型检查和构建通过。当前 UI chunk 约 984.49 kB（gzip 316.59 kB），保留 Vite 超过 500 kB 的警告，后续可按实际需求优化组件引入，不声称已有页面加载性能优化。

JWT 保存于内存，刷新后重新登录，避免演示 Key/Token 进入持久浏览器存储。前端不持有模型或数据库秘密，身份、实时角色和权限由后端负责。导航按当前账号和角色区分页面实例，API 延迟返回还需检查身份，防止旧请求覆盖新登录页面。Token 到期不自动续期，未实现完整公网会话管理。

退款与索引操作用明确核对界面，关闭不视为同意或拒绝。订单页面的未决审批 Map 按账号保存原 approval_id、幂等键及决定，使页面切换后仍可按原请求手动重试；不持久化该 Map，也不宣称它能保障服务端事务。未知结果先核对，后端审批、行锁、唯一约束与幂等记录仍是权威。任务与文档按真实业务状态显示，不能把成功 HTTP 当作完成。

安全 Markdown 由 marked 解析、DOMPurify 过滤；引用原文直接文本插值，认证核验 URL 根据 Chunk UUID 构造。图表使用轻量 SVG，免额外图表库依赖；BigInt/十进制原值用于比例计算及金额格式化，完整精确结果留在表格。NULL 留为空白，不替换成零；图表限制前 30 行避免把管理页面当成无限数据浏览器。

受控 UI 测试与真实浏览器流程分开归档。最终浏览器三个引导式任务通过，但多意图退款前轮曾只查询政策而未准备审批；明确记录该失败和实际 Token，不把改写提示后的通过解释成模型泛化改进。跨重启恢复待审批任务时按 Task ID 取最新累计 usage，避免把同一任务先前模型调用重复计算。测试库使用独立 Chroma 前缀、数据目录和 Redis DB；这提供本机验证隔离，不等于生产多租户方案。

## 明确评估合同，保留未知和失败

本阶段新增 `evaluations` 包，不复制业务规则，也不让评估器通过特殊接口绕过确认。严格 Pydantic 合同冻结可核对的状态、工具、参数、引用、SQL 和预期拒绝，缺证据标为 `manual_review` 或失败。18 条自编场景分 development/regression/validation 呈现，validation 在首次在线运行前冻结；编写者知晓代码和语料，不称外部独立盲评。历史多意图退款失败纳入 regression，不通过事后改题或删除失败提高成绩。

引用当前版本校验、连续原文结构验证和片段语义足够是不同判断。工具名称和部分 DTO 参数也不能证明原始模型参数完整正确；只在实际采集原始调用后做 schema 检查，无法取得证据时保留复核状态。人工 review 未执行时不伪造标签或把 `manual_review` 算通过。

SQL 输出使用语义列角色与精确 Decimal 单元格核对，参考从真实订单/明细/退款基表独立计算，不通过生成 SQL 或相同 API 结果自我证明。AST 合同限定受支持表达式的指标、日期、聚合和排序；数学等价但不在评估器识别范围的 SQL 标为复核，而不是谎称通用等价证明或放宽产品授权边界。

重试、预算与人工中断使用明确受控故障输入直接执行生产 LangGraph；模型与 Service 是测试 fixture，Checkpoint 为内存，不依赖付费网络来稳定制造故障。结果标记 `controlled_fault_injection`、provider usage N/A，fixture 计账不进入在线消费。真实数据库与持久 Checkpoint 的安全仍由原有 PostgreSQL 集成测试验证。

## 选择 LangSmith 摘要与默认本地追踪

项目已有 LangGraph 依赖链中的 `langsmith==0.14.8`，本阶段选择它实现可选摘要出口，不再额外引入 Langfuse 服务、数据库或另一套 SDK。选择依据是复用现有稳定锁和满足当前作品集范围，不能把两个产品的名字互换，或把 SDK 存在当作云导出已验收。

本地 JSONL 采用显式元数据字段白名单，不序列化完整状态再尝试遮盖。保留 Task/segment UUID、固定节点与工具名、安全错误、耗时、累计 Token 和未知消费；不记录问题、回复、工具参数、SQL、文档、用户/订单数据。request ID 仅保存 SHA256。默认本地开启，远端需要项目专用开关、key、endpoint、project 全部显式配置；应用调用仍关闭 LangGraph 自动 tracing，避免继承 ambient 配置上传完整 prompt。

远端 SDK Client 禁用环境代理/netrc、重定向和自动 retry/batching，限制目的 origin、connect/read 时间和有限队列；元数据导出失败不改变业务结果。root/segment 关联用于查看累计状态，步骤 span 明确为执行后快照而非真实时序。此实现没有 durable queue、日志轮转、跨进程文件锁或发送保证，本阶段也没有真实 LangSmith 云端发送凭据与成绩。

实际冻结 18 案例的 5 pass/3 fail/9 manual_review/1 unobserved 保留，不为了展示成功改写失败。复杂退款未准备审批和知识规划失败揭示模型限制；退款“比例”对 percent oracle 的歧义与双周相同值/不同形状揭示 judge 范围。缺完整原始模型参数和人工语义标签时，schema/来源结构通过不能推导需求质量。修复评估参照和后续物流选样只改善软件证据采集，不把旧 run 改称新的模型成绩；完整 1034、参照专项 104 与最终定向 112 不累加，最后源码并未再跑全量或模型。详见[验收报告](../evals/results/phase8/report.md)。

## 后续边界

- Phase9：应用镜像、Linux锁验证、全栈Compose、CI与干净启动。

当前只验证本地模拟系统，不推送密钥、不接真实支付、不未经授权公开部署。
