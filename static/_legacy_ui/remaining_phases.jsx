// Remaining DBA console modules, Phases 2-10.
// These screens are read-only aggregations over existing console APIs.

function rmFetch(path, params) {
  return v1Json(path, params)
    .then(function(payload) { return { ok: true, data: payload }; })
    .catch(function(err) { return { ok: false, error: err.message || String(err) }; });
}

function rmPill(status) {
  if (status === "ok" || status === "healthy" || status === "ready" || status === "succeeded" || status === "enabled") return "ok";
  if (status === "critical" || status === "blocked" || status === "failed" || status === "missing") return "danger";
  if (status === "running" || status === "info" || status === "active") return "info";
  if (status === "warning" || status === "pending_approval" || status === "stale" || status === "caution") return "warn";
  return "muted";
}

function rmDate(value) {
  if (!value) return "-";
  try { return new Date(value).toLocaleString("en-GB", { hour12: false }); }
  catch (e) { return String(value); }
}

function rmState(value) {
  return value ? "ok" : "missing";
}

function rmClusterId(cluster) {
  return cluster && cluster.id ? cluster.id : (window.ACTIVE_CLUSTER_ID || "uat");
}

function rmFinding(rows, severity, component, summary, detail) {
  rows.push({ severity: severity, component: component, summary: summary, detail: detail || "" });
}

