# Phase 6：运营分析 Agent 与只读 SQL

当前包与 HTTP 版本为 0.6.0 / phase=6。本阶段在原 LangGraph 图中增加分析路线，模型规划 SQL，后端通过专用 reader 与授权聚合视图执行，报告和图表数据只来自实际结果。最终完整检查 883 项全部通过，固定 SQL 对照 24/24、实际 HTTP 与四个 schema 输出合同在线烟测 4/4 分别归档；分组子集不重复累计为新的总数。

## 已实现的功能

- `0005_analytics` 创建 ea_analytics schema、版本 marker 和 daily_orders / daily_product_sales，迁移回退只移除本阶段视图/schema，不删除业务数据或 cluster 角色。
- 按上海支付日期统计 paid / shipped / delivered 且有 paid_at 的订单，金额以人民币分精确聚合。毛实付含运费、不扣退款；商品实付不含运费。
- 模拟完成退款先按订单汇总，再纳入原支付日期 cohort。完成退款率为当前 completed_simulated 订单数 / 已支付订单数，申请未完成不计；零分母保留 NULL。
- 商品名称/类目取自交易明细快照，同一商品历史名称不同时保留不同分组，不把当前 products 名称改写进历史。
- 独立开发/测试 reader 账号、密码文件、连接池与安全就绪检查；仅显式授分析 schema USAGE 和两个视图 SELECT，业务账号不获得分析视图权限。
- SQLGlot 30.22.0 的 PostgreSQL AST 精确白名单、单条 SELECT / 单授权视图 / 合法列 / 有限函数、只读事务、超时及行数/列数/单元格/负载预算。
- analyst / admin 的 catalog 和 query HTTP 接口，与图共用 AnalyticsService；直接 SQL 无需聊天模型。
- 原 LangGraph intent_router → analytics_planner → analytics_execution → response_generation → validation，复用私有会话、官方 Checkpoint、Task、执行限额与 usage 计账。没有第二个 Agent。
- named plan_analytics_sql 返回严格 SQLPlan，实际查询与确定性报告存入 Task.result.analysis；报告保留规范 SQL、精确数据、指标口径与图表规范，不追加自由文本模型解释。
- MCP 保留三个业务只读工具，Client / Server 排除分析连接并关闭分析 runtime。

## 代码与启动

主要代码为 `analytics/catalog.py`、`analytics/sql.py`、`analytics/runtime.py`、`services/analytics.py`、`schemas/analytics.py`、`api/routes/analytics.py` 与已有 `agents/graph.py`。数据库仍为 15 张业务表，加两个分析视图，schema head 为 0005_analytics；不用新实体模拟真实统计。

在已配置 Python 3.13 项目环境和 Docker 的本地工作区：

```powershell
.\scripts\stop.ps1
.\scripts\bootstrap.ps1
.\scripts\init-infra.ps1
.\scripts\init-analytics.ps1
.\scripts\migrate.ps1
.\scripts\migrate.ps1 -TestDatabase
.\scripts\check.ps1
.\scripts\run.ps1
```

stop 只适用于已登记且身份匹配的本项目后台 API，交互运行用 Ctrl+C。已有 PostgreSQL 容器因新增 secret 挂载可能重建，数据目录保留，不删除数据卷。新的 reader 密码与 DSN 仅在 ignored D 盘 var 文件中，初始化不打印凭据或修改业务/owner 密码。

`EA_ANALYTICS_ENABLED` 模板默认 false，run.ps1 显式启用；需要对应受限 EA_ANALYTICS_DATABASE_URL、已迁移视图及安全权限。配置不满足时就绪失败，查询不回退业务/owner 账号。完整变量和初始化顺序见[部署](deployment.md)。

登录 demo_analyst / demo_admin 后可调用 `GET /api/v1/analytics/catalog`、`POST /api/v1/analytics/query`。自然语言使用自己的 Agent 会话发送“统计最近七天的销售额”“本月退款率是多少”“对比最近两周的订单量”“哪些商品销售额最高”。Agent 路径需要已配置真实聊天模型，直接 SQL 不需要 Key；参数与字段见[API](api.md)。

## 验收状态与产物

专用账号初始化已在现有本地 cluster 执行，原业务数据保留。数据库代码的 Ruff 检查、格式与两个 PowerShell 初始化脚本语法检查已通过；这些预检不替代完整检查和实际业务查询。

| 验收类别 | 当前记录 |
| --- | --- |
| 全量工程检查 | 883 通过、0 失败/错误/跳过；JUnit 697.865 秒，真实 PostgreSQL / RAG 必选 |
| 真实数据库分组预检 | 16 项视图/reader、12 项 API、28 项 runtime、7 项持久图测试通过；不合计成全量成绩 |
| 固定 SQL 与独立事实基准 | 24/24：16 查询匹配、8 按预期拒绝；人工 SQL，不是 NL SQL 分数 |
| 实际 Uvicorn HTTP | 最终 0.6.0 / phase=6、六项就绪、991510 分、宽表两 series、客户 403 / 原始表 422，model_calls=0 仅指此快照 |
| 真实模型 NL SQL | 四个手选输出合同检查 4/4，8 次模型调用，8446 已知 Token，无未知消费 |

已归档[完整检查摘要](../evals/results/phase6/verification.json)、[源码冻结记录](../evals/results/phase6/source-freeze.json)、[可读报告](../evals/results/phase6/report.md)、[SQL JSON](../evals/results/phase6/sql-results.json)、[SQL 报告](../evals/results/phase6/sql-report.md)、[HTTP 快照](../evals/results/phase6/http.json)、[在线 JSON](../evals/results/phase6/live-results.json)、[在线报告](../evals/results/phase6/live-report.md)和[在线 CSV](../evals/results/phase6/live-cases.csv)。原始 pytest JSON / JUnit 为 `var/test-results/phase6.json` / `phase6.xml`，历史 Phase 3、4、5 文件保持原样。

