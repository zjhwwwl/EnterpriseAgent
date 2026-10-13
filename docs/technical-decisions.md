# 技术决策

当前 Phase 3 已实现业务 API 与可追溯证据检索。以下将当前取舍与后续规划分开，不能将设计决定当作运行成绩。

## 模块化单体，后续单 Agent 多工具

API、Service、Repository、Schemas、RAG 和 CLI 在同一包内维护。当前索引通过请求或CLI执行，先实现可解释的权限、事务与失败边界，不增加独立 Worker 框架、微服务、Kubernetes 或通用规则引擎。

Phase 4 使用一张 LangGraph 图按任务分支编排，复用当前 Service。多 Agent 协商、任务调度与追踪只有实际需要并完成验证后再增加。

## Python 3.13 与哈希锁

pyproject 声明依赖，requirements/base.lock、dev.lock 固定版本及分发文件哈希；bootstrap 使用 --require-hashes，再 --no-deps --no-build-isolation 可编辑安装。当前在 Windows Python 3.13 验证，不将锁文件存在当作 Linux 应用兼容证明。

使用项目 .venv、D 盘缓存与 TEMP/TMP，不修改全局 Python。Phase 3 本地推理采用 ONNX/FastEmbed，避免为展示功能引入整套训练框架；不能据此声称已经有GPU验证或训练能力。

## 工厂、日志与生命周期

create_app 可注入配置，导入不创建全局运行实例或提前读取实际环境。main.py 通过项目 CLI 保留 JSON 日志并关闭原始 access log；Windows 使用 Selector event loop 兼容异步 psycopg。

日志不保存请求体、查询字符串、Authorization、秘密或全量环境。统一错误保留HTTP状态和request_id，输入校验不回显原始字段。健康区分存活与真实依赖就绪，启动不自动迁移、seed、模型下载或索引。

## 数据库权限与业务事务

普通非owner账号只获明确逐表/列权限；订单id UPDATE用于FOR UPDATE，不授权状态或金额。审计仅追加，政策只读。确认/退款/工单/幂等、agent_tasks/agent_sessions已在迁移中授予DML，不虚称其尚未授权；任务表尚无图执行/API。知识文档INSERT/UPDATE与Chunk INSERT/DELETE由0002显式增加。

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

单独返回模型/阶段分数与来源。所有模式需向量相似度通过门槛，hybrid_rerank还需sigmoid重排分数通过门槛。分数只是相关性筛选，不是概率校准、语义蕴含或政策冲突证明。当前answer_mode=evidence_only，不宣称已生成忠实答案；LangGraph Agentic RAG、查询改写和验证节点属于Phase4。

## 索引发布代数与SQL权威

先写尝试代数/job ID，再解析、嵌入和写向量，最后锁文档验证同一次尝试后发布SQL Chunk与活动index。失败保留原件、错误和旧活动索引，过期工作不能覆盖新代数。

检索前后和引用详情均依据SQL可见性、活动代数与fingerprint。SQL/Chroma没有分布式事务，清理失败可能留下不可检索条目。数据库持久化开始后，未知提交结果下保留原件，避免已提交记录失去文件；仅文件写失败清理，故可能遗留未登记原件。当前没有维护清理工具或自动任务恢复。

## Chroma官方SDK兼容桥

固定chromadb-client 1.5.9与同版server。官方HTTP传输默认无限timeout，虚构chroma_server_request_timeout_seconds字段不会让请求受限，因此使用官方System/ServerAPI/Collection的窄桥，在首次网络请求前设置真实httpx session各阶段timeout，并在close释放资源。

桥没有自写REST协议或修改SDK全局对象，但依赖一个私有session字段；升级必须验证初始身份/租户/数据库请求、集合距离和实际延迟HTTP测试，不能只验证配置值存在。

## 可复现对照与诚实证据

30条中文dev/test人工数据、固定五文档checksum、同Embedding与top_k、每模式warmup1次、交替查询顺序，保存真实排名/引用/错误/耗时。文档级去重后Recall/MRR/nDCG与门控分别计算，失败不从分母删掉。

本地Embedding/Reranker不提供API计费Token字段，记null/N/A；生成LLM调用从真实Service记录，当前没有生成调用。dev用于调参，test单独报告，人工小集不称盲评，优化不保证改善，也不冒充性能压测。

## 后续边界

- Phase4：DeepSeek兼容接口、LangGraph、Checkpoint、有限执行和人工审批；调用限制在接入阶段验证，API Key不入前端。
- Phase5：官方MCP协议适配，真实Client发现/调用，复用Service。
- Phase6：授权视图、独立只读连接池/账号、SQL AST、超时和结果上限。
- Phase7：Vue五页、真实浏览器流程、引用和确认。
- Phase8：生成忠实度/任务/工具/SQL评估与可选Langfuse；目前没有tracing集成。
- Phase9：应用镜像、Linux锁验证、全栈Compose、CI与干净启动。

当前只验证本地模拟系统，不推送密钥、不接真实支付、不未经授权公开部署。
