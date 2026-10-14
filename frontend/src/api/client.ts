import { auth, clearAuth } from "../stores/auth";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError)
    return `${error.message}${error.requestId ? `（请求 ID：${error.requestId}）` : ""}`;
  if (error instanceof DOMException && error.name === "AbortError")
    return "请求已取消。";
  return "无法连接服务，请检查本地 API 并重试。";
}
interface Options {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}
function safePath(path: string): void {
  if (
    !/^\/(?:api\/v1\/|health\/)/.test(path) ||
    path.includes("\\") ||
    /[\r\n]/.test(path)
  )
    throw new ApiError(0, "unsafe_url", "仅允许向本工作台 API 发送请求。");
}
async function send(path: string, options: Options): Promise<Response> {
  safePath(path);
  const token = auth.token;
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body: BodyInit | undefined;
  if (options.body instanceof FormData) body = options.body;
  else if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers,
    body,
    signal: options.signal,
    credentials: "omit",
    redirect: "error",
    cache: "no-store",
  });
  // A delayed response for a previous login must not replace another user's UI.
  if (token && token !== auth.token)
    throw new ApiError(
      0,
      "session_changed",
      "登录身份已变化，请重新加载当前页面。",
    );
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
      request_id?: string;
    } | null;
    if (response.status === 401 && token === auth.token) clearAuth(true);
    throw new ApiError(
      response.status,
      payload?.error?.code ?? "http_error",
      payload?.error?.message ?? `请求失败（${response.status}）。`,
      payload?.request_id ?? response.headers.get("X-Request-ID"),
    );
  }
  return response;
}
export async function api<T = unknown>(
  path: string,
  options: Options = {},
): Promise<T> {
  const token = auth.token;
  const response = await send(path, options);
  const data = response.status === 204 ? undefined : await response.json();
  if (token && token !== auth.token)
    throw new ApiError(
      0,
      "session_changed",
      "登录身份已变化，请重新加载当前页面。",
    );
  return data as T;
}
export async function downloadFile(
  path: string,
  filename: string,
): Promise<void> {
  const token = auth.token;
  const response = await send(path, {});
  const blob = await response.blob();
  if (token && token !== auth.token)
    throw new ApiError(
      0,
      "session_changed",
      "登录身份已变化，请重新加载当前页面。",
    );
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename.replace(/[\\/\r\n]/g, "_");
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
