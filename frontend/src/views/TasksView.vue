<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, errorMessage } from "../api/client";
import {
  formatTime,
  statusLabel,
  statusType,
  taskTypeLabel,
} from "../api/agent-types";
import type { AgentTask, Page } from "../api/agent-types";
import TaskDetail from "../components/TaskDetail.vue";

const route = useRoute();
const router = useRouter();
const tasks = ref<AgentTask[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 20;
const loading = ref(false);
const error = ref("");
const detailError = ref("");
const detailLoading = ref(false);
const selectedTask = ref<AgentTask | null>(null);
const showDetail = ref(false);
const filterSession = ref(
  typeof route.query.session === "string" ? route.query.session : "",
);
let listGeneration = 0;
let detailGeneration = 0;
let selectingTaskId: string | null = null;

async function loadTasks() {
  const generation = ++listGeneration;
  loading.value = true;
  error.value = "";
  const search = new URLSearchParams({
    offset: String((page.value - 1) * pageSize),
    limit: String(pageSize),
  });
  if (filterSession.value.trim())
    search.set("session_id", filterSession.value.trim());
  try {
    const result = await api<Page<AgentTask>>(`/api/v1/agent/tasks?${search}`);
    if (generation !== listGeneration) return;
    tasks.value = result.items;
    total.value = result.total;
  } catch (failure) {
    if (generation === listGeneration) {
      error.value = errorMessage(failure);
      tasks.value = [];
      total.value = 0;
    }
  } finally {
    if (generation === listGeneration) loading.value = false;
  }
}

async function openTask(taskId: string) {
  const generation = ++detailGeneration;
  selectingTaskId = taskId;
  detailLoading.value = true;
  selectedTask.value = null;
  detailError.value = "";
  showDetail.value = true;
  try {
    const task = await api<AgentTask>(
      `/api/v1/agent/tasks/${encodeURIComponent(taskId)}`,
    );
    if (generation !== detailGeneration) return;
    selectedTask.value = task;
    if (route.query.task !== task.id)
      await router.replace({
        path: "/tasks",
        query: {
          ...(filterSession.value ? { session: filterSession.value } : {}),
          task: task.id,
        },
      });
  } catch (failure) {
    if (generation === detailGeneration)
      detailError.value = errorMessage(failure);
  } finally {
    if (generation === detailGeneration) {
      detailLoading.value = false;
      selectingTaskId = null;
    }
  }
}
function updateTask(task: AgentTask) {
  selectedTask.value = task;
  tasks.value = tasks.value.map((current) =>
    current.id === task.id ? task : current,
  );
}
async function applyFilter() {
  page.value = 1;
  await router.replace({
    path: "/tasks",
    query: filterSession.value.trim()
      ? { session: filterSession.value.trim() }
      : {},
  });
  await loadTasks();
}
function duration(value: number | null): string {
  return value === null ? "未记录" : `${value.toLocaleString("zh-CN")} ms`;
}
function modelCalls(task: AgentTask): string {
  return typeof task.token_usage.model_calls === "number"
    ? String(task.token_usage.model_calls)
    : "未记录";
}
function openRow(row: AgentTask) {
  void openTask(row.id);
}
async function closeDetail() {
  // Closing a detail view never resumes, rejects, or recovers a task.
  ++detailGeneration;
  selectingTaskId = null;
  detailLoading.value = false;
  if (route.query.task)
    await router.replace({
      path: "/tasks",
      query: filterSession.value.trim()
        ? { session: filterSession.value.trim() }
        : {},
    });
}

watch(
  () => route.query.task,
  (taskId) => {
    if (
      typeof taskId === "string" &&
      (taskId !== selectedTask.value?.id || !showDetail.value) &&
      taskId !== selectingTaskId
    )
      void openTask(taskId);
    else if (taskId === undefined && showDetail.value) showDetail.value = false;
  },
);
watch(
  () => route.query.session,
  (sessionId) => {
    const nextFilter = typeof sessionId === "string" ? sessionId : "";
    if (nextFilter !== filterSession.value) {
      filterSession.value = nextFilter;
      page.value = 1;
      void loadTasks();
    }
  },
);
onMounted(() => {
  void loadTasks();
  if (typeof route.query.task === "string") void openTask(route.query.task);
});
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">EXECUTION HISTORY</span>
      <h1>任务历史</h1>
      <p class="muted">查看当前账号的真实执行记录、工具步骤和人工确认状态。</p>
    </div>
    <el-button :loading="loading" @click="loadTasks">刷新记录</el-button>
  </div>
  <section class="panel">
    <div class="panel-header">
      <div>
        <strong>我的任务</strong
        ><span class="total-label muted">{{ total }} 条符合当前条件的记录</span>
      </div>
    </div>
    <div class="task-filters">
      <label for="task-session-filter">会话 ID</label
      ><el-input
        id="task-session-filter"
        v-model="filterSession"
        clearable
        placeholder="可选，输入完整会话 UUID"
        @keyup.enter="applyFilter"
        data-testid="task-session-filter"
      /><el-button type="primary" plain @click="applyFilter">筛选</el-button
      ><el-button
        v-if="filterSession"
        @click="
          filterSession = '';
          applyFilter();
        "
        >清除</el-button
      >
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
      class="task-list-error"
    />
    <el-table
      :data="tasks"
      v-loading="loading"
      class="task-table"
      @row-click="openRow"
      row-key="id"
      empty-text="当前条件下没有任务记录"
    >
      <el-table-column label="问题 / 任务" min-width="280"
        ><template #default="{ row }"
          ><div class="task-message">{{ row.message }}</div>
          <div class="task-short-id muted">{{ row.id }}</div></template
        ></el-table-column
      >
      <el-table-column label="类型" width="112"
        ><template #default="{ row }">{{
          taskTypeLabel(row.task_type)
        }}</template></el-table-column
      >
      <el-table-column label="状态" width="125"
        ><template #default="{ row }"
          ><el-tag :type="statusType(row.status)" size="small">{{
            statusLabel(row.status)
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="耗时" width="114"
        ><template #default="{ row }">{{
          duration(row.elapsed_ms)
        }}</template></el-table-column
      >
      <el-table-column label="模型尝试" width="100"
        ><template #default="{ row }">{{
          modelCalls(row)
        }}</template></el-table-column
      >
      <el-table-column label="创建时间（上海）" width="185"
        ><template #default="{ row }">{{
          formatTime(row.created_at)
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="92" fixed="right"
        ><template #default="{ row }"
          ><el-button
            text
            type="primary"
            size="small"
            @click.stop="openTask(row.id)"
            >详情</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <div class="task-table-footer">
      <span class="muted"
        >历史状态来自后端任务记录，HTTP 请求成功不代表任务完成。</span
      ><el-pagination
        v-if="total"
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        @current-change="loadTasks"
        :pager-count="5"
      />
    </div>
  </section>

  <el-drawer
    v-model="showDetail"
    title="任务执行详情"
    size="min(900px, 95vw)"
    :close-on-click-modal="false"
    @closed="closeDetail"
  >
    <div v-loading="detailLoading" class="task-drawer-body">
      <el-alert
        v-if="detailError"
        :title="detailError"
        type="error"
        :closable="false"
        show-icon
      />
      <template v-if="selectedTask"
        ><div class="detail-question">
          <span class="eyebrow">用户问题</span>
          <p>{{ selectedTask.message }}</p>
          <router-link
            :to="{
              path: '/agent',
              query: { session: selectedTask.session_id },
            }"
            >在所属会话中查看 ↗</router-link
          >
        </div>
        <TaskDetail :task="selectedTask" @updated="updateTask"
      /></template>
    </div>
  </el-drawer>
