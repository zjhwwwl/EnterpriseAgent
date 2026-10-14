<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, ApiError, downloadFile, errorMessage } from "../api/client";
import { pendingRefundConfirmations } from "../api/business-types";
import type {
  KnowledgeDocument,
  Logistics,
  Order,
  OrderItem,
  Page,
  Refund,
  RefundApproval,
  RefundConfirmation,
  RefundEligibility,
  RefundReason,
} from "../api/business-types";
import { auth } from "../stores/auth";
import { dateTime as date, money } from "../utils/format";

type VisibleError = {
  message: string;
  requestId: string | null;
  status: number | null;
};
type StaffAction = "approve" | "reject" | "complete-simulated";
const errorInfo = (error: unknown): VisibleError => ({
  message: error instanceof ApiError ? error.message : errorMessage(error),
  requestId: error instanceof ApiError ? error.requestId : null,
  status: error instanceof ApiError ? error.status : null,
});
const canRead = computed(() =>
  ["customer", "support", "admin"].includes(auth.user?.role ?? ""),
);
const isCustomer = computed(() => auth.user?.role === "customer");
const isStaff = computed(() =>
  ["support", "admin"].includes(auth.user?.role ?? ""),
);
const identity = computed(
  () => `${auth.user?.id ?? ""}:${auth.user?.role ?? ""}`,
);
const orders = ref<Page<Order> | null>(null);
const refunds = ref<Page<Refund> | null>(null);
const orderPage = ref(1);
const refundPage = ref(1);
const pageSize = 20;
const orderLoading = ref(false);
const refundLoading = ref(false);
const orderError = ref<VisibleError | null>(null);
const refundError = ref<VisibleError | null>(null);
const orderNumber = ref("");
const drawerOpen = ref(false);
const detailLoading = ref(false);
const detail = ref<Order | null>(null);
const items = ref<OrderItem[]>([]);
const logistics = ref<Logistics | null>(null);
const detailError = ref<VisibleError | null>(null);
const itemsError = ref<VisibleError | null>(null);
const logisticsError = ref<VisibleError | null>(null);
const reason = ref<RefundReason>("change_mind");
const notes = ref("");
const eligibility = ref<RefundEligibility | null>(null);
const eligibilityLoading = ref(false);
const eligibilityError = ref<VisibleError | null>(null);
const previewLoading = ref(false);
const approval = ref<RefundApproval | null>(null);
const approvalOpen = ref(false);
const approvalKey = ref("");
const submittedDecision = ref<boolean | null>(null);
const confirmationBusy = ref(false);
const confirmationError = ref<VisibleError | null>(null);
const confirmationAcknowledged = ref(false);
const confirmationDenied = computed(() => {
  const status = confirmationError.value?.status;
  return (
    status !== null &&
    status !== undefined &&
    status >= 400 &&
    status < 500 &&
    status !== 408 &&
    status !== 429
  );
});
const staffTarget = ref<Refund | null>(null);
const staffAction = ref<StaffAction>("approve");
const staffNote = ref("");
const staffAcknowledged = ref(false);
const staffBusy = ref(false);
const staffError = ref<VisibleError | null>(null);
const policyDownloadError = ref<VisibleError | null>(null);
let detailSequence = 0;
let eligibilitySequence = 0;
let orderSequence = 0;
let refundSequence = 0;
const readers = new Set<AbortController>();
function savePendingApproval() {
  if (!isCustomer.value || !auth.user || !approval.value || !approvalKey.value)
    return;
  pendingRefundConfirmations.set(auth.user.id, {
    approval: approval.value,
    idempotencyKey: approvalKey.value,
    decision: submittedDecision.value,
  });
}
function read<T>(path: string) {
  const controller = new AbortController();
  readers.add(controller);
  return api<T>(path, { signal: controller.signal }).finally(() =>
    readers.delete(controller),
  );
}
function stopReads() {
  readers.forEach((controller) => controller.abort());
  readers.clear();
}
function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
const orderLabels: Record<string, string> = {
  unpaid: "待支付",
  paid: "已支付",
  shipped: "配送中",
  delivered: "已签收",
  cancelled: "已取消",
};
const refundLabels: Record<string, string> = {
  pending_review: "待人工审核",
  approved: "已批准 · 待模拟完成",
  rejected: "已驳回",
  cancelled: "已取消",
  completed_simulated: "模拟退款完成",
};
const reasonLabels: Record<string, string> = {
  change_mind: "七天无理由",
  quality_issue: "商品质量问题",
  not_received: "未收到商品",
};
const logisticsLabels: Record<string, string> = {
  pending_dispatch: "等待发货",
  in_transit: "运输中",
  delivered: "已签收",
};
function eventText(event: Record<string, unknown>) {
  if (typeof event.at === "string" && typeof event.note === "string") {
    return `${date(event.at)} · ${event.note}${typeof event.status === "string" ? ` · ${logisticsLabels[event.status] ?? event.status}` : ""}`;
  }
  return Object.entries(event)
    .map(
      ([key, value]) =>
        `${key}：${
          typeof value === "object" ? JSON.stringify(value) : String(value)
        }`,
    )
    .join(" · ");
}
async function loadOrders() {
  if (!canRead.value) return;
  const sequence = ++orderSequence;
  const actor = identity.value;
  orderLoading.value = true;
  orderError.value = null;
  try {
    const result = await read<Page<Order>>(
      `/api/v1/orders?offset=${(orderPage.value - 1) * pageSize}&limit=${pageSize}`,
    );
    if (sequence === orderSequence && actor === identity.value)
      orders.value = result;
  } catch (error) {
    if (
      sequence === orderSequence &&
      actor === identity.value &&
      !isAbort(error)
    )
      orderError.value = errorInfo(error);
  } finally {
    if (sequence === orderSequence) orderLoading.value = false;
  }
}
async function loadRefunds() {
  if (!canRead.value) return;
  const sequence = ++refundSequence;
  const actor = identity.value;
  refundLoading.value = true;
  refundError.value = null;
  try {
    const result = await read<Page<Refund>>(
      `/api/v1/refunds?offset=${(refundPage.value - 1) * pageSize}&limit=${pageSize}`,
    );
    if (sequence === refundSequence && actor === identity.value)
      refunds.value = result;
  } catch (error) {
    if (
      sequence === refundSequence &&
      actor === identity.value &&
      !isAbort(error)
    )
      refundError.value = errorInfo(error);
  } finally {
    if (sequence === refundSequence) refundLoading.value = false;
  }
}
async function openOrder(number: string) {
  if (!canRead.value) return;
  const normalized = number.trim();
  if (!/^[A-Za-z0-9_-]{1,40}$/.test(normalized)) {
    ElMessage.warning("请输入 1–40 位字母、数字、下划线或连字符组成的订单号。");
    return;
  }
  const sequence = ++detailSequence;
  const actor = identity.value;
  eligibilitySequence++;
  drawerOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  items.value = [];
  logistics.value = null;
  detailError.value =
    itemsError.value =
    logisticsError.value =
    eligibilityError.value =
      null;
  eligibility.value = null;
  eligibilityLoading.value = false;
  notes.value = "";
  reason.value = "change_mind";
  policyDownloadError.value = null;
  const path = `/api/v1/orders/${encodeURIComponent(normalized)}`;
  try {
    const order = await read<Order>(path);
    if (sequence !== detailSequence || actor !== identity.value) return;
    detail.value = order;
    const results = await Promise.allSettled([
      read<OrderItem[]>(`${path}/items`),
      read<Logistics>(`${path}/logistics`),
    ]);
    if (sequence !== detailSequence || actor !== identity.value) return;
    const [itemResult, logisticsResult] = results;
    if (itemResult.status === "fulfilled") items.value = itemResult.value;
    else if (!isAbort(itemResult.reason))
      itemsError.value = errorInfo(itemResult.reason);
    if (logisticsResult.status === "fulfilled")
      logistics.value = logisticsResult.value;
    else if (!isAbort(logisticsResult.reason))
      logisticsError.value = errorInfo(logisticsResult.reason);
  } catch (error) {
    if (
      sequence === detailSequence &&
      actor === identity.value &&
      !isAbort(error)
    )
      detailError.value = errorInfo(error);
  } finally {
    if (sequence === detailSequence) detailLoading.value = false;
  }
}
async function checkEligibility() {
  if (!detail.value || !canRead.value) return;
  const sequence = ++eligibilitySequence;
  const actor = identity.value;
  const number = detail.value.order_no;
  const currentReason = reason.value;
  eligibilityLoading.value = true;
  eligibilityError.value = null;
  eligibility.value = null;
  try {
    const result = await read<RefundEligibility>(
      `/api/v1/orders/${encodeURIComponent(number)}/refund-eligibility?reason=${currentReason}`,
    );
    if (
      sequence === eligibilitySequence &&
      actor === identity.value &&
      detail.value?.order_no === number
    )
      eligibility.value = result;
  } catch (error) {
    if (
      sequence === eligibilitySequence &&
      actor === identity.value &&
      !isAbort(error)
    )
      eligibilityError.value = errorInfo(error);
  } finally {
    if (sequence === eligibilitySequence) eligibilityLoading.value = false;
  }
}
async function prepareRefund() {
  if (
    !detail.value ||
    !isCustomer.value ||
    previewLoading.value ||
    approval.value
  )
    return;
  const actor = identity.value;
  previewLoading.value = true;
  eligibilityError.value = null;
  try {
    const result = await api<RefundApproval>(
      `/api/v1/orders/${encodeURIComponent(detail.value.order_no)}/refund-preview`,
      {
        method: "POST",
        body: { reason: reason.value, notes: notes.value.trim() },
      },
    );
    if (actor !== identity.value) return;
    approval.value = result;
    approvalKey.value = crypto.randomUUID();
    submittedDecision.value = null;
    confirmationError.value = null;
    confirmationAcknowledged.value = false;
    approvalOpen.value = true;
    savePendingApproval();
  } catch (error) {
    if (actor === identity.value) eligibilityError.value = errorInfo(error);
  } finally {
    previewLoading.value = false;
  }
}
async function confirmRefund(confirmed: boolean) {
  if (
    !approval.value ||
    !isCustomer.value ||
    confirmationBusy.value ||
    confirmationDenied.value
  )
    return;
  if (confirmed && !confirmationAcknowledged.value) return;
  if (submittedDecision.value !== null && submittedDecision.value !== confirmed)
    return;
  const actor = identity.value;
  submittedDecision.value = confirmed;
  savePendingApproval();
  confirmationBusy.value = true;
  confirmationError.value = null;
  try {
    const result = await api<RefundConfirmation>("/api/v1/refunds", {
      method: "POST",
      body: { approval_id: approval.value.approval_id, confirmed },
      headers: { "Idempotency-Key": approvalKey.value },
    });
    if (actor !== identity.value) return;
    if (auth.user) pendingRefundConfirmations.delete(auth.user.id);
    approval.value = null;
    approvalOpen.value = false;
    ElMessage.success(
      result.refund
        ? "退款申请已进入人工审核；尚未执行退款。"
        : "已拒绝本次申请。",
    );
    await Promise.all([loadRefunds(), loadOrders()]);
    eligibility.value = null;
  } catch (error) {
    if (actor === identity.value) confirmationError.value = errorInfo(error);
  } finally {
    confirmationBusy.value = false;
  }
}
function recheckDeniedApproval() {
  if (!approval.value || !confirmationDenied.value || confirmationBusy.value)
    return;
  const number = approval.value.order_no;
  if (auth.user) pendingRefundConfirmations.delete(auth.user.id);
  approval.value = null;
  approvalOpen.value = false;
  approvalKey.value = "";
  submittedDecision.value = null;
  confirmationError.value = null;
  void Promise.all([loadRefunds(), openOrder(number)]);
}
function openStaffAction(refund: Refund, action: StaffAction) {
  if (!isStaff.value || staffBusy.value) return;
  staffTarget.value = refund;
  staffAction.value = action;
  staffNote.value = "";
  staffAcknowledged.value = false;
  staffError.value = null;
}
const staffTitle = computed(
  () =>
    ({
      approve: "批准退款申请",
      reject: "驳回退款申请",
      "complete-simulated": "模拟完成退款",
    })[staffAction.value],
);
async function submitStaffAction() {
  if (
    !staffTarget.value ||
    !isStaff.value ||
    staffBusy.value ||
    !staffAcknowledged.value
  )
    return;
  if (staffAction.value !== "complete-simulated" && !staffNote.value.trim())
    return;
  const actor = identity.value;
  const target = staffTarget.value;
  staffBusy.value = true;
  staffError.value = null;
  try {
    await api<Refund>(
      `/api/v1/refunds/${target.id}/${staffAction.value === "complete-simulated" ? "complete-simulated" : "review"}`,
      {
        method: "POST",
        ...(staffAction.value !== "complete-simulated"
          ? {
              body: {
                decision: staffAction.value,
                note: staffNote.value.trim(),
              },
            }
          : {}),
      },
    );
    if (actor !== identity.value) return;
    staffTarget.value = null;
    ElMessage.success(
      staffAction.value === "complete-simulated"
        ? "模拟退款已完成；未接入真实支付。"
        : "审核结果已保存。",
    );
    await loadRefunds();
  } catch (error) {
    if (actor === identity.value) staffError.value = errorInfo(error);
  } finally {
    staffBusy.value = false;
  }
}
async function downloadPolicy(documentId: string, title: string) {
  policyDownloadError.value = null;
  try {
    const source = await read<KnowledgeDocument>(
      `/api/v1/knowledge/documents/${documentId}`,
    );
    await downloadFile(
      `/api/v1/knowledge/documents/${documentId}/download`,
      `${title}.${source.file_type === "markdown" ? "md" : source.file_type}`,
    );
  } catch (error) {
    policyDownloadError.value = errorInfo(error);
  }
}
watch(reason, () => {
  eligibilitySequence++;
  eligibility.value = null;
  eligibilityLoading.value = false;
  eligibilityError.value = null;
});
watch(
  identity,
  () => {
    stopReads();
    orderSequence++;
    refundSequence++;
    detailSequence++;
    eligibilitySequence++;
    orders.value = refunds.value = null;
    orderPage.value = refundPage.value = 1;
    detail.value = null;
    drawerOpen.value = false;
    approval.value = null;
    approvalKey.value = "";
    submittedDecision.value = null;
    confirmationError.value = null;
    confirmationAcknowledged.value = false;
    approvalOpen.value = false;
    staffTarget.value = null;
    orderError.value = refundError.value = null;
    if (isCustomer.value && auth.user) {
      const pending = pendingRefundConfirmations.get(auth.user.id);
      if (pending) {
        approval.value = pending.approval;
        approvalKey.value = pending.idempotencyKey;
        submittedDecision.value = pending.decision;
      }
    }
    if (canRead.value) void Promise.all([loadOrders(), loadRefunds()]);
  },
  { immediate: true },
);
onBeforeUnmount(stopReads);
</script>

