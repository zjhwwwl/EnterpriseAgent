<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, ApiError, errorMessage } from "../api/client";
import type { KnowledgeDocumentContent } from "../api/business-types";
import { auth } from "../stores/auth";

const props = defineProps<{
  document: { id: string; title: string } | null;
}>();
const emit = defineEmits<{ close: [] }>();
const content = ref<KnowledgeDocumentContent | null>(null);
const busy = ref(false);
const error = ref("");
const requestId = ref<string | null>(null);
const identity = computed(
  () => `${auth.user?.id ?? ""}:${auth.user?.role ?? ""}:${auth.token}`,
);
let sequence = 0;
let controller: AbortController | null = null;

function stopRead() {
  sequence++;
  controller?.abort();
  controller = null;
  content.value = null;
  busy.value = false;
  error.value = "";
  requestId.value = null;
}

function close() {
  stopRead();
  emit("close");
}

async function loadContent() {
  stopRead();
  if (!props.document || !auth.user) return;
  const documentId = props.document.id;
  const actor = identity.value;
  const current = sequence;
  const reader = new AbortController();
  controller = reader;
  busy.value = true;
  try {
    const result = await api<KnowledgeDocumentContent>(
      `/api/v1/knowledge/documents/${encodeURIComponent(documentId)}/content`,
      { signal: reader.signal },
    );
    if (current !== sequence || actor !== identity.value) return;
    if (result.document_id !== documentId)
      throw new ApiError(
        0,
        "document_mismatch",
        "文档内容与所选文档不一致，请关闭后重试。",
      );
    content.value = result;
  } catch (failure) {
    if (
      current === sequence &&
      actor === identity.value &&
      !(failure instanceof DOMException && failure.name === "AbortError")
    ) {
      error.value =
        failure instanceof ApiError ? failure.message : errorMessage(failure);
      requestId.value = failure instanceof ApiError ? failure.requestId : null;
    }
  } finally {
    if (current === sequence) {
      controller = null;
      busy.value = false;
    }
  }
}

watch(
  () => props.document?.id,
  () => void loadContent(),
  { immediate: true },
);
watch(identity, () => {
  stopRead();
  if (props.document) emit("close");
});
onBeforeUnmount(stopRead);
</script>

<template>
  <el-dialog
    :model-value="!!document"
    :title="content?.title ?? document?.title ?? '查看文档'"
    width="min(760px, 94vw)"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="
      (open: boolean) => {
        if (!open) close();
      }
    "
  >
    <div class="document-preview" aria-live="polite">
      <p v-if="busy" class="muted" role="status">正在读取文档内容…</p>
      <template v-else-if="error">
        <el-alert :title="error" type="error" :closable="false" show-icon />
        <p v-if="requestId" class="muted">请求编号：{{ requestId }}</p>
        <el-button class="preview-retry" @click="loadContent"
          >重新加载</el-button
        >
      </template>
      <template v-else-if="content">
        <p class="muted preview-meta">
          {{ content.file_type.toUpperCase() }} · 第 {{ content.version }} 版
        </p>
        <p class="muted preview-description">
          此处展示文档文字。需要查看原始排版、图片或附件时，请下载原文。
        </p>
        <div
          v-if="content.content"
          class="preview-content"
          tabindex="0"
          aria-label="文档内容"
        >
          {{ content.content }}
        </div>
        <p v-else class="muted">该文档没有可预览的文字。</p>
      </template>
    </div>
    <template #footer><el-button @click="close">关闭</el-button></template>
  </el-dialog>
</template>

<style scoped>
.document-preview {
  min-height: 120px;
}
.preview-meta {
  margin: 0 0 8px;
  font-size: 12px;
}
.preview-description {
  margin: 0 0 18px;
  font-size: 12px;
  line-height: 1.7;
}
.preview-content {
  max-height: 58vh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.9;
  padding: 18px 20px;
  border: 1px solid var(--color-border, #e2e7df);
  border-radius: 10px;
  background: var(--color-canvas, #f6f7f3);
}
.preview-retry {
  margin-top: 16px;
}
</style>
