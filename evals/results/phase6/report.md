# Phase 6 实际验收报告

版本 0.6.0，在同一个 LangGraph 中增加运营分析路线。模型以 plan_analytics_sql 规划查询，业务层先检查 analyst/admin 权限，再用独立只读账号访问两个授权聚合视图。报告、精确金额、SQL、指标口径与 line/bar 图表规范只来自实际查询结果。

## 最终工程检查

最终 scripts/check.ps1：**883 passed，0 failures / errors / skipped**，JUnit 697.865 秒，真实 PostgreSQL 与 RAG 均为必选。Ruff lint、153 文件格式检查和 pip check 通过。[完整摘要](verification.json)记录时间与检查边界；[源码快照](source-freeze.json)的 143 个选定实现、测试、脚本、依赖和数据集文件在完整检查前后哈希一致。该快照不声称包括所有文档或部署配置。

pytest CLI 耗时 697.89 秒；保留 85 条 Chroma legacy embedding function config 的 DeprecationWarning，没有隐藏或声称警告已修复。

初次完整检查为 878/879，通过耗时 667.484 秒，唯一失败是旧认证测试在 collection 时生成的未来十分钟 iat 到执行时已过期。仅将测试改为执行时计算时间，没有修改认证生产规则。审查另修复合法输入经 SQL 规范化后变长导致的报告误拒，以及宽数值图表遗漏第一指标；新增四项回归。相关 122 项预检属于最终完整套件子集，不另累加总数。

## 真实 SQL 对照

[SQL JSON](sql-results.json)与[SQL 报告](sql-report.md)保留人工 SQL 24/24：16 个查询与原始业务事实参照一致，8 个输入按预期拒绝。固定业务日期 2026-10-12 / Asia/Shanghai；500 订单、1016 明细、19 退款，前后业务指纹稳定。SQL 评估不调用聊天模型，供应商 Token 为 null / N/A，不能作为自然语言转 SQL 准确率。

reader 的 ACL、默认及事务只读、危险权限和角色、单授权视图 AST、非法 SQL、查询超时、截断哨兵、结果体预算、取消恢复、身份与故障条件均有实际数据库或相应单元检查。系统元数据和既有 PUBLIC 基础权限保留，不声称对整个 PostgreSQL cluster 的其他数据库绝对禁止连接。

## HTTP 与在线模型

[当前 HTTP 快照](http.json)记录重启后的实际 Uvicorn：0.6.0 / phase=6、六项就绪、登录模拟 analyst、最近七天支付销售额 991510 分。宽表两个金额列都保留为真实 series；客户查询运营数据返回 403，原始业务表 SQL 返回 422。UTF-8 中文已核对。上述请求没有模型调用，零消费只适用于该快照。

[在线 JSON](live-results.json)、[在线报告](live-report.md)与[CSV](live-cases.csv)保留四个手选、带输出 schema 合同的真实 Agent 烟测：4/4，8 次模型调用尝试，prompt 7427、completion 1019、total 8446 Token，未知消费 0。问题覆盖七天销售额、月退款率、两周订单对比与商品销售排行；逐列逐行与 canonical 只读 API 参照比较。请求不共享数据库快照；操作者提供的模型标签不是供应商身份验证。

在线烟测执行于 2026-10-13T20:09:51.113992Z–20:09:59.665929Z，临时 retries=0，随后恢复默认有限重试并重启。此记录早于最终两项边界修复；最终代码离线复核其四个报告与引用查询仍一致，新增模型调用 0，结果写入完整摘要。没有将离线复核冒充新一轮在线调用。这些是开发烟测，不是盲评、统计模型质量、压力或性能基准。历史 Phase 3–5 记录保留。

## 限制与下一阶段

目前只支持一个授权视图上的受限 SELECT；不支持 JOIN、CTE、子查询、UNION 或任意函数。金额原始值以人民币分计，不通过 float。退款率按原支付日期订单群计算截至查询时的模拟完成退款比例，没有历史 as-of 快照或真实退款服务。SQL 执行安全与指标说明不能自动证明自然语言语义正确。

Vue 页面与图表渲染、截图、Linux 应用安装、应用镜像、全栈 Compose、GitHub Actions、完整独立语义评估和公开部署仍属于后续工作。本阶段只运行本机现有基础设施，密钥保存在 ignored D 盘文件。

Phase 7 将完成 Vue 3 / TypeScript / Element Plus 五页后台，接入现有真实 API、Markdown、工具步骤、知识来源、人工确认与运营图表；验收前端类型/构建及实际浏览器业务流程，记录截图与未完成项，并提交本地 Git Commit。
