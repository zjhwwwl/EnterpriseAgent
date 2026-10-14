import type { UserRole } from "../api/types";

export function money(
  value: string | number | bigint | null | undefined,
): string {
  if (
    value === null ||
    value === undefined ||
    (typeof value === "number" && !Number.isSafeInteger(value))
  )
    return "—";
  const raw = String(value);
  if (!/^-?\d+(?:\.0+)?$/.test(raw)) return "—";
  const cents = BigInt(raw.split(".")[0]!);
  const absolute = cents < 0n ? -cents : cents;
  const whole = String(absolute / 100n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${cents < 0n ? "-" : ""}¥${whole}.${String(absolute % 100n).padStart(2, "0")}`;
}
export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
export const roleLabels: Record<UserRole, string> = {
  customer: "客户",
  support: "售后客服",
  analyst: "运营分析员",
  admin: "管理员",
};
