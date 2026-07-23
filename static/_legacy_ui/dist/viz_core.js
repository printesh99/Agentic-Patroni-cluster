/* v31 shared chart visual primitives. Contains no datasets or fallback values. */
(function () {
  "use strict";
  var e = React.createElement;
  var PINK = "#ef476f", GREEN = "#26a65b";
  function EBox(props) {
    return e("div", { ref: function (el) {
      if (el && typeof echarts !== "undefined") {
        var c = echarts.getInstanceByDom(el) || echarts.init(el);
        c.setOption(Object.assign({ animation: false }, props.option), true);
      }
    }, style: { width: "100%", height: props.h || 220 } });
  }
  function baseAxis(x) {
    return { tooltip: { trigger: "axis" }, grid: { top: 24, right: 14, bottom: 40, left: 46 },
      xAxis: { type: "category", data: x || [], axisLabel: { fontSize: 10, interval: Math.max(0, Math.ceil((x || []).length / 8) - 1) } },
      yAxis: { type: "value", axisLabel: { fontSize: 10 } },
      legend: { bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 } };
  }
  function donut(entries, colors, unit) {
    return { tooltip: { trigger: "item", valueFormatter: function (n) { return n + " " + (unit || ""); } },
      legend: { bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
      series: [{ type: "pie", radius: ["46%", "72%"], center: ["50%", "44%"], label: { show: false },
        data: (entries || []).map(function (d, i) { return { name: d[0], value: d[1], itemStyle: { color: colors[i % colors.length] } }; }) }] };
  }
  function gauge(value, target, name, unit, invert) {
    var ok = invert ? value <= target : value >= target;
    return { series: [{ type: "gauge", startAngle: 210, endAngle: -30, min: 0, max: Math.max(target * 2, value * 1.3, 1),
      progress: { show: true, width: 12, itemStyle: { color: ok ? GREEN : PINK } },
      axisLine: { lineStyle: { width: 12, color: [[1, "rgba(124,58,237,.12)"]] } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, pointer: { show: false }, anchor: { show: false },
      title: { show: true, offsetCenter: [0, "34%"], fontSize: 11 },
      detail: { offsetCenter: [0, "-4%"], fontSize: 22, fontWeight: 800, color: ok ? GREEN : PINK, formatter: "{value} " + unit },
      data: [{ value: value, name: name + " · target " + (invert ? "≤" : "≥") + " " + target + " " + unit }] }] };
  }
  window.HbzViz = { EBox: EBox, baseAxis: baseAxis, donut: donut, gauge: gauge };
})();
