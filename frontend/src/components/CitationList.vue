<script setup lang="ts">
import { computed, ref } from "vue";
import { api, errorMessage } from "../api/client";
import type { Citation } from "../api/agent-types";
import DocumentPreview from "./DocumentPreview.vue";

const props = defineProps<{ citations: Citation[] }>();
const uniqueCitations = computed(() => [
  ...new Map(
    props.citations.map((citation) => [citation.chunk_id, citation]),
  ).values(),
]);
const selected = ref<Citation | null>(null);
const verified = ref<Citation | null>(null);
const busy = ref(false);
const error = ref("");
const showDialog = ref(false);
const previewDocument = ref<{ id: string; title: string } | null>(null);
let requestGeneration = 0;

function locationText(location: Record<string, unknown>): string {
  const labels: Record<string, string> = {
    page: "页",
    page_number: "页",
    paragraph: "段落",
    paragraph_index: "段落",
    start_line: "起始行",
    end_line: "结束行",
    line_start: "起始行",
    line_end: "结束行",
    heading: "章节",
  };
  return (
    Object.entries(location)
      .map(
        ([key, value]) =>
          `${labels[key] ?? key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`,
      )
      .join(" · ") || "文档文本片段"
  );
}

async function verify(citation: Citation) {
  const generation = ++requestGeneration;
  selected.value = citation;
  verified.value = null;
  error.value = "";
  busy.value = true;
  showDialog.value = true;
  try {
    // Derive the trusted API route from the chunk ID; never navigate a model URL.
    const current = await api<Citation>(
      `/api/v1/knowledge/citations/${encodeURIComponent(citation.chunk_id)}`,
    );
    if (generation === requestGeneration) verified.value = current;
  } catch (failure) {
    if (generation === requestGeneration) error.value = errorMessage(failure);
  } finally {
    if (generation === requestGeneration) busy.value = false;
  }
}

const changed = computed(
  () =>
    !!selected.value &&
    !!verified.value &&
    (selected.value.document_id !== verified.value.document_id ||
      selected.value.document_version !== verified.value.document_version ||
      selected.value.index_generation !== verified.value.index_generation ||
      selected.value.text !== verified.value.text),
);
</script>

<template>
  <section
    v-if="uniqueCitations.length"
    class="citation-list"
    aria-label="知识来源"
  >
    <h4>
      知识来源
      <span class="muted">{{ uniqueCitations.length }} 条文档出处</span>
    </h4>
    <article
      v-for="citation in uniqueCitations"
      :key="citation.chunk_id"
      class="citation-card"
    >
      <div class="citation-topline">
        <el-button
          link
          type="primary"
          class="citation-title"
          @click="
            previewDocument = {
              id: citation.document_id,
              title: citation.document_title,
            }
          "
          >{{ citation.document_title }}</el-button
        >
        <el-button
          text
          type="primary"
          size="small"
          class="citation-verify"
          @click="verify(citation)"
          >核对引用</el-button
        >
      </div>
      <div class="muted citation-meta">
        文档第 {{ citation.document_version }} 版 · 第
        {{ citation.chunk_index + 1 }} 个知识片段 ·
        {{ locationText(citation.source_location) }}
      </div>
      <details>
        <summary>查看引用原文</summary>
        <p class="source-text">{{ citation.text }}</p>
      </details>
    </article>
  </section>
  <el-dialog
    v-model="showDialog"
    title="核对引用来源"
    width="min(680px, 92vw)"
    :close-on-click-modal="false"
  >
    <p class="muted">
      使用当前账号从知识库读取引用片段。历史回答保留当时的来源版本。
    </p>
    <div v-loading="busy" class="citation-dialog-body">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="changed"
        title="当前来源与历史引用不同，请以显示的版本分别核对。"
        type="warning"
        :closable="false"
        show-icon
      />
      <template v-if="verified">
        <h3>{{ verified.document_title }}</h3>
        <p class="muted">
          文档第 {{ verified.document_version }} 版 ·
          {{ locationText(verified.source_location) }}
        </p>
        <p class="source-text">{{ verified.text }}</p>
      </template>
    </div>
    <template #footer
      ><el-button @click="showDialog = false">关闭</el-button></template
    >
  </el-dialog>
  <DocumentPreview
    :document="previewDocument"
    @close="previewDocument = null"
  />
</template>

<style scoped>
.citation-list {
  margin-top: 22px;
}
.citation-list h4 {
  margin: 0 0 12px;
  font-size: 14px;
}
.citation-list h4 span {
  margin-left: 10px;
  font-weight: 400;
  font-size: 12px;
}
.citation-card {
  margin-top: 8px;
  padding: 12px 14px;
  background: var(--color-canvas, #f6f7f3);
  border: 1px solid var(--color-border, #e2e7df);
  border-radius: 10px;
}
.citation-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.citation-title {
  max-width: 75%;
  white-space: normal;
  text-align: left;
  font-weight: 600;
  font-size: 13px;
}
.citation-meta {
  font-size: 12px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
summary {
  margin-top: 9px;
  color: var(--color-primary, #245b48);
  font-size: 12px;
  cursor: pointer;
}
.source-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.85;
}
.citation-dialog-body {
  min-height: 90px;
}
</style>