最终完整检查生成于 2026-10-14T08:20:33.3102553Z；Ruff lint、153 文件格式与 pip check 通过，CLI 697.89 秒。85 条 Chroma legacy embedding function config 弃用警告保留，没有隐藏或声称修复。143 个选定实现、脚本、测试、锁和数据集文件在完整检查前后哈希一致；该快照不声称覆盖所有文档与部署配置。

初次视图/API 分组检查有一个测试构造错误：测试订单号 46 字符超过既有 VARCHAR(40)，已仅将 fixture 缩至 30 字符并单项复验通过；生产约束未修改。AST、受控图、在线检查器与配置预检分别通过，它们与当前全量有重叠，不累计成新的总数。

首轮全量为 879 项、878 通过、1 失败、0 错误/跳过，JUnit 667.484 秒。失败来自旧 JWT 测试在收集时生成“未来签发时间”，长套件执行后该时间已不再位于未来；已仅把测试时间改为执行时计算，生产 JWT 校验保持原规则。同时修复图验证将规范化 SQL 再套原始输入长度导致边界误拒，以及图表把纯数值宽表首列当维度漏掉 series 的问题，新增四个回归测试。修复后 122 项聚焦检查及最终完整 883 项通过；聚焦子集不叠加总数，首轮失败保留原记录。

固定 SQL 评估以 human_authored_sql_fixtures 为来源，问题日期固定 2026-10-12，真实数据为 500 订单、1016 明细、19 退款，执行前后指纹 `7cd995f62e94ad4905f471ceb253356c8261f86e264fa6d02ccbddf0f905a7c1` 稳定。16 个成功查询与独立原始事实对照，8 个输入按预期拒绝；模型调用 0，供应商 Token 为 N/A。这证明这些 SQL 的结果与执行边界，不能冒称自然语言转 SQL 准确率。

在线检查通过本机真实 Agent / reader，对四个手选带输出 schema 合同的问题逐列逐行与 canonical 只读 API 比较，4/4 通过。参考与 Agent 请求不共享数据库快照，期间业务日期未漂移。供应商报告 prompt 7427、completion 1019、total 8446 Token，8 次模型调用尝试，消费完整、没有未知任务；操作者提供的 deepseek-flash 标签不能验证 DTO 未暴露的底层供应商身份。原始起止 2026-10-13T20:09:51.113992Z–20:09:59.665929Z，不是性能基准。

此次在线烟测临时使用合法配置 retries=0，之后已恢复默认 2 次有限重试并重启最终安全日志修复版本。健康不触发聊天模型，HTTP 快照的零调用不代替在线消费；历史 Phase 4 消费记录不回写。样例为开发烟测，不属于盲评、开放式问题质量或失败重试效果评估。

在线原始记录早于最终 SQL 长度与图表边界修复。最终代码对四个归档实际报告/参考结果作离线复核仍为 4/4，数据集与 evaluator hash 匹配，新增模型调用 0，完整摘要包含 archived_live_recheck；没有将离线复核称为新在线轮次或修改原消费。当前主服务已重启至最终实现，恢复默认 retries=2，并通过独立 HTTP、宽表结果、拒绝和 UTF-8 检查。

数据库测试以 enterprise_agent_test 为唯一重置目标，使用独立 reader 读取实际视图，并从原始订单/明细/退款在 Python 中计算参照。关闭默认只读后的 ACL 拒绝验证，与只读事务、AST、超时和结果限制分开核对。控制模型仅测试路线和故障；供应商消费只来自真实 usage，未知消费保持未知。详细边界见[Evaluation](evaluation.md)。

## 限制与未完成事项

- 首版只接受一个聚合视图上的受限 SELECT，不支持 JOIN、CTE、子查询、UNION、窗口函数、任意函数、动态数据源或 SELECT *（COUNT(*) 例外）。
- AST 与数据库权限证明执行安全边界，不能证明问题解析、指标、时间范围或 SQL 语义正确。metric_ids 的登记口径也不自动证明表达式语义匹配。
- 退款为模拟完成，当前状态可能变化；未提供历史时间点 as-of 快照、真实支付或财务对账。
- 保留 PostgreSQL 系统元数据和既有 PUBLIC 基础权限，仅拒绝两项目库的交叉连接；不声称 dedicated reader 对整个 cluster 所有其他库绝对不可连接。
- 返回 line / bar 图表规范与真实数据，暂无 Vue 图表页面、浏览器交互或截图。
- 结果截断不能用于完整总量；查询行、指标定义和时间范围均可核对，报告不推断业务变化原因。
- 当前两份锁在 Windows 解析，Linux 应用安装、镜像、全栈 Compose、GitHub Actions 与公网部署仍未验收。
- 在线开发场景不是独立盲评或负载基准；本阶段完成检查后仍需扩大模型语义评估。

## 下一阶段与验收标准

Phase 7 实现 Vue 3 / TypeScript / Element Plus 五页后台：Agent 对话、订单售后、知识库、运营分析和任务历史。接入真实 API、Markdown、工具步骤、来源、人工确认与分析图表；使用现有角色权限和实际错误，不在浏览器保存后端 Key。阶段验收包括前端类型/构建检查、实际浏览器业务流程、真实 API 结果与截图，记录未实现项并提交清晰本地 Git Commit，不自动公网部署。
