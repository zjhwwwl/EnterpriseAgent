# Analytics SQL Evaluation

本报告验证人工编写 SQL 的视图结果与独立基表聚合一致性，以及受限 SQL 拒绝边界。
自然语言问题只用于说明样例；本轮未调用模型，不代表自然语言转 SQL 正确率或线上安全保证。

- 固定问题日期：2026-10-12，时区 Asia/Shanghai。
- 实际执行时间：2026-10-13T20:06:16.140442+00:00。
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
| sales-last-seven-days | reference_comparison | True | 68.634 |
| sales-month-to-date | reference_comparison | True | 52.664 |
| sales-money-breakdown | reference_comparison | True | 62.242 |
| month-paid-cohort-refund-rate | reference_comparison | True | 71.157 |
| zero-denominator-refund-rate | reference_comparison | True | 53.958 |
| two-week-paid-order-comparison | reference_comparison | True | 63.062 |
| top-five-product-revenue | reference_comparison | True | 66.775 |
| daily-paid-sales-chart-data | reference_comparison | True | 40.225 |
| food-category-snapshot-revenue | reference_comparison | True | 61.399 |
| all-paid-money-exact-cents | reference_comparison | True | 38.527 |
| empty-sales-zero-by-explicit-coalesce | reference_comparison | True | 51.336 |
| product-units-sold | reference_comparison | True | 64.045 |
| daily-paid-order-count | reference_comparison | True | 60.796 |
| completed-refund-paid-cohort-amount | reference_comparison | True | 63.547 |
| snapshot-category-sales | reference_comparison | True | 61.408 |
| empty-daily-chart | reference_comparison | True | 58.775 |
| reject-customer-table | rejection | True | 0.13 |
| reject-write | rejection | True | 0.12 |
| reject-side-effect-function | rejection | True | 0.131 |
| reject-second-statement | rejection | True | 0.131 |
| reject-join | rejection | True | 0.143 |
| reject-wildcard | rejection | True | 0.087 |
| reject-role-change | rejection | True | 0.055 |
| reject-parameter-placeholder | rejection | True | 0.128 |

详见 results.json 的实际结果、独立参考结果、规范化 SQL 与失败代码。
