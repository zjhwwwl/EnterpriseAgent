# 测试与 Evaluation

## 真实结果的边界

Phase 4 在真实检索与业务测试上增加LangGraph、Tool Calling协议、人工中断、官方持久化、重启恢复与故障边界测试。[Phase 4](phase-4.md)初验511项通过、零失败/错误/跳过，后续真实在线手选烟测最终5/5检查通过；这不是完整模型质量评估，在线修复后[517项完整复验通过](../evals/results/phase4-live/verification.json)。历史检索对照保留于[Phase 3](phase-3.md)，不当作Phase 4 Agent完整效果分数，Phase 1/2历史验收保留原文件。

```powershell
.\scripts\check.ps1
.\scripts\evaluate-rag.ps1
```

check 执行 Ruff、格式、pytest、pip check，默认强制 `--require-postgres --require-rag`，保存 `var/test-results/phase4.xml`、`phase4.json`。`-SkipPostgres` / `-SkipRag` 明确排除对应环境检查；直接 pytest 未配置依赖可能跳过，不能当作完整验收。

## 测试分层

| 层 | 当前证据 |
| --- | --- |
| 确定性单元 | 配置、错误、日志、Argon2/JWT、退款规则、真实文件解析、分块覆盖、BM25/RRF、指标计算、runtime 容量 |
| PostgreSQL 集成 | 迁移、普通账号、归属、事务、并发、幂等、确认、审核、政策版本/来源 trigger |
| 进程内 HTTP | Pydantic、认证、角色、订单与知识接口契约、错误、不回显输入 |
| 真实进程 HTTP | Uvicorn + TCP、数据库退款闭环与服务停止监听；新增知识路径按实际报告验收 |
| 真实模型/Chroma | 固定模型校验、真实向量及重排、官方 SDK/实际向量库；没有内置假向量回退 |
| Retrieval Evaluation | 固定五文档、同配置对照、文档级相关性、引用合法性和耗时 |
| Tool 契约 | 严格参数、禁止身份/确认注入、复用 Service、安全错误；单元控制不当数据库证明 |
| Chat HTTP 协议 | 受控 HTTP 响应验证 Tool Call、usage、非法 JSON、未知工具、响应上限和超时，不调用线上模型 |
| 官方 Checkpoint | 实际 PostgreSQL saver、runtime 重启、interrupt/resume、普通账号无 DDL、advisory lock 与取消释放 |
| 真实 Agent 流程 | ScriptedChatModel 只控制路线；业务数据库、CPU Embedding/Reranker、Chroma 与官方图状态实际执行 |

控制流程测试可以注入明确测试替身来隔离故障，但不能用这些结果冒充真实模型或向量库成绩。事务/锁/权限测试使用 PostgreSQL，不使用 SQLite 替代。解析测试使用实际 PDF/DOCX bytes，不用粘贴文本充当 PDF 解析证明。

## 数据集

[rag.json](../evals/datasets/rag.json) 共 **30 条：25 可回答、5 无证据**；dev=10，test=20，其中 test 有 17 可回答和 3 无证据。依据当前五篇实际模拟文档人工标注 relevant_files、expected_status 与事实点，固定 SHA256。

这是文档级二元相关性集，不是生成答案评分集。多文档标签用于相关文档召回；引用存在与片段在语义上足以支持某个结论分开看待。详细协议见 [evals/README](../evals/README.md)。

dev 用于分块、检索和门槛调参；test 单独呈现为主结果。不把人工自编数据称为盲评，也不使用 test 反复选择阈值后宣称未参与调参。

## 真实 A/B 执行

`python -m enterprise_agent.evaluation --output var/evaluation/phase3` 使用受限应用账号、真实 ModelRuntime、ChromaStore 和 KnowledgeService。它不导入种子、不自动索引、不下载模型、不调用 DeepSeek。

固定语料来自五篇 `data/knowledge/*.md`，通过已登记文档 ID 限制 corpus，用户上传内容不进入本次对照。原件与 DB checksum、可见性、索引代数和 fingerprint 必须一致。

- A：纯 Vector。
- B：中文 BM25 + Vector + RRF + 真实 Reranker。
- 相同 Embedding、原件、Chunk、top_k 与运行环境；向量/BM25 上限各 30，RRF k=60，融合最多 30。
- 默认 top_k=5 个 Chunk；先按文档 ID 去重，可能少于五篇不同文档。
- 每模式同一 dev 查询 warmup 一次，记录但不计指标。
- 正式问题按数据集顺序执行，模式顺序交替。
- `apply_gate=False` 保留原始候选排名，status 仍按 Service 的真实相关性门槛判断，无需重复模型推理。