<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">ORDER OPERATIONS</div>
      <h1>订单与售后</h1>
      <p class="muted">
        数据库事实、政策校验与人工审核，让每一步售后处理都有依据。
      </p>
    </div>
    <el-tag effect="plain" type="warning">模拟业务 · 无真实支付</el-tag>
  </div>
  <div v-if="!canRead" class="panel empty-state">
    <h2>当前角色无权访问订单</h2>
    <p class="muted">
      订单页面开放给客户、客服与管理员。分析员请前往运营分析查看授权的汇总数据。
    </p>
    <router-link to="/analytics"
      ><el-button type="primary">前往运营分析</el-button></router-link
    >
  </div>
  <template v-else>
    <div class="metric-grid order-metrics">
      <div class="metric-card">
        <span class="muted">可访问订单</span
        ><strong>{{ orders?.total ?? "—" }}</strong
        ><small>{{
          isCustomer ? "仅展示本人订单" : "客服 / 管理员授权范围"
        }}</small>
      </div>
      <div class="metric-card">
        <span class="muted">退款申请</span
        ><strong>{{ refunds?.total ?? "—" }}</strong
        ><small>申请、审核与模拟完成分开记录</small>
      </div>
      <div class="metric-card">
        <span class="muted">业务保障</span
        ><strong class="text-metric">先确认，再提交</strong
        ><small>后端验证归属、政策与状态转换</small>
      </div>
    </div>
    <el-alert
      v-if="approval"
      class="pending-banner"
      type="warning"
      :closable="false"
      title="有一项退款操作尚未获得明确结果。"
    >
      <p>
        订单 {{ approval.order_no }} · {{ money(approval.amount_cents) }}。{{
          submittedDecision === null
            ? "等待您核对并确认或拒绝。"
            : "请使用原幂等键手动重试，避免重复操作。"
        }}
      </p>
      <p>
        待确认记录仅保留在本次页面会话。若刷新页面后重新登录，请先查看售后列表，核对是否已提交。
      </p>
      <el-button size="small" @click="approvalOpen = true">继续处理</el-button>
    </el-alert>
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>订单列表</h2>
          <p class="muted">金额以人民币元展示，数据库按整数分保存。</p>
        </div>
        <el-button :loading="orderLoading" @click="loadOrders"
          >刷新订单</el-button
        >
      </div>
      <form
        class="toolbar order-search"
        @submit.prevent="openOrder(orderNumber)"
      >
        <el-input
          v-model="orderNumber"
          maxlength="40"
          clearable
          placeholder="输入准确订单号，例如 10086"
          aria-label="订单号"
        />
        <el-button type="primary" native-type="submit" :loading="detailLoading"
          >查询订单</el-button
        >
      </form>
      <el-alert
        v-if="orderError"
        type="error"
        :title="orderError.message"
        :closable="false"
        ><p v-if="orderError.requestId">
          请求编号：{{ orderError.requestId }}
        </p></el-alert
      >
      <el-table
        v-loading="orderLoading"
        :data="orders?.items ?? []"
        empty-text="暂无可访问订单"
        class="business-table"
      >
        <el-table-column prop="order_no" label="订单号" min-width="150"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="openOrder(row.order_no)">{{
              row.order_no
            }}</el-button></template
          ></el-table-column
        >
        <el-table-column label="状态" min-width="110"
          ><template #default="{ row }"
            ><el-tag
              :type="row.status === 'delivered' ? 'success' : 'info'"
              effect="light"
              >{{ orderLabels[row.status] ?? row.status }}</el-tag
            ></template
          ></el-table-column
        >
        <el-table-column label="商品金额" min-width="115" align="right"
          ><template #default="{ row }">{{
            money(row.goods_paid_cents)
          }}</template></el-table-column
        >
        <el-table-column label="运费" min-width="90" align="right"
          ><template #default="{ row }">{{
            money(row.shipping_paid_cents)
          }}</template></el-table-column
        >
        <el-table-column label="实付总额" min-width="115" align="right"
          ><template #default="{ row }"
            ><strong>{{ money(row.paid_total_cents) }}</strong></template
          ></el-table-column
        >
        <el-table-column label="创建时间 · 北京时间" min-width="190"
          ><template #default="{ row }">{{
            date(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column label="操作" width="105" fixed="right"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="openOrder(row.order_no)"
              >查看详情</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <el-pagination
        v-model:current-page="orderPage"
        class="pagination"
        :page-size="pageSize"
        :total="orders?.total ?? 0"
        layout="total, prev, pager, next"
        @current-change="loadOrders"
      />
    </section>
    <section class="panel refund-panel">
      <div class="panel-header">
        <div>
          <h2>售后申请</h2>
          <p class="muted">待审核表示已提交申请；模拟完成不会触发真实退款。</p>
        </div>
        <el-button :loading="refundLoading" @click="loadRefunds"
          >刷新售后</el-button
        >
      </div>
      <el-alert
        v-if="refundError"
        type="error"
        :title="refundError.message"
        :closable="false"
        ><p v-if="refundError.requestId">
          请求编号：{{ refundError.requestId }}
        </p></el-alert
      >
      <el-table
        v-loading="refundLoading"
        :data="refunds?.items ?? []"
        empty-text="暂无退款申请"
        class="business-table"
      >
        <el-table-column prop="order_no" label="订单号" min-width="135"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="openOrder(row.order_no)">{{
              row.order_no
            }}</el-button></template
          ></el-table-column
        >
        <el-table-column label="原因" min-width="120"
          ><template #default="{ row }">{{
            reasonLabels[row.reason] ?? row.reason
          }}</template></el-table-column
        >
        <el-table-column label="申请金额" min-width="110" align="right"
          ><template #default="{ row }">{{
            money(row.amount_cents)
          }}</template></el-table-column
        >
        <el-table-column label="状态" min-width="190"
          ><template #default="{ row }"
            ><el-tag
              :type="
                row.status === 'completed_simulated'
                  ? 'success'
                  : row.status === 'pending_review'
                    ? 'warning'
                    : 'info'
              "
              >{{ refundLabels[row.status] ?? row.status }}</el-tag
            ></template
          ></el-table-column
        >
        <el-table-column label="审核备注" min-width="155" show-overflow-tooltip
          ><template #default="{ row }">{{
            row.review_note ?? "—"
          }}</template></el-table-column
        >
        <el-table-column label="申请时间 · 北京时间" min-width="190"
          ><template #default="{ row }">{{
            date(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column
          v-if="isStaff"
          label="操作"
          min-width="165"
          fixed="right"
          ><template #default="{ row }">
            <template v-if="row.status === 'pending_review'"
              ><el-button
                link
                type="primary"
                @click="openStaffAction(row, 'approve')"
                >批准</el-button
              ><el-button
                link
                type="danger"
                @click="openStaffAction(row, 'reject')"
                >驳回</el-button
              ></template
            >
            <el-button
              v-else-if="row.status === 'approved'"
              link
              type="primary"
              @click="openStaffAction(row, 'complete-simulated')"
              >模拟完成</el-button
            ><span v-else class="muted">已处理</span>
          </template></el-table-column
        >
      </el-table>
      <el-pagination
        v-model:current-page="refundPage"
        class="pagination"
        :page-size="pageSize"
        :total="refunds?.total ?? 0"
        layout="total, prev, pager, next"
        @current-change="loadRefunds"
      />
    </section>
  </template>
  <el-drawer
    v-model="drawerOpen"
    title="订单详情与政策判断"
    size="min(820px, 94vw)"
  >
    <div v-loading="detailLoading" class="drawer-body">
      <el-alert
        v-if="detailError"
        type="error"
        :title="detailError.message"
        :closable="false"
        ><p v-if="detailError.requestId">
          请求编号：{{ detailError.requestId }}
        </p></el-alert
      >
      <template v-if="detail">
        <div class="detail-heading">
          <h2>{{ detail.order_no }}</h2>
          <el-tag effect="plain">{{ orderLabels[detail.status] }}</el-tag>
        </div>
        <el-descriptions :column="2" border
          ><el-descriptions-item label="实付总额">{{
            money(detail.paid_total_cents)
          }}</el-descriptions-item
          ><el-descriptions-item label="商品 / 运费"
            >{{ money(detail.goods_paid_cents) }} /
            {{ money(detail.shipping_paid_cents) }}</el-descriptions-item
          ><el-descriptions-item label="支付时间">{{
            date(detail.paid_at)
          }}</el-descriptions-item
          ><el-descriptions-item label="签收时间">{{
            date(detail.delivered_at)
          }}</el-descriptions-item></el-descriptions
        >
        <h3>订单商品</h3>
        <el-alert
          v-if="itemsError"
          type="error"
          :title="itemsError.message"
          :closable="false"
          ><p v-if="itemsError.requestId">
            请求编号：{{ itemsError.requestId }}
          </p></el-alert
        >
        <el-table :data="items" empty-text="暂无商品记录"
          ><el-table-column
            prop="product_name"
            label="商品"
            min-width="160"
          /><el-table-column
            prop="quantity"
            label="数量"
            width="75"
          /><el-table-column label="实付金额" align="right" width="120"
            ><template #default="{ row }">{{
              money(row.paid_amount_cents)
            }}</template></el-table-column
          ></el-table
        >
        <h3>物流轨迹</h3>
        <el-alert
          v-if="logisticsError"
          :type="logisticsError.message.includes('暂无物流') ? 'info' : 'error'"
          :title="logisticsError.message"
          :closable="false"
          ><p v-if="logisticsError.requestId">
            请求编号：{{ logisticsError.requestId }}
          </p></el-alert
        >
        <template v-if="logistics"
          ><p class="muted">
            {{ logistics.tracking_no }} ·
            {{ logisticsLabels[logistics.status] ?? logistics.status }}
          </p>
          <el-timeline
            ><el-timeline-item
              v-for="(event, index) in logistics.events"
              :key="index"
              >{{ eventText(event) }}</el-timeline-item
            ></el-timeline
          ></template
        >
        <h3>退款资格</h3>
        <p class="muted">
          资格以当前订单事实和已发布政策判断。确认时后端会再次校验。
        </p>
        <el-form label-position="top"
          ><el-form-item label="售后原因"
            ><el-select v-model="reason" aria-label="售后原因"
              ><el-option
                v-for="(label, value) in reasonLabels"
                :key="value"
                :label="label"
                :value="value" /></el-select></el-form-item
          ><el-form-item v-if="isCustomer" label="申请备注"
            ><el-input
              v-model="notes"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              placeholder="说明商品问题或申请原因（选填）" /></el-form-item
        ></el-form>
        <div class="toolbar">
          <el-button :loading="eligibilityLoading" @click="checkEligibility"
            >检查退款资格</el-button
          ><el-button
            v-if="isCustomer"
            type="primary"
            :loading="previewLoading"
            :disabled="!!approval || !eligibility?.eligible"
            @click="prepareRefund"
            >生成待确认申请</el-button
          >
        </div>
        <el-alert
          v-if="eligibilityError"
          class="section-alert"
          type="error"
          :title="eligibilityError.message"
          :closable="false"
          ><p v-if="eligibilityError.requestId">
            请求编号：{{ eligibilityError.requestId }}
          </p></el-alert
        >
        <el-alert
          v-if="eligibility"
          class="section-alert"
          :type="eligibility.eligible ? 'success' : 'warning'"
          :title="eligibility.message"
          :closable="false"
          ><p>
            判断代码：{{ eligibility.code
            }}<span v-if="eligibility.eligible">
              · 可申请 {{ money(eligibility.amount_cents) }}</span
            >
          </p></el-alert
        >
        <div v-if="eligibility?.policy" class="policy-card">
          <strong>{{ eligibility.policy.document_title }}</strong>
          <p class="muted">
            政策 {{ eligibility.policy.policy_code }} v{{
              eligibility.policy.version
            }}
            · 来源文档 v{{ eligibility.policy.document_version }}
          </p>
          <p>
            退款窗口 {{ eligibility.policy.refund_window_days }} 天；{{
              eligibility.policy.refund_shipping
                ? "符合政策时可退运费"
                : "本政策不退运费"
            }}。
          </p>
          <el-button
            size="small"
            @click="
              downloadPolicy(
                eligibility.policy.document_id,
                eligibility.policy.document_title,
              )
            "
            >下载政策来源</el-button
          >
        </div>
        <el-alert
          v-if="policyDownloadError"
          class="section-alert"
          type="error"
          :title="policyDownloadError.message"
          :closable="false"
          ><p v-if="policyDownloadError.requestId">
            请求编号：{{ policyDownloadError.requestId }}
          </p></el-alert
        >
      </template>
    </div>
  </el-drawer>
  <el-dialog
    v-model="approvalOpen"
    title="核对退款申请"
    width="min(620px, 94vw)"
    :close-on-click-modal="!confirmationBusy"
    :close-on-press-escape="!confirmationBusy"
    :show-close="!confirmationBusy"
  >
    <template v-if="approval">
      <el-alert
        type="warning"
        :closable="false"
        title="确认只创建待审核申请，不执行真实退款。"
      />
      <el-descriptions :column="1" border class="confirmation-details"
        ><el-descriptions-item label="订单号">{{
          approval.order_no
        }}</el-descriptions-item
        ><el-descriptions-item label="申请金额">{{
          money(approval.amount_cents)
        }}</el-descriptions-item
        ><el-descriptions-item label="原因">{{
          reasonLabels[approval.reason]
        }}</el-descriptions-item
        ><el-descriptions-item label="备注">{{
          approval.notes || "无"
        }}</el-descriptions-item
        ><el-descriptions-item label="政策来源"
          >{{ approval.policy.document_title }} · 文档 v{{
            approval.policy.document_version
          }}</el-descriptions-item
        ><el-descriptions-item label="确认截止"
          >{{ date(approval.expires_at) }}（北京时间）</el-descriptions-item
        ></el-descriptions
      >
      <el-checkbox
        v-model="confirmationAcknowledged"
        :disabled="submittedDecision === false"
        >我已核对订单、金额、原因和政策，提交后进入人工审核</el-checkbox
      >
      <el-alert
        v-if="confirmationError"
        class="section-alert"
        type="error"
        :title="confirmationError.message"
        :closable="false"
        ><p v-if="confirmationError.requestId">
          请求编号：{{ confirmationError.requestId }}
        </p>
        <p>
          {{
            confirmationDenied
              ? "后端已拒绝本次确认。请重新检查订单与售后状态，核对当前规则。"
              : "结果尚未明确。重试将保留原请求参数和幂等键；不会自动重新提交。"
          }}
        </p></el-alert
      >
    </template>
    <template #footer
      ><el-button :disabled="confirmationBusy" @click="approvalOpen = false"
        >稍后处理</el-button
      ><el-button
        v-if="confirmationDenied"
        type="primary"
        @click="recheckDeniedApproval"
        >重新检查订单</el-button
      ><template v-else
        ><el-button
          type="danger"
          plain
          :loading="confirmationBusy && submittedDecision === false"
          :disabled="confirmationBusy || submittedDecision === true"
          @click="confirmRefund(false)"
          >{{
            submittedDecision === false ? "重试拒绝申请" : "拒绝申请"
          }}</el-button
        ><el-button
          type="primary"
          :loading="confirmationBusy && submittedDecision === true"
          :disabled="
            confirmationBusy ||
            !confirmationAcknowledged ||
            submittedDecision === false
          "
          @click="confirmRefund(true)"
          >{{
            submittedDecision === true ? "使用原幂等键重试确认" : "确认提交申请"
          }}</el-button
        ></template
      ></template
    >
  </el-dialog>
  <el-dialog
    :model-value="!!staffTarget"
    :title="staffTitle"
    width="min(600px, 94vw)"
    :close-on-click-modal="!staffBusy"
    :close-on-press-escape="!staffBusy"
    :show-close="!staffBusy"
    @update:model-value="
      (open: boolean) => {
        if (!open && !staffBusy) staffTarget = null;
      }
    "
  >
    <template v-if="staffTarget"
      ><el-alert
        type="warning"
        :closable="false"
        :title="
          staffAction === 'complete-simulated'
            ? '此操作仅记录模拟完成，不连接真实支付系统。'
            : '请依据订单事实与政策进行人工审核。'
        "
      />
      <div class="staff-summary">
        <strong>订单 {{ staffTarget.order_no }}</strong
        ><span
          >{{ money(staffTarget.amount_cents) }} ·
          {{ reasonLabels[staffTarget.reason] ?? staffTarget.reason }}</span
        >
        <p class="muted">申请备注：{{ staffTarget.notes || "无" }}</p>
      </div>
      <el-form v-if="staffAction !== 'complete-simulated'" label-position="top"
        ><el-form-item label="审核备注（必填）"
          ><el-input
            v-model="staffNote"
            type="textarea"
            maxlength="500"
            show-word-limit
            :rows="3"
            placeholder="记录本次审核的依据" /></el-form-item></el-form
      ><el-checkbox v-model="staffAcknowledged"
        >我已核对申请，确认执行「{{ staffTitle }}」</el-checkbox
      ><el-alert
        v-if="staffError"
        class="section-alert"
        type="error"
        :title="staffError.message"
        :closable="false"
        ><p v-if="staffError.requestId">请求编号：{{ staffError.requestId }}</p>
        <p>
          若网络中断，请关闭弹窗并刷新售后列表，核对实际状态后再操作。
        </p></el-alert
      ></template
    >
    <template #footer
      ><el-button :disabled="staffBusy" @click="staffTarget = null"
        >取消</el-button
      ><el-button
        :type="staffAction === 'reject' ? 'danger' : 'primary'"
        :loading="staffBusy"
        :disabled="
          !staffAcknowledged ||
          (staffAction !== 'complete-simulated' && !staffNote.trim())
        "
        @click="submitStaffAction"
        >确认{{ staffTitle }}</el-button
      ></template
    >
  </el-dialog>
