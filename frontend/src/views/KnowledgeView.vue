<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, ApiError, downloadFile, errorMessage } from "../api/client";
import type {
  KnowledgeDocument,
  KnowledgeSearch,
  Page,
  SearchMode,
} from "../api/business-types";
import CitationList from "../components/CitationList.vue";
import DocumentPreview from "../components/DocumentPreview.vue";
import { auth } from "../stores/auth";
import { dateTime as date } from "../utils/format";

type VisibleError = {
  message: string;
  requestId: string | null;
  code: string | null;
};
const errorInfo = (error: unknown): VisibleError => ({
  message: error instanceof ApiError ? error.message : errorMessage(error),
  requestId: error instanceof ApiError ? error.requestId : null,
  code: error instanceof ApiError ? error.code : null,
});
const isAdmin = computed(() => auth.user?.role === "admin");
const identity = computed(
  () => `${auth.user?.id ?? ""}:${auth.user?.role ?? ""}`,
);
const documents = ref<Page<KnowledgeDocument> | null>(null);
const page = ref(1);
const pageSize = 20;
const loading = ref(false);
const listError = ref<VisibleError | null>(null);
const downloadError = ref<VisibleError | null>(null);
const downloading = ref<string | null>(null);
const previewDocument = ref<KnowledgeDocument | null>(null);
const uploadOpen = ref(false);
const uploadTitle = ref("");
const uploadVisibility = ref<"public" | "internal">("public");
const selectedFile = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const uploadBusy = ref(false);
const uploadError = ref<VisibleError | null>(null);
const lastUploaded = ref<KnowledgeDocument | null>(null);
const pendingAction = ref<{
  document: KnowledgeDocument;
  kind: "delete" | "reindex";
} | null>(null);
const actionBusy = ref(false);
const actionAcknowledged = ref(false);
const actionError = ref<VisibleError | null>(null);
const searchQuery = ref("");
const searchMode = ref<SearchMode>("hybrid_rerank");
const searchTopK = ref(5);
const searchBusy = ref(false);
const searchResult = ref<KnowledgeSearch | null>(null);
const searchError = ref<VisibleError | null>(null);
const knowledgeChanged = ref(false);
let listSequence = 0;
let searchSequence = 0;
let downloadSequence = 0;
const readers = new Set<AbortController>();
const readyOnPage = computed(
  () =>
    documents.value?.items.filter((doc) => doc.status === "indexed").length ??
    0,
);
const statusLabels: Record<string, string> = {
  uploaded: "待准备",
  indexing: "准备中",
  indexed: "可查询",
  failed: "准备失败",
  deleted: "已删除",
};
const modeLabels: Record<SearchMode, string> = {
  vector: "按意思查找",
  hybrid: "关键词与意思匹配",
  hybrid_rerank: "智能查找（推荐）",
};
const modeDescriptions: Record<SearchMode, string> = {
  vector: "根据问题的意思查找，用词不同也能匹配相关内容。",
  hybrid: "同时参考问题中的关键词和意思查找相关内容。",
  hybrid_rerank: "同时参考关键词和意思，再按与问题的相关程度排列结果。",
};
const extension = (type: string) => (type === "markdown" ? "md" : type);
function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
function stopReads() {
  readers.forEach((controller) => controller.abort());
  readers.clear();
}
async function loadDocuments() {
  if (!auth.user) return;
  const sequence = ++listSequence;
  const actor = identity.value;
  const controller = new AbortController();
  readers.add(controller);
  loading.value = true;
  listError.value = null;
  try {
    const result = await api<Page<KnowledgeDocument>>(
      `/api/v1/knowledge/documents?offset=${(page.value - 1) * pageSize}&limit=${pageSize}`,
      { signal: controller.signal },
    );
    if (sequence === listSequence && actor === identity.value)
      documents.value = result;
  } catch (error) {
    if (
      sequence === listSequence &&
      actor === identity.value &&
      !isAbort(error)
    )
      listError.value = errorInfo(error);
  } finally {
    readers.delete(controller);
    if (sequence === listSequence) loading.value = false;
  }
}
function openUpload() {
  if (!isAdmin.value || uploadBusy.value) return;
  uploadTitle.value = "";
  uploadVisibility.value = "public";
  selectedFile.value = null;
  uploadError.value = null;
  uploadOpen.value = true;
  if (fileInput.value) fileInput.value.value = "";
}
function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  uploadError.value = null;
  if (file && !/\.(pdf|docx|txt|md|markdown)$/i.test(file.name)) {
    selectedFile.value = null;
    input.value = "";
    ElMessage.warning("仅支持 PDF、DOCX、TXT 与 Markdown 文件。");
    return;
  }
  selectedFile.value = file;
  if (file && !uploadTitle.value.trim())
    uploadTitle.value = file.name.replace(/\.[^.]+$/, "").slice(0, 200);
}
async function uploadDocument() {
  if (
    !isAdmin.value ||
    uploadBusy.value ||
    !selectedFile.value ||
    !uploadTitle.value.trim()
  )
    return;
  const actor = identity.value;
  const body = new FormData();
  body.append("file", selectedFile.value);
  body.append("title", uploadTitle.value.trim());
  body.append("visibility", uploadVisibility.value);
  uploadBusy.value = true;
  uploadError.value = null;
  try {
    const result = await api<KnowledgeDocument>("/api/v1/knowledge/documents", {
      method: "POST",
      body,
    });
    if (actor !== identity.value) return;
    lastUploaded.value = result;
    knowledgeChanged.value = !!searchResult.value;
    uploadOpen.value = false;
    page.value = 1;
    if (result.status === "indexed" && !result.index_error_code)
      ElMessage.success(
        `「${result.title}」已可查询，整理出 ${result.chunk_count} 个知识片段。`,
      );
    else
      ElMessage.warning(
        `原文已保存，当前状态为「${statusLabels[result.status] ?? "状态未知"}」。请检查提示后重新准备搜索内容。`,
      );
    await loadDocuments();
  } catch (error) {
    if (actor === identity.value) uploadError.value = errorInfo(error);
  } finally {
    uploadBusy.value = false;
  }
}
async function downloadDocument(document: KnowledgeDocument) {
  if (downloading.value) return;
  const sequence = ++downloadSequence;
  downloading.value = document.id;
  downloadError.value = null;
  try {
    await downloadFile(
      `/api/v1/knowledge/documents/${document.id}/download`,
      `${document.title}.${extension(document.file_type)}`,
    );
  } catch (error) {
    if (sequence === downloadSequence) downloadError.value = errorInfo(error);
  } finally {
    if (sequence === downloadSequence) downloading.value = null;
  }
}
function openAction(document: KnowledgeDocument, kind: "delete" | "reindex") {
  if (!isAdmin.value || actionBusy.value) return;
  pendingAction.value = { document, kind };
  actionAcknowledged.value = false;
  actionError.value = null;
}
async function performAction() {
  if (
    !pendingAction.value ||
    !isAdmin.value ||
    actionBusy.value ||
    !actionAcknowledged.value
  )
    return;
  const actor = identity.value;
  const { document, kind } = pendingAction.value;
  actionBusy.value = true;
  actionError.value = null;
  try {
    if (kind === "delete") {
      await api<void>(`/api/v1/knowledge/documents/${document.id}`, {
        method: "DELETE",
      });
      if (actor !== identity.value) return;
      ElMessage.success("文档已删除。历史引用需重新核验。");
    } else {
      const result = await api<KnowledgeDocument>(
        `/api/v1/knowledge/documents/${document.id}/reindex`,
        { method: "POST" },
      );
      if (actor !== identity.value) return;
      if (result.status === "indexed" && !result.index_error_code)
        ElMessage.success(`「${result.title}」的搜索内容已更新。`);
      else
        ElMessage.warning(
          `当前状态为「${statusLabels[result.status] ?? "状态未知"}」，请检查提示信息。`,
        );
    }
    knowledgeChanged.value = !!searchResult.value;
    pendingAction.value = null;
    await loadDocuments();
  } catch (error) {
    if (actor === identity.value) {
      actionError.value = errorInfo(error);
      await loadDocuments();
    }
  } finally {
    actionBusy.value = false;
  }
}
async function searchKnowledge() {
  const query = searchQuery.value.trim();
  if (!auth.user || searchBusy.value) return;
  if (query.length < 2) {
    ElMessage.warning("请输入至少两个字符的问题。");
    return;
  }
  const sequence = ++searchSequence;
  const actor = identity.value;
  const controller = new AbortController();
  readers.add(controller);
  searchBusy.value = true;
  searchError.value = null;
  searchResult.value = null;
  try {
    const result = await api<KnowledgeSearch>("/api/v1/knowledge/search", {
      method: "POST",
      body: { query, mode: searchMode.value, top_k: searchTopK.value },
      signal: controller.signal,
    });
    if (sequence === searchSequence && actor === identity.value) {
      searchResult.value = result;
      knowledgeChanged.value = false;
    }
  } catch (error) {
    if (
      sequence === searchSequence &&
      actor === identity.value &&
      !isAbort(error)
    )
      searchError.value = errorInfo(error);
  } finally {
    readers.delete(controller);
    if (sequence === searchSequence) searchBusy.value = false;
  }
}
watch(
  identity,
  () => {
    stopReads();
    listSequence++;
    searchSequence++;
    downloadSequence++;
    documents.value = null;
    page.value = 1;
    searchResult.value = null;
    searchError.value = listError.value = downloadError.value = null;
    searchBusy.value = false;
    downloading.value = null;
    previewDocument.value = null;
    pendingAction.value = null;
    uploadOpen.value = false;
    lastUploaded.value = null;
    knowledgeChanged.value = false;
    if (auth.user) void loadDocuments();
  },
  { immediate: true },
);
onBeforeUnmount(stopReads);
</script>

