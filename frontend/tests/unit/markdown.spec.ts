import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import MarkdownContent from "../../src/components/MarkdownContent.vue";

function render(content: string) {
  return mount(MarkdownContent, { props: { content } });
}

describe("model Markdown sanitization", () => {
  it("renders headings, emphasis, and tables from Markdown", () => {
    const wrapper = render(
      "# 业务回答\n\n**已完成**\n\n| 指标 | 值 |\n| --- | --- |\n| 订单 | 7 |",
    );
    expect(wrapper.get("h1").text()).toBe("业务回答");
    expect(wrapper.get("strong").text()).toBe("已完成");
    expect(wrapper.get("td").text()).toBe("订单");
    expect(wrapper.findAll("td")[1]!.text()).toBe("7");
  });

  it("removes scripts and event handlers while retaining harmless answer text", () => {
    const wrapper = render(
      '<script>alert("fixture")</script><p onclick="alert(1)">安全正文</p>',
    );
    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.get("p").attributes("onclick")).toBeUndefined();
    expect(wrapper.text()).toContain("安全正文");
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "jav&#x61;script:alert(1)",
  ])("removes an executable link protocol from %s", (url) => {
    const wrapper = render(`<a href="${url}">不可信链接</a>`);
    expect(wrapper.get("a").attributes("href")).toBeUndefined();
    expect(wrapper.text()).toContain("不可信链接");
  });

  it("keeps safe HTTPS links but does not allow a model-selected browser target", () => {
    const wrapper = render(
      '<a href="https://example.com/policy" target="_blank">公开资料</a>',
    );
    expect(wrapper.get("a").attributes("href")).toBe(
      "https://example.com/policy",
    );
    expect(wrapper.get("a").attributes("target")).toBeUndefined();
  });

  it("removes remote images, embeds, forms, styles, SVG, and inline CSS", () => {
    const wrapper = render(
      '<img src="https://example.com/tracker" onerror="alert(1)"><iframe src="https://example.com"></iframe><form><input name="password"><button>提交</button></form><style>body{display:none}</style><svg onload="alert(1)"><circle /></svg><p style="position:fixed" data-private="fixture">保留内容</p>',
    );
    expect(
      wrapper.find("img, iframe, form, input, button, style, svg").exists(),
    ).toBe(false);
    expect(wrapper.get("p").attributes("style")).toBeUndefined();
    expect(wrapper.get("p").attributes("data-private")).toBeUndefined();
    expect(wrapper.text()).toContain("保留内容");
  });
});
