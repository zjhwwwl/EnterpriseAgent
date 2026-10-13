# Phase 3：企业知识检索验收记录

## 当前阶段状态

代码范围为 **0.3.0 / Phase 3**。知识库管理、真实本地检索、完整检查及实际 HTTP 验收已通过：**309 项测试通过，0 失败、0 错误、0 跳过**。最终冻结源码后的 30 问真实对照已完成并归档，运行结束核对源码、数据与模型 manifest hash 均稳定。

Phase 1 和 Phase 2 的历史记录保持原样，不把其测试数量与本阶段分组数量相加作为新全量成绩。

## 已实现范围

- PDF、DOCX、UTF-8 TXT 与 Markdown 的真实解析；来源页/段落/表格/行号与字符范围。
- 字符限长、不跨 section 的分块，默认 500 字符、80 重叠。
- 固定 revision、文件大小与 SHA256 校验的中文 BGE 512 维 Embedding 和量化 BGE Reranker，真实 CPU ONNX 推理。
- 官方 Chroma 1.5.9 HTTP SDK、本地 Linux Chroma 容器、中文字符/二元组 BM25、RRF、Reranker。
- upload/list/detail/download/reindex/delete/search/citation API，角色可见性、admin 维护、当前代数引用与证据不足提示。
- `0002_knowledge` 索引发布字段、并发尝试 fencing、失败状态及数据库政策来源保护。
- dev/test 分开的 30 条中文数据集与真实对照 CLI，JSON/CSV/Markdown、独立引用校验及实际耗时。
- 明确模型准备、索引、评估与完整检查脚本；所有大文件、缓存与基础设施数据优先 D 盘。

返回固定 `answer_mode=evidence_only`。当前 **没有** DeepSeek 生成回答、LangGraph、Agent Tool Calling、多轮/Checkpoint、MCP、NL SQL、Vue 或独立 Worker。相关性门槛不是语义充分性的证明。

## 环境与已有真实证据

| 项目 | 当前已确认事实 |
| --- | --- |
| Python | Windows 项目 .venv，Python 3.13 |
| 模型 | 中文 BGE 512 维与量化 Reranker，CPU，固定 revision/SHA；文件总计 396,800,683 字节 |
| 模型目录 | 项目 D 盘 `var/models`，本地 manifest 校验 |
| Chroma | Linux 镜像/客户端 1.5.9，loopback 58000，实际 D 盘持久化挂载 |
| 业务与向量语料 | 五篇模拟种子文档已真实索引，44 个 Chunk，活动 generation=1 |
| 本地模型检查 | 3 项通过，2.08 秒；包含真实输出，不作为性能基准 |
| 解析/排序/runtime 分组 | 66 项通过，0.75 秒；分组回归，不代表完整阶段 |
| 政策来源 PostgreSQL 分组 | 20 项通过，65.0 秒；真实测试库与权限/约束 |

这些结果是对应分组运行的观察值。Linux 已运行基础设施容器，**Windows API 和 ONNX** 为应用验证环境；没有 Linux 应用镜像或跨平台安装验收证明。

## 验收结果

| 验收 | 当前状态 |
| --- | --- |
| 全套 Ruff、格式、pytest、pip check | Ruff lint/格式89文件通过，pip check通过 |
| PostgreSQL / 模型 / Chroma 必需条件下的完整 pytest | 309通过/0失败/0错误/0跳过，327.041秒，真实phase3.xml/json |
| 真实 authenticated knowledge API 上传、重索引、引用与角色回归 | 15项真实TCP检查全部通过，见var/test-results/phase3-demo-http.json |
| 30 条真实向量 vs 混合 + Reranker 对照 | 最终运行 complete；初轮和最终报告、JSON、两份 CSV 已分别归档 |
| Alembic 模型差异检查 | No new upgrade operations，已通过 |
| 当前 8000 服务重启为 Phase 3、live/ready/OpenAPI | 已为Phase3；application/database/redis/knowledge四项OK，真实HTTP通过 |
| 清晰本地 Git Commit | 提交主题为 `feat: implement Phase 3 knowledge retrieval and evaluation`；ID 见本地 git log，验收数据在提交前生成 |

真实TCP检查在2026-10-13T02:21:19 UTC完成：公开/内部multipart上传并真实索引、搜索/下载/引用、客户对内部资料所有读路径拒绝、重索引旧引用404、删除、无认证/非admin写入拒绝、退款政策来源保护及无关问题拒绝。重复索引CLI五篇均already_indexed/generation1。

