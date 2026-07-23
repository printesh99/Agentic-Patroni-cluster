/* logs-ui.js — Phase 6: live Logs Explorer + Log Analytics Center + Incident
 * Packs, wired to the backend log pillar (/api/v1/.../logs/* and
 * /log-analytics/*). Loaded after cloud_console_parity.js (which defines mock
 * shells) and before app.js, so these real implementations win by overriding
 * the global screen functions app.js renders by name. Classic script; uses the
 * vendored React global + the app's .card/.hd/.bd chrome + scoped .lq-* styles.
 */
(function () {
  "use strict";
  var React = window.React;
  if (!React) { return; }
  var h = React.createElement;
  var useState = React.useState, useEffect = React.useEffect, useRef = React.useRef;

  var SEV = ["fatal", "error", "warn", "info"];
  var SEV_COLOR = { fatal: "#b42318", error: "#d92d20", warn: "#dc6803", info: "#5b6472" };
  var RANGES = [["15m", "15m"], ["1h", "1h"], ["6h", "6h"], ["24h", "24h"]];

  function clusterId(cluster) {
    return cluster && cluster.id ? cluster.id : (window.ACTIVE_CLUSTER_ID || "prod");
  }

  // fetch JSON with array-aware query params (component=[a,b] -> repeated keys)
  function lqFetch(path, params) {
    var url = new URL(path, window.location.origin);
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v == null || v === "") return;
      if (Array.isArray(v)) { v.forEach(function (x) { url.searchParams.append(k, x); }); }
      else { url.searchParams.set(k, v); }
    });
    return fetch(url.toString(), { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function base(cluster) { return "/api/v1/clusters/" + encodeURIComponent(clusterId(cluster)); }
  function timeOf(iso) { return iso ? String(iso).slice(11, 19) : ""; }

  // ---- small shared UI bits --------------------------------------------
  function Pill(props) {
    var sev = props.sev || "info";
    return h("span", { className: "lq-pill", style: { background: SEV_COLOR[sev] || SEV_COLOR.info } },
      props.children != null ? props.children : sev);
  }

  function Chip(props) {
    return h("button", {
      className: "lq-chip" + (props.active ? " lq-chip-on" : ""),
      onClick: props.onClick, type: "button",
    }, props.label);
  }

  function toggle(list, val) {
    return list.indexOf(val) >= 0 ? list.filter(function (x) { return x !== val; }) : list.concat([val]);
  }

  // ===================================================================
  // Logs Explorer & Live Tail
  // ===================================================================
  function LogsExplorerScreen(props) {
    var cluster = props.cluster, lastRefresh = props.lastRefresh;
    var b = base(cluster);
    var s_q = useState(""), q = s_q[0], setQ = s_q[1];
    var s_comp = useState([]), comps = s_comp[0], setComps = s_comp[1];
    var s_lvl = useState([]), lvls = s_lvl[0], setLvls = s_lvl[1];
    var s_range = useState("1h"), range = s_range[0], setRange = s_range[1];
    var s_facets = useState({ components: [], levels: [] }), facets = s_facets[0], setFacets = s_facets[1];
    var s_data = useState(null), data = s_data[0], setData = s_data[1];
    var s_hist = useState(null), hist = s_hist[0], setHist = s_hist[1];
    var s_load = useState(false), loading = s_load[0], setLoading = s_load[1];
    var s_err = useState(null), err = s_err[0], setErr = s_err[1];
    var s_open = useState(null), openRow = s_open[0], setOpenRow = s_open[1];
    var s_ctx = useState(null), ctx = s_ctx[0], setCtx = s_ctx[1];
    var s_tail = useState(false), tailing = s_tail[0], setTailing = s_tail[1];
    var s_tailrows = useState([]), tailRows = s_tailrows[0], setTailRows = s_tailrows[1];
    var wsRef = useRef(null);

    // facets once per cluster
    useEffect(function () {
      lqFetch(b + "/logs/labels", { range: "6h" })
        .then(function (r) { setFacets({ components: r.components || [], levels: r.levels || [] }); })
        .catch(function () {});
    }, [clusterId(cluster)]);

    function runSearch() {
      setLoading(true); setErr(null); setOpenRow(null); setCtx(null);
      var params = { q: q, component: comps, level: lvls, range: range, limit: 300 };
      Promise.all([
        lqFetch(b + "/logs/search", params),
        lqFetch(b + "/logs/histogram", Object.assign({ step: histStep(range) }, params)),
      ]).then(function (res) {
        setData(res[0]); setHist(res[1]); setLoading(false);
      }).catch(function (e) { setErr(e.message || String(e)); setLoading(false); });
    }

    // auto-search on filter/refresh change
    useEffect(function () { runSearch(); },
      [clusterId(cluster), range, comps.join(","), lvls.join(","), lastRefresh]);

    function histStep(r) { return r === "15m" ? "1m" : r === "1h" ? "2m" : r === "6h" ? "10m" : "30m"; }

    function openContext(row) {
      if (openRow === row.ts_ns) { setOpenRow(null); setCtx(null); return; }
      setOpenRow(row.ts_ns); setCtx(null);
      lqFetch(b + "/logs/context/" + row.ts_ns,
        { component: row.component ? [row.component] : null, pod: row.pod, window: "5m", limit: 40 })
        .then(setCtx).catch(function () { setCtx({ before: [], after: [] }); });
    }

    // live tail
    useEffect(function () {
      if (!tailing) { if (wsRef.current) { wsRef.current.close(); wsRef.current = null; } return; }
      setTailRows([]);
      var proto = window.location.protocol === "https:" ? "wss" : "ws";
      var ws = new WebSocket(proto + "://" + window.location.host + "/ws/logs/tail");
      wsRef.current = ws;
      ws.onopen = function () { ws.send(JSON.stringify({ q: q, component: comps, level: lvls })); };
      ws.onmessage = function (ev) {
        try {
          var m = JSON.parse(ev.data);
          if (m.type === "entry") {
            setTailRows(function (prev) { return prev.concat([m]).slice(-250); });
          }
        } catch (e) {}
      };
      ws.onerror = function () {};
      return function () { ws.close(); wsRef.current = null; };
    }, [tailing]);

    function downloadUrl() {
      var url = new URL(b + "/logs/download", window.location.origin);
      url.searchParams.set("q", q); url.searchParams.set("range", range); url.searchParams.set("limit", 5000);
      comps.forEach(function (c) { url.searchParams.append("component", c); });
      lvls.forEach(function (l) { url.searchParams.append("level", l); });
      return url.toString();
    }

    var rows = tailing ? tailRows.slice().reverse() : (data && data.entries) || [];

    return h("div", { className: "page" },
      h("div", { className: "section-h" }, "Logs Explorer & Live Tail"),
      // ---- filter bar ----
      h("div", { className: "card" },
        h("div", { className: "bd lq-filters" },
          h("input", {
            className: "lq-search", placeholder: "Search log text…  (press Enter)", value: q,
            onChange: function (e) { setQ(e.target.value); },
            onKeyDown: function (e) { if (e.key === "Enter") runSearch(); },
          }),
          h("div", { className: "lq-row" },
            h("span", { className: "txt-xs muted" }, "Range"),
            RANGES.map(function (r) {
              return h(Chip, { key: r[0], label: r[1], active: range === r[0], onClick: function () { setRange(r[0]); } });
            })
          ),
          facets.components.length ? h("div", { className: "lq-row" },
            h("span", { className: "txt-xs muted" }, "Component"),
            facets.components.map(function (c) {
              return h(Chip, { key: c, label: c, active: comps.indexOf(c) >= 0, onClick: function () { setComps(toggle(comps, c)); } });
            })
          ) : null,
          facets.levels.length ? h("div", { className: "lq-row" },
            h("span", { className: "txt-xs muted" }, "Level"),
            facets.levels.map(function (l) {
              return h(Chip, { key: l, label: l, active: lvls.indexOf(l) >= 0, onClick: function () { setLvls(toggle(lvls, l)); } });
            })
          ) : null,
          h("div", { className: "lq-row" },
            h("button", { className: "lq-btn", type: "button", onClick: runSearch }, "Search"),
            h("button", {
              className: "lq-btn" + (tailing ? " lq-btn-on" : ""), type: "button",
              onClick: function () { setTailing(!tailing); },
            }, tailing ? "● Live Tail (on)" : "Live Tail"),
            h("a", { className: "lq-btn", href: downloadUrl(), download: "" }, "Export NDJSON"),
            h("span", { className: "txt-xs muted", style: { marginLeft: "auto" } },
              tailing ? (tailRows.length + " streamed") : ((data ? data.count : 0) + " lines"))
          )
        )
      ),
      // ---- histogram ----
      (!tailing && hist && hist.buckets && hist.buckets.length) ? h("div", { className: "card" },
        h("div", { className: "hd" }, h("span", null, "Volume"), h("span", { className: "txt-xs muted" }, "by severity")),
        h("div", { className: "bd" }, h(Histogram, { hist: hist }))
      ) : null,
      // ---- results ----
      h("div", { className: "card" },
        h("div", { className: "hd" },
          h("span", null, tailing ? "Live tail" : "Results"),
          loading ? h("span", { className: "txt-xs muted" }, "loading…") : null),
        h("div", { className: "bd" },
          err ? h("div", { className: "lq-err" }, "Error: " + err) :
          (!rows.length ? h("div", { className: "muted txt-sm" }, "No log lines for these filters.") :
            h("table", { className: "tbl lq-tbl" },
              h("thead", null, h("tr", null,
                h("th", { style: { width: "78px" } }, "Time"),
                h("th", { style: { width: "64px" } }, "Level"),
                h("th", { style: { width: "120px" } }, "Component"),
                h("th", null, "Message"))),
              h("tbody", null, rows.map(function (e, i) {
                var isOpen = openRow === e.ts_ns;
                return [
                  h("tr", { key: e.ts_ns + "-" + i, className: "lq-trow", onClick: function () { if (!tailing) openContext(e); } },
                    h("td", { className: "mono txt-xs" }, timeOf(e.ts)),
                    h("td", null, h(Pill, { sev: e.severity }, e.level || e.severity)),
                    h("td", { className: "txt-xs" }, e.component),
                    h("td", { className: "mono txt-xs lq-msg" }, e.message)),
                  (isOpen ? h("tr", { key: e.ts_ns + "-ctx" }, h("td", { colSpan: 4 },
                    h(Context, { ctx: ctx }))) : null),
                ];
              }))
            )
          )
        )
      )
    );
  }

  function Histogram(props) {
    var buckets = props.hist.buckets || [];
    var max = buckets.reduce(function (m, b) { return Math.max(m, b.total || 0); }, 1);
    return h("div", { className: "lq-hist" }, buckets.map(function (b, i) {
      return h("div", { className: "lq-hist-col", key: i, title: new Date(b.ts * 1000).toLocaleTimeString() + " · " + b.total },
        SEV.map(function (s) {
          var n = b[s] || 0; if (!n) return null;
          return h("div", { key: s, className: "lq-hist-seg",
            style: { height: (100 * n / max) + "%", background: SEV_COLOR[s] } });
        })
      );
    }));
  }

  function Context(props) {
    var ctx = props.ctx;
    if (!ctx) return h("div", { className: "muted txt-xs lq-ctx" }, "loading context…");
    var lines = (ctx.before || []).concat(ctx.after || []);
    if (!lines.length) return h("div", { className: "muted txt-xs lq-ctx" }, "no surrounding lines");
    return h("div", { className: "lq-ctx" }, lines.map(function (e, i) {
      return h("div", { key: i, className: "lq-ctx-line" },
        h("span", { className: "mono txt-xs muted" }, timeOf(e.ts) + " "),
        h("span", { className: "mono txt-xs" }, e.message));
    }));
  }

  // ===================================================================
  // Log Analytics Center
  // ===================================================================
  function LogAnalyticsScreen(props) {
    var cluster = props.cluster, lastRefresh = props.lastRefresh, b = base(cluster);
    var s_range = useState("6h"), range = s_range[0], setRange = s_range[1];
    var s = useState(null), d = s[0], setD = s[1];
    var s_err = useState(null), err = s_err[0], setErr = s_err[1];

    var rangeHours = { "15m": 1, "1h": 1, "6h": 6, "24h": 24 }[range] || 6;
    useEffect(function () {
      setErr(null);
      Promise.all([
        lqFetch(b + "/log-analytics/summary", { range: range }),
        lqFetch(b + "/log-analytics/signatures", { range: range, limit: 25 }),
        lqFetch(b + "/log-analytics/categories", { range: range }),
        lqFetch(b + "/log-analytics/findings", { range: range }),
        lqFetch("/api/v1/assistant/anomalies", { range_hours: rangeHours, step: "5m" }),
      ]).then(function (r) {
        setD({ summary: r[0], signatures: r[1], categories: r[2], findings: r[3], anomalies: r[4] });
      }).catch(function (e) { setErr(e.message || String(e)); });
    }, [clusterId(cluster), range, lastRefresh]);

    if (err) return h("div", { className: "page" }, h("div", { className: "lq-err" }, "Error: " + err));
    if (!d) return h("div", { className: "page" }, h("div", { className: "muted" }, "Loading log analytics…"));
    var sum = d.summary, bySev = sum.by_severity || {};

    return h("div", { className: "page" },
      h("div", { className: "section-h" }, "Log Analytics Center",
        h("span", { style: { marginLeft: "12px" } }, RANGES.map(function (r) {
          return h(Chip, { key: r[0], label: r[1], active: range === r[0], onClick: function () { setRange(r[0]); } });
        }))
      ),
      // tiles
      h("div", { className: "grid-4" },
        Tile("Total lines", sum.total, "info"),
        Tile("Errors", sum.error_count, "error"),
        Tile("Fatal", bySev.fatal || 0, "fatal"),
        Tile("Signatures", sum.signature_count, "info")
      ),
      h("div", { className: "grid-4", style: { marginTop: "8px" } },
        Tile("Warnings", bySev.warn || 0, "warn"),
        Tile("New (24h)", sum.new_signatures_24h, "warn"),
        Tile("Last error", timeOf(sum.last_error_ts) || "—", "info"),
        Tile("Components", Object.keys(sum.by_component || {}).length, "info")
      ),
      // anomalies (AI — log-volume z-score spikes)
      (d.anomalies && d.anomalies.count) ? h("div", { className: "card" },
        h("div", { className: "hd" }, h("span", null, "Anomalies"),
          h("span", { className: "txt-xs muted" }, (d.anomalies.count || 0) + " spikes (z≥3)")),
        h("div", { className: "bd" }, d.anomalies.anomalies.slice(0, 8).map(function (a, i) {
          return h("div", { className: "lq-finding", key: i },
            h(Pill, { sev: a.severity }, a.level),
            h("span", { className: "lq-find-title" }, a.count + " events (" + a.ratio + "× baseline " + a.baseline + ")"),
            h("span", { className: "txt-xs muted", style: { marginLeft: "auto" } }, "z=" + a.z + " · " + timeOf(a.ts)));
        }))
      ) : null,
      // findings
      h("div", { className: "card" },
        h("div", { className: "hd" }, h("span", null, "Findings"), h("span", { className: "txt-xs muted" }, (d.findings.count || 0) + " open")),
        h("div", { className: "bd" }, (d.findings.findings || []).length ?
          d.findings.findings.map(function (f, i) {
            return h("div", { className: "lq-finding", key: i },
              h("span", { className: "lq-pill", style: { background: findColor(f.severity) } }, f.severity),
              h("span", { className: "lq-find-cat txt-xs muted" }, f.category),
              h("span", { className: "lq-find-title" }, f.title),
              h("span", { className: "txt-xs muted", style: { marginLeft: "auto" } }, timeOf(f.last_seen)));
          }) : h("div", { className: "muted txt-sm" }, "No findings — clean window."))
      ),
      // categories + signatures
      h("div", { className: "grid-2" },
        h("div", { className: "card" },
          h("div", { className: "hd" }, "Categories"),
          h("div", { className: "bd" }, (d.categories.categories || []).length ?
            h("table", { className: "tbl" },
              h("thead", null, h("tr", null, h("th", null, "Category"), h("th", null, "Total"), h("th", null, "Err"), h("th", null, "Warn"))),
              h("tbody", null, d.categories.categories.map(function (c) {
                return h("tr", { key: c.category },
                  h("td", null, c.category), h("td", { className: "num" }, c.count),
                  h("td", { className: "num" }, c.error), h("td", { className: "num" }, c.warn));
              }))) : h("div", { className: "muted txt-sm" }, "No categorized events."))
        ),
        h("div", { className: "card" },
          h("div", { className: "hd" }, "Top signatures"),
          h("div", { className: "bd lq-sig-bd" }, (d.signatures.signatures || []).length ?
            d.signatures.signatures.map(function (sg) {
              return h("div", { className: "lq-sig", key: sg.signature_id },
                h(Pill, { sev: sg.severity }, sg.count),
                h("span", { className: "lq-sig-cat txt-xs muted" }, sg.category),
                h("span", { className: "lq-sig-pat mono txt-xs", title: sg.sample }, sg.pattern));
            }) : h("div", { className: "muted txt-sm" }, "No signatures."))
        )
      )
    );

    function Tile(label, val, sev) {
      return h("div", { className: "card lq-tile", key: label },
        h("div", { className: "bd" },
          h("div", { className: "txt-xs muted" }, label),
          h("div", { className: "lq-tile-num", style: { color: SEV_COLOR[sev] } }, val)));
    }
  }

  function findColor(sev) {
    return sev === "critical" ? "#b42318" : sev === "high" ? "#d92d20" : sev === "medium" ? "#dc6803" : "#5b6472";
  }

  // ===================================================================
  // Incident Packs — bundle current findings + a saved explorer query
  // ===================================================================
  function IncidentPacksScreen(props) {
    var cluster = props.cluster, lastRefresh = props.lastRefresh, b = base(cluster);
    var s = useState(null), d = s[0], setD = s[1];
    useEffect(function () {
      lqFetch(b + "/log-analytics/findings", { range: "24h" }).then(setD).catch(function () { setD({ findings: [] }); });
    }, [clusterId(cluster), lastRefresh]);
    if (!d) return h("div", { className: "page" }, h("div", { className: "muted" }, "Loading incident packs…"));
    var findings = d.findings || [];
    function exportPack() {
      var blob = new Blob([JSON.stringify({ cluster: clusterId(cluster), generated: new Date().toISOString(), findings: findings }, null, 2)],
        { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "incident-pack-" + clusterId(cluster) + ".json"; a.click();
    }
    return h("div", { className: "page" },
      h("div", { className: "section-h" }, "Incident Packs"),
      h("div", { className: "card" },
        h("div", { className: "hd" }, h("span", null, "Last 24h findings"),
          h("button", { className: "lq-btn", type: "button", onClick: exportPack }, "Export pack (JSON)")),
        h("div", { className: "bd" }, findings.length ? findings.map(function (f, i) {
          return h("div", { className: "lq-finding", key: i },
            h("span", { className: "lq-pill", style: { background: findColor(f.severity) } }, f.severity),
            h("span", { className: "lq-find-cat txt-xs muted" }, f.category),
            h("span", { className: "lq-find-title" }, f.title));
        }) : h("div", { className: "muted txt-sm" }, "No incidents in the last 24h."))
      )
    );
  }

  // ---- scoped styles ----------------------------------------------------
  function injectStyles() {
    if (document.getElementById("lq-styles")) return;
    var css = [
      ".lq-filters{display:flex;flex-direction:column;gap:8px}",
      ".lq-search{width:100%;padding:8px 10px;border:1px solid var(--border,#d0d5dd);border-radius:8px;background:var(--panel,#fff);color:inherit;font-size:13px}",
      ".lq-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap}",
      ".lq-chip{padding:3px 10px;border:1px solid var(--border,#d0d5dd);border-radius:999px;background:transparent;color:inherit;font-size:12px;cursor:pointer}",
      ".lq-chip-on{background:#7c3aed;border-color:#7c3aed;color:#fff}",
      ".lq-btn{padding:5px 12px;border:1px solid var(--border,#d0d5dd);border-radius:8px;background:var(--panel,#fff);color:inherit;font-size:12px;cursor:pointer;text-decoration:none;display:inline-block}",
      ".lq-btn-on{background:#b42318;border-color:#b42318;color:#fff}",
      ".lq-pill{display:inline-block;min-width:30px;text-align:center;padding:1px 8px;border-radius:999px;color:#fff;font-size:11px;font-weight:600}",
      ".lq-tbl td{vertical-align:top}",
      ".lq-trow{cursor:pointer}.lq-trow:hover{background:rgba(127,127,127,.08)}",
      ".lq-msg{white-space:pre-wrap;word-break:break-word}",
      ".lq-err{color:#b42318;font-size:13px}",
      ".lq-hist{display:flex;align-items:flex-end;gap:2px;height:90px}",
      ".lq-hist-col{flex:1;display:flex;flex-direction:column-reverse;height:100%;min-width:2px;background:rgba(127,127,127,.06)}",
      ".lq-hist-seg{width:100%}",
      ".lq-ctx{background:rgba(127,127,127,.06);border-left:3px solid #7c3aed;padding:6px 8px;margin:2px 0}",
      ".lq-ctx-line{white-space:pre-wrap;word-break:break-word}",
      ".lq-tile-num{font-size:24px;font-weight:700;margin-top:2px}",
      ".lq-finding{display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid rgba(127,127,127,.12)}",
      ".lq-find-title{font-size:13px}.lq-find-cat{min-width:96px}",
      ".lq-sig{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid rgba(127,127,127,.10)}",
      ".lq-sig-cat{min-width:90px}.lq-sig-pat{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".lq-sig-bd{max-height:360px;overflow:auto}",
    ].join("");
    var st = document.createElement("style");
    st.id = "lq-styles"; st.textContent = css;
    document.head.appendChild(st);
  }
  injectStyles();

  // override the mock shells so app.js renders these by global name
  window.LogsExplorerScreen = LogsExplorerScreen;
  window.LogAnalyticsScreen = LogAnalyticsScreen;
  window.IncidentPacksScreen = IncidentPacksScreen;
})();
