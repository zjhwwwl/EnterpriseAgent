/** Public DTOs from schemas/agent.py, business.py, and knowledge.py. */
import type { AnalyticsReport } from "./types";
export type { Page } from "./types";

export type TaskStatus =
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "rejected"
  | "expired"
  | "cancelled";
export type RefundReason = "change_mind" | "quality_issue" | "not_received";

export interface AgentSession {
  id: string;
  current_task_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  document_id: string;
  document_title: string;
  document_version: number;
  index_generation: number;
  chunk_id: string;
  chunk_index: number;
  text: string;
  source_location: Record<string, unknown>;
  citation_url: string;
  vector_similarity: number | null;
  bm25_score: number | null;
  rerank_score: number | null;
}

export interface PolicySnapshot {
  id: number;
  policy_code: string;
  version: number;
  document_id: string;
  document_title: string;
  document_version: number;
  valid_from: string;
  valid_until: string | null;
  refund_window_days: number;
  refund_shipping: boolean;
  allow_before_delivery: boolean;
  excluded_categories: string[];
}

export interface RefundApproval {
  approval_id: string;
  order_no: string;
  status: string;
  reason: RefundReason;
  notes: string;
  amount_cents: number;
  expires_at: string;
  policy: PolicySnapshot;
  requires_confirmation: boolean;
}

export interface RefundRecord {
  id: string;
  order_no: string;
  applicant_id: number;
  amount_cents: number;
  reason: string;
  notes: string;
  status: string;
  policy_version_id: number;
  policy_snapshot: Record<string, unknown>;
  created_at: string;
  reviewer_id: number | null;
  reviewed_at: string | null;
  review_note: string | null;
  completed_at: string | null;
}

export interface RefundConfirmation {
  approval_id: string;
  status: string;
  refund: RefundRecord | null;
  replayed: boolean;
}

export interface AgentStep {
  node?: string;
  tool?: string;
  attempt?: number;
  elapsed_ms?: number;
  error_code?: string | null;
  [key: string]: unknown;
}

export interface TokenUsage {
  model_calls?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  unknown_usage_calls?: number;
  usage_complete?: boolean;
  interrupted_usage_unknown?: boolean;
  [key: string]: unknown;
}

export type AgentAnalysis = AnalyticsReport;

export interface AgentTask {
  id: string;
  session_id: string;
  task_type: string;
  status: TaskStatus;
  message: string;
  result: {
    answer?: string | null;
    approval?: RefundApproval | null;
    refund_result?: RefundConfirmation | null;
    citations?: Citation[] | null;
    facts?: Array<{
      name?: string;
      arguments?: unknown;
      result?: unknown;
    }> | null;
    warning_codes?: string[] | null;
    analysis?: AgentAnalysis | null;
    [key: string]: unknown;
  };
  steps: AgentStep[];
  token_usage: TokenUsage;
  error_code: string | null;
  elapsed_ms: number | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

const statusLabels: Record<TaskStatus, string> = {
  running: "执行中",
  waiting_approval: "待人工确认",
  completed: "已完成",
  failed: "执行失败",
  rejected: "已拒绝",
  expired: "已过期",
  cancelled: "已取消",
};
export function statusLabel(status: TaskStatus): string {
  return statusLabels[status] ?? status;
}
export function statusType(
  status: TaskStatus,
): "success" | "info" | "warning" | "danger" {
  return status === "completed"
    ? "success"
    : status === "failed"
      ? "danger"
      : status === "waiting_approval" || status === "running"
        ? "warning"
        : "info";
}
export function taskTypeLabel(taskType: string): string {
  return (
    (
      {
        knowledge: "知识问答",
        business: "订单与售后",
        analytics: "运营分析",
        unknown: "待识别",
      } as Record<string, string>
    )[taskType] ?? taskType
  );
}
export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(date);
}
export function reasonLabel(reason: string): string {
  return (
    (
      {
        change_mind: "七天无理由",
        quality_issue: "商品质量问题",
        not_received: "未收到商品",
      } as Record<string, string>
    )[reason] ?? reason
  );
}
export function canRecover(task: AgentTask): boolean {
  return (
    task.status === "running" ||
    ["agent_checkpoint_error", "agent_interrupted", "agent_timeout"].includes(
      task.error_code ?? "",
    )
  );
}
export function blocksNextTurn(task: AgentTask | null): boolean {
  return !!task && (task.status === "waiting_approval" || canRecover(task));
}
