# Analytics SQL Evaluation

本报告验证人工编写 SQL 的视图结果与独立基表聚合一致性，以及受限 SQL 拒绝边界。
自然语言问题只用于说明样例；本轮未调用模型，不代表自然语言转 SQL 正确率或线上安全保证。

- 固定问题日期：2026-10-12，时区 Asia/Shanghai。
- 实际执行时间：2026-10-14T21:01:34.113241+00:00。
- 测试数据库：enterprise_agent_test；表行数：{'orders': 500, 'order_items': 1016, 'refund_requests': 19}。
- 数据指纹：`7cd995f62e94ad4905f471ceb253356c8261f86e264fa6d02ccbddf0f905a7c1`。
- 数据在执行前后保持一致：True。
- 正确查询对照通过：16/16。
- 安全拒绝样例通过：8/8。
- 总计：24/24；失败：0。
- 模型调用：0；Provider Token 消耗：N/A。

耗时来自本次实际执行，包含本地缓存、连接池和当前机器状态影响，不是性能基准。

| Case | Kind | Passed | Actual elapsed ms |
| --- | --- | --- | --- |
| sales-last-seven-days | reference_comparison | True | 40.431 |
| sales-month-to-date | reference_comparison | True | 29.78 |
| sales-money-breakdown | reference_comparison | True | 30.278 |
| month-paid-cohort-refund-rate | reference_comparison | True | 44.239 |
| zero-denominator-refund-rate | reference_comparison | True | 33.538 |
| two-week-paid-order-comparison | reference_comparison | True | 32.329 |
| top-five-product-revenue | reference_comparison | True | 42.343 |
| daily-paid-sales-chart-data | reference_comparison | True | 31.89 |
| food-category-snapshot-revenue | reference_comparison | True | 35.119 |
| all-paid-money-exact-cents | reference_comparison | True | 28.61 |
| empty-sales-zero-by-explicit-coalesce | reference_comparison | True | 29.922 |
| product-units-sold | reference_comparison | True | 35.856 |
| daily-paid-order-count | reference_comparison | True | 32.309 |
| completed-refund-paid-cohort-amount | reference_comparison | True | 41.526 |
| snapshot-category-sales | reference_comparison | True | 50.627 |
| empty-daily-chart | reference_comparison | True | 32.655 |
| reject-customer-table | rejection | True | 0.171 |
| reject-write | rejection | True | 0.155 |
| reject-side-effect-function | rejection | True | 0.165 |
| reject-second-statement | rejection | True | 0.147 |
| reject-join | rejection | True | 0.15 |
| reject-wildcard | rejection | True | 0.086 |
| reject-role-change | rejection | True | 0.058 |
| reject-parameter-placeholder | rejection | True | 0.124 |

详见 results.json 的实际结果、独立参考结果、规范化 SQL 与失败代码。
