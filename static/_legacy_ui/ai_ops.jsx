// AI Operations console + sub-views.
//
// window.AIOpsScreen dispatches on the `view` prop:
//   (none)      -> AI Ops console landing (provider/agent/RAG summary)
//   "nlsql"     -> Ask Your Database (NL -> guarded read-only SQL)
//   "vector"    -> Vector & RAG Monitor
//   "agents"    -> AI Agent Governance
//   "branching" -> Branching & Forks (live replication topology)
//
// Every panel reads live /api/v1/ai/* endpoints and degrades to an
// EmptyState with the backend `source`/`error` rather than rendering blank.

function aioUrl(path, params) {
  var url = new URL("/api/v1/ai" + path, window.location.origin);
  Object.entries(params || {}).forEach(function (e) {
    if (e[1] != null && e[1] !== "") url.searchParams.set(e[0], e[1]);
  });
  return url.toString();
}
function aioGet(path, params) {
  return fetch(aioUrl(path, params), { cache: "no-store" }).then(hbzJsonResponse);
}
function aioPost(path, body) {
  return fetch(aioUrl(path, {}), {
    method: "POST", cache: "no-store",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  }).then(hbzJsonResponse);
}

/* ------------------------------ shared hooks ------------------------------ */
function useAioLoad(fetcher, deps) {
  var s = useState({ data: null, loading: true, error: null });
  var state = s[0], setState = s[1];
  useEffect(function () {
    var alive = true;
    setState({ data: null, loading: true, error: null });
    fetcher()
      .then(function (d) { if (alive) setState({ data: d, loading: false, error: null }); })
      .catch(function (e) { if (alive) setState({ data: null, loading: false, error: e.message || String(e) }); });
    return function () { alive = false; };
  }, deps || []);
  return [state.data, state.loading, state.error];
}

