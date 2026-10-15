<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import {
  Bot,
  Package,
  BookOpen,
  ChartColumn,
  History,
  LogOut,
  PanelLeft,
  ArrowUpRight,
  RefreshCw,
} from "@lucide/vue";
import { router } from "./router";
import { auth, clearAuth } from "./stores/auth";
import { api } from "./api/client";
import { roleLabels } from "./utils/format";

const route = useRoute();
const collapsed = ref(false);
const ready = ref<boolean | null>(null);
const version = ref("");
const navigation = [
  { path: "/agent", label: "Agent 对话", icon: Bot, detail: "问答与业务执行" },
  {
    path: "/orders",
    label: "订单与售后",
    icon: Package,
    detail: "订单、物流与退款",
  },
  {
    path: "/knowledge",
    label: "企业知识库",
    icon: BookOpen,
    detail: "资料与可追溯证据",
  },
  {
    path: "/analytics",
    label: "运营数据分析",
    icon: ChartColumn,
    detail: "指标、查询与图表",
  },
  {
    path: "/tasks",
    label: "任务历史",
    icon: History,
    detail: "执行记录与审批",
  },
];
const title = computed(() => String(route.meta.title ?? "业务工作台"));
async function checkHealth() {
  try {
    const result = await api<{ status: string; version: string }>(
      "/health/ready",
    );
    ready.value = result.status === "ok";
    version.value = result.version;
  } catch {
    ready.value = false;
  }
}
let healthTimer: ReturnType<typeof setInterval>;
onMounted(() => {
  void checkHealth();
  healthTimer = setInterval(() => void checkHealth(), 30_000);
});
onUnmounted(() => clearInterval(healthTimer));
watch(
  () => auth.user,
  (user) => {
    if (!user) {
      ElMessage.closeAll();
      if (route.path !== "/login") void router.replace("/login");
    }
  },
);
watch(
  title,
  (value) => {
    document.title = `${value} · EnterpriseAgent`;
  },
  { immediate: true },
);
function logout() {
  clearAuth();
  void router.replace("/login");
}
</script>

<template>
  <RouterView v-if="!auth.user" />
  <div v-else class="workspace" :class="{ 'nav-collapsed': collapsed }">
    <aside class="sidebar">
      <RouterLink class="brand" to="/agent"
        ><span class="brand-mark">E<span>·</span>A</span
        ><span class="brand-name"
          >EnterpriseAgent<small>电商业务工作台</small></span
        ></RouterLink
      >
      <div class="nav-caption">WORKSPACE <span>工作空间</span></div>
      <nav aria-label="主要导航">
        <RouterLink
          v-for="item in navigation"
          :key="item.path"
          :to="item.path"
          class="nav-link"
          :title="item.label"
        >
          <component :is="item.icon" :size="19" /><span
            >{{ item.label }}<small>{{ item.detail }}</small></span
          ><span class="nav-dot" />
        </RouterLink>
      </nav>
      <div class="sidebar-note">
        <span class="tiny-dot" />模拟业务 · 本地演示
        <p>每个答案有来源<br />每次执行可追溯</p>
        <span class="version-label"
          >EnterpriseAgent {{ version || "0.9.0" }}</span
        >
      </div>
    </aside>
    <div class="workspace-body">
      <header class="topbar">
        <div class="breadcrumb">
          <button
            class="icon-button"
            aria-label="折叠导航"
            @click="collapsed = !collapsed"
          >
            <PanelLeft :size="18" /></button
          ><span>工作台</span><span class="breadcrumb-slash">/</span
          ><strong>{{ title }}</strong>
        </div>
        <div class="topbar-actions">
          <button
            class="health-chip"
            @click="checkHealth"
            :title="ready ? '点击重新检查服务' : '服务依赖可能未就绪，点击重试'"
          >
            <span class="tiny-dot" :class="{ offline: ready === false }" />{{
              ready === null
                ? "检查服务中"
                : ready
                  ? "服务已就绪"
                  : "服务未就绪"
            }}<RefreshCw :size="12" /></button
          ><span class="user-avatar">{{
            auth.user.username.replace("demo_", "").slice(0, 1).toUpperCase()
          }}</span>
          <div class="user-identity">
            <strong>{{ auth.user.username }}</strong
            ><small>{{ roleLabels[auth.user.role] }}</small>
          </div>
          <button
            class="icon-button"
            aria-label="退出登录"
            title="退出登录"
            @click="logout"
          >
            <LogOut :size="17" />
          </button>
        </div>
      </header>
      <main class="page-content">
        <RouterView :key="`${auth.user.id}:${auth.user.role}:${route.path}`" />
      </main>
      <footer class="workspace-footer">
        <span>EnterpriseAgent · 所有订单与客户数据均为模拟数据</span
        ><a href="/health/ready" target="_blank" rel="noopener"
          >服务状态<ArrowUpRight :size="12"
        /></a>
      </footer>
    </div>
  </div>
</template>