<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">ENTERPRISE KNOWLEDGE</div>
      <h1>企业知识库</h1>
      <p class="muted">
        查阅业务政策和操作说明，每条查询结果均可查看文档来源。
      </p>
    </div>
    <el-button v-if="isAdmin" type="primary" @click="openUpload"
      >上传文档</el-button
    >
  </div>
  <div class="metric-grid knowledge-metrics">
    <div class="metric-card">
      <span class="muted">可访问文档</span
      ><strong>{{ documents?.total ?? "—" }}</strong
      ><small>{{
        auth.user?.role === "customer"
          ? "客户仅可访问公开文档"
          : "按当前角色授权展示"
      }}</small>
    </div>
    <div class="metric-card">
      <span class="muted">当前页可查询文档</span
      ><strong
        >{{ documents ? readyOnPage : "—"
        }}<em v-if="documents"> / {{ documents.items.length }}</em></strong
      ><small>文档准备失败时，仍可查看或下载已保存的原文</small>
    </div>
    <div class="metric-card">
      <span class="muted">查找方式</span
      ><strong class="text-metric">{{ modeLabels[searchMode] }}</strong
      ><small>{{ modeDescriptions[searchMode] }}</small>
    </div>
  </div>
  <el-alert
    v-if="
      lastUploaded &&
      (lastUploaded.status !== 'indexed' || lastUploaded.index_error_code)
    "
    class="upload-result"
    type="warning"
    :closable="true"
    :title="`「${lastUploaded.title}」原文已保存，搜索内容尚未准备完成`"
    ><p>
      状态：{{ statusLabels[lastUploaded.status] ?? "状态未知" }} · 错误代码：{{
        lastUploaded.index_error_code ?? "无"
      }}。请刷新列表；如未恢复，可点击“更新搜索内容”重试。
    </p></el-alert
  >
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>知识文档</h2>
        <p class="muted">
          点击标题阅读文档，或下载原文查看完整排版。管理员可上传和维护文档。
        </p>
      </div>
      <el-button :loading="loading" @click="loadDocuments">刷新文档</el-button>
    </div>
    <el-alert
      v-if="listError"
      type="error"
      :title="listError.message"
      :closable="false"
      ><p v-if="listError.requestId">
        请求编号：{{ listError.requestId }}
      </p></el-alert
    >
    <el-alert
      v-if="downloadError"
      type="error"
      :title="downloadError.message"
      :closable="false"
      ><p v-if="downloadError.requestId">
        请求编号：{{ downloadError.requestId }}
      </p></el-alert
    >
    <el-table
      v-loading="loading"
      :data="documents?.items ?? []"
      empty-text="暂无可访问文档"
      class="document-table"
    >
      <el-table-column label="文档" min-width="220"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            class="document-title"
            @click="previewDocument = row"
            >{{ row.title }}</el-button
          >
          <div class="muted document-meta">
            {{ row.file_type.toUpperCase() }} · 第 {{ row.version }} 版 ·
            {{ row.visibility === "public" ? "公开" : "内部" }}
          </div></template
        ></el-table-column
      >
      <el-table-column label="查询状态" min-width="175"
        ><template #default="{ row }"
          ><el-tag
            :type="
              row.index_error_code || row.status === 'failed'
                ? 'danger'
                : row.status === 'indexed'
                  ? 'success'
                  : 'warning'
            "
            >{{ statusLabels[row.status] ?? "状态未知" }}</el-tag
          >
          <p v-if="row.index_error_code" class="index-error">
            搜索内容更新失败<span v-if="row.status === 'indexed'"
              >，仍可查询之前准备的内容</span
            >
          </p></template
        ></el-table-column
      >
      <el-table-column label="知识片段" min-width="105"
        ><template #default="{ row }">{{
          row.chunk_count
        }}</template></el-table-column
      >
      <el-table-column label="搜索内容更新时间" min-width="190"
        ><template #default="{ row }">{{
          date(row.indexed_at)
        }}</template></el-table-column
      >
      <el-table-column
        label="操作"
        :min-width="isAdmin ? 245 : 95"
        fixed="right"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :loading="downloading === row.id"
            :disabled="!!downloading && downloading !== row.id"
            @click="downloadDocument(row)"
            >下载原文</el-button
          ><template v-if="isAdmin"
            ><el-button
              link
              type="primary"
              :disabled="row.status === 'indexing' || actionBusy"
              @click="openAction(row, 'reindex')"
              >更新搜索内容</el-button
            ><el-button
              link
              type="danger"
              :disabled="actionBusy"
              @click="openAction(row, 'delete')"
              >删除</el-button
            ></template
          ></template
        ></el-table-column
      >
    </el-table>
    <el-pagination
      v-model:current-page="page"
      class="pagination"
      :page-size="pageSize"
      :total="documents?.total ?? 0"
      layout="total, prev, pager, next"
      @current-change="loadDocuments"
    />
  </section>
  <section class="panel search-panel">
    <div class="panel-header">
      <div>
        <h2>查找业务知识</h2>
        <p class="muted">输入问题，查看相关文档段落和出处。</p>
      </div>
      <el-tag effect="plain" type="info">文档原文</el-tag>
    </div>
    <form @submit.prevent="searchKnowledge">
      <el-input
        v-model="searchQuery"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        placeholder="例如：商品签收后几天内可以申请无理由退货？"
        aria-label="知识查询问题"
      />
      <div class="toolbar search-toolbar">
        <el-select
          v-model="searchMode"
          aria-label="查找方式"
          :disabled="searchBusy"
          ><el-option
            v-for="(label, value) in modeLabels"
            :key="value"
            :label="label"
            :value="value" /></el-select
        ><el-select
          v-model="searchTopK"
          aria-label="显示结果数量"
          :disabled="searchBusy"
          ><el-option
            v-for="count in [3, 5, 10]"
            :key="count"
            :label="`显示 ${count} 条结果`"
            :value="count" /></el-select
        ><el-button type="primary" native-type="submit" :loading="searchBusy"
          >查找知识</el-button
        >
      </div>
      <p class="muted search-description">{{ modeDescriptions[searchMode] }}</p>
    </form>
    <el-alert
      v-if="searchError"
      type="error"
      :title="searchError.message"
      :closable="false"
      ><p v-if="searchError.requestId">
        请求编号：{{ searchError.requestId }}
      </p></el-alert
    >
    <el-alert
      v-if="knowledgeChanged"
      type="warning"
      :closable="false"
      title="文档已更新，请重新查找并核对最新内容。"
    />
    <template v-if="searchResult"
      ><el-alert
        :type="searchResult.status === 'evidence_found' ? 'success' : 'warning'"
        :title="searchResult.message"
        :closable="false" />
      <div class="search-meta">
        <span>{{
          searchResult.status === "evidence_found"
            ? "找到相关内容"
            : "暂无充分相关内容"
        }}</span
        ><span
          >查找用时 {{ (searchResult.elapsed_ms / 1000).toFixed(2) }} 秒</span
        >
        <span>{{
          modeLabels[searchResult.mode as SearchMode] ?? "知识查找"
        }}</span>
      </div>
      <CitationList :citations="searchResult.citations"
    /></template>
    <div v-else-if="!searchBusy && !searchError" class="search-empty">
      <span class="search-mark">⌕</span>
      <p class="muted">输入业务问题，查看相关内容及其文档出处。</p>
    </div>
  </section>
  <el-dialog
    v-model="uploadOpen"
    title="上传知识文档"
    width="min(600px, 94vw)"
    :close-on-click-modal="!uploadBusy"
    :close-on-press-escape="!uploadBusy"
    :show-close="!uploadBusy"
  >
    <el-form label-position="top"
      ><el-form-item label="文档标题"
        ><el-input
          v-model="uploadTitle"
          maxlength="200"
          show-word-limit
          placeholder="输入准确、易识别的知识标题"
          :disabled="uploadBusy" /></el-form-item
      ><el-form-item label="可见范围"
        ><el-radio-group v-model="uploadVisibility" :disabled="uploadBusy"
          ><el-radio value="public">公开 · 客户可查阅</el-radio
          ><el-radio value="internal"
            >内部 · 员工可查阅</el-radio
          ></el-radio-group
        ></el-form-item
      ><el-form-item label="原始文件"
        ><div class="file-picker">
          <input
            ref="fileInput"
            type="file"
            accept=".pdf,.docx,.txt,.md,.markdown"
            aria-label="知识文档文件"
            :disabled="uploadBusy"
            @change="chooseFile"
          />
          <p class="muted">
            支持 PDF、Word 文档（DOCX）、纯文本（TXT）和 Markdown，默认最大 8
            MB。
          </p>
          <p v-if="selectedFile" class="chosen-file">
            {{ selectedFile.name }} ·
            {{ (selectedFile.size / 1024).toFixed(1) }} KiB
          </p>
        </div></el-form-item
      ></el-form
    >
    <el-alert
      type="info"
      :closable="false"
      title="上传后会自动整理文档，准备好后即可查询。请等待完成提示。"
    />
    <el-alert
      v-if="uploadError"
      class="section-alert"
      type="error"
      :title="uploadError.message"
      :closable="false"
      ><p v-if="uploadError.requestId">请求编号：{{ uploadError.requestId }}</p>
      <p>
        如果连接中断，请先关闭弹窗并刷新文档列表，确认是否已保存，再决定是否重新上传。
      </p></el-alert
    >
    <template #footer
      ><el-button :disabled="uploadBusy" @click="uploadOpen = false"
        >取消</el-button
      ><el-button
        type="primary"
        :loading="uploadBusy"
        :disabled="!selectedFile || !uploadTitle.trim()"
        @click="uploadDocument"
        >确认上传</el-button
      ></template
    >
  </el-dialog>
  <el-dialog
    :model-value="!!pendingAction"
    :title="
      pendingAction?.kind === 'delete' ? '确认删除文档' : '确认更新搜索内容'
    "
    width="min(590px, 94vw)"
    :close-on-click-modal="!actionBusy"
    :close-on-press-escape="!actionBusy"
    :show-close="!actionBusy"
    @update:model-value="
      (open: boolean) => {
        if (!open && !actionBusy) pendingAction = null;
      }
    "
  >
    <template v-if="pendingAction"
      ><h3>{{ pendingAction.document.title }}</h3>
      <p class="muted">文档第 {{ pendingAction.document.version }} 版</p>
      <el-alert
        :type="pendingAction.kind === 'delete' ? 'warning' : 'info'"
        :closable="false"
        :title="
          pendingAction.kind === 'delete'
            ? '删除后无法继续查阅，历史回答中的引用也可能失效。已发布退款政策的来源文档无法删除。'
            : '重新整理已保存的文档，更新可查询的内容。准备失败时仍可查询之前已准备好的内容。'
        "
      /><el-checkbox v-model="actionAcknowledged" class="action-checkbox"
        >我已核对文档，确认{{
          pendingAction.kind === "delete" ? "删除" : "更新搜索内容"
        }}</el-checkbox
      ><el-alert
        v-if="actionError"
        type="error"
        :title="actionError.message"
        :closable="false"
        ><p v-if="actionError.code">错误代码：{{ actionError.code }}</p>
        <p v-if="actionError.requestId">
          请求编号：{{ actionError.requestId }}
        </p></el-alert
      ></template
    >
    <template #footer
      ><el-button :disabled="actionBusy" @click="pendingAction = null"
        >取消</el-button
      ><el-button
        :type="pendingAction?.kind === 'delete' ? 'danger' : 'primary'"
        :loading="actionBusy"
        :disabled="!actionAcknowledged"
        @click="performAction"
        >确认{{
          pendingAction?.kind === "delete" ? "删除文档" : "更新搜索内容"
        }}</el-button
      ></template
    >
  </el-dialog>
  <DocumentPreview
    :document="previewDocument"
    @close="previewDocument = null"
  />
