import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TaskDetail from "../../src/components/TaskDetail.vue";
import type { AgentTask } from "../../src/api/agent-types";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../src/api/client", () => ({
  api: request,
  errorMessage: (error: unknown) =>
    error instanceof Error ? error.message : "请求失败",
}));

function waitingTask(): AgentTask {
  return {
    id: "65144c6f-57bd-46cc-804f-9bad49dd09a3",
    session_id: "c87fd0b2-d07c-4aaf-b0bf-f7f4eae38dca",
    task_type: "business",
    status: "waiting_approval",
    message: "为单测订单创建退款申请",
    result: {
      approval: {
        approval_id: "edbffdf4-89c4-4ed6-bd84-b158738e83ee",
        order_no: "UNIT-10086",
        status: "pending",
        reason: "change_mind",
        notes: "受控单测数据",
        amount_cents: 9900,
        expires_at: "2030-10-12T02:00:00Z",
        requires_confirmation: true,
        policy: {
          id: 1,
          policy_code: "unit_refund",
          version: 1,
          document_id: "88167315-0339-4647-b209-89ebd11e17fc",
          document_title: "单测售后政策",
          document_version: 1,
          valid_from: "2026-01-01T00:00:00Z",
          valid_until: null,
          refund_window_days: 7,
          refund_shipping: false,
          allow_before_delivery: true,
          excluded_categories: [],
        },
      },
    },
    steps: [],
    token_usage: {},
    error_code: null,
    elapsed_ms: 10,
    created_at: "2026-10-12T02:00:00Z",
    updated_at: "2026-10-12T02:00:00Z",
    completed_at: null,
  };
}

const mounted: VueWrapper[] = [];
function view(task = waitingTask()) {
  const wrapper = mount(TaskDetail, {
    props: { task },
    global: {
      stubs: {
        MarkdownContent: {
          props: ["content"],
          template: "<p>{{ content }}</p>",
        },
        CitationList: true,
        AnalyticsChart: true,
        "el-button": {
          props: ["disabled", "loading"],
          template: '<button :disabled="disabled"><slot /></button>',
        },
        "el-tag": {
          props: ["type"],
          template: '<span :data-type="type"><slot /></span>',
        },
        "el-alert": {
          props: ["title", "type"],
          template: '<p role="alert" :data-type="type">{{ title }}</p>',
        },
        "el-dialog": {
          props: ["modelValue"],
          template:
            '<section v-if="modelValue"><slot /><slot name="footer" /></section>',
        },
        "el-descriptions": { template: "<div><slot /></div>" },
        "el-descriptions-item": { template: "<div><slot /></div>" },
        "el-checkbox": {
          props: ["modelValue", "disabled"],
          emits: ["update:modelValue"],
          template:
            '<label><input type="checkbox" :checked="modelValue" :disabled="disabled" @change="$emit(\'update:modelValue\', $event.target.checked)" /><slot /></label>',
        },
      },
    },
  });
  mounted.push(wrapper);
  return wrapper;
}

describe("explicit human approval", () => {
  beforeEach(() => {
    request.mockReset();
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(new Date("2026-10-12T04:00:00Z"));
  });
  afterEach(() => {
    mounted.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.useRealTimers();
  });

  it("does not resume after rendering or opening the approval details", async () => {
    const wrapper = view();
    expect(request).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="review-approval"]').trigger("click");
    expect(request).not.toHaveBeenCalled();
    expect(
      wrapper.get('[data-testid="confirm-approval"]').attributes("disabled"),
    ).toBeDefined();
  });

  it("requires checking the confirmation statement and prevents concurrent submissions", async () => {
    let finish!: (task: AgentTask) => void;
    request.mockImplementation(
      () =>
        new Promise<AgentTask>((resolve) => {
          finish = resolve;
        }),
    );
    const task = waitingTask();
    const wrapper = view(task);
    await wrapper.get('[data-testid="review-approval"]').trigger("click");
    await wrapper.get('input[type="checkbox"]').setValue(true);
    const submit = wrapper.get('[data-testid="confirm-approval"]');
    await submit.trigger("click");
    await submit.trigger("click");

    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith(
      `/api/v1/agent/tasks/${task.id}/resume`,
      {
        method: "POST",
        body: {
          approval_id: task.result.approval!.approval_id,
          confirmed: true,
        },
      },
    );
    const completed = { ...task, status: "completed" as const };
    finish(completed);
    await flushPromises();
    expect(wrapper.emitted("updated")).toEqual([[completed]]);
  });

  it("sends an explicit rejection without treating the dialog open as a decision", async () => {
    const task = waitingTask();
    const rejected = { ...task, status: "rejected" as const };
    request.mockResolvedValue(rejected);
    const wrapper = view(task);
    await wrapper.get('[data-testid="review-approval"]').trigger("click");
    expect(request).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="reject-approval"]').trigger("click");
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      `/api/v1/agent/tasks/${task.id}/resume`,
      {
        method: "POST",
        body: {
          approval_id: task.result.approval!.approval_id,
          confirmed: false,
        },
      },
    );
    expect(wrapper.text()).toContain("服务器返回：已拒绝");
  });

  it("disables expired approval and displays a failed task returned in an HTTP 200 body", () => {
    const task = waitingTask();
    task.result.approval!.expires_at = "2026-10-12T01:00:00Z";
    const expired = view(task);
    expect(
      expired.get('[data-testid="review-approval"]').attributes("disabled"),
    ).toBeDefined();
    expect(request).not.toHaveBeenCalled();

    const failed = view({
      ...task,
      status: "failed",
      error_code: "agent_model_unavailable",
      result: { answer: "受控失败回答" },
    });
    expect(failed.get('[data-testid="task-status"]').text()).toBe("执行失败");
    expect(
      failed.get('[data-testid="task-status"]').attributes("data-type"),
    ).toBe("danger");
    expect(failed.find('[data-testid="approval-panel"]').exists()).toBe(false);
    expect(failed.text()).toContain("agent_model_unavailable");
  });

  it("retains exact query strings and labels unknown token usage instead of claiming zero", () => {
    const task = waitingTask();
    task.status = "completed";
    task.result = {
      analysis: {
        answer: "受控分析单测",
        chart: null,
        metric_ids: ["paid_sales"],
        metric_definitions: {},
        query: {
          normalized_sql:
            "SELECT gross_revenue_cents FROM ea_analytics.daily_orders",
          columns: ["gross_revenue_cents"],
          rows: [["9007199254740993"]],
          row_count: 1,
          truncated: false,
          elapsed_ms: 1,
        },
      },
    };
    const wrapper = view(task);
    expect(wrapper.get("td").text()).toBe("9007199254740993");
    expect(wrapper.get('[data-testid="unknown-usage"]').text()).toContain(
      "已知 Token 不能视为实际总消耗",
    );
    expect(request).not.toHaveBeenCalled();
  });
});