</template>

<style scoped>
.total-label {
  margin-left: 12px;
  font-size: 12px;
  font-weight: 400;
}
.task-filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 8px 0 22px;
}
.task-filters > label {
  font-size: 12px;
  white-space: nowrap;
  color: #63715b;
}
.task-filters > .el-input {
  max-width: 390px;
}
.task-filters .el-button + .el-button {
  margin-left: 0;
}
.task-list-error {
  margin-bottom: 16px;
}
.task-table :deep(.el-table__row) {
  cursor: pointer;
}
.task-message {
  font-size: 13px;
  line-height: 1.7;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.task-short-id {
  font-size: 10px;
  margin-top: 5px;
  overflow-wrap: anywhere;
}
.task-table-footer {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: center;
  margin-top: 20px;
}
.task-table-footer > span {
  font-size: 11px;
  line-height: 1.8;
}
.task-drawer-body {
  min-height: 140px;
}
.detail-question {
  padding: 4px 0 22px;
  margin-bottom: 20px;
  border-bottom: 1px solid #e5e9e0;
}
.detail-question p {
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
}
.detail-question a {
  font-size: 12px;
  color: #2c6346;
  text-decoration: none;
}
@media (max-width: 850px) {
  .task-filters {
    flex-wrap: wrap;
  }
  .task-table-footer {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
