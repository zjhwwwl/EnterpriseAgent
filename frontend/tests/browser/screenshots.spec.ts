import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { expect, test, type Page, type Response } from "@playwright/test";
import type { AgentTask, Page as ResultPage } from "../../src/api/agent-types";

// Read existing fictional test data. The wrapper disables the model on the API
// server, and this browser guard blocks every business mutation before sending.
const allowed =
  process.env.EA_FRONTEND_SCREENSHOTS_ONLY === "true" &&
  process.env.EA_FRONTEND_TEST_DATABASE === "enterprise_agent_test";
const password = process.env.EA_FRONTEND_DEMO_PASSWORD ?? "DemoOnly!2026";
const titles = {
  agent: "Agent 对话",
  orders: "订单与售后",
  knowledge: "企业知识库",
  analytics: "运营数据分析",
  tasks: "任务历史",
} as const;

function endpoint(response: Response, path: string, method = "GET"): boolean {
  return (
    new URL(response.url()).pathname === path &&
    response.request().method() === method
  );
}

async function login(page: Page, username: string): Promise<void> {
  const logout = page.getByRole("button", { name: "退出登录", exact: true });
  if (await logout.count()) await logout.click();
  else await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "进入业务工作台" }),
  ).toBeVisible();
  await page.locator(".login-form .el-select").click();
  await page
    .getByRole("option", { name: new RegExp(`\\b${username}\\b`) })
    .click();
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await expect(logout).toBeVisible();
}

async function navigate(page: Page, view: keyof typeof titles): Promise<void> {
  await page
    .getByRole("navigation", { name: "主要导航" })
    .getByRole("link", { name: new RegExp(titles[view]) })
    .click();
  await expect(
    page.getByRole("heading", { name: titles[view], exact: true }),
  ).toBeVisible();
}

