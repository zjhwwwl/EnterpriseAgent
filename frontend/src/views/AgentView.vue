<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, errorMessage } from "../api/client";
import { auth } from "../stores/auth";
import { blocksNextTurn, formatTime, statusLabel } from "../api/agent-types";
import type { AgentSession, AgentTask, Page } from "../api/agent-types";
import TaskDetail from "../components/TaskDetail.vue";

const route = useRoute();
const router = useRouter();
const sessions = ref<AgentSession[]>([]);
const sessionsTotal = ref(0);
const sessionPage = ref(1);
const sessionLimit = 12;
const selectedSession = ref<AgentSession | null>(null);
const tasks = ref<AgentTask[]>([]);
const tasksTotal = ref(0);
const taskPage = ref(1);
const taskLimit = 10;
const currentTask = ref<AgentTask | null>(null);
const draft = ref("");
const loadingSessions = ref(false);
const loadingTasks = ref(false);
const creating = ref(false);
const sending = ref(false);
const needsReconcile = ref(false);
const sidebarError = ref("");
const conversationError = ref("");
const draftError = ref("");
const conversationNotice = ref("");
let taskRequest = 0;
let sidebarRequest = 0;
let selectingSessionId: string | null = null;
const orderedTasks = computed(() => [...tasks.value].reverse());
const blocked = computed(() => blocksNextTurn(currentTask.value));
const canSend = computed(
  () =>
    !!selectedSession.value &&
    !loadingTasks.value &&
    !sending.value &&
    !blocked.value &&
    !needsReconcile.value &&
    Array.from(draft.value.trim()).length >= 2,
);
const suggestions = computed(() => {
  const items = ["平台优惠券可以叠加使用吗？"];
  if (auth.user?.role !== "analyst")
    items.push("查询订单 10086 的物流状态。", "订单 10086 是否符合退款条件？");
  if (auth.user?.role === "analyst" || auth.user?.role === "admin")
    items.push("统计最近七天的销售额。");
  return items;
});
function shortId(id: string) {
  return id.slice(0, 8);
}
function querySession(): string | null {
  return typeof route.query.session === "string" ? route.query.session : null;
}

async function loadSessions(selectInitial = false) {
  const generation = ++sidebarRequest;
  loadingSessions.value = true;
  sidebarError.value = "";
  try {
    const page = await api<Page<AgentSession>>(
      `/api/v1/agent/sessions?offset=${(sessionPage.value - 1) * sessionLimit}&limit=${sessionLimit}`,
    );
    if (generation !== sidebarRequest) return;
    sessions.value = page.items;
    sessionsTotal.value = page.total;
    if (selectInitial) {
      const sessionId = querySession() ?? page.items[0]?.id;
      if (sessionId) await selectSession(sessionId, false);
    }
  } catch (failure) {
    if (generation === sidebarRequest)
      sidebarError.value = errorMessage(failure);
  } finally {
    if (generation === sidebarRequest) loadingSessions.value = false;
  }
}

async function selectSession(sessionId: string, updateRoute = true) {
  const generation = ++taskRequest;
  selectingSessionId = sessionId;
  loadingTasks.value = true;
  conversationError.value = "";
  conversationNotice.value = "";
  draftError.value = "";
  selectedSession.value = null;
  currentTask.value = null;
  tasks.value = [];
  tasksTotal.value = 0;
  taskPage.value = 1;
  try {
    const session = await api<AgentSession>(
      `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}`,
    );
    const page = await api<Page<AgentTask>>(
      `/api/v1/agent/tasks?session_id=${encodeURIComponent(session.id)}&offset=0&limit=${taskLimit}`,
    );
    const current = session.current_task_id
      ? (page.items.find((task) => task.id === session.current_task_id) ??
        (await api<AgentTask>(
          `/api/v1/agent/tasks/${encodeURIComponent(session.current_task_id)}`,
        )))
      : null;
    if (generation !== taskRequest) return;
    selectedSession.value = session;
    tasks.value = page.items;
    tasksTotal.value = page.total;
    currentTask.value = current;
    needsReconcile.value = false;
    if (updateRoute && querySession() !== session.id)
      await router.replace({ path: "/agent", query: { session: session.id } });
  } catch (failure) {
    if (generation === taskRequest)
      conversationError.value = errorMessage(failure);
  } finally {
    if (generation === taskRequest) {
      loadingTasks.value = false;
      selectingSessionId = null;
    }
  }
}