function aioSettled(entries) { return Promise.allSettled(entries.map(function (entry) { return entry[1](); })).then(function (results) { var data = {}, errors = {}; results.forEach(function (result, index) { var key = entries[index][0]; if (result.status === "fulfilled") data[key] = result.value; else errors[key] = result.reason && (result.reason.message || String(result.reason)); }); if (!Object.keys(data).length) throw new Error(Object.values(errors).join(" · ") || "AI Operations data unavailable"); data._errors = errors; return data; }); }
function aioNum(value, fallback) { var parsed = typeof value === "string" ? Number(value.replace(/,/g, "")) : Number(value); return Number.isFinite(parsed) ? parsed : (fallback == null ? null : fallback); }
function aioTone(value) { var text = String(value || "unknown").toLowerCase(); if (/critical|high|failed|error|rejected|inactive/.test(text)) return "danger"; if (/medium|pending|warning|disabled|unknown/.test(text)) return "warn"; if (/low|complete|active|running|approved|enabled|healthy|streaming|sync/.test(text)) return "ok"; return "info"; }
function aioLabel(value) { return String(value == null ? "unknown" : value).replace(/[_-]+/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }
function aioCountRows(rows, field) { var counts = {}; (rows || []).forEach(function (row) { var key = aioLabel(row && row[field]); counts[key] = (counts[key] || 0) + 1; }); return Object.keys(counts).map(function (key) { return { label: key, value: counts[key], tone: aioTone(key) }; }); }
function AioStatus({ value }) { return <span className={"pill " + aioTone(value)}><span className="dot" />{aioLabel(value)}</span>; }
function AioPartial({ errors }) { var keys = Object.keys(errors || {}); return keys.length ? <div className="aio-partial"><Icon.AlertTriangle size={14}/><span>Partial live data: {keys.map(aioLabel).join(", ")} unavailable. Healthy panels remain visible.</span></div> : null; }
function AioLoading() { return <div className="grid-4 aio-skeleton" aria-label="Loading AI Operations data">{[1,2,3,4].map(function (i) { return <div className="card stat aio-skeleton-card" key={i}><div className="lbl">Loading</div><div className="val">&nbsp;</div></div>; })}</div>; }

function AioError({ title, error }) {
  return (
    <div className="tile-error flex-row" style={{ marginBottom: 10 }}>
      <Icon.AlertCircle size={14} /><strong style={{ marginLeft: 6 }}>{title}</strong>
      <span className="muted txt-xs" style={{ marginLeft: 8 }}>{hbzErrorText(error)}</span>
    </div>
  );
}

/* ------------------------------ Ops console ------------------------------- */
function AioConsole({ cluster, lastRefresh }) {
  var res = useAioLoad(function () {
    return aioSettled([["overview", function () { return aioGet("/overview"); }], ["gateway", function () { return aioGet("/model-gateway/status"); }], ["rag", function () { return aioGet("/rag/kb", { limit: 1 }); }]]);
  }, [lastRefresh]);
  var data = res[0], loading = res[1], error = res[2];
  if (loading && !data) return <AioLoading />;
  if (error) return <AioError title="AI Ops error" error={error} />;
  var ov = (data && data.overview) || {};
  var gw = (data && data.gateway) || {};
  var rag = (data && data.rag) || {};
  var recs = ov.recommendations_summary || {};
  var severityRows = ["CRITICAL", "HIGH", "MEDIUM", "LOW"].map(function (key) { return { label: key.charAt(0) + key.slice(1).toLowerCase(), value: Number(recs[key] != null ? recs[key] : recs[key.toLowerCase()] || 0), tone: key === "CRITICAL" || key === "HIGH" ? "danger" : key === "MEDIUM" ? "warn" : "ok" }; }).filter(function (row) { return row.value > 0; });
  var pendingCount = Number(recs.PENDING != null ? recs.PENDING : (recs.pending != null ? recs.pending : (recs.open || 0)));
  var recommendationRows = ov.recent_recommendations || [];
  var categoryRows = aioCountRows(recommendationRows, "category");
  return (
    <div>
      <AioPartial errors={data && data._errors} />
      <div className="grid-4">
        <Stat label="Provider" value={gw.provider || "disabled"} sub={<span className={"pill " + (gw.configured ? "ok" : "warn")}><span className="dot" />{gw.configured ? "configured" : "off"}</span>} />
        <Stat label="Model" value={gw.model || "-"} sub={gw.base_url || "default endpoint"} />
        <Stat label="Open incidents" value={ov.open_incidents != null ? ov.open_incidents : "-"} sub="unresolved" />
        <Stat label="RAG documents" value={rag.count != null ? rag.count : "-"} sub={rag.semantic_enabled ? "semantic on" : "keyword"} />
      </div>
      <div className="grid-2">
        <div className="card">
          <div className="hd">Recommendation summary <SourceBadge source={ov.source} /></div>
          <div className="bd grid-2">
            <Stat label="Total" value={recs.total != null ? recs.total : "-"} />
            <Stat label="Pending" value={pendingCount} />
            {severityRows.length ? <DonutChart rows={severityRows} center={recs.total || severityRows.reduce(function (sum, row) { return sum + row.value; }, 0)} sub="by severity" size={140} /> : null}
            {categoryRows.length ? <BarList rows={categoryRows} limit={6} valueFormatter={function (v) { return fmtInt(v); }} /> : null}
          </div>
        </div>
        <div className="card">
          <div className="hd">Recent recommendations</div>
          <div className="bd" style={{ overflowX: "auto" }}>
            {recommendationRows.length ? (
              <table className="table">
                <thead><tr><th>Severity</th><th>Category</th><th>Title</th></tr></thead>
                <tbody>
                  {recommendationRows.map(function (r, i) {
                    return <tr key={r.id || i}><td><span className={"pill " + ((r.severity === "HIGH" || r.severity === "CRITICAL") ? "danger" : r.severity === "MEDIUM" ? "warn" : "ok")}><span className="dot" />{r.severity || "-"}</span></td><td>{r.category || "-"}</td><td className="txt-xs">{r.title || r.summary || "-"}</td></tr>;
                  })}
                </tbody>
              </table>
            ) : <EmptyState icon={Icon.Bot} title="No recommendations yet" hint="The AI agent has not produced recommendations for this window." source={ov.source} />}
          </div>
        </div>
      </div>
    </div>
  );
}

/* --------------------------- Ask Your Database ---------------------------- */
function AioNlSql({ cluster, lastRefresh }) {
  var qs = useState("");
  var q = qs[0], setQ = qs[1];
  var rs = useState(null);
  var result = rs[0], setResult = rs[1];
  var bs = useState(false);
  var busy = bs[0], setBusy = bs[1];
  var ds = useAioLoad(function () { return aioGet("/nlsql/databases", { cluster_id: cluster && cluster.id }); }, [cluster && cluster.id, lastRefresh]);
  var databaseData = ds[0] || {}, dbs = databaseData.databases || [];
  var selectedState = useState(""), selectedDatabase = selectedState[0], setSelectedDatabase = selectedState[1];
  var limitState = useState(100), limit = limitState[0], setLimit = limitState[1];

  function ask() {
    if (!q.trim()) return;
    setBusy(true);
    aioPost("/nlsql", { question: q, limit: limit, database: selectedDatabase || null, cluster_id: cluster && cluster.id })
      .then(function (d) { setResult(d); setBusy(false); })
      .catch(function (e) { setResult({ error: e.message || String(e) }); setBusy(false); });
  }

  var rows = (result && result.rows) || [];
  var columns = (result && result.columns) || [];
  var numericIndex = columns.findIndex(function (_, index) { return rows.some(function (row) { return row && aioNum(row[index]) != null; }); });
  var resultChartRows = numericIndex < 0 ? [] : rows.slice(0, 10).map(function (row, index) { return { label: String((row && row[0]) == null ? "Row " + (index + 1) : row[0]).slice(0, 48), value: aioNum(row && row[numericIndex], 0), tone: "info" }; });
  var databaseRows = Object.keys((result && result.per_database_counts) || {}).map(function (name) { return { label: name, value: aioNum(result.per_database_counts[name], 0), tone: "info" }; });
  return (
    <div>
      <div className="card">
        <div className="hd"><Icon.Bot size={14} /> Ask Your Database <span className="muted txt-xs">natural language → guarded read-only SQL</span></div>
        <div className="bd">
          <div className="grid-2" style={{ marginBottom: 12 }}>
            <div className="field" style={{ margin: 0 }}><label>Database scope</label><select value={selectedDatabase} onChange={function (e) { setSelectedDatabase(e.target.value); }}><option value="">All Patroni application databases</option>{dbs.map(function (db) { return <option key={db} value={db}>{db}</option>; })}</select></div>
            <div className="field" style={{ margin: 0 }}><label>Maximum rows per database</label><select value={limit} onChange={function (e) { setLimit(Number(e.target.value)); }}><option value={50}>50</option><option value={100}>100</option><option value={250}>250</option><option value={500}>500</option></select></div>
          </div>
          <div className="field" style={{ margin: 0 }}><label>Question</label><textarea value={q} rows={7} style={{ width: "100%", resize: "vertical", minHeight: 150, fontFamily: "var(--mono, monospace)", lineHeight: 1.55 }} placeholder="Ask about tables, indexes, row counts, database sizes, sessions, locks, or schema objects across the Patroni cluster…" onChange={function (e) { setQ(e.target.value); }} onKeyDown={function (e) { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") ask(); }} /></div>
          <div className="flex-row" style={{ gap: 10, justifyContent: "space-between", marginTop: 10, flexWrap: "wrap" }}>
            <div className="muted txt-xs">Ctrl/⌘ + Enter to run · SELECT / WITH only · never queries the monitoring metadata database</div>
            <button className="btn primary" disabled={busy || !q.trim()} onClick={ask}>
              {busy ? <Icon.Loader size={12} /> : <Icon.Send size={12} />} Ask
            </button>
          </div>
        </div>
      </div>

      {result && result.error && !result.sql && <AioError title="Ask failed" error={result.error} />}

      {result && (result.sql || result.executed != null) && (
        <div className="card">
          <div className="hd">Generated SQL {result.provider ? <span className="pill muted"><span className="dot" />{result.provider}{result.model ? " / " + result.model : ""}</span> : null} {result.database_scope ? <span className="pill ok"><span className="dot" />{result.database_scope}</span> : null}</div>
          <div className="bd">
            <pre className="mono txt-xs" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{result.sql || "(no SQL produced)"}</pre>
            {result.error && <div className="muted txt-xs" style={{ marginTop: 8 }}><Icon.AlertTriangle size={11} /> {result.error}</div>}
            {result.executed && <SourceBadge source={result.source} detail={result.row_count + " rows"} />}
          </div>
        </div>
      )}

      {result && result.executed && (resultChartRows.length || databaseRows.length) && (
        <div className="grid-2 aio-result-visuals">
          {resultChartRows.length ? <div className="card"><div className="hd">Visual result · {columns[numericIndex]}</div><div className="bd"><BarList rows={resultChartRows} limit={10} valueFormatter={function (v) { return fmtInt(v); }}/></div></div> : null}
          {databaseRows.length ? <div className="card"><div className="hd">Rows by database</div><div className="bd"><DonutChart rows={databaseRows} center={rows.length} sub="result rows" size={150}/></div></div> : null}
        </div>
      )}

      {result && result.executed && (
        <div className="card">
          <div className="hd">Result ({rows.length} rows across {Object.keys(result.per_database_counts || {}).length} databases)</div>
          <div className="bd" style={{ overflow: "auto", maxHeight: "62vh", padding: 0 }}>
            {rows.length ? (
              <table className="table" style={{ minWidth: "100%", whiteSpace: "nowrap" }}>
                <thead style={{ position: "sticky", top: 0, zIndex: 2 }}><tr>{columns.map(function (column) { return <th key={column} className="mono txt-xs">{column}</th>; })}</tr></thead>
                <tbody>
                  {rows.map(function (row, i) {
                    return <tr key={i}>{(row || []).map(function (cell, j) { return <td key={j} className="mono txt-xs" title={cell == null ? "" : String(cell)}>{cell == null ? <span className="muted">NULL</span> : (typeof cell === "object" ? JSON.stringify(cell) : String(cell))}</td>; })}</tr>;
                  })}
                </tbody>
              </table>
            ) : <EmptyState icon={Icon.Database} title="No rows" hint="The query executed but returned no rows." />}
          </div>
        </div>
      )}
    </div>
  );
}

/* -------------------------- Vector & RAG Monitor -------------------------- */
function AioVector({ cluster, lastRefresh }) {
  var res = useAioLoad(function () {
    return aioSettled([["rag", function () { return aioGet("/rag/kb", { limit: 50 }); }], ["gateway", function () { return aioGet("/model-gateway/status"); }]]);
  }, [lastRefresh]);
  var data = res[0], loading = res[1], error = res[2];
  if (loading && !data) return <AioLoading />;
  if (error) return <AioError title="Vector monitor error" error={error} />;
  var rag = (data && data.rag) || {};
  var gw = (data && data.gateway) || {};
  var docs = rag.documents || [];
  var methodRows = aioCountRows(docs, "method");
  var sourceRows = aioCountRows(docs, "source_file").slice(0, 8);
  return (
    <div>
      <AioPartial errors={data && data._errors} />
      <div className="grid-4">
        <Stat label="KB documents" value={rag.count != null ? rag.count : docs.length} sub="ai_knowledge_base" />
        <Stat label="Semantic search" value={rag.semantic_enabled ? "enabled" : "keyword"} sub={rag.semantic_enabled ? "pgvector embeddings" : "no embeddings"} />
        <Stat label="Provider" value={gw.provider || "disabled"} sub={gw.model || "-"} />
        <Stat label="Embeddings" value={gw.embeddings_model || gw.model || "-"} sub="model" />
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Retrieval methods</div><div className="bd"><DonutChart rows={methodRows} center={docs.length} sub="documents" size={150}/></div></div>
        <div className="card"><div className="hd">Knowledge sources</div><div className="bd"><BarList rows={sourceRows} limit={8} valueFormatter={function (v) { return fmtInt(v); }} emptyText="Source metadata has not been indexed yet."/></div></div>
      </div>
      <div className="card">
        <div className="hd">Knowledge base documents <SourceBadge source={rag.source} /></div>
        <div className="bd" style={{ overflowX: "auto" }}>
          {docs.length ? (
            <table className="table">
              <thead><tr><th>ID</th><th>Title</th><th>Category</th><th className="num">Score</th></tr></thead>
              <tbody>
                {docs.map(function (dd, i) {
                  return <tr key={dd.id || dd.runbook_id || i}><td className="mono txt-xs">{dd.runbook_id || dd.id || "-"}</td><td className="txt-xs">{dd.title || "-"}</td><td>{dd.category || dd.source || "-"}</td><td className="num">{dd.score != null ? Number(dd.score).toFixed(3) : "-"}</td></tr>;
                })}
              </tbody>
            </table>
          ) : <EmptyState icon={Icon.Layers} title="Empty knowledge base" hint="No RAG documents ingested yet." source={rag.source} />}
        </div>
      </div>
    </div>
  );
}

/* -------------------------- AI Agent Governance --------------------------- */
function AioAgents({ cluster, lastRefresh }) {
  var res = useAioLoad(function () {
    return aioSettled([["overview", function () { return aioGet("/overview"); }], ["agents", function () { return aioGet("/agents"); }], ["audit", function () { return aioGet("/audit", { limit: 50 }); }]]);
  }, [lastRefresh]);
  var data = res[0], loading = res[1], error = res[2];
  if (loading && !data) return <AioLoading />;
  if (error) return <AioError title="Governance error" error={error} />;
  var ov = (data && data.overview) || {};
  var agents = (data && data.agents) || {};
  var audit = (data && data.audit) || {};
  var runs = agents.runs || agents.agents || [];
  var auditRows = audit.audit || audit.items || audit.entries || [];
  var agentStatus = ov.agent || {};
  var runRows = Array.isArray(runs) ? runs : [];
  var runStatusRows = aioCountRows(runRows, "status");
  var triggerRows = aioCountRows(runRows, "trigger_type");
  var runTimeline = runRows.slice(0, 12).reverse().map(function (run) { return { key: run.id, title: run.status || "unknown", label: (run.agent_name || "agent") + " · " + (run.started_at || run.created_at || ""), sub: run.trigger_type || run.triggered_by, tone: aioTone(run.status) }; });
  return (
    <div>
      <AioPartial errors={data && data._errors} />
      <div className="grid-4">
        <Stat label="Scheduler" value={agentStatus.scheduler_enabled ? "on" : "off"} sub={agentStatus.running ? "running" : "idle"} />
        <Stat label="Execution" value={agentStatus.execution_enabled ? "enabled" : "analyze-only"} sub="control gate" />
        <Stat label="Agent runs" value={Array.isArray(runs) ? runs.length : "-"} sub="recent" />
        <Stat label="Audit entries" value={Array.isArray(auditRows) ? auditRows.length : "-"} sub="governance trail" />
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Run outcomes</div><div className="bd"><DonutChart rows={runStatusRows} center={runRows.length} sub="recent runs" size={155}/></div></div>
        <div className="card"><div className="hd">Trigger distribution</div><div className="bd"><BarList rows={triggerRows} limit={8} valueFormatter={function (v) { return fmtInt(v); }}/></div></div>
      </div>
      <div className="card"><div className="hd">Agent run timeline</div><div className="bd"><TimelineStrip rows={runTimeline} emptyText="No agent runs have been recorded."/></div></div>
      <div className="card">
        <div className="hd">Governance audit trail <SourceBadge source={audit.source} /></div>
        <div className="bd" style={{ overflowX: "auto" }}>
          {Array.isArray(auditRows) && auditRows.length ? (
            <table className="table">
              <thead><tr><th>When</th><th>Action</th><th>Status</th><th>Actor</th></tr></thead>
              <tbody>
                {auditRows.map(function (a, i) {
                  return <tr key={a.action_id || a.id || i}><td className="txt-xs">{a.created_at || a.timestamp || "-"}</td><td>{a.action_type || a.action || "-"}</td><td><AioStatus value={a.execution_status || a.status} /></td><td className="mono txt-xs">{a.executed_by || a.requested_by || a.actor || "-"}</td></tr>;
                })}
              </tbody>
            </table>
          ) : <EmptyState icon={Icon.Shield} title="No governed actions yet" hint="No AI agent actions have been recorded for audit." source={audit.source} />}
        </div>
      </div>
    </div>
  );
}

/* --------------------------- Branching & Forks ---------------------------- */
function AioBranching({ cluster, lastRefresh }) {
  var res = useAioLoad(function () { return aioGet("/branching"); }, [lastRefresh]);
  var data = res[0], loading = res[1], error = res[2];
  if (loading && !data) return <AioLoading />;
  if (error) return <AioError title="Branching error" error={error} />;
  var d = data || {};
  var sum = d.summary || {};
  var logical = d.logical_slots || [];
  var standbys = d.standbys || [];
  var pubs = d.publications || [];
  var subs = d.subscriptions || [];
  var activeCount = logical.filter(function (s) { return s.active; }).length;
  var inventoryRows = [
    { label: "Logical slots", value: aioNum(sum.logical_slots, logical.length), tone: "info" },
    { label: "Physical standbys", value: aioNum(sum.physical_standbys, standbys.length), tone: "ok" },
    { label: "Publications", value: aioNum(sum.publications, pubs.length), tone: "warn" },
    { label: "Subscriptions", value: aioNum(sum.subscriptions, subs.length), tone: "info" },
  ];
  var standbyRows = aioCountRows(standbys, "sync_state");
  var slotRows = [
    { label: "Active", value: activeCount, tone: "ok" },
    { label: "Inactive", value: logical.length - activeCount, tone: "warn" },
  ].filter(function (r) { return r.value > 0; });
  return (
    <div>
      <div className="grid-4">
        <Stat label="Logical slots" value={sum.logical_slots != null ? sum.logical_slots : logical.length} sub="logical branches" />
        <Stat label="Physical standbys" value={sum.physical_standbys != null ? sum.physical_standbys : standbys.length} sub="streaming forks" />
        <Stat label="Publications" value={sum.publications != null ? sum.publications : pubs.length} sub="logical sources" />
        <Stat label="Subscriptions" value={sum.subscriptions != null ? sum.subscriptions : subs.length} sub="logical targets" />
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Replication inventory</div><div className="bd"><BarList rows={inventoryRows} limit={8} valueFormatter={function (v) { return fmtInt(v); }}/></div></div>
        <div className="card"><div className="hd">Standby synchronization</div><div className="bd"><DonutChart rows={standbyRows} center={standbys.length} sub="standbys" size={155}/></div></div>
      </div>
      {slotRows.length ? (
        <div className="card">
          <div className="hd">Logical slot activity <SourceBadge source={d.source} /></div>
          <div className="bd" style={{ display: "flex", justifyContent: "center" }}>
            <DonutChart rows={slotRows} center={logical.length} sub="logical slots" size={170} valueFormatter={function (v) { return fmtInt(v); }} />
          </div>
        </div>
      ) : null}
      <div className="grid-2">
        <div className="card">
          <div className="hd">Logical replication slots <SourceBadge source={d.source} /></div>
          <div className="bd" style={{ overflowX: "auto" }}>
            {logical.length ? (
              <table className="table">
                <thead><tr><th>Slot</th><th>Database</th><th>Active</th><th>WAL status</th><th className="num">Retained WAL</th></tr></thead>
                <tbody>
                  {logical.map(function (s, i) {
                    return <tr key={s.slot_name || i}><td className="mono">{s.slot_name}</td><td>{s.database || "-"}</td><td><span className={"pill " + (s.active ? "ok" : "warn")}><span className="dot" />{s.active ? "active" : "inactive"}</span></td><td>{s.wal_status || "-"}</td><td className="num">{s.retained_wal || "-"}</td></tr>;
                  })}
                </tbody>
              </table>
            ) : <EmptyState icon={Icon.GitBranch} title="No logical slots" hint="No logical replication slots (branches) exist." source={d.source} />}
          </div>
        </div>
        <div className="card">
          <div className="hd">Physical standbys (forks)</div>
          <div className="bd" style={{ overflowX: "auto" }}>
            {standbys.length ? (
              <table className="table">
                <thead><tr><th>Application</th><th>Client</th><th>State</th><th>Sync</th></tr></thead>
                <tbody>
                  {standbys.map(function (s, i) {
                    return <tr key={i}><td className="mono">{s.application_name || "-"}</td><td className="txt-xs">{s.client_addr || "-"}</td><td>{s.state}</td><td><span className={"pill " + (s.sync_state === "sync" ? "ok" : "muted")}><span className="dot" />{s.sync_state || "async"}</span></td></tr>;
                  })}
                </tbody>
              </table>
            ) : <EmptyState icon={Icon.GitBranch} title="No standbys" hint="No physical replicas are streaming." />}
          </div>
        </div>
      </div>
      <div className="grid-2">
        <div className="card">
          <div className="hd">Publications</div>
          <div className="bd" style={{ overflowX: "auto" }}>
            {pubs.length ? (
              <table className="table">
                <thead><tr><th>Name</th><th>All tables</th><th className="num">Tables</th></tr></thead>
                <tbody>{pubs.map(function (p, i) { return <tr key={p.name || i}><td className="mono">{p.name}</td><td>{p.all_tables ? "yes" : "no"}</td><td className="num">{p.table_count}</td></tr>; })}</tbody>
              </table>
            ) : <EmptyState icon={Icon.Layers} title="No publications" hint="No logical publications defined." />}
          </div>
        </div>
        <div className="card">
          <div className="hd">Subscriptions</div>
          <div className="bd" style={{ overflowX: "auto" }}>
            {subs.length ? (
              <table className="table">
                <thead><tr><th>Name</th><th>Enabled</th></tr></thead>
                <tbody>{subs.map(function (s, i) { return <tr key={s.name || i}><td className="mono">{s.name}</td><td><span className={"pill " + (s.enabled ? "ok" : "warn")}><span className="dot" />{s.enabled ? "enabled" : "disabled"}</span></td></tr>; })}</tbody>
              </table>
            ) : <EmptyState icon={Icon.Layers} title="No subscriptions" hint="No logical subscriptions on this node (expected on a source)." />}
          </div>
        </div>
      </div>
      {d.errors && Object.keys(d.errors).length ? <div className="muted txt-xs" style={{ marginTop: 8 }}>Partial data: {Object.keys(d.errors).join(", ")} unavailable (permissions).</div> : null}
    </div>
  );
}

/* ------------------------------- dispatcher ------------------------------- */
function AIOpsScreen(props) {
  var view = props && props.view;
  var body;
  if (view === "nlsql") body = <AioNlSql {...props} />;
  else if (view === "vector") body = <AioVector {...props} />;
  else if (view === "agents") body = <AioAgents {...props} />;
  else if (view === "branching") body = <AioBranching {...props} />;
  else body = <AioConsole {...props} />;
  return <div className="page">{body}</div>;
}

window.AIOpsScreen = AIOpsScreen;