test.describe("read-only real local page screenshots", () => {
  test.skip(
    !allowed,
    "Requires isolated prepared test data and screenshot mode",
  );

  test("capture existing business pages without model calls or mutations", async ({
    page,
    browser,
    request,
  }, testInfo) => {
    const started = Date.now();
    const captures: Array<Record<string, unknown>> = [];
    const browserErrors: string[] = [];
    const blockedRequests: Array<{ path: string; method: string }> = [];
    let newModelRequests = 0;
    const requests = {
      messages: 0,
      resume: 0,
      recover: 0,
      business_mutations: 0,
    };
    let outcome: "running" | "passed" | "failed" = "running";
    let failure: string | null = null;
    const artifact = testInfo.outputPath("captured.json");
    const selectedTasks: Record<string, { id: string; session_id: string }> =
      {};
    const readonlyPosts = new Set([
      "/api/v1/auth/login",
      "/api/v1/analytics/query",
    ]);

    page.on("pageerror", (error) => browserErrors.push(error.message));
    await page.route("**/api/v1/**", async (route) => {
      const outbound = route.request();
      const path = new URL(outbound.url()).pathname;
      const method = outbound.method();
      if (/\/agent\/sessions\/[^/]+\/messages$/.test(path)) {
        newModelRequests++;
        requests.messages++;
      }
      if (/\/agent\/tasks\/[^/]+\/resume$/.test(path)) requests.resume++;
      if (/\/agent\/tasks\/[^/]+\/recover$/.test(path)) requests.recover++;
      if (
        !["GET", "HEAD", "OPTIONS"].includes(method) &&
        !(method === "POST" && readonlyPosts.has(path))
      ) {
        blockedRequests.push({ path, method });
        requests.business_mutations++;
        await route.abort("blockedbyclient");
        return;
      }
      await route.continue();
    });

    async function persist(): Promise<void> {
      await mkdir(dirname(artifact), { recursive: true });
      await writeFile(
        artifact,
        `${JSON.stringify(
          {
            schema_version: 1,
            evidence_kind: "real_browser_readonly_existing_local_test_data",
            status: outcome,
            failure,
            started_at: new Date(started).toISOString(),
            captured_at: new Date().toISOString(),
            elapsed_ms: Date.now() - started,
            browser_version: browser.version(),
            browser_channel: "msedge",
            test_database: "enterprise_agent_test",
            frontend_origin: "http://127.0.0.1:5174",
            api_origin: "http://127.0.0.1:8001",
            model_enabled_on_test_api: false,
            new_model_requests: newModelRequests,
            requests_from_browser: requests,
            blocked_mutation_requests: blockedRequests,
            browser_errors: browserErrors,
            selected_tasks: selectedTasks,
            captures,
            limitations: [
              "Existing completed model tasks are historical records; no new answer is generated",
              "approval.png shows the historical completed refund task receipt, not a new waiting approval",
              "Login and authorized read-only SQL are the only allowed POST endpoints",
              "No images are edited; each PNG is captured directly from the actual browser page",
            ],
          },
          null,
          2,
        )}\n`,
        "utf8",
      );
    }

    async function capture(
      name: string,
      details: Record<string, unknown> = {},
    ) {
      await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
      await expect(page.locator(".el-message:visible")).toHaveCount(0);
      await page.evaluate(async () => {
        await document.fonts.ready;
        window.scrollTo(0, 0);
      });
      const widths: Array<Record<string, number>> = [];
      for (const width of [1280, 1366, 1440]) {
        await page.setViewportSize({ width, height: 960 });
        await expect
          .poll(() =>
            page.evaluate(() =>
              Math.max(
                document.documentElement.scrollWidth,
                document.body.scrollWidth,
              ),
            ),
          )
          .toBeLessThanOrEqual(width + 1);
        widths.push({
          viewport_width: width,
          body_scroll_width: await page.evaluate(
            () => document.body.scrollWidth,
          ),
          document_scroll_width: await page.evaluate(
            () => document.documentElement.scrollWidth,
          ),
        });
      }
      await page.evaluate(() => window.scrollTo(0, 0));
      const png = await page.screenshot({
        path: testInfo.outputPath(`${name}.png`),
        fullPage: true,
        animations: "disabled",
      });
      captures.push({
        filename: `${name}.png`,
        captured_at: new Date().toISOString(),
        path: new URL(page.url()).pathname,
        viewport: page.viewportSize(),
        png_width: png.readUInt32BE(16),
        png_height: png.readUInt32BE(20),
        scroll_y: await page.evaluate(() => window.scrollY),
        width_checks: widths,
        ...details,
      });
      await persist();
    }

    async function taskDrawer(taskId: string) {
      const row = page.locator(".task-table .el-table__body tr").filter({
        has: page.getByText(taskId, { exact: true }),
      });
      await row.getByRole("button", { name: "详情", exact: true }).click();
      const drawer = page.locator(".el-drawer:visible");
      await expect(drawer.getByTestId("task-status")).toHaveText("已完成");
      return drawer;
    }

    async function discoverTasks(username: string): Promise<AgentTask[]> {
      const origin = "http://127.0.0.1:8001";
      const response = await request.post(`${origin}/api/v1/auth/login`, {
        data: { username, password },
      });
      if (response.status() !== 200)
        throw new Error(
          `Read-only reference login failed, HTTP ${response.status()}`,
        );
      // The actual token is kept in this local scope only; never persist it,
      // headers, login responses, or browser storage in an artifact.
      const body: unknown = await response.json();
      if (
        !body ||
        typeof body !== "object" ||
        !("access_token" in body) ||
        typeof body.access_token !== "string"
      )
        throw new Error("Read-only reference login returned no token");
      const tasks = await request.get(`${origin}/api/v1/agent/tasks?limit=20`, {
        headers: { Authorization: `Bearer ${body.access_token}` },
      });
      if (tasks.status() !== 200)
        throw new Error(
          `Read-only task discovery failed, HTTP ${tasks.status()}`,
        );
      return ((await tasks.json()) as ResultPage<AgentTask>).items;
    }

    function chooseTask(
      tasks: AgentTask[],
      kind: string,
      matches: (task: AgentTask) => boolean,
    ): AgentTask {
      const task = tasks.find(
        (item) => item.status === "completed" && matches(item),
      );
      if (!task)
        throw new Error(
          `No completed ${kind} task in the latest 20 authorized tasks. Run scripts/verify-frontend.ps1 -AllowLive successfully before read-only screenshots.`,
        );
      selectedTasks[kind] = { id: task.id, session_id: task.session_id };
      return task;
    }

    async function selectConversation(task: AgentTask): Promise<void> {
      // A full navigation would clear this application's in-memory login.
      // Select the actual persisted session through its UI, including paging.
      for (let pageNumber = 0; pageNumber < 20; pageNumber++) {
        await expect(
          page.locator(".session-list .el-loading-mask:visible"),
        ).toHaveCount(0);
        const button = page.locator(".session-item").filter({
          hasText: `会话 ${task.session_id.slice(0, 8)}`,
        });
        if (await button.count()) {
          await button.click();
          await expect(
            page.locator(`[data-task-id="${task.id}"]`),
          ).toBeVisible();
          return;
        }
        const next = page.locator(".session-pagination .btn-next");
        if (!(await next.count()) || !(await next.isEnabled())) break;
        const response = page.waitForResponse((value) =>
          endpoint(value, "/api/v1/agent/sessions"),
        );
        await next.click();
        expect((await response).status()).toBe(200);
      }
      throw new Error(
        "The discovered historical session is not accessible in the paginated UI. Run the complete browser workflow before screenshots.",
      );
    }

    try {
      expect(testInfo.project.use.baseURL).toBe("http://127.0.0.1:5174");
      expect(
        (await request.get("http://127.0.0.1:8001/health/ready")).status(),
      ).toBe(200);
      const customerTasks = await discoverTasks("demo_customer");
      const historicalKnowledge = chooseTask(
        customerTasks,
        "knowledge",
        (task) =>
          task.task_type === "knowledge" && !!task.result.citations?.length,
      );
      const historicalRefund = chooseTask(
        customerTasks,
        "refund",
        (task) => !!task.result.refund_result?.refund,
      );
      const historicalAnalytics = chooseTask(
        await discoverTasks("demo_analyst"),
        "analytics",
        (task) => !!task.result.analysis,
      );
      await login(page, "demo_customer");
      await selectConversation(historicalKnowledge);
      const knowledgeTask = page.locator(
        `[data-task-id="${historicalKnowledge.id}"]`,
      );
      await expect(knowledgeTask.getByTestId("task-status")).toHaveText(
        "已完成",
      );
      await expect(
        knowledgeTask.locator(".citation-card").first(),
      ).toBeVisible();
      await knowledgeTask.evaluate((element) =>
        element.scrollIntoView({ block: "start" }),
      );
      await capture("agent", { displayed_task_id: historicalKnowledge.id });
      await knowledgeTask
        .getByRole("button", { name: "核对引用", exact: true })
        .first()
        .click();
      const citationDialog = page.getByRole("dialog", {
        name: "核对引用来源",
        exact: true,
      });
      await expect(citationDialog.locator(".source-id")).toBeVisible();
      await capture("citation", { displayed_task_id: historicalKnowledge.id });
      await citationDialog
        .getByRole("button", { name: "关闭", exact: true })
        .click();
      await expect(citationDialog).not.toBeVisible();

      await navigate(page, "orders");
      await expect(page.locator(".refund-panel")).toContainText("模拟退款完成");
      await capture("orders", { includes_refund_history: true });

      await navigate(page, "tasks");
      await expect(
        page.getByText(historicalKnowledge.id, { exact: true }),
      ).toBeVisible();
      await capture("tasks");
      const drawer = await taskDrawer(historicalRefund.id);
      await capture("approval", {
        displayed_task_id: historicalRefund.id,
        task_state: "completed",
        description: "Historical completed refund application receipt",
      });
      await drawer.locator(".el-drawer__close-btn").click();
      await expect(drawer).not.toBeVisible();

      await login(page, "demo_admin");
      await navigate(page, "knowledge");
      await expect(
        page.locator(".document-table .el-table__body tr"),
      ).toHaveCount(5);
      await capture("knowledge");

      await login(page, "demo_analyst");
      await selectConversation(historicalAnalytics);
      const analyticsTask = page.locator(
        `[data-task-id="${historicalAnalytics.id}"]`,
      );
      await expect(analyticsTask.getByTestId("task-status")).toHaveText(
        "已完成",
      );
      await expect(analyticsTask.getByTestId("analytics-chart")).toBeVisible();
      await capture("agent-analytics", {
        displayed_task_id: historicalAnalytics.id,
      });
      await navigate(page, "analytics");
      await page
        .locator(".query-preset")
        .filter({ hasText: "商品销售排行" })
        .click();
      const analysis = page.waitForResponse((value) =>
        endpoint(value, "/api/v1/analytics/query", "POST"),
      );
      await page.getByRole("button", { name: "执行查询", exact: true }).click();
      expect((await analysis).status()).toBe(200);
      await expect(page.getByTestId("analytics-chart")).toBeVisible();
      await capture("analytics");

      expect(newModelRequests).toBe(0);
      expect(requests).toEqual({
        messages: 0,
        resume: 0,
        recover: 0,
        business_mutations: 0,
      });
      expect(blockedRequests).toEqual([]);
      expect(browserErrors).toEqual([]);
      outcome = "passed";
    } catch (error) {
      outcome = "failed";
      failure = (
        error instanceof Error ? error.message : "Screenshot capture failed"
      )
        .replace(/Bearer\s+[^\s"']+/gi, "Bearer [redacted]")
        .replace(/sk-[a-zA-Z0-9_-]+/g, "[redacted key]")
        .slice(0, 4000);
      throw error;
    } finally {
      await persist();
    }
  });
});