</template>

<style scoped>
.order-metrics {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 24px;
}
.metric-card strong {
  display: block;
  margin: 12px 0 8px;
  font-size: 30px;
}
.metric-card strong.text-metric {
  font-size: 20px;
}
.metric-card small {
  color: var(--text-muted, #7c817b);
}
.pending-banner {
  margin-bottom: 20px;
}
.order-search {
  max-width: 560px;
  margin-bottom: 20px;
  flex-wrap: nowrap;
}
.business-table {
  margin-top: 12px;
}
.pagination {
  justify-content: flex-end;
  margin-top: 20px;
}
.refund-panel {
  margin-top: 24px;
}
.drawer-body {
  min-height: 160px;
}
.detail-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;
}
.detail-heading h2 {
  margin: 0;
}
.drawer-body h3 {
  margin-top: 28px;
  margin-bottom: 14px;
  font-size: 15px;
}
.section-alert {
  margin-top: 16px;
}
.policy-card {
  margin-top: 16px;
  padding: 18px;
  border: 1px solid #dce7df;
  border-radius: 12px;
  background: #f3f7f2;
}
.policy-card p {
  font-size: 13px;
  line-height: 1.7;
}
.confirmation-details {
  margin: 20px 0;
}
.staff-summary {
  display: grid;
  gap: 8px;
  margin: 20px 0;
}
:deep(.el-checkbox) {
  height: auto;
  white-space: normal;
  line-height: 1.6;
}
:deep(.el-checkbox__label) {
  white-space: normal;
}
@media (max-width: 1100px) {
  .order-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .order-metrics > :last-child {
    grid-column: 1 / -1;
  }
}
</style>
