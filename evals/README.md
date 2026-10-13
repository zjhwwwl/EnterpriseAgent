# Phase 3 检索评估

本目录包含人工依据项目五篇模拟政策编写的中文数据集。数据集有 **30 条问题：25 条可回答，5 条无对应证据**。dev 集有 10 条，test 保留集有 20 条；test 的 17 条可回答问题和 3 条无证据问题分别统计。该规模适合可复现的作品集演示，不能外推到真实企业、大型知识库或通用中文问答。

当前评估实际执行 PostgreSQL、Chroma 和已校验的本地 ONNX Embedding/Reranker。它不创建账号、不导入种子数据、不自动索引、不下载模型，也不调用 DeepSeek 或其他生成模型。没有实际运行前不会提供指标或声称优化有效。

## 数据与版本

[datasets/rag.json](datasets/rag.json) 标注 query、dev/test、是否可回答、相关文档文件名和人工依据。相关性是文档级二元标签，参考文档来自实际的 `data/knowledge/*.md`。多文档标签表示这些文档均与问题相关。

数据集固定记录五篇文档的 SHA256。执行器同时检查本地原件、数据库登记的 checksum、活动索引代数及当前模型/分块 fingerprint。源文档变更后必须人工核对并修订标签与校验值，不能忽略版本不匹配继续评估。上传的用户文档不会参与本对照实验。

dev 用于调整分块、检索参数和拒答阈值；参数选定后再查看 test 主结果。该数据集由项目作者人工编写，不能宣称是真正盲评。不得根据 test 结果反复改阈值后继续把它当作未经调参的保留集。

## 执行

先按照项目启动文档完成迁移、模拟数据初始化、Chroma 服务启动、本地模型准备及五篇种子文档索引。评估使用普通应用数据库账号，禁止用迁移 owner 连接。启用 RAG，并保持模型目录、Chunk 设置与索引时一致。

模型文件校验可独立执行，不联网下载：

```powershell
.\.venv\Scripts\python.exe scripts\prepare-rag-models.py --verify-only
```

运行真实对照：

```powershell
.\.venv\Scripts\python.exe -m enterprise_agent.evaluation --output var/evaluation/phase3
```

环境配置从项目 Settings 读取，例如 `EA_DATABASE_URL`、`EA_JWT_SECRET`、`EA_RAG_ENABLED=true`、`EA_RAG_MODEL_DIR` 和 Chroma 地址。凭据保留在本地环境或不提交的 .env，不能写入数据集或评估报告。执行器也不输出数据库 URL。

可选 `--dataset` 指定数据集文件，`--top-k` 取 5 至 10；两种配置共用相同 top_k，报告记录实际值。默认 top_k 是 **5 个 Chunk**，不是五篇不同文档。

评估账号是已初始化的 `demo_customer`，只检索固定公开政策。评估本身不执行登录，也不修改业务数据。

## 比较协议

对比配置为：

- A：纯向量检索。
- B：BM25 + 向量检索 + RRF + 真实本地 Reranker。

两组使用相同 corpus、Embedding、Chunk 索引和 top_k。Service 的向量与 BM25 候选上限均为 30，RRF 合并后保留最多 30 个候选。候选数是上限；少于该数量时使用真实可用结果，不补造候选。

每模式先使用相同 dev 查询 warmup 一次，单独记录耗时，不计入指标。正式查询按数据集顺序执行，模式顺序逐查询交替，降低固定先后顺序的影响。一个问题每模式只执行一次，不构成严谨的性能基准。

检索调用采用 `apply_gate=False`，保留原始 top_k 引用以衡量召回；`status` 仍由 Service 的真实证据阈值判定，拒答评估无需重复运行模型。Reranker 返回 logit 经 sigmoid 得到的 **0–1 未校准分数**，不能当作概率。所有模式需向量相似度通过门槛；hybrid_rerank 还需同时通过重排门槛。初轮运行后发现的门控逻辑问题与重跑见下方记录，不能将见过 test 后的修正称为盲评。

## 指标口径

先按文档 ID 去重，保留首次出现的顺序，再与 relevant_files 比较：

