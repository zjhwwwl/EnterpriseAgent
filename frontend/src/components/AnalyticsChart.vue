<script setup lang="ts">
import { computed } from "vue";
import type { ChartSpec, Scalar } from "../api/types";

const props = defineProps<{ chart?: unknown }>();
const colors = [
  "#62855d",
  "#bc9a5f",
  "#5e94a1",
  "#a07b91",
  "#8fa352",
  "#7e94bc",
];
const spec = computed<ChartSpec | null>(() => {
  const input = props.chart as Partial<ChartSpec> | null;
  if (
    !input ||
    !["line", "bar"].includes(String(input.type)) ||
    input.source !== "query_rows" ||
    !Array.isArray(input.labels) ||
    !input.labels.every((label) => typeof label === "string") ||
    !Array.isArray(input.series) ||
    !input.series.length
  )
    return null;
  if (
    !input.series.every(
      (series) =>
        series !== null &&
        typeof series === "object" &&
        typeof series.name === "string" &&
        Array.isArray(series.data) &&
        series.data.length === input.labels!.length &&
        series.data.every(
          (value) =>
            value === null ||
            typeof value === "string" ||
            (typeof value === "number" &&
              Number.isFinite(value) &&
              (!Number.isInteger(value) || Number.isSafeInteger(value))),
        ),
    )
  )
    return null;
  return input as ChartSpec;
});
function decimal(value: Scalar): { digits: bigint; scale: number } | null {
  if (
    value === null ||
    typeof value === "boolean" ||
    (typeof value === "number" && !Number.isFinite(value))
  )
    return null;
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value));
  if (!match) return null;
  return {
    digits: BigInt(`${match[1]}${match[2]}${match[3] ?? ""}`),
    scale: match[3]?.length ?? 0,
  };
}
const visibleLabels = computed(() => spec.value?.labels.slice(0, 30) ?? []);
const plotted = computed(() => {
  const all =
    spec.value?.series.map((series) => series.data.slice(0, 30).map(decimal)) ??
    [];
  const scale = Math.max(0, ...all.flat().map((value) => value?.scale ?? 0));
  const units = all.map((series) =>
    series.map((value) =>
      value ? value.digits * 10n ** BigInt(scale - value.scale) : null,
    ),
  );
  const max = units.flat().reduce<bigint>((largest, value) => {
    const abs = value === null ? 0n : value < 0n ? -value : value;
    return abs > largest ? abs : largest;
  }, 0n);
  const negative = units.flat().some((value) => value !== null && value < 0n);
  const baseline = negative ? 138 : 239;
  const height = negative ? 97 : 198;
  return {
    baseline,
    negative,
    values: units.map((series) =>
      series.map((value) =>
        value === null
          ? null
          : max === 0n
            ? 0
            : (Number((value * 10_000n) / max) / 10_000) * height,
      ),
    ),
  };
});
const slot = computed(() => 812 / Math.max(1, visibleLabels.value.length));
function x(index: number) {
  return 44 + slot.value * (index + 0.5);
}
function segments(values: (number | null)[]): string[] {
  const output: string[] = [];
  let segment: string[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (segment.length) output.push(segment.join(" "));
      segment = [];
    } else segment.push(`${x(index)},${plotted.value.baseline - value}`);
  });
  if (segment.length) output.push(segment.join(" "));
  return output;
}
function barX(index: number, series: number) {
  return (
    44 +
    slot.value * index +
    slot.value * 0.15 +
    (series * slot.value * 0.7) / Math.max(1, spec.value?.series.length ?? 1)
  );
}
function tooltip(series: number, row: number) {
  return `${spec.value?.series[series]?.name} · ${visibleLabels.value[row]}：${spec.value?.series[series]?.data[row] ?? "NULL"}`;
}
</script>

<template>
  <div
    v-if="spec && visibleLabels.length"
    class="chart-panel"
    data-testid="analytics-chart"
  >
    <div class="chart-legend">
      <span
        v-for="(series, index) in spec.series"
        :key="`${series.name}:${index}`"
        ><i :style="{ background: colors[index % colors.length] }" />{{
          series.name
        }}</span
      >
    </div>
    <svg
      viewBox="0 0 900 290"
      class="chart-svg"
      role="img"
      aria-label="运营查询结果图表"
    >
      <line
        v-for="y in [40, 90, 140, 190, 240]"
        :key="y"
        x1="38"
        :y1="y"
        x2="864"
        :y2="y"
        stroke="#e9eedf"
        stroke-dasharray="3 4"
      />
      <line
        x1="38"
        :y1="plotted.baseline"
        x2="864"
        :y2="plotted.baseline"
        stroke="#bdcbb0"
      />
      <g v-for="(values, seriesIndex) in plotted.values" :key="seriesIndex">
        <template v-if="spec.type === 'line'">
          <polyline
            v-for="(points, index) in segments(values)"
            :key="index"
            :points="points"
            fill="none"
            :stroke="colors[seriesIndex % colors.length]"
            stroke-width="2.3"
          />
          <template v-for="(value, row) in values" :key="row">
            <circle
              v-if="value !== null"
              :cx="x(row)"
              :cy="plotted.baseline - value"
              r="3"
              :fill="colors[seriesIndex % colors.length]"
            >
              <title>{{ tooltip(seriesIndex, row) }}</title>
            </circle>
          </template>
        </template>
        <template v-else>
          <template v-for="(value, row) in values" :key="row">
            <rect
              v-if="value !== null"
              :x="barX(row, seriesIndex)"
              :y="Math.min(plotted.baseline, plotted.baseline - value)"
              :width="Math.max(0.8, (slot * 0.7) / spec.series.length - 2)"
              :height="Math.max(value === 0 ? 1 : 0, Math.abs(value))"
              rx="2"
              :fill="colors[seriesIndex % colors.length]"
            >
              <title>{{ tooltip(seriesIndex, row) }}</title>
            </rect>
          </template>
        </template>
      </g>
      <template v-for="(label, index) in visibleLabels" :key="index">
        <text
          v-if="
            index % Math.max(1, Math.ceil(visibleLabels.length / 7)) === 0 ||
            index === visibleLabels.length - 1
          "
          :x="x(index)"
          y="267"
          text-anchor="middle"
          font-size="10"
          fill="#90a17c"
        >
          {{ label.length > 11 ? `${label.slice(0, 10)}…` : label }}
        </text>
      </template>
    </svg>
    <p class="chart-caption">
      图形按原始数值比例展示，精确值保留在结果表中。{{
        spec.labels.length > 30
          ? "图表仅展示前 30 行，完整受限结果见表格。"
          : "悬停图形可查看原始值；NULL 不绘制为零。"
      }}
    </p>
  </div>
  <div v-else class="empty-state">没有可绘制的结果，原始查询值以表格为准。</div>
</template>
