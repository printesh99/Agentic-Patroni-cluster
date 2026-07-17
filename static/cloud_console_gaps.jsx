// Cloud Console Gap Modules (CC-1 .. CC-15).
//
// Azure PostgreSQL Flexible Server / AWS Aurora style console capabilities layered
// on top of the existing HBZ DBA console. Every screen is READ-ONLY: it aggregates
// existing read-only console APIs, renders explicit "unknown / not collected /
// evidence unavailable" states when data is missing, and never triggers a write
// path or displays secret/endpoint values. Guarded dry-run/approval flows in the
// existing screens remain the only way to make changes.
//
// Shared helpers (rmFetch, useRmPayload, rmPill, rmDate, rmFinding, RmFindingTable,
// RmLoadPage, RmErrorPage, rmClusterId) live in remaining_phases.jsx and are global
// in this multi-script architecture. UI primitives (KPI, Stat, SourceBadge,
// EmptyState, BarList, DonutChart, fmtBytes, fmtInt, fmtSec, Icon) are global too.

/* ===================== Local helpers ===================== */

// Redact anything that looks like an endpoint / IP / bucket / secret so cloud
// console panes can show a path WITHOUT leaking sensitive values (AGENTS.md §12).
function ccRedact(value) {
  if (value === null || value === undefined || value === "") return "—";
  var s = String(value);
  // IPv4 / host:port / S3 endpoints / bucket-looking tokens -> symbolic label.
  if (/^\d{1,3}(\.\d{1,3}){3}/.test(s)) return "‹redacted-ip›";
  if (/(amazonaws|s3|noobaa|openshift-storage|\.svc|https?:\/\/)/i.test(s)) return "‹redacted-endpoint›";
  if (/(secret|key|token|password|cipher)/i.test(s)) return "‹redacted-secret›";
  return s;
}

// Presence-only signal: is a sensitive value configured? (true/false, never shown).
function ccPresent(value) {
  return value !== null && value !== undefined && String(value).trim() !== "";
}

// The /auth and /tls endpoints return each pg_settings entry as an OBJECT
// ({name, setting, ...}), not a scalar. Rendering such an object as a React
// child crashes. ccNormSettings() flattens a settings map to scalar values so
// `settings.ssl` etc. are always safe to render and compare.
function ccSettingVal(v) {
  if (v && typeof v === "object") return v.setting !== undefined ? v.setting : "";
  return v;
}
function ccNormSettings(obj) {
  var out = {};
  Object.keys(obj || {}).forEach(function(k) { out[k] = ccSettingVal(obj[k]); });
  return out;
}

function ccScoreTone(score) {
  if (score == null) return "muted";
  if (score >= 85) return "green";
  if (score >= 65) return "orange";
  return "red";
}

function ccSevWeight(sev) {
  var s = String(sev || "").toLowerCase();
  if (s === "critical" || s === "blocked") return 25;
  if (s === "warning" || s === "warn" || s === "caution" || s === "stale") return 10;
  if (s === "info") return 3;
  return 0;
}

// Build an advisor-style score (100 minus weighted findings, floored at 0).
function ccScoreFromFindings(findings) {
  var penalty = (findings || []).reduce(function(sum, f) { return sum + ccSevWeight(f.severity); }, 0);
  return Math.max(0, 100 - penalty);
}

function CcScoreCard({ label, score, sub }) {
  var tone = ccScoreTone(score);
  var color = tone === "green" ? "var(--ok)" : tone === "orange" ? "var(--warn)" : tone === "red" ? "var(--danger)" : "var(--fg-dim)";
  return (
    <div className="card stat" style={{minWidth: 150}}>
      <div className="lbl">{label}</div>
      <div className="val" style={{color: color}}>{score == null ? "unknown" : score}{score == null ? "" : <span className="unit">/100</span>}</div>
      {sub && <div className="muted txt-xs">{sub}</div>}
    </div>
  );
}

