# Phase 8：评估与元数据追踪

本阶段版本 0.8.0，范围是已有电商模块化单体的可复现评估、安全日志与可选追踪。保留单张 LangGraph 图、可信 Principal、后端业务规则、独立 SQL reader、人工确认及五页 Vue 后台；不新增独立 Agent、微服务、支付服务或公网部署。

## 已实现的代码

- `evaluations/dataset.py`：严格 Pydantic 案例合同、可信订单号码模板绑定、数据集加载。
- `evaluations/checks.py`：真实 Task/工具/引用/业务快照/审批/SQL 证据检查，缺证据或语义标签时保留 `manual_review`。
- `evaluations/references.py`：保守 SQL 指标/日期/聚合 AST 合同、语义列映射和独立基表事实参考。
- `evaluations/boundaries.py`：生产 LangGraph 的声明故障注入，真实参数校验、显式模型/Service fixture、内存 Checkpoint 与 JSON/CSV/Markdown 产物。
- `evaluations/live.py` 与 `scripts/evaluate-phase8.ps1`：受守卫的真实测试库准备、独立本机 API、冻结在线案例、逐条证据与消费保存。
- `observability.py`：本地元数据 JSONL、Task/segment 关联、累计 usage 与白名单字段；可选显式 LangSmith 摘要队列。
- `AgentService` / application lifespan：每次主动执行结束后采集安全快照，统一关闭可选 exporter；LangGraph 自动 tracing 继续关闭。

严格数据集有 18 条 Agent 案例：development=3、regression=2、validation=13；16 条故障案例单独统计。案例由开发助手依据代码与模拟语料编写，编写者知道系统行为。首次运行前冻结 validation 不能称外部独立标注或盲评。

## 运行与验收约束

```powershell
.\scripts\check.ps1
.\scripts\frontend.ps1 -Mode Check
.\scripts\evaluate-phase8.ps1 -Mode Boundaries
.\scripts\evaluate-phase8.ps1 -Mode Prepare
.\scripts\evaluate-phase8.ps1 -Mode Live -AllowLive -Prepared
```

默认 `evaluate-phase8.ps1` 等同 Boundaries。Prepare 使用真实模型和 Chroma 索引专用测试数据，不发聊天请求；Live 必须显式 AllowLive，使用 127.0.0.1:8002、enterprise_agent_test、独立 Chroma 前缀 ea_phase8_eval、var/evaluation-test 和 Redis DB 2。Live 默认也会准备，Prepared 只在已有真实准备摘要和守卫通过时跳过。完整配置见[部署](deployment.md)。

默认评估不发在线模型请求。在线入口必须显式 AllowLive，并使用专用 localhost 测试 API/数据库/reader；订单绑定、当前日期和退款资格由实际事实提供。退款仅准备待确认预览，不确认，不创建退款/工单或改订单状态；执行前后验证业务不变。测试任务、会话和审批预览可以登记，它们不是实际资金退款。

结构合同、精确行、来源版本和 schema 可机器判断；原文是否充分回答问题必须人工复核。未复核语义保留 `manual_review`，真实失败保留；既有多意图退款失败在 regression 重现，不通过修改题目冒称优化成功。消费使用真实 Task 累计 usage，并保留失败/未知用量，不累加同一 Task 的多份快照。

本地追踪只保留 UUID、状态、固定工具/节点名、安全错误、耗时和 Token；request ID 保存 SHA256。不导出问题、答案、工具参数、SQL、文档和个人字段。节点 span 是执行后观察，不能声称完整实时 tracing。LangSmith 默认关闭，本阶段无云凭据或真实云端发送验收；官方 SDK 受控 transport 合同与本地 JSONL 证据分开。

每轮在线产物使用新的 var/evaluation/phase8/live/UTC 目录，失败不覆盖；检查结果含 pass、fail 与 manual_review。新 session 在付费消息前用独立测试库核对，未知响应只查询原 Task，不重发/恢复/确认。脚本成功退出仅表示记录完成，不是模型质量通过。

## 最终执行结果

[完整后端回归](../evals/results/phase8/python-regression.json) **1034 项全部通过**，0 失败/错误/跳过，JUnit 712.590 秒，强制真实 PostgreSQL/RAG；87 条 Chroma 弃用警告未屏蔽。完整运行后修正评估 SQL 参照的商品 ID 投影映射，[104 项定向复验](../evals/results/phase8/evaluation-oracle-unit.json)通过、2.909 秒；在线后再修正物流选样，最终四文件[112 项通过](../evals/results/phase8/evaluation-unit.json)、JUnit 2.898 秒/CLI 2.92 秒，Ruff、176 文件格式和 pip check 通过。未重跑完整套件，子集不累加；相对 full 冻结仅评估 references.py/live.py 改动，相对 live 冻结仅 live.py，业务规则未放宽，准确源码差异见[验收报告](../evals/results/phase8/report.md)。

