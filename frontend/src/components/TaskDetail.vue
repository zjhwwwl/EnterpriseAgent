<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, errorMessage } from "../api/client";
import {
  canRecover,
  formatTime,
  reasonLabel,
  statusLabel,
  statusType,
  taskTypeLabel,
} from "../api/agent-types";
import type { AgentTask } from "../api/agent-types";
import { money } from "../utils/format";
import MarkdownContent from "./MarkdownContent.vue";
import CitationList from "./CitationList.vue";
import AnalyticsChart from "./AnalyticsChart.vue";

const props = withDefaults(
  defineProps<{ task: AgentTask; compact?: boolean }>(),
  { compact: false },
);
const emit = defineEmits<{ updated: [task: AgentTask] }>();
const busy = ref(false);
const error = ref("");
const notice = ref("");
const approvalDialog = ref(false);
const recoverDialog = ref(false);
const checked = ref(false);
const now = ref(Date.now());
const timer = window.setInterval(() => {
  now.value = Date.now();
}, 30_000);
onBeforeUnmount(() => window.clearInterval(timer));

watch(
  () => props.task.id,
  () => {
    error.value = "";
    notice.value = "";
    approvalDialog.value = false;
    recoverDialog.value = false;
    checked.value = false;
  },
);

const approval = computed(() => props.task.result.approval ?? null);
const refund = computed(() => props.task.result.refund_result?.refund ?? null);
const analysis = computed(() => props.task.result.analysis ?? null);
const citations = computed(() => props.task.result.citations ?? []);
const approvalExpired = computed(() => {
  if (!approval.value) return true;
  const deadline = new Date(approval.value.expires_at).getTime();
  return !Number.isFinite(deadline) || deadline <= now.value;
});
const waiting = computed(() => props.task.status === "waiting_approval");
const recoverable = computed(() => canRecover(props.task));
const usage = computed(() => props.task.token_usage);
const usageIncomplete = computed(
  () =>
    usage.value.usage_complete !== true ||
    usage.value.interrupted_usage_unknown === true ||
    (usage.value.unknown_usage_calls ?? 0) > 0,
);
function usageValue(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN")
    : "未记录";
}
function elapsed(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toLocaleString("zh-CN", { maximumFractionDigits: 3 })} ms`
    : "未记录";
}

const nodeLabels: Record<string, string> = {
  intent_router: "识别任务意图",
  knowledge_retrieval: "检索知识库",
  query_rewrite: "改写检索问题",
  tool_planner: "选择业务工具",
  tool_execution: "执行业务工具",
  business_tool: "业务工具",
  human_approval: "人工确认",
  commit_refund: "提交退款申请",
  response_generation: "生成回答",
  validation: "验证结果",
  model_call: "模型调用",
  analytics_planner: "规划分析 SQL",
  analytics_execution: "执行只读 SQL",
};
function nodeLabel(node?: string): string {
  return node ? (nodeLabels[node] ?? node) : "执行步骤";
}
function openApproval() {
  error.value = "";
  checked.value = false;
  now.value = Date.now();
  approvalDialog.value = true;
}

async function refresh() {
  if (busy.value) return;
  const taskId = props.task.id;
  busy.value = true;
  error.value = "";
  try {
    const task = await api<AgentTask>(
      `/api/v1/agent/tasks/${encodeURIComponent(taskId)}`,
    );
    if (props.task.id === taskId) {
      emit("updated", task);
      notice.value = "已读取服务器上的最新任务状态。";
    }
  } catch (failure) {
    error.value = errorMessage(failure);
  } finally {
    busy.value = false;
  }
}

async function decide(confirmed: boolean) {
  const currentApproval = approval.value;
  if (
    busy.value ||
    !waiting.value ||
    !currentApproval ||
    (confirmed && !checked.value)
  )
    return;
  // Expiry and backend business rules are checked again by the server.
  now.value = Date.now();
  if (confirmed && approvalExpired.value) {
    error.value = "此确认已到期，请先刷新服务器状态。";
    return;
  }
  const taskId = props.task.id;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const task = await api<AgentTask>(
      `/api/v1/agent/tasks/${encodeURIComponent(taskId)}/resume`,
      {
        method: "POST",
        body: { approval_id: currentApproval.approval_id, confirmed },
      },
    );
    if (props.task.id === taskId) {
      emit("updated", task);
      approvalDialog.value = false;
      notice.value = `服务器返回：${statusLabel(task.status)}。`;
    }
  } catch (failure) {
    error.value = `${errorMessage(failure)}。请先刷新任务核对实际结果，避免重复提交。`;
  } finally {
    busy.value = false;
  }
}

async function recover() {
  if (busy.value || !recoverable.value) return;
  const taskId = props.task.id;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const task = await api<AgentTask>(
      `/api/v1/agent/tasks/${encodeURIComponent(taskId)}/recover`,
      { method: "POST", body: {} },
    );
    if (props.task.id === taskId) {
      emit("updated", task);
      recoverDialog.value = false;
      notice.value = `恢复操作返回：${statusLabel(task.status)}。`;
    }
  } catch (failure) {
    error.value = `${errorMessage(failure)}。请刷新任务状态后再决定后续操作。`;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section
    class="task-detail"
    :data-task-id="task.id"
    :data-task-status="task.status"
  >
    <div class="task-head">
      <div class="task-head-tags">
        <el-tag
          :type="statusType(task.status)"
          effect="light"
          data-testid="task-status"
          >{{ statusLabel(task.status) }}</el-tag
        >
        <span class="muted">{{ taskTypeLabel(task.task_type) }}</span>
        <span class="muted">{{ formatTime(task.created_at) }}</span>
      </div>
      <el-button text size="small" :loading="busy" @click="refresh"
        >刷新状态</el-button
      >
    </div>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
      class="task-alert"
    />
    <el-alert
      v-if="notice"
      :title="notice"
      type="info"
      :closable="false"
      class="task-alert"
    />
    <el-alert
      v-if="task.error_code"
      :title="`任务错误：${task.error_code}`"
      type="error"
      :closable="false"
      show-icon
      class="task-alert"
    />

    <template v-if="waiting">
      <div v-if="approval" class="approval-panel" data-testid="approval-panel">
        <div class="approval-title">
          <span class="approval-dot"></span><strong>退款申请需要你确认</strong>
        </div>
        <p>确认后创建待审核的售后工单；实际申请状态以服务器返回为准。</p>
        <div class="approval-grid">
          <div>
            <span>订单编号</span><strong>{{ approval.order_no }}</strong>
          </div>
          <div>
            <span>申请金额</span
            ><strong class="amount">{{ money(approval.amount_cents) }}</strong>
          </div>
          <div>
            <span>申请原因</span
            ><strong>{{ reasonLabel(approval.reason) }}</strong>
          </div>
          <div>
            <span>确认截止（上海时间）</span
            ><strong>{{ formatTime(approval.expires_at) }}</strong>
          </div>
        </div>
        <p v-if="approval.notes" class="approval-notes">
          备注：{{ approval.notes }}
        </p>
        <p class="policy-line">
          适用政策：{{ approval.policy.document_title }} v{{
            approval.policy.document_version
          }}
          · {{ approval.policy.policy_code }} v{{ approval.policy.version }}
        </p>
        <el-alert
          v-if="approvalExpired"
          title="确认期限已到，请刷新任务或拒绝本次申请；服务器会再次核验期限。"
          type="warning"
          :closable="false"
          class="task-alert"
        />
        <div class="approval-actions">
          <el-button
            type="primary"
            :disabled="busy || approvalExpired"
            @click="openApproval"
            data-testid="review-approval"
            >核对并确认申请</el-button
          >
          <el-button :disabled="busy" @click="openApproval"
            >查看详情 / 拒绝</el-button
          >
        </div>
      </div>
      <el-alert
        v-else
        title="任务正在等待人工确认，但当前返回未包含确认内容。请刷新任务核对服务器状态。"
        type="warning"
        :closable="false"
        show-icon
      />
    </template>

    <div
      v-if="task.result.answer"
      class="answer-content"
      data-testid="task-answer"
    >
      <MarkdownContent :content="task.result.answer" />
    </div>
    <p v-else-if="!waiting" class="muted answer-placeholder">
      {{
        task.status === "running"
          ? "服务器尚未返回最终回答，请手动刷新任务状态。"
          : "此任务没有回答正文。"
      }}
    </p>

    <div v-if="refund" class="refund-receipt" data-testid="refund-receipt">
      <strong>申请提交时的退款回执</strong>
      <div class="receipt-grid">
        <span>订单 {{ refund.order_no }}</span
        ><span>金额 {{ money(refund.amount_cents) }}</span>
        <span
          >状态
          <el-tag size="small" type="info">{{ refund.status }}</el-tag></span
        >
      </div>
      <code>申请 ID：{{ refund.id }}</code>
      <p class="muted">此为提交时记录；最新审核状态请在“订单与售后”查看。</p>
      <p v-if="task.result.refund_result?.replayed" class="muted">
        服务器返回已有的幂等申请记录。
      </p>
    </div>

    <AnalyticsChart v-if="analysis?.chart" :chart="analysis.chart" />
    <details v-if="analysis" class="analysis-rows">
      <summary>
        完整受限查询结果 · {{ analysis.query.row_count }} 行{{
          analysis.query.truncated ? " · 已截断" : ""
        }}
      </summary>
      <div class="query-table-scroll">
        <table class="query-table">
          <thead>
            <tr>
              <th v-for="column in analysis.query.columns" :key="column">
                {{ column }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in analysis.query.rows" :key="rowIndex">
              <td v-for="(cell, cellIndex) in row" :key="cellIndex">
                {{ cell === null ? "NULL" : String(cell) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted">
        查询耗时
        {{
          elapsed(analysis.query.elapsed_ms)
        }}；金额原始值以人民币分计，派生值按执行 SQL 解释。
      </p>
    </details>

    <CitationList :citations="citations" />
    <el-alert
      v-for="warning in task.result.warning_codes ?? []"
      :key="warning"
      :title="`结果提示：${warning}`"
      type="warning"
      :closable="false"
      class="task-alert"
    />

    <div v-if="recoverable && !waiting" class="recover-panel">
      <p>
        此任务可能有尚未核对的持久状态。恢复会读取检查点，并按后端状态继续任务；后续步骤可能调用模型。
      </p>
      <el-button
        :disabled="busy"
        @click="recoverDialog = true"
        data-testid="recover-task"
        >核对并恢复任务</el-button
      >
    </div>

    <details class="execution-details" :open="!compact">
      <summary>
        执行详情 · {{ task.steps.length }} 个记录 ·
        {{ elapsed(task.elapsed_ms) }}
      </summary>
      <div class="usage-grid">
        <div>
          <span>模型尝试次数</span
          ><strong>{{ usageValue(usage.model_calls) }}</strong>
        </div>
        <div>
          <span>已知输入 Token</span
          ><strong>{{ usageValue(usage.prompt_tokens) }}</strong>
        </div>
        <div>
          <span>已知输出 Token</span
          ><strong>{{ usageValue(usage.completion_tokens) }}</strong>
        </div>
        <div>
          <span>已知总 Token</span
          ><strong>{{ usageValue(usage.total_tokens) }}</strong>
        </div>
      </div>
      <p
        v-if="usageIncomplete"
        class="usage-warning"
        data-testid="unknown-usage"
      >
        消耗记录不完整{{
          typeof usage.unknown_usage_calls === "number"
            ? `，${usage.unknown_usage_calls} 次模型调用的用量未知`
            : ""
        }}{{
          usage.interrupted_usage_unknown
            ? "；执行中断期间的用量也可能未知"
            : ""
        }}。已知 Token 不能视为实际总消耗。
      </p>
      <ol v-if="task.steps.length" class="step-list">
        <li v-for="(step, index) in task.steps" :key="index">
          <span class="step-index">{{ index + 1 }}</span>
          <div class="step-info">
            <strong
              >{{ nodeLabel(step.node) }}
              <code v-if="step.tool">{{ step.tool }}</code></strong
            >
            <span class="muted"
              >{{ step.node
              }}{{
                step.attempt !== undefined ? ` · 第 ${step.attempt} 次尝试` : ""
              }}</span
            >
            <span v-if="step.error_code" class="step-error">{{
              step.error_code
            }}</span>
          </div>
          <span class="muted step-elapsed">{{ elapsed(step.elapsed_ms) }}</span>
        </li>
      </ol>
      <p v-else class="muted">尚无执行步骤记录。</p>
      <details v-if="task.result.facts?.length" class="facts-details">
        <summary>已执行工具的参数与结果</summary>
        <pre>{{ JSON.stringify(task.result.facts, null, 2) }}</pre>
      </details>
      <div class="task-identifiers">
        <span>任务 ID：{{ task.id }}</span
        ><span>会话 ID：{{ task.session_id }}</span
        ><span>最后更新：{{ formatTime(task.updated_at) }}</span>
      </div>
    </details>

    <el-dialog
      v-model="approvalDialog"
      title="确认退款申请"
      width="min(680px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!busy"
      :show-close="!busy"
      data-testid="approval-dialog"
    >
      <template v-if="approval">
        <el-alert
          title="此操作只创建模拟退款申请和待审核工单，不执行支付退款。"
          type="info"
          :closable="false"
        />
        <el-descriptions :column="1" border class="approval-description">
          <el-descriptions-item label="订单编号">{{
            approval.order_no
          }}</el-descriptions-item>
          <el-descriptions-item label="退款金额">{{
            money(approval.amount_cents)
          }}</el-descriptions-item>
          <el-descriptions-item label="申请原因">{{
            reasonLabel(approval.reason)
          }}</el-descriptions-item>
          <el-descriptions-item label="申请备注">{{
            approval.notes || "无"
          }}</el-descriptions-item>
          <el-descriptions-item label="确认截止"
            >{{
              formatTime(approval.expires_at)
            }}（上海时间）</el-descriptions-item
          >
          <el-descriptions-item label="政策版本"
            >{{ approval.policy.policy_code }} v{{
              approval.policy.version
            }}</el-descriptions-item
          >
          <el-descriptions-item label="知识文档"
            >{{ approval.policy.document_title }} v{{
              approval.policy.document_version
            }}</el-descriptions-item
          >
          <el-descriptions-item label="退款窗口"
            >{{ approval.policy.refund_window_days }} 天；{{
              approval.policy.refund_shipping
                ? "政策允许退运费"
                : "政策不退运费"
            }}</el-descriptions-item
          >
        </el-descriptions>
        <p class="muted">确认 ID：{{ approval.approval_id }}</p>
        <el-checkbox
          v-model="checked"
          :disabled="busy || approvalExpired"
          data-testid="approval-checkbox"
          >我已核对订单、金额、原因和政策，并确认提交申请。</el-checkbox
        >
        <el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          class="task-alert"
        />
        <el-alert
          v-if="approvalExpired"
          title="确认已到期，无法继续确认。"
          type="warning"
          :closable="false"
          class="task-alert"
        />
      </template>
      <template #footer>
        <el-button :disabled="busy" @click="approvalDialog = false"
          >暂不处理</el-button
        >
        <el-button
          type="danger"
          plain
          :loading="busy"
          :disabled="!waiting"
          @click="decide(false)"
          data-testid="reject-approval"
          >拒绝申请</el-button
        >
        <el-button
          type="primary"
          :loading="busy"
          :disabled="!waiting || !checked || approvalExpired"
          @click="decide(true)"
          data-testid="confirm-approval"
          >确认提交</el-button
        >
      </template>
    </el-dialog>

    <el-dialog
      v-model="recoverDialog"
      title="恢复中断任务"
      width="min(540px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!busy"
      :show-close="!busy"
    >
      <p>
        系统会核对任务的持久化检查点和已提交的业务记录，必要时继续执行。已有退款申请受后端幂等规则保护。
      </p>
      <p class="muted">任务 ID：{{ task.id }}</p>
      <p>继续执行可能调用模型并消耗 Token。关闭此窗口会保留当前状态。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template #footer
        ><el-button :disabled="busy" @click="recoverDialog = false"
          >暂不恢复</el-button
        ><el-button
          type="primary"
          :loading="busy"
          @click="recover"
          data-testid="confirm-recover"
          >恢复任务</el-button
        ></template
      >
    </el-dialog>
  </section>
</template>

<style scoped>
.task-detail {
  min-width: 0;
}
.task-head,
.task-head-tags {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.task-head {
  justify-content: space-between;
}
.task-head-tags {
  font-size: 12px;
}
.task-alert {
  margin-top: 12px;
}
.answer-content {
  margin-top: 14px;
}
.answer-placeholder {
  line-height: 1.8;
}
.approval-panel {
  background: #fff8ec;
  border: 1px solid #efd9b7;
  border-radius: 12px;
  padding: 18px;
  margin: 16px 0;
}
.approval-title {
  display: flex;
  gap: 9px;
  align-items: center;
  color: #855b22;
}
.approval-dot {
  width: 7px;
  height: 7px;
  background: #bc8141;
  border-radius: 50%;
}
.approval-panel > p {
  font-size: 13px;
  line-height: 1.7;
}
.approval-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px 20px;
  margin: 18px 0;
}
.approval-grid > div {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.approval-grid span {
  color: #84735c;
  font-size: 12px;
}
.approval-grid strong {
  font-size: 14px;
  font-weight: 550;
}
.approval-grid .amount {
  font-size: 21px;
  color: #81501b;
}
.approval-notes,
.policy-line {
  overflow-wrap: anywhere;
}
.approval-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}
.approval-actions .el-button + .el-button {
  margin-left: 0;
}
.approval-description {
  margin-top: 18px;
}
.refund-receipt {
  border-left: 3px solid #3d795e;
  padding: 14px 18px;
  background: #f0f6ef;
  margin: 16px 0;
  font-size: 13px;
}
.receipt-grid {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  margin: 12px 0;
}
.refund-receipt code {
  overflow-wrap: anywhere;
  font-size: 11px;
}
.recover-panel {
  margin-top: 16px;
  padding: 15px;
  border: 1px solid #e8d6bd;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.8;
}
.execution-details,
.analysis-rows {
  margin-top: 18px;
  border-top: 1px solid var(--color-border, #e4e8e1);
  padding-top: 13px;
}
summary {
  font-size: 12px;
  cursor: pointer;
  color: var(--color-primary, #245b48);
}
.usage-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0 12px;
}
.usage-grid div {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.usage-grid span {
  color: var(--color-muted, #748174);
  font-size: 11px;
}
.usage-grid strong {
  font-size: 16px;
  font-weight: 550;
}
.usage-warning {
  font-size: 12px;
  line-height: 1.8;
  color: #8a632d;
}
.step-list {
  list-style: none;
  margin: 20px 0;
  padding: 0;
}
.step-list li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 11px 0;
  border-bottom: 1px solid var(--color-border, #e6e9e2);
}
.step-index {
  background: #edf3eb;
  color: #346346;
  border-radius: 6px;
  width: 23px;
  height: 23px;
  display: grid;
  place-items: center;
  font-size: 11px;
  flex-shrink: 0;
}
.step-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 0;
  font-size: 12px;
}
.step-info strong {
  font-weight: 500;
}
.step-info code {
  font-size: 11px;
  color: #456f54;
  overflow-wrap: anywhere;
}
.step-error {
  color: #aa4941;
  font-size: 11px;
  overflow-wrap: anywhere;
}
.step-elapsed {
  flex-shrink: 0;
  font-size: 11px;
}
.facts-details pre {
  max-height: 360px;
  overflow: auto;
  font-size: 11px;
  line-height: 1.7;
  background: #f5f7f2;
  padding: 12px;
  border-radius: 8px;
}
.task-identifiers {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 15px;
  font-size: 10px;
  color: var(--color-muted, #798476);
  overflow-wrap: anywhere;
}
.query-table-scroll {
  overflow: auto;
  margin-top: 14px;
  max-height: 360px;
}
.query-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.query-table th,
.query-table td {
  text-align: left;
  white-space: nowrap;
  padding: 9px 12px;
  border-bottom: 1px solid var(--color-border, #e4e8e1);
}
.query-table th {
  background: #f3f6f0;
}
.analysis-rows > p {
  font-size: 11px;
  line-height: 1.7;
}
@media (max-width: 800px) {
  .usage-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .approval-grid {
    grid-template-columns: 1fr;
  }
}
</style>
