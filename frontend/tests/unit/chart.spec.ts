import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AnalyticsChart from "../../src/components/AnalyticsChart.vue";

describe("analytics chart from executed rows", () => {
  it("keeps large exact money values in tooltips and never mutates source values", () => {
    const chart = {
      type: "bar",
      source: "query_rows",
      labels: ["单测商品 A", "单测商品 B"],
      series: [
        {
          name: "goods_revenue_cents",
          column_index: 1,
          data: ["9007199254740993", "9007199254740994"],
        },
      ],
    };
    const before = JSON.stringify(chart);
    const wrapper = mount(AnalyticsChart, { props: { chart } });
    const tooltips = wrapper.findAll("rect title").map((node) => node.text());
    expect(tooltips).toEqual([
      "goods_revenue_cents · 单测商品 A：9007199254740993",
      "goods_revenue_cents · 单测商品 B：9007199254740994",
    ]);
    expect(JSON.stringify(chart)).toBe(before);
    expect(wrapper.text()).toContain("精确值保留在结果表中");
  });

  it("distinguishes missing values from a genuine zero in a bar series", () => {
    const wrapper = mount(AnalyticsChart, {
      props: {
        chart: {
          type: "bar",
          source: "query_rows",
          labels: ["缺失", "零值", "销量"],
          series: [{ name: "orders", column_index: 1, data: [null, "0", "7"] }],
        },
      },
    });
    expect(wrapper.findAll("rect")).toHaveLength(2);
    expect(wrapper.findAll("rect title").map((node) => node.text())).toEqual([
      "orders · 零值：0",
      "orders · 销量：7",
    ]);
    expect(wrapper.text()).toContain("NULL 不绘制为零");
  });

  it("does not label an unsafe numeric integer as an exact query value", () => {
    const wrapper = mount(AnalyticsChart, {
      props: {
        chart: {
          type: "bar",
          source: "query_rows",
          labels: ["数值已丢失精度"],
          series: [
            {
              name: "goods_revenue_cents",
              column_index: 1,
              data: [Number("9007199254740993")],
            },
          ],
        },
      },
    });
    expect(wrapper.find("svg").exists()).toBe(false);
    expect(wrapper.text()).toContain("原始查询值以表格为准");
  });

  it("leaves a gap rather than joining a line through a missing observation", () => {
    const wrapper = mount(AnalyticsChart, {
      props: {
        chart: {
          type: "line",
          source: "query_rows",
          labels: ["周一", "周二", "周三"],
          series: [{ name: "orders", column_index: 1, data: ["7", null, "9"] }],
        },
      },
    });
    expect(wrapper.findAll("circle")).toHaveLength(2);
    expect(wrapper.findAll("polyline")).toHaveLength(2);
    expect(wrapper.findAll("circle title").map((node) => node.text())).toEqual([
      "orders · 周一：7",
      "orders · 周三：9",
    ]);
  });

  it("uses an unavailable state for malformed chart data instead of breaking the task page", () => {
    for (const chart of [
      null,
      { type: "bar", source: "query_rows", labels: ["一天"], series: [null] },
      {
        type: "bar",
        source: "query_rows",
        labels: ["一天"],
        series: [{ name: "orders", data: [] }],
      },
    ]) {
      const wrapper = mount(AnalyticsChart, { props: { chart } });
      expect(wrapper.find("svg").exists()).toBe(false);
      expect(wrapper.text()).toContain("原始查询值以表格为准");
    }
  });
});