async function loadTasks() {
  const selected = selectedSession.value;
  if (!selected) return;
  const generation = ++taskRequest;
  loadingTasks.value = true;
  conversationError.value = "";
  try {
    const [page, session] = await Promise.all([
      api<Page<AgentTask>>(
        `/api/v1/agent/tasks?session_id=${encodeURIComponent(selected.id)}&offset=${(taskPage.value - 1) * taskLimit}&limit=${taskLimit}`,
      ),
      api<AgentSession>(
        `/api/v1/agent/sessions/${encodeURIComponent(selected.id)}`,
      ),
    ]);
    const current = session.current_task_id
      ? (page.items.find((task) => task.id === session.current_task_id) ??
        (await api<AgentTask>(
          `/api/v1/agent/tasks/${encodeURIComponent(session.current_task_id)}`,
        )))
      : null;
    if (generation !== taskRequest || selectedSession.value?.id !== selected.id)
      return;
    selectedSession.value = session;
    tasks.value = page.items;
    tasksTotal.value = page.total;
    currentTask.value = current;
  } catch (failure) {
    if (generation === taskRequest)
      conversationError.value = errorMessage(failure);
  } finally {
    if (generation === taskRequest) loadingTasks.value = false;
  }
}

async function createSession() {
  if (creating.value || sending.value) return;
  const preservePrefill = typeof route.query.message === "string";
  creating.value = true;
  sidebarError.value = "";
  try {
    const session = await api<AgentSession>("/api/v1/agent/sessions", {
      method: "POST",
      body: {},
    });
    sessionPage.value = 1;
    await loadSessions();
    await selectSession(session.id);
    if (!preservePrefill) draft.value = "";
  } catch (failure) {
    sidebarError.value = errorMessage(failure);
  } finally {
    creating.value = false;
  }
}

async function send() {
  const selected = selectedSession.value;
  const message = draft.value.trim();
  draftError.value = "";
  if (
    !selected ||
    sending.value ||
    loadingTasks.value ||
    blocked.value ||
    needsReconcile.value
  )
    return;
  if (Array.from(message).length < 2 || Array.from(message).length > 2000) {
    draftError.value = "请输入 2–2000 个字符。";
    return;
  }
  sending.value = true;
  conversationError.value = "";
  conversationNotice.value = "";
  try {
    // This is a bounded synchronous turn. Never retry a POST automatically.
    const task = await api<AgentTask>(
      `/api/v1/agent/sessions/${encodeURIComponent(selected.id)}/messages`,
      { method: "POST", body: { message } },
    );
    if (selectedSession.value?.id === selected.id) {
      draft.value = "";
      currentTask.value = task;
      selectedSession.value = {
        ...selectedSession.value,
        current_task_id: task.id,
      };
      taskPage.value = 1;
      await loadTasks();
      conversationNotice.value = `本轮任务状态：${statusLabel(task.status)}。`;
    }
    await loadSessions();
  } catch (failure) {
    needsReconcile.value = true;
    conversationError.value = `${errorMessage(failure)}。如请求已到达服务器，请先点击“刷新会话”核对任务；不要立即重复发送。`;
  } finally {
    sending.value = false;
  }
}

function updateTask(updated: AgentTask) {
  tasks.value = tasks.value.map((task) =>
    task.id === updated.id ? updated : task,
  );
  if (currentTask.value?.id === updated.id) currentTask.value = updated;
}
async function refreshConversation() {
  if (sending.value || loadingTasks.value || !selectedSession.value) return;
  await selectSession(selectedSession.value.id, false);
}
async function showLatest() {
  taskPage.value = 1;
  await loadTasks();
}
function fillSuggestion(message: string) {
  draft.value = message;
}

