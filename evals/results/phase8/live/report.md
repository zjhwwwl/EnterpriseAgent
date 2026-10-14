# Phase 8 真实模型评估

开发助手编写的小样本，首次运行前冻结；不是第三方盲评。语义与缺失原始参数证据保留 manual_review。

| Split | Total | Pass | Fail | Manual review | Unobserved |
|---|---:|---:|---:|---:|---:|
| development | 3 | 1 | 0 | 1 | 1 |
| regression | 2 | 0 | 1 | 1 | 0 |
| validation | 13 | 4 | 2 | 7 | 0 |

实际观测 17 个唯一任务；已知供应商 Token 39440，usage_complete=True。

| Case | Outcome | Failed / unreviewed checks |
|---|---|---|
| dev_own_logistics | unobserved | evaluation_local_error_RuntimeError |
| dev_return_window | manual_review | quote_answers_question |
| dev_sales_seven_days | pass | None |
| regression_foreign_order | manual_review | tool_model_argument_schema |
| regression_complex_refund_preview | fail | task_status, tool_required_create_refund_request, tool_model_argument_schema, tool_arguments_create_refund_request, approval_schema |
| validation_order_details | manual_review | tool_model_argument_schema |
| validation_refund_eligibility | manual_review | tool_model_argument_schema |
| validation_simple_refund_preview | manual_review | tool_model_argument_schema |
| validation_membership_points_boundary | manual_review | quote_answers_question |
| validation_coupon_refund | fail | task_status, task_error_code, continuous_original_quotes, quote_answers_question |
| validation_delivery_reference | manual_review | quote_answers_question |
| validation_missing_policy | pass | None |
| validation_refund_rate | fail | sql_rows, sql_ast_metric_expression |
| validation_order_week_comparison | manual_review | sql_rows, sql_ast_date_window, sql_ast_metric_expression, sql_ast_result_shape |
| validation_product_top_five | pass | None |
| validation_customer_analytics_denied | pass | None |
| validation_base_table_denied | manual_review | base_table_refusal_meaning |
| validation_out_of_domain | pass | None |

响应耗时为当前机器的单次实际观察，不是性能基准。API会写会话、任务、审批预览和审计；评估不会确认退款，业务基表前后指纹另行核对。
