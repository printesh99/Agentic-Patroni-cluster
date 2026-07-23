function aiAgentUrl(path, params) {
  var url = new URL("/api/ai-agent" + path, window.location.origin);
  Object.entries(params || {}).forEach(function(entry) {
    var key = entry[0], value = entry[1];
    if (value != null && value !== "" && value !== "ALL") url.searchParams.set(key, value);
  });
  return url.toString();
}

function aiAgentJson(path, options, params) {
  return fetch(aiAgentUrl(path, params), Object.assign({ cache: "no-store" }, options || {})).then(hbzJsonResponse);
}

function aiAgentApiJson(path, options) {
  return fetch(path, Object.assign({ cache: "no-store" }, options || {})).then(hbzJsonResponse);
}

function aiAgentDate(value) {
  if (!value) return "-";
  try { return new Date(value).toLocaleString("en-GB", { hour12: false }); } catch (e) { return String(value); }
}

function aiAgentTone(value) {
  var v = String(value || "").toUpperCase();
  if (v === "CRITICAL" || v === "HIGH" || v === "FAILED" || v === "BLOCKED" || v === "REJECTED") return "danger";
  if (v === "MEDIUM" || v === "PENDING" || v === "PREVIEW_ONLY") return "warn";
  if (v === "LOW" || v === "INFO" || v === "RUNNING") return "info";
  if (v === "COMPLETED" || v === "APPROVED" || v === "EXECUTED") return "ok";
  return "muted";
}

function aiAgentActor(currentUser) {
  return (currentUser && (currentUser.email || currentUser.username || currentUser.name)) || "dba";
}

function AiAgentProviderCard({ status }) {
  var provider = (status && status.provider) || {};
  return (
    <div className="card">
      <div className="hd">AI Provider <span className={"pill " + (provider.configured && provider.api_key_present ? "ok" : "warn")}><span className="dot"/>{provider.provider || "disabled"}</span></div>
      <div className="bd grid-2">
        <Stat label="Configured" value={provider.configured ? "Yes" : "No"} sub={provider.api_key_present ? "API key present" : "API key missing"}/>
        <Stat label="Model" value={provider.model || "-"} sub={provider.base_url || "default endpoint"}/>
      </div>
    </div>
  );
}

