# Phase 8 验收报告

0.8.0 增加可复现的 Agent Evaluation、结构化日志关联及本地 trace 元数据，提供默认关闭的 LangSmith 适配器。本目录按证据层保存实际运行结果，保留失败、人工复核项和未执行项。最终统计、验证边界及证据入口见 [verification.json](verification.json)。

## 工程回归和源码范围

[完整 Python 回归](python-regression.json)实际执行 1034 项，全部通过，0 failures / errors / skipped，JUnit 耗时 712.590 秒；运行要求真实 PostgreSQL 和 RAG 集成环境。[原始摘要](python-summary.json)保留执行脚本的统计。该套件包含明确的单元替身及真实集成测试，不把所有测试统称为真实模型调用。

完整套件之后先修正了 `src/enterprise_agent/evaluations/references.py` 的评估参照：商品查询未投影商品 ID 时，不将 ID 计入数值列角色。新增相应 SQL 参照测试后，[中间定向复验](evaluation-oracle-unit.json)104 项全部通过，JUnit 耗时 2.909 秒。最初 [103 项定向记录](evaluation-unit-initial.json)仍归档。在线评估之后又修正物流用例的绑定采样，最终 [112 项定向复验](evaluation-unit.json)全部通过，0 failures / errors / skipped，JUnit 耗时 2.898 秒；新增采样检查使用明确的 API fixture，不增加付费模型请求。

上述各次 pytest 结果不相加，112 项定向复验也不冒充重新执行完整套件。[完整回归后的第一次 80 文件比对](source-regression-comparison.json)仅参照模块改变；[完整回归到最终源码比对](source-fullsuite-release-comparison.json)仅 `references.py` 和 `live.py` 两个评估模块改变，业务 Service、生产 Agent 图和模型 SDK 没有变化。测试、前端、文档和公开证据文件没有纳入这个 80 文件哈希集合。

前端 [43 项单元回归](frontend-unit.json)全部通过。严格类型检查、Prettier、Vite 生产构建及生产依赖审计通过，构建实际耗时 516 毫秒，生产依赖漏洞数为 0。Ruff lint、176 文件格式检查及 `pip check` 通过。完整 pytest 的控制台保留 87 条 warnings，不以零失败表述为零警告。性能数字只用于记录本机实际运行，不作为性能基准。

## 已完成的受控检查

`boundaries/results.json`、`boundaries/results.csv` 和 `boundaries/report.md` 来自 16 个离线受控边界用例。实际运行生产 LangGraph、工具参数校验和 interrupt；模型、业务 Service 返回及知识政策为明确的 fixture，checkpoint 使用 InMemorySaver。16 个用例符合各自预期，包括预期失败、有限重试、预算终止、可信身份和人工确认。这不是在线模型任务完成率，也没有执行真实业务数据库。

供应商消费为 `null` / `not_applicable_no_provider_requests`。fixture 中 34 次调用、222 Token 及 5 次未知消费只用于账本边界检查，不加入真实模型消费。

上述受控边界与前端、pytest 层不代替在线模型质量和远程 Tracing 验证。

## 真实数据准备和独立 SQL 对照

[准备记录](preparation.json)保留已有的虚构种子数据，不重置数据库：25 用户、30 商品、500 订单、1016 明细、456 物流、19 退款、19 工单和 5 文档是准备时快照，后续会话、任务与审计数量会变化。五份文档实际解析、切分、嵌入并索引到独立 Chroma prefix `ea_phase8_eval`，共 44 chunk，模型供应商请求数为 0。

[SQL 结果](sql/results.json)及 [SQL 报告](sql/report.md)实际完成 24 个用例：16 个人工编写的分析视图查询与独立基础表聚合逐值一致，8 个 SQL 拒绝用例符合预期，数据前后指纹一致。固定问题日期为 2026-10-12、时区 Asia/Shanghai；这是种子数据的固定 SQL 回归，不是当前日期的在线业务统计。参考聚合来自独立基表，避免将同一视图查询自身当作正确性参照。SQL 执行并无模型请求，Provider Token 为 N/A；这些结果不推导自然语言转 SQL 准确率，也不构成完整线上安全保证。

## 真实检索配置对照

