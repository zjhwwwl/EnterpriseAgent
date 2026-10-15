import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { computed, defineComponent, inject, provide, type Ref } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeView from "../../src/views/KnowledgeView.vue";
import type { KnowledgeDocument } from "../../src/api/business-types";
import { auth, clearAuth } from "../../src/stores/auth";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../src/api/client", async (original) => ({
  ...(await original<typeof import("../../src/api/client")>()),
  api: request,
}));

const documents: KnowledgeDocument[] = ["pdf", "docx", "txt", "markdown"].map(
  (fileType, index) => ({
    id: `document-${fileType}`,
    title: `${fileType.toUpperCase()} 业务说明`,
    file_type: fileType,
    checksum: "fixture-checksum",
    version: 1,
    visibility: "public",
    status: ["uploaded", "indexing", "indexed", "failed"][index]!,
    index_generation: 2,
    index_attempt_generation: 2,
    chunk_count: 3,
    index_error_code: null,
    indexed_at: null,
    created_at: "2026-10-15T00:00:00Z",
  }),
);
const nextDocument = {
  ...documents[0]!,
  id: "document-page-two",
  title: "第二页的文档",
};

const Table = defineComponent({
  props: ["data"],
  setup(props) {
    provide(
      "rows",
      computed(() => props.data),
    );
    return {};
  },
  template: "<div><slot /></div>",
});
const Column = defineComponent({
  props: ["label"],
  setup() {
    return { rows: inject<Ref<KnowledgeDocument[]>>("rows") };
  },
  template:
    '<section :aria-label="label"><div v-for="row in rows" :key="row.id"><slot :row="row" /></div></section>',
});
const mounted: VueWrapper[] = [];
function view() {
  const wrapper = mount(KnowledgeView, {
    global: {
      directives: { loading: () => undefined },
      stubs: {
        CitationList: true,
        "el-table": Table,
        "el-table-column": Column,
        "el-button": {
          props: ["disabled", "nativeType"],
          template:
            '<button :disabled="disabled" :type="nativeType ?? \'button\'"><slot /></button>',
        },
        "el-tag": { template: "<span><slot /></span>" },
        "el-alert": {
          props: ["title"],
          template: '<div role="alert">{{ title }}<slot /></div>',
        },
        "el-dialog": {
          props: ["modelValue", "title"],
          template:
            '<section v-if="modelValue" role="dialog" :aria-label="title"><slot /><slot name="footer" /></section>',
        },
        "el-input": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template:
            '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
        "el-select": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template:
            "<select :value=\"modelValue\" @change=\"$emit('update:modelValue', typeof modelValue === 'number' ? Number($event.target.value) : $event.target.value)\"><slot /></select>",
        },
        "el-option": {
          props: ["value", "label"],
          template: '<option :value="value">{{ label }}</option>',
        },
        "el-pagination": {
          emits: ["update:currentPage", "current-change"],
          template:
            "<button class=\"next-page\" @click=\"$emit('update:currentPage', 2); $emit('current-change', 2)\">下一页</button>",
        },
        "el-form": { template: "<div><slot /></div>" },
        "el-form-item": { template: "<div><slot /></div>" },
        "el-radio-group": { template: "<div><slot /></div>" },
        "el-radio": { template: "<span><slot /></span>" },
        "el-checkbox": { template: "<label><slot /></label>" },
      },
    },
  });
  mounted.push(wrapper);
  return wrapper;
}

describe("business-facing knowledge browser", () => {
  beforeEach(() => {
    auth.user = { id: 1, username: "knowledge-unit", role: "admin" };
    auth.token = "knowledge-unit-session";
    request.mockReset();
    request.mockImplementation(
      (path: string, options?: { body?: { mode: string } }) => {
        if (path.includes("/content")) {
          const documentId = path.split("/").at(-2);
          const selected = [...documents, nextDocument].find(
            (doc) => doc.id === documentId,
          )!;
          return Promise.resolve({
            document_id: selected.id,
            title: selected.title,
            file_type: selected.file_type,
            version: selected.version,
            content: `${selected.title}的完整正文。`,
          });
        }
        if (path === "/api/v1/knowledge/search")
          return Promise.resolve({
            status: "insufficient_evidence",
            message: "没有找到足够相关的文档内容。",
            answer_mode: "evidence_only",
            mode: options!.body!.mode,
            citations: [],
            elapsed_ms: 200,
            llm_calls: 0,
          });
        return Promise.resolve({
          items: path.includes("offset=20") ? [nextDocument] : documents,
          total: 21,
          offset: path.includes("offset=20") ? 20 : 0,
          limit: 20,
        });
      },
    );
  });
  afterEach(() => {
    mounted.splice(0).forEach((wrapper) => wrapper.unmount());
    clearAuth();
  });

  it("opens every supported document format by clicking its title, independently of search readiness", async () => {
    const wrapper = view();
    await flushPromises();
    const titles = wrapper.findAll(".document-title");
    expect(titles).toHaveLength(4);
    for (let index = 0; index < titles.length; index++) {
      await titles[index]!.trigger("click");
      await flushPromises();
      expect(wrapper.get(".preview-content").text()).toBe(
        `${documents[index]!.title}的完整正文。`,
      );
      const dialog = wrapper.get('[role="dialog"]');
      const close = dialog
        .findAll("button")
        .find((button) => button.text() === "关闭")!;
      await close.trigger("click");
      expect(wrapper.find(".preview-content").exists()).toBe(false);
    }
  });

  it("keeps titles on later document-list pages clickable", async () => {
    const wrapper = view();
    await flushPromises();
    await wrapper.get(".next-page").trigger("click");
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      "/api/v1/knowledge/documents?offset=20&limit=20",
      { signal: expect.any(AbortSignal) },
    );
    await wrapper.get(".document-title").trigger("click");
    await flushPromises();
    expect(wrapper.get(".preview-content").text()).toBe(
      `${nextDocument.title}的完整正文。`,
    );
  });

  it("uses everyday labels and updates the selected method summary while retaining the API mode", async () => {
    const wrapper = view();
    await flushPromises();
    const text = wrapper.text();
    expect(text).toContain("智能查找（推荐）");
    expect(text).toContain("待准备");
    expect(text).toContain("准备中");
    expect(text).toContain("可查询");
    expect(text).toContain("准备失败");
    expect(text).not.toMatch(
      /BM25|Rerank|Chunk|向量|混合检索|重排|索引代次|LLM/,
    );
    await wrapper.get('select[aria-label="查找方式"]').setValue("vector");
    expect(wrapper.get(".knowledge-metrics").text()).toContain("按意思查找");
    expect(wrapper.get(".knowledge-metrics").text()).not.toContain("智能查找");
    await wrapper
      .get('textarea[aria-label="知识查询问题"]')
      .setValue("退款规则");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(request).toHaveBeenCalledWith("/api/v1/knowledge/search", {
      method: "POST",
      body: { query: "退款规则", mode: "vector", top_k: 5 },
      signal: expect.any(AbortSignal),
    });
    expect(wrapper.get(".search-meta").text()).toContain("按意思查找");
    expect(wrapper.get(".search-meta").text()).toContain("0.20 秒");
  });
});