另有 5 项 ASGI 上传 CORS 测试通过（0.07 秒），覆盖 Content-Length 早拒与无 Content-Length 分块超限；允许 Origin 的 413 携带 CORS/request ID，业务探测路由未收到超限正文，小文件正控实际送达。另 3 项定向回归通过（18.91 秒）：重排不能绕过向量门槛、上传后模型文件消失、数据库提交后取消仍保留原件。这些分组不与完整数量相加。

完整报告期间有51条官方Chroma SDK legacy embedding-function配置DeprecationWarning，未屏蔽且没有导致失败。警告不是完整检查失败，也没有声称已消除；升级SDK时需要重新验证当前兼容桥与官方Collection契约。

测试与 HTTP 的脱敏归档为 [verification.json](../evals/results/phase3/verification.json) 与 [http.json](../evals/results/phase3/http.json)，原始 JUnit 保留 `var/test-results/phase3.xml`。327.041 秒是测试套件时间，不是 API 性能。

## 真实对照结果

最终运行开始于 **2026-10-13T02:28:23 UTC**，使用 2026-10-12 的真实模拟数据库种子与五篇已索引文档。详细 [报告](../evals/results/phase3/report.md)、[原始 JSON](../evals/results/phase3/results.json)、[指标 CSV](../evals/results/phase3/metrics.csv)、[逐查询 CSV](../evals/results/phase3/queries.csv) 均已归档。

| test 模式 | 可回答/无证据 | R@1 | R@3/5 | MRR | nDCG@5 | 无证据拒绝 | 引用有效 | mean/P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| vector | 17/3 | 0.941 | 1/1 | 1 | 1 | 3/3 | 100% | 38.529/42.376 | 0 |
| hybrid_rerank | 17/3 | 0.941 | 1/1 | 1 | 1 | 3/3 | 100% | 1008.561/1136.540 | 0 |

dev 的 8 条可回答、2 条无证据单独统计：两组 R@1=0.812、R@3/5=1、MRR=0.938、nDCG@5=0.954；vector mean/P95 为 37.390/39.510 ms，hybrid_rerank 为 990.552/1067.304 ms。生成模型实际调用及输入/输出 Token 均为 0，本地 Embedding/Reranker 的 API usage 为 N/A。

初轮 hybrid_rerank 只拒绝 test 无证据问题的 2/3；查看 test 后修正为向量与 sigmoid 重排分数双门槛，保留 [初轮原始报告](../evals/results/phase3-initial/report.md)。test 参与了 QA，不能称盲评。最终运行没有并行本项目 pytest，但不是受控全机静默性能测量；两组文档级指标相同且混合重排更慢，没有据此宣称准确率提升。

## 启动和检查

```powershell
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

默认完整检查要求真实 PostgreSQL 与 RAG。显式 Skip 参数或无条件直接 pytest 可能跳过环境用例，不能替代完整阶段验收。检索评估不 seed、不索引、不调用生成 LLM；缺语料、checksum 或索引 fingerprint 不一致时拒绝运行。

## 当前限制

1. PDF 无 OCR；DOCX 只含正文与表格，缺页眉页脚、文本框和修订文本；TXT/MD 仅 UTF-8。
2. Chunk 是字符预算，模型上限 512 Token，长文本会被 SDK 截断；输入分数不是校准概率。
3. 索引在请求/CLI 内运行，本地推理串行控制容量，不是持久 Worker 或高并发生产设计。
4. PostgreSQL 与 Chroma 不在同一事务。代数/job 校验保护活动索引；向量清理失败可能留下无效条目。数据库持久化开始后未知提交结果仍保留原件，可能留下未登记文件，没有自动清理工具。
5. 证据存在、位置有效与分数通过，不代表回答语义正确。当前评估不包含生成答案忠实度或 Agent 任务完成率。
6. 小型人工政策集是作品集对照样本，不是真实企业数据、盲评或生产性能测试。初轮test无关问题暴露门控错误；查看test后修复向量/重排AND条件，所以test已参与QA，不能称未参与开发的保留成绩；两次原始结果均保留。
7. 尚无公网发布、真实支付退款、完整应用容器、GitHub Actions 或 Vue 浏览器证据。

## 下一阶段

Phase 4 接入 LangGraph 单 Agent、多业务工具、可信 State、最大步数/超时/有限重试、PostgreSQL Checkpoint、多轮会话与人工中断。沿用现有归属、确认、幂等和事务规则；实测图恢复与业务提交之间的失败场景。在线 DeepSeek 调用必须使用实际 API 响应和 usage，不能用替身宣称模型成功。
