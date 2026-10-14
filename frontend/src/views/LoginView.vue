<script setup lang="ts">
import { ref } from "vue";
import { useRoute } from "vue-router";
import {
  ArrowRight,
  BookOpen,
  Package,
  ChartColumn,
  ShieldCheck,
} from "@lucide/vue";
import { auth, login } from "../stores/auth";
import { errorMessage } from "../api/client";
import { router } from "../router";

const route = useRoute();
const username = ref("demo_customer");
const password = ref("DemoOnly!2026");
const busy = ref(false);
const error = ref("");
const accounts = [
  {
    value: "demo_customer",
    label: "模拟客户",
    description: "自己的订单、售后申请与公开知识",
  },
  {
    value: "demo_support",
    label: "售后客服",
    description: "查询订单、审核申请与内部知识",
  },
  {
    value: "demo_analyst",
    label: "运营分析员",
    description: "授权运营指标、分析报告与图表",
  },
  {
    value: "demo_admin",
    label: "管理员",
    description: "知识维护、售后审核与运营分析",
  },
  {
    value: "demo_other",
    label: "另一位模拟客户",
    description: "用于验证跨客户资源拒绝访问",
  },
];
async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await login(username.value.trim(), password.value);
    password.value = "";
    const next =
      typeof route.query.next === "string" &&
      ["/agent", "/orders", "/knowledge", "/analytics", "/tasks"].includes(
        route.query.next,
      )
        ? route.query.next
        : "/agent";
    await router.replace(next);
  } catch (cause) {
    error.value = errorMessage(cause);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="login-page">
    <section class="login-story">
      <div class="brand">
        <span class="brand-mark">E<span>·</span>A</span
        ><span class="brand-name"
          >EnterpriseAgent<small>电商智能业务助手</small></span
        >
      </div>
      <div class="story-copy">
        <span class="story-kicker">CONNECTED BUSINESS WORKSPACE</span>
        <h1>让每一次业务问答，<br /><em>有据可查。</em></h1>
        <p>
          连接知识、订单与运营数据。<br />在一个工作空间，完成查询、分析和业务协作。
        </p>
        <div class="story-capabilities">
          <span><BookOpen :size="18" />知识检索</span
          ><span><Package :size="18" />订单售后</span
          ><span><ChartColumn :size="18" />运营分析</span>
        </div>
        <div class="flow-illustration">
          <span>提出任务</span><i /><span>核对证据</span><i /><span
            >确认执行</span
          >
        </div>
      </div>
      <div class="story-footer">
        <ShieldCheck :size="16" />模拟业务数据 · 权限校验 · 敏感操作人工确认
      </div>
    </section>
    <section class="login-form-pane">
      <div class="login-form">
        <span class="eyebrow">WELCOME BACK</span>
        <h2>进入业务工作台</h2>
        <p class="muted">选择一个模拟身份，开始体验完整业务流程。</p>
        <el-alert
          v-if="auth.expired"
          title="登录已到期，请重新登录。"
          type="warning"
          :closable="false"
          show-icon
        /><el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          show-icon
        />
        <form @submit.prevent="submit">
          <label class="field-label" for="login-account">演示账号</label
          ><el-select
            id="login-account"
            v-model="username"
            aria-label="演示账号"
            size="large"
            class="full-width"
            ><el-option
              v-for="account in accounts"
              :key="account.value"
              :value="account.value"
              :label="`${account.label} · ${account.value}`"
          /></el-select>
          <p class="account-description">
            {{
              accounts.find((account) => account.value === username)
                ?.description
            }}
          </p>
          <label class="field-label" for="login-password">密码</label
          ><el-input
            id="login-password"
            v-model="password"
            type="password"
            show-password
            autocomplete="current-password"
            aria-label="密码"
            size="large"
            maxlength="256"
          /><el-button
            native-type="submit"
            type="primary"
            size="large"
            :loading="busy"
            class="login-submit"
            >进入工作台<ArrowRight v-if="!busy" :size="17"
          /></el-button>
        </form>
        <div class="demo-hint">
          <ShieldCheck :size="17" />
          <p>
            这里是本地模拟演示环境。<br />公开演示密码：<code
              >DemoOnly!2026</code
            >
          </p>
        </div>
        <p class="login-session-note">
          登录凭证保存在当前页面内存中，刷新后需要重新登录。
        </p>
      </div>
      <small class="login-copyright"
        >EnterpriseAgent · 开源电商 Agent 项目</small
      >
    </section>
  </div>
</template>
