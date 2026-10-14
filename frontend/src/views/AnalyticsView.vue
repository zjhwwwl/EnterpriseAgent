<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  ChartColumn,
  Play,
  ArrowRight,
  ShieldCheck,
  Database,
  RefreshCw,
} from "@lucide/vue";
import { api, errorMessage } from "../api/client";
import type { AnalyticsCatalog, AnalyticsReport } from "../api/types";
import { auth } from "../stores/auth";
import { router } from "../router";
import AnalyticsChart from "../components/AnalyticsChart.vue";
import MarkdownContent from "../components/MarkdownContent.vue";

const permitted = computed(() =>
  ["analyst", "admin"].includes(auth.user?.role ?? ""),
);
const catalog = ref<AnalyticsCatalog | null>(null);
const result = ref<AnalyticsReport | null>(null);
const sql = ref("");
const metric = ref("paid_sales");
const active = ref("sales");
const natural = ref("");
const loading = ref(false);
const catalogBusy = ref(false);
const error = ref("");
const presets = [
  {
    id: "sales",
    label: "最近七天销售额",
    detail: "含今天 · 支付日期",
    metric: "paid_sales",
  },
  {
    id: "refund",
    label: "本月退款率",
    detail: "已支付订单群 · 模拟完成",
    metric: "refund_rate",
  },
  {
    id: "weeks",
    label: "两周订单量对比",
    detail: "两个完整七天周期",
    metric: "order_count",
  },
  {
    id: "products",
    label: "商品销售排行",
    detail: "最近七天 · 前五名",
    metric: "product_sales",
  },
];
const metricLabels: Record<string, string> = {
  paid_sales: "支付销售额",
  refund_rate: "完成退款率",
  order_count: "已支付订单量",
  product_sales: "商品实付销售额",
};
function presetQuery(id: string) {
  if (!catalog.value) return "";
  const windows = catalog.value.date_windows;
  const range = (name: string) =>
    `business_date >= DATE '${windows[name]!.start_inclusive}' AND business_date < DATE '${windows[name]!.end_exclusive}'`;
  if (id === "refund")
    return `SELECT ROUND(SUM(completed_refund_order_count) * 100.0 / NULLIF(SUM(paid_order_count), 0), 2) AS refund_rate_percent\nFROM ea_analytics.daily_orders\nWHERE ${range("this_month_to_date")}`;
  if (id === "weeks")
    return `SELECT CASE WHEN business_date < DATE '${windows.latest_complete_week!.start_inclusive}' THEN '上周' ELSE '本周' END AS period, SUM(paid_order_count) AS order_count\nFROM ea_analytics.daily_orders\nWHERE business_date >= DATE '${windows.previous_complete_week!.start_inclusive}' AND business_date < DATE '${windows.latest_complete_week!.end_exclusive}'\nGROUP BY period\nORDER BY period`;
  if (id === "products")
    return `SELECT product_id, product_name, SUM(goods_revenue_cents) AS goods_revenue_cents\nFROM ea_analytics.daily_product_sales\nWHERE ${range("last_seven_days_including_today")}\nGROUP BY product_id, product_name\nORDER BY goods_revenue_cents DESC, product_id, product_name\nLIMIT 5`;
  return `SELECT business_date, gross_revenue_cents\nFROM ea_analytics.daily_orders\nWHERE ${range("last_seven_days_including_today")}\nORDER BY business_date`;
}
function choose(id: string) {
  const preset = presets.find((item) => item.id === id);
  if (!preset || loading.value) return;
  active.value = id;
  metric.value = preset.metric;
  sql.value = presetQuery(id);
  result.value = null;
  error.value = "";
}
async function loadCatalog() {
  if (!permitted.value || catalogBusy.value) return;
  catalogBusy.value = true;
  error.value = "";
  try {
    catalog.value = await api<AnalyticsCatalog>("/api/v1/analytics/catalog");
    choose(active.value);
  } catch (cause) {
    error.value = errorMessage(cause);
  } finally {
    catalogBusy.value = false;
  }
}
async function execute() {
  if (loading.value || !sql.value.trim()) return;
  loading.value = true;
  error.value = "";
  result.value = null;
  try {
    result.value = await api<AnalyticsReport>("/api/v1/analytics/query", {
      method: "POST",
      body: { sql: sql.value.trim(), metric_ids: [metric.value] },
    });
  } catch (cause) {
    error.value = errorMessage(cause);
  } finally {
    loading.value = false;
  }
}
function askAgent() {
  if (natural.value.trim().length >= 2)
    void router.push({
      path: "/agent",
      query: { message: natural.value.trim() },
    });
}
onMounted(() => void loadCatalog());
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">BUSINESS INTELLIGENCE</span>
      <h1>运营数据分析</h1>
      <p>核对指标口径，查询真实模拟数据，让结论有据可依。</p>
    </div>
    <el-tag type="success" effect="plain"
      ><ShieldCheck
        :size="12"
        style="vertical-align: middle; margin-right: 5px"
      />只读分析</el-tag
    >
  </div>
  <el-alert
    v-if="!permitted"
    title="当前身份不能访问运营指标"
    description="请使用运营分析员或管理员账号。分析权限不授予原始订单明细访问权。"
    type="warning"
    :closable="false"
    show-icon
  />
  <template v-else>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <section class="panel">
      <div class="panel-header">
        <h3>用自然语言发起分析</h3>
        <span class="muted">由 Agent 规划与执行</span>
      </div>
      <div class="toolbar" style="margin-bottom: 0">
        <el-input
          v-model="natural"
          maxlength="2000"
          aria-label="分析问题"
          placeholder="例如：统计最近七天的销售额"
          @keyup.enter="askAgent"
          style="flex: 1"
        /><el-button :disabled="natural.trim().length < 2" @click="askAgent"
          >前往 Agent 对话<ArrowRight :size="14" style="margin-left: 7px"
        /></el-button>
      </div>
      <p class="chart-caption">
        进入对话后可核对问题并显式发送，页面不会自动调用聊天模型。
      </p>
    </section>
    <div v-loading="catalogBusy" class="query-presets">
      <button
        v-for="preset in presets"
        :key="preset.id"
        class="query-preset"
        :class="{ active: active === preset.id }"
        :disabled="!catalog || loading"
        @click="choose(preset.id)"
      >
        <small
          >0{{ presets.indexOf(preset) + 1 }} /
          {{ metricLabels[preset.metric] }}</small
        ><strong>{{ preset.label }}</strong
        ><span>{{ preset.detail }}</span>
      </button>
    </div>
    <div class="split-panel">
      <div>
        <section class="panel">
          <div class="panel-header">
            <h3>查询工作区</h3>
            <el-tag size="small" effect="plain">授权聚合视图</el-tag>
          </div>
          <div class="toolbar">
            <span class="section-description">指标口径</span
            ><el-select
              v-model="metric"
              aria-label="指标口径"
              style="width: 190px"
              ><el-option
                v-for="(label, key) in metricLabels"
                :key="key"
                :label="label"
                :value="key"
            /></el-select>
          </div>
          <el-input
            v-model="sql"
            class="sql-editor"
            type="textarea"
            :rows="8"
            :maxlength="8000"
            aria-label="只读 SQL"
            placeholder="从上方选择查询模板，或输入受限 SELECT"
            :disabled="loading"
          />
          <div class="sql-actions">
            <span class="readonly-note"
              ><ShieldCheck :size="13" />只允许授权视图上的受限 SELECT</span
            ><el-button
              type="primary"
              :loading="loading"
              :disabled="!catalog || !sql.trim()"
              @click="execute"
              ><Play
                v-if="!loading"
                :size="13"
                style="margin-right: 7px"
              />执行查询</el-button
            >
          </div>
        </section>
        <section class="panel" v-loading="loading">
          <div class="panel-header">
            <h3>查询结果</h3>
            <span v-if="result" class="muted"
              >{{ result.query.elapsed_ms }} ms · 数据库执行</span
            >
          </div>
          <div v-if="!result" class="empty-state">
            <ChartColumn
              :size="34"
              stroke-width="1.2"
              style="margin-bottom: 12px"
            />
            <p>执行查询后展示数据、图表与分析报告</p>
            <span class="muted">这里的数值来自实际查询，不预填演示结果。</span>
          </div>
          <template v-else
            ><div class="analysis-summary">
              <el-tag type="success" size="small"
                >{{ result.query.row_count }} 行</el-tag
              ><span>金额原始值以人民币分计，比例口径见 SQL</span>
            </div>
            <el-alert
              v-if="result.query.truncated"
              title="结果已截断：不能据此计算完整总量。"
              type="warning"
              :closable="false" /><el-table
              :data="result.query.rows"
              empty-text="所选条件没有结果"
              max-height="410"
              ><el-table-column
                v-for="(column, index) in result.query.columns"
                :key="column"
                :label="column"
                :min-width="140"
                ><template #default="scope"
                  ><span class="data-code">{{
                    scope.row[index] === null
                      ? "NULL"
                      : String(scope.row[index])
                  }}</span></template
                ></el-table-column
              ></el-table
            ><AnalyticsChart :chart="result.chart" /><el-collapse
              style="margin-top: 20px"
              ><el-collapse-item
                title="查看分析报告、指标口径与执行 SQL"
                name="report"
                ><MarkdownContent
                  :content="result.answer" /></el-collapse-item></el-collapse
          ></template>
        </section>
      </div>
      <aside>
        <section class="panel">
          <div class="panel-header">
            <h3>指标口径</h3>
            <ShieldCheck :size="16" color="#8d9f77" />
          </div>
          <ul class="definition-list">
            <li
              v-for="(definition, key) in catalog?.metric_definitions"
              :key="key"
            >
              <strong>{{ metricLabels[key] ?? key }}</strong
              >{{ definition }}
            </li>
          </ul>
          <p class="chart-caption">结果反映业务记录，不能据此推断变化原因。</p>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h3>可用数据集</h3>
            <Database :size="15" color="#8d9f77" />
          </div>
          <div
            v-for="(columns, view) in catalog?.views"
            :key="view"
            class="schema-entry"
          >
            <code>ea_analytics.{{ view }}</code>
            <p>{{ Object.keys(columns).join(" · ") }}</p>
          </div>
          <p class="chart-caption">
            日期按 Asia/Shanghai 计算，时间范围采用左闭右开。
          </p>
          <el-button
            size="small"
            text
            :loading="catalogBusy"
            @click="loadCatalog"
            ><RefreshCw
              :size="12"
              style="margin-right: 6px"
            />刷新日期与指标目录</el-button
          >
        </section>
      </aside>
    </div>
  </template>
</template>