本阶段前端重新执行 **43 项/6 文件**，格式、严格类型、构建和生产 audit 通过；构建 516 ms 是单次本机观察。UI chunk 984.49 kB、gzip 316.59 kB，Vite >500 kB 警告保留；没有新一轮付费浏览器或负载测试。

[RAG 对照](../evals/results/phase8/rag/report.md)重新执行真实 CPU/Chroma 检索 60 条模式记录，源码/模型 manifest/语料稳定。test 17 可回答/3 无证据：Recall@3 **0.970588 → 1.000000**、nDCG@5 **0.977244 → 1.000000**，平均耗时 **38.298 → 966.886 ms**，P95 **40.979 → 1073.442 ms**。两组拒绝无证据 3/3、错误 0，300 候选 Chunk 的结构引用核验全部匹配。已有历史 QA 集并非盲评，增加时延不能隐藏，结构引用不代替回答语义。

[独立 SQL](../evals/results/phase8/sql/report.md) **24/24**：16 人工查询精确匹配独立基表事实，8 非法输入按预期拒绝，数据指纹稳定；日期固定为种子 2026-10-12，没有模型调用，不声称 NL SQL 24/24。[受控图](../evals/results/phase8/boundaries/report.md) **16/16** 达到声明期望；34 调用、222 已知 Token、5 未知消费均为 fixture 计账，供应商请求/账单 N/A，不计入线上消费。

首次[在线冻结评估](../evals/results/phase8/live/report.md)保留原始成绩：

| split | total | pass | fail | manual_review | unobserved |
| --- | ---: | ---: | ---: | ---: | ---: |
| development | 3 | 1 | 0 | 1 | 1 |
| regression | 2 | 0 | 1 | 1 | 0 |
| validation | 13 | 4 | 2 | 7 | 0 |
| 总计 | 18 | 5 | 3 | 9 | 1 |

实际 17 唯一 Task、36 次模型调用尝试、prompt 35626 / completion 3814 / total **39440 Token**，usage_complete=true。首物流样本选到无物流订单，独立参考在消息前 404，因此未发送付费请求；在线期间选定源码与真实业务基表指纹稳定，没有提交退款、创建工单或修改订单状态。

三项 frozen fail 均保留：复杂退款只完成读取而未准备审批；优惠券知识任务无效模型规划；退款率结果 ratio 0.02 与 oracle percent 2.33 不符，但原问题“比例保留两位”有单位歧义，不能直接断言事实错误或事后调整分数。双周比较两行数值 30/13 与单行参考一致，judge 不支持对应形状，保留 manual_review。原始模型参数和语义标签不足的其他复核项不当作已通过，不以 5/18 或 Task completed 推导完整语义质量。

在线首次冻结后不重跑模型、不改原题/期望/成绩。物流选样已修正为最多 100 个拥有订单验证真实物流 200，404 跳过、其他错误立即失败，并有新增边界测试；[最终源码相对 live 冻结](../evals/results/phase8/source-postrun-comparison.json)多这一处 live.py 改动。[相对完整套件](../evals/results/phase8/source-fullsuite-release-comparison.json)另外包含参照 references.py，80 个所选文件不含测试/前端/全部部署文件。不能把第一次在线结果回写为最新源码的实时验收，最后源码/产物核对由[统一验收报告](../evals/results/phase8/report.md)记录。

[真实本地追踪](../evals/results/phase8/observability/verification.json)为 17 Task/17 JSONL 快照，request hash、状态、错误和 Token 与 Task DTO 相同，白名单元数据通过；快照的 39440 Token/36 次调用不能再次累加。LangSmith 关闭、无云凭据，SDK 合同仅受控 requests transport 验证，没有真实云端 trace 或网络抓包证明。

[本地重启证据](../evals/results/phase8/demo-ready.json)记录 API 8000 元数据 0.8.0/phase=8、六项 ready 全部通过，前端 5173 HTTP 200 且健康代理一致；新增模型请求 0。它是本机服务快照，不是新浏览器流程、Linux 应用镜像或公网部署验收。

## 已知限制与下一阶段

小型自编数据不能代表开放输入模型泛化，结构引文合法不证明语义相关；保守 SQL judge 也不是通用 SQL 等价证明。受控模型/Service 和内存 Checkpoint 故障集不当作真实模型、数据库或持久恢复成绩。本地 JSONL 没有轮转/跨进程锁，LangSmith 队列不提供 durable delivery，云端出口未真实验收。

Phase 9 将处理目标平台依赖锁、API/前端应用镜像、全栈 Docker Compose、GitHub Actions 和干净环境运行验证。当前没有 Linux 应用镜像、公网部署或真实支付/退款连接。
