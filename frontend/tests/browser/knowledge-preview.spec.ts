import { expect, test, type Page } from "@playwright/test";

async function guardReads(page: Page) {
  const blocked: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.name));
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const safePost = [
      "/api/v1/auth/login",
      "/api/v1/knowledge/search",
    ].includes(url.pathname);
    if (
      !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) ||
      (request.method() !== "GET" && !safePost)
    ) {
      blocked.push(`${request.method()} ${url.pathname}`);
      await route.abort();
    } else await route.continue();
  });
  return { blocked, errors };
}

async function enterKnowledge(page: Page, account?: string) {
  await page.goto("/login");
  await expect(page.locator("#login-password")).toHaveValue("DemoOnly!2026");
  if (account) {
    await page.locator(".login-form .el-select").click();
    await page.getByRole("option", { name: new RegExp(account) }).click();
  }
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page
    .getByRole("navigation", { name: "主要导航" })
    .getByRole("link", { name: /企业知识库/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "企业知识库", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".document-title").first()).toBeVisible();
}

async function preview(page: Page, title: ReturnType<Page["locator"]>) {
  const response = page.waitForResponse(
    (result) =>
      result.request().method() === "GET" &&
      /\/knowledge\/documents\/[^/]+\/content$/.test(
        new URL(result.url()).pathname,
      ),
  );
  await title.click();
  const actual = await response;
  expect(actual.status()).toBe(200);
  const document = (await actual.json()) as {
    document_id: string;
    title: string;
    content: string;
  };
  const dialog = page.getByRole("dialog", {
    name: document.title,
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("文档内容")).toHaveText(document.content);
  expect(document.content.trim().length).toBeGreaterThan(0);
  return { dialog, document };
}

test("default demo login, every document title, plain search modes and citation preview", async ({
  page,
}, testInfo) => {
  const checks = await guardReads(page);
  await enterKnowledge(page);
  const titles = page.locator(".document-title");
  const count = await titles.count();
  expect(count).toBeGreaterThanOrEqual(5);
  const opened: string[] = [];
  for (let index = 0; index < count; index++) {
    const { dialog, document } = await preview(page, titles.nth(index));
    opened.push(document.title);
    if (index === 0)
      await page.screenshot({
        path: testInfo.outputPath("document-preview.png"),
        animations: "disabled",
      });
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dialog).not.toBeVisible();
  }
  await page.getByLabel("知识查询问题").fill("已签收订单多久内可以申请退款？");
  const modes = [
    ["按意思查找", "vector"],
    ["关键词与意思匹配", "hybrid"],
    ["智能查找（推荐）", "hybrid_rerank"],
  ];
  for (const [label, mode] of modes) {
    await page
      .locator(".search-toolbar .el-select")
      .filter({
        has: page.getByRole("combobox", { name: "查找方式", exact: true }),
      })
      .click();
    await page.getByRole("option", { name: label, exact: true }).click();
    await expect(page.locator(".knowledge-metrics .text-metric")).toHaveText(
      label,
    );
    const response = page.waitForResponse(
      (result) =>
        result.request().method() === "POST" &&
        new URL(result.url()).pathname === "/api/v1/knowledge/search",
    );
    await page.getByRole("button", { name: "查找知识", exact: true }).click();
    const result = await response;
    expect(result.status()).toBe(200);
    expect((await result.json()).mode).toBe(mode);
  }
  const { dialog } = await preview(
    page,
    page.locator(".citation-title").first(),
  );
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.locator("body")).not.toContainText(
    /BM25|Reranker|Chunk|混合检索|向量检索|索引代次/,
  );
  expect(checks).toEqual({ blocked: [], errors: [] });
  await testInfo.attach("actual-local-preview-checks", {
    body: JSON.stringify({ opened, search_modes: modes, ...checks }),
    contentType: "application/json",
  });
});

test("staff document popup fits a common desktop resolution", async ({
  page,
}, testInfo) => {
  const checks = await guardReads(page);
  await page.setViewportSize({ width: 1366, height: 768 });
  await enterKnowledge(page, "demo_support");
  const { dialog } = await preview(
    page,
    page.locator(".document-title").first(),
  );
  const bounds = await dialog.locator(".el-dialog").boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.width).toBeLessThanOrEqual(800);
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(768);
  await expect(
    dialog.getByRole("button", { name: "关闭", exact: true }),
  ).toBeInViewport();
  await page.screenshot({
    path: testInfo.outputPath("staff-preview-desktop.png"),
    animations: "disabled",
  });
  expect(checks).toEqual({ blocked: [], errors: [] });
});
