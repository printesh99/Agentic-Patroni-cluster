/* v31 live-only chart overlays. The legacy module_charts*.js files still
   provide the visual toolkit, but their seeded overlays are disabled. Every
   chart below requires an API payload with available=true. */
(function () {
  "use strict";
  var e = React.createElement, V = window.HbzViz;
  if (!V || !window.HBZ_LIVE_CHARTS_ONLY) return;
  var EBox = V.EBox, baseAxis = V.baseAxis, donut = V.donut, gauge = V.gauge;
  var PURPLE = "#7c3aed", TEAL = "#14b8a6", AMBER = "#f59e0b", PINK = "#ef476f";
  var SKY = "#2f9fe8", SLATE = "#94a3b8", GREEN = "#26a65b", INDIGO = "#6366f1";
  var COLORS = [PURPLE, TEAL, AMBER, SKY, PINK, SLATE, GREEN, INDIGO];

  function liveCard(title, meta, source, body) {
    return e("div", { className: "card" },
      e("div", { className: "hd" }, title,
        e("span", { className: "meta" }, meta,
          e("span", { className: "pill ok", style: { fontSize: 10, marginLeft: 8 } }, "live · " + (source || "API")))),
      e("div", { className: "bd" }, body));
  }
  function grid2(a, b) { return e("div", { className: "grid-2" }, a, b); }
  function activeId() { return window.activeClusterId ? window.activeClusterId() : "uat"; }
  function chartUrl(module, view) {
    var q = "cluster_id=" + encodeURIComponent(activeId());
    if (view) q += "&view=" + encodeURIComponent(view);
    return "/api/v1/charts/" + encodeURIComponent(module) + "?" + q;
  }
  function usePayload(url) {
    var state = React.useState(null), data = state[0], setData = state[1];
    React.useEffect(function () {
      var cancelled = false;
      setData(null);
      fetch(url, { cache: "no-store" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).then(function (body) {
        if (!cancelled) setData(body && body.available === true ? body : false);
      }).catch(function () { if (!cancelled) setData(false); });
      return function () { cancelled = true; };
    }, [url]);
    return data;
  }
  function wrapLive(name, endpoint, render) {
    var original = window[name];
    if (typeof original !== "function") return;
    window[name] = function (props) {
      var url = typeof endpoint === "function" ? endpoint(props || {}) : endpoint;
      var payload = usePayload(url);
      var charts = payload ? render(payload, props || {}) : null;
      return charts ? e(React.Fragment, null,
        e("div", { className: "page", style: { paddingBottom: 0 } }, charts),
        e(original, props)) : e(original, props);
    };
  }
  function labels(points) {
    return (points || []).map(function (p) {
      var d = new Date(Number(p[0]));
      return isNaN(d) ? String(p[0]) : d.toISOString().slice(5, 16).replace("T", " ");
    });
  }
  function values(points) { return (points || []).map(function (p) { return Number(p[1]) || 0; }); }
  function hbar(rows, unit, color) {
    rows = rows || [];
    return { tooltip: { trigger: "axis", valueFormatter: function (n) { return n + (unit || ""); } },
      grid: { top: 10, right: 50, bottom: 22, left: 140 },
      xAxis: { type: "value", axisLabel: { fontSize: 10 } },
      yAxis: { type: "category", data: rows.map(function (r) { return r[0]; }).reverse(), axisLabel: { fontSize: 10, fontFamily: "monospace" } },
      series: [{ type: "bar", barWidth: 13, label: { show: true, position: "right", fontSize: 9.5 },
        data: rows.map(function (r) { return { value: r[1], itemStyle: { color: color || PURPLE } }; }).reverse() }] };
  }
  function lineOption(series, stack) {
    series = series || [];
    var longest = series.reduce(function (a, b) { return (b.points || []).length > (a.points || []).length ? b : a; }, { points: [] });
    var o = baseAxis(labels(longest.points));
    o.series = series.map(function (s, i) { return {
      name: s.name || s.label || "series " + (i + 1), type: "line", stack: stack || undefined,
      smooth: true, symbol: "none", itemStyle: { color: COLORS[i % COLORS.length] },
      lineStyle: { width: 1.5 }, areaStyle: stack ? { opacity: .35 } : undefined,
      data: values(s.points)
    }; });
    return o;
  }

  wrapLive("AdvisorScreen", function () { return chartUrl("advisor"); }, function (d) {
    return grid2(
      liveCard("Findings by severity", "open recommendations", d.source, e(EBox, { h: 210, option: donut(d.severity || [], [PINK, AMBER, SKY, GREEN], "findings") })),
      liveCard("Findings by category", "worst first", d.source, e(EBox, { h: 210, option: hbar(d.categories || [], "", PURPLE) })));
  });

  wrapLive("WalArchivePressureScreen", function () { return chartUrl("wal"); }, function (d) {
    var series = [{ name: "WAL archived", points: d.archived_rate_series || [] }];
    return grid2(
      liveCard("WAL archive trend", "24h", d.source, e(EBox, { h: 220, option: lineOption(series) })),
      liveCard("Archive queue depth", "current .ready files", d.source, e(EBox, { h: 220, option: gauge(Number(d.ready_queue || 0), 32, "queue", "files", true) })));
  });

  wrapLive("BackupRecoveryScreen", function () { return chartUrl("backups"); }, function (d) {
    var rows = d.timeline || [], x = rows.map(function (r) { return String(r.stop_time || r.label || "-").slice(5, 16); });
    var o = baseAxis(x); o.series = [{ name: "backup size GB", type: "bar", barWidth: 14, itemStyle: { color: PURPLE }, data: rows.map(function (r) { return r.size_gb || 0; }) }];
    var g = baseAxis(x); g.series = [{ name: "repo size GB", type: "line", smooth: true, symbol: "none", itemStyle: { color: AMBER }, areaStyle: { opacity: .12 }, data: rows.map(function (r) { return r.repo_gb || 0; }) }];
    return grid2(liveCard("Backup timeline", "pgBackRest", d.source, e(EBox, { h: 220, option: o })), liveCard("Repository size", "repo1", d.source, e(EBox, { h: 220, option: g })));
  });

  wrapLive("DrReadinessScreen", function () { return chartUrl("dr"); }, function (d) {
    var rpoMin = Number(d.rpo_seconds || 0) / 60;
    return liveCard("RPO — data loss window", "current replication replay lag", d.source, e(EBox, { h: 210, option: gauge(+rpoMin.toFixed(2), 15, "RPO", "min", true) }));
  });

  wrapLive("LogsExplorerScreen", function () { return chartUrl("logs"); }, function (d) {
    return liveCard("Log volume by severity", "24h", d.source, e(EBox, { h: 220, option: lineOption(d.series || [], "logs") }));
  });

  wrapLive("ObjectMetricsScreen", function () { return chartUrl("objects"); }, function (d) {
    var tree = { tooltip: { valueFormatter: function (n) { return n + " GB"; } }, series: [{ type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false }, label: { fontSize: 11, formatter: "{b}\n{c} GB" }, data: (d.treemap || []).map(function (r, i) { return { name: r.name, value: r.gb, itemStyle: { color: COLORS[i % COLORS.length] } }; }) }] };
    return grid2(liveCard("Database footprint", "relation size share", d.source, e(EBox, { h: 240, option: tree })), liveCard("Fastest growing (30d)", "GB added", d.source, e(EBox, { h: 240, option: hbar((d.growth_30d || []).map(function (r) { return [r.name, r.gb]; }), " GB", PURPLE) })));
  });

  wrapLive("DbLoadTimelineScreen", function () { return "/api/v1/perf/db-load?window=24h&dim=wait_class&cluster_id=" + encodeURIComponent(activeId()); }, function (d) {
    return liveCard("DB load — average active sessions by wait class", "24h · ASH-style", d.source, e(EBox, { h: 280, option: lineOption(d.series || [], "load") }));
  });

  wrapLive("PerformanceInsightsScreen", function (p) { return chartUrl("perf", p.view || "waits"); }, function (d, p) {
    var view = p.view || "waits";
    if (view === "waits") return grid2(
      liveCard("Sessions by wait class", "pg_stat_activity", d.source, e(EBox, { h: 210, option: donut(d.waits_donut || [], COLORS, "sessions") })),
      liveCard("Longest waits", "seconds", d.source, e(EBox, { h: 210, option: hbar(d.longest_waits || [], " s", PURPLE) })));
    if (view === "topsql" || view === "slow") {
      var sc = { tooltip: { formatter: function (x) { return x.value[0] + " calls · " + x.value[1] + " ms"; } }, grid: { top: 30, right: 20, bottom: 40, left: 54 }, xAxis: { type: "log", name: "calls" }, yAxis: { type: "log", name: "mean ms" }, series: [{ type: "scatter", symbolSize: 9, itemStyle: { color: PURPLE }, data: d.scatter || [] }] };
      var hist = baseAxis(["<1ms", "1–10ms", "10–100ms", "0.1–1s", "1–10s", ">10s"]); hist.series = [{ type: "bar", itemStyle: { color: PURPLE }, data: d.histogram || [] }];
      return grid2(liveCard("Calls vs mean time", "pg_stat_statements", d.source, e(EBox, { h: 240, option: sc })), liveCard("Runtime distribution", "mean-time buckets", d.source, e(EBox, { h: 240, option: hist })));
    }
    if (view === "indexes") return grid2(
      liveCard("Index usage", "pg_stat_user_indexes", d.source, e(EBox, { h: 210, option: donut(d.usage || [], [GREEN, TEAL, AMBER], "indexes") })),
      liveCard("Largest unused indexes", "GB", d.source, e(EBox, { h: 210, option: hbar(d.largest_unused || [], " GB", AMBER) })));
    if (view === "bloat") return liveCard("Table bloat", "% dead/bloat", d.source, e(EBox, { h: 220, option: hbar(d.bloat_pct || [], "%", AMBER) }));
    if (view === "vacuum") return liveCard("Oldest vacuum", "hours since last vacuum", d.source, e(EBox, { h: 220, option: hbar(d.oldest_hours || [], " h", PURPLE) }));
    if (view === "activity") return grid2(
      liveCard("Sessions by application", "24h", d.source, e(EBox, { h: 250, option: lineOption(d.sessions_by_app || [], "sessions") })),
      liveCard("Idle-in-transaction offenders", "minutes", d.source, e(EBox, { h: 220, option: hbar(d.idle_in_txn_minutes || [], " min", PINK) })));
    return null;
  });

  wrapLive("ClusterScreen", function () { return chartUrl("cluster"); }, function (d) {
    var rows = (d.members || []).map(function (m) { return [m.name, m.lag_mb === null ? 0 : m.lag_mb]; });
    return liveCard("Member replay lag", "current Patroni members", d.source, e(EBox, { h: 220, option: hbar(rows, " MB", PURPLE) }));
  });

  wrapLive("ReplicationHAScreen", function (p) { return chartUrl("replication", p.view === "logical" ? "logical" : "physical"); }, function (d, p) {
    if (p.view === "logical") return liveCard("Logical slot retained WAL", "GB", d.source, e(EBox, { h: 220, option: hbar((d.slots || []).map(function (s) { return [s.slot, s.retained_wal_gb]; }), " GB", PURPLE) }));
    return liveCard("Replica replay lag", "24h", d.source, e(EBox, { h: 220, option: lineOption([{ name: "lag", points: d.lag_series || [] }]) }));
  });

  wrapLive("CapacityPlanningScreen", function () { return chartUrl("capacity"); }, function (d) {
    var hist = d.history || [], fc = d.forecast || [], all = hist.concat(fc), o = baseAxis(labels(all));
    o.series = [{ name: "used", type: "line", smooth: true, symbol: "none", itemStyle: { color: PURPLE }, areaStyle: { opacity: .15 }, data: values(hist).concat(fc.map(function () { return null; })) }, { name: "forecast", type: "line", symbol: "none", lineStyle: { type: "dashed", color: AMBER }, data: hist.map(function () { return null; }).concat(values(fc)) }];
    return liveCard("Storage forecast", "live history + projection", d.source, e(EBox, { h: 260, option: o }));
  });

  wrapLive("AnomaliesScreen", function () { return chartUrl("anomalies"); }, function (d) {
    var o = baseAxis(labels(d.score_series || [])); o.series = [{ name: "anomaly score", type: "line", smooth: true, symbol: "none", itemStyle: { color: PURPLE }, data: values(d.score_series || []) }, { name: "anomaly", type: "effectScatter", itemStyle: { color: PINK }, data: (d.anomalies || []).map(function (p) { return [labels([p])[0], p[1]]; }) }];
    return liveCard("Anomaly detection", "model score", d.source, e(EBox, { h: 250, option: o }));
  });

  function heatRenderer(title) { return function (d) {
    var days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    var o = { tooltip: { position: "top" }, grid: { top: 10, right: 16, bottom: 44, left: 44 }, xAxis: { type: "category", data: Array.from({ length: 24 }, function (_, i) { return i; }) }, yAxis: { type: "category", data: days }, visualMap: { min: 0, max: Math.max.apply(null, (d.cells || []).map(function (c) { return c[2]; }).concat([1])), orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#f3f0fb", "#c4b5fd", PURPLE, "#4c1d95"] } }, series: [{ type: "heatmap", data: d.cells || [] }] };
    return liveCard(title, "hour × weekday", d.source, e(EBox, { h: 240, option: o }));
  }; }
  wrapLive("ActivityStreamScreen", function () { return chartUrl("heatmap"); }, heatRenderer("Activity heatmap"));
  wrapLive("AuditLogScreen", function () { return chartUrl("heatmap"); }, heatRenderer("Audit actions heatmap"));

  wrapLive("CollectorHealthScreen", function () { return chartUrl("collector"); }, function (d) {
    return liveCard("Collector success rate", "24h", d.source, e(EBox, { h: 220, option: lineOption([{ name: "success %", points: d.success_series || [] }]) }));
  });

  function versionRenderer(d) {
    return liveCard("Estate by major version", "cluster inventory", d.source, e(EBox, { h: 220, option: donut(d.estate || [], COLORS, "clusters") }));
  }
  wrapLive("VersionReadinessScreen", function () { return chartUrl("upgrades"); }, versionRenderer);
  wrapLive("LifecycleScreen", function (p) { return p.view === "upgrade" ? chartUrl("upgrades") : "/api/v1/charts/noop"; }, function (d, p) { return p.view === "upgrade" ? versionRenderer(d) : null; });
})();
