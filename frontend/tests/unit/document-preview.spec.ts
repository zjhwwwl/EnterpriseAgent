import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DocumentPreview from "../../src/components/DocumentPreview.vue";
import { ApiError } from "../../src/api/client";
import type { KnowledgeDocumentContent } from "../../src/api/business-types";
import { auth, clearAuth } from "../../src/stores/auth";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../src/api/client", async (original) => ({
  ...(await original<typeof import("../../src/api/client")>()),
  api: request,
}));

const document = { id: "doc-policy", title: "售后处理说明" };
function response(
  overrides: Partial<KnowledgeDocumentContent> = {},
): KnowledgeDocumentContent {
  return {
    document_id: document.id,
    title: document.title,
    file_type: "markdown",
    version: 2,
    content: "# 售后处理说明\n\n退货申请应在规定期限内提交。\n\n文档末尾。",
    ...overrides,
  };
}

const mounted: VueWrapper[] = [];
function view(selected: { id: string; title: string } | null = document) {
  const wrapper = mount(DocumentPreview, {
    props: { document: selected },
    global: {
      stubs: {
        "el-dialog": {
          props: ["modelValue", "title"],
          emits: ["update:modelValue"],
          template:
            '<section v-if="modelValue" role="dialog" :aria-label="title"><h2>{{ title }}</h2><button class="dialog-x" @click="$emit(\'update:modelValue\', false)">关闭弹窗</button><slot /><slot name="footer" /></section>',
        },
        "el-button": { template: "<button><slot /></button>" },
        "el-alert": {
          props: ["title"],
          template: '<p role="alert">{{ title }}</p>',
        },
      },
    },
  });
  mounted.push(wrapper);
  return wrapper;
}

describe("authenticated full-document preview", () => {
  beforeEach(() => {
    request.mockReset();
    auth.user = { id: 1, username: "preview-unit", role: "admin" };
    auth.token = "unit-session-a";
  });
  afterEach(() => {
    mounted.splice(0).forEach((wrapper) => wrapper.unmount());
    clearAuth();
  });

  it("waits until a document is selected before requesting its content", async () => {
    const wrapper = view(null);
    expect(request).not.toHaveBeenCalled();
    request.mockResolvedValue(response());
    await wrapper.setProps({ document });
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      "/api/v1/knowledge/documents/doc-policy/content",
      { signal: expect.any(AbortSignal) },
    );
    expect(wrapper.get(".preview-content").text()).toBe(response().content);
  });

  it.each(["pdf", "docx", "txt", "markdown"])(
    "shows complete parsed %s text and its actual version",
    async (fileType) => {
      const completeText = `首段\n\n${"中间正文。".repeat(4_000)}\n\n最后一段。`;
      request.mockResolvedValue(
        response({ file_type: fileType, content: completeText }),
      );
      const wrapper = view();
      await flushPromises();
      expect(wrapper.get(".preview-content").text()).toBe(completeText);
      expect(wrapper.get(".preview-meta").text()).toContain(
        fileType.toUpperCase(),
      );
      expect(wrapper.get(".preview-meta").text()).toContain("第 2 版");
    },
  );

  it("escapes HTML and scripts in the title and source rather than executing them", async () => {
    const unsafe = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    request.mockResolvedValue(response({ title: unsafe, content: unsafe }));
    const wrapper = view();
    await flushPromises();
    expect(wrapper.get("h2").text()).toBe(unsafe);
    expect(wrapper.get(".preview-content").text()).toBe(unsafe);
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find("script").exists()).toBe(false);
  });

  it("shows permission errors and the request number, then permits an explicit retry", async () => {
    request.mockRejectedValueOnce(
      new ApiError(
        404,
        "document_not_found",
        "文档不存在或无权访问。",
        "req-preview",
      ),
    );
    request.mockResolvedValueOnce(response());
    const wrapper = view();
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("文档不存在或无权访问。");
    expect(wrapper.text()).toContain("req-preview");
    expect(wrapper.find(".preview-content").exists()).toBe(false);
    await wrapper.get(".preview-retry").trigger("click");
    await flushPromises();
    expect(request).toHaveBeenCalledTimes(2);
    expect(wrapper.get(".preview-content").text()).toBe(response().content);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it("aborts a pending read on close and ignores a response delivered afterward", async () => {
    let resolve!: (value: KnowledgeDocumentContent) => void;
    request.mockImplementationOnce(
      () => new Promise<KnowledgeDocumentContent>((done) => (resolve = done)),
    );
    const wrapper = view();
    expect(wrapper.get('[role="status"]').text()).toContain("正在读取");
    const signal = request.mock.calls[0]![1].signal as AbortSignal;
    await wrapper.get(".dialog-x").trigger("click");
    expect(signal.aborted).toBe(true);
    expect(wrapper.emitted("close")).toHaveLength(1);
    await wrapper.setProps({ document: null });
    resolve(response());
    await flushPromises();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
    expect(wrapper.find(".preview-content").exists()).toBe(false);
  });

  it("does not let an older document response replace a newly selected one", async () => {
    let resolveOld!: (value: KnowledgeDocumentContent) => void;
    request.mockImplementationOnce(
      () =>
        new Promise<KnowledgeDocumentContent>((done) => (resolveOld = done)),
    );
    const newer = { id: "doc-shipping", title: "物流说明" };
    request.mockResolvedValueOnce(
      response({
        document_id: newer.id,
        title: newer.title,
        content: "新文档全文。",
      }),
    );
    const wrapper = view();
    const oldSignal = request.mock.calls[0]![1].signal as AbortSignal;
    await wrapper.setProps({ document: newer });
    await flushPromises();
    resolveOld(response());
    await flushPromises();
    expect(oldSignal.aborted).toBe(true);
    expect(wrapper.get("h2").text()).toBe(newer.title);
    expect(wrapper.get(".preview-content").text()).toBe("新文档全文。");
  });

  it("clears content and aborts pending access when the login session changes", async () => {
    let resolve!: (value: KnowledgeDocumentContent) => void;
    request.mockImplementationOnce(
      () => new Promise<KnowledgeDocumentContent>((done) => (resolve = done)),
    );
    const wrapper = view();
    const signal = request.mock.calls[0]![1].signal as AbortSignal;
    auth.token = "unit-session-b";
    await flushPromises();
    expect(signal.aborted).toBe(true);
    expect(wrapper.emitted("close")).toHaveLength(1);
    resolve(response());
    await flushPromises();
    expect(wrapper.find(".preview-content").exists()).toBe(false);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("clears previously loaded text when the account signs out", async () => {
    request.mockResolvedValue(response());
    const wrapper = view();
    await flushPromises();
    expect(wrapper.find(".preview-content").exists()).toBe(true);
    clearAuth();
    await flushPromises();
    expect(wrapper.find(".preview-content").exists()).toBe(false);
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("refuses a successful response for a different document", async () => {
    request.mockResolvedValue(response({ document_id: "unexpected-document" }));
    const wrapper = view();
    await flushPromises();
    expect(wrapper.find(".preview-content").exists()).toBe(false);
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
  });

  it("aborts pending work when the preview component is unmounted", () => {
    request.mockReturnValue(new Promise(() => undefined));
    const wrapper = view();
    const signal = request.mock.calls[0]![1].signal as AbortSignal;
    wrapper.unmount();
    mounted.pop();
    expect(signal.aborted).toBe(true);
  });
});
