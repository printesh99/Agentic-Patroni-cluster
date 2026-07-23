(() => {
  // object_monitor_v31_dba_fixed/static/ai_ops.jsx
  function aioUrl(path, params) {
    var url = new URL("/api/v1/ai" + path, window.location.origin);
    Object.entries(params || {}).forEach(function(e) {
      if (e[1] != null && e[1] !== "") url.searchParams.set(e[0], e[1]);
    });
    return url.toString();
  }
  function aioGet(path, params) {
    return fetch(aioUrl(path, params), { cache: "no-store" }).then(hbzJsonResponse);
  }
  function aioPost(path, body) {
    return fetch(aioUrl(path, {}), {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(hbzJsonResponse);
  }
  function useAioLoad(fetcher, deps) {
    var s = useState({ data: null, loading: true, error: null });
    var state = s[0], setState = s[1];
    useEffect(function() {
      var alive = true;
      setState({ data: null, loading: true, error: null });
      fetcher().then(function(d) {
        if (alive) setState({ data: d, loading: false, error: null });
      }).catch(function(e) {
        if (alive) setState({ data: null, loading: false, error: e.message || String(e) });
      });
      return function() {
        alive = false;
      };
    }, deps || []);
    return [state.data, state.loading, state.error];
  }
  function aioSettled(entries) {
    return Promise.allSettled(entries.map(function(entry) {
      return entry[1]();
    })).then(function(results) {
      var data = {}, errors = {};
      results.forEach(function(result, index) {
        var key = entries[index][0];
        if (result.status === "fulfilled") data[key] = result.value;
        else errors[key] = result.reason && (result.reason.message || String(result.reason));
      });
      if (!Object.keys(data).length) throw new Error(Object.values(errors).join(" \xB7 ") || "AI Operations data unavailable");
      data._errors = errors;
      return data;
    });
  }
  function aioNum(value, fallback) {
    var parsed = typeof value === "string" ? Number(value.replace(/,/g, "")) : Number(value);
    return Number.isFinite(parsed) ? parsed : fallback == null ? null : fallback;
  }
  function aioTone(value) {
    var text = String(value || "unknown").toLowerCase();
    if (/critical|high|failed|error|rejected|inactive/.test(text)) return "danger";
    if (/medium|pending|warning|disabled|unknown/.test(text)) return "warn";
    if (/low|complete|active|running|approved|enabled|healthy|streaming|sync/.test(text)) return "ok";
    return "info";
  }
  function aioLabel(value) {
    return String(value == null ? "unknown" : value).replace(/[_-]+/g, " ").replace(/\b\w/g, function(c) {
      return c.toUpperCase();
    });
  }
  function aioCountRows(rows, field) {
    var counts = {};
    (rows || []).forEach(function(row) {
      var key = aioLabel(row && row[field]);
      counts[key] = (counts[key] || 0) + 1;
    });
    return Object.keys(counts).map(function(key) {
      return { label: key, value: counts[key], tone: aioTone(key) };
    });
  }
  function AioStatus({ value }) {
    return /* @__PURE__ */ React.createElement("span", { className: "pill " + aioTone(value) }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), aioLabel(value));
  }
  function AioPartial({ errors }) {
    var keys = Object.keys(errors || {});
    return keys.length ? /* @__PURE__ */ React.createElement("div", { className: "aio-partial" }, /* @__PURE__ */ React.createElement(Icon.AlertTriangle, { size: 14 }), /* @__PURE__ */ React.createElement("span", null, "Partial live data: ", keys.map(aioLabel).join(", "), " unavailable. Healthy panels remain visible.")) : null;
  }
  function AioLoading() {
    return /* @__PURE__ */ React.createElement("div", { className: "grid-4 aio-skeleton", "aria-label": "Loading AI Operations data" }, [1, 2, 3, 4].map(function(i) {
      return /* @__PURE__ */ React.createElement("div", { className: "card stat aio-skeleton-card", key: i }, /* @__PURE__ */ React.createElement("div", { className: "lbl" }, "Loading"), /* @__PURE__ */ React.createElement("div", { className: "val" }, "\xA0"));
    }));
  }
  function AioError({ title, error }) {
    return /* @__PURE__ */ React.createElement("div", { className: "tile-error flex-row", style: { marginBottom: 10 } }, /* @__PURE__ */ React.createElement(Icon.AlertCircle, { size: 14 }), /* @__PURE__ */ React.createElement("strong", { style: { marginLeft: 6 } }, title), /* @__PURE__ */ React.createElement("span", { className: "muted txt-xs", style: { marginLeft: 8 } }, hbzErrorText(error)));
  }
  function AioConsole({ cluster, lastRefresh }) {
    var res = useAioLoad(function() {
      return aioSettled([["overview", function() {
        return aioGet("/overview");
      }], ["gateway", function() {
        return aioGet("/model-gateway/status");
      }], ["rag", function() {
        return aioGet("/rag/kb", { limit: 1 });
      }]]);
    }, [lastRefresh]);
    var data = res[0], loading = res[1], error = res[2];
    if (loading && !data) return /* @__PURE__ */ React.createElement(AioLoading, null);
    if (error) return /* @__PURE__ */ React.createElement(AioError, { title: "AI Ops error", error });
    var ov = data && data.overview || {};
    var gw = data && data.gateway || {};
    var rag = data && data.rag || {};
    var recs = ov.recommendations_summary || {};
    var severityRows = ["CRITICAL", "HIGH", "MEDIUM", "LOW"].map(function(key) {
      return { label: key.charAt(0) + key.slice(1).toLowerCase(), value: Number(recs[key] != null ? recs[key] : recs[key.toLowerCase()] || 0), tone: key === "CRITICAL" || key === "HIGH" ? "danger" : key === "MEDIUM" ? "warn" : "ok" };
    }).filter(function(row) {
      return row.value > 0;
    });
    var pendingCount = Number(recs.PENDING != null ? recs.PENDING : recs.pending != null ? recs.pending : recs.open || 0);
    var recommendationRows = ov.recent_recommendations || [];
    var categoryRows = aioCountRows(recommendationRows, "category");
    return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(AioPartial, { errors: data && data._errors }), /* @__PURE__ */ React.createElement("div", { className: "grid-4" }, /* @__PURE__ */ React.createElement(Stat, { label: "Provider", value: gw.provider || "disabled", sub: /* @__PURE__ */ React.createElement("span", { className: "pill " + (gw.configured ? "ok" : "warn") }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), gw.configured ? "configured" : "off") }), /* @__PURE__ */ React.createElement(Stat, { label: "Model", value: gw.model || "-", sub: gw.base_url || "default endpoint" }), /* @__PURE__ */ React.createElement(Stat, { label: "Open incidents", value: ov.open_incidents != null ? ov.open_incidents : "-", sub: "unresolved" }), /* @__PURE__ */ React.createElement(Stat, { label: "RAG documents", value: rag.count != null ? rag.count : "-", sub: rag.semantic_enabled ? "semantic on" : "keyword" })), /* @__PURE__ */ React.createElement("div", { className: "grid-2" }, /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Recommendation summary ", /* @__PURE__ */ React.createElement(SourceBadge, { source: ov.source })), /* @__PURE__ */ React.createElement("div", { className: "bd grid-2" }, /* @__PURE__ */ React.createElement(Stat, { label: "Total", value: recs.total != null ? recs.total : "-" }), /* @__PURE__ */ React.createElement(Stat, { label: "Pending", value: pendingCount }), severityRows.length ? /* @__PURE__ */ React.createElement(DonutChart, { rows: severityRows, center: recs.total || severityRows.reduce(function(sum, row) {
      return sum + row.value;
    }, 0), sub: "by severity", size: 140 }) : null, categoryRows.length ? /* @__PURE__ */ React.createElement(BarList, { rows: categoryRows, limit: 6, valueFormatter: function(v) {
      return fmtInt(v);
    } }) : null)), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Recent recommendations"), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflowX: "auto" } }, recommendationRows.length ? /* @__PURE__ */ React.createElement("table", { className: "table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, /* @__PURE__ */ React.createElement("th", null, "Severity"), /* @__PURE__ */ React.createElement("th", null, "Category"), /* @__PURE__ */ React.createElement("th", null, "Title"))), /* @__PURE__ */ React.createElement("tbody", null, recommendationRows.map(function(r, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: r.id || i }, /* @__PURE__ */ React.createElement("td", null, /* @__PURE__ */ React.createElement("span", { className: "pill " + (r.severity === "HIGH" || r.severity === "CRITICAL" ? "danger" : r.severity === "MEDIUM" ? "warn" : "ok") }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), r.severity || "-")), /* @__PURE__ */ React.createElement("td", null, r.category || "-"), /* @__PURE__ */ React.createElement("td", { className: "txt-xs" }, r.title || r.summary || "-"));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.Bot, title: "No recommendations yet", hint: "The AI agent has not produced recommendations for this window.", source: ov.source })))));
  }
  function AioNlSql({ cluster, lastRefresh }) {
    var qs = useState("");
    var q = qs[0], setQ = qs[1];
    var rs = useState(null);
    var result = rs[0], setResult = rs[1];
    var bs = useState(false);
    var busy = bs[0], setBusy = bs[1];
    var ds = useAioLoad(function() {
      return aioGet("/nlsql/databases", { cluster_id: cluster && cluster.id });
    }, [cluster && cluster.id, lastRefresh]);
    var databaseData = ds[0] || {}, dbs = databaseData.databases || [];
    var selectedState = useState(""), selectedDatabase = selectedState[0], setSelectedDatabase = selectedState[1];
    var limitState = useState(100), limit = limitState[0], setLimit = limitState[1];
    function ask() {
      if (!q.trim()) return;
      setBusy(true);
      aioPost("/nlsql", { question: q, limit, database: selectedDatabase || null, cluster_id: cluster && cluster.id }).then(function(d) {
        setResult(d);
        setBusy(false);
      }).catch(function(e) {
        setResult({ error: e.message || String(e) });
        setBusy(false);
      });
    }
    var rows = result && result.rows || [];
    var columns = result && result.columns || [];
    var numericIndex = columns.findIndex(function(_, index) {
      return rows.some(function(row) {
        return row && aioNum(row[index]) != null;
      });
    });
    var resultChartRows = numericIndex < 0 ? [] : rows.slice(0, 10).map(function(row, index) {
      return { label: String((row && row[0]) == null ? "Row " + (index + 1) : row[0]).slice(0, 48), value: aioNum(row && row[numericIndex], 0), tone: "info" };
    });
    var databaseRows = Object.keys(result && result.per_database_counts || {}).map(function(name) {
      return { label: name, value: aioNum(result.per_database_counts[name], 0), tone: "info" };
    });
    return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, /* @__PURE__ */ React.createElement(Icon.Bot, { size: 14 }), " Ask Your Database ", /* @__PURE__ */ React.createElement("span", { className: "muted txt-xs" }, "natural language \u2192 guarded read-only SQL")), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement("div", { className: "grid-2", style: { marginBottom: 12 } }, /* @__PURE__ */ React.createElement("div", { className: "field", style: { margin: 0 } }, /* @__PURE__ */ React.createElement("label", null, "Database scope"), /* @__PURE__ */ React.createElement("select", { value: selectedDatabase, onChange: function(e) {
      setSelectedDatabase(e.target.value);
    } }, /* @__PURE__ */ React.createElement("option", { value: "" }, "All Patroni application databases"), dbs.map(function(db) {
      return /* @__PURE__ */ React.createElement("option", { key: db, value: db }, db);
    }))), /* @__PURE__ */ React.createElement("div", { className: "field", style: { margin: 0 } }, /* @__PURE__ */ React.createElement("label", null, "Maximum rows per database"), /* @__PURE__ */ React.createElement("select", { value: limit, onChange: function(e) {
      setLimit(Number(e.target.value));
    } }, /* @__PURE__ */ React.createElement("option", { value: 50 }, "50"), /* @__PURE__ */ React.createElement("option", { value: 100 }, "100"), /* @__PURE__ */ React.createElement("option", { value: 250 }, "250"), /* @__PURE__ */ React.createElement("option", { value: 500 }, "500")))), /* @__PURE__ */ React.createElement("div", { className: "field", style: { margin: 0 } }, /* @__PURE__ */ React.createElement("label", null, "Question"), /* @__PURE__ */ React.createElement("textarea", { value: q, rows: 7, style: { width: "100%", resize: "vertical", minHeight: 150, fontFamily: "var(--mono, monospace)", lineHeight: 1.55 }, placeholder: "Ask about tables, indexes, row counts, database sizes, sessions, locks, or schema objects across the Patroni cluster\u2026", onChange: function(e) {
      setQ(e.target.value);
    }, onKeyDown: function(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") ask();
    } })), /* @__PURE__ */ React.createElement("div", { className: "flex-row", style: { gap: 10, justifyContent: "space-between", marginTop: 10, flexWrap: "wrap" } }, /* @__PURE__ */ React.createElement("div", { className: "muted txt-xs" }, "Ctrl/\u2318 + Enter to run \xB7 SELECT / WITH only \xB7 never queries the monitoring metadata database"), /* @__PURE__ */ React.createElement("button", { className: "btn primary", disabled: busy || !q.trim(), onClick: ask }, busy ? /* @__PURE__ */ React.createElement(Icon.Loader, { size: 12 }) : /* @__PURE__ */ React.createElement(Icon.Send, { size: 12 }), " Ask")))), result && result.error && !result.sql && /* @__PURE__ */ React.createElement(AioError, { title: "Ask failed", error: result.error }), result && (result.sql || result.executed != null) && /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Generated SQL ", result.provider ? /* @__PURE__ */ React.createElement("span", { className: "pill muted" }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), result.provider, result.model ? " / " + result.model : "") : null, " ", result.database_scope ? /* @__PURE__ */ React.createElement("span", { className: "pill ok" }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), result.database_scope) : null), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement("pre", { className: "mono txt-xs", style: { whiteSpace: "pre-wrap", margin: 0 } }, result.sql || "(no SQL produced)"), result.error && /* @__PURE__ */ React.createElement("div", { className: "muted txt-xs", style: { marginTop: 8 } }, /* @__PURE__ */ React.createElement(Icon.AlertTriangle, { size: 11 }), " ", result.error), result.executed && /* @__PURE__ */ React.createElement(SourceBadge, { source: result.source, detail: result.row_count + " rows" }))), result && result.executed && (resultChartRows.length || databaseRows.length) && /* @__PURE__ */ React.createElement("div", { className: "grid-2 aio-result-visuals" }, resultChartRows.length ? /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Visual result \xB7 ", columns[numericIndex]), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(BarList, { rows: resultChartRows, limit: 10, valueFormatter: function(v) {
      return fmtInt(v);
    } }))) : null, databaseRows.length ? /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Rows by database"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(DonutChart, { rows: databaseRows, center: rows.length, sub: "result rows", size: 150 }))) : null), result && result.executed && /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Result (", rows.length, " rows across ", Object.keys(result.per_database_counts || {}).length, " databases)"), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflow: "auto", maxHeight: "62vh", padding: 0 } }, rows.length ? /* @__PURE__ */ React.createElement("table", { className: "table", style: { minWidth: "100%", whiteSpace: "nowrap" } }, /* @__PURE__ */ React.createElement("thead", { style: { position: "sticky", top: 0, zIndex: 2 } }, /* @__PURE__ */ React.createElement("tr", null, columns.map(function(column) {
      return /* @__PURE__ */ React.createElement("th", { key: column, className: "mono txt-xs" }, column);
    }))), /* @__PURE__ */ React.createElement("tbody", null, rows.map(function(row, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: i }, (row || []).map(function(cell, j) {
        return /* @__PURE__ */ React.createElement("td", { key: j, className: "mono txt-xs", title: cell == null ? "" : String(cell) }, cell == null ? /* @__PURE__ */ React.createElement("span", { className: "muted" }, "NULL") : typeof cell === "object" ? JSON.stringify(cell) : String(cell));
      }));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.Database, title: "No rows", hint: "The query executed but returned no rows." }))));
  }
  function AioVector({ cluster, lastRefresh }) {
    var res = useAioLoad(function() {
      return aioSettled([["rag", function() {
        return aioGet("/rag/kb", { limit: 50 });
      }], ["gateway", function() {
        return aioGet("/model-gateway/status");
      }]]);
    }, [lastRefresh]);
    var data = res[0], loading = res[1], error = res[2];
    if (loading && !data) return /* @__PURE__ */ React.createElement(AioLoading, null);
    if (error) return /* @__PURE__ */ React.createElement(AioError, { title: "Vector monitor error", error });
    var rag = data && data.rag || {};
    var gw = data && data.gateway || {};
    var docs = rag.documents || [];
    var methodRows = aioCountRows(docs, "method");
    var sourceRows = aioCountRows(docs, "source_file").slice(0, 8);
    return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(AioPartial, { errors: data && data._errors }), /* @__PURE__ */ React.createElement("div", { className: "grid-4" }, /* @__PURE__ */ React.createElement(Stat, { label: "KB documents", value: rag.count != null ? rag.count : docs.length, sub: "ai_knowledge_base" }), /* @__PURE__ */ React.createElement(Stat, { label: "Semantic search", value: rag.semantic_enabled ? "enabled" : "keyword", sub: rag.semantic_enabled ? "pgvector embeddings" : "no embeddings" }), /* @__PURE__ */ React.createElement(Stat, { label: "Provider", value: gw.provider || "disabled", sub: gw.model || "-" }), /* @__PURE__ */ React.createElement(Stat, { label: "Embeddings", value: gw.embeddings_model || gw.model || "-", sub: "model" })), /* @__PURE__ */ React.createElement("div", { className: "grid-2" }, /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Retrieval methods"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(DonutChart, { rows: methodRows, center: docs.length, sub: "documents", size: 150 }))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Knowledge sources"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(BarList, { rows: sourceRows, limit: 8, valueFormatter: function(v) {
      return fmtInt(v);
    }, emptyText: "Source metadata has not been indexed yet." })))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Knowledge base documents ", /* @__PURE__ */ React.createElement(SourceBadge, { source: rag.source })), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflowX: "auto" } }, docs.length ? /* @__PURE__ */ React.createElement("table", { className: "table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, /* @__PURE__ */ React.createElement("th", null, "ID"), /* @__PURE__ */ React.createElement("th", null, "Title"), /* @__PURE__ */ React.createElement("th", null, "Category"), /* @__PURE__ */ React.createElement("th", { className: "num" }, "Score"))), /* @__PURE__ */ React.createElement("tbody", null, docs.map(function(dd, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: dd.id || dd.runbook_id || i }, /* @__PURE__ */ React.createElement("td", { className: "mono txt-xs" }, dd.runbook_id || dd.id || "-"), /* @__PURE__ */ React.createElement("td", { className: "txt-xs" }, dd.title || "-"), /* @__PURE__ */ React.createElement("td", null, dd.category || dd.source || "-"), /* @__PURE__ */ React.createElement("td", { className: "num" }, dd.score != null ? Number(dd.score).toFixed(3) : "-"));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.Layers, title: "Empty knowledge base", hint: "No RAG documents ingested yet.", source: rag.source }))));
  }
  function AioAgents({ cluster, lastRefresh }) {
    var res = useAioLoad(function() {
      return aioSettled([["overview", function() {
        return aioGet("/overview");
      }], ["agents", function() {
        return aioGet("/agents");
      }], ["audit", function() {
        return aioGet("/audit", { limit: 50 });
      }]]);
    }, [lastRefresh]);
    var data = res[0], loading = res[1], error = res[2];
    if (loading && !data) return /* @__PURE__ */ React.createElement(AioLoading, null);
    if (error) return /* @__PURE__ */ React.createElement(AioError, { title: "Governance error", error });
    var ov = data && data.overview || {};
    var agents = data && data.agents || {};
    var audit = data && data.audit || {};
    var runs = agents.runs || agents.agents || [];
    var auditRows = audit.audit || audit.items || audit.entries || [];
    var agentStatus = ov.agent || {};
    var runRows = Array.isArray(runs) ? runs : [];
    var runStatusRows = aioCountRows(runRows, "status");
    var triggerRows = aioCountRows(runRows, "trigger_type");
    var runTimeline = runRows.slice(0, 12).reverse().map(function(run) {
      return { key: run.id, title: run.status || "unknown", label: (run.agent_name || "agent") + " \xB7 " + (run.started_at || run.created_at || ""), sub: run.trigger_type || run.triggered_by, tone: aioTone(run.status) };
    });
    return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement(AioPartial, { errors: data && data._errors }), /* @__PURE__ */ React.createElement("div", { className: "grid-4" }, /* @__PURE__ */ React.createElement(Stat, { label: "Scheduler", value: agentStatus.scheduler_enabled ? "on" : "off", sub: agentStatus.running ? "running" : "idle" }), /* @__PURE__ */ React.createElement(Stat, { label: "Execution", value: agentStatus.execution_enabled ? "enabled" : "analyze-only", sub: "control gate" }), /* @__PURE__ */ React.createElement(Stat, { label: "Agent runs", value: Array.isArray(runs) ? runs.length : "-", sub: "recent" }), /* @__PURE__ */ React.createElement(Stat, { label: "Audit entries", value: Array.isArray(auditRows) ? auditRows.length : "-", sub: "governance trail" })), /* @__PURE__ */ React.createElement("div", { className: "grid-2" }, /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Run outcomes"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(DonutChart, { rows: runStatusRows, center: runRows.length, sub: "recent runs", size: 155 }))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Trigger distribution"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(BarList, { rows: triggerRows, limit: 8, valueFormatter: function(v) {
      return fmtInt(v);
    } })))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Agent run timeline"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(TimelineStrip, { rows: runTimeline, emptyText: "No agent runs have been recorded." }))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Governance audit trail ", /* @__PURE__ */ React.createElement(SourceBadge, { source: audit.source })), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflowX: "auto" } }, Array.isArray(auditRows) && auditRows.length ? /* @__PURE__ */ React.createElement("table", { className: "table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, /* @__PURE__ */ React.createElement("th", null, "When"), /* @__PURE__ */ React.createElement("th", null, "Action"), /* @__PURE__ */ React.createElement("th", null, "Status"), /* @__PURE__ */ React.createElement("th", null, "Actor"))), /* @__PURE__ */ React.createElement("tbody", null, auditRows.map(function(a, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: a.action_id || a.id || i }, /* @__PURE__ */ React.createElement("td", { className: "txt-xs" }, a.created_at || a.timestamp || "-"), /* @__PURE__ */ React.createElement("td", null, a.action_type || a.action || "-"), /* @__PURE__ */ React.createElement("td", null, /* @__PURE__ */ React.createElement(AioStatus, { value: a.execution_status || a.status })), /* @__PURE__ */ React.createElement("td", { className: "mono txt-xs" }, a.executed_by || a.requested_by || a.actor || "-"));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.Shield, title: "No governed actions yet", hint: "No AI agent actions have been recorded for audit.", source: audit.source }))));
  }
  function AioBranching({ cluster, lastRefresh }) {
    var res = useAioLoad(function() {
      return aioGet("/branching");
    }, [lastRefresh]);
    var data = res[0], loading = res[1], error = res[2];
    if (loading && !data) return /* @__PURE__ */ React.createElement(AioLoading, null);
    if (error) return /* @__PURE__ */ React.createElement(AioError, { title: "Branching error", error });
    var d = data || {};
    var sum = d.summary || {};
    var logical = d.logical_slots || [];
    var standbys = d.standbys || [];
    var pubs = d.publications || [];
    var subs = d.subscriptions || [];
    var activeCount = logical.filter(function(s) {
      return s.active;
    }).length;
    var inventoryRows = [
      { label: "Logical slots", value: aioNum(sum.logical_slots, logical.length), tone: "info" },
      { label: "Physical standbys", value: aioNum(sum.physical_standbys, standbys.length), tone: "ok" },
      { label: "Publications", value: aioNum(sum.publications, pubs.length), tone: "warn" },
      { label: "Subscriptions", value: aioNum(sum.subscriptions, subs.length), tone: "info" }
    ];
    var standbyRows = aioCountRows(standbys, "sync_state");
    var slotRows = [
      { label: "Active", value: activeCount, tone: "ok" },
      { label: "Inactive", value: logical.length - activeCount, tone: "warn" }
    ].filter(function(r) {
      return r.value > 0;
    });
    return /* @__PURE__ */ React.createElement("div", null, /* @__PURE__ */ React.createElement("div", { className: "grid-4" }, /* @__PURE__ */ React.createElement(Stat, { label: "Logical slots", value: sum.logical_slots != null ? sum.logical_slots : logical.length, sub: "logical branches" }), /* @__PURE__ */ React.createElement(Stat, { label: "Physical standbys", value: sum.physical_standbys != null ? sum.physical_standbys : standbys.length, sub: "streaming forks" }), /* @__PURE__ */ React.createElement(Stat, { label: "Publications", value: sum.publications != null ? sum.publications : pubs.length, sub: "logical sources" }), /* @__PURE__ */ React.createElement(Stat, { label: "Subscriptions", value: sum.subscriptions != null ? sum.subscriptions : subs.length, sub: "logical targets" })), /* @__PURE__ */ React.createElement("div", { className: "grid-2" }, /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Replication inventory"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(BarList, { rows: inventoryRows, limit: 8, valueFormatter: function(v) {
      return fmtInt(v);
    } }))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Standby synchronization"), /* @__PURE__ */ React.createElement("div", { className: "bd" }, /* @__PURE__ */ React.createElement(DonutChart, { rows: standbyRows, center: standbys.length, sub: "standbys", size: 155 })))), slotRows.length ? /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Logical slot activity ", /* @__PURE__ */ React.createElement(SourceBadge, { source: d.source })), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { display: "flex", justifyContent: "center" } }, /* @__PURE__ */ React.createElement(DonutChart, { rows: slotRows, center: logical.length, sub: "logical slots", size: 170, valueFormatter: function(v) {
      return fmtInt(v);
    } }))) : null, /* @__PURE__ */ React.createElement("div", { className: "grid-2" }, /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Logical replication slots ", /* @__PURE__ */ React.createElement(SourceBadge, { source: d.source })), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflowX: "auto" } }, logical.length ? /* @__PURE__ */ React.createElement("table", { className: "table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, /* @__PURE__ */ React.createElement("th", null, "Slot"), /* @__PURE__ */ React.createElement("th", null, "Database"), /* @__PURE__ */ React.createElement("th", null, "Active"), /* @__PURE__ */ React.createElement("th", null, "WAL status"), /* @__PURE__ */ React.createElement("th", { className: "num" }, "Retained WAL"))), /* @__PURE__ */ React.createElement("tbody", null, logical.map(function(s, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: s.slot_name || i }, /* @__PURE__ */ React.createElement("td", { className: "mono" }, s.slot_name), /* @__PURE__ */ React.createElement("td", null, s.database || "-"), /* @__PURE__ */ React.createElement("td", null, /* @__PURE__ */ React.createElement("span", { className: "pill " + (s.active ? "ok" : "warn") }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), s.active ? "active" : "inactive")), /* @__PURE__ */ React.createElement("td", null, s.wal_status || "-"), /* @__PURE__ */ React.createElement("td", { className: "num" }, s.retained_wal || "-"));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.GitBranch, title: "No logical slots", hint: "No logical replication slots (branches) exist.", source: d.source }))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Physical standbys (forks)"), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflowX: "auto" } }, standbys.length ? /* @__PURE__ */ React.createElement("table", { className: "table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, /* @__PURE__ */ React.createElement("th", null, "Application"), /* @__PURE__ */ React.createElement("th", null, "Client"), /* @__PURE__ */ React.createElement("th", null, "State"), /* @__PURE__ */ React.createElement("th", null, "Sync"))), /* @__PURE__ */ React.createElement("tbody", null, standbys.map(function(s, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: i }, /* @__PURE__ */ React.createElement("td", { className: "mono" }, s.application_name || "-"), /* @__PURE__ */ React.createElement("td", { className: "txt-xs" }, s.client_addr || "-"), /* @__PURE__ */ React.createElement("td", null, s.state), /* @__PURE__ */ React.createElement("td", null, /* @__PURE__ */ React.createElement("span", { className: "pill " + (s.sync_state === "sync" ? "ok" : "muted") }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), s.sync_state || "async")));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.GitBranch, title: "No standbys", hint: "No physical replicas are streaming." })))), /* @__PURE__ */ React.createElement("div", { className: "grid-2" }, /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Publications"), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflowX: "auto" } }, pubs.length ? /* @__PURE__ */ React.createElement("table", { className: "table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, /* @__PURE__ */ React.createElement("th", null, "Name"), /* @__PURE__ */ React.createElement("th", null, "All tables"), /* @__PURE__ */ React.createElement("th", { className: "num" }, "Tables"))), /* @__PURE__ */ React.createElement("tbody", null, pubs.map(function(p, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: p.name || i }, /* @__PURE__ */ React.createElement("td", { className: "mono" }, p.name), /* @__PURE__ */ React.createElement("td", null, p.all_tables ? "yes" : "no"), /* @__PURE__ */ React.createElement("td", { className: "num" }, p.table_count));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.Layers, title: "No publications", hint: "No logical publications defined." }))), /* @__PURE__ */ React.createElement("div", { className: "card" }, /* @__PURE__ */ React.createElement("div", { className: "hd" }, "Subscriptions"), /* @__PURE__ */ React.createElement("div", { className: "bd", style: { overflowX: "auto" } }, subs.length ? /* @__PURE__ */ React.createElement("table", { className: "table" }, /* @__PURE__ */ React.createElement("thead", null, /* @__PURE__ */ React.createElement("tr", null, /* @__PURE__ */ React.createElement("th", null, "Name"), /* @__PURE__ */ React.createElement("th", null, "Enabled"))), /* @__PURE__ */ React.createElement("tbody", null, subs.map(function(s, i) {
      return /* @__PURE__ */ React.createElement("tr", { key: s.name || i }, /* @__PURE__ */ React.createElement("td", { className: "mono" }, s.name), /* @__PURE__ */ React.createElement("td", null, /* @__PURE__ */ React.createElement("span", { className: "pill " + (s.enabled ? "ok" : "warn") }, /* @__PURE__ */ React.createElement("span", { className: "dot" }), s.enabled ? "enabled" : "disabled")));
    }))) : /* @__PURE__ */ React.createElement(EmptyState, { icon: Icon.Layers, title: "No subscriptions", hint: "No logical subscriptions on this node (expected on a source)." })))), d.errors && Object.keys(d.errors).length ? /* @__PURE__ */ React.createElement("div", { className: "muted txt-xs", style: { marginTop: 8 } }, "Partial data: ", Object.keys(d.errors).join(", "), " unavailable (permissions).") : null);
  }
  function AIOpsScreen(props) {
    var view = props && props.view;
    var body;
    if (view === "nlsql") body = /* @__PURE__ */ React.createElement(AioNlSql, { ...props });
    else if (view === "vector") body = /* @__PURE__ */ React.createElement(AioVector, { ...props });
    else if (view === "agents") body = /* @__PURE__ */ React.createElement(AioAgents, { ...props });
    else if (view === "branching") body = /* @__PURE__ */ React.createElement(AioBranching, { ...props });
    else body = /* @__PURE__ */ React.createElement(AioConsole, { ...props });
    return /* @__PURE__ */ React.createElement("div", { className: "page" }, body);
  }
  window.AIOpsScreen = AIOpsScreen;
})();
