import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CitationList from "../../src/components/CitationList.vue";
import type { Citation } from "../../src/api/agent-types";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../src/api/client", () => ({
  api: request,
  errorMessage: (error: unknown) =>
    error instanceof Error ? error.message : "请求失败",
}));

const citation: Citation = {
  document_id: "88167315-0339-4647-b209-89ebd11e17fc",
  document_title: '单测退款规则 <img src=x onerror="alert(1)">',
  document_version: 1,
  index_generation: 1,
  chunk_id: "ac34c334-b6ae-4562-9b73-1463458e4c28",
  chunk_index: 0,
  text: '<script>alert("unit fixture")</script> 请在七天内提交申请。',
  source_location: { page: 2 },
  citation_url: 'javascript:alert("untrusted model URL")',
  vector_similarity: 0.7,
  bm25_score: 1,
  rerank_score: 0.8,
};

function view(citations = [citation]) {
  return mount(CitationList, {
    props: { citations },
    global: {
      directives: { loading: () => undefined },
      stubs: {
        "el-button": { template: "<button><slot /></button>" },
        "el-dialog": {
          props: ["modelValue"],
          template:
            '<div v-if="modelValue" data-testid="citation-dialog"><slot /><slot name="footer" /></div>',
        },
        "el-alert": {
          props: ["title"],
          template: '<p role="alert">{{ title }}</p>',
        },
      },
    },
  });
}

describe("authenticated citation display", () => {
  beforeEach(() => {
    request.mockReset();
  });

  it("shows a task-authorized historical citation as escaped text, without following its URL", () => {
    const wrapper = view();
    expect(wrapper.text()).toContain(citation.document_title);
    expect(wrapper.text()).toContain(citation.text);
    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find("a").exists()).toBe(false);
    expect(request).not.toHaveBeenCalled();
  });

  it("fetches the current source through a trusted citation route only after a click", async () => {
    request.mockResolvedValue({ ...citation, text: "当前认证返回的单测来源" });
    const wrapper = view();
    await wrapper.get("button").trigger("click");
    await flushPromises();

    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith(
      `/api/v1/knowledge/citations/${citation.chunk_id}`,
    );
    expect(wrapper.get('[data-testid="citation-dialog"]').text()).toContain(
      "当前认证返回的单测来源",
    );
    expect(wrapper.get('[role="alert"]').text()).toContain(
      "当前来源与历史引用不同",
    );
  });

  it("keeps permission errors visible without presenting a fetched source", async () => {
    request.mockRejectedValue(new Error("当前账号无权访问单测来源"));
    const wrapper = view();
    await wrapper.get("button").trigger("click");
    await flushPromises();

    const dialog = wrapper.get('[data-testid="citation-dialog"]');
    expect(dialog.get('[role="alert"]').text()).toBe(
      "当前账号无权访问单测来源",
    );
    expect(dialog.find(".source-text").exists()).toBe(false);
    expect(dialog.find(".source-id").exists()).toBe(false);
  });

  it("does not let an older source response replace the currently selected citation", async () => {
    let releaseOld!: (value: Citation) => void;
    request.mockImplementationOnce(
      () =>
        new Promise<Citation>((resolve) => {
          releaseOld = resolve;
        }),
    );
    const newer = {
      ...citation,
      chunk_id: "53bb4599-94ab-482d-a5d7-0e3d921e44e0",
      document_title: "第二个单测来源",
      text: "第二个来源正文",
    };
    request.mockResolvedValueOnce(newer);
    const wrapper = view([citation, newer]);
    const buttons = wrapper.findAll(".citation-topline button");
    await buttons[0]!.trigger("click");
    await buttons[1]!.trigger("click");
    await flushPromises();
    releaseOld(citation);
    await flushPromises();

    const dialog = wrapper.get('[data-testid="citation-dialog"]');
    expect(dialog.find(".source-text").text()).toBe(newer.text);
    expect(dialog.text()).not.toContain(citation.text);
  });
});
