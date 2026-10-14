import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, errorMessage } from "../../src/api/client";
import { auth, clearAuth, login } from "../../src/stores/auth";

const fetchRequest = vi.fn<typeof fetch>();
const user = { id: 1, username: "unit_customer", role: "customer" as const };

function json(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("authenticated API client", () => {
  beforeEach(() => {
    clearAuth();
    fetchRequest.mockReset();
    vi.stubGlobal("fetch", fetchRequest);
  });
  afterEach(() => {
    clearAuth();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sends the in-memory bearer token with a bounded same-origin request", async () => {
    auth.token = "unit-token";
    fetchRequest.mockResolvedValue(json({ items: [], total: 0 }));
    await api("/api/v1/orders");
    const [path, options] = fetchRequest.mock.calls[0]!;
    expect(path).toBe("/api/v1/orders");
    expect(new Headers(options!.headers).get("Authorization")).toBe(
      "Bearer unit-token",
    );
    expect(options).toMatchObject({
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
    });
  });

  it("rejects external URLs before sending a bearer token", async () => {
    auth.token = "unit-token";
    for (const invalid of [
      "https://example.com/api/v1/orders",
      "//example.com/api/v1/orders",
      "/api/v1/\\example.com",
      "/api/v1/orders\n",
    ]) {
      await expect(api(invalid)).rejects.toMatchObject({ code: "unsafe_url" });
    }
    expect(fetchRequest).not.toHaveBeenCalled();
  });

  it("sets JSON content type while allowing the browser to choose a multipart boundary", async () => {
    fetchRequest.mockImplementation(async () => json({ ok: true }));
    await api("/api/v1/agent/sessions", { method: "POST", body: {} });
    expect(
      new Headers(fetchRequest.mock.calls[0]![1]!.headers).get("Content-Type"),
    ).toBe("application/json");
    expect(fetchRequest.mock.calls[0]![1]!.body).toBe("{}");
    const form = new FormData();
    form.append("title", "受控单测文件");
    await api("/api/v1/knowledge/documents", { method: "POST", body: form });
    expect(fetchRequest.mock.calls[1]![1]!.body).toBe(form);
    expect(
      new Headers(fetchRequest.mock.calls[1]![1]!.headers).has("Content-Type"),
    ).toBe(false);
  });

  it("accepts a successful empty 204 response for document deletion", async () => {
    fetchRequest.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(
      api("/api/v1/knowledge/documents/unit-id", { method: "DELETE" }),
    ).resolves.toBeUndefined();
  });

  it("clears the current identity on 401 and preserves a safe request ID in the displayed error", async () => {
    auth.token = "unit-token";
    auth.user = user;
    fetchRequest.mockResolvedValue(
      json(
        {
          error: { code: "token_expired", message: "登录已过期" },
          request_id: "unit-request-id",
        },
        401,
      ),
    );
    let failure: unknown;
    try {
      await api("/api/v1/orders");
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({
      status: 401,
      code: "token_expired",
      requestId: "unit-request-id",
    });
    expect(errorMessage(failure)).toBe(
      "登录已过期（请求 ID：unit-request-id）",
    );
    expect(auth).toMatchObject({ token: "", user: null, expired: true });
  });

  it("keeps a valid identity on a business 403 and uses the header request ID fallback", async () => {
    auth.token = "unit-token";
    auth.user = user;
    fetchRequest.mockResolvedValue(
      json({ error: { code: "forbidden", message: "没有查询权限" } }, 403, {
        "X-Request-ID": "unit-header-id",
      }),
    );
    await expect(api("/api/v1/analytics/catalog")).rejects.toMatchObject({
      status: 403,
      requestId: "unit-header-id",
    });
    expect(auth.token).toBe("unit-token");
    expect(auth.user).toEqual(user);
  });

  it("does not let an old 401 log out a subsequently selected identity", async () => {
    let finish!: (response: Response) => void;
    fetchRequest.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    auth.token = "old-unit-token";
    const pending = api("/api/v1/orders");
    auth.token = "new-unit-token";
    auth.user = { ...user, id: 2, username: "unit_other" };
    finish(
      json({ error: { code: "token_expired", message: "旧账号过期" } }, 401),
    );
    await expect(pending).rejects.toMatchObject({ code: "session_changed" });
    expect(auth.token).toBe("new-unit-token");
    expect(auth.user?.username).toBe("unit_other");
  });

  it("rejects a JSON body that finishes after login identity changes", async () => {
    let finishBody!: (body: unknown) => void;
    let bodyStarted!: () => void;
    const reading = new Promise<void>((resolve) => {
      bodyStarted = resolve;
    });
    const response = json({});
    vi.spyOn(response, "json").mockImplementation(
      () =>
        new Promise((resolve) => {
          finishBody = resolve;
          bodyStarted();
        }),
    );
    fetchRequest.mockResolvedValue(response);
    auth.token = "old-unit-token";
    const pending = api("/api/v1/orders");
    // Allow fetch/send to finish before simulating a delayed response body.
    await reading;
    auth.token = "new-unit-token";
    finishBody({ items: [{ order_no: "OLD-ACCOUNT-ORDER" }] });
    await expect(pending).rejects.toMatchObject({ code: "session_changed" });
  });

  it("verifies /me after login and keeps the bearer token out of browser storage", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    fetchRequest.mockResolvedValueOnce(
      json({ access_token: "unit-login-token", expires_in: 3600, user }),
    );
    fetchRequest.mockResolvedValueOnce(json(user));
    await login("unit_customer", "unit-password");
    expect(fetchRequest.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/auth/login",
      "/api/v1/auth/me",
    ]);
    expect(
      new Headers(fetchRequest.mock.calls[1]![1]!.headers).get("Authorization"),
    ).toBe("Bearer unit-login-token");
    expect(auth.user).toEqual(user);
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("removes the provisional login token when current-user verification fails", async () => {
    fetchRequest.mockResolvedValueOnce(
      json({ access_token: "unit-login-token", expires_in: 3600, user }),
    );
    fetchRequest.mockResolvedValueOnce(
      json({ error: { code: "invalid_token", message: "校验失败" } }, 401),
    );
    await expect(login("unit_customer", "unit-password")).rejects.toMatchObject(
      { status: 401 },
    );
    expect(auth.user).toBeNull();
    expect(auth.token).toBe("");
  });

  it("preserves an HTTP 200 failed-task body rather than turning it into task success", async () => {
    fetchRequest.mockResolvedValue(
      json({ status: "failed", error_code: "agent_model_unavailable" }),
    );
    await expect(api("/api/v1/agent/tasks/unit-task")).resolves.toEqual({
      status: "failed",
      error_code: "agent_model_unavailable",
    });
  });
});
