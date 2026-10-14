import { createRouter, createWebHistory } from "vue-router";
import { auth } from "./stores/auth";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/agent" },
    {
      path: "/login",
      component: () => import("./views/LoginView.vue"),
      meta: { title: "登录" },
    },
    {
      path: "/agent",
      component: () => import("./views/AgentView.vue"),
      meta: { title: "Agent 对话" },
    },
    {
      path: "/orders",
      component: () => import("./views/OrdersView.vue"),
      meta: { title: "订单与售后" },
    },
    {
      path: "/knowledge",
      component: () => import("./views/KnowledgeView.vue"),
      meta: { title: "企业知识库" },
    },
    {
      path: "/analytics",
      component: () => import("./views/AnalyticsView.vue"),
      meta: { title: "运营数据分析" },
    },
    {
      path: "/tasks",
      component: () => import("./views/TasksView.vue"),
      meta: { title: "任务历史" },
    },
    { path: "/:pathMatch(.*)*", redirect: "/agent" },
  ],
});
router.beforeEach((to) => {
  if (!auth.user && to.path !== "/login")
    return { path: "/login", query: { next: to.path } };
  if (auth.user && to.path === "/login") return "/agent";
});
