export type UserRole = "customer" | "support" | "analyst" | "admin";
export interface User {
  id: number;
  username: string;
  role: UserRole;
}
export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
export type Scalar = string | number | boolean | null;
export interface ChartSpec {
  type: "line" | "bar";
  labels: string[];
  series: { name: string; column_index: number; data: Scalar[] }[];
  source: "query_rows";
}
export interface AnalyticsReport {
  answer: string;
  query: {
    normalized_sql: string;
    columns: string[];
    rows: Scalar[][];
    row_count: number;
    truncated: boolean;
    elapsed_ms: number;
  };
  chart: ChartSpec | null;
  metric_ids: string[];
  metric_definitions: Record<string, string>;
}
export interface AnalyticsCatalog {
  views: Record<string, Record<string, string>>;
  metric_definitions: Record<string, string>;
  today: string;
  business_timezone: string;
  date_windows: Record<
    string,
    { start_inclusive: string; end_exclusive: string }
  >;
  notes: string[];
}
