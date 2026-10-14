import { describe, expect, it } from "vitest";
import { dateTime, money } from "../../src/utils/format";

describe("exact monetary display", () => {
  it.each([
    [0, "¥0.00"],
    [1, "¥0.01"],
    [12900, "¥129.00"],
    [-1, "-¥0.01"],
    [-123456n, "-¥1,234.56"],
    ["9007199254740993", "¥90,071,992,547,409.93"],
    ["9007199254740993.000", "¥90,071,992,547,409.93"],
  ] as const)(
    "formats integer cents %s without floating-point money arithmetic",
    (input, expected) => {
      expect(money(input)).toBe(expected);
    },
  );

  it("refuses an unsafe numeric value rather than inventing its lost cents", () => {
    expect(money(Number("9007199254740993"))).toBe("—");
  });

  it("shows an unavailable value for malformed, fractional, or missing cents", () => {
    for (const invalid of [
      null,
      undefined,
      "",
      "NaN",
      "1e5",
      "100.5",
      " 100 ",
      1.5,
      NaN,
      Infinity,
    ]) {
      expect(money(invalid), `Invalid monetary input ${String(invalid)}`).toBe(
        "—",
      );
    }
  });
});

describe("Shanghai time presentation", () => {
  it("converts a UTC instant across the Shanghai calendar-day boundary", () => {
    const displayed = dateTime("2026-10-12T18:30:00Z");
    expect(displayed).toContain("2026/10/13");
    expect(displayed).toContain("02:30");
  });

  it("uses the unavailable marker for missing or invalid timestamps", () => {
    for (const invalid of [undefined, null, "", "not-a-date"])
      expect(dateTime(invalid)).toBe("—");
  });
});
