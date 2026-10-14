import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import {
  expect,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
  type Response,
  type TestInfo,
} from "@playwright/test";
import type { AgentTask, Citation } from "../../src/api/agent-types";
import type {
  AnalyticsCatalog,
  AnalyticsReport,
  Page as ResultPage,
  User,
} from "../../src/api/types";
import type {
  KnowledgeDocument,
  Order,
  Refund,
  RefundEligibility,
} from "../../src/api/business-types";

// This suite is intentionally opt-in: it writes only fictional test business data
// and explicitly submits three tasks to the locally configured real model.
const allowed =
  process.env.EA_FRONTEND_ALLOW_LIVE === "true" &&
  process.env.EA_FRONTEND_TEST_DATABASE === "enterprise_agent_test";
const apiOrigin = "http://127.0.0.1:8001";
const password = process.env.EA_FRONTEND_DEMO_PASSWORD ?? "DemoOnly!2026";
const reuseTaskId = process.env.EA_FRONTEND_REUSE_TASK_ID?.trim() ?? "";

async function checkStatement(scope: Locator): Promise<void> {
  const input = scope.locator('input[type="checkbox"]');
  await expect(input).not.toBeChecked();
  // The actual Element Plus input is hidden; its visible label is clickable.
  await scope.locator(".el-checkbox__label").click();
  await expect(input).toBeChecked();
}

function endpoint(response: Response, path: string, method = "GET"): boolean {
  const url = new URL(response.url());
  return url.pathname === path && response.request().method() === method;
}

function safeFailure(value: unknown): string {
  return (value instanceof Error ? value.message : "Browser workflow failed")
    .replace(/Bearer\s+[^\s"']+/gi, "Bearer [redacted]")
    .replace(/sk-[a-zA-Z0-9_-]+/g, "[redacted key]")
    .slice(0, 4000);
}

async function uiLogin(page: Page, username: string): Promise<void> {
  const logout = page.getByRole("button", { name: "退出登录", exact: true });
  if (await logout.count()) await logout.click();
  else await page.goto("/login");
  await expect(
    page.getByRole("heading", { name: "进入业务工作台" }),
  ).toBeVisible();
  // Element Plus overlays its readonly combobox input with the selected label.
  // Click the control wrapper, the same target a user can normally click.
  await page.locator(".login-form .el-select").click();
  await page
    .getByRole("option", { name: new RegExp(`\\b${username}\\b`) })
    .click();
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await expect(logout).toBeVisible();
  expect(
    await page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    })),
  ).toEqual({ local: 0, session: 0 });
}

const titles = {
  agent: "Agent 对话",
  orders: "订单与售后",
  knowledge: "企业知识库",
  analytics: "运营数据分析",
  tasks: "任务历史",
} as const;
async function navigate(page: Page, view: keyof typeof titles): Promise<void> {
  await page
    .getByRole("navigation", { name: "主要导航" })
    .getByRole("link", { name: new RegExp(titles[view]) })
    .click();
  await expect(
    page.getByRole("heading", { name: titles[view], exact: true }),
  ).toBeVisible();
}

async function apiJson<T>(
  context: APIRequestContext,
  path: string,
): Promise<T> {
  const response = await context.get(path);
  if (response.status() !== 200)
    throw new Error(
      `Read-only test reference failed: ${path}, HTTP ${response.status()}`,
    );
  return (await response.json()) as T;
}