</template>

<style scoped>
.knowledge-metrics {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 24px;
}
.metric-card strong {
  display: block;
  margin: 12px 0 8px;
  font-size: 30px;
}
.metric-card strong.text-metric {
  font-size: 20px;
}
.metric-card em {
  font-size: 17px;
  font-style: normal;
  color: #969f95;
}
.metric-card small {
  color: var(--color-muted, #7c817b);
}
.upload-result {
  margin-bottom: 20px;
}
.document-table {
  margin-top: 12px;
}
.document-title {
  max-width: 100%;
  white-space: normal;
  text-align: left;
  font-weight: 600;
  line-height: 1.7;
}
.document-meta {
  font-size: 12px;
}
.index-error {
  margin: 6px 0 0;
  color: #ba5b42;
  font-size: 11px;
  overflow-wrap: anywhere;
}
.pagination {
  justify-content: flex-end;
  margin-top: 20px;
}
.search-panel {
  margin-top: 24px;
}
.search-toolbar {
  margin: 16px 0 20px;
}
.search-toolbar .el-select {
  width: 205px;
}
.search-toolbar .el-select + .el-select {
  width: 160px;
}
.search-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-top: 14px;
  font-size: 12px;
  color: var(--color-muted, #758076);
}
.search-description {
  margin: -6px 0 20px;
  font-size: 12px;
  line-height: 1.7;
}
.search-empty {
  text-align: center;
  padding: 35px 20px 25px;
}
.search-mark {
  color: #a8b7a4;
  font-size: 44px;
  line-height: 1;
}
.file-picker {
  width: 100%;
  padding: 18px;
  border: 1px dashed #b9c7b7;
  border-radius: 10px;
  background: #f7f9f4;
}
.file-picker input {
  width: 100%;
  font: inherit;
}
.file-picker p {
  margin-bottom: 0;
  font-size: 12px;
  line-height: 1.8;
}
.chosen-file {
  color: #245b48;
  overflow-wrap: anywhere;
}
.section-alert {
  margin-top: 16px;
}
.action-checkbox {
  margin: 20px 0;
}
:deep(.el-checkbox) {
  height: auto;
  white-space: normal;
  line-height: 1.6;
}
:deep(.el-checkbox__label) {
  white-space: normal;
}
@media (max-width: 1100px) {
  .knowledge-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .knowledge-metrics > :last-child {
    grid-column: 1 / -1;
  }
}
</style>
