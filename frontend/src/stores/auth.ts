import { reactive } from "vue";
import { api } from "../api/client";
import type { User } from "../api/types";

export const auth = reactive<{
  user: User | null;
  token: string;
  expiresAt: number;
  expired: boolean;
}>({ user: null, token: "", expiresAt: 0, expired: false });
let expiryTimer: ReturnType<typeof setTimeout> | undefined;

export function clearAuth(expired = false): void {
  if (expiryTimer) clearTimeout(expiryTimer);
  auth.user = null;
  auth.token = "";
  auth.expiresAt = 0;
  auth.expired = expired;
}

export async function login(username: string, password: string): Promise<void> {
  clearAuth();
  const result = await api<{
    access_token: string;
    expires_in: number;
    user: User;
  }>("/api/v1/auth/login", { method: "POST", body: { username, password } });
  auth.token = result.access_token;
  auth.expiresAt = Date.now() + result.expires_in * 1000;
  try {
    auth.user = await api<User>("/api/v1/auth/me");
    expiryTimer = setTimeout(
      () => clearAuth(true),
      Math.max(0, auth.expiresAt - Date.now()),
    );
  } catch (error) {
    clearAuth();
    throw error;
  }
}