// Severity/category recommendation table shared by the advisor + posture screens.
function CcRecoTable({ rows, emptyHint }) {
  var list = rows || [];
  if (!list.length) return <EmptyState icon={Icon.CheckCircle} title="No recommendations" hint={emptyHint || "No action items were derived from the available evidence."}/>;
  return (
    <table className="tbl">
      <thead><tr><th>Severity</th><th>Category</th><th>Recommendation</th><th>Evidence</th><th>Next safe step</th></tr></thead>
      <tbody>
        {list.map(function(row, index) {
          return (
            <tr key={index}>
              <td><span className={"pill " + rmPill(row.severity)}><span className="dot"/>{row.severity}</span></td>
              <td>{row.category}</td>
              <td><strong>{row.title}</strong>{row.impact ? <div className="muted txt-xs mt-2">Impact: {row.impact}</div> : null}</td>
              <td className="txt-xs muted">{row.source || "unknown"}</td>
              <td className="txt-xs">{row.next || "Review in target screen"}{row.target ? <div className="muted txt-xs">→ {row.target}</div> : null}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function CcSafetyNote({ text }) {
  return (
    <div className="card"><div className="bd"><div className="tile-error"><Icon.Shield size={13}/><span>{text}</span></div></div></div>
  );
}

function ccNum(value) { return Number(value || 0); }

/* ===================== CC-1 Cloud Advisor / Recommendation Center ===================== */
function CloudAdvisorScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/advisor/parameters"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/index-advisor"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/bloat"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/findings", { status: "all" }),
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
    ]).then(function(r) { return { readiness: r[0], backups: r[1], advisor: r[2], indexes: r[3], bloat: r[4], findings: r[5], alerts: r[6] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Cloud advisor"/>;
  if (state.error) return <RmErrorPage title="Cloud advisor" error={state.error}/>;

  var readiness = state.data.readiness.ok ? state.data.readiness.data : {};
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var advisor = state.data.advisor.ok ? state.data.advisor.data : {};
  var indexes = state.data.indexes.ok ? state.data.indexes.data : {};
  var bloat = state.data.bloat.ok ? state.data.bloat.data : {};
  var findings = state.data.findings.ok ? (state.data.findings.data.findings || []) : [];
  var alerts = state.data.alerts.ok ? (state.data.alerts.data.alerts || []) : [];

  var recos = [];
  function add(sev, cat, title, source, impact, next, target) {
    recos.push({ severity: sev, category: cat, title: title, source: source, impact: impact, next: next, target: target });
  }
  // Availability / DR
  (readiness.items || readiness.checks || []).forEach(function(c) {
    if (c.status && c.status !== "ok") add(c.status === "critical" ? "critical" : "warning", "availability", c.label + ": " + (c.detail || "degraded"), c.source || "readiness", "Runtime/HA readiness gap", "Open Environment Readiness", "Operations · Environment Readiness");
  });
  var settings = backups.settings || {};
  var repo = backups.repo || {};
  if (settings.archive_mode !== "on") add("critical", "backup/DR", "WAL archive_mode is not on", "backups-api", "PITR and rebuild evidence missing", "Open Recovery Assurance", "DR & Cutover · Recovery Assurance");
  if (!ccPresent(repo.bucket) || !ccPresent(repo.s3_endpoint)) add("critical", "backup/DR", "pgBackRest object-storage config incomplete", "backups-api", "Backups may not be offsite", "Open Backups", "Operations · Backups");
  // Performance
  var advParams = advisor.recommendations || advisor.parameters || [];
  advParams.slice(0, 5).forEach(function(p) { add("warning", "performance", "Parameter tuning: " + (p.name || p.parameter || "setting"), "advisor/parameters", p.rationale || p.reason || "Tuning candidate", "Validate in Advisor", "Advisor & Health · Advisor"); });
  var unused = (indexes.indexes || indexes.recommendations || []).filter(function(i) { return i.unused || i.recommendation === "drop"; });
  if (unused.length) add("warning", "performance", unused.length + " unused index candidate(s)", "pg_stat_user_indexes", "Write amplification + bloat", "Review Index Advisor", "Performance Insights · Index Advisor");
  var bloatRows = bloat.tables || bloat.rows || [];
  if (bloatRows.length) add("info", "performance", bloatRows.length + " table(s) with dead-tuple pressure", "object-metrics", "Bloat / vacuum pressure", "Review Bloat", "Performance Insights · Bloat");
  // Collector findings + alerts
  findings.forEach(function(f) { add(f.severity || "info", "operations", f.title || "Collector finding", "collector", f.detail || "", "Open Ops Inbox", "Operations · Ops Inbox"); });
  alerts.forEach(function(a) { add(a.severity || "warning", "operations", a.name || "Active alert", "alerts", a.summary || "", "Open Alerts", "Monitoring · Alerts"); });

  var score = ccScoreFromFindings(recos);
  var byCat = {};
  recos.forEach(function(r) { byCat[r.category] = (byCat[r.category] || 0) + 1; });
  var catRows = Object.keys(byCat).map(function(k) { return { label: k, value: byCat[k] }; });
  var criticals = recos.filter(function(r) { return r.severity === "critical"; }).length;

  return (
    <div className="page">
      <div className="tile-row">
        <CcScoreCard label="Advisor score" score={score} sub="100 − weighted findings"/>
        <KPI color={criticals ? "red" : "green"} label="Critical items" value={fmtInt(criticals)} sub="must-fix"/>
        <KPI color={recos.length ? "orange" : "green"} label="Total recommendations" value={fmtInt(recos.length)} sub="all categories"/>
        <KPI color="blue" label="Categories" value={fmtInt(catRows.length)} sub="availability · DR · perf · ops"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd"><span className="flex-row"><Icon.CheckCircle size={15}/>Recommendation breakdown</span></div><div className="bd"><BarList rows={catRows} valueFormatter={fmtInt} emptyText="No recommendations by category."/></div></div>
        <div className="card"><div className="hd">Score inputs</div><div className="bd"><div className="grid-2">
          <Stat label="Readiness" value={(readiness.summary || {}).status || "unknown"} sub="runtime checks"/>
          <Stat label="archive_mode" value={settings.archive_mode || "unknown"} sub="WAL coverage"/>
          <Stat label="S3 repo config" value={(ccPresent(repo.bucket) && ccPresent(repo.s3_endpoint)) ? "present" : "incomplete"} sub="presence only (not shown)"/>
          <Stat label="Active alerts" value={fmtInt(alerts.length)} sub="derived/live"/>
        </div></div></div>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.CheckCircle size={15}/>Consolidated recommendations</span><SourceBadge source="readiness + backups + advisor + collector"/></div><div className="bd"><CcRecoTable rows={recos} emptyHint="Estate is healthy across the evidence the console can see."/></div></div>
      <CcSafetyNote text="The advisor never triggers changes. Each item points to the existing guarded dry-run / approval screen. Secret and endpoint values are checked for presence only and never displayed."/>
    </div>
  );
}

/* ===================== CC-2 Query Store / DB Load Timeline ===================== */
function DbLoadTimelineScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/topsql"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/waits"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/application-activity"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/topsql/history", { limit: 50 }),
    ]).then(function(r) { return { topsql: r[0], waits: r[1], activity: r[2], history: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="DB load timeline"/>;
  if (state.error) return <RmErrorPage title="DB load timeline" error={state.error}/>;

  var topsql = state.data.topsql.ok ? state.data.topsql.data : {};
  var waits = state.data.waits.ok ? state.data.waits.data : {};
  var activity = state.data.activity.ok ? state.data.activity.data : {};
  var history = state.data.history.ok ? state.data.history.data : {};

  var sqlRows = topsql.top_sql || topsql.statements || topsql.rows || [];
  var waitRows = (waits.waits || waits.rows || []).map(function(w) { return { label: (w.wait_event_type || "—") + " / " + (w.wait_event || "—"), value: ccNum(w.count || w.sessions || w.value) }; });
  var summary = activity.summary || {};
  var hist = history.captures || history.history || history.rows || [];
  var loadRows = sqlRows.slice(0, 8).map(function(s) { return { label: (s.query || s.query_text || "query").slice(0, 60), value: ccNum(s.total_time_ms || s.total_exec_time || s.calls || s.value) }; });

  var findings = [];
  if (!sqlRows.length) rmFinding(findings, "info", "Query store", "No pg_stat_statements evidence available locally", "DB load timeline degrades to wait/activity evidence only.");
  if (!waitRows.length) rmFinding(findings, "info", "Wait events", "No wait-event evidence available locally");
  if (!hist.length) rmFinding(findings, "info", "Regression", "No Top SQL history captured yet, regression detection unavailable");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Top SQL tracked" value={fmtInt(sqlRows.length)} sub={topsql.source || "pg_stat_statements"}/>
        <KPI color="blue" label="Wait classes" value={fmtInt(waitRows.length)} sub="current pressure"/>
        <KPI color="blue" label="Active sessions" value={fmtInt(summary.active_sessions || summary.total_sessions || 0)} sub="pg_stat_activity"/>
        <KPI color={hist.length ? "green" : "orange"} label="History captures" value={fmtInt(hist.length)} sub="regression window"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.TrendingUp size={15}/>DB load notes</span></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">DB load by SQL (proxy)</div><div className="bd"><BarList rows={loadRows} valueFormatter={fmtInt} emptyText="No Top SQL load to chart."/></div></div>
        <div className="card"><div className="hd">DB load by wait event</div><div className="bd"><BarList rows={waitRows} valueFormatter={fmtInt} emptyText="No wait-event pressure to chart."/></div></div>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.FileText size={15}/>Top SQL trend</span><SourceBadge source={topsql.source || "pg_stat_statements"}/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Query</th><th className="num">Calls</th><th className="num">Total ms</th><th className="num">Mean ms</th><th>Drill</th></tr></thead><tbody>
          {sqlRows.slice(0, 12).map(function(s, i) { return <tr key={i}><td className="mono txt-xs" title={s.query || s.query_text}>{String(s.query || s.query_text || "—").slice(0, 70)}</td><td className="num">{fmtInt(s.calls)}</td><td className="num">{fmtInt(Math.round(s.total_time_ms || s.total_exec_time || 0))}</td><td className="num">{(s.mean_time_ms || s.mean_exec_time || 0).toFixed ? (s.mean_time_ms || s.mean_exec_time || 0).toFixed(2) : "—"}</td><td className="txt-xs muted">Top SQL · Plan Cache · Slow Queries</td></tr>; })}
          {!sqlRows.length && <tr><td colSpan="5" className="muted">No Top SQL evidence. Drill into Top SQL, Wait Events, Plan Cache, Slow Queries, and Metrics Explorer.</td></tr>}
        </tbody></table>
      </div></div>
    </div>
  );
}

/* ===================== CC-3 Advanced PgBouncer Diagnostics ===================== */
function PgBouncerAdvancedScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/ui/cluster"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/application-activity"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/sessions"),
    ]).then(function(r) { return { cluster: r[0], activity: r[1], sessions: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="PgBouncer diagnostics"/>;
  if (state.error) return <RmErrorPage title="PgBouncer diagnostics" error={state.error}/>;
  var ui = state.data.cluster.ok ? state.data.cluster.data : {};
  var pgb = ui.pgbouncer || {};
  var activity = state.data.activity.ok ? state.data.activity.data : {};
  var summary = activity.summary || {};
  var dbRows = activity.database_breakdown || activity.db_breakdown || [];
  var userRows = activity.user_breakdown || [];
  var sourceRows = activity.source_breakdown || [];

  var podsReady = ccNum(pgb.pods_ready), podsTotal = ccNum(pgb.pods_total);
  var waiting = ccNum(summary.waiting_sessions || summary.idle_in_transaction_sessions);
  var active = ccNum(summary.active_sessions);
  var idle = ccNum(summary.idle_sessions);
  var serverRatio = (active + idle) ? Math.round((active / (active + idle)) * 100) : null;

  var findings = [];
  if (podsTotal && podsReady < podsTotal) rmFinding(findings, "critical", "Pods", "Not all PgBouncer pods are ready", podsReady + "/" + podsTotal);
  if (!ccNum(summary.pgbouncer_sessions)) rmFinding(findings, "warning", "Routing", "No PgBouncer-sourced sessions detected", "Normal for local Docker; production should show pooled sessions.");
  if (waiting > 0) rmFinding(findings, "warning", "Saturation", "Waiting/idle-in-transaction client pressure present", "Waiting clients: " + waiting);
  if (ccNum(summary.direct_sessions) > ccNum(summary.pgbouncer_sessions)) rmFinding(findings, "warning", "Routing", "Direct sessions exceed pooled sessions");
  // Pinning-style hints
  var pinHints = [];
  if (ccNum(summary.idle_in_transaction_sessions) > 0) pinHints.push({ severity: "warning", component: "Pinning risk", summary: "idle-in-transaction sessions can pin server connections in transaction pooling", detail: "Count: " + summary.idle_in_transaction_sessions });
  if (ccNum(summary.prepared_statement_sessions) > 0) pinHints.push({ severity: "info", component: "Pinning risk", summary: "Prepared statements may be incompatible with transaction pooling", detail: "" });
  if (!pinHints.length) pinHints.push({ severity: "info", component: "Pinning risk", summary: "No pinning evidence collected locally", detail: "Long transactions, session state, and prepared statements would surface here." });

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={podsTotal && podsReady === podsTotal ? "green" : podsTotal ? "red" : "blue"} label="PgBouncer pods" value={podsTotal ? podsReady + "/" + podsTotal : "unknown"} sub="ready/total"/>
        <KPI color="blue" label="Pooled sessions" value={fmtInt(summary.pgbouncer_sessions || 0)} sub="inferred"/>
        <KPI color={waiting ? "orange" : "green"} label="Waiting clients" value={fmtInt(waiting)} sub="pool pressure"/>
        <KPI color="blue" label="Active/idle ratio" value={serverRatio == null ? "unknown" : serverRatio + "%"} sub="server utilization"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Users size={15}/>Pool diagnostics</span><SourceBadge source="pg_stat_activity + cluster metadata"/></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">SHOW POOLS (per-database)</div><div className="bd">
          <table className="tbl"><thead><tr><th>Database</th><th className="num">Sessions</th><th className="num">Active</th><th className="num">Idle</th></tr></thead><tbody>
            {dbRows.slice(0, 10).map(function(row, i) { return <tr key={i}><td>{row.datname || row.database || "—"}</td><td className="num">{fmtInt(row.sessions)}</td><td className="num">{fmtInt(row.active)}</td><td className="num">{fmtInt(row.idle)}</td></tr>; })}
            {!dbRows.length && <tr><td colSpan="4" className="muted">Pool evidence not collected.</td></tr>}
          </tbody></table>
        </div></div>
        <div className="card"><div className="hd">Per-user saturation</div><div className="bd">
          <table className="tbl"><thead><tr><th>User</th><th className="num">Sessions</th><th className="num">Active</th><th>Source</th></tr></thead><tbody>
            {userRows.slice(0, 10).map(function(row, i) { return <tr key={i}><td>{row.usename || row.user || "—"}</td><td className="num">{fmtInt(row.sessions)}</td><td className="num">{fmtInt(row.active)}</td><td className="txt-xs">{(sourceRows[0] && sourceRows[0].connection_source) || "—"}</td></tr>; })}
            {!userRows.length && <tr><td colSpan="4" className="muted">Per-user evidence not collected.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.AlertTriangle size={15}/>Pinning &amp; pooling-mode hints</span></div><div className="bd"><RmFindingTable rows={pinHints}/></div></div>
      <CcSafetyNote text="PgBouncer auth files, user secrets, and passwords are never read or displayed. Missing data renders as 'evidence not collected'."/>
    </div>
  );
}

/* ===================== CC-4 Earliest / Latest Restorable Time ===================== */
function RestoreWindowScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/cutover/runs", { limit: 10 }),
      rmFetch("/api/v1/readiness"),
    ]).then(function(r) { return { backups: r[0], runs: r[1], readiness: r[2] }; });
  });
  var targetKind = React.useState("timestamp");
  var targetVal = React.useState("");
  var kind = targetKind[0], setKind = targetKind[1];
  var val = targetVal[0], setVal = targetVal[1];

  if (state.loading && !state.data) return <RmLoadPage title="Restore window"/>;
  if (state.error) return <RmErrorPage title="Restore window" error={state.error}/>;
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var archive = backups.archive || {};
  var summary = backups.summary || {};
  var history = backups.history || [];
  var settings = backups.settings || {};

  var earliest = backups.earliest_restore || archive.oldest_backup_time || summary.oldest_backup || null;
  var latest = archive.last_archived_time || backups.latest_restore || null;
  var rpo = archive.rpo_seconds != null ? archive.rpo_seconds : (archive.archive_lag_sec != null ? archive.archive_lag_sec : null);

  // Client-side PITR target validation shell only (no execution).
  var validation = (function() {
    if (!val) return { tone: "muted", msg: "Enter a target to preview validation (read-only)." };
    if (kind === "timestamp") {
      var t = Date.parse(val);
      if (isNaN(t)) return { tone: "danger", msg: "Not a parseable timestamp." };
      if (earliest && t < Date.parse(earliest)) return { tone: "danger", msg: "Target precedes earliest restorable time." };
      if (latest && t > Date.parse(latest)) return { tone: "warn", msg: "Target is after latest recoverable WAL time." };
      return { tone: "ok", msg: "Target is within the apparent restore window (preflight only)." };
    }
    if (kind === "lsn") return { tone: /^[0-9A-Fa-f]+\/[0-9A-Fa-f]+$/.test(val) ? "ok" : "danger", msg: /^[0-9A-Fa-f]+\/[0-9A-Fa-f]+$/.test(val) ? "LSN format looks valid (preflight only)." : "LSN must look like X/Y hex." };
    if (kind === "xid") return { tone: /^\d+$/.test(val) ? "ok" : "danger", msg: /^\d+$/.test(val) ? "XID format looks valid (preflight only)." : "XID must be numeric." };
    return { tone: val.trim() ? "ok" : "danger", msg: val.trim() ? "Named restore point provided (preflight only)." : "Provide a restore point name." };
  })();

  var findings = [];
  if (settings.archive_mode !== "on") rmFinding(findings, "critical", "Archive", "archive_mode is not on, PITR window is not guaranteed");
  if (!latest) rmFinding(findings, "warning", "WAL", "Latest recoverable WAL time is unknown");
  if (!history.length) rmFinding(findings, "warning", "Restore drill", "No restore drill history, stale-drill risk");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={earliest ? "green" : "orange"} label="Earliest restorable" value={earliest ? rmDate(earliest) : "unknown"} sub="repo coverage"/>
        <KPI color={latest ? "green" : "orange"} label="Latest recoverable" value={latest ? rmDate(latest) : "unknown"} sub="last archived WAL"/>
        <KPI color={rpo == null ? "orange" : ccNum(rpo) > 300 ? "red" : "green"} label="Current RPO" value={rpo == null ? "unknown" : fmtSec(ccNum(rpo))} sub="archive lag estimate"/>
        <KPI color={history.length ? "green" : "orange"} label="Restore drills" value={fmtInt(history.length)} sub="validation history"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.HardDrive size={15}/>Restore window blockers</span><SourceBadge source="pgBackRest + WAL archive"/></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">PITR target validation (preflight)</div><div className="bd">
          <div className="flex-row" style={{gap: 8, flexWrap: "wrap", marginBottom: 8}}>
            <select className="cluster-select" value={kind} onChange={function(e) { setKind(e.target.value); }} aria-label="Target kind" style={{maxWidth: 180}}>
              <option value="timestamp">Timestamp</option>
              <option value="lsn">LSN</option>
              <option value="xid">XID</option>
              <option value="name">Named restore point</option>
            </select>
            <input className="cluster-select" style={{flex: 1, minWidth: 180}} placeholder={kind === "timestamp" ? "2026-06-14 01:00:00Z" : kind === "lsn" ? "0/16B3748" : kind === "xid" ? "748213" : "rp_before_change"} value={val} onChange={function(e) { setVal(e.target.value); }} aria-label="Target value"/>
          </div>
          <div className={"tile-error"} style={{borderColor: "var(--border)"}}><span className={"pill " + (validation.tone === "ok" ? "ok" : validation.tone === "danger" ? "danger" : validation.tone === "warn" ? "warn" : "muted")}><span className="dot"/>{validation.tone === "ok" ? "valid" : validation.tone}</span><span>{validation.msg}</span></div>
          <div className="muted txt-xs mt-2">Validation is computed in the browser against the apparent restore window. Restore execution stays in the guarded Backups / Recovery flow.</div>
        </div></div>
        <div className="card"><div className="hd">Restore drill history</div><div className="bd">
          <table className="tbl"><thead><tr><th>When</th><th>Kind</th><th>Status</th></tr></thead><tbody>
            {history.slice(0, 10).map(function(h, i) { return <tr key={i}><td className="mono txt-xs">{rmDate(h.completed_at || h.created_at || h.time)}</td><td>{h.kind || h.type || "validation"}</td><td><span className={"pill " + rmPill(h.status || h.state)}>{h.status || h.state || "—"}</span></td></tr>; })}
            {!history.length && <tr><td colSpan="3" className="muted">No restore drills recorded.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
    </div>
  );
}

