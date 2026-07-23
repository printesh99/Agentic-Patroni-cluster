// Phase 1 Day 1 operational screens: runs, audit, alerts.

function v1Url(path, params) {
  var url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(function(entry) {
    var key = entry[0];
    var value = entry[1];
    if (value !== null && value !== undefined && value !== "" && value !== "all") {
      url.searchParams.set(key, value);
    }
  });
  return url.toString();
}

async function v1Json(path, params) {
  var response = await fetch(v1Url(path, params), { cache: "no-store" });
  return hbzJsonResponse(response);
}

function phase1Date(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("en-GB", { hour12: false });
}

function phase1Pill(kind) {
  if (kind === "succeeded" || kind === "active" || kind === "ok") return "ok";
  if (kind === "failed" || kind === "critical" || kind === "danger") return "danger";
  if (kind === "pending_approval" || kind === "warning" || kind === "warn") return "warn";
  if (kind === "running" || kind === "info") return "info";
  return "muted";
}

function Phase1Toolbar({ children, loading, error, source }) {
  return (
    <div className="card">
      <div className="bd" style={{display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap"}}>
        {children}
        <div className="grow"/>
        {source && <span className="pill muted">{source}</span>}
        {loading && <span className="pill muted"><Icon.Loader size={12}/>Loading</span>}
        {error && <span className="pill danger"><span className="dot"/>{error}</span>}
      </div>
    </div>
  );
}

function RunHistoryScreen({ lastRefresh }) {
  var statusState = React.useState("all");
  var kindState = React.useState("all");
  var dataState = React.useState({ jobs: [], count: 0, source: "" });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var actionState = React.useState(false);

  var status = statusState[0], setStatus = statusState[1];
  var kind = kindState[0], setKind = kindState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var actionBusy = actionState[0], setActionBusy = actionState[1];

  function submitDryRun() {
    setActionBusy(true);
    setError(null);
    fetch("/api/v1/jobs/dry-run", {
      method: "POST",
      headers: { "content-type": "application/json", "x-console-role": "operator" },
      body: JSON.stringify({
        cluster_id: activeClusterId(),
        kind: "backup_validate",
        target: "phase1",
        reason: "Phase 1 console validation"
      })
    })
      .then(hbzJsonResponse)
      .then(function() {
        return v1Json("/api/v1/jobs", { cluster: activeClusterId(), status: status, kind: kind });
      })
      .then(function(payload) {
        setData(payload);
        setActionBusy(false);
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setActionBusy(false);
      });
  }

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    v1Json("/api/v1/jobs", { cluster: activeClusterId(), status: status, kind: kind })
      .then(function(payload) {
        if (!alive) return;
        setData(payload);
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [status, kind, lastRefresh]);

  var jobs = data.jobs || [];
  var running = jobs.filter(function(j) { return j.state === "running"; }).length;
  var pending = jobs.filter(function(j) { return j.state === "pending_approval"; }).length;
  var complete = jobs.filter(function(j) { return j.state === "succeeded"; }).length;
  var failed = jobs.filter(function(j) { return j.state === "failed"; }).length;
  var jobStateRows = phaseCountRows(jobs, function(j) { return j.state; });
  var jobKindRows = phaseCountRows(jobs, function(j) { return j.kind; }, function() { return "info"; });
  var jobTimelineRows = jobs.slice(0, 8).map(function(job) {
    return { title: job.kind, sub: phase1Date(job.submitted_at), tone: phase1Pill(job.state), label: job.state };
  });

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <div className="field" style={{margin: 0, minWidth: 170}}>
          <label>State</label>
          <select value={status} onChange={function(e) { setStatus(e.target.value); }}>
            <option value="all">All states</option>
            <option value="pending_approval">Pending approval</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
          </select>
        </div>
        <div className="field" style={{margin: 0, minWidth: 170}}>
          <label>Kind</label>
          <select value={kind} onChange={function(e) { setKind(e.target.value); }}>
            <option value="all">All kinds</option>
            <option value="switchover">Switchover</option>
            <option value="backup_validate">Backup validate</option>
            <option value="vacuum">Vacuum</option>
            <option value="param_set">Parameter change</option>
          </select>
        </div>
        <button className="btn sm primary" onClick={submitDryRun} disabled={actionBusy}>
          <Icon.Play size={12}/> Validate
        </button>
      </Phase1Toolbar>

      <div className="section-h">Run History</div>
      <div className="grid-4">
        <Stat label="Total runs" value={jobs.length}/>
        <Stat label="Pending approval" value={pending}/>
        <Stat label="Running" value={running}/>
        <Stat label="Completed / failed" value={complete + " / " + failed}/>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><DonutChart title="Run State" rows={jobStateRows} center={jobs.length} sub="jobs"/></div></div>
        <div className="card"><div className="bd"><BarList title="Run Kind" rows={jobKindRows}/></div></div>
        <div className="card"><div className="bd"><div className="chart-title">Recent Runs</div><TimelineStrip rows={jobTimelineRows}/></div></div>
      </div>

      <div className="card">
        <div className="hd">Console Jobs <span className="meta">{jobs.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Submitted</th><th>Kind</th><th>Cluster</th><th>Target</th><th>Actor</th><th>State</th><th>Request</th><th></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map(function(job) {
                return (
                  <tr key={job.id}>
                    <td>{phase1Date(job.submitted_at)}</td>
                    <td className="mono">{job.kind}</td>
                    <td className="mono">{job.cluster_name || job.cluster_id}</td>
                    <td>{job.target || "-"}</td>
                    <td>{job.submitted_by || "-"}</td>
                    <td><span className={"pill " + phase1Pill(job.state)}><span className="dot"/>{job.state}</span></td>
                    <td className="mono txt-xs">{job.request_id}</td>
                    <td><button className="btn ghost sm" onClick={function() { setSelected(job); }}><Icon.Eye size={12}/>Detail</button></td>
                  </tr>
                );
              })}
              {!loading && jobs.length === 0 && (
                <tr>
                  <td colSpan="8" style={{textAlign: "center", padding: 28}} className="muted">
                    No console-managed runs have been submitted yet. Metadata DB, approvals, and worker execution are the next Phase 1 slices.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <Drawer onClose={function() { setSelected(null); }}>
          <div className="hd">
            <Icon.Terminal size={16}/>
            <div>
              <div style={{fontWeight: 600, fontSize: 14}}>Run detail</div>
              <div className="muted txt-xs">request_id <span className="mono">{selected.request_id}</span></div>
            </div>
            <button className="btn ghost icon" style={{marginLeft: "auto"}} onClick={function() { setSelected(null); }}><Icon.X size={14}/></button>
          </div>
          <div className="bd">
            <div className="grid-2 txt-sm">
              <div><div className="txt-xs muted">Kind</div>{selected.kind}</div>
              <div><div className="txt-xs muted">State</div><span className={"pill " + phase1Pill(selected.state)}><span className="dot"/>{selected.state}</span></div>
              <div><div className="txt-xs muted">Cluster</div>{selected.cluster_name || selected.cluster_id}</div>
              <div><div className="txt-xs muted">Target</div>{selected.target || "-"}</div>
              <div><div className="txt-xs muted">Submitted by</div>{selected.submitted_by_email || "-"}</div>
              <div><div className="txt-xs muted">Approved by</div>{selected.approved_by_email || "-"}</div>
              <div><div className="txt-xs muted">Submitted</div>{phase1Date(selected.submitted_at)}</div>
              <div><div className="txt-xs muted">Completed</div>{phase1Date(selected.completed_at)}</div>
            </div>
            {selected.reason && (
              <div className="mt-3">
                <div className="txt-xs muted">Reason</div>
                <div className="txt-sm">{selected.reason}</div>
              </div>
            )}
            {(selected.stdout_excerpt || selected.stderr_excerpt) && (
              <div className="mt-3">
                <div className="txt-xs muted">Execution output</div>
                <pre className="logbox" style={{whiteSpace: "pre-wrap"}}>
                  {selected.stdout_excerpt && <div className="ok">{selected.stdout_excerpt}</div>}
                  {selected.stderr_excerpt && <div className="err">{selected.stderr_excerpt}</div>}
                </pre>
              </div>
            )}
            {selected.result && (
              <div className="mt-3">
                <div className="txt-xs muted">Result</div>
                <pre className="logbox" style={{whiteSpace: "pre-wrap"}}>{JSON.stringify(selected.result, null, 2)}</pre>
              </div>
            )}
          </div>
        </Drawer>
      )}
    </div>
  );
}

function AuditLogScreen({ lastRefresh }) {
  var searchState = React.useState("");
  var dataState = React.useState({ audit: [], count: 0, source: "" });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);

  var search = searchState[0], setSearch = searchState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    v1Json("/api/v1/audit", { cluster: activeClusterId(), limit: 50 })
      .then(function(payload) {
        if (!alive) return;
        setData(payload);
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  var rows = (data.audit || []).filter(function(row) {
    if (!search) return true;
    var text = [row.actor || row.actor_sub, row.action, row.detail, row.target_kind, row.target_id, row.request_id].join(" ").toLowerCase();
    return text.indexOf(search.toLowerCase()) >= 0;
  });
  var auditActionRows = phaseCountRows(rows, function(row) { return row.action; }, function() { return "info"; });
  var auditTargetRows = phaseCountRows(rows, function(row) { return row.target_kind || "not reported"; }, function() { return "teal"; });
  var auditStatusRows = phaseCountRows(rows, function(row) { return row.executed ? "executed" : row.dry_run ? "dry-run" : "recorded"; }, function(key) { return key === "executed" ? "ok" : key === "dry-run" ? "info" : "muted"; });

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <div className="field" style={{margin: 0, minWidth: 300}}>
          <label>Search audit stream</label>
          <input type="text" value={search} onChange={function(e) { setSearch(e.target.value); }} placeholder="actor, action, target, request_id"/>
        </div>
      </Phase1Toolbar>

      <div className="section-h">Audit Log</div>
      <div className="grid-4">
        <Stat label="Rows loaded" value={rows.length}/>
        <Stat label="Source events" value={(data.audit || []).length}/>
        <Stat label="Execution evidence" value={rows.length ? (rows.filter(function(row) { return row.executed; }).length + " executed") : "-"}/>
        <Stat label="Latest event" value={rows[0] ? phase1Date(rows[0].ts).split(",")[1] || "-" : "-"}/>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><BarList title="Actions" rows={auditActionRows}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Targets" rows={auditTargetRows} center={rows.length} sub="events"/></div></div>
        <div className="card"><div className="bd"><StatusBreakdown rows={auditStatusRows}/></div></div>
      </div>

      <div className="card">
        <div className="hd">Derived Audit Events <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Request ID</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(function(row) {
                return (
                  <tr key={row.id}>
                    <td>{phase1Date(row.ts)}</td>
                    <td className="mono txt-xs">{row.actor || row.actor_sub || "not reported"}</td>
                    <td className="mono">{row.action}</td>
                    <td><span className="muted">{row.target_kind || "not reported"}</span> <span className="mono txt-xs">{row.target_id || row.detail || "-"}</span></td>
                    <td className="mono txt-xs">{row.request_id}</td>
                    <td><span className={"pill " + (row.executed ? "ok" : row.dry_run ? "info" : "muted")}><span className="dot"/> {row.executed ? "executed" : row.dry_run ? "dry-run" : "recorded"}</span></td>
                    <td><button className="btn ghost sm" onClick={function() { setSelected(row); }}><Icon.Eye size={12}/>Detail</button></td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && (
                <tr><td colSpan="7" style={{textAlign: "center", padding: 28}} className="muted">No audit events match the current filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <Drawer onClose={function() { setSelected(null); }}>
          <div className="hd">
            <Icon.Shield size={16}/>
            <div>
              <div style={{fontWeight: 600, fontSize: 14}}>Audit detail</div>
              <div className="muted txt-xs">row <span className="mono">{selected.id}</span></div>
            </div>
            <button className="btn ghost icon" style={{marginLeft: "auto"}} onClick={function() { setSelected(null); }}><Icon.X size={14}/></button>
          </div>
          <div className="bd">
            <div className="logbox">
              <div className="ok">{JSON.stringify(selected, null, 2)}</div>
            </div>
          </div>
        </Drawer>
      )}
    </div>
  );
}

function AlertsScreen({ lastRefresh }) {
  var dataState = React.useState({ alerts: [], rules: [], count: 0, source: "" });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);

  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    v1Json("/api/v1/alerts", { cluster: activeClusterId() })
      .then(function(payload) {
        if (!alive) return;
        setData(payload);
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  var alerts = data.alerts || [];
  var rules = data.rules || [];
  var critical = alerts.filter(function(a) { return a.severity === "critical"; }).length;
  var warning = alerts.filter(function(a) { return a.severity === "warning"; }).length;
  var alertSeverityRows = phaseCountRows(alerts, function(a) { return a.severity; });
  var ruleSeverityRows = phaseCountRows(rules, function(r) { return r.severity; });
  var ruleStatusRows = phaseCountRows(rules, function(r) { return r.enabled ? "enabled" : "disabled"; }, function(key) { return key === "enabled" ? "ok" : "muted"; });

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <span className={"pill " + (alerts.length ? "warn" : "ok")}><span className="dot"/>{alerts.length ? "Active alerts" : "No active alerts"}</span>
      </Phase1Toolbar>

      <div className="section-h">Alerts & Insights</div>
      <div className="grid-4">
        <Stat label="Active alerts" value={alerts.length}/>
        <Stat label="Critical" value={critical}/>
        <Stat label="Warning" value={warning}/>
        <Stat label="Rules enabled" value={rules.filter(function(r) { return r.enabled; }).length}/>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><DonutChart title="Active Severity" rows={alertSeverityRows} center={alerts.length} sub="alerts"/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Rule Severity" rows={ruleSeverityRows} center={rules.length} sub="rules"/></div></div>
        <div className="card"><div className="bd"><StatusBreakdown rows={ruleStatusRows}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Active Alerts <span className="meta">{alerts.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Severity</th><th>Name</th><th>Cluster</th><th>Summary</th><th>Source</th></tr></thead>
              <tbody>
                {alerts.map(function(alert) {
                  return (
                    <tr key={alert.id}>
                      <td><span className={"pill " + phase1Pill(alert.severity)}><span className="dot"/>{alert.severity}</span></td>
                      <td>{alert.name}</td>
                      <td className="mono">{alert.cluster_id}</td>
                      <td>{alert.summary}</td>
                      <td className="mono txt-xs">{alert.source}</td>
                    </tr>
                  );
                })}
                {!loading && alerts.length === 0 && (
                  <tr><td colSpan="5" style={{textAlign: "center", padding: 28}} className="muted">No active alerts from Phase 1 derived checks.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="hd">Alert Rules <span className="meta">{(data.rules || []).length} bootstrap rules</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Rule</th><th>Severity</th><th>Status</th></tr></thead>
              <tbody>
                {(data.rules || []).map(function(rule) {
                  return (
                    <tr key={rule.name}>
                      <td>{rule.name}</td>
                      <td><span className={"pill " + phase1Pill(rule.severity)}>{rule.severity}</span></td>
                      <td><span className={"pill " + (rule.enabled ? "ok" : "muted")}><span className="dot"/>{rule.enabled ? "enabled" : "disabled"}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
window.RunHistoryScreen = RunHistoryScreen;
window.AuditLogScreen = AuditLogScreen;
window.AlertsScreen = AlertsScreen;
window.v1Url = v1Url;
window.v1Json = v1Json;
window.phase1Date = phase1Date;
window.phase1Pill = phase1Pill;
window.Phase1Toolbar = Phase1Toolbar;
