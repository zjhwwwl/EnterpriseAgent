export type RefundReason = "change_mind" | "quality_issue" | "not_received";
export type OrderStatus =
  "unpaid" | "paid" | "shipped" | "delivered" | "cancelled";
export type RefundStatus =
  | "pending_review"
  | "approved"
  | "rejected"
  | "cancelled"
  | "completed_simulated";
export type ApprovalStatus = "pending" | "consumed" | "rejected" | "expired";

export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface Order {
  id: number;
  order_no: string;
  user_id: number;
  status: OrderStatus;
  goods_paid_cents: number;
  shipping_paid_cents: number;
  paid_total_cents: number;
  created_at: string;
  paid_at: string | null;
  delivered_at: string | null;
}

export interface OrderItem {
  id: number;
  product_id: number;
  product_name: string;
  category: string;
  quantity: number;
  unit_price_cents: number;
  paid_amount_cents: number;
}

export interface Logistics {
  order_no: string;
  tracking_no: string;
  status: string;
  events: Record<string, unknown>[];
}

export interface RefundPolicy {
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

export interface RefundEligibility {
  order_no: string;
  eligible: boolean;
  code: string;
  message: string;
  amount_cents: number;
  policy: RefundPolicy | null;
}

export interface RefundApproval {
  approval_id: string;
  order_no: string;
  status: ApprovalStatus;
  reason: RefundReason;
  notes: string;
  amount_cents: number;
  expires_at: string;
  policy: RefundPolicy;
  requires_confirmation: boolean;
}

export interface Refund {
  id: string;
  order_no: string;
  applicant_id: number;
  amount_cents: number;
  reason: string;
  notes: string;
  status: RefundStatus;
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
  status: ApprovalStatus;
  refund: Refund | null;
  replayed: boolean;
}

/** Keep an uncertain confirmation across SPA navigation, never in browser storage. */
export const pendingRefundConfirmations = new Map<
  number,
  {
    approval: RefundApproval;
    idempotencyKey: string;
    decision: boolean | null;
  }
>();

export interface KnowledgeDocument {
  id: string;
  title: string;
  file_type: string;
  checksum: string;
  version: number;
  visibility: "public" | "internal";
  status: string;
  index_generation: number;
  index_attempt_generation: number;
  chunk_count: number;
  index_error_code: string | null;
  indexed_at: string | null;
  created_at: string;
}

export type SearchMode = "vector" | "hybrid" | "hybrid_rerank";
export type { Citation } from "./agent-types";

export interface KnowledgeSearch {
  status: "evidence_found" | "insufficient_evidence";
  message: string;
  answer_mode: "evidence_only";
  mode: string;
  citations: import("./agent-types").Citation[];
  elapsed_ms: number;
  stage_ms: Record<string, number>;
  model_fingerprint: string;
  llm_calls: number;
  llm_input_tokens: number;
  llm_output_tokens: number;
}
