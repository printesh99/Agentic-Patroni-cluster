/* Application Monitoring charts. v31 renders only live payloads and returns an
   honest unavailable state when the backing API has no rows. */
(function () {
  "use strict";
  var e = React.createElement;
  var VIOLET = "#7c3aed", TEAL = "#14b8a6", INDIGO = "#6366f1", AMBER = "#f59e0b",
      SKY = "#2f9fe8", PINK = "#ef476f", SLATE = "#94a3b8", GREEN = "#26a65b";

  function EBox(props) {
    return e("div", {
      ref: function (el) {
        if (el && typeof echarts !== "undefined") {
          var c = echarts.getInstanceByDom(el) || echarts.init(el);
          c.setOption(Object.assign({ animation: false }, props.option), true);
        }
      },
      style: { width: "100%", height: props.h || 240 }
    });
  }

  function unavailableBadge() {
    return e("span", { className: "pill warn", style: { fontSize: 10, marginLeft: 8 } }, "live data unavailable");
  }
  function card(title, meta, body, isSample) {
    return e("div", { className: "card" },
      e("div", { className: "hd" }, title, e("span", { className: "meta" }, meta, isSample ? unavailableBadge() : null)),
      e("div", { className: "bd" }, body));
  }

  function hhmm(ts) {
    var d = new Date(typeof ts === "number" && ts < 2e10 ? ts * 1000 : ts);
    if (isNaN(d)) return String(ts);
    return (d.getHours() < 10 ? "0" : "") + d.getHours() + ":" + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
  }

  /* ---- Estate Overview: full stacked session trend ---- */
  window.AppMonTrendArea = function (props) {
    var series = props.series || [];
    var live = series.length > 0 && series.some(function (s) { return s.points && s.points.length > 1; });
    if (!live) return e("div", { className: "muted", style: { padding: "42px 12px", textAlign: "center" } }, "Live session history unavailable; no trend is inferred.");
    var x, out;
    if (live) {
      var longest = series.reduce(function (a, b) { return (b.points || []).length > (a.points || []).length ? b : a; }, series[0]);
      x = (longest.points || []).map(function (p) { return hhmm(p[0]); });
      out = series.map(function (s, i) {
        var colors = [VIOLET, TEAL, AMBER, SKY, PINK, SLATE, GREEN, INDIGO];
        return { name: s.label || s.name || "series " + (i + 1), type: "line", stack: "t", smooth: true, symbol: "none",
          itemStyle: { color: colors[i % colors.length] }, lineStyle: { width: 1 }, areaStyle: { opacity: .5 },
          data: (s.points || []).map(function (p) { return p[1]; }) };
      });
    }
    return e("div", null, e(EBox, {
        h: props.height && props.height > 160 ? props.height : 260,
        option: {
          tooltip: { trigger: "axis" },
          legend: { bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
          grid: { top: 24, right: 14, bottom: 40, left: 44 },
          xAxis: { type: "category", data: x, axisLabel: { fontSize: 10, interval: Math.ceil(x.length / 8) } },
          yAxis: { type: "value", axisLabel: { fontSize: 10 } },
          series: out
        }
      }));
  };

  /* ---- Estate Overview: waits mix donut ---- */
  window.AppMonWaitsMix = function (props) {
    var rows = props.rows || [];
    var agg = {};
    rows.forEach(function (r) { var k = r.wait_event_type || (r.state === "active" ? "CPU (no wait)" : r.state) || "other"; agg[k] = (agg[k] || 0) + Number(r.sessions || 0); });
    var entries = Object.keys(agg).map(function (k) { return { name: k, value: agg[k] }; }).filter(function (d) { return d.value > 0; });
    var live = entries.length > 0;
    if (!live) return null;
    var colors = [GREEN, SLATE, SKY, PINK, AMBER, VIOLET, TEAL];
    return card("Waits mix", live ? "top sessions by wait class" : "wait class share", e(EBox, {
      h: 210,
      option: {
        tooltip: { trigger: "item", valueFormatter: function (n) { return n + " sessions"; } },
        legend: { bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
        series: [{ type: "pie", radius: ["46%", "72%"], center: ["50%", "44%"],
          label: { show: false }, data: entries.map(function (d, i) { return Object.assign({ itemStyle: { color: colors[i % colors.length] } }, d); }) }]
      }
    }), !live);
  };

  /* ---- Domain views: dead-tuple risk + sessions by state ---- */
  window.AppMonDomainCharts = function (props) {
    var d = props.data || {};
    var dead = (d.dead_tuples || []).slice(0, 12);
    var deadLive = dead.length > 0;
    dead = dead.slice().sort(function (a, b) { return a.value - b.value; });

    var sess = d.sessions || [];
    var sessLive = sess.length > 0;
    var byDb = {}, states = ["active", "idle", "idle in transaction", "other"];
    if (sessLive) {
      sess.forEach(function (r) {
        var db = r.datname || "?"; byDb[db] = byDb[db] || { active: 0, idle: 0, "idle in transaction": 0, other: 0 };
        var st = states.indexOf(r.state) >= 0 ? r.state : "other";
        byDb[db][st] += Number(r.sessions || 0);
      });
    }
    if (!deadLive && !sessLive) return null;
    var dbs = Object.keys(byDb).slice(0, 10);
    var stColors = { active: GREEN, idle: SLATE, "idle in transaction": AMBER, other: VIOLET };

    return e("div", { className: "grid-2 mt-3" },
      card("Dead-tuple risk", "vacuum candidates \u00b7 % dead", e(EBox, {
        h: Math.max(180, dead.length * 26 + 60),
        option: {
          tooltip: { trigger: "axis", valueFormatter: function (n) { return n + "% dead"; } },
          grid: { top: 10, right: 40, bottom: 22, left: 130 },
          xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: "{value}%" } },
          yAxis: { type: "category", data: dead.map(function (r) { return r.relation; }), axisLabel: { fontSize: 10, fontFamily: "monospace" } },
          series: [{ type: "bar", barWidth: 12,
            label: { show: true, position: "right", fontSize: 9.5, formatter: "{c}%" },
            data: dead.map(function (r) { return { value: r.value, itemStyle: { color: r.value >= 20 ? PINK : r.value >= 10 ? AMBER : VIOLET } }; }),
            markLine: { symbol: "none", label: { fontSize: 9, formatter: "vacuum threshold" }, lineStyle: { color: PINK, type: "dashed" }, data: [{ xAxis: 20 }] } }]
        }
      }), !deadLive),
      card("Sessions by state", "per database", e(EBox, {
        h: Math.max(180, dbs.length * 30 + 70),
        option: {
          tooltip: { trigger: "axis" },
          legend: { bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
          grid: { top: 10, right: 16, bottom: 40, left: 90 },
          xAxis: { type: "value", axisLabel: { fontSize: 10 } },
          yAxis: { type: "category", data: dbs, axisLabel: { fontSize: 10, fontFamily: "monospace" } },
          series: states.map(function (st) {
            return { name: st, type: "bar", stack: "s", barWidth: 14, itemStyle: { color: stColors[st] },
              data: dbs.map(function (db) { return byDb[db][st]; }) };
          })
        }
      }), !sessLive));
  };

  /* ---- Replication & DBA: retained WAL + locks mix ---- */
  window.AppMonReplCharts = function (props) {
    var r = props.repl || {}, d = props.dba || {};
    var slots = (r.slots || []).slice(0, 10);
    var slotsLive = slots.length > 0;
    slots = slots.slice().sort(function (a, b) { return (a.retained_wal_bytes || 0) - (b.retained_wal_bytes || 0); });
    var LIMIT = 5 * 1073741824;

    var locks = (d.locks || []);
    var locksLive = locks.length > 0;
    if (!slotsLive && !locksLive) return null;
    var lockColors = [SLATE, VIOLET, AMBER, TEAL, PINK, SKY];

    return e("div", { className: "grid-2 mt-3" },
      card("Retained WAL per slot", "dashed line = 5 GB risk threshold", e(EBox, {
        h: Math.max(180, slots.length * 30 + 60),
        option: {
          tooltip: { trigger: "axis", valueFormatter: function (n) { return (Math.round(n / 1073741824 * 10) / 10) + " GB"; } },
          grid: { top: 10, right: 50, bottom: 22, left: 110 },
          xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: function (v) { return Math.round(v / 1073741824) + "G"; } } },
          yAxis: { type: "category", data: slots.map(function (s) { return s.slot; }), axisLabel: { fontSize: 10, fontFamily: "monospace" } },
          series: [{ type: "bar", barWidth: 13,
            label: { show: true, position: "right", fontSize: 9.5, formatter: function (p) { return (Math.round(p.value / 1073741824 * 10) / 10) + " GB"; } },
            data: slots.map(function (s) { return { value: s.retained_wal_bytes || 0, itemStyle: { color: (s.retained_wal_bytes || 0) >= LIMIT ? PINK : VIOLET } }; }),
            markLine: { symbol: "none", label: { show: false }, lineStyle: { color: PINK, type: "dashed" }, data: [{ xAxis: LIMIT }] } }]
        }
      }), !slotsLive),
      card("Locks by mode", "pg_locks snapshot", e(EBox, {
        h: 220,
        option: {
          tooltip: { trigger: "item", valueFormatter: function (n) { return n + " locks"; } },
          legend: { bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
          series: [{ type: "pie", radius: ["46%", "72%"], center: ["50%", "44%"], label: { show: false },
            data: locks.map(function (l, i) { return { name: l.mode, value: l.value, itemStyle: { color: lockColors[i % lockColors.length] } }; }) }]
        }
      }), !locksLive));
  };
})();