- Recall@1/3/5：前 K 篇去重文档中的相关文档数 ÷ 人工标注相关文档数。
- MRR：第一篇相关文档排名倒数的均值。
- nDCG@5：二元相关性，以 log2(rank+1) 折扣，与理想排序比较。
- 可回答问题门控接受率、无证据问题拒绝率与全部问题门控正确率分别计算。
- 引用有效率：独立调用真实 `Service.citation`，验证权限、活动版本、Chunk 文本、位置及引用标识一致。
- 执行完成率与错误数：保留真实失败，不从检索指标分母中删除失败问题。
- mean/P95 latency：实际 Service 调用的墙钟耗时，含数据库与模型计算，排除后续引用验证；P95 使用 nearest-rank 定义。

返回 Chunk 去重后可能不足五篇文档，未召回文档按未命中处理。无证据问题没有“相关文档”，其 Recall/MRR/nDCG 标记为 N/A，只参与门控拒绝与执行统计。

引用有效只证明来源存在、权限与内容吻合，不代表该片段的语义足以支持回答。当前阶段返回证据，不生成自然语言政策答案；生成答案忠实度、Agent 任务完成率及业务工具正确性属于后续评估范围。

生成模型使用量来自真实 Service 返回记录，当前 RAG 路径没有生成模型调用。Embedding/Reranker 是本地计算，不提供在线 API Token 使用统计，因此相关字段为 null/N/A，不能写成“本地 Token 消耗为零”。

## 产物与复现

指定输出目录生成：

- `results.json`：元数据、每查询两组完整排序与分数、引用验证、门控状态、stage_ms、错误及分组汇总。
- `metrics.csv`：dev/test 分开的两组汇总。
- `queries.csv`：逐查询可读记录。
- `report.md`：test 主结果、dev 调参结果、实际耗时及口径。

JSON 在运行中保留 `running` 状态与已完成查询。部分失败、取消或依赖错误不能冒充完整成功；只以最终 run_status 判定完整性。查询或引用校验失败时 CLI 返回非零，并保留已经测得的数据。执行器结束时核对源码、数据集、原件、语料及模型 manifest hash；运行期间变化时，对照报告无效。

报告记录 UTC 时间、Git commit 与 dirty 状态、数据集 hash、语料版本和索引代数、模型仓库固定 revision、manifest/hash、依赖与代码 hash、CPU/系统信息及实际配置。运行环境应保持不变。保存原始失败和较差结果，不能只保留表现更好的运行。

至少保留一份实际执行的 A/B 报告后再讨论是否改进。单纯检索 Recall 较高，不代表系统已完成 Agent、MCP、NL SQL 或真实企业部署。


## 本次对照与 QA 修正记录

初轮运行完整归档于 [results/phase3-initial/](results/phase3-initial/report.md)。文档级检索指标相同，但 hybrid_rerank 对 test 的 3 条无证据问题只拒绝了 2 条（66.67%）；`test-18` 的无关医疗剂量问题被单独的重排门槛放行。初轮结果仍完整保留。

查看该结果后，将通用证据门控修正为：所有模式必须向量相似度至少0.45；hybrid_rerank另外必须 sigmoid 重排分数至少0.05。数值没有在此次修正中重新挑选，但 **test 已参与 QA**，后续重跑不能称为盲评或未参与开发的保留集。要衡量泛化，需要新增独立未见过的问题。

源码冻结后的最终真实 30 问对照开始于 2026-10-13T02:28:23 UTC，使用 2026-10-12 DB 种子：test 的 17 条可回答问题，两组 R@1=0.941、R@3/5=1、MRR=1、nDCG@5=1；3 条无证据问题均拒绝，引用有效率 100%，执行错误 0。Service mean/P95 为 vector 38.529/42.376 ms，hybrid_rerank 1008.561/1136.540 ms。dev 独立列于报告，不混入 test 主结果。运行结束核对源码、数据与模型 manifest hash 稳定；没有并行本项目 pytest，但不构成受控全机静默性能测量。

完整 [报告](results/phase3/report.md)、[JSON](results/phase3/results.json)、[指标 CSV](results/phase3/metrics.csv)、[逐查询 CSV](results/phase3/queries.csv) 与脱敏 [测试](results/phase3/verification.json)、[HTTP 验收](results/phase3/http.json) 均已归档。生成模型调用及输入/输出 Token 均为实际 0，本地模型 API usage 为 N/A。报告保留当时的 Git commit 与 dirty=true，不把后续提交回写成运行时状态。

本次小语料上混合重排没有改善文档级召回/排序，且明显增加延迟。该观察不应被包装为“Reranker已经提升准确率”，也不能由3条无证据问题推导通用拒答可靠性。