/* ===================== CC-5 Blue/Green Upgrade Workflow ===================== */
function BlueGreenUpgradeScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/lifecycle/upgrade/" + encodeURIComponent(clusterId)),
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
    ]).then(function(r) { return { upgrade: r[0], readiness: r[1], backups: r[2], jobs: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Blue/green upgrade"/>;
  if (state.error) return <RmErrorPage title="Blue/green upgrade" error={state.error}/>;
  var upgrade = state.data.upgrade.ok ? state.data.upgrade.data : {};
  var readiness = state.data.readiness.ok ? state.data.readiness.data : {};
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var preflight = upgrade.preflight || {};
  var upgradeJobs = jobs.filter(function(j) { return /upgrade|clone|blue|green|rehears/i.test(j.kind || j.target || ""); });

  var settings = backups.settings || {};
  var repo = backups.repo || {};
  var checklist = [
    { item: "Source cluster healthy", ok: preflight.cluster_healthy !== false && ((readiness.summary || {}).status !== "critical") },
    { item: "Backup repo configured (offsite)", ok: ccPresent(repo.bucket) && ccPresent(repo.s3_endpoint) },
    { item: "WAL archive_mode on", ok: settings.archive_mode === "on" },
    { item: "Target version selected", ok: ccPresent(upgrade.target_version || upgrade.available_target) },
    { item: "Guarded execution framework", ok: !!preflight.phase8_execution_enabled },
  ];
  var ready = checklist.every(function(c) { return c.ok; });

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Source version" value={upgrade.current_version || "unknown"} sub={cluster.name}/>
        <KPI color="blue" label="Target version" value={upgrade.target_version || upgrade.available_target || "unselected"} sub="rehearsal target"/>
        <KPI color={ready ? "green" : "orange"} label="Cutover readiness" value={ready ? "ready" : "review"} sub="preflight checklist"/>
        <KPI color={upgradeJobs.length ? "blue" : "muted"} label="Rehearsal jobs" value={fmtInt(upgradeJobs.length)} sub="dry-run / approval"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Blue/green workflow</div><div className="bd">
          <table className="tbl"><thead><tr><th>Stage</th><th>Environment</th><th>Notes</th></tr></thead><tbody>
            <tr><td><strong>Blue (current)</strong></td><td className="mono txt-xs">{cluster.name} · DC1</td><td>Production read path · {upgrade.current_version || "unknown"}</td></tr>
            <tr><td><strong>Green (rehearsal)</strong></td><td className="mono txt-xs">clone / standby · DC2 candidate</td><td>Validation environment · {upgrade.target_version || upgrade.available_target || "target unselected"}</td></tr>
            <tr><td>Cutover</td><td>4-eyes approval</td><td>Switch traffic only via guarded cutover job</td></tr>
            <tr><td>Rollback</td><td>Blue retained</td><td>Keep blue until green validated; failback documented in cutover runbook</td></tr>
          </tbody></table>
          <div className="muted txt-xs mt-2">DC1/DC2 and application impact: traffic stays on Blue/DC1 until an approved switchover. Green rehearsal does not affect the application path.</div>
        </div></div>
        <div className="card"><div className="hd">Validation checklist</div><div className="bd">
          <table className="tbl"><thead><tr><th>Check</th><th>Status</th></tr></thead><tbody>
            {checklist.map(function(c, i) { return <tr key={i}><td>{c.item}</td><td><span className={"pill " + (c.ok ? "ok" : "warn")}><span className="dot"/>{c.ok ? "ready" : "review"}</span></td></tr>; })}
          </tbody></table>
        </div></div>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.ArrowRight size={15}/>Rehearsal / upgrade jobs</span><SourceBadge source="lifecycle + jobs"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Submitted</th><th>Kind</th><th>State</th><th>Reason</th></tr></thead><tbody>
          {upgradeJobs.slice(0, 12).map(function(j) { return <tr key={j.id}><td className="mono txt-xs">{rmDate(j.submitted_at)}</td><td>{j.kind}</td><td><span className={"pill " + rmPill(j.state)}>{j.state}</span></td><td>{j.reason}</td></tr>; })}
          {!upgradeJobs.length && <tr><td colSpan="4" className="muted">No rehearsal/upgrade jobs. Create-and-validate runs go through the guarded Lifecycle / Upgrades flow.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="No clone, CR patch, scale, restore, or switchover is triggered here. Blue/green actions use the existing approval / job / audit framework only."/>
    </div>
  );
}

/* ===================== CC-6 Global / Geo Replica Topology ===================== */
function GeoTopologyScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
      rmFetch("/api/v1/readiness"),
    ]).then(function(r) { return { clusters: r[0], topology: r[1], readiness: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Geo topology"/>;
  if (state.error) return <RmErrorPage title="Geo topology" error={state.error}/>;
  var clusters = state.data.clusters.ok ? (state.data.clusters.data.clusters || []) : [];
  var topology = state.data.topology.ok ? state.data.topology.data : {};
  var members = topology.members || topology.nodes || [];
  var ts = topology.summary || {};

  var prod = clusters.filter(function(c) { return c.is_primary || /^prod/i.test(c.id) || c.role === "PROD"; });
  var dr = clusters.filter(function(c) { return /^dr/i.test(c.id) || c.role === "DR"; });
  var uat = clusters.filter(function(c) { return /uat/i.test(c.id) || c.role === "UAT"; });
  var known = prod.length + dr.length + uat.length;
  var unknown = clusters.filter(function(c) { return !c.role && !c.is_primary && !/prod|dr|uat/i.test(c.id); });

  function clusterCard(title, list, tone) {
    return (
      <div className="card"><div className="hd">{title} <span className={"pill " + tone}>{list.length}</span></div><div className="bd">
        <table className="tbl"><thead><tr><th>Cluster</th><th>Health</th><th>Agent</th><th>Snapshot</th></tr></thead><tbody>
          {list.map(function(c) { return <tr key={c.id}><td><strong>{c.name}</strong><div className="muted txt-xs">{c.region || c.k8s_namespace}</div></td><td><span className={"pill " + rmPill(c.health)}>{c.health || "unknown"}</span></td><td><span className={"pill " + rmPill(c.agent_configured ? "ok" : "missing")}>{c.agent_configured ? "configured" : "missing"}</span></td><td className="mono txt-xs">{c.latest_snapshot ? rmDate(c.latest_snapshot.collected_at) : "none"}</td></tr>; })}
          {!list.length && <tr><td colSpan="4" className="muted">None known to console.</td></tr>}
        </tbody></table>
      </div></div>
    );
  }

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="green" label="PROD / DC1" value={fmtInt(prod.length)} sub="primary side"/>
        <KPI color="blue" label="DR / DC2" value={fmtInt(dr.length)} sub="standby side"/>
        <KPI color="muted" label="UAT" value={fmtInt(uat.length)} sub="non-prod"/>
        <KPI color={unknown.length ? "orange" : "green"} label="Unknown role" value={fmtInt(unknown.length)} sub="marked, not guessed"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.GitBranch size={15}/>Active cluster replication ({cluster.name})</span><SourceBadge source="live PostgreSQL + Patroni"/></div><div className="bd">
        <div className="grid-3">
          <Stat label="Members" value={fmtInt(members.length)} sub="Patroni members"/>
          <Stat label="Max lag" value={ts.max_lag_bytes != null ? fmtBytes(ts.max_lag_bytes) : "unknown"} sub="streaming"/>
          <Stat label="Inactive slots" value={ts.inactive_slots != null ? fmtInt(ts.inactive_slots) : "unknown"} sub="WAL retention risk"/>
        </div>
        <table className="tbl mt-2"><thead><tr><th>Member</th><th>Role</th><th>State</th><th className="num">Lag</th><th>Stream/Archive</th></tr></thead><tbody>
          {members.map(function(m, i) { return <tr key={i}><td className="mono txt-xs">{m.name || m.member || "—"}</td><td>{m.role || "—"}</td><td><span className={"pill " + rmPill(m.state)}>{m.state || "unknown"}</span></td><td className="num">{m.lag_bytes != null ? fmtBytes(m.lag_bytes) : "—"}</td><td className="txt-xs">{m.sync_state || m.replication_type || (m.role === "leader" ? "source" : "stream")}</td></tr>; })}
          {!members.length && <tr><td colSpan="5" className="muted">No member evidence for this cluster.</td></tr>}
        </tbody></table>
        <div className="muted txt-xs mt-2">Physical streaming, logical replication, and pgBackRest fallback are distinct evidence paths. Promotion is intentionally not available from this map.</div>
      </div></div>
      <div className="grid-2">{clusterCard("PROD / DC1", prod, "ok")}{clusterCard("DR / DC2", dr, "info")}</div>
      <div className="grid-2">{clusterCard("UAT", uat, "muted")}{clusterCard("Unknown role (verify)", unknown, "warn")}</div>
    </div>
  );
}

/* ===================== CC-7 Maintenance & Patch Feed ===================== */
function MaintenanceFeedScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/maintenance"),
      rmFetch("/api/v1/lifecycle/upgrade/" + encodeURIComponent(clusterId)),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/parameters"),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
    ]).then(function(r) { return { maintenance: r[0], upgrade: r[1], params: r[2], jobs: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Maintenance feed"/>;
  if (state.error) return <RmErrorPage title="Maintenance feed" error={state.error}/>;
  var maintenance = state.data.maintenance.ok ? state.data.maintenance.data : {};
  var upgrade = state.data.upgrade.ok ? state.data.upgrade.data : {};
  var params = state.data.params.ok ? state.data.params.data : {};
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var windows = maintenance.windows || maintenance.maintenance_windows || [];
  var blackouts = maintenance.blackout_windows || maintenance.blackouts || [];
  var pendingRestart = (params.parameters || []).filter(function(p) { return p.pending_restart; });
  var pendingUpgrade = upgrade.update_available || upgrade.minor_update_available || ccPresent(upgrade.target_version);

  var feed = [];
  windows.forEach(function(w) { feed.push({ when: w.start || w.starts_at, sev: "info", kind: "window", title: w.title || "Maintenance window", detail: (w.environment || "") + " " + (w.cluster || "") }); });
  blackouts.forEach(function(b) { feed.push({ when: b.start || b.starts_at, sev: "warning", kind: "blackout", title: b.title || "Blackout window", detail: b.reason || "" }); });
  if (pendingUpgrade) feed.push({ when: null, sev: "warning", kind: "patch", title: "Minor upgrade available: " + (upgrade.target_version || "see Version Readiness"), detail: "current " + (upgrade.current_version || "unknown") });
  if (pendingRestart.length) feed.push({ when: null, sev: "warning", kind: "restart", title: pendingRestart.length + " parameter(s) require restart", detail: "restart-required GUCs pending" });
  feed.sort(function(a, b) { return new Date(b.when || 0) - new Date(a.when || 0); });

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={windows.length ? "green" : "orange"} label="Maintenance windows" value={fmtInt(windows.length)} sub="configured"/>
        <KPI color={blackouts.length ? "orange" : "muted"} label="Blackout windows" value={fmtInt(blackouts.length)} sub="change-frozen"/>
        <KPI color={pendingUpgrade ? "orange" : "green"} label="Pending upgrade" value={pendingUpgrade ? "yes" : "no"} sub="minor patch"/>
        <KPI color={pendingRestart.length ? "orange" : "green"} label="Restart-required" value={fmtInt(pendingRestart.length)} sub="pg_settings"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Clock size={15}/>Maintenance &amp; patch feed</span><SourceBadge source="jobs + audit + maintenance"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>When</th><th>Type</th><th>Severity</th><th>Item</th></tr></thead><tbody>
          {feed.map(function(row, i) { return <tr key={i}><td className="mono txt-xs">{row.when ? rmDate(row.when) : "—"}</td><td>{row.kind}</td><td><span className={"pill " + rmPill(row.sev)}>{row.sev}</span></td><td><strong>{row.title}</strong>{row.detail ? <div className="muted txt-xs">{row.detail}</div> : null}</td></tr>; })}
          {!feed.length && <tr><td colSpan="4" className="muted">No maintenance, patch, or restart items. Add maintenance window metadata to enforce change-window policy.</td></tr>}
        </tbody></table>
      </div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">Runtime / image posture</div><div className="bd"><div className="grid-2">
          <Stat label="PostgreSQL" value={upgrade.current_version || "unknown"} sub="server_version"/>
          <Stat label="Operator/PGO" value={upgrade.pgo_version || upgrade.operator_version || "unknown"} sub="package/channel"/>
          <Stat label="Image tag" value={upgrade.image_tag || "unknown"} sub="digest not shown"/>
          <Stat label="Restart required" value={pendingRestart.length ? "yes" : "no"} sub="application impact on restart"/>
        </div></div></div>
        <div className="card"><div className="hd">Restart-required parameters</div><div className="bd">
          <table className="tbl"><thead><tr><th>Name</th><th>Context</th></tr></thead><tbody>
            {pendingRestart.slice(0, 10).map(function(p, i) { return <tr key={i}><td className="mono">{p.name}</td><td>{p.context || "—"}</td></tr>; })}
            {!pendingRestart.length && <tr><td colSpan="2" className="muted">No restart-required changes pending.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <CcSafetyNote text="Maintenance is not scheduled or executed here. Execution flows through the existing guarded job framework."/>
    </div>
  );
}

/* ===================== CC-8 Cost / Capacity Optimizer ===================== */
function CapacityOptimizerScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/lifecycle/scale/" + encodeURIComponent(clusterId)),
      rmFetch("/api/ui/cluster"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "storage_bytes", range: "30d", agg: "max" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/forecast", { metric: "storage_bytes", range: "30d", horizon_days: 30 }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
    ]).then(function(r) { return { scale: r[0], ui: r[1], storage: r[2], forecast: r[3], backups: r[4] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Capacity optimizer"/>;
  if (state.error) return <RmErrorPage title="Capacity optimizer" error={state.error}/>;
  var scale = state.data.scale.ok ? state.data.scale.data : {};
  var ui = state.data.ui.ok ? state.data.ui.data : {};
  var storage = state.data.storage.ok ? state.data.storage.data : {};
  var forecast = state.data.forecast.ok ? state.data.forecast.data : {};
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var current = scale.current || {};
  var resources = current.resources || {};
  var pg = ui.pg || ui.cluster || {};
  var cpuPct = ccNum(pg.cpu_pct != null ? pg.cpu_pct : cluster.cpu);
  var memPct = ccNum(pg.mem_pct != null ? pg.mem_pct : cluster.mem);
  var conns = ccNum(pg.active_connections != null ? pg.active_connections : cluster.activeConns);
  var maxConns = ccNum(pg.max_connections != null ? pg.max_connections : cluster.maxConns);
  var points = storage.points || [];

  var recos = [];
  function add(sev, cat, title, source, impact, next) { recos.push({ severity: sev, category: cat, title: title, source: source, impact: impact, next: next, target: "DBA review item" }); }
  if (cpuPct > 0 && cpuPct < 20) add("info", "compute", "CPU appears over-provisioned (cost proxy)", "object-metrics", "~" + cpuPct + "% CPU used", "Review downsizing in Lifecycle / Scaling");
  if (memPct > 0 && memPct < 30) add("info", "compute", "Memory appears over-provisioned (cost proxy)", "object-metrics", "~" + memPct + "% memory used", "Review memory request/limit");
  if (cpuPct > 85) add("warning", "compute", "CPU pressure, may be under-provisioned", "object-metrics", "~" + cpuPct + "% CPU used", "Review scale-up in Lifecycle / Scaling");
  if (maxConns && conns / maxConns < 0.1) add("info", "connections", "Connection ceiling far above usage (cost proxy)", "pg_stat_activity", conns + "/" + maxConns + " connections", "Review max_connections / pooling");
  if (!points.length) add("warning", "storage", "Storage growth series unavailable", "object-metrics", "Forecasting limited", "Check collector ingest");
  if (cluster.role === "UAT") add("info", "non-prod", "Non-production stop/start advisory (cost proxy)", "console metadata", "UAT may not need 24x7 compute", "Consider scheduled stop/start");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={cpuPct > 85 ? "red" : cpuPct < 20 && cpuPct > 0 ? "orange" : "green"} label="CPU utilization" value={cpuPct ? cpuPct + "%" : "unknown"} sub="cost proxy"/>
        <KPI color={memPct < 30 && memPct > 0 ? "orange" : "green"} label="Memory utilization" value={memPct ? memPct + "%" : "unknown"} sub="cost proxy"/>
        <KPI color="blue" label="Connections" value={maxConns ? conns + "/" + maxConns : "unknown"} sub="capacity headroom"/>
        <KPI color={points.length ? "green" : "orange"} label="Storage samples" value={fmtInt(points.length)} sub={storage.source_table || "30d series"}/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Sliders size={15}/>Optimization recommendations (DBA review)</span><SourceBadge source="object-metrics + lifecycle"/></div><div className="bd"><CcRecoTable rows={recos} emptyHint="No capacity/cost optimization signals from current evidence."/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">Provisioned resources</div><div className="bd"><div className="grid-2">
          <Stat label="CPU" value={resources.cpu || cluster.compute || "unknown"} sub="request/limit"/>
          <Stat label="Memory" value={resources.memory || "unknown"} sub="request/limit"/>
          <Stat label="Storage" value={resources.storage_gib ? resources.storage_gib + " GiB" : (cluster.totalStorageGiB ? cluster.totalStorageGiB + " GiB" : "unknown")} sub="provisioned"/>
          <Stat label="Backup retention" value={(backups.settings || {}).retention_full ? (backups.settings.retention_full + " full") : "unknown"} sub="growth driver"/>
        </div></div></div>
        <div className="card"><div className="hd">Growth signals</div><div className="bd"><div className="grid-2">
          <Stat label="Storage forecast" value={forecast.source ? "available" : "not available"} sub="30d horizon"/>
          <Stat label="Latest storage" value={points.length ? fmtBytes(points[points.length - 1][1]) : "unknown"} sub="observed"/>
          <Stat label="WAL growth" value={(backups.archive || {}).wal_bytes ? fmtBytes(backups.archive.wal_bytes) : "unknown"} sub="archive volume"/>
          <Stat label="Object-store growth" value={ccPresent((backups.repo || {}).bucket) ? "tracked" : "unknown"} sub="bucket not shown"/>
        </div></div></div>
      </div>
      <CcSafetyNote text="Figures are operational cost proxies — no real cloud billing source is connected locally. All recommendations are DBA review items, not automatic changes."/>
    </div>
  );
}

/* ===================== CC-9 Network / Private Access Visualizer ===================== */
function NetworkAccessScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/ui/cluster"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/auth"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
    ]).then(function(r) { return { ui: r[0], auth: r[1], backups: r[2], topology: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Network access"/>;
  if (state.error) return <RmErrorPage title="Network access" error={state.error}/>;
  var ui = state.data.ui.ok ? state.data.ui.data : {};
  var auth = state.data.auth.ok ? state.data.auth.data : {};
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var topology = state.data.topology.ok ? state.data.topology.data : {};
  var pgb = ui.pgbouncer || {};
  var settings = ccNormSettings(auth.settings || {});
  var repo = backups.repo || {};

  // Symbolic, redacted path hops.
  var hops = [
    { node: "Application client", type: "app", evidence: "console metadata", value: cluster.region || "—" },
    { node: "LoadBalancer (pgbouncer-lb)", type: "lb", evidence: "manifest pattern", value: ccRedact(cluster.name + "-pgbouncer-lb") },
    { node: "NetworkPolicy", type: "policy", evidence: "manifest", value: "ingress restricted (assumed)" },
    { node: "PgBouncer", type: "proxy", evidence: "cluster metadata", value: (pgb.pods_ready || 0) + "/" + (pgb.pods_total || 0) + " ready" },
    { node: "Primary service", type: "svc", evidence: "manifest pattern", value: ccRedact(cluster.name + "-primary.svc") },
    { node: "PostgreSQL (Patroni)", type: "db", evidence: "live PostgreSQL", value: "listen_addresses=" + (settings.listen_addresses || "unknown") + " ssl=" + (settings.ssl || "unknown") },
    { node: "pgBackRest repo-host", type: "repo", evidence: "backups-api", value: ccPresent(repo.repo) ? "repo configured" : "unknown" },
    { node: "NooBaa / S3 object store", type: "s3", evidence: "backups-api", value: ccPresent(repo.bucket) && ccPresent(repo.s3_endpoint) ? "endpoint configured (redacted)" : "unknown" },
  ];

  var findings = [];
  if (settings.ssl !== "on" && settings.ssl !== "true" && settings.ssl) rmFinding(findings, "warning", "TLS", "ssl is not enabled on the database path");
  if (!ccPresent(repo.bucket) || !ccPresent(repo.s3_endpoint)) rmFinding(findings, "warning", "Object storage", "S3 endpoint/bucket presence not confirmed (DR reachability uncertain)");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={pgb.pods_total && pgb.pods_ready === pgb.pods_total ? "green" : "blue"} label="PgBouncer path" value={(pgb.pods_ready || 0) + "/" + (pgb.pods_total || 0)} sub="proxy ready"/>
        <KPI color={settings.ssl === "on" ? "green" : "orange"} label="DB TLS" value={settings.ssl || "unknown"} sub="ssl setting"/>
        <KPI color={ccPresent(repo.bucket) ? "green" : "orange"} label="Object-store path" value={ccPresent(repo.bucket) ? "configured" : "unknown"} sub="endpoint redacted"/>
        <KPI color="muted" label="DC path" value="DC1 ⇄ DC2" sub="cross-DC assumptions"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Globe size={15}/>App → PostgreSQL → Object storage path</span><SourceBadge source="cluster metadata + manifest patterns"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>#</th><th>Hop</th><th>Type</th><th>Evidence</th><th>Value (redacted)</th></tr></thead><tbody>
          {hops.map(function(h, i) { return <tr key={i}><td className="num">{i + 1}</td><td><strong>{h.node}</strong></td><td>{h.type}</td><td className="txt-xs muted">{h.evidence}</td><td className="mono txt-xs">{h.value}</td></tr>; })}
        </tbody></table>
      </div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">DR reachability assumptions</div><div className="bd"><div className="grid-2">
          <Stat label="DC1 (PROD)" value="primary path" sub="writes + archive"/>
          <Stat label="DC2 (DR)" value="standby path" sub="stream + repo fallback"/>
          <Stat label="Repo path" value={ccPresent(repo.repo) ? repo.repo : "unknown"} sub={"stanza " + (repo.stanza || "unknown")}/>
          <Stat label="Members" value={fmtInt((topology.members || []).length)} sub="topology"/>
        </div></div></div>
        <div className="card"><div className="hd">Network findings</div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      </div>
      <CcSafetyNote text="No live network probe is run from this screen. Endpoints, IPs, buckets, and load balancer addresses are shown only as symbolic/redacted labels."/>
    </div>
  );
}

/* ===================== CC-10 Database Activity Stream ===================== */
function ActivityStreamScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/audit", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
      rmFetch("/api/v1/cutover/runs", { limit: 20 }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/findings", { status: "all" }),
      rmFetch("/api/v1/collector/runs", { cluster: clusterId, limit: 20 }),
    ]).then(function(r) { return { jobs: r[0], audit: r[1], alerts: r[2], cutover: r[3], findings: r[4], collector: r[5] }; });
  });
  var catState = React.useState("all");
  var cat = catState[0], setCat = catState[1];
  if (state.loading && !state.data) return <RmLoadPage title="Activity stream"/>;
  if (state.error) return <RmErrorPage title="Activity stream" error={state.error}/>;
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var audit = state.data.audit.ok ? (state.data.audit.data.audit || []) : [];
  var alerts = state.data.alerts.ok ? (state.data.alerts.data.alerts || []) : [];
  var cutover = state.data.cutover.ok ? (state.data.cutover.data.runs || state.data.cutover.data.jobs || []) : [];
  var findings = state.data.findings.ok ? (state.data.findings.data.findings || []) : [];
  var collector = state.data.collector.ok ? (state.data.collector.data.runs || []) : [];

  var events = [];
  jobs.forEach(function(j) { events.push({ when: j.submitted_at || j.completed_at, cat: "job", sev: rmPill(j.state) === "danger" ? "critical" : "info", title: j.kind, detail: j.state + (j.reason ? " · " + j.reason : "") }); });
  audit.forEach(function(a) { events.push({ when: a.ts || a.created_at || a.time, cat: "audit", sev: "info", title: a.action || a.event || "audit", detail: a.actor || a.user || "" }); });
  alerts.forEach(function(a) { events.push({ when: a.started_at, cat: "alert", sev: a.severity || "warning", title: a.name, detail: a.summary || "" }); });
  cutover.forEach(function(c) { events.push({ when: c.started_at || c.created_at, cat: "cutover", sev: rmPill(c.status) === "danger" ? "critical" : "info", title: c.mode || c.kind || "cutover", detail: c.status || "" }); });
  findings.forEach(function(f) { events.push({ when: f.last_seen_at || f.first_seen_at, cat: "finding", sev: f.severity || "warning", title: f.title, detail: f.detail || "" }); });
  collector.forEach(function(c) { events.push({ when: c.started_at || c.created_at, cat: "collector", sev: "info", title: "collector run", detail: (c.status || "") + (c.metric_count ? " · " + c.metric_count + " metrics" : "") }); });
  var cats = ["all", "job", "audit", "alert", "cutover", "finding", "collector"];
  var filtered = events.filter(function(e) { return cat === "all" || e.cat === cat; });
  filtered.sort(function(a, b) { return new Date(b.when || 0) - new Date(a.when || 0); });

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Events" value={fmtInt(events.length)} sub="merged timeline"/>
        <KPI color={alerts.length ? "red" : "green"} label="Alerts" value={fmtInt(alerts.length)} sub="active"/>
        <KPI color="blue" label="Jobs" value={fmtInt(jobs.length)} sub="recent"/>
        <KPI color="blue" label="Audit rows" value={fmtInt(audit.length)} sub="operator activity"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Activity size={15}/>Database activity stream</span>
        <span className="flex-row" style={{gap: 6, marginLeft: "auto"}}>
          {cats.map(function(c) { return <button key={c} className={"btn sm " + (cat === c ? "primary" : "ghost")} onClick={function() { setCat(c); }}>{c}</button>; })}
        </span>
      </div><div className="bd">
        <table className="tbl"><thead><tr><th>When</th><th>Category</th><th>Severity</th><th>Event</th></tr></thead><tbody>
          {filtered.slice(0, 60).map(function(row, i) { return <tr key={i}><td className="mono txt-xs">{rmDate(row.when)}</td><td>{row.cat}</td><td><span className={"pill " + rmPill(row.sev)}>{row.sev}</span></td><td><strong>{row.title || "—"}</strong>{row.detail ? <div className="muted txt-xs">{row.detail}</div> : null}</td></tr>; })}
          {!filtered.length && <tr><td colSpan="4" className="muted">No events for this filter.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Event details exclude tokens, passwords, private keys, and secret values."/>
    </div>
  );
}