Reranker 返回 logit 经 sigmoid 得到的0–1未校准分数，不是概率。所有模式需向量相似度通过门槛，hybrid_rerank另需同时通过重排门槛；门控只检查相关性，不能证明语义充分、政策无冲突或生成答案忠实。

## 指标定义

| 指标 | 当前计算口径 |
| --- | --- |
| Recall@1/3/5 | 前 K 篇去重文档中的相关文档数 / 标注相关文档数 |
| MRR | 第一篇相关文档排名倒数的均值 |
| nDCG@5 | 二元相关性、log2(rank+1) 折扣、归一化理想排序 |
| 门控 | 可回答接受率、无证据拒绝率、全部问题正确率 |
| 引用有效率 | 单独调用真实 citation API/Service 校验文本、位置、权限与当前代数 |
| 执行 | 完成率、错误数量，失败不从可回答指标分母删除 |
| latency | Service 真实墙钟 mean/P95，排除后续引用验证；P95 为 nearest-rank |
| 生成模型使用量 | Service 实际返回的调用与输入/输出 Token；当前路径没有生成调用 |
| 本地模型 API 使用量 | null / N/A，本地 Embedding/Reranker没有在线计费 usage |

无证据问题没有相关文档，Recall/MRR/nDCG 为 N/A，只参与门控与执行统计。一次运行每问题每模式一次，不能当作严谨性能基准；冷启动成本在 warmup 单列，不混入热查询平均数。

## 产物与版本

输出目录包含 results.json、metrics.csv、queries.csv、report.md。JSON 保存每查询完整排名、真实分数、引用检查、stage_ms、状态和失败；CSV 与 Markdown 分开 dev/test 汇总。

记录 Git commit/dirty、UTC、数据集 hash、代码 hash、模型仓库 fixed revision 与文件 hash、实际 manifest hash、依赖、CPU/系统、语料 checksum/version/generation 及配置。原件 checksum 在执行前校验；结束时核对源码、数据集、数据库语料版本与模型 manifest，变化时比较无效。部分、失败或中止运行不算完整成功，执行器保留测得记录。

文件 SHA256 记录运行时的原始字节；Git 换行归一化可能使检出文件的 SHA 不同。复现时同时核对 Git 版本、平台、模型与配置。

检查通过及引用合法不能替代开放式语义正确性。已有真实DeepSeek手选5场景烟测，但没有独立的完整在线任务/意图/参数/语义质量分数，不能将5/5检查通过称模型任务完成率100%；NL SQL与MCP端到端分数也尚未提供。

## Phase 4 受控图验收

tests/integration/test_agent_workflow.py 使用固定 seed UTC 2026-10-12、真实普通 PostgreSQL 连接、真实固定 CPU 模型、独立 Chroma 集合、适用退款政策索引与官方 PostgreSQL Checkpointer。ScriptedChatModel 给出明确 route_task / 业务工具规划，assistant/tool_calls JSON 符合兼容协议，脚本耗尽会失败。

覆盖待确认无退款/工单、task_id 准备重放、runtime 重启后一次确认、重复确认、跨用户拒绝、拒绝审计、同会话 busy、参数拒绝、工具/步数上限、有限临时错误重试与多轮历史。在真实 prepare/confirm 事务返回后注入 CancelledError 模拟 worker loss；SDK包装后的可恢复失败经 recover 重跑，实际审批、退款、工单和审计只有原记录。不宣称OS进程恰好在业务提交瞬间被杀的实测。独立Uvicorn TCP测试在waiting_approval阶段真实停止/重启进程后确认一次。重索引跨审批后仍保留已提交事实，并明确清除陈旧引用、记录 warnings。

这验证软件控制与业务一致性，不证明模型理解。受控usage仅验证计账，不列为实际消费。初验161项unit、37项真实PG/RAG/API、1项TCP预检及完整511项通过（JUnit524.068秒、81条Chroma warning）保留原始记录；TCP JSON仍controlled_model_only=true、online_deepseek_tested=false、provider_token_usage=null。后续在线修复110项核心回归通过，冻结源码[517项完整复验通过](../evals/results/phase4-live/verification.json)；旧511摘要不覆盖为新代码证明。

