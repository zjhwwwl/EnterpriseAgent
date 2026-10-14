# Controlled workflow boundary evaluation

Evaluation kind: `controlled_fault_injection`. Real LangGraph and parameter validators
run with scripted model and Service fixtures. Provider usage: **not applicable**.

Cases: 16; passed: 16; failed: 0; elapsed: 821.565 ms.

These outcomes measure declared workflow boundary expectations, not model task completion
quality, database authorization, production latency or billed tokens.

Fixture bookkeeping: `{"model_calls": 34, "prompt_tokens": 139, "completion_tokens": 83, "total_tokens": 222, "unknown_usage_calls": 5}`.

| Case | Boundary passed | State | Error | Local elapsed ms | Failed checks |
| --- | --- | --- | --- | ---: | --- |
| valid_read_only_tool | True | completed | — | 16.488 | — |
| extra_confirmation_field | True | failed | invalid_tool_arguments | 11.064 | — |
| missing_order_number | True | failed | invalid_tool_arguments | 10.356 | — |
| wrong_order_number_type | True | failed | invalid_tool_arguments | 11.388 | — |
| forged_foreign_actor_id | True | failed | invalid_tool_arguments | 9.964 | — |
| retryable_recovers_with_usage | True | completed | — | 318.836 | — |
| retry_budget_exhausted | True | failed | agent_model_unavailable | 319.941 | — |
| invalid_response_not_retried | True | failed | agent_model_invalid_response | 7.961 | — |
| local_timeout_usage_unknown | True | failed | agent_model_timeout | 28.371 | — |
| repeated_planning_step_limit | True | failed | agent_step_limit | 15.848 | — |
| oversized_tool_batch | True | failed | agent_tool_limit | 9.907 | — |
| unsupported_request_bounded | True | completed | — | 8.785 | — |
| refund_preview_interrupts | True | running | — | 11.585 | — |
| human_rejection_no_refund_write | True | rejected | — | 13.911 | — |
| human_confirmation_replay_once | True | completed | — | 13.51 | — |
| mismatched_approval_rejected | True | failed | approval_changed | 12.546 | — |

- Not a DeepSeek or other provider quality benchmark.
- No database executes; ownership and business rules need separate integration tests.
- Fixture token totals are scripted bookkeeping, not billing or tokenizer measurement.
- Timeout is a local controlled model fixture; provider transport is not exercised.
- Replay uses InMemorySaver; PostgreSQL persistence needs separate integration tests.