watch(
  () => route.query.session,
  (sessionId) => {
    if (
      typeof sessionId === "string" &&
      sessionId !== selectedSession.value?.id &&
      sessionId !== selectingSessionId
    )
      void selectSession(sessionId, false);
  },
);
watch(
  () => route.query.message,
  (message) => {
    // Navigation from Analytics only fills the composer; submission remains explicit.
    if (typeof message === "string")
      draft.value = Array.from(message).slice(0, 2000).join("");
  },
  { immediate: true },
);
onMounted(() => {
  void loadSessions(true);
});
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">AGENT WORKSPACE</span>
      <h1>Agent 对话</h1>
      <p class="muted">结合企业知识与业务事实处理问题，敏感操作交由你确认。</p>
    </div>
    <el-button
      :disabled="sending"
      :loading="creating"
      type="primary"
      @click="createSession"
      data-testid="new-session"
      >新建对话</el-button
    >
  </div>

  <div class="agent-layout">
    <aside class="panel session-panel">
      <div class="panel-header">
        <strong>我的会话</strong
        ><el-button
          text
          size="small"
          :loading="loadingSessions"
          @click="loadSessions()"
          >刷新</el-button
        >
      </div>
      <el-alert
        v-if="sidebarError"
        :title="sidebarError"
        type="error"
        :closable="false"
        show-icon
      />
      <div v-loading="loadingSessions" class="session-list">
        <button
          v-for="session in sessions"
          :key="session.id"
          class="session-item"
          :class="{ active: selectedSession?.id === session.id }"
          :disabled="sending || creating"
          @click="selectSession(session.id)"
          :aria-pressed="selectedSession?.id === session.id"
        >
          <span class="session-title">会话 {{ shortId(session.id) }}</span>
          <span class="session-date">{{ formatTime(session.updated_at) }}</span>
          <span
            v-if="currentTask && selectedSession?.id === session.id"
            class="session-state"
            >{{ statusLabel(currentTask.status) }}</span
          >
        </button>
        <div
          v-if="!loadingSessions && !sessions.length && !sidebarError"
          class="empty-state"
        >
          <p>还没有持久化会话。</p>
          <p class="muted">点击“新建对话”开始。</p>
        </div>
      </div>
      <el-pagination
        v-if="sessionsTotal > sessionLimit"
        v-model:current-page="sessionPage"
        :page-size="sessionLimit"
        :total="sessionsTotal"
        layout="prev, pager, next"
        small
        :pager-count="5"
        :disabled="sending || creating"
        @current-change="loadSessions()"
        class="session-pagination"
      />
      <p class="sidebar-note muted">
        共 {{ sessionsTotal }} 个会话 · 仅展示当前账号及角色拥有的记录
      </p>
    </aside>

    <section class="panel conversation-panel">
      <div class="panel-header conversation-header">
        <div>
          <strong>{{
            selectedSession
              ? `会话 ${shortId(selectedSession.id)}`
              : "业务智能助手"
          }}</strong>
          <p v-if="selectedSession" class="muted">
            {{ tasksTotal }} 轮已保存任务 · 记录来自服务器
          </p>
        </div>
        <div class="toolbar">
          <el-button
            v-if="selectedSession"
            text
            :disabled="sending"
            :loading="loadingTasks"
            @click="refreshConversation"
            >刷新会话</el-button
          ><router-link
            v-if="selectedSession"
            :to="{ path: '/tasks', query: { session: selectedSession.id } }"
            >执行历史</router-link
          >
        </div>
      </div>
      <div class="conversation-body" v-loading="loadingTasks">
        <el-alert
          v-if="conversationError"
          :title="conversationError"
          type="error"
          :closable="false"
          show-icon
          class="conversation-alert"
        />
        <el-alert
          v-if="conversationNotice"
          :title="conversationNotice"
          type="info"
          :closable="false"
          class="conversation-alert"
        />
        <div v-if="!selectedSession && !loadingTasks" class="chat-welcome">
          <div class="welcome-mark">EA</div>
          <h2>让每一步都有依据</h2>
          <p class="muted">
            查询订单、检索政策、创建待审核申请。<br />选中已有会话，或新建对话开始。
          </p>
          <div class="suggestion-list">
            <button
              v-for="suggestion in suggestions"
              :key="suggestion"
              @click="fillSuggestion(suggestion)"
            >
              {{ suggestion }}<span>↗</span>
            </button>
          </div>
        </div>
        <template v-else-if="selectedSession">
          <div v-if="!tasks.length && !loadingTasks" class="chat-welcome">
            <div class="welcome-mark">EA</div>
            <h2>这个会话已准备好</h2>
            <p class="muted">
              提交问题后，执行步骤、真实引用与任务状态会显示在这里。
            </p>
            <div class="suggestion-list">
              <button
                v-for="suggestion in suggestions"
                :key="suggestion"
                @click="fillSuggestion(suggestion)"
              >
                {{ suggestion }}<span>↗</span>
              </button>
            </div>
          </div>
          <article
            v-for="task in orderedTasks"
            :key="task.id"
            class="conversation-turn"
          >
            <div class="user-message">
              <span>你</span>
              <p>{{ task.message }}</p>
            </div>
            <div class="assistant-message">
              <div class="assistant-label">ENTERPRISE AGENT</div>
              <TaskDetail :task="task" compact @updated="updateTask" />
            </div>
          </article>
          <el-pagination
            v-if="tasksTotal > taskLimit"
            v-model:current-page="taskPage"
            :page-size="taskLimit"
            :total="tasksTotal"
            layout="prev, pager, next"
            :disabled="sending"
            @current-change="loadTasks"
            class="task-pagination"
          />
          <p v-if="tasksTotal > taskLimit" class="muted pagination-note">
            第 1 页显示最新任务，每页按对话时间正序展示。
          </p>
        </template>
        <div v-if="sending" class="pending-message" role="status">
          <span class="pending-dot"></span
          >正在等待服务器完成本轮工作流。请保持页面，长任务不会自动重复提交。
        </div>
      </div>

      <div class="composer">
        <div v-if="needsReconcile" class="blocked-note">
          上次提交结果尚未核对，请点击“刷新会话”读取服务器任务后再发送。
        </div>
        <div v-if="blocked" class="blocked-note">
          <span
            >当前任务{{
              currentTask ? statusLabel(currentTask.status) : ""
            }}，请先核对或处理该任务。</span
          ><el-button
            v-if="taskPage !== 1"
            text
            size="small"
            @click="showLatest"
            >查看最新任务</el-button
          >
        </div>
        <label for="agent-message" class="composer-label">业务问题</label>
        <el-input
          id="agent-message"
          v-model="draft"
          type="textarea"
          :rows="3"
          maxlength="2000"
          show-word-limit
          resize="none"
          :disabled="
            !selectedSession ||
            sending ||
            loadingTasks ||
            blocked ||
            needsReconcile
          "
          placeholder="输入订单、企业政策或运营问题…"
          @keydown.ctrl.enter.prevent="send"
          data-testid="agent-message"
        />
        <p v-if="draftError" class="draft-error">{{ draftError }}</p>
        <div class="composer-bottom">
          <span class="muted">Ctrl + Enter 发送 · 提交后会调用已配置模型</span
          ><el-button
            type="primary"
            :disabled="!canSend"
            :loading="sending"
            @click="send"
            data-testid="send-message"
            >发送问题</el-button
          >
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.agent-layout {
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.session-panel {
  padding: 0;
  overflow: hidden;
}
.session-panel .panel-header {
  padding: 15px 18px;
  border-bottom: 1px solid var(--color-border, #e2e7df);
}
.session-panel > .el-alert {
  margin: 12px;
  width: auto;
}
.session-list {
  min-height: 140px;
  padding: 8px;
}
.session-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 9px;
  text-align: left;
  padding: 14px 12px;
  margin-bottom: 4px;
  cursor: pointer;
  color: inherit;
  font-family: inherit;
}
.session-item:hover {
  background: #f5f7f1;
}
.session-item.active {
  background: #edf3e9;
  border-color: #d3e0ca;
}
.session-item:disabled {
  cursor: default;
}
.session-title {
  font-size: 13px;
  font-weight: 550;
}
.session-date {
  color: var(--color-muted, #7b8579);
  font-size: 10px;
}
.session-state {
  font-size: 11px;
  color: #477455;
}
.session-pagination {
  justify-content: center;
  margin: 12px 4px;
}
.sidebar-note {
  padding: 4px 17px 16px;
  margin: 0;
  font-size: 10px;
  line-height: 1.8;
}
.conversation-panel {
  padding: 0;
  overflow: hidden;
  min-height: 680px;
}
.conversation-header {
  padding: 16px 24px;
  border-bottom: 1px solid var(--color-border, #e2e7df);
}
.conversation-header p {
  font-size: 11px;
  margin: 7px 0 0;
}
.conversation-header a {
  font-size: 12px;
  color: var(--color-primary, #245b48);
  text-decoration: none;
}
.conversation-body {
  min-height: 415px;
  max-height: 72vh;
  overflow-y: auto;
  padding: 18px 24px 24px;
}
.conversation-alert {
  margin-bottom: 16px;
}
.chat-welcome {
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  min-height: 365px;
  padding: 24px 0;
  text-align: center;
}
.welcome-mark {
  width: 48px;
  height: 48px;
  background: #e9f0e2;
  border: 1px solid #d6e2cb;
  color: #2c5b41;
  display: grid;
  place-items: center;
  border-radius: 13px;
  font-family: Georgia, serif;
  font-size: 18px;
}
.chat-welcome h2 {
  font-size: 21px;
  font-weight: 550;
  margin: 18px 0 6px;
}
.chat-welcome > p {
  line-height: 1.9;
  font-size: 12px;
}
.suggestion-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
  width: min(620px, 100%);
  margin-top: 14px;
}
.suggestion-list button {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  border: 1px solid #e0e6d9;
  border-radius: 8px;
  padding: 12px 13px;
  background: #fbfcf8;
  text-align: left;
  color: #566451;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
  line-height: 1.7;
}
.suggestion-list button:hover {
  border-color: #94b48a;
  background: #f2f7ed;
}
.suggestion-list span {
  color: #7b9770;
  font-size: 14px;
}
.conversation-turn {
  margin-bottom: 28px;
}
.user-message {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 7px;
  margin: 6px 0 24px;
}
.user-message > span {
  font-size: 11px;
  color: #73806b;
}
.user-message p {
  margin: 0;
  max-width: 90%;
  padding: 13px 17px;
  background: #edf3e8;
  border-radius: 11px 11px 2px 11px;
  color: #304c34;
  font-size: 13px;
  line-height: 1.85;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.assistant-label {
  font-size: 10px;
  color: #6b8562;
  letter-spacing: 1.2px;
  margin: 0 0 14px;
  font-weight: 550;
}
.assistant-message {
  padding-bottom: 18px;
  border-bottom: 1px solid #edf0e8;
}
.task-pagination {
  justify-content: center;
  margin-top: 16px;
}
.pagination-note {
  font-size: 10px;
  text-align: center;
}
.pending-message {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12px;
  color: #596e50;
  padding: 16px 0;
  line-height: 1.8;
}
.pending-dot {
  width: 7px;
  height: 7px;
  background: #64915a;
  border-radius: 50%;
  flex-shrink: 0;
}
.composer {
  padding: 16px 24px 20px;
  background: #fbfcf8;
  border-top: 1px solid var(--color-border, #e2e7df);
}
.composer-label {
  display: block;
  font-size: 11px;
  color: #60725a;
  margin-bottom: 9px;
}
.composer-bottom {
  margin-top: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.composer-bottom > span {
  font-size: 10px;
  line-height: 1.8;
}
.blocked-note {
  font-size: 12px;
  color: #986928;
  margin-bottom: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.draft-error {
  font-size: 11px;
  color: #b04e47;
  margin: 8px 0 0;
}
@media (max-width: 1100px) {
  .agent-layout {
    grid-template-columns: 208px minmax(0, 1fr);
    gap: 14px;
  }
  .conversation-body,
  .composer,
  .conversation-header {
    padding-left: 18px;
    padding-right: 18px;
  }
}
@media (max-width: 800px) {
  .agent-layout {
    grid-template-columns: 1fr;
  }
  .session-list {
    display: flex;
    overflow-x: auto;
    min-height: 0;
  }
  .session-item {
    min-width: 190px;
    width: auto;
  }
  .suggestion-list {
    grid-template-columns: 1fr;
  }
  .conversation-header {
    flex-wrap: wrap;
  }
}
</style>