[RAG 结果](rag/results.json)、[逐查询 CSV](rag/queries.csv)、[汇总 CSV](rag/metrics.csv)和 [执行器报告](rag/report.md)取自本阶段重新运行的真实 Embedding、Chroma、BM25 和 Reranker。沿用已有 Phase 3 检索评估器，因此执行器报告标题仍含 Phase 3；本次时间为 2026-10-14T19:01:57Z—19:02:30Z，60 条模式运行记录完整，未复用历史结果。30 个不同问题分别执行纯向量和混合检索加 Rerank，test 与 dev 分开；源码、数据集及模型清单哈希前后一致，语料未变化。

| test 指标 | 纯向量 | 混合检索 + Rerank |
| --- | ---: | ---: |
| 可回答 / 无证据问题 | 17 / 3 | 17 / 3 |
| 文档级 Recall@3 | 0.970588 | 1.000000 |
| 文档级 nDCG@5 | 0.977244 | 1.000000 |
| 检索错误 | 0 | 0 |
| 平均实际耗时 ms | 38.298 | 966.886 |
| P95 实际耗时 ms | 40.979 | 1073.442 |

Recall、MRR 和 nDCG 使用文档级二元相关性，先按文档去重，不将 Chunk 排名混为文档排名；可回答问题的 17 项是检索指标分母，无证据 3 项分别检验门控。dev 的 10 项（8 可回答、2 无证据）另行报告。此自编、已接触语料的小集不是第三方盲评，两种配置仍须同时报告检索增益与时延成本，单次本机耗时不作性能基准。

两个模式共实际核验 300 个候选 Chunk 引用，版本、原文、位置与访问权限检查均匹配。该结构有效性不证明每段引用语义足以回答问题，回答忠实度仍需独立标注。生成模型调用为 0；本地 Embedding / Reranker 没有供应商 API Token 账单，消费为 N/A，不纳入在线模型账本。

## 冻结数据集真实模型评估

[原始在线 JSON](live/results.json)、[逐用例 CSV](live/cases.csv)和 [执行器报告](live/report.md)取自唯一一轮在线运行，2026-10-14T19:05:03Z—19:05:51Z。18 个手选问题在运行前冻结；3 个 development、2 个 regression、13 个 validation 的标签是开发用分组，不等于独立第三方盲测。未自动修改问题、Prompt 或判分后重复调用模型刷成绩。

| 分组 | 总数 | 全部自动检查通过 | 失败 | 待人工复核 | 未执行或未观测 |
| --- | ---: | ---: | ---: | ---: | ---: |
| development | 3 | 1 | 0 | 1 | 1 |
| regression | 2 | 0 | 1 | 1 | 0 |
| validation | 13 | 4 | 2 | 7 | 0 |
| 合计 | 18 | 5 | 3 | 9 | 1 |

完整判分共 219 个 pass、8 个 fail 和 15 个 manual_review 检查；检查数不当作独立用例数。17 个真实 Task 的状态为 13 completed、3 failed、1 waiting_approval；它们是流程状态，不能推导 13 项任务完成或语义正确。预期权限拒绝可以通过用例，缺失语义或原始参数证据的 completed 任务仍保留 manual_review。

[离线诊断](live-analysis.json)保留三个失败和一个未执行项：

- `regression_complex_refund_preview` 只完成查询与政策获取，缺少退款预览、`create_refund_request` 和人工确认；没有将较清晰问题的成功反推为复杂意图问题已经修复。
- `validation_coupon_refund` 任务以 `invalid_model_plan` 失败，未满足连续原文引用契约；其来源结构有效不抵消回答失败。
- `validation_refund_rate` 保存的 SQL 返回 `ROUND(ratio, 2)=0.02`，冻结参照返回 `ROUND(100 * ratio, 2)=2.33`。原始判分保留 fail；问题文字使用“比例保留两位”，比例与百分比的单位及舍入口径存在歧义，不能仅凭本次参照差异断言数据库事实或模型 SQL 运算错误。
- `dev_own_logistics` 在独立物流参照 GET 返回 404 后停止，所选归属正确的订单没有物流记录。`message_attempted=false`，没有 Session / Task DTO，可观测模型调用为 0；这是评估采集先决条件问题，保留 18 项分母里的未执行结果，不把不存在的 DTO 伪装成恢复后的任务。

四个正常自然语言分析问题均获取独立基表参照：其中 2 项满足冻结检查、1 项出现上述退款率口径差异、1 项周比较保留 manual_review。周比较真实返回 `latest=13`、`previous=30` 两行，独立基表参照为同值的单行两列；保守列映射和 AST 检查尚不支持该等价形状，离线观察不改原分。另一个基础表访问请求被归为 unsupported，拒绝文字的语义仍待复核，不能将它计入 SQL 数值正确率。