真实在线运行必须另外记录所用模型、供应商时间与usage、源码/数据版本、完整任务结果及人工质量标签。未报告usage的失败标记 unknown_usage_calls 与 usage_complete=false，已知Token和不能代替完整消费。中断的不完整标记跨 recover 保留，恢复后也不能把未知消费宣称完整或归零。缺Key或未实际运行时明确保留“未在线验收”，不补零Token或预填正确率。

Phase 4公共证据已归档到evals/results/phase4/：[verification.json](../evals/results/phase4/verification.json)、[http-restart.json](../evals/results/phase4/http-restart.json)、[demo-http.json](../evals/results/phase4/demo-http.json)、[report.md](../evals/results/phase4/report.md)。历史Phase3原始报告保持不变，最终图测试与HTTP证据不替代在线模型语义评估。

## 后续评估

真实在线烟测独立归档于[phase4-live报告](../evals/results/phase4-live/report.md)、[最终JSON](../evals/results/phase4-live/results.json)与[CSV](../evals/results/phase4-live/results.csv)。最终5场景检查5/5通过，其中越权按预期任务failed；12次调用、prompt11315/completion875/total12190 Token，无未知消费。首探针和4/5、3/5、4/5失败轮均保留，已知累计45443 Token/46次调用；不得仅列最终消费。模型标签为操作者提供，非供应商响应版本名。

5个问题手选用于开发修复，所有轮次均非盲评，不能推导统计提升或泛化质量。检查器新增区分检索不足与候选拒答，旧三轮仍有真实失败；RAG阈值未变。实际源引用用HTTP验证，耗时仅本次观察，不是性能基准，Token是供应商实际报告的已知用量。在线运行需脚本显式--allow-live；当前天气问题拒答、七天政策等少样本观察不替代独立领域集。

Phase 5–8 在已有真实图与业务一致性测试之上扩展 MCP、SQL 和在线任务/工具/回答语义数据集。只读 SQL 绕过、工具意图和参数效果、引文语义充分性、政策冲突及领域外问题需要独立标注，不能仅靠 schema 通过或 substring 匹配证明。

线上模型缺 Key 或未经实际调用时明确标记未运行；Token 从实际 API usage 获取。优化没有改善也保留原始结果，不预填分数、伪造压测或部署成绩。


## 已归档的真实对照

初轮 hybrid_rerank 的 test 无证据拒绝率为 2/3，`test-18` 被单独重排门槛放行。[初轮报告](../evals/results/phase3-initial/report.md) 与 JSON/CSV 完整保留。查看 test 结果后，把所有模式的向量门槛与 hybrid_rerank 的重排门槛合并为 AND，原 0.45/0.05 数值保持不变。

**test参与了QA修正，因此修正后的结果不能称为盲评或未参与开发的保留成绩。** 两次结果保留以便审查，泛化评估需新独立问题。

源码冻结后的最终运行（开始于 2026-10-13T02:28:23 UTC），真实 DB seed 为 2026-10-12：

| test 模式 | 可回答/无证据 | R@1 | R@3 | R@5 | MRR | nDCG@5 | 无证据拒绝 | 引用有效 | mean/P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vector | 17/3 | 0.941 | 1 | 1 | 1 | 1 | 3/3 | 100% | 38.529/42.376 | 0 |
| hybrid_rerank | 17/3 | 0.941 | 1 | 1 | 1 | 1 | 3/3 | 100% | 1008.561/1136.540 | 0 |

这是一次真实Service查询时间的观察，不是HTTP或生成答案性能。该集上混合重排没有改善文档级召回/排序，延迟更高；没有据此宣称优化有效。LLM实际调用及输入/输出Token均为0，本地模型计费usage为N/A。

dev 8/2 单独统计：两组 R@1=0.812、R@3/5=1、MRR=0.938、nDCG@5=0.954；vector mean/P95 37.390/39.510 ms，hybrid_rerank 990.552/1067.304 ms。最终运行没有并行本项目 pytest，但没有控制全机负载，不能称为严格性能基准。

最终 [报告](../evals/results/phase3/report.md)、[JSON](../evals/results/phase3/results.json)、[指标 CSV](../evals/results/phase3/metrics.csv)、[逐查询 CSV](../evals/results/phase3/queries.csv) 已归档。运行结束源码、数据与 manifest hash 稳定；完整测试及真实 HTTP 证据分别见 [verification.json](../evals/results/phase3/verification.json) 与 [http.json](../evals/results/phase3/http.json)。评估时 Git 为 Phase 2 提交且 dirty=true，准确反映 Phase 3 尚未提交的工作区；不回写为后续提交或伪造干净状态。
