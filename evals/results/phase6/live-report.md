# 运营分析在线冒烟记录

四个手选、带输出合同的开发检查，不属于盲评，不可泛化为模型质量。
通过 canonical 只读 API 查询逐列逐行对照；不同请求不共享数据库快照。
通过 4/4；消费完整：True。
已知 Token：{'prompt_tokens': 7427, 'completion_tokens': 1019, 'total_tokens': 8446}；已知模型调用尝试：8。
未知消费保留为未知；跨业务日期时判定失败，需新日期重新执行。

| 场景 | 已执行 | 通过 | 依据 |
|---|---|---|---|
| paid_sales_last_seven_days | True | True | ordered_columns_and_rows_match_real_reader_reference |
| refund_rate_month_cohort | True | True | ordered_columns_and_rows_match_real_reader_reference |
| compare_complete_seven_day_periods | True | True | ordered_columns_and_rows_match_real_reader_reference |
| product_sales_top_five | True | True | ordered_columns_and_rows_match_real_reader_reference |