function AiAgentSchedulerCard({ scheduler, status, onAction, busy }) {
  var agent = (scheduler && scheduler.ai_agent) || {};
  var jobs = (scheduler && scheduler.job_details) || [];
  var jobNames = jobs.map(function(job) { return job.id; }).join(", ") || "none";
  return (
    <div className="card">
      <div className="hd">
        Scheduler Operations
        <span className={"pill " + (scheduler && scheduler.enabled ? "ok" : "muted")}><span className="dot"/>{scheduler && scheduler.enabled ? "running" : "stopped"}</span>
      </div>
      <div className="bd">
        <div className="grid-4">
          <Stat label="Health collector" value={scheduler && scheduler.env_enabled ? "Enabled" : "Off"} sub={(scheduler && scheduler.interval_seconds ? Math.round(scheduler.interval_seconds / 60) : "-") + " min interval"}/>
          <Stat label="Agent schedule" value={agent.env_enabled ? "Enabled" : "Off"} sub={(agent.interval_seconds ? Math.round(agent.interval_seconds / 60) : (status && status.interval_minutes) || "-") + " min interval"}/>
          <Stat label="Last health run" value={scheduler && scheduler.has_last_run ? "Done" : "-"} sub={scheduler ? aiAgentDate(scheduler.last_run_at) : "-"}/>
          <Stat label="Last agent run" value={agent.has_last_run ? "Done" : "-"} sub={aiAgentDate(agent.last_run_at)}/>
        </div>
        <div className="flex-row mt-3" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn sm primary" disabled={busy} onClick={function() { onAction("start"); }}><Icon.Play size={12}/> Start jobs</button>
          <button className="btn sm ghost" disabled={busy} onClick={function() { onAction("agentTick"); }}><Icon.Zap size={12}/> Agent tick</button>
          <button className="btn sm ghost" disabled={busy} onClick={function() { onAction("healthTick"); }}><Icon.Activity size={12}/> Health tick</button>
          <button className="btn sm ghost" disabled={busy} onClick={function() { onAction("stop"); }}><Icon.StopCircle size={12}/> Stop jobs</button>
          <span className="pill muted">jobs: {jobNames}</span>
        </div>
        <div style={{ overflowX: "auto", marginTop: 12 }}>
          <table className="tbl">
            <thead><tr><th>Job</th><th>Trigger</th><th>Next run</th></tr></thead>
            <tbody>
              {jobs.map(function(job) {
                return (
                  <tr key={job.id}>
                    <td className="mono txt-xs">{job.id}</td>
                    <td className="txt-xs">{job.trigger || "-"}</td>
                    <td className="txt-xs">{aiAgentDate(job.next_run_at)}</td>
                  </tr>
                );
              })}
              {!jobs.length && <tr><td colSpan="3" className="muted" style={{ textAlign: "center", padding: 14 }}>No scheduler jobs are active.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function AiAgentScreen({ cluster, lastRefresh, currentUser }) {
  var statusState = useState(null);
  var schedulerState = useState(null);
  var recState = useState({ recommendations: [], summary: {}, count: 0 });
  var runsState = useState({ runs: [], count: 0 });
  var loadingState = useState(true);
  var busyState = useState(false);
  var errorState = useState(null);
  var selectedState = useState(null);
  var detailState = useState(null);
  var filtersState = useState({ severity: "ALL", category: "ALL", approval_status: "ALL", cluster_name: "", database_name: "" });
  var runFormState = useState({ category: "ALL", database_name: "", lookback_minutes: 30 });
  var rejectReasonState = useState("");

  var status = statusState[0], setStatus = statusState[1];
  var scheduler = schedulerState[0], setScheduler = schedulerState[1];
  var recData = recState[0], setRecData = recState[1];
  var runsData = runsState[0], setRunsData = runsState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var detail = detailState[0], setDetail = detailState[1];
  var filters = filtersState[0], setFilters = filtersState[1];
  var runForm = runFormState[0], setRunForm = runFormState[1];
  var rejectReason = rejectReasonState[0], setRejectReason = rejectReasonState[1];
  var actor = aiAgentActor(currentUser);

  function setFilter(name, value) {
    setFilters(function(prev) { var next = Object.assign({}, prev); next[name] = value; return next; });
  }

  function setRunValue(name, value) {
    setRunForm(function(prev) { var next = Object.assign({}, prev); next[name] = value; return next; });
  }

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([
      aiAgentJson("/status"),
      aiAgentApiJson("/api/v1/scheduler/status"),
      aiAgentJson("/recommendations", null, Object.assign({}, filters, { limit: 200 })),
      aiAgentJson("/runs", null, { limit: 20 }),
    ])
      .then(function(results) {
        setStatus(results[0]);
        setScheduler(results[1]);
        setRecData(results[2]);
        setRunsData(results[3]);
        setLoading(false);
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setLoading(false);
      });
  }

  useEffect(function() { load(); }, [lastRefresh, filters]);

  function runNow() {
    setBusy(true);
    setError(null);
    aiAgentJson("/run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        cluster_name: cluster && cluster.name,
        database_name: runForm.database_name || null,
        category: runForm.category,
        lookback_minutes: Number(runForm.lookback_minutes || 30),
        triggered_by: actor,
      }),
    })
      .then(function() { setBusy(false); load(); })
      .catch(function(err) { setBusy(false); setError(err.message || String(err)); });
  }

  function schedulerAction(kind) {
    setBusy(true);
    setError(null);
    var calls;
    if (kind === "start") {
      calls = [
        aiAgentApiJson("/api/v1/scheduler/start?run_now=true", { method: "POST" }),
        aiAgentApiJson("/api/v1/scheduler/agent/start?run_now=true", { method: "POST" }),
      ];
    } else if (kind === "agentTick") {
      calls = [aiAgentApiJson("/api/v1/scheduler/agent/tick", { method: "POST" })];
    } else if (kind === "healthTick") {
      calls = [aiAgentApiJson("/api/v1/scheduler/tick", { method: "POST" })];
    } else {
      calls = [aiAgentApiJson("/api/v1/scheduler/stop", { method: "POST" })];
    }
    Promise.all(calls)
      .then(function() { setBusy(false); load(); })
      .catch(function(err) { setBusy(false); setError(err.message || String(err)); });
  }

  function openDetail(row) {
    setSelected(row);
    setDetail(null);
    setRejectReason("");
    aiAgentJson("/recommendations/" + row.recommendation_id)
      .then(function(payload) { setDetail(payload.recommendation); })
      .catch(function(err) { setError(err.message || String(err)); });
  }

  function recAction(action, payload) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    aiAgentJson("/recommendations/" + selected.recommendation_id + "/" + action, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(Object.assign({ actor: actor }, payload || {})),
    })
      .then(function(result) {
        setBusy(false);
        var row = result.recommendation || selected;
        setSelected(row);
        openDetail(row);
        load();
      })
      .catch(function(err) { setBusy(false); setError(err.message || String(err)); });
  }

  var rows = recData.recommendations || [];
  var runs = runsData.runs || [];
  var summary = recData.summary || {};
  var pending = rows.filter(function(r) { return r.approval_status === "PENDING"; }).length;
  var approved = rows.filter(function(r) { return r.approval_status === "APPROVED"; }).length;
  var high = rows.filter(function(r) { return r.severity === "CRITICAL" || r.severity === "HIGH"; }).length;
  var latestRun = runs[0];

  return (
    <div className="page">
      {error && (
        <div className="tile-error flex-row" style={{ marginBottom: 10 }}>
          <Icon.AlertCircle size={14}/><strong style={{ marginLeft: 6 }}>AI agent error</strong>
          <span className="muted txt-xs" style={{ marginLeft: 8 }}>{hbzErrorText(error)}</span>
        </div>
      )}

      <div className="card">
        <div className="bd flex-row" style={{ gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div className="field" style={{ margin: 0, minWidth: 160 }}>
            <label>Run category</label>
            <select value={runForm.category} onChange={function(e) { setRunValue("category", e.target.value); }}>
              {["ALL", "PERFORMANCE", "INDEX", "ERROR", "BACKUP", "REPLICATION"].map(function(v) { return <option key={v} value={v}>{v}</option>; })}
            </select>
          </div>
          <div className="field" style={{ margin: 0, minWidth: 190 }}>
            <label>Database</label>
            <input value={runForm.database_name} onChange={function(e) { setRunValue("database_name", e.target.value); }} placeholder="optional"/>
          </div>
          <div className="field" style={{ margin: 0, width: 130 }}>
            <label>Lookback min</label>
            <input type="number" min="5" max="1440" value={runForm.lookback_minutes} onChange={function(e) { setRunValue("lookback_minutes", e.target.value); }}/>
          </div>
          <button className="btn sm primary" disabled={busy || (status && status.running)} onClick={runNow}>
            {busy ? <Icon.Loader size={12}/> : <Icon.Play size={12}/>} Run AI Agent Now
          </button>
          <span className={"pill " + (status && status.running ? "info" : "muted")}><span className="dot"/>{status && status.running ? "RUNNING" : "IDLE"}</span>
          <span className={"pill " + (status && status.scheduler_enabled ? "ok" : "muted")}><Icon.Clock size={10}/> scheduler {status && status.scheduler_enabled ? "on" : "off"}</span>
          <span className={"pill " + (status && status.provider && status.provider.api_key_present ? "ok" : "warn")}><Icon.Lock size={10}/> key {status && status.provider && status.provider.api_key_present ? "present" : "missing"}</span>
          <span className={"pill " + (status && status.email_enabled ? "ok" : "muted")}><Icon.Bell size={10}/> email {status && status.email_enabled ? "on" : "off"}</span>
          <span className={"pill " + (status && status.execution_enabled ? "warn" : "muted")}><Icon.Shield size={10}/> execution {status && status.execution_enabled ? "enabled" : "disabled"}</span>
        </div>
      </div>

      <div className="grid-2">
        <AiAgentProviderCard status={status}/>
        <AiAgentSchedulerCard scheduler={scheduler} status={status} busy={busy} onAction={schedulerAction}/>
      </div>

      <div className="grid-4">
        <Stat label="Recommendations" value={summary.total || rows.length} sub={pending + " pending"}/>
        <Stat label="High severity" value={high} sub="critical / high"/>
        <Stat label="Approved" value={approved} sub="awaiting controlled execution"/>
        <Stat label="Latest run" value={latestRun ? latestRun.status : "-"} sub={latestRun ? aiAgentDate(latestRun.started_at) : "none"}/>
      </div>

      <div className="card">
        <div className="bd flex-row" style={{ gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div className="field" style={{ margin: 0, minWidth: 140 }}>
            <label>Severity</label>
            <select value={filters.severity} onChange={function(e) { setFilter("severity", e.target.value); }}>
              {["ALL", "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"].map(function(v) { return <option key={v} value={v}>{v}</option>; })}
            </select>
          </div>
          <div className="field" style={{ margin: 0, minWidth: 160 }}>
            <label>Category</label>
            <select value={filters.category} onChange={function(e) { setFilter("category", e.target.value); }}>
              {["ALL", "PERFORMANCE", "INDEX", "ERROR", "BACKUP", "REPLICATION", "PATRONI", "CONNECTION", "STORAGE", "VACUUM", "SECURITY", "OTHER"].map(function(v) { return <option key={v} value={v}>{v}</option>; })}
            </select>
          </div>
          <div className="field" style={{ margin: 0, minWidth: 150 }}>
            <label>Status</label>
            <select value={filters.approval_status} onChange={function(e) { setFilter("approval_status", e.target.value); }}>
              {["ALL", "PENDING", "APPROVED", "REJECTED", "EXECUTED", "FAILED"].map(function(v) { return <option key={v} value={v}>{v}</option>; })}
            </select>
          </div>
          <div className="field" style={{ margin: 0, minWidth: 190 }}>
            <label>Cluster</label>
            <input value={filters.cluster_name} onChange={function(e) { setFilter("cluster_name", e.target.value); }} placeholder="optional"/>
          </div>
          <div className="field" style={{ margin: 0, minWidth: 170 }}>
            <label>Database</label>
            <input value={filters.database_name} onChange={function(e) { setFilter("database_name", e.target.value); }} placeholder="optional"/>
          </div>
          <button className="btn sm ghost" onClick={load}><Icon.RefreshCw size={12}/> Reload</button>
        </div>
      </div>

      <div className="card">
        <div className="hd">AI Recommendations <span className="meta">{loading ? "loading" : rows.length + " rows"}</span></div>
        <div style={{ overflowX: "auto", maxHeight: 520 }}>
          <table className="tbl">
            <thead>
              <tr><th>Created</th><th>Severity</th><th>Category</th><th>Status</th><th>Finding</th><th>Object</th><th>Confidence</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map(function(row) {
                return (
                  <tr key={row.recommendation_id}>
                    <td className="txt-xs">{aiAgentDate(row.created_at)}</td>
                    <td><span className={"pill " + aiAgentTone(row.severity)}><span className="dot"/>{row.severity}</span></td>
                    <td className="mono txt-xs">{row.category}</td>
                    <td><span className={"pill " + aiAgentTone(row.approval_status)}><span className="dot"/>{row.approval_status}</span></td>
                    <td style={{ minWidth: 340, whiteSpace: "normal" }}>
                      <strong>{row.finding}</strong>
                      <div className="muted txt-xs">{row.recommendation}</div>
                    </td>
                    <td className="mono txt-xs">{row.object_name || row.database_name || row.cluster_name || "-"}</td>
                    <td className="num">{row.confidence_score != null ? Math.round(Number(row.confidence_score) * 100) + "%" : "-"}</td>
                    <td><button className="btn ghost sm" onClick={function() { openDetail(row); }}><Icon.Eye size={12}/> Detail</button></td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && (
                <tr><td colSpan="8" className="muted" style={{ textAlign: "center", padding: 24 }}>No AI recommendations match the current filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="hd">Run History <span className="meta">{runs.length} rows</span></div>
        <div style={{ overflowX: "auto", maxHeight: 280 }}>
          <table className="tbl">
            <thead><tr><th>Run</th><th>Trigger</th><th>Status</th><th>Started</th><th>Finished</th><th>Summary</th></tr></thead>
            <tbody>
              {runs.map(function(run) {
                return (
                  <tr key={run.run_id}>
                    <td className="mono txt-xs">#{run.run_id}</td>
                    <td>{run.trigger_type}</td>
                    <td><span className={"pill " + aiAgentTone(run.status)}><span className="dot"/>{run.status}</span></td>
                    <td className="txt-xs">{aiAgentDate(run.started_at)}</td>
                    <td className="txt-xs">{aiAgentDate(run.finished_at)}</td>
                    <td className="txt-xs" style={{ maxWidth: 420, whiteSpace: "normal" }}>{run.error_message || run.summary || "-"}</td>
                  </tr>
                );
              })}
              {!runs.length && <tr><td colSpan="6" className="muted" style={{ textAlign: "center", padding: 18 }}>No AI agent runs recorded yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <Drawer onClose={function() { setSelected(null); setDetail(null); }}>
          <div className="hd">
            <Icon.Zap size={16}/>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>Recommendation detail</div>
              <div className="muted txt-xs">#{selected.recommendation_id}</div>
            </div>
            <button className="btn ghost icon" style={{ marginLeft: "auto" }} onClick={function() { setSelected(null); setDetail(null); }}><Icon.X size={14}/></button>
          </div>
          <div className="bd">
            {!detail && <div className="muted">Loading detail...</div>}
            {detail && (
              <div>
                <div className="flex-row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <span className={"pill " + aiAgentTone(detail.severity)}><span className="dot"/>{detail.severity}</span>
                  <span className="pill muted">{detail.category}</span>
                  <span className={"pill " + aiAgentTone(detail.approval_status)}><span className="dot"/>{detail.approval_status}</span>
                  <span className="pill muted">{detail.risk_level} risk</span>
                  <span className="pill info">{detail.confidence_score != null ? Math.round(Number(detail.confidence_score) * 100) + "%" : "-"} confidence</span>
                </div>
                <div className="section-h">Finding</div>
                <div className="txt-sm">{detail.finding}</div>
                <div className="section-h mt-3">Root Cause</div>
                <div className="txt-sm">{detail.root_cause || "-"}</div>
                <div className="section-h mt-3">Recommendation</div>
                <div className="txt-sm">{detail.recommendation || "-"}</div>
                {(detail.recommended_sql || detail.rollback_sql) && (
                  <div className="grid-2 mt-3">
                    <div>
                      <div className="txt-xs muted">Recommended SQL</div>
                      <pre className="logbox" style={{ whiteSpace: "pre-wrap" }}>{detail.recommended_sql || "-"}</pre>
                    </div>
                    <div>
                      <div className="txt-xs muted">Rollback SQL</div>
                      <pre className="logbox" style={{ whiteSpace: "pre-wrap" }}>{detail.rollback_sql || "-"}</pre>
                    </div>
                  </div>
                )}
                <div className="section-h mt-3">Evidence</div>
                <pre className="logbox" style={{ whiteSpace: "pre-wrap", maxHeight: 260 }}>{JSON.stringify(detail.evidence || {}, null, 2)}</pre>

                <div className="section-h mt-3">Actions</div>
                <div className="flex-row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <button className="btn sm primary" disabled={busy || detail.approval_status !== "PENDING"} onClick={function() { recAction("approve"); }}><Icon.Check size={12}/> Approve</button>
                  <button className="btn sm ghost" disabled={busy || detail.approval_status === "EXECUTED"} onClick={function() { recAction("reject", { reason: rejectReason || "Rejected from Web UI" }); }}><Icon.X size={12}/> Reject</button>
                  <button className="btn sm ghost" disabled={busy || detail.approval_status !== "APPROVED"} onClick={function() { recAction("execute", { confirm: true }); }}><Icon.ShieldAlert size={12}/> Execute</button>
                  <input style={{ minWidth: 240 }} value={rejectReason} onChange={function(e) { setRejectReason(e.target.value); }} placeholder="reject reason"/>
                </div>

                <div className="section-h mt-3">Audit History</div>
                <div style={{ overflowX: "auto", maxHeight: 240 }}>
                  <table className="tbl">
                    <thead><tr><th>Time</th><th>Action</th><th>Status</th><th>Actor</th><th>Output</th></tr></thead>
                    <tbody>
                      {(detail.audit_history || []).map(function(row) {
                        return (
                          <tr key={row.action_id}>
                            <td className="txt-xs">{aiAgentDate(row.created_at)}</td>
                            <td>{row.action_type}</td>
                            <td><span className={"pill " + aiAgentTone(row.execution_status)}>{row.execution_status}</span></td>
                            <td className="mono txt-xs">{row.executed_by || row.approved_by || row.requested_by || "-"}</td>
                            <td className="txt-xs" style={{ maxWidth: 320, whiteSpace: "normal" }}>{row.error_message || row.execution_output || "-"}</td>
                          </tr>
                        );
                      })}
                      {(!detail.audit_history || !detail.audit_history.length) && <tr><td colSpan="5" className="muted" style={{ textAlign: "center", padding: 14 }}>No audit actions yet.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </Drawer>
      )}
    </div>
  );
}

window.AiAgentScreen = AiAgentScreen;