9 个 manual_review 用例保留完整未复核检查。引用语义尚无独立人工标注；公开 Task 投影也不能证明模型原始 JSON 的所有字段都满足工具参数 schema，只有可观察字段与已执行工具能够自动检查。这些未知项没有被计作通过或从分母删除。

运行前后 80 个源码 / 依赖 / 数据集哈希完全一致，业务基表数据指纹一致；评估会写模拟会话、任务、审批预览和审计，没有 resume / 确认退款或修改订单状态。单一退款问题达到 waiting_approval，不自动提交。

供应商账本按唯一 Task ID 保留最新累计快照：**17 个 Task、36 次模型调用，prompt 35626 + completion 3814 = 39440 Token**，未知调用数为 0，`usage_complete=true`。包含失败用例成本，不混入受控 fixture 的 222 Token、历史 Phase 6 / 7 的消费或本地 Embedding / Reranker 的 N/A。本轮首项在模型请求之前失败，未出现已发送但没有可观测 Task 的消息，因此该项不导致已调用任务的账本缺失。逐请求实际耗时已保存在 CSV / JSON，不作为性能基准。

## 本地 Observability 和可选远程 Tracing

[trace 核验](observability/verification.json)与 [元数据 JSONL](observability/snapshots.jsonl)保存在线评估 17 个唯一 Task 的最后快照，全部匹配 Task DTO 的状态、错误代码、请求 ID 的 SHA256 及累计消费。字段使用严格白名单；没有复制问题、回答、工具参数、SQL、文档正文、用户身份、认证头、签名秘密或原始 API 日志。step 元数据保留节点、工具名、尝试、耗时、错误与 span 关联，不记录完整模型输入输出。

上述 17 条快照的累计账本仍是 **39440 Token / 36 次调用**，只复核同一轮消费，不再次累加。Task DTO 与本地日志对应也不等同于网络报文捕获、模型准确率或外部服务确认。

LangSmith 作为可选的元数据导出适配器，默认关闭；本阶段未配置远端凭据，因此没有真实远端连接验收。SDK 路径的受控检查与本地真实 snapshot 分开呈现，不声称云端 trace 已上传、可访问或可视化成功。

## 采集修复、最终本机验证及限制

物流采样修复会在未来运行中先核验订单有可观测物流，再绑定物流用例；本阶段只增加 fixture 检查，没有再次付费重跑。数据集、判分规则和原始 18 项结果保持原样。[在线到最终源码比对](source-postrun-comparison.json)记录在线冻结的 80 文件前后完全一致，而后续最终源码只有 `live.py` 改变；因此 `source_stable=true` 的范围是首次实际在线运行，不是声称整个阶段的源码从未变化。

[本机 HTTP 验证](demo-ready.json)实际取得后端 0.8.0 / Phase 8、六项 readiness 成功、前端 5173 的 HTTP 200，以及 Vite 同源健康代理同版本、六项成功。健康和元数据请求没有调用模型。这是本机开发服务检查，不是一轮新浏览器业务测试、Linux 镜像、全栈 Docker Compose、GitHub Actions 或生产部署验证。

[公开文件检查](release-checks.json)对 9 个当前可取得的真实配置凭据逐一精确匹配，没有命中 Git 可见文件、生产前端文件以及所检查的 Phase 8 数据、trace 与日志；未复制私密签名文件或原始认证日志。该检查不构成通用泄漏检测保证。Markdown 本地文件链接存在，未验证锚点或远程 URL；`git diff --check` 通过。主验收在最终公开文档冻结后刷新该 JSON 的文件、链接数量，具体范围以该文件为准。

[证据来源清单](artifact-sources.json)记录原始本地文件与公开文件 SHA256：18 个文件逐字节一致；`python-summary.json` 仅移除原执行脚本写出的 3 字节 UTF-8 BOM，并验证剩余字节完全一致，两份哈希分别保留。公开 JSON 使用 UTF-8 无 BOM，CSV 保留执行器输出编码；`.gitattributes` 对本目录禁用文本换行转换，使 Git 保存相同字节，克隆后仍可核对公开文件哈希。失败、引用片段、数值、未知状态和消费没有重写。没有修改历史 Phase 3 / 6 / 7 指标。

复杂退款规划、知识回答计划失败、引用语义独立复核、完整模型参数采集和 SQL 口径 / 等价形状复核仍有明确未解决项。Phase 9 将完成全栈 Docker Compose、GitHub Actions 和部署文档的实际验证；公开部署及付费云资源仍不在自动执行范围内。
