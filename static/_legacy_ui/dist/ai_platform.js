/* ai_platform.js - live-only AI Platform panels. */
(function () {
  "use strict";
  var React = window.React;
  if (!React) return;
  var e = React.createElement;
  var useEffect = React.useEffect;
  var useMemo = React.useMemo;
  var useState = React.useState;

  var C = {
    crit: "#c0392b",
    high: "#d35400",
    med: "#b7791f",
    low: "#2f80ed",
    info: "#5b6472",
    ok: "#1a7f4b",
    warn: "#b7791f",
    violet: "#6d28d9",
    teal: "#0f766e",
    border: "var(--border)"
  };

  function clusterId(props) {
    return (props && props.cluster && props.cluster.id) || window.ACTIVE_CLUSTER_ID || "uat";
  }

  function apiUrl(path, params) {
    var url = new URL(path, window.location.origin);
    Object.keys(params || {}).forEach(function (key) {
      var value = params[key];
      if (value !== undefined && value !== null && value !== "" && value !== "ALL") {
        url.searchParams.set(key, value);
      }
    });
    return url.toString();
  }

  function json(path, params, options) {
    var init = Object.assign({ cache: "no-store", headers: {} }, options || {});
    if (init.body !== undefined && !init.headers["content-type"]) {
      init.headers["content-type"] = "application/json";
    }
    return fetch(apiUrl(path, params), init).then(function (res) {
      return res.text().then(function (text) {
        var body = text ? JSON.parse(text) : null;
        if (!res.ok) throw new Error((body && (body.detail || body.error)) || res.statusText || ("HTTP " + res.status));
        return body;
      });
    });
  }

  function useLive(path, params, deps) {
    var state = useState({ loading: true, data: null, error: null });
    var value = state[0], setValue = state[1];
    useEffect(function () {
      var alive = true;
      setValue(function (old) { return { loading: true, data: old.data, error: null }; });
      json(path, params).then(function (payload) {
        if (alive) setValue({ loading: false, data: payload, error: null });
      }).catch(function (err) {
        if (alive) setValue({ loading: false, data: null, error: err.message || String(err) });
      });
      return function () { alive = false; };
    }, deps || [path, JSON.stringify(params || {})]);
    return value;
  }

  function arr(payload, keys) {
    if (Array.isArray(payload)) return payload;
    for (var i = 0; i < keys.length; i += 1) {
      if (payload && Array.isArray(payload[keys[i]])) return payload[keys[i]];
    }
    return [];
  }

  function fmtDate(value) {
    if (!value) return "-";
    try { return new Date(value).toLocaleString("en-GB", { hour12: false }); }
    catch (_err) { return String(value); }
  }

  function titleCase(value) {
    var s = String(value || "").toLowerCase();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "-";
  }

  function tone(value) {
    var v = String(value || "").toUpperCase();
    if (v === "CRITICAL" || v === "HIGH" || v === "FAILED" || v === "REJECTED") return C.crit;
    if (v === "MEDIUM" || v === "PENDING" || v === "RUNNING") return C.warn;
    if (v === "LOW" || v === "INFO") return C.low;
    if (v === "COMPLETED" || v === "APPROVED" || v === "EXECUTED" || v === "SUCCESS") return C.ok;
    return C.info;
  }

  function pill(text, color) {
    return e("span", {
      className: "pill",
      style: { borderColor: color || C.border, color: color || "var(--fg)", fontSize: 10.5 }
    }, e("span", { className: "dot", style: { background: color || C.info } }), text);
  }

  function sourceBadge(state) {
    if (state.loading) return pill("loading", C.info);
    if (state.error) return pill("endpoint error", C.crit);
    return pill("live", C.ok);
  }

  function Card(props) {
    return e("div", { className: "card", style: props.style || null },
      e("div", { className: "hd" }, props.title, props.meta ? e("span", { className: "meta" }, props.meta) : null),
      e("div", { className: "bd" }, props.children));
  }

  function Stat(props) {
    return e("div", { className: "stat" },
      e("div", { className: "lbl" }, props.label),
      e("div", { className: "val", style: { fontSize: 22 } }, props.value == null ? "-" : props.value),
      props.sub ? e("div", { className: "sub" }, props.sub) : null);
  }

  function Empty(props) {
    return e("div", { style: { textAlign: "center", padding: "28px 16px", color: "var(--fg-dim)" } },
      e("div", { style: { fontSize: 13, fontWeight: 700, color: "var(--fg)" } }, props.title || "No live records"),
      e("div", { style: { fontSize: 11.5, marginTop: 4 } }, props.hint || "The backing endpoint returned no rows for this cluster."));
  }

  function ErrorBox(props) {
    if (!props.error) return null;
    return e("div", { className: "tile-error flex-row", style: { marginBottom: 10 } },
      e("strong", null, "Live endpoint error"),
      e("span", { className: "muted txt-xs", style: { marginLeft: 8 } }, props.error));
  }

  function normRec(row) {
    row = row || {};
    var id = row.recommendation_id || row.id;
    return {
      raw: row,
      id: id,
      severity: String(row.severity || "INFO").toUpperCase(),
      category: String(row.category || "OTHER").toUpperCase(),
      cluster: row.cluster_name || row.cluster || "-",
      database: row.database_name || "",
      object: row.object_name || "",
      finding: row.finding || row.title || ("Recommendation " + (id || "")),
      root: row.root_cause || "",
      action: row.recommendation || row.recommended_action || "",
      sql: row.recommended_sql || "",
      rollback: row.rollback_sql || "",
      risk: row.risk_level || "",
      confidence: row.confidence_score == null ? null : Math.round(Number(row.confidence_score) * 100),
      status: String(row.approval_status || row.status || "PENDING").toUpperCase(),
      execution: String(row.execution_status || "").toUpperCase(),
      created: row.created_at || row.created || null,
      evidence: row.evidence || {}
    };
  }

  function normAudit(row) {
    row = row || {};
    return {
      id: row.action_id || row.id,
      recommendation_id: row.recommendation_id,
      ts: row.created_at || row.execution_started_at,
      type: row.action_type || row.type || "-",
      actor: row.executed_by || row.approved_by || row.requested_by || row.user || "-",
      status: row.execution_status || row.status || "-",
      output: row.execution_output || row.output || row.error_message || ""
    };
  }

  function RecTable(props) {
    var rows = props.rows || [];
    if (!rows.length) return e(Empty, { title: props.emptyTitle || "No live recommendations" });
    return e("div", { style: { overflowX: "auto" } },
      e("table", { className: "tbl" },
        e("thead", null, e("tr", null,
          ["Created", "Severity", "Category", "Status", "Finding", "Object", "Confidence", ""].map(function (h) {
            return e("th", { key: h }, h);
          }))),
        e("tbody", null, rows.map(function (r) {
          return e("tr", { key: r.id, onClick: function () { props.onSelect && props.onSelect(r); }, style: { cursor: props.onSelect ? "pointer" : "default" } },
            e("td", { className: "txt-xs nowrap" }, fmtDate(r.created)),
            e("td", null, pill(r.severity, tone(r.severity))),
            e("td", { className: "mono txt-xs" }, r.category),
            e("td", null, pill(r.status, tone(r.status))),
            e("td", { style: { minWidth: 320, whiteSpace: "normal" } },
              e("strong", null, r.finding),
              r.action ? e("div", { className: "muted txt-xs" }, r.action) : null),
            e("td", { className: "mono txt-xs" }, r.object || r.database || r.cluster || "-"),
            e("td", { className: "num" }, r.confidence == null ? "-" : r.confidence + "%"),
            e("td", null, props.onSelect ? e("button", { className: "btn ghost sm", onClick: function (ev) { ev.stopPropagation(); props.onSelect(r); } }, "Detail") : null));
        }))));
  }

  function Detail(props) {
    var r = props.rec;
    if (!r) return e(Empty, { title: "Select a live recommendation" });
    return e(Card, { title: "Recommendation detail", meta: pill("#" + r.id, C.violet) },
      e("div", { className: "flex-row", style: { gap: 8, flexWrap: "wrap", marginBottom: 10 } },
        pill(r.severity, tone(r.severity)), pill(r.category, C.violet), pill(r.status, tone(r.status)),
        r.risk ? pill("risk " + r.risk, tone(r.risk)) : null),
      e("div", { className: "section-h" }, "Finding"),
      e("div", { className: "txt-sm" }, r.finding),
      e("div", { className: "section-h mt-3" }, "Root Cause"),
      e("div", { className: "txt-sm" }, r.root || "-"),
      e("div", { className: "section-h mt-3" }, "Recommendation"),
      e("div", { className: "txt-sm" }, r.action || "-"),
      (r.sql || r.rollback) ? e("div", { className: "grid-2 mt-3" },
        e("div", null, e("div", { className: "txt-xs muted" }, "Recommended SQL"), e("pre", { className: "logbox", style: { whiteSpace: "pre-wrap" } }, r.sql || "-")),
        e("div", null, e("div", { className: "txt-xs muted" }, "Rollback SQL"), e("pre", { className: "logbox", style: { whiteSpace: "pre-wrap" } }, r.rollback || "-"))) : null,
      e("div", { className: "section-h mt-3" }, "Evidence"),
      e("pre", { className: "logbox", style: { whiteSpace: "pre-wrap", maxHeight: 260 } }, JSON.stringify(r.evidence || {}, null, 2)),
      props.actions ? e("div", { className: "flex-row mt-3", style: { gap: 8, flexWrap: "wrap" } }, props.actions(r)) : null);
  }

  function useRecommendations(props, limit) {
    var cid = clusterId(props);
    var st = useLive("/api/v1/ai/recommendations", { cluster_id: cid, limit: limit || 200 }, [cid, props.lastRefresh]);
    return {
      loading: st.loading,
      error: st.error,
      data: st.data,
      rows: arr(st.data, ["recommendations", "items", "results"]).map(normRec)
    };
  }

  function Overview(props) {
    var cid = clusterId(props);
    var ov = useLive("/api/v1/ai/overview", { cluster_id: cid }, [cid, props.lastRefresh]);
    var gateway = useLive("/api/v1/ai/model-gateway/status", {}, [props.lastRefresh]);
    var recs = useRecommendations(props, 200);
    var rows = recs.rows;
    var open = rows.filter(function (r) { return r.status !== "EXECUTED" && r.status !== "REJECTED"; });
    var pending = rows.filter(function (r) { return r.status === "PENDING"; });
    var high = open.filter(function (r) { return r.severity === "CRITICAL" || r.severity === "HIGH"; });
    var provider = gateway.data || {};
    var agent = (ov.data && ov.data.agent) || provider.agent || {};
    return e("div", { className: "page" },
      e(ErrorBox, { error: ov.error || recs.error || gateway.error }),
      e("div", { className: "grid-4" },
        e(Stat, { label: "Recommendations", value: rows.length, sub: "live AI records" }),
        e(Stat, { label: "Open high severity", value: high.length, sub: "critical / high" }),
        e(Stat, { label: "Pending approvals", value: pending.length, sub: "DBA gate" }),
        e(Stat, { label: "Agent state", value: agent.running ? "RUNNING" : "IDLE", sub: sourceBadge(ov) })),
      e("div", { className: "grid-2 mt-3" },
        e(Card, { title: "Model Gateway", meta: sourceBadge(gateway) },
          e("div", { className: "grid-2" },
            e(Stat, { label: "Provider", value: provider.provider || (provider.provider_status && provider.provider_status.provider) || "disabled" }),
            e(Stat, { label: "Model", value: provider.model || "-" }),
            e(Stat, { label: "Configured", value: provider.configured ? "Yes" : "No" }),
            e(Stat, { label: "API key", value: provider.api_key_present ? "Present" : "Missing" }))),
        e(Card, { title: "Latest Live Recommendations", meta: sourceBadge(recs) },
          e(RecTable, { rows: rows.slice(0, 5), emptyTitle: "No live AI recommendations" }))));
  }

  function Gateway(props) {
    var st = useLive("/api/v1/ai/model-gateway/status", {}, [props.lastRefresh]);
    var d = st.data || {};
    return e("div", { className: "page" },
      e(ErrorBox, { error: st.error }),
      e("div", { className: "grid-4" },
        e(Stat, { label: "Provider", value: d.provider || "disabled" }),
        e(Stat, { label: "Model", value: d.model || "-" }),
        e(Stat, { label: "Configured", value: d.configured ? "Yes" : "No" }),
        e(Stat, { label: "API key", value: d.api_key_present ? "Present" : "Missing" })),
      e(Card, { title: "Raw Gateway Status", meta: sourceBadge(st), style: { marginTop: 12 } },
        e("pre", { className: "logbox", style: { whiteSpace: "pre-wrap" } }, JSON.stringify(d, null, 2))));
  }

  function RagKb(props) {
    var qState = useState("");
    var q = qState[0], setQ = qState[1];
    var activeState = useState("");
    var active = activeState[0], setActive = activeState[1];
    var st = useLive("/api/v1/ai/rag/kb", { query: active, limit: 50 }, [active, props.lastRefresh]);
    var docs = arr(st.data, ["documents", "items", "results"]);
    return e("div", { className: "page" },
      e(ErrorBox, { error: st.error }),
      e(Card, { title: "Knowledge Base Search", meta: sourceBadge(st) },
        e("div", { className: "flex-row", style: { gap: 8, flexWrap: "wrap" } },
          e("input", { value: q, onChange: function (ev) { setQ(ev.target.value); }, placeholder: "Search runbooks and incidents", style: { minWidth: 320 } }),
          e("button", { className: "btn sm primary", onClick: function () { setActive(q); } }, "Search"),
          e("span", { className: "pill muted" }, (st.data && st.data.semantic_enabled) ? "semantic on" : "keyword mode"))),
      e(Card, { title: "Live Documents", meta: docs.length + " rows", style: { marginTop: 12 } },
        docs.length ? e("div", { style: { overflowX: "auto" } },
          e("table", { className: "tbl" },
            e("thead", null, e("tr", null, ["Title", "Runbook", "Method", "Score", "Source"].map(function (h) { return e("th", { key: h }, h); }))),
            e("tbody", null, docs.map(function (d, i) {
              return e("tr", { key: d.id || i },
                e("td", null, d.title || d.chunk_title || "-"),
                e("td", { className: "mono txt-xs" }, d.runbook_id || "-"),
                e("td", null, d.method || "-"),
                e("td", { className: "num" }, d.score == null ? "-" : Number(d.score).toFixed(3)),
                e("td", { className: "txt-xs" }, d.source_file || d.source || "-"));
            })))) : e(Empty, { title: "No live knowledge-base rows" })));
  }

  function Agents(props) {
    var cid = clusterId(props);
    var st = useLive("/api/v1/ai/agents", { limit: 50 }, [props.lastRefresh]);
    var rows = arr(st.data, ["runs", "agents", "items", "results"]);
    var busy = useState(false);
    var running = busy[0], setRunning = busy[1];
    function runNow() {
      setRunning(true);
      json("/api/v1/ai/agents/run-now", null, {
        method: "POST",
        body: JSON.stringify({ cluster_name: cid, triggered_by: "web-ui" })
      }).then(function () {
        setRunning(false);
        window.location.reload();
      }).catch(function (err) {
        setRunning(false);
        alert(err.message || String(err));
      });
    }
    return e("div", { className: "page" },
      e(ErrorBox, { error: st.error }),
      e(Card, { title: "Agent Scheduler", meta: sourceBadge(st) },
        e("div", { className: "flex-row", style: { gap: 8, flexWrap: "wrap", marginBottom: 12 } },
          e("button", { className: "btn sm primary", disabled: running, onClick: runNow }, running ? "Running" : "Run agent now"),
          e("span", { className: "pill muted" }, rows.length + " runs")),
        rows.length ? e("div", { style: { overflowX: "auto" } },
          e("table", { className: "tbl" },
            e("thead", null, e("tr", null, ["Run", "Trigger", "Status", "Started", "Finished", "Summary"].map(function (h) { return e("th", { key: h }, h); }))),
            e("tbody", null, rows.map(function (r) {
              return e("tr", { key: r.run_id || r.id },
                e("td", { className: "mono txt-xs" }, "#" + (r.run_id || r.id)),
                e("td", null, r.trigger_type || "-"),
                e("td", null, pill(r.status || "-", tone(r.status))),
                e("td", { className: "txt-xs" }, fmtDate(r.started_at)),
                e("td", { className: "txt-xs" }, fmtDate(r.finished_at)),
                e("td", { className: "txt-xs", style: { maxWidth: 420, whiteSpace: "normal" } }, r.error_message || r.summary || "-"));
            })))) : e(Empty, { title: "No live agent runs" })));
  }

  function RecommendationsView(props) {
    var recs = useRecommendations(props, 300);
    var selectedState = useState(null);
    var selected = selectedState[0], setSelected = selectedState[1];
    var rows = recs.rows;
    return e("div", { className: "page" },
      e(ErrorBox, { error: recs.error }),
      e(Card, { title: "Recommendations Inbox", meta: sourceBadge(recs) },
        e(RecTable, { rows: rows, onSelect: setSelected, emptyTitle: "No live recommendations" })),
      e("div", { style: { marginTop: 12 } }, e(Detail, { rec: selected || rows[0] || null })));
  }

  function ApprovalView(props) {
    var recs = useRecommendations(props, 300);
    var selectedState = useState(null);
    var selected = selectedState[0], setSelected = selectedState[1];
    var commentState = useState("");
    var comment = commentState[0], setComment = commentState[1];
    var busyState = useState(false);
    var busy = busyState[0], setBusy = busyState[1];
    var pending = recs.rows.filter(function (r) { return r.status === "PENDING"; });
    function act(r, action) {
      setBusy(true);
      json("/api/v1/ai/recommendations/" + encodeURIComponent(r.id) + "/" + action, null, {
        method: "POST",
        body: JSON.stringify({ actor: "web-ui", reason: comment || undefined })
      }).then(function () { setBusy(false); window.location.reload(); })
        .catch(function (err) { setBusy(false); alert(err.message || String(err)); });
    }
    return e("div", { className: "page" },
      e(ErrorBox, { error: recs.error }),
      e("div", { className: "grid-3" },
        e(Stat, { label: "Awaiting approval", value: pending.length }),
        e(Stat, { label: "Approved", value: recs.rows.filter(function (r) { return r.status === "APPROVED"; }).length }),
        e(Stat, { label: "Rejected", value: recs.rows.filter(function (r) { return r.status === "REJECTED"; }).length })),
      e(Card, { title: "Approval Queue", meta: sourceBadge(recs), style: { marginTop: 12 } },
        e(RecTable, { rows: pending, onSelect: setSelected, emptyTitle: "No live approvals pending" })),
      e("div", { style: { marginTop: 12 } }, e(Detail, {
        rec: selected || pending[0] || null,
        actions: function (r) {
          return [
            e("input", { key: "comment", value: comment, onChange: function (ev) { setComment(ev.target.value); }, placeholder: "approval or rejection note", style: { minWidth: 260 } }),
            e("button", { key: "approve", className: "btn sm primary", disabled: busy, onClick: function () { act(r, "approve"); } }, "Approve"),
            e("button", { key: "reject", className: "btn sm ghost", disabled: busy || !comment.trim(), onClick: function () { act(r, "reject"); } }, "Reject")
          ];
        }
      })));
  }

  function ExecutorView(props) {
    var recs = useRecommendations(props, 300);
    var approved = recs.rows.filter(function (r) { return r.status === "APPROVED"; });
    var busyState = useState(false);
    var busy = busyState[0], setBusy = busyState[1];
    function exec(r) {
      setBusy(true);
      json("/api/v1/ai/recommendations/" + encodeURIComponent(r.id) + "/execute", null, {
        method: "POST",
        body: JSON.stringify({ actor: "web-ui", confirm: true })
      }).then(function () { setBusy(false); window.location.reload(); })
        .catch(function (err) { setBusy(false); alert(err.message || String(err)); });
    }
    return e("div", { className: "page" },
      e(ErrorBox, { error: recs.error }),
      e(Card, { title: "Approved Actions", meta: sourceBadge(recs) },
        approved.length ? approved.map(function (r) {
          return e("div", { key: r.id, className: "flex-row", style: { gap: 10, padding: "10px 0", borderBottom: "1px solid var(--divider)" } },
            pill(r.severity, tone(r.severity)),
            e("div", { style: { flex: 1, minWidth: 0 } },
              e("strong", null, "#" + r.id + " - " + r.finding),
              e("div", { className: "muted txt-xs" }, r.sql || r.action || "No SQL payload")),
            e("button", { className: "btn sm primary", disabled: busy, onClick: function () { exec(r); } }, "Execute"));
        }) : e(Empty, { title: "No approved live actions" })));
  }

  function EvidenceView(props) {
    var recs = useRecommendations(props, 100);
    var selectedState = useState(null);
    var selected = selectedState[0], setSelected = selectedState[1];
    var row = selected || recs.rows[0] || null;
    return e("div", { className: "page" },
      e(ErrorBox, { error: recs.error }),
      e("div", { className: "grid-2" },
        e(Card, { title: "Recommendations With Evidence", meta: sourceBadge(recs) },
          e(RecTable, { rows: recs.rows, onSelect: setSelected, emptyTitle: "No live evidence records" })),
        e(Detail, { rec: row })));
  }

  function AuditLogs(props) {
    var cid = clusterId(props);
    var st = useLive("/api/v1/ai/audit", { cluster_id: cid, limit: 300 }, [cid, props.lastRefresh]);
    var rows = arr(st.data, ["audit", "items", "results"]).map(normAudit);
    return e("div", { className: "page" },
      e(ErrorBox, { error: st.error }),
      e(Card, { title: "AI Audit Logs", meta: sourceBadge(st) },
        rows.length ? e("div", { style: { overflowX: "auto" } },
          e("table", { className: "tbl" },
            e("thead", null, e("tr", null, ["Time", "Type", "Actor", "Recommendation", "Status", "Output"].map(function (h) { return e("th", { key: h }, h); }))),
            e("tbody", null, rows.map(function (r) {
              return e("tr", { key: r.id },
                e("td", { className: "txt-xs nowrap" }, fmtDate(r.ts)),
                e("td", null, r.type),
                e("td", { className: "mono txt-xs" }, r.actor),
                e("td", { className: "mono txt-xs" }, r.recommendation_id || "-"),
                e("td", null, pill(r.status, tone(r.status))),
                e("td", { className: "txt-xs", style: { maxWidth: 420, whiteSpace: "normal" } }, r.output || "-"));
            })))) : e(Empty, { title: "No live audit rows" })));
  }

  function Governance(props) {
    var status = useLive("/api/ai-agent/status", {}, [props.lastRefresh]);
    var scheduler = useLive("/api/v1/scheduler/status", {}, [props.lastRefresh]);
    var s = status.data || {};
    var sch = scheduler.data || {};
    return e("div", { className: "page" },
      e(ErrorBox, { error: status.error || scheduler.error }),
      e("div", { className: "grid-4" },
        e(Stat, { label: "Agent", value: s.running ? "RUNNING" : "IDLE", sub: sourceBadge(status) }),
        e(Stat, { label: "Scheduler", value: sch.enabled ? "RUNNING" : "STOPPED", sub: sourceBadge(scheduler) }),
        e(Stat, { label: "Execution", value: s.execution_enabled ? "Enabled" : "Disabled" }),
        e(Stat, { label: "Email", value: s.email_enabled ? "Enabled" : "Disabled" })),
      e(Card, { title: "Live Governance Status", meta: "from service endpoints", style: { marginTop: 12 } },
        e("pre", { className: "logbox", style: { whiteSpace: "pre-wrap" } }, JSON.stringify({ ai_agent: s, scheduler: sch }, null, 2))));
  }

  window.__AIPLAT = {
    C: C,
    pill: pill,
    sourceBadge: sourceBadge,
    json: json,
    useLive: useLive,
    Overview: Overview,
    Gateway: Gateway,
    RagKb: RagKb,
    Agents: Agents,
    Evidence: EvidenceView,
    Inbox: RecommendationsView,
    Approvals: ApprovalView,
    Executor: ExecutorView,
    AuditLogs: AuditLogs,
    Governance: Governance
  };

  window.AIPlatformScreen = function (props) {
    var view = (props && props.view) || "overview";
    if (view === "overview") return e(Overview, props);
    if (view === "gateway") return e(Gateway, props);
    if (view === "rag") return e(RagKb, props);
    if (view === "agents") return e(Agents, props);
    if (view === "evidence") return e(EvidenceView, props);
    if (view === "inbox") return e(RecommendationsView, props);
    if (view === "approvals") return e(ApprovalView, props);
    if (view === "executor") return e(ExecutorView, props);
    if (view === "audit") return e(AuditLogs, props);
    if (view === "governance") return e(Governance, props);
    return e(Overview, props);
  };
})();