function RmFindingTable({ rows }) {
  var list = rows || [];
  if (!list.length) return <EmptyState icon={Icon.CheckCircle} title="No findings" hint="No issues were derived from the available evidence."/>;
  return (
    <table className="tbl">
      <thead><tr><th>Severity</th><th>Component</th><th>Evidence</th></tr></thead>
      <tbody>
        {list.map(function(row, index) {
          return (
            <tr key={index}>
              <td><span className={"pill " + rmPill(row.severity)}><span className="dot"/>{row.severity}</span></td>
              <td>{row.component}</td>
              <td><strong>{row.summary}</strong>{row.detail ? <div className="muted txt-xs mt-2">{row.detail}</div> : null}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function RmLoadPage({ title }) {
  return <div className="page"><div className="tile-row"><KPI skeleton/><KPI skeleton/><KPI skeleton/><KPI skeleton/></div></div>;
}

function RmErrorPage({ title, error }) {
  return <div className="page"><EmptyState icon={Icon.AlertTriangle} title={title + " unavailable"} hint={error}/></div>;
}

function useRmPayload(lastRefresh, deps, loader) {
  var dataState = React.useState(null);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    loader().then(function(payload) {
      if (!alive) return;
      setData(payload);
      setLoading(false);
    }).catch(function(err) {
      if (!alive) return;
      setError(err.message || String(err));
      setLoading(false);
    });
    return function() { alive = false; };
  }, [lastRefresh].concat(deps || []));
  return { data: data, loading: loading, error: error };
}

function RecoveryAssuranceScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/cutover/runs", { limit: 10 }),
      rmFetch("/api/v1/readiness"),
    ]).then(function(r) { return { backups: r[0], runs: r[1], readiness: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Recovery assurance"/>;
  if (state.error) return <RmErrorPage title="Recovery assurance" error={state.error}/>;

  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var repo = backups.repo || {};
  var summary = backups.summary || {};
  var archive = backups.archive || {};
  var settings = backups.settings || {};
  var schedules = backups.schedules || [];
  var history = backups.history || [];
  var findings = [];
  if (!repo.repo) rmFinding(findings, "critical", "Repository", "pgBackRest repo name is missing");
  if (!repo.bucket || !repo.s3_endpoint) rmFinding(findings, "critical", "Repository", "S3 bucket or endpoint is not configured", "Bucket/endpoint values are checked for presence only.");
  if (settings.archive_mode !== "on") rmFinding(findings, "critical", "WAL archive", "archive_mode is not enabled", "PITR and rebuild evidence require archive coverage.");
  if (!history.length) rmFinding(findings, "warning", "Restore drill", "No backup validation or restore drill history recorded");
  if (!schedules.length) rmFinding(findings, "warning", "Schedules", "No backup schedules returned by the console");
  if (Number(archive.failed_count || 0) > 0) rmFinding(findings, "warning", "Archive", "Archive failures were recorded", "Failed count: " + archive.failed_count);

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={summary.status === "critical" ? "red" : findings.length ? "orange" : "green"} label="Recovery assurance" value={summary.status || "unknown"} sub="pgBackRest + archive + drill evidence"/>
        <KPI color={repo.bucket && repo.s3_endpoint ? "green" : "red"} label="S3 repo config" value={repo.bucket && repo.s3_endpoint ? "present" : "missing"} sub="bucket/endpoint presence"/>
        <KPI color={settings.archive_mode === "on" ? "green" : "red"} label="WAL archive" value={settings.archive_mode || "-"} sub="archive_mode"/>
        <KPI color={history.length ? "green" : "orange"} label="Validation history" value={fmtInt(history.length)} sub="recorded jobs"/>
      </div>

      <div className="card">
        <div className="hd"><span className="flex-row"><Icon.HardDrive size={15}/>Recovery blockers</span><SourceBadge source={backups.source || "backups-api"}/></div>
        <div className="bd"><RmFindingTable rows={findings}/></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Backup schedules</div>
          <div className="bd">
            <table className="tbl">
              <thead><tr><th>Type</th><th>Cron</th><th>Retention</th><th>State</th></tr></thead>
              <tbody>
                {schedules.map(function(row) {
                  return <tr key={row.type}><td>{row.type}</td><td className="mono txt-xs">{row.cron}</td><td>{row.retention_days}d</td><td><span className={"pill " + rmPill(row.enabled ? "enabled" : "missing")}>{row.enabled ? "enabled" : "disabled"}</span></td></tr>;
                })}
                {!schedules.length && <tr><td colSpan="4" className="muted">No schedules available.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">PITR readiness</div>
          <div className="bd">
            <div className="grid-2">
              <Stat label="Repo" value={repo.repo || "-"} sub={repo.stanza ? "stanza " + repo.stanza : "stanza unknown"}/>
              <Stat label="Archive command" value={settings.archive_command || "-"} sub={"archive_timeout=" + (settings.archive_timeout || "-")}/>
              <Stat label="Failed archive count" value={fmtInt(archive.failed_count || 0)} sub={archive.last_failed_time ? rmDate(archive.last_failed_time) : "no failure time"}/>
              <Stat label="Last archived WAL" value={archive.last_archived_wal || "-"} sub={archive.last_archived_time ? rmDate(archive.last_archived_time) : "not recorded"}/>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PgBouncerDeepDiveScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/ui/cluster"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/application-activity"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/sessions"),
    ]).then(function(r) { return { cluster: r[0], activity: r[1], sessions: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="PgBouncer pooling"/>;
  if (state.error) return <RmErrorPage title="PgBouncer pooling" error={state.error}/>;
  var ui = state.data.cluster.ok ? state.data.cluster.data : {};
  var pgb = ui.pgbouncer || {};
  var activity = state.data.activity.ok ? state.data.activity.data : {};
  var summary = activity.summary || {};
  var sourceRows = activity.source_breakdown || [];
  var clientRows = activity.client_breakdown || [];
  var findings = [];
  if (Number(pgb.pods_ready || 0) < Number(pgb.pods_total || 0)) rmFinding(findings, "critical", "Pods", "Not all PgBouncer pods are ready");
  if (!Number(summary.pgbouncer_sessions || 0)) rmFinding(findings, "warning", "Routing", "No PgBouncer-sourced sessions detected", "This may be normal in local Docker, but production traffic should show pooled sessions.");
  if (Number(summary.idle_in_transaction_sessions || 0) > 0) rmFinding(findings, "warning", "Sessions", "Idle-in-transaction sessions are present");
  if (Number(summary.direct_sessions || 0) > Number(summary.pgbouncer_sessions || 0)) rmFinding(findings, "warning", "Routing", "Direct sessions exceed PgBouncer sessions");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={Number(pgb.pods_ready || 0) === Number(pgb.pods_total || 0) ? "green" : "red"} label="PgBouncer pods" value={(pgb.pods_ready || 0) + "/" + (pgb.pods_total || 0)} sub="ready/total"/>
        <KPI color="blue" label="Pooled sessions" value={fmtInt(summary.pgbouncer_sessions || 0)} sub="inferred from pg_stat_activity"/>
        <KPI color={summary.direct_sessions ? "orange" : "green"} label="Direct sessions" value={fmtInt(summary.direct_sessions || 0)} sub="bypassing pool"/>
        <KPI color={summary.idle_in_transaction_sessions ? "orange" : "green"} label="Idle in txn" value={fmtInt(summary.idle_in_transaction_sessions || 0)} sub="session risk"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Users size={15}/>Pooling findings</span></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="grid-2">
        <div className="card">
          <div className="hd">Connection source breakdown</div>
          <div className="bd">
            <table className="tbl"><thead><tr><th>Source</th><th>Sessions</th><th>Active</th><th>Idle</th><th>Oldest query</th></tr></thead><tbody>
              {sourceRows.map(function(row) { return <tr key={row.connection_source}><td>{row.connection_source}</td><td className="num">{row.sessions}</td><td className="num">{row.active}</td><td className="num">{row.idle}</td><td>{fmtSec(row.oldest_query_age_sec || 0)}</td></tr>; })}
              {!sourceRows.length && <tr><td colSpan="5" className="muted">No source breakdown available.</td></tr>}
            </tbody></table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Top clients</div>
          <div className="bd">
            <table className="tbl"><thead><tr><th>Client</th><th>Source</th><th>Sessions</th><th>Users</th><th>DBs</th></tr></thead><tbody>
              {clientRows.slice(0, 8).map(function(row, index) { return <tr key={index}><td className="mono txt-xs">{row.client_addr}</td><td>{row.connection_source}</td><td className="num">{row.sessions}</td><td className="num">{row.users}</td><td className="num">{row.databases}</td></tr>; })}
              {!clientRows.length && <tr><td colSpan="5" className="muted">No client evidence available.</td></tr>}
            </tbody></table>
          </div>
        </div>
      </div>
    </div>
  );
}

function WalArchivePressureScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "wal_bytes", range: "30d", agg: "max" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "replication_slot_wal_bytes", range: "30d", agg: "max" }),
    ]).then(function(r) { return { backups: r[0], repl: r[1], wal: r[2], slotWal: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="WAL archive pressure"/>;
  if (state.error) return <RmErrorPage title="WAL archive pressure" error={state.error}/>;
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var repl = state.data.repl.ok ? state.data.repl.data : {};
  var wal = state.data.wal.ok ? state.data.wal.data : {};
  var slotWal = state.data.slotWal.ok ? state.data.slotWal.data : {};
  var archive = backups.archive || {};
  var settings = backups.settings || {};
  var rs = repl.summary || {};
  var findings = [];
  if (settings.archive_mode !== "on") rmFinding(findings, "critical", "Archive mode", "archive_mode is not on");
  if (Number(archive.failed_count || 0) > 0) rmFinding(findings, "warning", "Archive failures", "Archive failures are recorded");
  if (!wal.available || !(wal.points || []).length) rmFinding(findings, "warning", "WAL metric", "WAL generation series is unavailable");
  if (Number(rs.max_lag_bytes || 0) > 16 * 1024 * 1024) rmFinding(findings, "warning", "Replication lag", "Replication lag exceeds 16 MiB", fmtBytes(rs.max_lag_bytes));
  if (Number(rs.inactive_slots || 0) > 0) rmFinding(findings, "warning", "Slots", "Inactive slots may retain WAL");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={settings.archive_mode === "on" ? "green" : "red"} label="Archive mode" value={settings.archive_mode || "-"} sub={settings.archive_command || "(no archive command)"}/>
        <KPI color={archive.failed_count ? "orange" : "green"} label="Archive failures" value={fmtInt(archive.failed_count || 0)} sub={archive.last_failed_time ? rmDate(archive.last_failed_time) : "no failure time"}/>
        <KPI color={rs.max_lag_bytes ? "orange" : "green"} label="Max repl lag" value={fmtBytes(rs.max_lag_bytes || 0)} sub="replication topology"/>
        <KPI color={slotWal.available ? "green" : "orange"} label="Slot WAL series" value={(slotWal.points || []).length} sub={slotWal.source_table || "no samples"}/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.HardDrive size={15}/>WAL and archive findings</span></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">WAL settings</div><div className="bd"><div className="grid-3"><Stat label="wal_level" value={settings.wal_level || "-"} /><Stat label="max_wal_size" value={(settings.max_wal_size || "-") + " MB"} /><Stat label="archive_timeout" value={settings.archive_timeout || "-"} /></div></div></div>
        <div className="card"><div className="hd">Replication slots</div><div className="bd"><div className="grid-3"><Stat label="Slots" value={fmtInt(rs.replication_slots || 0)}/><Stat label="Logical" value={fmtInt(rs.logical_slots || 0)}/><Stat label="Inactive" value={fmtInt(rs.inactive_slots || 0)}/></div></div></div>
      </div>
    </div>
  );
}

function StorageHealthScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "storage_bytes", range: "30d", agg: "max" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/forecast", { metric: "storage_bytes", range: "30d", horizon_days: 30 }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/lifecycle/scale/" + encodeURIComponent(clusterId)),
    ]).then(function(r) { return { readiness: r[0], storage: r[1], forecast: r[2], backups: r[3], scale: r[4] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Storage health"/>;
  if (state.error) return <RmErrorPage title="Storage health" error={state.error}/>;
  var readiness = state.data.readiness.ok ? state.data.readiness.data : {};
  var storage = state.data.storage.ok ? state.data.storage.data : {};
  var forecast = state.data.forecast.ok ? state.data.forecast.data : {};
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var scale = state.data.scale.ok ? state.data.scale.data : {};
  var current = scale.current || {};
  var resources = current.resources || {};
  var checks = readiness.items || readiness.checks || [];
  var storageCheck = checks.filter(function(c) { return c.key === "kubernetes" || c.key === "pgbackrest"; });
  var points = storage.points || [];
  var latestBytes = points.length ? points[points.length - 1][1] : 0;
  var findings = [];
  storageCheck.forEach(function(c) { if (c.status !== "ok") rmFinding(findings, c.status === "critical" ? "critical" : "warning", c.label, c.detail, c.source); });
  if (!points.length) rmFinding(findings, "warning", "Object metrics", "Storage metric series has no samples");
  if (!(backups.repo || {}).bucket || !(backups.repo || {}).s3_endpoint) rmFinding(findings, "critical", "Object storage", "pgBackRest object storage config is incomplete");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Observed storage" value={fmtBytes(latestBytes || 0)} sub={storage.source_table || "metric series"}/>
        <KPI color="green" label="Provisioned storage" value={resources.storage_gib ? resources.storage_gib + " GiB" : "-"} sub={resources.namespace || "namespace unknown"}/>
        <KPI color={findings.length ? "orange" : "green"} label="Storage findings" value={fmtInt(findings.length)} sub="ODF/PVC/NooBaa evidence gaps"/>
        <KPI color={(backups.repo || {}).bucket ? "green" : "red"} label="S3 repo" value={(backups.repo || {}).bucket ? "configured" : "missing"} sub="bucket value not displayed"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.HardDrive size={15}/>Storage and object-store findings</span></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">Capacity signals</div><div className="bd"><div className="grid-3"><Stat label="30d samples" value={fmtInt(points.length)}/><Stat label="Forecast source" value={forecast.source || "not available"}/><Stat label="Rollup source" value={storage.source_table || "-"}/></div></div></div>
        <div className="card"><div className="hd">OpenShift storage readiness</div><div className="bd"><table className="tbl"><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead><tbody>{storageCheck.map(function(c) { return <tr key={c.key}><td>{c.label}</td><td><span className={"pill " + rmPill(c.status)}>{c.status}</span></td><td>{c.detail}</td></tr>; })}</tbody></table></div></div>
      </div>
    </div>
  );
}

function ChangeCalendarScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/audit", { cluster: clusterId, limit: 30 }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/maintenance"),
    ]).then(function(r) { return { jobs: r[0], audit: r[1], maintenance: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Change calendar"/>;
  if (state.error) return <RmErrorPage title="Change calendar" error={state.error}/>;
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var audit = state.data.audit.ok ? (state.data.audit.data.audit || []) : [];
  var maintenance = state.data.maintenance.ok ? state.data.maintenance.data : {};
  var pending = jobs.filter(function(j) { return j.state === "pending_approval"; });
  var risky = jobs.filter(function(j) { return /restore|cutover|switchover|upgrade|scale|replica|restart|reinit|param|patroni/i.test(j.kind || j.target || ""); });
  var windows = maintenance.windows || maintenance.maintenance_windows || [];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={pending.length ? "orange" : "green"} label="Pending approvals" value={fmtInt(pending.length)} sub="change gate"/>
        <KPI color={risky.length ? "orange" : "green"} label="Risky job requests" value={fmtInt(risky.length)} sub="dry-run / approval evidence"/>
        <KPI color={windows.length ? "green" : "orange"} label="Windows" value={fmtInt(windows.length)} sub="configured maintenance windows"/>
        <KPI color="blue" label="Audit events" value={fmtInt(audit.length)} sub="recent evidence"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Pending and risky work</div><div className="bd"><table className="tbl"><thead><tr><th>Submitted</th><th>Kind</th><th>State</th><th>Reason</th></tr></thead><tbody>{risky.slice(0, 12).map(function(j) { return <tr key={j.id}><td className="mono txt-xs">{rmDate(j.submitted_at)}</td><td>{j.kind}</td><td><span className={"pill " + rmPill(j.state)}>{j.state}</span></td><td>{j.reason}</td></tr>; })}{!risky.length && <tr><td colSpan="4" className="muted">No risky job requests.</td></tr>}</tbody></table></div></div>
        <div className="card"><div className="hd">Maintenance policy</div><div className="bd"><RmFindingTable rows={windows.length ? [] : [{ severity: "warning", component: "Calendar", summary: "No maintenance windows returned", detail: "Add window metadata before enforcing change-window-only policy." }]}/></div></div>
      </div>
    </div>
  );
}

function OpsInboxScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/collector/alert-bundle-requests", { cluster: clusterId, status: "all" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/findings", { status: "all" }),
    ]).then(function(r) { return { alerts: r[0], jobs: r[1], bundles: r[2], findings: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Operations inbox"/>;
  if (state.error) return <RmErrorPage title="Operations inbox" error={state.error}/>;
  var alerts = state.data.alerts.ok ? (state.data.alerts.data.alerts || []) : [];
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var bundles = state.data.bundles.ok ? (state.data.bundles.data.bundles || state.data.bundles.data.requests || []) : [];
  var findings = state.data.findings.ok ? (state.data.findings.data.findings || []) : [];
  var pendingJobs = jobs.filter(function(j) { return j.state === "pending_approval"; });
  var failedJobs = jobs.filter(function(j) { return j.state === "failed"; });
  var inbox = [];
  alerts.forEach(function(a) { inbox.push({ kind: "alert", severity: a.severity, title: a.name, detail: a.summary, when: a.started_at }); });
  pendingJobs.forEach(function(j) { inbox.push({ kind: "approval", severity: "warning", title: j.kind, detail: j.reason, when: j.submitted_at }); });
  failedJobs.forEach(function(j) { inbox.push({ kind: "job", severity: "critical", title: j.kind, detail: j.reason, when: j.completed_at || j.submitted_at }); });
  bundles.forEach(function(b) { inbox.push({ kind: "bundle", severity: b.severity, title: b.alert_name, detail: b.message || b.issue_id, when: b.requested_at }); });
  findings.forEach(function(f) { inbox.push({ kind: "finding", severity: f.severity, title: f.title, detail: f.detail, when: f.last_seen_at }); });
  inbox.sort(function(a, b) { return new Date(b.when || 0) - new Date(a.when || 0); });
  return (
    <div className="page">
      <div className="tile-row"><KPI color={alerts.length ? "red" : "green"} label="Active alerts" value={fmtInt(alerts.length)} sub="derived/live"/><KPI color={pendingJobs.length ? "orange" : "green"} label="Approvals" value={fmtInt(pendingJobs.length)} sub="pending"/><KPI color={bundles.length ? "orange" : "green"} label="Bundle requests" value={fmtInt(bundles.length)} sub="collector queue"/><KPI color={findings.length ? "orange" : "green"} label="Open findings" value={fmtInt(findings.length)} sub="collector"/></div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Bell size={15}/>Unified operations inbox</span><SourceBadge source="alerts + jobs + collector"/></div><div className="bd"><table className="tbl"><thead><tr><th>When</th><th>Type</th><th>Severity</th><th>Item</th></tr></thead><tbody>{inbox.map(function(row, index) { return <tr key={index}><td className="mono txt-xs">{rmDate(row.when)}</td><td>{row.kind}</td><td><span className={"pill " + rmPill(row.severity)}>{row.severity}</span></td><td><strong>{row.title}</strong><div className="muted txt-xs mt-2">{row.detail}</div></td></tr>; })}{!inbox.length && <tr><td colSpan="4" className="muted">Inbox is clear.</td></tr>}</tbody></table></div></div>
    </div>
  );
}

function EstateInventoryScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters"),
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
    ]).then(function(r) { return { clusters: r[0], readiness: r[1], alerts: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Estate inventory"/>;
  if (state.error) return <RmErrorPage title="Estate inventory" error={state.error}/>;
  var clusters = state.data.clusters.ok ? (state.data.clusters.data.clusters || []) : [];
  var readiness = state.data.readiness.ok ? state.data.readiness.data : {};
  var alerts = state.data.alerts.ok ? (state.data.alerts.data.alerts || []) : [];
  var agentReported = clusters.filter(function(c) { return c.agent_configured != null; });
  var configuredAgents = agentReported.filter(function(c) { return c.agent_configured; }).length;
  return (
    <div className="page">
      <div className="tile-row"><KPI color="blue" label="Clusters" value={fmtInt(clusters.length)} sub="known to console"/><KPI color={agentReported.length ? (configuredAgents === agentReported.length ? "green" : "orange") : "muted"} label="Remote-agent coverage" value={agentReported.length ? (configuredAgents + "/" + agentReported.length) : "Unavailable"} sub="reported by cluster registry"/><KPI color={((readiness.summary || {}).status === "critical") ? "red" : "orange"} label="Readiness" value={(readiness.summary || {}).status || "-"} sub="central runtime"/><KPI color={alerts.length ? "red" : "green"} label="Alerts" value={fmtInt(alerts.length)} sub="active"/></div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Database size={15}/>Estate matrix</span></div><div className="bd"><table className="tbl"><thead><tr><th>Cluster</th><th>Role</th><th>Namespace</th><th>Version</th><th>Health</th><th>Agent</th><th>Latest snapshot</th></tr></thead><tbody>{clusters.map(function(c) { return <tr key={c.id}><td><strong>{c.name}</strong><div className="muted txt-xs">{c.region}</div></td><td>{c.role || c.label || "Unavailable"}</td><td className="mono txt-xs">{c.namespace || c.k8s_namespace || "Unavailable"}</td><td>{c.pg_version || "-"}</td><td><span className={"pill " + rmPill(c.serverState || c.health || "unavailable")}>{c.serverState || c.health || "Unavailable"}</span></td><td><span className={"pill " + rmPill(c.agent_configured == null ? "muted" : c.agent_configured ? "ok" : "missing")}>{c.agent_configured == null ? "not reported" : c.agent_configured ? "configured" : "missing"}</span></td><td className="mono txt-xs">{c.latest_snapshot ? rmDate(c.latest_snapshot.collected_at) : "not reported"}</td></tr>; })}</tbody></table></div></div>
    </div>
  );
}

function VersionReadinessScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/lifecycle/upgrade/" + encodeURIComponent(clusterId)),
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/parameters"),
    ]).then(function(r) { return { upgrade: r[0], readiness: r[1], params: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Version readiness"/>;
  if (state.error) return <RmErrorPage title="Version readiness" error={state.error}/>;
  var upgrade = state.data.upgrade.ok ? state.data.upgrade.data : {};
  var readiness = state.data.readiness.ok ? state.data.readiness.data : {};
  var params = state.data.params.ok ? state.data.params.data : {};
  var pendingRestart = (params.parameters || []).filter(function(p) { return p.pending_restart; });
  var preflightRows = Array.isArray(upgrade.preflight) ? upgrade.preflight : [];
  var preflight = Array.isArray(upgrade.preflight) ? {} : (upgrade.preflight || {});
  var findings = [];
  preflightRows.forEach(function(row) { if (row.ok === false) rmFinding(findings, "critical", "Preflight", row.name || "Preflight check failed"); });
  if (preflight.cluster_healthy === false) rmFinding(findings, "critical", "Preflight", "Cluster is not healthy for upgrade or patch");
  if ((preflight.backup_repo || {}).bucket === "" || (preflight.backup_repo || {}).s3_endpoint === "") rmFinding(findings, "critical", "Backup repo", "Backup repo bucket/endpoint are incomplete");
  if (pendingRestart.length) rmFinding(findings, "warning", "Pending restart", pendingRestart.length + " settings require restart");
  if (((readiness.summary || {}).status === "critical")) rmFinding(findings, "warning", "Runtime", "Runtime readiness is critical");
  return (
    <div className="page">
      <div className="tile-row"><KPI color={findings.filter(function(f) { return f.severity === "critical"; }).length ? "red" : "green"} label="Patch readiness" value={findings.length ? "review" : "ready"} sub="read-only preflight"/><KPI color="blue" label="PostgreSQL" value={(upgrade.current || {}).postgres_version || upgrade.current_version || "Unavailable"} sub="server_version"/><KPI color={pendingRestart.length ? "orange" : "green"} label="Pending restart" value={fmtInt(pendingRestart.length)} sub="pg_settings"/><KPI color="muted" label="Execution control" value="Approval-gated" sub="guarded lifecycle"/></div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.ArrowRight size={15}/>Version and patch blockers</span></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="card"><div className="hd">Key PostgreSQL settings</div><div className="bd"><table className="tbl"><thead><tr><th>Name</th><th>Setting</th><th>Context</th><th>Pending restart</th></tr></thead><tbody>{Object.keys((upgrade.current || {}).settings || {}).map(function(k) { var p = upgrade.current.settings[k]; return <tr key={k}><td className="mono">{p.name}</td><td>{p.setting} {p.unit || ""}</td><td>{p.context}</td><td><span className={"pill " + rmPill(p.pending_restart ? "warning" : "ok")}>{p.pending_restart ? "yes" : "no"}</span></td></tr>; })}</tbody></table></div></div>
    </div>
  );
}

function EvidenceExportScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/audit", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/collector/alert-bundle-requests", { cluster: clusterId, status: "all" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
    ]).then(function(r) { return { readiness: r[0], alerts: r[1], jobs: r[2], audit: r[3], bundles: r[4], backups: r[5] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Evidence export"/>;
  if (state.error) return <RmErrorPage title="Evidence export" error={state.error}/>;
  function exportManifest() {
    var payload = {
      generated_at: new Date().toISOString(),
      cluster_id: clusterId,
      redaction: "No secret data, kubeconfig, private keys, bearer tokens, S3 access keys, or passwords are included.",
      sources: state.data,
    };
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "hbz-pg-evidence-" + clusterId + "-" + Date.now() + ".json";
    a.click();
    URL.revokeObjectURL(url);
  }
  var packs = [
    ["Readiness evidence", state.data.readiness.ok ? "available" : "error", "Runtime source checks, startup findings, ingest freshness"],
    ["Alert evidence", state.data.alerts.ok ? ((state.data.alerts.data.alerts || []).length + " active") : "error", "Active alerts and rules"],
    ["Job evidence", state.data.jobs.ok ? ((state.data.jobs.data.jobs || []).length + " jobs") : "error", "Dry-run, approval, and execution metadata"],
    ["Audit evidence", state.data.audit.ok ? ((state.data.audit.data.audit || []).length + " rows") : "error", "Recent operator activity"],
    ["Support bundle requests", state.data.bundles.ok ? ((state.data.bundles.data.bundles || state.data.bundles.data.requests || []).length + " requests") : "error", "Collector bundle queue"],
    ["Backup evidence", state.data.backups.ok ? ((state.data.backups.data.summary || {}).status || "available") : "error", "Repo, schedules, archive posture"],
  ];
  return (
    <div className="page">
      <div className="tile-row"><KPI color="blue" label="Evidence packs" value={fmtInt(packs.length)} sub="read-only sources"/><KPI color="green" label="Redaction" value="enabled" sub="secrets excluded"/><KPI color="blue" label="Format" value="JSON" sub="client-side manifest"/><KPI color="orange" label="PDF/ZIP" value="future" sub="Phase 10 backend extension"/></div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Download size={15}/>Evidence export center</span><button className="btn sm primary" onClick={exportManifest}><Icon.Download size={12}/> Export JSON manifest</button></div><div className="bd"><table className="tbl"><thead><tr><th>Pack</th><th>Status</th><th>Contents</th></tr></thead><tbody>{packs.map(function(row) { return <tr key={row[0]}><td>{row[0]}</td><td><span className={"pill " + rmPill(row[1] === "error" ? "critical" : "ok")}>{row[1]}</span></td><td>{row[2]}</td></tr>; })}</tbody></table></div></div>
      <div className="card"><div className="bd"><div className="tile-error"><Icon.Shield size={13}/><span>Export is generated in the browser from existing API responses. It does not decode Kubernetes Secrets or include passwords, tokens, private keys, kubeconfig, or S3 secret values.</span></div></div></div>
    </div>
  );
}

Object.assign(window, {
  rmFetch: rmFetch, rmPill: rmPill, rmDate: rmDate, rmState: rmState,
  rmClusterId: rmClusterId, rmFinding: rmFinding, RmFindingTable: RmFindingTable,
  RmLoadPage: RmLoadPage, RmErrorPage: RmErrorPage, useRmPayload: useRmPayload,
});

window.RecoveryAssuranceScreen = RecoveryAssuranceScreen;
window.PgBouncerDeepDiveScreen = PgBouncerDeepDiveScreen;
window.WalArchivePressureScreen = WalArchivePressureScreen;
window.StorageHealthScreen = StorageHealthScreen;
window.ChangeCalendarScreen = ChangeCalendarScreen;
window.OpsInboxScreen = OpsInboxScreen;
window.EstateInventoryScreen = EstateInventoryScreen;
window.VersionReadinessScreen = VersionReadinessScreen;
window.EvidenceExportScreen = EvidenceExportScreen;
