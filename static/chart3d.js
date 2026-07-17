/* chart3d.js — global ECharts "3D depth" enhancer.
   Wraps echarts.init so every chart gets gradient fills, soft drop
   shadows, and rounded caps: bars look extruded, donuts get lift,
   lines get glow. Loaded right after the echarts vendor bundle. */
(function () {
  "use strict";
  if (typeof echarts === "undefined") return;

  function lighten(hex, amt) {
    var m = /^#([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    var n = parseInt(m[1], 16);
    var r = Math.min(255, ((n >> 16) & 255) + amt);
    var g = Math.min(255, ((n >> 8) & 255) + amt);
    var b = Math.min(255, (n & 255) + amt);
    return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  }
  function darken(hex, amt) { return lighten(hex, -amt); }

  function vGrad(top, bottom) {
    return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: top }, { offset: 1, color: bottom }
    ]);
  }

  var SHADOW = "rgba(28, 20, 60, .28)";

  function enhanceSeries(s, chart) {
    if (!s || typeof s !== "object") return;
    var t = s.type;
    try {
      if (t === "bar") {
        s.itemStyle = s.itemStyle || {};
        var base = typeof s.itemStyle.color === "string" ? s.itemStyle.color : null;
        if (base && /^#([0-9a-f]{6})$/i.test(base)) {
          s.itemStyle.color = vGrad(lighten(base, 38), darken(base, 18));
        } else if (!s.itemStyle.color) {
          // fall back to palette color per series via colorBy default; add gloss via decal-free shadow only
        }
        if (s.itemStyle.borderRadius == null) {
          s.itemStyle.borderRadius = (s.stack ? 2 : [5, 5, 0, 0]);
        }
        if (s.itemStyle.shadowBlur == null) {
          s.itemStyle.shadowBlur = 6;
          s.itemStyle.shadowOffsetY = 3;
          s.itemStyle.shadowOffsetX = 1;
          s.itemStyle.shadowColor = SHADOW;
        }
        s.emphasis = s.emphasis || {};
        s.emphasis.itemStyle = Object.assign({ shadowBlur: 14, shadowOffsetY: 6, shadowColor: "rgba(28,20,60,.4)" }, s.emphasis.itemStyle || {});
      } else if (t === "line") {
        s.lineStyle = s.lineStyle || {};
        if (s.lineStyle.shadowBlur == null) {
          s.lineStyle.shadowBlur = 8;
          s.lineStyle.shadowOffsetY = 5;
          s.lineStyle.shadowColor = "rgba(28,20,60,.22)";
        }
        if (s.areaStyle && s.areaStyle.opacity != null && !s.areaStyle.color) {
          var lc = (s.itemStyle && typeof s.itemStyle.color === "string") ? s.itemStyle.color
                 : (typeof s.color === "string" ? s.color : null);
          if (lc && /^#([0-9a-f]{6})$/i.test(lc)) {
            var op = s.areaStyle.opacity;
            s.areaStyle = {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: lc + Math.round(Math.min(1, op * 2.6) * 255).toString(16).padStart(2, "0") },
                { offset: 1, color: lc + "00" }
              ])
            };
          }
        }
      } else if (t === "pie") {
        s.itemStyle = s.itemStyle || {};
        if (s.itemStyle.shadowBlur == null) {
          s.itemStyle.shadowBlur = 14;
          s.itemStyle.shadowOffsetY = 7;
          s.itemStyle.shadowColor = "rgba(28,20,60,.30)";
        }
        if (s.itemStyle.borderWidth == null) {
          s.itemStyle.borderWidth = 2;
          s.itemStyle.borderColor = "rgba(255,255,255,.9)";
        }
        if (s.itemStyle.borderRadius == null) s.itemStyle.borderRadius = 4;
        s.emphasis = s.emphasis || {};
        s.emphasis.scaleSize = s.emphasis.scaleSize || 6;
      } else if (t === "gauge") {
        // leave gauges alone
      } else if (t === "scatter" || t === "effectScatter") {
        s.itemStyle = s.itemStyle || {};
        if (s.itemStyle.shadowBlur == null) {
          s.itemStyle.shadowBlur = 8;
          s.itemStyle.shadowOffsetY = 4;
          s.itemStyle.shadowColor = SHADOW;
        }
      }
    } catch (err) { /* never break a chart over styling */ }
  }

  var origInit = echarts.init;
  echarts.init = function () {
    var chart = origInit.apply(echarts, arguments);
    if (chart && chart.setOption && !chart.__c3d) {
      chart.__c3d = true;
      var orig = chart.setOption;
      chart.setOption = function (option) {
        try {
          if (option && typeof option === "object" && !Array.isArray(option)) {
            var tt = option.tooltip;
            if (tt == null || (typeof tt === "object" && !Array.isArray(tt))) {
              option.tooltip = Object.assign({
                backgroundColor: "rgba(30,23,64,.95)",
                borderWidth: 0,
                borderRadius: 8,
                padding: [8, 11],
                textStyle: { color: "#eae7f3", fontSize: 11 },
                extraCssText: "box-shadow:0 10px 28px rgba(20,12,50,.35);"
              }, tt || {});
            }
          }
          if (option && option.series) {
            var arr = Array.isArray(option.series) ? option.series : [option.series];
            for (var i = 0; i < arr.length; i++) enhanceSeries(arr[i], chart);
          }
        } catch (err) { /* noop */ }
        return orig.apply(chart, arguments);
      };
    }
    return chart;
  };
})();
