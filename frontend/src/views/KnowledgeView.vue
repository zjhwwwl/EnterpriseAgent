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
  uploaded: "待索引",
  indexing: "索引中",
  indexed: "已索引",
  failed: "索引失败",
  deleted: "已删除",
};
const modeLabels: Record<SearchMode, string> = {
  vector: "向量检索",
  hybrid: "BM25 + 向量",
  hybrid_rerank: "混合检索 + 重排",
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
        `「${result.title}」已索引，生成 ${result.chunk_count} 个片段。`,
      );
    else
      ElMessage.warning(
        `原文已保存，索引状态为「${statusLabels[result.status] ?? result.status}」。请查看索引错误并重试。`,
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
        ElMessage.success(
          `重新索引完成，当前索引代次 ${result.index_generation}。`,
        );
      else
        ElMessage.warning(
          `索引状态为「${statusLabels[result.status] ?? result.status}」，请检查错误信息。`,
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
    ElMessage.warning("请输入至少两个字符的检索问题。");
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
      <p class="muted">从原文到证据片段，建立可追溯、可维护的业务知识。</p>
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
      <span class="muted">当前页索引就绪</span
      ><strong
        >{{ documents ? readyOnPage : "—"
        }}<em v-if="documents"> / {{ documents.items.length }}</em></strong
      ><small>失败重试保留原始文件与错误状态</small>
    </div>
    <div class="metric-card">
      <span class="muted">检索方式</span
      ><strong class="text-metric">混合检索 + 重排</strong
      ><small>BM25、向量与本地 Reranker</small>
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
    :title="`「${lastUploaded.title}」原文已保存，但索引需要处理`"
    ><p>
      状态：{{ statusLabels[lastUploaded.status] ?? lastUploaded.status }} ·
      错误代码：{{
        lastUploaded.index_error_code ?? "无"
      }}。上传成功不代表可检索，请刷新并在文档列表重新索引。
    </p></el-alert
  >
  <section class="panel">
    <div class="panel-header">
      <div>
        <h2>知识文档</h2>
        <p class="muted">下载原文与索引状态均由后端返回。管理员可维护文档。</p>
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
          ><div class="document-title">{{ row.title }}</div>
          <div class="muted document-meta">
            {{ row.file_type.toUpperCase() }} · 文档 v{{ row.version }} ·
            {{ row.visibility === "public" ? "公开" : "内部" }}
          </div></template
        ></el-table-column
      >
      <el-table-column label="索引状态" min-width="175"
        ><template #default="{ row }"
          ><el-tag
            :type="
              row.index_error_code || row.status === 'failed'
                ? 'danger'
                : row.status === 'indexed'
                  ? 'success'
                  : 'warning'
            "
            >{{ statusLabels[row.status] ?? row.status }}</el-tag
          >
          <p v-if="row.index_error_code" class="index-error">
            {{ row.index_error_code
            }}<span v-if="row.status === 'indexed'"> · 保留上一有效索引</span>
          </p></template
        ></el-table-column
      >
      <el-table-column label="片段 / 索引代次" min-width="125"
        ><template #default="{ row }"
          >{{ row.chunk_count }} / {{ row.index_generation }}</template
        ></el-table-column
      >
      <el-table-column label="最近索引 · 北京时间" min-width="190"
        ><template #default="{ row }">{{
          date(row.indexed_at)
        }}</template></el-table-column
      >
      <el-table-column
        label="操作"
        :min-width="isAdmin ? 215 : 95"
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
              >重新索引</el-button
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
        <h2>检索证据</h2>
        <p class="muted">直接查看检索片段与来源，不调用付费对话模型。</p>
      </div>
      <el-tag effect="plain" type="info">Evidence only</el-tag>
    </div>
    <form @submit.prevent="searchKnowledge">
      <el-input
        v-model="searchQuery"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        placeholder="例如：商品签收后几天内可以申请无理由退货？"
        aria-label="知识检索问题"
      />
      <div class="toolbar search-toolbar">
        <el-select
          v-model="searchMode"
          aria-label="检索模式"
          :disabled="searchBusy"
          ><el-option
            v-for="(label, value) in modeLabels"
            :key="value"
            :label="label"
            :value="value" /></el-select
        ><el-select
          v-model="searchTopK"
          aria-label="检索结果数量"
          :disabled="searchBusy"
          ><el-option
            v-for="count in [3, 5, 10]"
            :key="count"
            :label="`返回 ${count} 个片段`"
            :value="count" /></el-select
        ><el-button type="primary" native-type="submit" :loading="searchBusy"
          >检索知识库</el-button
        >
      </div>
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
      title="文档已更新，请重新检索并核验最新证据。"
    />
    <template v-if="searchResult"
      ><el-alert
        :type="searchResult.status === 'evidence_found' ? 'success' : 'warning'"
        :title="searchResult.message"
        :closable="false" />
      <div class="search-meta">
        <span>{{
          searchResult.status === "evidence_found" ? "找到证据" : "证据不足"
        }}</span
        ><span>检索耗时 {{ searchResult.elapsed_ms.toFixed(1) }} ms</span
        ><span>LLM 调用 {{ searchResult.llm_calls }} 次</span>
      </div>
      <CitationList :citations="searchResult.citations"
    /></template>
    <div v-else-if="!searchBusy && !searchError" class="search-empty">
      <span class="search-mark">⌕</span>
      <p class="muted">输入业务问题，查看文档原文、位置与有效引用。</p>
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
            PDF / DOCX / TXT / Markdown。大小由服务端校验，默认上限 8 MiB。
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
      title="上传后解析、切分并生成索引，请等待后端返回状态。"
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
        >确认上传并索引</el-button
      ></template
    >
  </el-dialog>
  <el-dialog
    :model-value="!!pendingAction"
    :title="pendingAction?.kind === 'delete' ? '确认删除文档' : '确认重新索引'"
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
      <p class="muted">
        文档 v{{ pendingAction.document.version }} · 当前索引代次
        {{ pendingAction.document.index_generation }}
      </p>
      <el-alert
        :type="pendingAction.kind === 'delete' ? 'warning' : 'info'"
        :closable="false"
        :title="
          pendingAction.kind === 'delete'
            ? '删除后不再用于检索，关联历史引用可能失效。已发布退款政策的来源文档由后端保护。'
            : '重新解析原文并建立新索引。失败时保留上一有效索引，具体状态以服务端返回为准。'
        "
      /><el-checkbox v-model="actionAcknowledged" class="action-checkbox"
        >我已核对文档，确认{{
          pendingAction.kind === "delete" ? "删除" : "重新索引"
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
          pendingAction?.kind === "delete" ? "删除文档" : "重新索引"
        }}</el-button
      ></template
    >
  </el-dialog>
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
  width: 190px;
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