/* ===================== CC-11 Parameter Profile / Drift Manager ===================== */
function ParameterDriftScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/parameters"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/advisor/parameters"),
    ]).then(function(r) { return { params: r[0], advisor: r[1] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Parameter drift"/>;
  if (state.error) return <RmErrorPage title="Parameter drift" error={state.error}/>;
  var params = state.data.params.ok ? state.data.params.data : {};
  var advisor = state.data.advisor.ok ? state.data.advisor.data : {};
  var all = params.parameters || [];
  var pendingRestart = all.filter(function(p) { return p.pending_restart; });
  var nonDefault = all.filter(function(p) { return p.source && p.source !== "default" && p.boot_val != null && String(p.setting) !== String(p.boot_val); });
  var recos = advisor.recommendations || advisor.parameters || [];

  // Build drift rows: recommended vs current from advisor, plus pending-restart deltas.
  var driftRows = [];
  recos.forEach(function(r) {
    var name = r.name || r.parameter;
    var cur = all.filter(function(p) { return p.name === name; })[0] || {};
    driftRows.push({ name: name, baseline: r.recommended != null ? r.recommended : r.suggested_value, current: cur.setting != null ? cur.setting : "unknown", severity: r.severity || "warning", restart: !!cur.pending_restart, note: r.rationale || r.reason || "advisor recommendation" });
  });

  var findings = [];
  if (pendingRestart.length) rmFinding(findings, "warning", "Pending restart", pendingRestart.length + " settings have pending-restart values (active value differs)");
  if (!recos.length) rmFinding(findings, "info", "Baseline", "No advisor baseline available, drift compares non-default settings only");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Parameters" value={fmtInt(all.length)} sub="pg_settings"/>
        <KPI color={nonDefault.length ? "orange" : "green"} label="Non-default" value={fmtInt(nonDefault.length)} sub="changed from boot"/>
        <KPI color={pendingRestart.length ? "orange" : "green"} label="Pending restart" value={fmtInt(pendingRestart.length)} sub="active ≠ pending"/>
        <KPI color={driftRows.length ? "orange" : "green"} label="Advisor drift" value={fmtInt(driftRows.length)} sub="vs recommended"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Sliders size={15}/>Drift notes</span></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Sliders size={15}/>Profile drift (baseline vs current)</span><SourceBadge source="pg_settings + advisor"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Parameter</th><th>Recommended baseline</th><th>Current</th><th>Severity</th><th>Restart</th><th>Note</th></tr></thead><tbody>
          {driftRows.slice(0, 20).map(function(r, i) { return <tr key={i}><td className="mono">{r.name}</td><td>{String(r.baseline != null ? r.baseline : "—")}</td><td>{String(r.current)}</td><td><span className={"pill " + rmPill(r.severity)}>{r.severity}</span></td><td><span className={"pill " + (r.restart ? "warn" : "ok")}>{r.restart ? "yes" : "no"}</span></td><td className="txt-xs muted">{r.note}</td></tr>; })}
          {!driftRows.length && <tr><td colSpan="6" className="muted">No advisor drift. See non-default settings below.</td></tr>}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd">Pending-restart changes</div><div className="bd">
        <table className="tbl"><thead><tr><th>Name</th><th>Active value</th><th>Context</th></tr></thead><tbody>
          {pendingRestart.slice(0, 15).map(function(p, i) { return <tr key={i}><td className="mono">{p.name}</td><td>{p.setting} {p.unit || ""}</td><td>{p.context}</td></tr>; })}
          {!pendingRestart.length && <tr><td colSpan="3" className="muted">No pending-restart deltas.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="No parameter patch is executed here. Changes go through the existing parameter advisor / validation job flow. Pending-restart values are distinguished from active values."/>
    </div>
  );
}

/* ===================== CC-12 Log Analytics Center ===================== */
function LogAnalyticsScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/pods"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/findings", { status: "all" }),
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/logs/search", { range: "1h", limit: 100 }),
    ]).then(function(r) { return { pods: r[0], findings: r[1], alerts: r[2], logs: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Log analytics"/>;
  if (state.error) return <RmErrorPage title="Log analytics" error={state.error}/>;
  var pods = state.data.pods.ok ? (state.data.pods.data.pods || state.data.pods.data.items || []) : [];
  var findings = state.data.findings.ok ? (state.data.findings.data.findings || []) : [];
  var alerts = state.data.alerts.ok ? (state.data.alerts.data.alerts || []) : [];
  var logPayload = state.data.logs && state.data.logs.ok ? state.data.logs.data : {};
  var logEntries = logPayload.entries || [];

  // Category coverage by source (evidence presence only — no raw log content read).
  var categories = [
    { key: "postgresql", label: "PostgreSQL", count: pods.filter(function(p) { return /postgres|dc1/i.test(p.name || ""); }).length },
    { key: "patroni", label: "Patroni", count: pods.filter(function(p) { return /patroni|dc1/i.test(p.name || ""); }).length },
    { key: "pgo", label: "PGO / operator", count: pods.filter(function(p) { return /operator|pgo/i.test(p.name || ""); }).length },
    { key: "pgbouncer", label: "PgBouncer", count: pods.filter(function(p) { return /pgbouncer|bouncer/i.test(p.name || ""); }).length },
    { key: "pgbackrest", label: "pgBackRest", count: pods.filter(function(p) { return /backrest|repo/i.test(p.name || ""); }).length },
  ];
  // Cluster recurring signatures from findings (already sanitized upstream).
  var sigCounts = {};
  findings.forEach(function(f) { var k = (f.title || f.issue_id || "finding"); sigCounts[k] = (sigCounts[k] || 0) + 1; });
  var sigRows = Object.keys(sigCounts).map(function(k) { return { label: k, value: sigCounts[k] }; });

  var findingsTbl = [];
  if (!pods.length) rmFinding(findingsTbl, "info", "Log sources", "No pod inventory available locally, log categories show as not collected");
  if (!findings.length && !alerts.length) rmFinding(findingsTbl, "info", "Signatures", "No error signatures or alerts to cluster yet");

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Loki entries" value={fmtInt(logEntries.length)} sub={logPayload.source || "Loki unavailable"}/>
        <KPI color={findings.length ? "orange" : "green"} label="Error signatures" value={fmtInt(sigRows.length)} sub="clustered findings"/>
        <KPI color={alerts.length ? "red" : "green"} label="Active alerts" value={fmtInt(alerts.length)} sub="log-derived/derived"/>
        <KPI color="blue" label="Categories" value={fmtInt(categories.length)} sub="PG · Patroni · PGO · ..."/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.FileText size={15}/>Log analytics notes</span></div><div className="bd"><RmFindingTable rows={findingsTbl}/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">Log category coverage</div><div className="bd">
          <table className="tbl"><thead><tr><th>Category</th><th>Sources</th><th>Status</th></tr></thead><tbody>
            {categories.map(function(c) { return <tr key={c.key}><td>{c.label}</td><td className="num">{fmtInt(c.count)}</td><td><span className={"pill " + (c.count ? "ok" : "muted")}>{c.count ? "available" : "not collected"}</span></td></tr>; })}
          </tbody></table>
        </div></div>
        <div className="card"><div className="hd">Top recurring signatures</div><div className="bd"><BarList rows={sigRows} valueFormatter={fmtInt} emptyText="No recurring error signatures."/></div></div>
      </div>
      <div className="card"><div className="hd">Clustered findings (sanitized)</div><div className="bd">
        <table className="tbl"><thead><tr><th>Severity</th><th>Signature</th><th>Detail</th><th>Last seen</th></tr></thead><tbody>
          {findings.slice(0, 15).map(function(f, i) { return <tr key={i}><td><span className={"pill " + rmPill(f.severity)}>{f.severity}</span></td><td><strong>{f.title}</strong></td><td className="txt-xs muted">{f.detail}</td><td className="mono txt-xs">{rmDate(f.last_seen_at)}</td></tr>; })}
          {!findings.length && <tr><td colSpan="4" className="muted">No findings to display.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Logs are queried from bounded Loki search and summarized with sanitized findings. Raw log content, secrets, and customer data are not displayed."/>
    </div>
  );
}

/* ===================== CC-13 Security Posture Center ===================== */
function SecurityPostureScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/auth"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/tls"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/pgaudit"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/sensitive-data"),
      rmFetch("/api/v1/compliance/operational", { cluster: clusterId }),
    ]).then(function(r) { return { auth: r[0], tls: r[1], pgaudit: r[2], sensitive: r[3], compliance: r[4] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Security posture"/>;
  if (state.error) return <RmErrorPage title="Security posture" error={state.error}/>;
  var auth = state.data.auth.ok ? state.data.auth.data : {};
  var tls = state.data.tls.ok ? state.data.tls.data : {};
  var pgaudit = state.data.pgaudit.ok ? state.data.pgaudit.data : {};
  var sensitive = state.data.sensitive.ok ? state.data.sensitive.data : {};
  var authSum = auth.summary || {};
  var tlsSum = tls.summary || {};
  var auditSum = pgaudit.summary || {};
  var settings = ccNormSettings(auth.settings || {});

  var recos = [];
  function add(sev, cat, title, source, impact, next) { recos.push({ severity: sev, category: cat, title: title, source: source, impact: impact, next: next, target: "Security & Compliance" }); }
  if (settings.ssl !== "on" && settings.ssl) add("critical", "TLS", "ssl is not enabled", "pg_settings", "Plaintext DB connections", "Review TLS Certificates");
  if (tlsSum.status && tlsSum.status !== "ok") add("warning", "TLS", "TLS posture: " + tlsSum.status, "pg_stat_ssl", "Certificate/rotation gap", "Review TLS Certificates");
  if (auditSum.installed === false || auditSum.preloaded === false) add("warning", "audit", "pgaudit not fully enabled", "pgaudit", "Reduced audit coverage", "Review pgaudit Settings");
  if (authSum.status && authSum.status !== "ok") add("warning", "auth", "Authentication posture: " + authSum.status, "pg_hba_file_rules", "HBA/password risk", "Review Authentication");
  if (settings.password_encryption && settings.password_encryption !== "scram-sha-256") add("warning", "auth", "password_encryption is not scram-sha-256", "pg_settings", "Weaker password hashing", "Review Authentication");
  var sensCount = ccNum(sensitive.count || (sensitive.columns || []).length);
  if (sensCount) add("info", "data", sensCount + " sensitive-column candidate(s) by naming heuristic", "metadata", "Review masking/access", "Review Sensitive Data");

  // Category scores
  function catScore(items) { return ccScoreFromFindings(items); }
  var tlsItems = recos.filter(function(r) { return r.category === "TLS"; });
  var authItems = recos.filter(function(r) { return r.category === "auth"; });
  var auditItems = recos.filter(function(r) { return r.category === "audit"; });
  var overall = ccScoreFromFindings(recos);

  return (
    <div className="page">
      <div className="tile-row">
        <CcScoreCard label="Security score" score={overall} sub="weighted posture"/>
        <CcScoreCard label="TLS" score={catScore(tlsItems)} sub={settings.ssl || "ssl unknown"}/>
        <CcScoreCard label="Auth/HBA" score={catScore(authItems)} sub={authSum.status || "unknown"}/>
        <CcScoreCard label="Audit" score={catScore(auditItems)} sub={auditSum.installed ? "pgaudit on" : "pgaudit off"}/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Shield size={15}/>Prioritized remediation</span><SourceBadge source="pg_settings + pg_hba + pgaudit + metadata"/></div><div className="bd"><CcRecoTable rows={recos} emptyHint="Security posture is within policy across the evidence the console can see."/></div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">Posture detail</div><div className="bd"><div className="grid-2">
          <Stat label="ssl" value={settings.ssl || "unknown"} sub="TLS enabled"/>
          <Stat label="password_encryption" value={settings.password_encryption || "unknown"} sub="hashing"/>
          <Stat label="log_connections" value={settings.log_connections || "unknown"} sub="audit trail"/>
          <Stat label="pgaudit" value={auditSum.installed ? "installed" : "absent"} sub={auditSum.preloaded ? "preloaded" : "not preloaded"}/>
        </div></div></div>
        <div className="card"><div className="hd">Sensitive data &amp; certs</div><div className="bd"><div className="grid-2">
          <Stat label="Sensitive columns" value={fmtInt(sensCount)} sub="naming heuristic"/>
          <Stat label="Cert expiry" value={tlsSum.certificate_expiry || tlsSum.cert_expiry || tlsSum.expires_at ? rmDate(tlsSum.certificate_expiry || tlsSum.cert_expiry || tlsSum.expires_at) : "unknown"} sub="server cert"/>
          <Stat label="HBA rules" value={fmtInt((auth.hba || []).length)} sub="pg_hba_file_rules"/>
          <Stat label="Roles" value={fmtInt((auth.roles || []).length)} sub="pg_roles"/>
        </div></div></div>
      </div>
      <CcSafetyNote text="No secret value, certificate private key, kubeconfig, token, or S3 credential is printed. All checks read presence/metadata only."/>
    </div>
  );
}

/* ===================== CC-14 Support Case / Incident Pack Tracker ===================== */
function IncidentPacksScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/collector/alert-bundle-requests", { cluster: clusterId, status: "all" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/findings", { status: "all" }),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
    ]).then(function(r) { return { bundles: r[0], findings: r[1], jobs: r[2], alerts: r[3] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Incident packs"/>;
  if (state.error) return <RmErrorPage title="Incident packs" error={state.error}/>;
  var bundles = state.data.bundles.ok ? (state.data.bundles.data.bundles || state.data.bundles.data.requests || []) : [];
  var findings = state.data.findings.ok ? (state.data.findings.data.findings || []) : [];
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var alerts = state.data.alerts.ok ? (state.data.alerts.data.alerts || []) : [];

  // Each bundle request = one incident pack candidate.
  var packs = bundles.map(function(b) {
    var linkedJobs = jobs.filter(function(j) { return (j.reason || "").indexOf(b.issue_id || "~none~") >= 0; });
    return {
      id: b.issue_id || b.id, status: b.status || "open", owner: b.requested_by || b.owner || "—",
      severity: b.severity || "warning", title: b.alert_name || b.message || "incident",
      requested: b.requested_at, alerts: 1, jobs: linkedJobs.length, redaction: "redacted",
    };
  });

  function exportPack(pack) {
    var payload = {
      generated_at: new Date().toISOString(), cluster_id: clusterId, incident: pack.id,
      redaction: "No secret data, kubeconfig, private keys, bearer tokens, S3 access keys, or passwords are included.",
      pack: pack, linked_findings: findings.filter(function(f) { return (f.issue_id || "") === pack.id; }),
    };
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = "hbz-pg-incident-" + clusterId + "-" + (pack.id || "pack") + "-" + Date.now() + ".json"; a.click();
    URL.revokeObjectURL(url);
  }

  var open = packs.filter(function(p) { return p.status !== "closed" && p.status !== "resolved"; }).length;

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={open ? "orange" : "green"} label="Open packs" value={fmtInt(open)} sub="incident tracker"/>
        <KPI color="blue" label="Total packs" value={fmtInt(packs.length)} sub="bundle requests"/>
        <KPI color={findings.length ? "orange" : "green"} label="Linked findings" value={fmtInt(findings.length)} sub="evidence"/>
        <KPI color="green" label="Redaction" value="enabled" sub="local export only"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.FileText size={15}/>Incident / support packs</span><SourceBadge source="collector + findings + jobs"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Owner</th><th>Linked</th><th>Redaction</th><th>Export</th></tr></thead><tbody>
          {packs.map(function(p, i) { return <tr key={i}><td><strong>{p.title}</strong><div className="muted txt-xs mono">{p.id}</div></td><td><span className={"pill " + rmPill(p.severity)}>{p.severity}</span></td><td><span className={"pill " + rmPill(p.status)}>{p.status}</span></td><td>{p.owner}</td><td className="txt-xs">{p.alerts} alert · {p.jobs} jobs</td><td><span className="pill ok">redacted</span></td><td><button className="btn sm ghost" onClick={function() { exportPack(p); }}><Icon.Download size={12}/> JSON</button></td></tr>; })}
          {!packs.length && <tr><td colSpan="7" className="muted">No incident packs. Bundle requests from the collector appear here.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Exports are generated locally from existing redacted API responses. No external upload is performed and no secret values are included."/>
    </div>
  );
}

/* ===================== CC-15 SLA / RTO / RPO Compliance Dashboard ===================== */
function SlaComplianceScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
      rmFetch("/api/v1/cutover/runs", { limit: 10 }),
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
    ]).then(function(r) { return { backups: r[0], topology: r[1], cutover: r[2], readiness: r[3], jobs: r[4] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="SLA compliance"/>;
  if (state.error) return <RmErrorPage title="SLA compliance" error={state.error}/>;
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var topology = state.data.topology.ok ? state.data.topology.data : {};
  var cutover = state.data.cutover.ok ? (state.data.cutover.data.runs || state.data.cutover.data.jobs || []) : [];
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var archive = backups.archive || {};
  var settings = backups.settings || {};
  var history = backups.history || [];
  var ts = topology.summary || {};

  // Targets (operational defaults — explicit, not claimed from a billing SLA).
  var RPO_TARGET = 300, RTO_TARGET = 1800;
  var rpoActual = archive.rpo_seconds != null ? archive.rpo_seconds : archive.archive_lag_sec;
  var lagBytes = ts.max_lag_bytes;
  var lastBackup = archive.last_archived_time || backups.last_backup;
  var lastDrill = history.length ? (history[0].completed_at || history[0].created_at) : null;
  var lastCutover = cutover.length ? cutover[0] : null;

  var rows = [];
  function row(metric, target, actual, ok) { rows.push({ metric: metric, target: target, actual: actual, ok: ok }); }
  row("RPO (archive lag)", fmtSec(RPO_TARGET), rpoActual != null ? fmtSec(ccNum(rpoActual)) : "unknown", rpoActual != null ? ccNum(rpoActual) <= RPO_TARGET : null);
  row("RTO (recovery)", fmtSec(RTO_TARGET), "estimate pending drill", null);
  row("Backup freshness", "< 24h", lastBackup ? rmDate(lastBackup) : "unknown", lastBackup ? (Date.now() - Date.parse(lastBackup)) < 86400000 : null);
  row("Latest restorable", "available", lastBackup ? rmDate(lastBackup) : "unknown", !!lastBackup);
  row("Replication lag", "< 16 MiB", lagBytes != null ? fmtBytes(ccNum(lagBytes)) : "unknown", lagBytes != null ? ccNum(lagBytes) < 16 * 1024 * 1024 : null);
  row("Restore drill age", "< 90d", lastDrill ? rmDate(lastDrill) : "no drills", lastDrill ? (Date.now() - Date.parse(lastDrill)) < 90 * 86400000 : null);
  row("Cutover rehearsal", "passing", lastCutover ? (lastCutover.status || "—") : "none", lastCutover ? rmPill(lastCutover.status) === "ok" : null);

  var known = rows.filter(function(r) { return r.ok !== null; });
  var met = known.filter(function(r) { return r.ok; }).length;
  var score = known.length ? Math.round((met / known.length) * 100) : null;
  var findings = [];
  if (settings.archive_mode !== "on") rmFinding(findings, "critical", "RPO", "archive_mode off, RPO commitment cannot be met");
  if (!history.length) rmFinding(findings, "warning", "RTO", "No restore drill, RTO is an unvalidated estimate");

  return (
    <div className="page">
      <div className="tile-row">
        <CcScoreCard label="Compliance score" score={score} sub={known.length + " measurable metrics"}/>
        <KPI color={rpoActual != null ? (ccNum(rpoActual) <= RPO_TARGET ? "green" : "red") : "orange"} label="RPO actual" value={rpoActual != null ? fmtSec(ccNum(rpoActual)) : "unknown"} sub={"target " + fmtSec(RPO_TARGET)}/>
        <KPI color={lastDrill ? "green" : "orange"} label="Last restore drill" value={lastDrill ? rmDate(lastDrill) : "none"} sub="< 90d target"/>
        <KPI color={lagBytes != null ? (ccNum(lagBytes) < 16 * 1024 * 1024 ? "green" : "orange") : "orange"} label="Replication lag" value={lagBytes != null ? fmtBytes(ccNum(lagBytes)) : "unknown"} sub="< 16 MiB target"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Shield size={15}/>Compliance blockers</span></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.CheckCircle size={15}/>SLA / RTO / RPO — target vs actual</span><SourceBadge source="backups + replication + cutover"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Metric</th><th>Target</th><th>Actual</th><th>Status</th></tr></thead><tbody>
          {rows.map(function(r, i) { return <tr key={i}><td><strong>{r.metric}</strong></td><td>{r.target}</td><td>{r.actual}</td><td><span className={"pill " + (r.ok === null ? "muted" : r.ok ? "ok" : "danger")}><span className="dot"/>{r.ok === null ? "unknown" : r.ok ? "met" : "missed"}</span></td></tr>; })}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Targets are explicit operational defaults, not a billing SLA. Unknown evidence is shown as 'unknown' and never counted as compliant."/>
    </div>
  );
}

/* ===================== Exports ===================== */
Object.assign(window, {
  ccRedact: ccRedact, ccPresent: ccPresent, ccSettingVal: ccSettingVal,
  ccNormSettings: ccNormSettings, ccScoreTone: ccScoreTone, ccSevWeight: ccSevWeight,
  ccScoreFromFindings: ccScoreFromFindings, CcScoreCard: CcScoreCard,
  CcRecoTable: CcRecoTable, CcSafetyNote: CcSafetyNote, ccNum: ccNum,
  CloudAdvisorScreen: CloudAdvisorScreen,
  DbLoadTimelineScreen: DbLoadTimelineScreen,
  PgBouncerAdvancedScreen: PgBouncerAdvancedScreen,
  RestoreWindowScreen: RestoreWindowScreen,
  BlueGreenUpgradeScreen: BlueGreenUpgradeScreen,
  GeoTopologyScreen: GeoTopologyScreen,
  MaintenanceFeedScreen: MaintenanceFeedScreen,
  CapacityOptimizerScreen: CapacityOptimizerScreen,
  NetworkAccessScreen: NetworkAccessScreen,
  ActivityStreamScreen: ActivityStreamScreen,
  ParameterDriftScreen: ParameterDriftScreen,
  LogAnalyticsScreen: LogAnalyticsScreen,
  SecurityPostureScreen: SecurityPostureScreen,
  IncidentPacksScreen: IncidentPacksScreen,
  SlaComplianceScreen: SlaComplianceScreen,
});