test.describe("real local EnterpriseAgent browser workflow", () => {
  test.describe.configure({ mode: "serial" });
  test.skip(
    !allowed,
    "Requires explicit EA_FRONTEND_ALLOW_LIVE=true and dedicated enterprise_agent_test database",
  );

  test("customer approvals, RAG, staff review, knowledge management and analyst queries", async ({
    page,
    request,
    playwright,
  }, testInfo: TestInfo) => {
    test.setTimeout(480_000);
    expect(testInfo.project.use.baseURL).toBe("http://127.0.0.1:5174");
    if (
      reuseTaskId &&
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        reuseTaskId,
      )
    )
      throw new Error("EA_FRONTEND_REUSE_TASK_ID must be a task UUID");
    const started = Date.now();
    const contexts: APIRequestContext[] = [];
    const latestTasks = new Map<string, AgentTask>();
    const responseReads = new Set<Promise<void>>();
    const messageTaskIds = new Set<string>();
    const browserErrors: string[] = [];
    const counters = {
      messages: 0,
      resume: 0,
      recover: 0,
      refund_confirmation: 0,
    };
    const evidence: Array<{
      step: string;
      checked_at: string;
      details: unknown;
    }> = [];
    const screenshots: string[] = [];
    let outcome: "running" | "passed" | "failed" = "running";
    let failure: string | null = null;
    let writer = Promise.resolve();
    let reusedStartingTokens: number | null = null;
    const artifact = testInfo.outputPath("live-results.json");

    function taskSummary(task: AgentTask) {
      return {
        id: task.id,
        session_id: task.session_id,
        task_type: task.task_type,
        status: task.status,
        error_code: task.error_code,
        elapsed_ms: task.elapsed_ms,
        token_usage: {
          model_calls: task.token_usage.model_calls ?? null,
          prompt_tokens: task.token_usage.prompt_tokens ?? null,
          completion_tokens: task.token_usage.completion_tokens ?? null,
          total_tokens: task.token_usage.total_tokens ?? null,
          unknown_usage_calls: task.token_usage.unknown_usage_calls ?? null,
          usage_complete: task.token_usage.usage_complete ?? false,
          interrupted_usage_unknown:
            task.token_usage.interrupted_usage_unknown ?? false,
        },
        steps: task.steps.map((step) => ({
          node: step.node,
          tool: step.tool,
          attempt: step.attempt,
          elapsed_ms: step.elapsed_ms,
          error_code: step.error_code,
        })),
        citation_count: task.result.citations?.length ?? 0,
        approval_id: task.result.approval?.approval_id ?? null,
        refund: task.result.refund_result?.refund
          ? {
              id: task.result.refund_result.refund.id,
              order_no: task.result.refund_result.refund.order_no,
              status: task.result.refund_result.refund.status,
              amount_cents: task.result.refund_result.refund.amount_cents,
            }
          : null,
        analysis: task.result.analysis
          ? {
              metric_ids: task.result.analysis.metric_ids,
              columns: task.result.analysis.query.columns,
              rows: task.result.analysis.query.rows,
              normalized_sql: task.result.analysis.query.normalized_sql,
              truncated: task.result.analysis.query.truncated,
            }
          : null,
      };
    }

    function snapshot() {
      const tasks = [...latestTasks.values()];
      const sum = (
        key:
          | "model_calls"
          | "prompt_tokens"
          | "completion_tokens"
          | "total_tokens"
          | "unknown_usage_calls",
      ) =>
        tasks.reduce((total, task) => {
          const value = task.token_usage[key];
          return (
            total +
            (typeof value === "number" &&
            Number.isSafeInteger(value) &&
            value >= 0
              ? value
              : 0)
          );
        }, 0);
      const incompleteTaskIds = tasks
        .filter(
          (task) =>
            task.token_usage.usage_complete !== true ||
            task.token_usage.interrupted_usage_unknown === true ||
            (task.token_usage.unknown_usage_calls ?? 0) > 0,
        )
        .map((task) => task.id);
      const unobservedMessages = Math.max(
        0,
        counters.messages - messageTaskIds.size,
      );
      const complete =
        incompleteTaskIds.length === 0 &&
        unobservedMessages === 0 &&
        tasks.length > 0;
      return {
        schema_version: 1,
        evidence_kind: "real_browser_real_api_real_model",
        reused_task_id: reuseTaskId || null,
        test_database: "enterprise_agent_test",
        api_origin: apiOrigin,
        frontend_origin: "http://127.0.0.1:5174",
        started_at: new Date(started).toISOString(),
        updated_at: new Date().toISOString(),
        elapsed_ms: Date.now() - started,
        status: outcome,
        failure,
        requests_from_browser: { ...counters },
        model_usage: {
          deduplication:
            "Latest observed DTO for each unique task ID; resume responses replace earlier snapshots",
          observed_task_count: tasks.length,
          model_attempts: sum("model_calls"),
          provider_known_prompt_tokens: sum("prompt_tokens"),
          provider_known_completion_tokens: sum("completion_tokens"),
          provider_known_total_tokens: sum("total_tokens"),
          reused_starting_provider_known_total_tokens: reusedStartingTokens,
          token_scope:
            "Cumulative unique-task ledger, including prior usage of an explicitly reused task",
          unknown_usage_calls: sum("unknown_usage_calls"),
          usage_complete: complete,
          actual_total_tokens: complete ? sum("total_tokens") : null,
          incomplete_task_ids: incompleteTaskIds,
          messages_without_observed_task_dto: unobservedMessages,
        },
        tasks: tasks.map(taskSummary),
        evidence: [...evidence],
        screenshots: [...screenshots],
        browser_errors: [...browserErrors],
        limitations: [
          "Three guided development smoke tasks; not a blind accuracy benchmark",
          "Read-only reference queries execute separately and do not share one database snapshot",
          "All mutations target fictional local test data; simulated refund completion does not call a payment provider",
        ],
      };
    }

    function persist(): Promise<void> {
      const text = `${JSON.stringify(snapshot(), null, 2)}\n`;
      writer = writer.then(async () => {
        await mkdir(dirname(artifact), { recursive: true });
        await writeFile(artifact, text, "utf8");
      });
      return writer;
    }
    async function checked(step: string, details: unknown): Promise<void> {
      evidence.push({ step, checked_at: new Date().toISOString(), details });
      await persist();
    }
    async function capture(name: string): Promise<void> {
      await page.screenshot({
        path: testInfo.outputPath(`${name}.png`),
        fullPage: true,
      });
      screenshots.push(`${name}.png`);
      await persist();
    }
    async function remember(task: AgentTask): Promise<void> {
      latestTasks.set(task.id, task);
      await persist();
    }
    const responseListener = (response: Response) => {
      const path = new URL(response.url()).pathname;
      if (
        response.request().method() !== "POST" ||
        !/\/api\/v1\/agent\/(?:sessions\/[^/]+\/messages|tasks\/[^/]+\/(?:resume|recover))$/.test(
          path,
        )
      )
        return;
      const reading = (async () => {
        try {
          const task = (await response.json()) as AgentTask;
          if (
            typeof task.id === "string" &&
            typeof task.status === "string" &&
            task.token_usage &&
            task.result &&
            Array.isArray(task.steps)
          ) {
            if (path.endsWith("/messages")) messageTaskIds.add(task.id);
            await remember(task);
          }
        } catch (error) {
          evidence.push({
            step: "task_response_capture_failed",
            checked_at: new Date().toISOString(),
            details: { path, error: safeFailure(error) },
          });
          await persist();
        }
      })();
      responseReads.add(reading);
      void reading.finally(() => responseReads.delete(reading));
    };
    page.on("response", responseListener);
    page.on("request", (outbound) => {
      if (outbound.method() !== "POST") return;
      const path = new URL(outbound.url()).pathname;
      if (/\/agent\/sessions\/[^/]+\/messages$/.test(path)) counters.messages++;
      else if (/\/agent\/tasks\/[^/]+\/resume$/.test(path)) counters.resume++;
      else if (/\/agent\/tasks\/[^/]+\/recover$/.test(path)) counters.recover++;
      else if (path === "/api/v1/refunds") counters.refund_confirmation++;
    });
    page.on("pageerror", (error) => {
      browserErrors.push(safeFailure(error));
      void persist();
    });

    async function actualLogin(
      username: string,
    ): Promise<{ context: APIRequestContext; user: User }> {
      const response = await request.post(`${apiOrigin}/api/v1/auth/login`, {
        data: { username, password },
      });
      if (response.status() !== 200)
        throw new Error(
          `Reference identity login failed, HTTP ${response.status()}`,
        );
      const body = (await response.json()) as { access_token: string };
      if (typeof body.access_token !== "string")
        throw new Error("Reference login did not return an access token");
      const context = await playwright.request.newContext({
        baseURL: apiOrigin,
        extraHTTPHeaders: { Authorization: `Bearer ${body.access_token}` },
        timeout: 30_000,
      });
      contexts.push(context);
      return { context, user: await apiJson<User>(context, "/api/v1/auth/me") };
    }
    async function newSession(): Promise<string> {
      const response = page.waitForResponse((value) =>
        endpoint(value, "/api/v1/agent/sessions", "POST"),
      );
      await page.getByTestId("new-session").click();
      const sessionResponse = await response;
      expect(sessionResponse.status()).toBe(201);
      const session = (await sessionResponse.json()) as { id: string };
      await expect(page.locator(".session-item.active")).toContainText(
        session.id.slice(0, 8),
      );
      await expect(page.locator("textarea#agent-message")).toBeEnabled();
      return session.id;
    }
    async function sendTask(
      sessionId: string,
      message: string,
    ): Promise<AgentTask> {
      await page.locator("textarea#agent-message").fill(message);
      await expect(page.getByTestId("send-message")).toBeEnabled();
      const response = page.waitForResponse(
        (value) =>
          endpoint(
            value,
            `/api/v1/agent/sessions/${sessionId}/messages`,
            "POST",
          ),
        { timeout: 180_000 },
      );
      await page.getByTestId("send-message").click();
      const result = await response;
      expect(result.status()).toBe(200);
      const task = (await result.json()) as AgentTask;
      messageTaskIds.add(task.id);
      await remember(task);
      await expect(page.locator(`[data-task-id="${task.id}"]`)).toBeVisible();
      return task;
    }
    async function queryOrder(number: string): Promise<Response> {
      await page
        .getByRole("textbox", { name: "订单号", exact: true })
        .fill(number);
      const response = page.waitForResponse((value) =>
        endpoint(value, `/api/v1/orders/${number}`),
      );
      await page.getByRole("button", { name: "查询订单", exact: true }).click();
      return await response;
    }
    async function closeDrawer(): Promise<void> {
      const drawer = page.locator(".el-drawer:visible");
      await drawer.locator(".el-drawer__close-btn").click();
      await expect(drawer).not.toBeVisible();
    }
    async function executeTemplate(
      analyst: APIRequestContext,
      label: string,
    ): Promise<AnalyticsReport> {
      await page.locator(".query-preset").filter({ hasText: label }).click();
      const response = page.waitForResponse((value) =>
        endpoint(value, "/api/v1/analytics/query", "POST"),
      );
      await page.getByRole("button", { name: "执行查询", exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(200);
      const report = (await result.json()) as AnalyticsReport;
      expect(report.query.row_count).toBeGreaterThan(0);
      expect(report.query.truncated).toBe(false);
      expect(report.chart?.source).toBe("query_rows");
      await expect(page.getByTestId("analytics-chart")).toBeVisible();
      const referenceResponse = await analyst.post("/api/v1/analytics/query", {
        data: result.request().postDataJSON(),
      });
      expect(referenceResponse.status()).toBe(200);
      const reference = (await referenceResponse.json()) as AnalyticsReport;
      expect(report.query.columns).toEqual(reference.query.columns);
      expect(report.query.rows).toEqual(reference.query.rows);
      await checked(`analytics_template_${label}`, {
        columns: report.query.columns,
        row_count: report.query.row_count,
        rows: report.query.rows,
        chart_type: report.chart?.type,
      });
      return report;
    }

    await persist();
    try {
      const ready = await request.get(`${apiOrigin}/health/ready`);
      expect(ready.status()).toBe(200);
      const health = (await ready.json()) as {
        status: string;
        version: string;
      };
      expect(health.status).toBe("ok");
      const customer = await actualLogin("demo_customer");
      const existingTask = reuseTaskId
        ? await apiJson<AgentTask>(
            customer.context,
            `/api/v1/agent/tasks/${reuseTaskId}`,
          )
        : null;
      if (existingTask) {
        expect(existingTask.status).toBe("waiting_approval");
        expect(existingTask.result.approval?.reason).toBe("change_mind");
        expect(
          new Date(existingTask.result.approval!.expires_at).getTime(),
        ).toBeGreaterThan(Date.now());
        reusedStartingTokens = existingTask.token_usage.total_tokens ?? null;
        await remember(existingTask);
      }
      const other = await actualLogin("demo_other");
      const ownOrders = await apiJson<ResultPage<Order>>(
        customer.context,
        "/api/v1/orders?limit=100",
      );
      const otherOrders = await apiJson<ResultPage<Order>>(
        other.context,
        "/api/v1/orders?limit=100",
      );
      expect(ownOrders.items.length).toBeGreaterThan(0);
      expect(
        ownOrders.items.every((order) => order.user_id === customer.user.id),
      ).toBe(true);
      const foreign = otherOrders.items.find(
        (order) => !ownOrders.items.some((own) => own.id === order.id),
      );
      if (!foreign)
        throw new Error(
          "Dedicated seed has no second-customer order for an ownership check",
        );
      const beforeRefunds = await apiJson<ResultPage<Refund>>(
        customer.context,
        "/api/v1/refunds?limit=100",
      );
      const activeOrders = new Set(
        beforeRefunds.items
          .filter((refund) =>
            ["pending_review", "approved", "completed_simulated"].includes(
              refund.status,
            ),
          )
          .map((refund) => refund.order_no),
      );
      let candidate: Order | undefined;
      let eligibility: RefundEligibility | undefined;
      for (const order of ownOrders.items.filter(
        (order) =>
          !existingTask ||
          order.order_no === existingTask.result.approval?.order_no,
      )) {
        if (activeOrders.has(order.order_no)) continue;
        const result = await apiJson<RefundEligibility>(
          customer.context,
          `/api/v1/orders/${order.order_no}/refund-eligibility?reason=change_mind`,
        );
        if (result.eligible) {
          candidate = order;
          eligibility = result;
          break;
        }
      }
      if (!candidate || !eligibility?.policy)
        throw new Error(
          "Dedicated seed has no currently eligible order without an active refund",
        );
      const order = candidate;
      const policy = eligibility.policy;
      await checked("dedicated_environment_ready", {
        version: health.version,
        own_order_count: ownOrders.total,
        eligible_order_no: order.order_no,
        foreign_order_no: foreign.order_no,
      });

      await uiLogin(page, "demo_customer");
      await navigate(page, "analytics");
      await expect(
        page.getByText("当前身份不能访问运营指标", { exact: true }),
      ).toBeVisible();
      expect(
        (await customer.context.get("/api/v1/analytics/catalog")).status(),
      ).toBe(403);
      await navigate(page, "orders");
      const deniedOrder = await queryOrder(foreign.order_no);
      expect(deniedOrder.status()).toBe(404);
      const deniedDrawer = page.locator(".el-drawer:visible");
      await expect(deniedDrawer.locator(".el-alert--error")).toBeVisible();
      await expect(deniedDrawer.locator(".detail-heading")).toHaveCount(0);
      await closeDrawer();
      expect((await queryOrder(order.order_no)).status()).toBe(200);
      const drawer = page.locator(".el-drawer:visible");
      await expect(drawer.locator(".detail-heading")).toContainText(
        order.order_no,
      );
      const eligibilityResponse = page.waitForResponse((value) =>
        endpoint(value, `/api/v1/orders/${order.order_no}/refund-eligibility`),
      );
      await drawer
        .getByRole("button", { name: "检查退款资格", exact: true })
        .click();
      expect((await eligibilityResponse).status()).toBe(200);
      await expect(
        drawer.getByRole("button", { name: "生成待确认申请", exact: true }),
      ).toBeEnabled();
      const confirmCount = counters.refund_confirmation;
      const previewResponse = page.waitForResponse((value) =>
        endpoint(
          value,
          `/api/v1/orders/${order.order_no}/refund-preview`,
          "POST",
        ),
      );
      await drawer
        .getByRole("button", { name: "生成待确认申请", exact: true })
        .click();
      expect((await previewResponse).status()).toBe(201);
      const previewDialog = page.getByRole("dialog", {
        name: "核对退款申请",
        exact: true,
      });
      await expect(previewDialog).toBeVisible();
      await expect(
        previewDialog.getByRole("button", {
          name: "确认提交申请",
          exact: true,
        }),
      ).toBeDisabled();
      expect(counters.refund_confirmation).toBe(confirmCount);
      await previewDialog
        .getByRole("button", { name: "稍后处理", exact: true })
        .click();
      await expect(previewDialog).not.toBeVisible();
      expect(counters.refund_confirmation).toBe(confirmCount);
      await closeDrawer();
      await page.getByRole("button", { name: "继续处理", exact: true }).click();
      const rejectionResponse = page.waitForResponse((value) =>
        endpoint(value, "/api/v1/refunds", "POST"),
      );
      await previewDialog
        .getByRole("button", { name: "拒绝申请", exact: true })
        .click();
      const rejection = await rejectionResponse;
      expect(rejection.status()).toBe(200);
      expect(await rejection.json()).toMatchObject({
        status: "rejected",
        refund: null,
      });
      await expect(previewDialog).not.toBeVisible();
      expect(counters.refund_confirmation).toBe(confirmCount + 1);
      expect(
        (
          await apiJson<ResultPage<Refund>>(
            customer.context,
            "/api/v1/refunds?limit=100",
          )
        ).total,
      ).toBe(beforeRefunds.total);
      await checked("customer_ownership_and_explicit_preview_rejection", {
        own_order_no: order.order_no,
        foreign_order_no: foreign.order_no,
        foreign_status: 404,
        created_refunds: 0,
        confirmation_posts: 1,
      });
      await capture("orders");

      await navigate(page, "agent");
      let customerSession: string;
      let refundTask: AgentTask;
      if (existingTask) {
        customerSession = existingTask.session_id;
        refundTask = existingTask;
        await page
          .locator(".session-item")
          .filter({ hasText: customerSession.slice(0, 8) })
          .click();
        await expect(
          page
            .locator(`[data-task-id="${existingTask.id}"]`)
            .getByTestId("task-status"),
        ).toHaveText("待人工确认");
        await checked("existing_checkpoint_reused_after_server_restart", {
          task_id: existingTask.id,
          session_id: customerSession,
          approval_id: existingTask.result.approval?.approval_id,
          existing_provider_known_total_tokens: reusedStartingTokens,
          new_refund_model_submissions: 0,
        });
      } else {
        customerSession = await newSession();
        refundTask = await sendTask(
          customerSession,
          `帮我为订单 ${order.order_no} 创建退款申请。退款原因是商品不想要了。`,
        );
      }
      expect(refundTask.status).toBe("waiting_approval");
      expect(refundTask.result.approval?.order_no).toBe(order.order_no);
      expect(refundTask.result.approval?.amount_cents).toBe(
        eligibility.amount_cents,
      );
      expect(
        (
          await apiJson<ResultPage<Refund>>(
            customer.context,
            "/api/v1/refunds?limit=100",
          )
        ).total,
      ).toBe(beforeRefunds.total);
      const taskPanel = page.locator(`[data-task-id="${refundTask.id}"]`);
      await expect(taskPanel.getByTestId("task-status")).toHaveText(
        "待人工确认",
      );
      const resumeBefore = counters.resume;
      await taskPanel.getByTestId("review-approval").click();
      const approvalDialog = page.getByRole("dialog", {
        name: "确认退款申请",
        exact: true,
      });
      await expect(
        approvalDialog.getByTestId("confirm-approval"),
      ).toBeDisabled();
      expect(counters.resume).toBe(resumeBefore);
      await capture("approval");
      await checkStatement(approvalDialog.getByTestId("approval-checkbox"));
      const commitResponse = page.waitForResponse(
        (value) =>
          endpoint(
            value,
            `/api/v1/agent/tasks/${refundTask.id}/resume`,
            "POST",
          ),
        { timeout: 180_000 },
      );
      await approvalDialog.getByTestId("confirm-approval").click();
      const committedResponse = await commitResponse;
      expect(committedResponse.status()).toBe(200);
      const committedTask = (await committedResponse.json()) as AgentTask;
      await remember(committedTask);
      expect(committedTask.status).toBe("completed");
      expect(committedTask.result.refund_result?.refund?.status).toBe(
        "pending_review",
      );
      const createdRefund = committedTask.result.refund_result?.refund;
      if (!createdRefund)
        throw new Error(
          "Explicit Agent confirmation did not return a refund application",
        );
      expect(createdRefund.order_no).toBe(order.order_no);
      await expect(approvalDialog).not.toBeVisible();
      await expect(taskPanel.getByTestId("refund-receipt")).toContainText(
        "pending_review",
      );
      expect(counters.resume).toBe(resumeBefore + 1);
      expect(
        (
          await apiJson<Order>(
            customer.context,
            `/api/v1/orders/${order.order_no}`,
          )
        ).status,
      ).toBe(order.status);
      expect(
        (
          await apiJson<Refund>(
            customer.context,
            `/api/v1/refunds/${createdRefund.id}`,
          )
        ).status,
      ).toBe("pending_review");
      await checked("agent_refund_explicit_confirmation", {
        task_id: committedTask.id,
        refund_id: createdRefund.id,
        refund_status: "pending_review",
        order_status_unchanged: true,
        resume_posts: 1,
      });

      const knowledgeTask = await sendTask(
        customerSession,
        "平台优惠券可以叠加使用吗？请依据企业知识库政策回答并给出来源。",
      );
      expect(knowledgeTask.status).toBe("completed");
      expect(knowledgeTask.result.citations?.length).toBeGreaterThan(0);
      const historicalCitation = knowledgeTask.result.citations![0]!;
      const knowledgePanel = page.locator(
        `[data-task-id="${knowledgeTask.id}"]`,
      );
      await expect(knowledgePanel.getByTestId("task-status")).toHaveText(
        "已完成",
      );
      const citationResponse = page.waitForResponse((value) =>
        endpoint(
          value,
          `/api/v1/knowledge/citations/${historicalCitation.chunk_id}`,
        ),
      );
      await knowledgePanel
        .locator(".citation-topline")
        .first()
        .getByRole("button", { name: "核验来源", exact: true })
        .click();
      const currentCitationResponse = await citationResponse;
      expect(currentCitationResponse.status()).toBe(200);
      const currentCitation =
        (await currentCitationResponse.json()) as Citation;
      expect(currentCitation.document_id).toBe(historicalCitation.document_id);
      expect(currentCitation.text).toBe(historicalCitation.text);
      const citationDialog = page.getByRole("dialog", {
        name: "核验知识来源",
        exact: true,
      });
      await expect(citationDialog.locator(".source-id")).toHaveText(
        historicalCitation.chunk_id,
      );
      await citationDialog
        .getByRole("button", { name: "关闭", exact: true })
        .click();
      await expect(citationDialog).not.toBeVisible();
      await knowledgePanel.scrollIntoViewIfNeeded();
      await checked("agent_real_rag_and_current_source_verification", {
        task_id: knowledgeTask.id,
        citation_count: knowledgeTask.result.citations?.length,
        verified_chunk_id: currentCitation.chunk_id,
        document_version: currentCitation.document_version,
      });
      await capture("agent");
      await page.getByRole("link", { name: "执行历史", exact: true }).click();
      await expect(
        page.getByRole("heading", { name: "任务历史", exact: true }),
      ).toBeVisible();
      await expect(page.locator("input#task-session-filter")).toHaveValue(
        customerSession,
      );
      const historyRow = page
        .locator(".task-table .el-table__body tr")
        .filter({ has: page.getByText(knowledgeTask.id, { exact: true }) });
      await expect(historyRow).toContainText("已完成");
      await historyRow
        .getByRole("button", { name: "详情", exact: true })
        .click();
      const historyDrawer = page.locator(".el-drawer:visible");
      await expect(historyDrawer.getByTestId("task-status")).toHaveText(
        "已完成",
      );
      expect(
        await historyDrawer.locator(".step-list li").count(),
      ).toBeGreaterThan(0);
      await capture("tasks");
      await closeDrawer();
      await checked("persisted_multiturn_task_history", {
        session_id: customerSession,
        displayed_task_id: knowledgeTask.id,
      });

      await uiLogin(page, "demo_support");
      await navigate(page, "orders");
      const refundRow = (action: string) =>
        page
          .locator(".refund-panel .el-table__body tr")
          .filter({
            has: page.getByRole("button", {
              name: order.order_no,
              exact: true,
            }),
          })
          .filter({
            has: page.getByRole("button", { name: action, exact: true }),
          });
      await expect(refundRow("批准")).toContainText("待人工审核");
      await refundRow("批准")
        .getByRole("button", { name: "批准", exact: true })
        .click();
      const staffDialog = page.getByRole("dialog", {
        name: "批准退款申请",
        exact: true,
      });
      await expect(
        staffDialog.getByRole("button", {
          name: "确认批准退款申请",
          exact: true,
        }),
      ).toBeDisabled();
      await staffDialog
        .getByPlaceholder("记录本次审核的依据")
        .fill("浏览器验收：已核对模拟订单事实与知识库政策。");
      await checkStatement(staffDialog);
      const approveResponse = page.waitForResponse((value) =>
        endpoint(value, `/api/v1/refunds/${createdRefund.id}/review`, "POST"),
      );
      await staffDialog
        .getByRole("button", { name: "确认批准退款申请", exact: true })
        .click();
      const approved = await approveResponse;
      expect(approved.status()).toBe(200);
      expect(((await approved.json()) as Refund).status).toBe("approved");
      await expect(staffDialog).not.toBeVisible();
      await refundRow("模拟完成")
        .getByRole("button", { name: "模拟完成", exact: true })
        .click();
      const completeDialog = page.getByRole("dialog", {
        name: "模拟完成退款",
        exact: true,
      });
      await expect(
        completeDialog.getByRole("button", {
          name: "确认模拟完成退款",
          exact: true,
        }),
      ).toBeDisabled();
      await checkStatement(completeDialog);
      const completeResponse = page.waitForResponse((value) =>
        endpoint(
          value,
          `/api/v1/refunds/${createdRefund.id}/complete-simulated`,
          "POST",
        ),
      );
      await completeDialog
        .getByRole("button", { name: "确认模拟完成退款", exact: true })
        .click();
      const completed = await completeResponse;
      expect(completed.status()).toBe(200);
      expect(((await completed.json()) as Refund).status).toBe(
        "completed_simulated",
      );
      await expect(completeDialog).not.toBeVisible();
      expect(
        (
          await apiJson<Refund>(
            customer.context,
            `/api/v1/refunds/${createdRefund.id}`,
          )
        ).status,
      ).toBe("completed_simulated");
      await checked("staff_review_then_explicit_simulated_completion", {
        refund_id: createdRefund.id,
        final_status: "completed_simulated",
        completion_scope:
          "Simulated business record only; no payment integration exists",
      });

      await uiLogin(page, "demo_admin");
      await navigate(page, "knowledge");
      await page
        .getByRole("textbox", { name: "知识检索问题", exact: true })
        .fill("平台优惠券可以叠加使用吗？");
      const searchResponse = page.waitForResponse((value) =>
        endpoint(value, "/api/v1/knowledge/search", "POST"),
      );
      await page
        .getByRole("button", { name: "检索知识库", exact: true })
        .click();
      const search = await searchResponse;
      expect(search.status()).toBe(200);
      expect(await search.json()).toMatchObject({
        status: "evidence_found",
        mode: "hybrid_rerank",
        llm_calls: 0,
      });
      const uniqueTitle = `浏览器验收模拟资料-${randomUUID().slice(0, 8)}`;
      await page.getByRole("button", { name: "上传文档", exact: true }).click();
      const uploadDialog = page.getByRole("dialog", {
        name: "上传知识文档",
        exact: true,
      });
      await uploadDialog
        .getByPlaceholder("输入准确、易识别的知识标题")
        .fill(uniqueTitle);
      await uploadDialog.locator('input[type="file"]').setInputFiles({
        name: `${uniqueTitle}.md`,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# ${uniqueTitle}\n\n本资料用于 EnterpriseAgent 本地电商业务演示的浏览器验收。\n所有商品和客户均为模拟数据；本文不修改已发布的退款政策。\n`,
          "utf8",
        ),
      });
      const uploadResponse = page.waitForResponse(
        (value) => endpoint(value, "/api/v1/knowledge/documents", "POST"),
        { timeout: 120_000 },
      );
      await uploadDialog
        .getByRole("button", { name: "确认上传并索引", exact: true })
        .click();
      const uploadedResponse = await uploadResponse;
      expect(uploadedResponse.status()).toBe(201);
      const uploaded = (await uploadedResponse.json()) as KnowledgeDocument;
      expect(uploaded.status).toBe("indexed");
      expect(uploaded.chunk_count).toBeGreaterThan(0);
      await expect(uploadDialog).not.toBeVisible();
      const documentRow = () =>
        page
          .locator(".el-table__body tr")
          .filter({ has: page.getByText(uniqueTitle, { exact: true }) });
      await expect(documentRow()).toContainText("已索引");
      await capture("knowledge");
      await documentRow()
        .getByRole("button", { name: "重新索引", exact: true })
        .click();
      const reindexDialog = page.getByRole("dialog", {
        name: "确认重新索引",
        exact: true,
      });
      await expect(
        reindexDialog.getByRole("button", {
          name: "确认重新索引",
          exact: true,
        }),
      ).toBeDisabled();
      await checkStatement(reindexDialog);
      const reindexResponse = page.waitForResponse(
        (value) =>
          endpoint(
            value,
            `/api/v1/knowledge/documents/${uploaded.id}/reindex`,
            "POST",
          ),
        { timeout: 120_000 },
      );
      await reindexDialog
        .getByRole("button", { name: "确认重新索引", exact: true })
        .click();
      const reindexedResponse = await reindexResponse;
      expect(reindexedResponse.status()).toBe(200);
      const reindexed = (await reindexedResponse.json()) as KnowledgeDocument;
      expect(reindexed.status).toBe("indexed");
      expect(reindexed.index_generation).toBeGreaterThan(
        uploaded.index_generation,
      );
      await expect(reindexDialog).not.toBeVisible();
      await documentRow()
        .getByRole("button", { name: "删除", exact: true })
        .click();
      const deleteDialog = page.getByRole("dialog", {
        name: "确认删除文档",
        exact: true,
      });
      await checkStatement(deleteDialog);
      const deleteResponse = page.waitForResponse((value) =>
        endpoint(value, `/api/v1/knowledge/documents/${uploaded.id}`, "DELETE"),
      );
      await deleteDialog
        .getByRole("button", { name: "确认删除文档", exact: true })
        .click();
      expect((await deleteResponse).status()).toBe(204);
      await expect(deleteDialog).not.toBeVisible();
      await expect(documentRow()).toHaveCount(0);
      const protectedRow = page.locator(".el-table__body tr").filter({
        has: page.getByText(policy.document_title, { exact: true }),
      });
      await protectedRow
        .getByRole("button", { name: "删除", exact: true })
        .click();
      const protectedDialog = page.getByRole("dialog", {
        name: "确认删除文档",
        exact: true,
      });
      await checkStatement(protectedDialog);
      const protectedResponse = page.waitForResponse((value) =>
        endpoint(
          value,
          `/api/v1/knowledge/documents/${policy.document_id}`,
          "DELETE",
        ),
      );
      await protectedDialog
        .getByRole("button", { name: "确认删除文档", exact: true })
        .click();
      const protectedDeletion = await protectedResponse;
      expect(protectedDeletion.status()).toBe(409);
      expect(await protectedDeletion.json()).toMatchObject({
        error: { code: "policy_source_protected" },
      });
      await expect(protectedDialog).toContainText("policy_source_protected");
      await protectedDialog
        .getByRole("button", { name: "取消", exact: true })
        .click();
      await checked("admin_real_upload_reindex_delete_and_protected_policy", {
        uploaded_document_id: uploaded.id,
        initial_generation: uploaded.index_generation,
        reindexed_generation: reindexed.index_generation,
        delete_status: 204,
        policy_document_id: policy.document_id,
        protected_delete_status: 409,
      });

      const analyst = await actualLogin("demo_analyst");
      await uiLogin(page, "demo_analyst");
      await navigate(page, "orders");
      await expect(
        page.getByRole("heading", {
          name: "当前角色无权访问订单",
          exact: true,
        }),
      ).toBeVisible();
      expect((await analyst.context.get("/api/v1/orders")).status()).toBe(403);
      await navigate(page, "analytics");
      const catalog = await apiJson<AnalyticsCatalog>(
        analyst.context,
        "/api/v1/analytics/catalog",
      );
      for (const label of [
        "最近七天销售额",
        "本月退款率",
        "两周订单量对比",
        "商品销售排行",
      ])
        await executeTemplate(analyst.context, label);
      await capture("analytics");
      await page
        .getByRole("textbox", { name: "只读 SQL", exact: true })
        .fill("SELECT id FROM public.orders");
      const blockedResponse = page.waitForResponse((value) =>
        endpoint(value, "/api/v1/analytics/query", "POST"),
      );
      await page.getByRole("button", { name: "执行查询", exact: true }).click();
      const blocked = await blockedResponse;
      expect(blocked.status()).toBeGreaterThanOrEqual(400);
      expect(blocked.status()).toBeLessThan(500);
      const blockedBody = (await blocked.json()) as {
        error: { code: string };
        request_id: string;
      };
      await expect(page.locator(".el-alert--error")).toContainText("请求 ID");
      await checked("analyst_backend_and_ui_authorization_and_sql_rejection", {
        orders_status: 403,
        raw_table_sql_status: blocked.status(),
        raw_table_error_code: blockedBody.error.code,
      });
      const guidedQuestion =
        "统计最近七天（包含今天）的每日支付销售额。请按业务日期 business_date 升序返回 business_date 和 SUM(gross_revenue_cents) AS sales_cents，按 business_date 分组，指标使用 paid_sales，日期范围遵循目录。";
      const messagesBeforeNavigation = counters.messages;
      await page
        .getByRole("textbox", { name: "分析问题", exact: true })
        .fill(guidedQuestion);
      await page
        .getByRole("button", { name: "前往 Agent 对话", exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: "Agent 对话", exact: true }),
      ).toBeVisible();
      await expect(page.locator("textarea#agent-message")).toHaveValue(
        guidedQuestion,
      );
      const analystSession = await newSession();
      await expect(page.locator("textarea#agent-message")).toHaveValue(
        guidedQuestion,
      );
      expect(counters.messages).toBe(messagesBeforeNavigation);
      const analyticsTask = await sendTask(analystSession, guidedQuestion);
      expect(analyticsTask.status).toBe("completed");
      expect(analyticsTask.task_type).toBe("analytics");
      expect(analyticsTask.result.analysis?.metric_ids).toEqual(["paid_sales"]);
      const window = catalog.date_windows.last_seven_days_including_today!;
      const referenceResponse = await analyst.context.post(
        "/api/v1/analytics/query",
        {
          data: {
            sql: `SELECT business_date, SUM(gross_revenue_cents) AS sales_cents FROM ea_analytics.daily_orders WHERE business_date >= DATE '${window.start_inclusive}' AND business_date < DATE '${window.end_exclusive}' GROUP BY business_date ORDER BY business_date`,
            metric_ids: ["paid_sales"],
          },
        },
      );
      expect(referenceResponse.status()).toBe(200);
      const reference = (await referenceResponse.json()) as AnalyticsReport;
      expect(analyticsTask.result.analysis?.query.columns).toEqual(
        reference.query.columns,
      );
      expect(analyticsTask.result.analysis?.query.rows).toEqual(
        reference.query.rows,
      );
      const analyticsPanel = page.locator(
        `[data-task-id="${analyticsTask.id}"]`,
      );
      await expect(analyticsPanel.getByTestId("task-status")).toHaveText(
        "已完成",
      );
      await expect(analyticsPanel.getByTestId("analytics-chart")).toBeVisible();
      await checked("natural_analytics_explicit_send_and_exact_reference", {
        task_id: analyticsTask.id,
        navigation_model_submissions: 0,
        columns: reference.query.columns,
        rows: reference.query.rows,
      });
      await capture("agent-analytics");
      await Promise.allSettled([...responseReads]);
      expect(counters.messages).toBe(existingTask ? 2 : 3);
      expect(counters.resume).toBe(1);
      expect(counters.recover).toBe(0);
      expect(latestTasks.size).toBe(3);
      expect(
        [...latestTasks.values()].reduce(
          (total, task) => total + (task.token_usage.model_calls ?? 0),
          0,
        ),
      ).toBeGreaterThanOrEqual(3);
      expect(browserErrors).toEqual([]);
      outcome = "passed";
      await persist();
    } catch (error) {
      outcome = "failed";
      failure = safeFailure(error);
      await persist();
      throw error;
    } finally {
      await Promise.allSettled([...responseReads]);
      await persist();
      page.off("response", responseListener);
      await Promise.allSettled(contexts.map((context) => context.dispose()));
    }
  });
});
