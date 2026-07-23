// Cloud Console Parity — Wave 2 (CC-16 .. CC-39).
//
// Next-wave managed-PostgreSQL console parity (Azure Flexible Server / AWS RDS &
// Aurora / GCP Cloud SQL & AlloyDB / Crunchy Bridge), adapted to the self-managed
// Patroni / PGO / OpenShift estate. Same rules as CC-1..15: READ-ONLY first,
// reuse existing read-only APIs, every write-like action stays preflight /
// approval-gated, secrets & endpoints redacted, explicit unknown states.
//
// Reuses global helpers from remaining_phases.jsx (useRmPayload, rmFetch, rmPill,
// rmDate, rmFinding, RmFindingTable, RmLoadPage, RmErrorPage, rmClusterId) and
// cloud_console_gaps.jsx (ccRedact, ccPresent, ccNum, CcScoreCard, CcRecoTable,
// CcSafetyNote, ccScoreFromFindings) plus UI primitives (KPI, Stat, SourceBadge,
// EmptyState, BarList, fmt*, Icon). This file loads after cloud_console_gaps.

/* ===================== Local helpers ===================== */

function cpStore(key, fallback) {
  try { var v = localStorage.getItem("hbz-parity-" + key); return v ? JSON.parse(v) : fallback; }
  catch (e) { return fallback; }
}
function cpSave(key, value) {
  try { localStorage.setItem("hbz-parity-" + key, JSON.stringify(value)); } catch (e) {}
}
function cpCopy(text) {
  try { if (navigator && navigator.clipboard) navigator.clipboard.writeText(text); } catch (e) {}
}
// z-score anomaly scan over a [[ts, val], ...] series. Returns count + last anomaly.
function cpAnoms(points, sigma) {
  var vals = (points || []).map(function(p) { return Number(p[1] || 0); });
  if (vals.length < 8) return { count: 0, last: null, mean: null, std: null, n: vals.length };
  var mean = vals.reduce(function(a, b) { return a + b; }, 0) / vals.length;
  var variance = vals.reduce(function(a, b) { return a + (b - mean) * (b - mean); }, 0) / vals.length;
  var std = Math.sqrt(variance);
  var th = sigma || 3;
  var anoms = [];
  (points || []).forEach(function(p, i) { if (std > 0 && Math.abs(vals[i] - mean) > th * std) anoms.push(p); });
  return { count: anoms.length, last: anoms.length ? anoms[anoms.length - 1] : null, mean: mean, std: std, n: vals.length };
}
// Linear "time to exhaustion" from first/last point of a series toward a limit.
function cpExhaust(points, limit) {
  var pts = points || [];
  if (pts.length < 2 || !limit) return null;
  var first = pts[0], last = pts[pts.length - 1];
  var dv = Number(last[1]) - Number(first[1]);
  var dt = Number(last[0]) - Number(first[0]);
  if (dv <= 0 || dt <= 0) return null;
  var ratePerMs = dv / dt;
  var remaining = limit - Number(last[1]);
  if (remaining <= 0) return 0;
  return remaining / ratePerMs; // ms until exhaustion
}
function cpDays(ms) {
  if (ms == null) return "stable / no growth";
  if (ms <= 0) return "at limit";
  var d = ms / 86400000;
  if (d > 3650) return "> 10y";
  return Math.round(d) + "d";
}
function CpCopyRow({ label, value }) {
  return (
    <div className="flex-row" style={{justifyContent: "space-between", gap: 8, alignItems: "center", padding: "4px 0"}}>
      <span className="txt-xs muted" style={{minWidth: 120}}>{label}</span>
      <code className="mono txt-xs" style={{flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{value}</code>
      <button className="btn sm ghost" title="Copy" onClick={function() { cpCopy(value); }}><Icon.FileText size={12}/></button>
    </div>
  );
}

/* ============================================================
   PHASE P1 — Connectivity & Access Experience
   ============================================================ */

/* ---------- CC-16 Connect Hub ---------- */
function ConnectHubScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/ui/cluster"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/auth"),
    ]).then(function(r) { return { ui: r[0], auth: r[1] }; });
  });
  var langState = React.useState("psql");
  var lang = langState[0], setLang = langState[1];
  if (state.loading && !state.data) return <RmLoadPage title="Connect"/>;
  if (state.error) return <RmErrorPage title="Connect" error={state.error}/>;
  var ui = state.data.ui.ok ? state.data.ui.data : {};
  var pgb = ui.pgbouncer || {};
  var settings = ccNormSettings((state.data.auth.ok ? state.data.auth.data : {}).settings || {});
  var ns = cluster.namespace || (cluster.name || "uat-pgcluster-uae");
  var name = cluster.name || "uat-pgcluster-uae";
  var port = "5555";
  var db = "<database>";
  var user = "<user>";
  var primary = name + "-primary." + ns + ".svc";
  var replicas = name + "-replicas." + ns + ".svc";
  var bouncer = name + "-pgbouncer." + ns + ".svc";
  var ssl = settings.ssl === "on" ? "require" : "prefer";
  var endpoints = [
    { role: "Primary (read/write)", host: primary, port: port },
    { role: "Replicas (read-only)", host: replicas, port: port },
    { role: "PgBouncer (pooled)", host: bouncer, port: port },
  ];
  function snippet(host) {
    if (lang === "psql") return "PGPASSWORD=‹from-secret› psql \"host=" + host + " port=" + port + " dbname=" + db + " user=" + user + " sslmode=" + ssl + "\"";
    if (lang === "jdbc") return "jdbc:postgresql://" + host + ":" + port + "/" + db + "?user=" + user + "&ssl=true&sslmode=" + ssl;
    if (lang === "python") return "psycopg.connect(host='" + host + "', port=" + port + ", dbname='" + db + "', user='" + user + "', password=‹from-secret›, sslmode='" + ssl + "')";
    if (lang === "dotnet") return "Host=" + host + ";Port=" + port + ";Database=" + db + ";Username=" + user + ";Password=‹from-secret›;SSL Mode=Require";
    if (lang === "go") return "postgres://" + user + ":‹from-secret›@" + host + ":" + port + "/" + db + "?sslmode=" + ssl;
    return "host=" + host + " port=" + port + " dbname=" + db + " user=" + user + " sslmode=" + ssl;
  }
  var langs = ["psql", "jdbc", "python", "dotnet", "go", "libpq"];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Endpoints" value={fmtInt(endpoints.length)} sub="primary · replicas · pooled"/>
        <KPI color={settings.ssl === "on" ? "green" : "orange"} label="TLS" value={settings.ssl || "unknown"} sub={"sslmode=" + ssl}/>
        <KPI color={pgb.pods_total && pgb.pods_ready === pgb.pods_total ? "green" : "blue"} label="PgBouncer" value={(pgb.pods_ready || 0) + "/" + (pgb.pods_total || 0)} sub="pooled path"/>
        <KPI color="muted" label="Port" value={port} sub="PGO service port"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Globe size={15}/>Connection endpoints</span><SourceBadge source="cluster metadata + auth"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Role</th><th>Host (service DNS)</th><th className="num">Port</th><th>Copy</th></tr></thead><tbody>
          {endpoints.map(function(e, i) { return <tr key={i}><td><strong>{e.role}</strong></td><td className="mono txt-xs">{e.host}</td><td className="num">{e.port}</td><td><button className="btn sm ghost" onClick={function() { cpCopy(e.host + ":" + e.port); }}><Icon.FileText size={12}/></button></td></tr>; })}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Terminal size={15}/>Connection snippets</span>
        <span className="flex-row" style={{gap: 6, marginLeft: "auto"}}>{langs.map(function(l) { return <button key={l} className={"btn sm " + (lang === l ? "primary" : "ghost")} onClick={function() { setLang(l); }}>{l}</button>; })}</span>
      </div><div className="bd">
        {endpoints.map(function(e, i) { return <div key={i} style={{marginBottom: 8}}><div className="txt-xs muted">{e.role}</div><CpCopyRow label="" value={snippet(e.host)}/></div>; })}
        <div className="muted txt-xs mt-2">Password shown as <code>‹from-secret›</code>; fetch it from the cluster Secret (<code>uat-pg-object-monitor-db</code>) — never inline. Download the CA cert from the PGO-generated <code>*-cluster-cert</code> secret for <code>sslmode=verify-full</code>.</div>
      </div></div>
      <CcSafetyNote text="No secret value is read or displayed. Hosts are service DNS names; use the cluster Secret for credentials and the PGO CA for TLS verification."/>
    </div>
  );
}

/* ---------- CC-17 Endpoints & Listeners ---------- */
function EndpointsScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/ui/cluster"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
    ]).then(function(r) { return { ui: r[0], topology: r[1] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Endpoints"/>;
  if (state.error) return <RmErrorPage title="Endpoints" error={state.error}/>;
  var ui = state.data.ui.ok ? state.data.ui.data : {};
  var pgb = ui.pgbouncer || {};
  var topology = state.data.topology.ok ? state.data.topology.data : {};
  var members = topology.members || [];
  var name = cluster.name || "uat-pgcluster-uae";
  var ns = cluster.namespace || name;
  var leader = members.filter(function(m) { return (m.role || "").toLowerCase().indexOf("lead") >= 0 || (m.role || "").toLowerCase() === "master"; })[0];
  var rows = [
    { ep: name + "-primary", kind: "Service (ClusterIP)", routes: "current leader (read/write)", tls: "PGO server cert", health: leader ? "ok" : "unknown" },
    { ep: name + "-replicas", kind: "Service (ClusterIP)", routes: "sync/async standbys (read-only)", tls: "PGO server cert", health: members.length > 1 ? "ok" : "unknown" },
    { ep: name + "-pgbouncer", kind: "Service (ClusterIP)", routes: "PgBouncer pool → primary", tls: "PGO server cert", health: pgb.pods_ready === pgb.pods_total && pgb.pods_total ? "ok" : "unknown" },
    { ep: name + "-pgbouncer-lb", kind: "LoadBalancer", routes: "external → PgBouncer", tls: "edge", health: "unknown" },
    { ep: name + "-primary-lb", kind: "LoadBalancer", routes: "external → primary", tls: "edge", health: "unknown" },
  ];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Endpoints" value={fmtInt(rows.length)} sub="services + LBs"/>
        <KPI color={leader ? "green" : "orange"} label="Writer" value={leader ? "primary" : "unknown"} sub={leader ? (leader.name || "leader") : "no leader evidence"}/>
        <KPI color={members.length > 1 ? "green" : "orange"} label="Reader members" value={fmtInt(Math.max(0, members.length - 1))} sub="replica endpoints"/>
        <KPI color={pgb.pods_total && pgb.pods_ready === pgb.pods_total ? "green" : "blue"} label="Pooled" value={(pgb.pods_ready || 0) + "/" + (pgb.pods_total || 0)} sub="PgBouncer"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.GitBranch size={15}/>Endpoints &amp; listeners ({ns})</span><SourceBadge source="cluster metadata + Patroni"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Endpoint</th><th>Kind</th><th>Routes to</th><th>TLS</th><th>Health</th></tr></thead><tbody>
          {rows.map(function(r, i) { return <tr key={i}><td className="mono txt-xs">{r.ep}</td><td>{r.kind}</td><td>{r.routes}</td><td className="txt-xs">{r.tls}</td><td><span className={"pill " + rmPill(r.health)}>{r.health}</span></td></tr>; })}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd">Patroni members behind endpoints</div><div className="bd">
        <table className="tbl"><thead><tr><th>Member</th><th>Role</th><th>State</th><th className="num">Lag</th></tr></thead><tbody>
          {members.map(function(m, i) { return <tr key={i}><td className="mono txt-xs">{m.name || "—"}</td><td>{m.role || "—"}</td><td><span className={"pill " + rmPill(m.state)}>{m.state || "unknown"}</span></td><td className="num">{m.lag_bytes != null ? fmtBytes(m.lag_bytes) : "—"}</td></tr>; })}
          {!members.length && <tr><td colSpan="4" className="muted">No member evidence for this cluster.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="LoadBalancer external addresses are intentionally not shown. Endpoint health for LBs reads 'unknown' without a live probe."/>
    </div>
  );
}

/* ---------- CC-18 Firewall / Access Rules Governance ---------- */
function AccessRulesScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/auth"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/hba"),
    ]).then(function(r) { return { auth: r[0], hba: r[1] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Access rules"/>;
  if (state.error) return <RmErrorPage title="Access rules" error={state.error}/>;
  var auth = state.data.auth.ok ? state.data.auth.data : {};
  var settings = ccNormSettings(auth.settings || {});
  var hba = (state.data.hba.ok ? (state.data.hba.data.hba || state.data.hba.data.rules) : null) || auth.hba || [];
  var trust = hba.filter(function(r) { return /trust/i.test(r.auth_method || r.method || ""); });
  var wideOpen = hba.filter(function(r) { return /0\.0\.0\.0\/0|::\/0/.test(r.address || r.cidr || ""); });
  var findings = [];
  if (settings.listen_addresses === "*") rmFinding(findings, "warning", "Listen", "listen_addresses='*' — rely on NetworkPolicy + HBA to scope access");
  if (trust.length) rmFinding(findings, "critical", "HBA", trust.length + " trust-auth rule(s) present", "trust bypasses passwords");
  if (wideOpen.length) rmFinding(findings, "warning", "HBA", wideOpen.length + " rule(s) allow any source CIDR");
  if (!hba.length) rmFinding(findings, "info", "HBA", "No pg_hba_file_rules evidence available locally");
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="HBA rules" value={fmtInt(hba.length)} sub="pg_hba_file_rules"/>
        <KPI color={trust.length ? "red" : "green"} label="Trust rules" value={fmtInt(trust.length)} sub="password bypass"/>
        <KPI color={wideOpen.length ? "orange" : "green"} label="Any-source rules" value={fmtInt(wideOpen.length)} sub="0.0.0.0/0 or ::/0"/>
        <KPI color={settings.listen_addresses === "*" ? "orange" : "green"} label="listen_addresses" value={settings.listen_addresses || "unknown"} sub="bind scope"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Lock size={15}/>Access governance findings</span><SourceBadge source="pg_hba + pg_settings"/></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="card"><div className="hd">Host-based access rules</div><div className="bd">
        <table className="tbl"><thead><tr><th>Type</th><th>Database</th><th>User</th><th>Source</th><th>Method</th></tr></thead><tbody>
          {hba.slice(0, 25).map(function(r, i) { return <tr key={i}><td>{r.type || "host"}</td><td>{r.database || r.db || "all"}</td><td>{r.user_name || r.user || "all"}</td><td className="mono txt-xs">{ccRedact(r.address || r.cidr || "—")}</td><td><span className={"pill " + (/trust/i.test(r.auth_method || r.method || "") ? "danger" : "ok")}>{r.auth_method || r.method || "—"}</span></td></tr>; })}
          {!hba.length && <tr><td colSpan="5" className="muted">No HBA evidence available.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Source addresses are redacted. No access change is made here — HBA/auth edits route to the existing guarded Authentication validation flow."/>
    </div>
  );
}

/* ============================================================
   PHASE P2 — Deep Monitoring & Intelligence
   ============================================================ */

/* ---------- CC-19 Enhanced Host/OS Monitoring ---------- */
function HostMonitoringScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/ui/cluster"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/sessions"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "cpu", range: "24h", agg: "avg" }),
    ]).then(function(r) { return { ui: r[0], sessions: r[1], cpu: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Host monitoring"/>;
  if (state.error) return <RmErrorPage title="Host monitoring" error={state.error}/>;
  var ui = state.data.ui.ok ? state.data.ui.data : {};
  var sessions = state.data.sessions.ok ? (state.data.sessions.data.sessions || state.data.sessions.data.rows || []) : [];
  var pods = (cluster.pods || []);
  var uiMembers = ui.members || ui.pods || [];
  var rows = pods.length ? pods : uiMembers;
  var active = sessions.filter(function(s) { return (s.state || "") === "active"; });
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Pods" value={fmtInt(rows.length)} sub="database instances"/>
        <KPI color={cluster.cpu > 85 ? "red" : "green"} label="Leader CPU" value={(cluster.cpu != null ? cluster.cpu : "—") + "%"} sub="observed"/>
        <KPI color={cluster.mem > 85 ? "red" : "green"} label="Leader memory" value={(cluster.mem != null ? cluster.mem : "—") + "%"} sub="observed"/>
        <KPI color="blue" label="Active backends" value={fmtInt(active.length)} sub="processes"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Activity size={15}/>Per-pod host metrics</span><SourceBadge source="object-metrics + cluster"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Pod</th><th>Node</th><th>Role</th><th className="num">CPU%</th><th className="num">Mem%</th><th className="num">Restarts</th><th>Age</th></tr></thead><tbody>
          {rows.map(function(p, i) { return <tr key={i}><td className="mono txt-xs">{p.name || "—"}</td><td className="txt-xs">{p.node || "—"}</td><td>{p.role || "—"}</td><td className="num">{p.cpu != null ? p.cpu : "—"}</td><td className="num">{p.mem != null ? p.mem : "—"}</td><td className="num">{p.restarts != null ? p.restarts : "—"}</td><td className="txt-xs">{p.age || "—"}</td></tr>; })}
          {!rows.length && <tr><td colSpan="7" className="muted">No pod-level host metrics collected for this cluster.</td></tr>}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd">Top backends (process list)</div><div className="bd">
        <table className="tbl"><thead><tr><th>PID</th><th>User</th><th>DB</th><th>State</th><th>Wait</th><th>Query age</th></tr></thead><tbody>
          {sessions.slice(0, 12).map(function(s, i) { return <tr key={i}><td className="num">{s.pid || "—"}</td><td>{s.usename || s.user || "—"}</td><td>{s.datname || s.db || "—"}</td><td><span className={"pill " + rmPill(s.state)}>{s.state || "—"}</span></td><td className="txt-xs">{s.wait_event || "—"}</td><td className="txt-xs">{s.query_age_sec != null ? fmtSec(s.query_age_sec) : "—"}</td></tr>; })}
          {!sessions.length && <tr><td colSpan="6" className="muted">No active backend evidence.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Host metrics are derived from collected object-metrics and cluster metadata — no node shell access. Missing values render as '—'."/>
    </div>
  );
}

/* ---------- CC-20 Anomaly Detection & Proactive Insights ---------- */
function AnomaliesScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var metrics = [
    { key: "wal_bytes", label: "WAL generation" },
    { key: "storage_bytes", label: "Storage used" },
    { key: "replication_slot_wal_bytes", label: "Slot WAL retained" },
    { key: "cpu", label: "CPU" },
  ];
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all(metrics.map(function(m) {
      return rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: m.key, range: "30d", agg: "max" });
    })).then(function(r) { return { series: r }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Anomalies"/>;
  if (state.error) return <RmErrorPage title="Anomalies" error={state.error}/>;
  var rows = metrics.map(function(m, i) {
    var data = state.data.series[i].ok ? state.data.series[i].data : {};
    var points = data.points || [];
    var a = cpAnoms(points, 3);
    return { label: m.label, key: m.key, samples: a.n, anomalies: a.count, last: a.last, available: !!(data.available || points.length) };
  });
  var totalAnoms = rows.reduce(function(s, r) { return s + r.anomalies; }, 0);
  var withData = rows.filter(function(r) { return r.available; }).length;
  var findings = [];
  rows.forEach(function(r) { if (r.anomalies > 0) rmFinding(findings, "warning", r.label, r.anomalies + " anomalous sample(s) beyond 3σ", r.last ? ("last: " + rmDate(r.last[0])) : ""); });
  if (!withData) rmFinding(findings, "info", "Baseline", "No metric series available locally to baseline");
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={totalAnoms ? "orange" : "green"} label="Anomalies (30d)" value={fmtInt(totalAnoms)} sub="beyond 3σ baseline"/>
        <KPI color="blue" label="Metrics baselined" value={withData + "/" + rows.length} sub="with samples"/>
        <KPI color="blue" label="Method" value="z-score 3σ" sub="rolling mean/std"/>
        <KPI color={findings.filter(function(f){return f.severity==="warning";}).length ? "orange" : "green"} label="Proactive items" value={fmtInt(findings.length)} sub="review"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.TrendingUp size={15}/>Proactive insights</span><SourceBadge source="object-metrics series"/></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="card"><div className="hd">Per-metric baseline scan</div><div className="bd">
        <table className="tbl"><thead><tr><th>Metric</th><th className="num">Samples</th><th className="num">Anomalies</th><th>Last anomaly</th><th>Status</th></tr></thead><tbody>
          {rows.map(function(r, i) { return <tr key={i}><td><strong>{r.label}</strong></td><td className="num">{fmtInt(r.samples)}</td><td className="num">{fmtInt(r.anomalies)}</td><td className="mono txt-xs">{r.last ? rmDate(r.last[0]) : "—"}</td><td><span className={"pill " + (!r.available ? "muted" : r.anomalies ? "warn" : "ok")}>{!r.available ? "no data" : r.anomalies ? "anomalous" : "normal"}</span></td></tr>; })}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Anomaly scoring is computed in the browser over already-ingested series (no ML service, no external call). Sparse series report 'no data' rather than guessing."/>
    </div>
  );
}

/* ---------- CC-21 Visual EXPLAIN / Plan Explorer ---------- */
function PlanExplorerScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/topsql").then(function(r) { return { topsql: r }; });
  });
  var selState = React.useState(null);
  var sel = selState[0], setSel = selState[1];
  var planState = React.useState({ loading: false, data: null, error: null });
  var plan = planState[0], setPlan = planState[1];
  React.useEffect(function() {
    if (!sel) return undefined;
    var alive = true;
    setPlan({ loading: true, data: null, error: null });
    rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/plans/" + encodeURIComponent(sel)).then(function(r) {
      if (!alive) return;
      if (r.ok) setPlan({ loading: false, data: r.data, error: null });
      else setPlan({ loading: false, data: null, error: r.error });
    });
    return function() { alive = false; };
  }, [sel, clusterId, lastRefresh]);
  if (state.loading && !state.data) return <RmLoadPage title="Plan explorer"/>;
  if (state.error) return <RmErrorPage title="Plan explorer" error={state.error}/>;
  var topsql = state.data.topsql.ok ? state.data.topsql.data : {};
  var rows = topsql.top_sql || topsql.statements || topsql.rows || [];
  function qid(s) { return s.queryid || s.query_id || s.id; }
  var nodes = plan.data ? (plan.data.nodes || plan.data.plan_nodes || []) : [];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Tracked queries" value={fmtInt(rows.length)} sub={topsql.source || "pg_stat_statements"}/>
        <KPI color={sel ? "blue" : "muted"} label="Selected" value={sel ? "1" : "none"} sub="pick a query"/>
        <KPI color={plan.data ? "green" : "muted"} label="Plan captured" value={plan.data ? "yes" : "no"} sub="EXPLAIN disabled"/>
        <KPI color="muted" label="Node count" value={fmtInt(nodes.length)} sub="plan tree"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Top SQL — select to explain</div><div className="bd">
          <table className="tbl"><thead><tr><th>Query</th><th className="num">Calls</th><th></th></tr></thead><tbody>
            {rows.slice(0, 15).map(function(s, i) { var id = qid(s); return <tr key={i}><td className="mono txt-xs" title={s.query || s.query_text}>{String(s.query || s.query_text || "—").slice(0, 50)}</td><td className="num">{fmtInt(s.calls)}</td><td><button className="btn sm ghost" onClick={function() { setSel(id); }} disabled={!id}>Plan</button></td></tr>; })}
            {!rows.length && <tr><td colSpan="3" className="muted">No Top SQL evidence to explain.</td></tr>}
          </tbody></table>
        </div></div>
        <div className="card"><div className="hd"><span className="flex-row"><Icon.FileText size={15}/>Execution plan</span></div><div className="bd">
          {plan.loading && <div className="muted">Loading plan…</div>}
          {!plan.loading && !sel && <EmptyState icon={Icon.FileText} title="No query selected" hint="Pick a query on the left to view its captured plan tree."/>}
          {!plan.loading && sel && !nodes.length && <EmptyState icon={Icon.FileText} title="No plan captured" hint="No stored plan for this query. EXPLAIN execution stays disabled/guarded."/>}
          {nodes.length ? (
            <table className="tbl"><thead><tr><th>Node</th><th className="num">Est. cost</th><th className="num">Est. rows</th><th className="num">Actual rows</th></tr></thead><tbody>
              {nodes.slice(0, 30).map(function(n, i) { var slow = n.actual_rows != null && n.plan_rows != null && Number(n.actual_rows) > 10 * Number(n.plan_rows || 1); return <tr key={i} className={slow ? "row-warn" : ""}><td style={{paddingLeft: (8 + (n.depth || n.level || 0) * 14)}} className="txt-xs">{n.node_type || n.type || "node"}{slow ? " ⚠" : ""}</td><td className="num">{n.total_cost != null ? n.total_cost : "—"}</td><td className="num">{n.plan_rows != null ? fmtInt(n.plan_rows) : "—"}</td><td className="num">{n.actual_rows != null ? fmtInt(n.actual_rows) : "—"}</td></tr>; })}
            </tbody></table>
          ) : null}
        </div></div>
      </div>
      <CcSafetyNote text="Plans are read from captured plan history only; live EXPLAIN/EXPLAIN ANALYZE is not executed from this screen."/>
    </div>
  );
}

/* ---------- CC-22 Capacity Planning & Forecast ---------- */
function CapacityPlanningScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "storage_bytes", range: "30d", agg: "max" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "wal_bytes", range: "30d", agg: "max" }),
      rmFetch("/api/v1/lifecycle/scale/" + encodeURIComponent(clusterId)),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/parameters"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/sessions"),
    ]).then(function(r) { return { storage: r[0], wal: r[1], scale: r[2], params: r[3], sessions: r[4] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Capacity planning"/>;
  if (state.error) return <RmErrorPage title="Capacity planning" error={state.error}/>;
  var storage = state.data.storage.ok ? state.data.storage.data : {};
  var scale = state.data.scale.ok ? state.data.scale.data : {};
  var params = (state.data.params.ok ? (state.data.params.data.parameters || []) : []);
  var sessionPayload = state.data.sessions.ok ? state.data.sessions.data : {};
  var sessions = sessionPayload.sessions || [];
  var resources = (scale.current || {}).resources || {};
  var storageLimit = (resources.storage_gib ? resources.storage_gib : (cluster.totalStorageGiB || 0)) * 1024 * 1024 * 1024;
  var storagePts = storage.points || [];
  var maxConns = Number((params.filter(function(p){return p.name==="max_connections";})[0]||{}).setting || cluster.maxConns || 0);
  var curConns = sessions.length || cluster.activeConns || 0;
  var rows = [
    { res: "Storage", current: storagePts.length ? fmtBytes(storagePts[storagePts.length-1][1]) : "unknown", limit: storageLimit ? fmtBytes(storageLimit) : "unknown", eta: cpDays(cpExhaust(storagePts, storageLimit)) },
    { res: "Connections", current: fmtInt(curConns), limit: maxConns ? fmtInt(maxConns) : "unknown", eta: maxConns ? (curConns / maxConns > 0.8 ? "high usage" : "headroom ok") : "unknown" },
  ];
  var findings = [];
  if (storageLimit && storagePts.length) { var eta = cpExhaust(storagePts, storageLimit); if (eta != null && eta < 30 * 86400000) rmFinding(findings, "warning", "Storage", "Projected to reach provisioned limit within 30 days", "ETA: " + cpDays(eta)); }
  if (maxConns && curConns / maxConns > 0.8) rmFinding(findings, "warning", "Connections", "Connection usage above 80% of max_connections");
  if (!storagePts.length) rmFinding(findings, "info", "Forecast", "Storage series unavailable — projection limited");
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Storage now" value={storagePts.length ? fmtBytes(storagePts[storagePts.length-1][1]) : "unknown"} sub={storage.source_table || "30d series"}/>
        <KPI color="blue" label="Storage limit" value={storageLimit ? fmtBytes(storageLimit) : "unknown"} sub="provisioned"/>
        <KPI color="blue" label="Connections" value={maxConns ? curConns + "/" + maxConns : "unknown"} sub="usage"/>
        <KPI color={findings.length ? "orange" : "green"} label="Capacity risks" value={fmtInt(findings.length)} sub="forecast"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.TrendingUp size={15}/>Time-to-exhaustion forecast</span><SourceBadge source="object-metrics + lifecycle"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Resource</th><th>Current</th><th>Limit</th><th>Projection</th></tr></thead><tbody>
          {rows.map(function(r, i) { return <tr key={i}><td><strong>{r.res}</strong></td><td>{r.current}</td><td>{r.limit}</td><td>{r.eta}</td></tr>; })}
        </tbody></table>
        <div className="muted txt-xs mt-2">Projection is a linear extrapolation of the observed 30-day trend; treat as a planning signal, not a guarantee.</div>
      </div></div>
      <div className="card"><div className="hd">Capacity findings</div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <CcSafetyNote text="Forecasts are DBA planning signals derived from ingested series. Scaling actions stay in the guarded Lifecycle / Scaling flow."/>
    </div>
  );
}

/* ============================================================
   PHASE P3 — Data Protection & Lifecycle
   ============================================================ */

/* ---------- CC-23 Snapshot & Clone/Fork Catalog ---------- */
function SnapshotsScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups/schedules"),
    ]).then(function(r) { return { backups: r[0], schedules: r[1] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Snapshots"/>;
  if (state.error) return <RmErrorPage title="Snapshots" error={state.error}/>;
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var history = backups.history || backups.backups || [];
  var schedules = (state.data.schedules.ok ? (state.data.schedules.data.schedules || state.data.schedules.data.rows) : null) || backups.schedules || [];
  var full = history.filter(function(h) { return /full/i.test(h.type || h.backup_type || ""); });
  var repo = backups.repo || {};
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Backups" value={fmtInt(history.length)} sub="pgBackRest set"/>
        <KPI color={full.length ? "green" : "orange"} label="Full backups" value={fmtInt(full.length)} sub="restore anchors"/>
        <KPI color={schedules.length ? "green" : "orange"} label="Schedules" value={fmtInt(schedules.length)} sub="full/diff/incr"/>
        <KPI color={ccPresent(repo.bucket) ? "green" : "orange"} label="Repo" value={ccPresent(repo.bucket) ? "configured" : "unknown"} sub="bucket redacted"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.HardDrive size={15}/>Snapshot / backup catalog</span><SourceBadge source="pgBackRest"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>When</th><th>Type</th><th className="num">Size</th><th>Status</th></tr></thead><tbody>
          {history.slice(0, 15).map(function(h, i) { return <tr key={i}><td className="mono txt-xs">{rmDate(h.completed_at || h.timestamp || h.time)}</td><td>{h.type || h.backup_type || "—"}</td><td className="num">{h.size_bytes != null ? fmtBytes(h.size_bytes) : "—"}</td><td><span className={"pill " + rmPill(h.status || h.state || "ok")}>{h.status || h.state || "—"}</span></td></tr>; })}
          {!history.length && <tr><td colSpan="4" className="muted">No backup/snapshot evidence available.</td></tr>}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd">Clone / fork to PITR sandbox (preflight)</div><div className="bd">
        <div className="muted txt-xs">A fork creates an isolated copy of this cluster at a chosen restore point for testing — like Aurora clone / Crunchy fork / Neon branch. This screen previews readiness only; the actual clone runs through the guarded Backups → Clone approval job.</div>
        <table className="tbl mt-2"><thead><tr><th>Prerequisite</th><th>Status</th></tr></thead><tbody>
          <tr><td>Full backup anchor present</td><td><span className={"pill " + (full.length ? "ok" : "warn")}>{full.length ? "ready" : "missing"}</span></td></tr>
          <tr><td>WAL archive configured</td><td><span className={"pill " + ((backups.settings||{}).archive_mode === "on" ? "ok" : "warn")}>{(backups.settings||{}).archive_mode === "on" ? "ready" : "review"}</span></td></tr>
          <tr><td>Object-store repo configured</td><td><span className={"pill " + (ccPresent(repo.bucket) ? "ok" : "warn")}>{ccPresent(repo.bucket) ? "ready" : "review"}</span></td></tr>
        </tbody></table>
      </div></div>
      <CcSafetyNote text="No clone/fork is created here. Snapshot copy/restore/fork execute only via the existing guarded clone/PITR approval jobs."/>
    </div>
  );
}

/* ---------- CC-24 Encryption & Key Management Posture ---------- */
function EncryptionScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/tls"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/auth"),
    ]).then(function(r) { return { tls: r[0], backups: r[1], auth: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Encryption"/>;
  if (state.error) return <RmErrorPage title="Encryption" error={state.error}/>;
  var tls = state.data.tls.ok ? state.data.tls.data : {};
  var tlsSum = tls.summary || {};
  var backups = state.data.backups.ok ? state.data.backups.data : {};
  var settings = ccNormSettings((state.data.auth.ok ? state.data.auth.data : {}).settings || {});
  var repo = backups.repo || {};
  var rows = [
    { area: "In-transit (TLS)", status: settings.ssl === "on" ? "ok" : "warn", detail: "ssl=" + (settings.ssl || "unknown") },
    { area: "Server certificate", status: tlsSum.status === "ok" ? "ok" : (tlsSum.status ? "warn" : "muted"), detail: tlsSum.certificate_expiry || tlsSum.cert_expiry || tlsSum.expires_at ? ("expires " + rmDate(tlsSum.certificate_expiry || tlsSum.cert_expiry || tlsSum.expires_at)) : "expiry unknown" },
    { area: "At-rest (PVC / Ceph RBD)", status: "muted", detail: "encryption depends on ODF/Ceph StorageClass — verify on cluster" },
    { area: "Secret encryption (etcd)", status: "muted", detail: "OpenShift etcd encryption — verify cluster config" },
    { area: "pgBackRest repo cipher", status: ccPresent(repo.cipher_type || repo.cipher) ? "ok" : "muted", detail: ccPresent(repo.cipher_type || repo.cipher) ? "cipher configured" : "cipher presence unknown" },
  ];
  var findings = [];
  if (settings.ssl !== "on" && settings.ssl) rmFinding(findings, "critical", "TLS", "ssl is not enabled — traffic may be plaintext");
  if (tlsSum.cert_expiry && (Date.parse(tlsSum.cert_expiry) - Date.now()) < 30 * 86400000) rmFinding(findings, "warning", "Certificate", "Server certificate expires within 30 days");
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={settings.ssl === "on" ? "green" : "orange"} label="In-transit" value={settings.ssl || "unknown"} sub="TLS"/>
        <KPI color={tlsSum.status === "ok" ? "green" : "orange"} label="Cert posture" value={tlsSum.status || "unknown"} sub="server cert"/>
        <KPI color="muted" label="At-rest" value="verify" sub="Ceph/ODF + etcd"/>
        <KPI color={ccPresent(repo.cipher_type || repo.cipher) ? "green" : "muted"} label="Repo cipher" value={ccPresent(repo.cipher_type || repo.cipher) ? "configured" : "unknown"} sub="pgBackRest"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Shield size={15}/>Encryption &amp; key posture</span><SourceBadge source="pg_settings + TLS + backups"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Area</th><th>Status</th><th>Detail</th></tr></thead><tbody>
          {rows.map(function(r, i) { return <tr key={i}><td><strong>{r.area}</strong></td><td><span className={"pill " + r.status}>{r.status === "muted" ? "verify" : r.status === "ok" ? "ok" : "review"}</span></td><td className="txt-xs">{r.detail}</td></tr>; })}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd">Encryption findings</div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <CcSafetyNote text="No key, certificate private key, or cipher value is printed — presence and expiry metadata only. At-rest encryption is a cluster/ODF property to verify on OpenShift."/>
    </div>
  );
}

/* ---------- CC-25 Storage Autoscale & IOPS Policy ---------- */
function StorageAutoscaleScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/lifecycle/scale/" + encodeURIComponent(clusterId)),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "storage_bytes", range: "30d", agg: "max" }),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/metrics/series", { metric: "iops", range: "24h", agg: "avg" }),
    ]).then(function(r) { return { scale: r[0], storage: r[1], iops: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Storage autoscale"/>;
  if (state.error) return <RmErrorPage title="Storage autoscale" error={state.error}/>;
  var scale = state.data.scale.ok ? state.data.scale.data : {};
  var storage = state.data.storage.ok ? state.data.storage.data : {};
  var resources = (scale.current || {}).resources || {};
  var pts = storage.points || [];
  var latest = pts.length ? Number(pts[pts.length-1][1]) : 0;
  var limit = (resources.storage_gib || cluster.totalStorageGiB || 0) * 1024 * 1024 * 1024;
  var usedPct = limit ? Math.round((latest / limit) * 100) : null;
  var findings = [];
  if (usedPct != null && usedPct > 80) rmFinding(findings, "warning", "Autogrow", "Storage above 80% — recommend grow or enable autogrow headroom");
  if (!pts.length) rmFinding(findings, "info", "Series", "Storage series unavailable — autoscale signals limited");
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={usedPct != null && usedPct > 80 ? "orange" : "green"} label="Storage used" value={usedPct != null ? usedPct + "%" : "unknown"} sub={fmtBytes(latest)}/>
        <KPI color="blue" label="Provisioned" value={limit ? fmtBytes(limit) : "unknown"} sub="PVC capacity"/>
        <KPI color="muted" label="Autogrow" value="PGO-managed" sub="resize PVC via CR"/>
        <KPI color="blue" label="StorageClass" value={resources.storage_class || "ocs-storagecluster-ceph-rbd"} sub="ODF/Ceph RBD"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.HardDrive size={15}/>Storage autoscale signals</span><SourceBadge source="lifecycle + object-metrics"/></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="card"><div className="hd">Headroom &amp; policy</div><div className="bd"><div className="grid-3">
        <Stat label="Used %" value={usedPct != null ? usedPct + "%" : "unknown"} sub="of provisioned"/>
        <Stat label="Free" value={limit ? fmtBytes(Math.max(0, limit - latest)) : "unknown"} sub="remaining"/>
        <Stat label="30d samples" value={fmtInt(pts.length)} sub="trend basis"/>
        <Stat label="Next safe step" value={usedPct != null && usedPct > 80 ? "grow PVC" : "no action"} sub="review item"/>
        <Stat label="IOPS evidence" value={(state.data.iops.ok && (state.data.iops.data.points||[]).length) ? "available" : "not collected"} sub="provisioned class"/>
        <Stat label="Resize path" value="guarded job" sub="PGO CR patch (approval)"/>
      </div></div></div>
      <CcSafetyNote text="PVC resize / IOPS class changes are PGO CR operations and stay in the guarded Lifecycle / Scaling approval flow. This screen is advisory only."/>
    </div>
  );
}

/* ---------- CC-26 Read Replica & Promotion Workflow ---------- */
function ReplicaWorkflowScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/lifecycle/replicas/" + encodeURIComponent(clusterId)),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
      rmFetch("/api/v1/readiness"),
    ]).then(function(r) { return { replicas: r[0], topology: r[1], readiness: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Replica workflow"/>;
  if (state.error) return <RmErrorPage title="Replica workflow" error={state.error}/>;
  var replicas = state.data.replicas.ok ? state.data.replicas.data : {};
  var topology = state.data.topology.ok ? state.data.topology.data : {};
  var readiness = state.data.readiness.ok ? state.data.readiness.data : {};
  var members = topology.members || [];
  var ts = topology.summary || {};
  var standbys = members.filter(function(m) { return (m.role || "").toLowerCase().indexOf("lead") < 0 && (m.role || "").toLowerCase() !== "master"; });
  var checklist = [
    { item: "Cluster healthy", ok: ((readiness.summary || {}).status !== "critical") },
    { item: "At least one standby present", ok: standbys.length > 0 },
    { item: "Replication lag within bound", ok: ts.max_lag_bytes != null ? Number(ts.max_lag_bytes) < 16*1024*1024 : null },
    { item: "Sync standby available (for safe promote)", ok: members.some(function(m){ return /sync/i.test(m.sync_state || m.role || ""); }) },
  ];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Standbys" value={fmtInt(standbys.length)} sub="read replicas"/>
        <KPI color={ts.max_lag_bytes != null ? (Number(ts.max_lag_bytes) < 16*1024*1024 ? "green" : "orange") : "muted"} label="Max lag" value={ts.max_lag_bytes != null ? fmtBytes(ts.max_lag_bytes) : "unknown"} sub="streaming"/>
        <KPI color={replicas.max_replicas ? "blue" : "muted"} label="Replica slots" value={(replicas.current_replicas != null ? replicas.current_replicas : standbys.length) + "/" + (replicas.max_replicas || "?")} sub="current/max"/>
        <KPI color={((readiness.summary || {}).status === "critical") ? "red" : "green"} label="Readiness" value={(readiness.summary || {}).status || "unknown"} sub="promote gate"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Replica members</div><div className="bd">
          <table className="tbl"><thead><tr><th>Member</th><th>Role</th><th>State</th><th className="num">Lag</th></tr></thead><tbody>
            {standbys.map(function(m, i) { return <tr key={i}><td className="mono txt-xs">{m.name || "—"}</td><td>{m.role || "—"}</td><td><span className={"pill " + rmPill(m.state)}>{m.state || "unknown"}</span></td><td className="num">{m.lag_bytes != null ? fmtBytes(m.lag_bytes) : "—"}</td></tr>; })}
            {!standbys.length && <tr><td colSpan="4" className="muted">No standby members found.</td></tr>}
          </tbody></table>
        </div></div>
        <div className="card"><div className="hd">Promotion / add-replica checklist (preflight)</div><div className="bd">
          <table className="tbl"><thead><tr><th>Check</th><th>Status</th></tr></thead><tbody>
            {checklist.map(function(c, i) { return <tr key={i}><td>{c.item}</td><td><span className={"pill " + (c.ok === null ? "muted" : c.ok ? "ok" : "warn")}>{c.ok === null ? "unknown" : c.ok ? "ready" : "review"}</span></td></tr>; })}
          </tbody></table>
          <div className="muted txt-xs mt-2">DC1/DC2 + application impact: promotion changes the writer endpoint; the application reconnects via the primary service. Add/promote/decouple execute via guarded Lifecycle / HA jobs.</div>
        </div></div>
      </div>
      <CcSafetyNote text="No replica is added, promoted, or decoupled here. All actions route through the existing approval / job / audit framework."/>
    </div>
  );
}

/* ============================================================
   PHASE P4 — Operations Automation & Governance
   ============================================================ */

/* ---------- CC-27 Maintenance Window Scheduler ---------- */
function MaintenanceSchedulerScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/maintenance").then(function(r) { return { maintenance: r }; });
  });
  var formState = React.useState({ day: "Sunday", start: "01:00", duration: "2", tz: "UTC" });
  var form = formState[0], setForm = formState[1];
  if (state.loading && !state.data) return <RmLoadPage title="Maintenance scheduler"/>;
  if (state.error) return <RmErrorPage title="Maintenance scheduler" error={state.error}/>;
  var maintenance = state.data.maintenance.ok ? state.data.maintenance.data : {};
  var windows = maintenance.windows || maintenance.maintenance_windows || [];
  var blackouts = maintenance.blackout_windows || maintenance.blackouts || [];
  function upd(k, v) { setForm(Object.assign({}, form, (function(){ var o={}; o[k]=v; return o; })())); }
  var days = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={windows.length ? "green" : "orange"} label="Windows" value={fmtInt(windows.length)} sub="configured"/>
        <KPI color={blackouts.length ? "orange" : "muted"} label="Blackouts" value={fmtInt(blackouts.length)} sub="exclusions"/>
        <KPI color={maintenance.paused ? "orange" : "green"} label="Patroni pause" value={maintenance.paused ? "paused" : "running"} sub="maintenance mode"/>
        <KPI color="muted" label="Preview" value="client-side" sub="no live write"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Define window (preview)</div><div className="bd">
          <div className="grid-2">
            <label className="txt-xs muted">Day<select className="cluster-select" value={form.day} onChange={function(e){upd("day",e.target.value);}}>{days.map(function(d){return <option key={d} value={d}>{d}</option>;})}</select></label>
            <label className="txt-xs muted">Start (HH:MM)<input className="cluster-select" value={form.start} onChange={function(e){upd("start",e.target.value);}}/></label>
            <label className="txt-xs muted">Duration (h)<input className="cluster-select" value={form.duration} onChange={function(e){upd("duration",e.target.value);}}/></label>
            <label className="txt-xs muted">Timezone<input className="cluster-select" value={form.tz} onChange={function(e){upd("tz",e.target.value);}}/></label>
          </div>
          <div className="tile-error mt-2" style={{borderColor:"var(--border)"}}><Icon.Clock size={13}/><span>Window: <strong>{form.day} {form.start} {form.tz}</strong> for {form.duration}h. Cron preview: <code className="mono">0 {Number(String(form.start).split(":")[0]||0)} * * {days.indexOf(form.day)}</code></span></div>
          <div className="muted txt-xs mt-2">This previews the schedule only. Applying a window writes via the guarded maintenance/config validation job.</div>
        </div></div>
        <div className="card"><div className="hd">Configured windows &amp; exclusions</div><div className="bd">
          <table className="tbl"><thead><tr><th>Type</th><th>When</th><th>Notes</th></tr></thead><tbody>
            {windows.map(function(w, i) { return <tr key={"w"+i}><td><span className="pill ok">window</span></td><td className="txt-xs">{w.day || w.cron || rmDate(w.start)}</td><td className="txt-xs">{w.environment || w.cluster || ""}</td></tr>; })}
            {blackouts.map(function(b, i) { return <tr key={"b"+i}><td><span className="pill warn">blackout</span></td><td className="txt-xs">{rmDate(b.start)}</td><td className="txt-xs">{b.reason || ""}</td></tr>; })}
            {!windows.length && !blackouts.length && <tr><td colSpan="3" className="muted">No windows or exclusions configured yet.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <CcSafetyNote text="Scheduling is previewed in the browser. Persisting a window/exclusion routes through the guarded maintenance validation job; nothing is executed automatically."/>
    </div>
  );
}

/* ---------- CC-28 Alert Rule Builder & Action Groups ---------- */
function AlertRulesScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/alert-rules"),
      rmFetch("/api/v1/notifications/channels"),
      rmFetch("/api/v1/alerts", { cluster: clusterId }),
    ]).then(function(r) { return { rules: r[0], channels: r[1], alerts: r[2] }; });
  });
  var rule = React.useState({ metric: "replication_lag_bytes", op: ">", threshold: "16777216", severity: "warning" });
  var form = rule[0], setForm = rule[1];
  if (state.loading && !state.data) return <RmLoadPage title="Alert rules"/>;
  if (state.error) return <RmErrorPage title="Alert rules" error={state.error}/>;
  var rules = state.data.rules.ok ? (state.data.rules.data.rules || state.data.rules.data.alert_rules || []) : [];
  var channels = state.data.channels.ok ? (state.data.channels.data.channels || []) : [];
  var alerts = state.data.alerts.ok ? (state.data.alerts.data.alerts || []) : [];
  function upd(k, v) { setForm(Object.assign({}, form, (function(){ var o={}; o[k]=v; return o; })())); }
  var metrics = ["replication_lag_bytes","storage_used_pct","cpu_pct","active_connections","wal_archive_failures","backup_age_hours"];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Alert rules" value={fmtInt(rules.length)} sub="configured"/>
        <KPI color={channels.length ? "green" : "orange"} label="Action channels" value={fmtInt(channels.length)} sub="routing targets"/>
        <KPI color={alerts.length ? "red" : "green"} label="Active alerts" value={fmtInt(alerts.length)} sub="firing"/>
        <KPI color="muted" label="Builder" value="preview" sub="validate before save"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Rule builder (preview)</div><div className="bd">
          <div className="grid-2">
            <label className="txt-xs muted">Metric<select className="cluster-select" value={form.metric} onChange={function(e){upd("metric",e.target.value);}}>{metrics.map(function(m){return <option key={m} value={m}>{m}</option>;})}</select></label>
            <label className="txt-xs muted">Operator<select className="cluster-select" value={form.op} onChange={function(e){upd("op",e.target.value);}}><option>{">"}</option><option>{">="}</option><option>{"<"}</option><option>{"<="}</option></select></label>
            <label className="txt-xs muted">Threshold<input className="cluster-select" value={form.threshold} onChange={function(e){upd("threshold",e.target.value);}}/></label>
            <label className="txt-xs muted">Severity<select className="cluster-select" value={form.severity} onChange={function(e){upd("severity",e.target.value);}}><option>info</option><option>warning</option><option>critical</option></select></label>
          </div>
          <div className="tile-error mt-2" style={{borderColor:"var(--border)"}}><Icon.AlertTriangle size={13}/><span>Preview: when <code className="mono">{form.metric} {form.op} {form.threshold}</code> → <span className={"pill "+rmPill(form.severity)}>{form.severity}</span> routed to {channels.length || 0} channel(s).</span></div>
          <div className="muted txt-xs mt-2">Saving a rule posts to the existing alert-rules API; this preview does not page anyone.</div>
        </div></div>
        <div className="card"><div className="hd">Existing rules</div><div className="bd">
          <table className="tbl"><thead><tr><th>Rule</th><th>Severity</th><th>Enabled</th></tr></thead><tbody>
            {rules.slice(0, 12).map(function(r, i) { return <tr key={i}><td className="txt-xs">{r.name || r.metric || "rule"}</td><td><span className={"pill " + rmPill(r.severity)}>{r.severity || "—"}</span></td><td><span className={"pill " + (r.enabled !== false ? "ok" : "muted")}>{r.enabled !== false ? "yes" : "no"}</span></td></tr>; })}
            {!rules.length && <tr><td colSpan="3" className="muted">No alert rules configured.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <CcSafetyNote text="The builder previews thresholds and routing only. No notification is sent from this screen."/>
    </div>
  );
}

/* ---------- CC-29 Logs Explorer & Live Tail ---------- */
function LogsExplorerScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/pods").then(function(r) { return { pods: r }; });
  });
  var selState = React.useState(null);
  var sel = selState[0], setSel = selState[1];
  var qState = React.useState("");
  var q = qState[0], setQ = qState[1];
  var logState = React.useState({ loading: false, lines: [], error: null });
  var log = logState[0], setLog = logState[1];
  React.useEffect(function() {
    if (!sel) return undefined;
    var alive = true;
    setLog({ loading: true, lines: [], error: null });
    rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/pods/" + encodeURIComponent(sel) + "/logs/preview", { tail: 200 }).then(function(r) {
      if (!alive) return;
      if (r.ok) setLog({ loading: false, lines: (r.data.lines || (r.data.log ? String(r.data.log).split("\n") : [])), error: null });
      else setLog({ loading: false, lines: [], error: r.error });
    });
    return function() { alive = false; };
  }, [sel, clusterId, lastRefresh]);
  if (state.loading && !state.data) return <RmLoadPage title="Logs explorer"/>;
  if (state.error) return <RmErrorPage title="Logs explorer" error={state.error}/>;
  var pods = state.data.pods.ok ? (state.data.pods.data.pods || state.data.pods.data.items || []) : [];
  var filtered = (log.lines || []).filter(function(l) { return !q || String(l).toLowerCase().indexOf(q.toLowerCase()) >= 0; });
  var errLines = (log.lines || []).filter(function(l) { return /error|fatal|panic/i.test(String(l)); });
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Log sources" value={fmtInt(pods.length)} sub="pods"/>
        <KPI color={sel ? "blue" : "muted"} label="Selected" value={sel ? "1 pod" : "none"} sub="tail 200"/>
        <KPI color={errLines.length ? "orange" : "green"} label="Error lines" value={fmtInt(errLines.length)} sub="in tail"/>
        <KPI color="blue" label="Matching" value={fmtInt(filtered.length)} sub={q ? "filtered" : "all"}/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Log sources</div><div className="bd">
          <table className="tbl"><thead><tr><th>Pod</th><th></th></tr></thead><tbody>
            {pods.map(function(p, i) { return <tr key={i}><td className="mono txt-xs">{p.name || p}</td><td><button className="btn sm ghost" onClick={function(){ setSel(p.name || p); }}>Tail</button></td></tr>; })}
            {!pods.length && <tr><td colSpan="2" className="muted">No pod inventory available locally.</td></tr>}
          </tbody></table>
        </div></div>
        <div className="card"><div className="hd"><span className="flex-row"><Icon.FileText size={15}/>Log tail{sel ? " · " + sel : ""}</span>
          <input className="cluster-select" style={{marginLeft:"auto", maxWidth: 200}} placeholder="filter…" value={q} onChange={function(e){setQ(e.target.value);}}/>
        </div><div className="bd">
          {log.loading && <div className="muted">Loading…</div>}
          {!log.loading && !sel && <EmptyState icon={Icon.FileText} title="No source selected" hint="Pick a pod to tail its sanitized log preview."/>}
          {!log.loading && sel && (
            <div className="logbox" style={{maxHeight: 360, overflow: "auto"}}>
              {filtered.slice(0, 200).map(function(l, i) { return <div key={i} className={/error|fatal|panic/i.test(String(l)) ? "danger" : ""}>{String(l)}</div>; })}
              {!filtered.length && <div className="muted">No matching lines.</div>}
            </div>
          )}
        </div></div>
      </div>
      <CcSafetyNote text="Log previews are read-only and sanitized by the existing pod-log endpoint. Refresh re-tails; no raw secrets or customer data are surfaced."/>
    </div>
  );
}

/* ---------- CC-30 Tags, Ownership & Resource Organization ---------- */
function TagsScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return rmFetch("/api/v1/clusters").then(function(r) { return { clusters: r }; });
  });
  var tagState = React.useState(function() { return cpStore("tags", {}); });
  var tags = tagState[0], setTags = tagState[1];
  var editState = React.useState({ id: "", owner: "", app: "", env: "", cost: "" });
  var edit = editState[0], setEdit = editState[1];
  if (state.loading && !state.data) return <RmLoadPage title="Tags"/>;
  if (state.error) return <RmErrorPage title="Tags" error={state.error}/>;
  var clusters = state.data.clusters.ok ? (state.data.clusters.data.clusters || []) : [];
  function saveTag() {
    if (!edit.id) return;
    var next = Object.assign({}, tags); next[edit.id] = { owner: edit.owner, app: edit.app, env: edit.env, cost: edit.cost };
    setTags(next); cpSave("tags", next);
  }
  function updEdit(k, v) { setEdit(Object.assign({}, edit, (function(){ var o={}; o[k]=v; return o; })())); }
  var tagged = Object.keys(tags).length;
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Clusters" value={fmtInt(clusters.length)} sub="estate"/>
        <KPI color={tagged ? "green" : "orange"} label="Tagged" value={tagged + "/" + clusters.length} sub="owner/app/env"/>
        <KPI color="muted" label="Storage" value="local" sub="console-side metadata"/>
        <KPI color="blue" label="Cost centers" value={fmtInt(Object.keys((function(){var s={};Object.keys(tags).forEach(function(k){if(tags[k].cost)s[tags[k].cost]=1;});return s;})()).length)} sub="distinct"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Assign tags</div><div className="bd">
          <div className="grid-2">
            <label className="txt-xs muted">Cluster<select className="cluster-select" value={edit.id} onChange={function(e){updEdit("id",e.target.value);}}><option value="">— select —</option>{clusters.map(function(c){return <option key={c.id} value={c.id}>{c.name}</option>;})}</select></label>
            <label className="txt-xs muted">Owner<input className="cluster-select" value={edit.owner} onChange={function(e){updEdit("owner",e.target.value);}}/></label>
            <label className="txt-xs muted">Application<input className="cluster-select" value={edit.app} onChange={function(e){updEdit("app",e.target.value);}}/></label>
            <label className="txt-xs muted">Environment<input className="cluster-select" value={edit.env} onChange={function(e){updEdit("env",e.target.value);}}/></label>
            <label className="txt-xs muted">Cost center<input className="cluster-select" value={edit.cost} onChange={function(e){updEdit("cost",e.target.value);}}/></label>
          </div>
          <button className="btn sm primary mt-2" onClick={saveTag} disabled={!edit.id}>Save tags</button>
        </div></div>
        <div className="card"><div className="hd">Tagged estate</div><div className="bd">
          <table className="tbl"><thead><tr><th>Cluster</th><th>Owner</th><th>App</th><th>Env</th><th>Cost</th></tr></thead><tbody>
            {clusters.map(function(c) { var t = tags[c.id] || {}; return <tr key={c.id}><td className="mono txt-xs">{c.name}</td><td>{t.owner || "—"}</td><td>{t.app || "—"}</td><td>{t.env || "—"}</td><td>{t.cost || "—"}</td></tr>; })}
            {!clusters.length && <tr><td colSpan="5" className="muted">No clusters known to console.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <CcSafetyNote text="Tags are stored locally in the browser as console-side metadata (no server write). Use them to organize and filter the estate."/>
    </div>
  );
}

/* ============================================================
   PHASE P5 — Self-Service & Developer Experience
   ============================================================ */

/* ---------- CC-31 SQL Workbench ---------- */
function SqlWorkbenchScreen({ cluster, lastRefresh }) {
  var savedState = React.useState(function() { return cpStore("saved-sql", []); });
  var saved = savedState[0], setSaved = savedState[1];
  var nameState = React.useState("");
  var name = nameState[0], setName = nameState[1];
  var sqlState = React.useState("SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size\nFROM pg_database WHERE datistemplate = false\nORDER BY pg_database_size(datname) DESC;");
  var sql = sqlState[0], setSql = sqlState[1];
  function save() {
    if (!name.trim()) return;
    var next = saved.concat([{ name: name.trim(), sql: sql, at: new Date().toISOString() }]).slice(-50);
    setSaved(next); cpSave("saved-sql", next); setName("");
  }
  function load(item) { setSql(item.sql); }
  function del(idx) { var next = saved.filter(function(_, i){ return i !== idx; }); setSaved(next); cpSave("saved-sql", next); }
  function exportLib() {
    var blob = new Blob([JSON.stringify(saved, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob); var a = document.createElement("a"); a.href = url; a.download = "hbz-saved-sql-" + Date.now() + ".json"; a.click(); URL.revokeObjectURL(url);
  }
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Saved queries" value={fmtInt(saved.length)} sub="local library"/>
        <KPI color="muted" label="Execution" value="Live Connect" sub="DBA · read-only"/>
        <KPI color="green" label="Mode" value="read-only" sub="write SQL guarded"/>
        <KPI color="blue" label="Export" value="JSON" sub="library"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd"><span className="flex-row"><Icon.Terminal size={15}/>Editor</span>
          <span className="flex-row" style={{gap:6, marginLeft:"auto"}}>
            <input className="cluster-select" style={{maxWidth: 160}} placeholder="name to save…" value={name} onChange={function(e){setName(e.target.value);}}/>
            <button className="btn sm primary" onClick={save} disabled={!name.trim()}>Save</button>
            <button className="btn sm ghost" onClick={function(){ cpCopy(sql); }}>Copy</button>
          </span>
        </div><div className="bd">
          <textarea className="cluster-select" style={{width:"100%", minHeight: 180, fontFamily:"var(--mono, monospace)", fontSize: 12}} value={sql} onChange={function(e){setSql(e.target.value);}}/>
          <div className="muted txt-xs mt-2">Run queries from <strong>Database Administration → Live Connect</strong> (DBA role, read-only). This workbench manages your saved query library and history locally.</div>
        </div></div>
        <div className="card"><div className="hd"><span className="flex-row"><Icon.FileText size={15}/>Saved library</span><button className="btn sm ghost" style={{marginLeft:"auto"}} onClick={exportLib} disabled={!saved.length}><Icon.Download size={12}/> Export</button></div><div className="bd">
          <table className="tbl"><thead><tr><th>Name</th><th>Saved</th><th></th></tr></thead><tbody>
            {saved.slice().reverse().map(function(item, i) { var idx = saved.length - 1 - i; return <tr key={idx}><td><strong>{item.name}</strong></td><td className="mono txt-xs">{rmDate(item.at)}</td><td><button className="btn sm ghost" onClick={function(){ load(item); }}>Load</button> <button className="btn sm ghost" onClick={function(){ del(idx); }}><Icon.X size={12}/></button></td></tr>; })}
            {!saved.length && <tr><td colSpan="3" className="muted">No saved queries yet.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <CcSafetyNote text="The workbench stores queries locally and does not execute SQL itself. Execution is delegated to the guarded, read-only Live Connect DBA session."/>
    </div>
  );
}

/* ---------- CC-32 Cost Showback & Budgets ---------- */
function CostShowbackScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters"),
      rmFetch("/api/v1/lifecycle/scale/" + encodeURIComponent(clusterId)),
    ]).then(function(r) { return { clusters: r[0], scale: r[1] }; });
  });
  var budgetState = React.useState(function() { return cpStore("budget", ""); });
  var budget = budgetState[0], setBudget = budgetState[1];
  if (state.loading && !state.data) return <RmLoadPage title="Cost showback"/>;
  if (state.error) return <RmErrorPage title="Cost showback" error={state.error}/>;
  var clusters = state.data.clusters.ok ? (state.data.clusters.data.clusters || []) : [];
  // Cost proxy: weight cores + RAM + storage into an abstract "units" (NOT money).
  function units(c) { var cores = Number(c.cores || 16), ram = Number(c.ram_gib || c.ramGiB || 64), stg = Number(c.total_storage_gib || c.totalStorageGiB || 2048); return Math.round(cores * 10 + ram * 2 + stg * 0.05); }
  var rows = clusters.map(function(c) { return { name: c.name, role: c.role || c.label, region: c.region, units: units(c) }; });
  rows.sort(function(a, b) { return b.units - a.units; });
  var total = rows.reduce(function(s, r) { return s + r.units; }, 0);
  var byRegion = {};
  rows.forEach(function(r) { var k = (r.region || "unknown").split("·")[0].trim(); byRegion[k] = (byRegion[k] || 0) + r.units; });
  var regionRows = Object.keys(byRegion).map(function(k) { return { label: k, value: byRegion[k] }; });
  var over = budget && Number(budget) > 0 && total > Number(budget);
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Total cost proxy" value={fmtInt(total)} sub="abstract units"/>
        <KPI color="blue" label="Clusters" value={fmtInt(rows.length)} sub="estate"/>
        <KPI color={over ? "red" : "green"} label="Budget status" value={budget ? (over ? "over" : "within") : "unset"} sub={budget ? ("budget " + budget) : "set a budget"}/>
        <KPI color="muted" label="Source" value="proxy" sub="no real billing"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Cost proxy by region</div><div className="bd"><BarList rows={regionRows} valueFormatter={fmtInt} emptyText="No clusters to attribute."/></div></div>
        <div className="card"><div className="hd">Budget (advisory)</div><div className="bd">
          <label className="txt-xs muted">Monthly budget (cost-proxy units)<input className="cluster-select" value={budget} onChange={function(e){ setBudget(e.target.value); cpSave("budget", e.target.value); }} placeholder="e.g. 2000"/></label>
          <div className={"tile-error mt-2"} style={{borderColor:"var(--border)"}}><span className={"pill " + (over ? "danger" : budget ? "ok" : "muted")}><span className="dot"/>{budget ? (over ? "over budget" : "within budget") : "no budget set"}</span><span>Total {fmtInt(total)} vs budget {budget || "—"}.</span></div>
        </div></div>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Sliders size={15}/>Per-cluster showback</span><SourceBadge source="console clusters + lifecycle"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Cluster</th><th>Role</th><th>Region</th><th className="num">Cost proxy</th><th className="num">Share</th></tr></thead><tbody>
          {rows.map(function(r, i) { return <tr key={i}><td className="mono txt-xs">{r.name}</td><td>{r.role}</td><td className="txt-xs">{r.region}</td><td className="num">{fmtInt(r.units)}</td><td className="num">{total ? Math.round((r.units/total)*100) + "%" : "—"}</td></tr>; })}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="These are operational cost proxies (compute + memory + storage weighting), not currency. No real cloud billing source is connected. Budgets are advisory."/>
    </div>
  );
}

/* ---------- CC-33 Index & Auto-Tuning Loop ---------- */
function AutoTuningScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/advisor/parameters"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/index-advisor"),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
    ]).then(function(r) { return { advisor: r[0], indexes: r[1], jobs: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Auto-tuning"/>;
  if (state.error) return <RmErrorPage title="Auto-tuning" error={state.error}/>;
  var advisor = state.data.advisor.ok ? state.data.advisor.data : {};
  var indexes = state.data.indexes.ok ? state.data.indexes.data : {};
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var paramRecs = advisor.recommendations || advisor.parameters || [];
  var idxRecs = indexes.recommendations || indexes.indexes || [];
  var tuningJobs = jobs.filter(function(j) { return /param|index|tun|vacuum|reindex/i.test(j.kind || j.target || ""); });
  var steps = [];
  paramRecs.slice(0, 8).forEach(function(p) { steps.push({ kind: "parameter", target: p.name || p.parameter, rec: String(p.recommended != null ? p.recommended : p.suggested_value), state: "recommended" }); });
  idxRecs.slice(0, 8).forEach(function(ix) { steps.push({ kind: "index", target: (ix.table || ix.relname || "table") + (ix.columns ? "(" + ix.columns + ")" : ""), rec: ix.recommendation || (ix.unused ? "drop" : "create"), state: "recommended" }); });
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={paramRecs.length ? "orange" : "green"} label="Parameter tunables" value={fmtInt(paramRecs.length)} sub="advisor"/>
        <KPI color={idxRecs.length ? "orange" : "green"} label="Index actions" value={fmtInt(idxRecs.length)} sub="create/drop"/>
        <KPI color={tuningJobs.length ? "blue" : "muted"} label="Tuning jobs" value={fmtInt(tuningJobs.length)} sub="validated/applied"/>
        <KPI color="green" label="Apply mode" value="guarded" sub="approval-gated"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Sliders size={15}/>Tuning loop: recommend → validate → apply</span><SourceBadge source="advisor + index-advisor + jobs"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Kind</th><th>Target</th><th>Recommendation</th><th>Stage</th><th>Next safe step</th></tr></thead><tbody>
          {steps.map(function(s, i) { return <tr key={i}><td>{s.kind}</td><td className="mono txt-xs">{s.target}</td><td>{s.rec}</td><td><span className="pill warn">{s.state}</span></td><td className="txt-xs muted">{s.kind === "parameter" ? "Validate in Advisor (dry-run job)" : "Review in Index Advisor (dry-run job)"}</td></tr>; })}
          {!steps.length && <tr><td colSpan="5" className="muted">No tuning recommendations from current evidence.</td></tr>}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd">Tuning history (jobs)</div><div className="bd">
        <table className="tbl"><thead><tr><th>Submitted</th><th>Kind</th><th>State</th></tr></thead><tbody>
          {tuningJobs.slice(0, 12).map(function(j) { return <tr key={j.id}><td className="mono txt-xs">{rmDate(j.submitted_at)}</td><td>{j.kind}</td><td><span className={"pill " + rmPill(j.state)}>{j.state}</span></td></tr>; })}
          {!tuningJobs.length && <tr><td colSpan="3" className="muted">No tuning jobs recorded.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="The loop never auto-applies. Each recommendation links to the existing guarded validate → approve → apply job flow, with job history as the rollback/audit trail."/>
    </div>
  );
}

/* ---------- CC-34 Extension & Preload Governance ---------- */
function ExtensionGovernanceScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var db = "uat_object_metrics";
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/databases/" + encodeURIComponent(db) + "/extensions"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/parameters"),
    ]).then(function(r) { return { ext: r[0], params: r[1] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Extension governance"/>;
  if (state.error) return <RmErrorPage title="Extension governance" error={state.error}/>;
  var ext = state.data.ext.ok ? state.data.ext.data : {};
  var installed = ext.installed || ext.extensions || [];
  var params = (state.data.params.ok ? (state.data.params.data.parameters || []) : []);
  var preload = (params.filter(function(p){return p.name==="shared_preload_libraries";})[0]||{}).setting || "";
  var preloadList = String(preload).split(",").map(function(s){return s.trim();}).filter(Boolean);
  var allowlist = ["pg_stat_statements","pgaudit","pg_cron","pgvector","vector","pg_partman","postgis","pg_trgm","btree_gin","hypopg"];
  var notAllowed = installed.filter(function(e) { return allowlist.indexOf((e.name || e.extname || "").toLowerCase()) < 0; });
  var hasVector = installed.some(function(e){ return /vector/i.test(e.name || e.extname || ""); });
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Installed" value={fmtInt(installed.length)} sub={"db " + db}/>
        <KPI color="blue" label="Preloaded libs" value={fmtInt(preloadList.length)} sub="shared_preload_libraries"/>
        <KPI color={notAllowed.length ? "orange" : "green"} label="Off-allowlist" value={fmtInt(notAllowed.length)} sub="governance"/>
        <KPI color={hasVector ? "green" : "muted"} label="pgvector / AI" value={hasVector ? "ready" : "absent"} sub="vector readiness"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Installed extensions</div><div className="bd">
          <table className="tbl"><thead><tr><th>Extension</th><th>Version</th><th>Allowlist</th></tr></thead><tbody>
            {installed.slice(0, 20).map(function(e, i) { var nm = (e.name || e.extname || "").toLowerCase(); return <tr key={i}><td className="mono txt-xs">{e.name || e.extname}</td><td className="txt-xs">{e.version || e.extversion || "—"}</td><td><span className={"pill " + (allowlist.indexOf(nm) >= 0 ? "ok" : "warn")}>{allowlist.indexOf(nm) >= 0 ? "allowed" : "review"}</span></td></tr>; })}
            {!installed.length && <tr><td colSpan="3" className="muted">No extension evidence for this database.</td></tr>}
          </tbody></table>
        </div></div>
        <div className="card"><div className="hd">Preload libraries &amp; AI readiness</div><div className="bd">
          <table className="tbl"><thead><tr><th>shared_preload_libraries</th></tr></thead><tbody>
            {preloadList.map(function(p, i) { return <tr key={i}><td className="mono txt-xs">{p}</td></tr>; })}
            {!preloadList.length && <tr><td className="muted">None / not collected.</td></tr>}
          </tbody></table>
          <div className="tile-error mt-2" style={{borderColor:"var(--border)"}}><Icon.Database size={13}/><span>pgvector / AI readiness: <span className={"pill " + (hasVector ? "ok" : "muted")}>{hasVector ? "vector extension present" : "no vector extension"}</span></span></div>
        </div></div>
      </div>
      <CcSafetyNote text="Install/preload changes validate as dry-run jobs in the existing Extensions / Configuration flows. The allowlist here is governance guidance, not an enforcement action."/>
    </div>
  );
}

/* ============================================================
   PHASE P6 — Platform & Enterprise Integration
   ============================================================ */

/* ---------- CC-35 Platform & Provider Service Health ---------- */
function PlatformHealthScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/readiness"),
      rmFetch("/api/v1/clusters"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/findings", { status: "all" }),
    ]).then(function(r) { return { readiness: r[0], clusters: r[1], findings: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Platform health"/>;
  if (state.error) return <RmErrorPage title="Platform health" error={state.error}/>;
  var readiness = state.data.readiness.ok ? state.data.readiness.data : {};
  var clusters = state.data.clusters.ok ? (state.data.clusters.data.clusters || []) : [];
  var findings = state.data.findings.ok ? (state.data.findings.data.findings || []) : [];
  var checks = readiness.items || readiness.checks || [];
  // Map readiness checks into platform layers.
  function layer(keys, label) {
    var c = checks.filter(function(x){ return keys.indexOf(x.key) >= 0; });
    var bad = c.filter(function(x){ return x.status && x.status !== "ok"; });
    return { label: label, status: !c.length ? "muted" : bad.length ? (bad.some(function(x){return x.status==="critical";}) ? "critical" : "warning") : "ok", detail: c.map(function(x){return x.label;}).join(", ") || "no evidence" };
  }
  var layers = [
    layer(["kubernetes"], "OpenShift API / nodes"),
    layer(["pgbackrest","storage"], "ODF / Ceph / object store"),
    layer(["database","patroni"], "PostgreSQL / Patroni"),
    layer(["prometheus"], "Monitoring / Prometheus"),
    layer(["remote_agents","ingest"], "Remote agents / ingest"),
  ];
  var degraded = layers.filter(function(l){ return l.status === "warning" || l.status === "critical"; }).length;
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={((readiness.summary || {}).status === "critical") ? "red" : ((readiness.summary || {}).status === "ok") ? "green" : "orange"} label="Platform status" value={(readiness.summary || {}).status || "unknown"} sub="aggregate"/>
        <KPI color={degraded ? "orange" : "green"} label="Degraded layers" value={fmtInt(degraded)} sub={"of " + layers.length}/>
        <KPI color="blue" label="Clusters" value={fmtInt(clusters.length)} sub="estate"/>
        <KPI color={findings.length ? "orange" : "green"} label="Open findings" value={fmtInt(findings.length)} sub="collector"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Shield size={15}/>Platform layer health</span><SourceBadge source="readiness + collector"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Layer</th><th>Status</th><th>Evidence</th></tr></thead><tbody>
          {layers.map(function(l, i) { return <tr key={i}><td><strong>{l.label}</strong></td><td><span className={"pill " + (l.status === "muted" ? "muted" : rmPill(l.status))}>{l.status === "muted" ? "no evidence" : l.status}</span></td><td className="txt-xs">{l.detail}</td></tr>; })}
        </tbody></table>
      </div></div>
      <div className="card"><div className="hd">Recent platform findings</div><div className="bd">
        <table className="tbl"><thead><tr><th>Severity</th><th>Finding</th><th>Last seen</th></tr></thead><tbody>
          {findings.slice(0, 12).map(function(f, i) { return <tr key={i}><td><span className={"pill " + rmPill(f.severity)}>{f.severity}</span></td><td><strong>{f.title}</strong><div className="muted txt-xs">{f.detail}</div></td><td className="mono txt-xs">{rmDate(f.last_seen_at)}</td></tr>; })}
          {!findings.length && <tr><td colSpan="3" className="muted">No platform findings.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Platform health is aggregated from read-only readiness checks and collector findings. No live OpenShift/ODF command is run."/>
    </div>
  );
}

/* ---------- CC-36 Quotas & Limits ---------- */
function QuotasScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/config/parameters"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/perf/sessions"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
    ]).then(function(r) { return { params: r[0], sessions: r[1], topology: r[2] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Quotas"/>;
  if (state.error) return <RmErrorPage title="Quotas" error={state.error}/>;
  var params = (state.data.params.ok ? (state.data.params.data.parameters || []) : []);
  var sessions = state.data.sessions.ok ? (state.data.sessions.data.sessions || []) : [];
  var ts = (state.data.topology.ok ? state.data.topology.data : {}).summary || {};
  function pv(n) { var p = params.filter(function(x){return x.name===n;})[0]; return p ? Number(p.setting) : null; }
  var rows = [
    { res: "Connections", used: (sessionPayload.summary || {}).total != null ? sessionPayload.summary.total : (cluster.activeConns != null ? cluster.activeConns : null), limit: pv("max_connections") || cluster.maxConns },
    { res: "Replication slots", used: ts.replication_slots != null ? ts.replication_slots : null, limit: pv("max_replication_slots") },
    { res: "WAL senders", used: ts.wal_senders != null ? ts.wal_senders : null, limit: pv("max_wal_senders") },
    { res: "Worker processes", used: null, limit: pv("max_worker_processes") },
    { res: "Prepared transactions", used: null, limit: pv("max_prepared_transactions") },
    { res: "Logical workers", used: ts.logical_workers != null ? ts.logical_workers : null, limit: pv("max_logical_replication_workers") },
  ];
  var atRisk = rows.filter(function(r){ return r.used != null && r.limit && r.used / r.limit > 0.8; }).length;
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Tracked limits" value={fmtInt(rows.length)} sub="server-level"/>
        <KPI color={atRisk ? "orange" : "green"} label="Near limit (>80%)" value={fmtInt(atRisk)} sub="headroom risk"/>
        <KPI color="blue" label="max_connections" value={pv("max_connections") || "unknown"} sub="ceiling"/>
        <KPI color="blue" label="max_repl_slots" value={pv("max_replication_slots") || "unknown"} sub="ceiling"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Sliders size={15}/>Quotas &amp; limits</span><SourceBadge source="pg_settings + activity + topology"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>Resource</th><th className="num">Used</th><th className="num">Limit</th><th className="num">Headroom</th><th>Status</th></tr></thead><tbody>
          {rows.map(function(r, i) { var pct = (r.used != null && r.limit) ? Math.round((r.used/r.limit)*100) : null; return <tr key={i}><td><strong>{r.res}</strong></td><td className="num">{r.used != null ? fmtInt(r.used) : "—"}</td><td className="num">{r.limit != null ? fmtInt(r.limit) : "unknown"}</td><td className="num">{pct != null ? (100-pct) + "%" : "—"}</td><td><span className={"pill " + (pct == null ? "muted" : pct > 80 ? "warn" : "ok")}>{pct == null ? "unknown" : pct > 80 ? "near limit" : "ok"}</span></td></tr>; })}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Limits read from pg_settings; usage from live activity/topology where available. Unknown usage renders as '—' rather than assuming zero headroom."/>
    </div>
  );
}

/* ---------- CC-37 Audit/Event Streaming & SIEM Export ---------- */
function EventStreamingScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/audit", { cluster: clusterId, limit: 50 }),
      rmFetch("/api/v1/notifications/channels"),
    ]).then(function(r) { return { audit: r[0], channels: r[1] }; });
  });
  var cfgState = React.useState(function() { return cpStore("siem", { sink: "webhook", format: "json", target: "" }); });
  var cfg = cfgState[0], setCfg = cfgState[1];
  if (state.loading && !state.data) return <RmLoadPage title="Event streaming"/>;
  if (state.error) return <RmErrorPage title="Event streaming" error={state.error}/>;
  var audit = state.data.audit.ok ? (state.data.audit.data.audit || []) : [];
  var channels = state.data.channels.ok ? (state.data.channels.data.channels || []) : [];
  function upd(k, v) { var n = Object.assign({}, cfg, (function(){var o={};o[k]=v;return o;})()); setCfg(n); cpSave("siem", n); }
  var sample = audit.slice(0, 3).map(function(a) { return { ts: a.created_at || a.time, actor: a.actor || a.user || "—", action: a.action || a.event, target: ccRedact(a.target || ""), result: a.result || a.status || "ok" }; });
  function exportSample() {
    var blob = new Blob([JSON.stringify({ stream: cfg, sample: sample, redaction: "secrets/endpoints redacted" }, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob); var a = document.createElement("a"); a.href = url; a.download = "hbz-siem-sample-" + Date.now() + ".json"; a.click(); URL.revokeObjectURL(url);
  }
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Audit events" value={fmtInt(audit.length)} sub="recent"/>
        <KPI color={channels.length ? "green" : "orange"} label="Channels" value={fmtInt(channels.length)} sub="possible sinks"/>
        <KPI color="muted" label="Sink" value={cfg.sink} sub={cfg.format}/>
        <KPI color="green" label="Redaction" value="enabled" sub="local preview"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Streaming config (preview)</div><div className="bd">
          <div className="grid-2">
            <label className="txt-xs muted">Sink<select className="cluster-select" value={cfg.sink} onChange={function(e){upd("sink",e.target.value);}}><option>webhook</option><option>file</option><option>syslog</option></select></label>
            <label className="txt-xs muted">Format<select className="cluster-select" value={cfg.format} onChange={function(e){upd("format",e.target.value);}}><option>json</option><option>cef</option><option>ndjson</option></select></label>
            <label className="txt-xs muted">Target (redacted)<input className="cluster-select" value={cfg.target} onChange={function(e){upd("target",e.target.value);}} placeholder="siem.internal/ingest"/></label>
          </div>
          <button className="btn sm ghost mt-2" onClick={exportSample}><Icon.Download size={12}/> Export redacted sample</button>
          <div className="muted txt-xs mt-2">Config is stored locally and previewed only. No live outbound stream is opened without explicit approval.</div>
        </div></div>
        <div className="card"><div className="hd">Redacted event preview</div><div className="bd">
          <table className="tbl"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Result</th></tr></thead><tbody>
            {sample.map(function(s, i) { return <tr key={i}><td className="mono txt-xs">{rmDate(s.ts)}</td><td>{s.actor}</td><td className="txt-xs">{s.action}</td><td><span className={"pill " + rmPill(s.result)}>{s.result}</span></td></tr>; })}
            {!sample.length && <tr><td colSpan="4" className="muted">No audit events to preview.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <CcSafetyNote text="Event payloads are redacted (no tokens, passwords, keys, or endpoints). Export is local; no external upload occurs without separate approval."/>
    </div>
  );
}

/* ---------- CC-38 Access Recertification & Console RBAC ---------- */
function AccessReviewScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/roles"),
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/privileges"),
    ]).then(function(r) { return { roles: r[0], privs: r[1] }; });
  });
  var attestState = React.useState(function() { return cpStore("attest", {}); });
  var attest = attestState[0], setAttest = attestState[1];
  if (state.loading && !state.data) return <RmLoadPage title="Access review"/>;
  if (state.error) return <RmErrorPage title="Access review" error={state.error}/>;
  var roles = state.data.roles.ok ? (state.data.roles.data.roles || []) : [];
  var privs = state.data.privs.ok ? (state.data.privs.data.privileges || state.data.privs.data.rows || []) : [];
  var superusers = roles.filter(function(r){ return r.superuser || r.rolsuper; });
  function setA(name, val) { var n = Object.assign({}, attest, (function(){var o={};o[name]=val;return o;})()); setAttest(n); cpSave("attest", n); }
  var reviewed = Object.keys(attest).filter(function(k){ return attest[k]; }).length;
  var findings = [];
  if (superusers.length) rmFinding(findings, "warning", "Superusers", superusers.length + " role(s) have superuser", "Recertify superuser access periodically");
  if (!roles.length) rmFinding(findings, "info", "Roles", "No role evidence available locally");
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Roles" value={fmtInt(roles.length)} sub="pg_roles"/>
        <KPI color={superusers.length ? "orange" : "green"} label="Superusers" value={fmtInt(superusers.length)} sub="high privilege"/>
        <KPI color="blue" label="Privilege grants" value={fmtInt(privs.length)} sub="matrix rows"/>
        <KPI color={reviewed ? "green" : "orange"} label="Attested" value={reviewed + "/" + roles.length} sub="recertified"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.Shield size={15}/>Recertification findings</span><SourceBadge source="pg_roles + privileges"/></div><div className="bd"><RmFindingTable rows={findings}/></div></div>
      <div className="card"><div className="hd">Role recertification</div><div className="bd">
        <table className="tbl"><thead><tr><th>Role</th><th>Superuser</th><th>Login</th><th>Attestation</th></tr></thead><tbody>
          {roles.slice(0, 25).map(function(r, i) { var nm = r.name || r.rolname; var a = attest[nm]; return <tr key={i}><td className="mono txt-xs">{nm}</td><td><span className={"pill " + ((r.superuser||r.rolsuper) ? "warn" : "ok")}>{(r.superuser||r.rolsuper) ? "yes" : "no"}</span></td><td>{(r.login||r.rolcanlogin) ? "yes" : "no"}</td><td>{a ? <span className="pill ok">attested</span> : <span className="flex-row" style={{gap:4}}><button className="btn sm ghost" onClick={function(){ setA(nm, "approved"); }}>Approve</button><button className="btn sm ghost" onClick={function(){ setA(nm, "flagged"); }}>Flag</button></span>}</td></tr>; })}
          {!roles.length && <tr><td colSpan="4" className="muted">No role evidence available.</td></tr>}
        </tbody></table>
      </div></div>
      <CcSafetyNote text="Attestations are stored locally as a review aid. Actual revokes/grants route to the existing guarded privilege validation jobs — no privilege change is made here."/>
    </div>
  );
}

/* ---------- CC-39 Data Migration & Logical-Replication Wizard ---------- */
function MigrationWizardScreen({ cluster, lastRefresh }) {
  var clusterId = rmClusterId(cluster);
  var state = useRmPayload(lastRefresh, [clusterId], function() {
    return Promise.all([
      rmFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/logical"),
      rmFetch("/api/v1/jobs", { cluster: clusterId, limit: 50 }),
    ]).then(function(r) { return { logical: r[0], jobs: r[1] }; });
  });
  if (state.loading && !state.data) return <RmLoadPage title="Migration wizard"/>;
  if (state.error) return <RmErrorPage title="Migration wizard" error={state.error}/>;
  var logical = state.data.logical.ok ? state.data.logical.data : {};
  var pubs = logical.publications || [];
  var subs = logical.subscriptions || [];
  var slots = logical.slots || logical.replication_slots || [];
  var jobs = state.data.jobs.ok ? (state.data.jobs.data.jobs || []) : [];
  var migJobs = jobs.filter(function(j){ return /migrat|logical|subscr|publicat|dump|restore|cutover/i.test(j.kind || j.target || ""); });
  var steps = [
    { n: 1, step: "Inventory source objects", status: pubs.length || subs.length ? "in-evidence" : "todo", detail: pubs.length + " publication(s), " + subs.length + " subscription(s)" },
    { n: 2, step: "Create publication on source", status: pubs.length ? "present" : "todo", detail: "guarded DDL job" },
    { n: 3, step: "Create slot + subscription on target", status: subs.length ? "present" : "todo", detail: slots.length + " slot(s)" },
    { n: 4, step: "Monitor initial copy + lag", status: subs.length ? "monitor" : "todo", detail: "table copy / catchup" },
    { n: 5, step: "Cutover (stop writes → verify → switch)", status: "todo", detail: "4-eyes approval" },
  ];
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color="blue" label="Publications" value={fmtInt(pubs.length)} sub="source side"/>
        <KPI color="blue" label="Subscriptions" value={fmtInt(subs.length)} sub="target side"/>
        <KPI color="blue" label="Logical slots" value={fmtInt(slots.length)} sub="WAL retention"/>
        <KPI color={migJobs.length ? "blue" : "muted"} label="Migration jobs" value={fmtInt(migJobs.length)} sub="dry-run/approval"/>
      </div>
      <div className="card"><div className="hd"><span className="flex-row"><Icon.GitBranch size={15}/>Logical replication / migration wizard (preflight)</span><SourceBadge source="logical replication + jobs"/></div><div className="bd">
        <table className="tbl"><thead><tr><th>#</th><th>Step</th><th>Status</th><th>Detail</th></tr></thead><tbody>
          {steps.map(function(s) { return <tr key={s.n}><td className="num">{s.n}</td><td><strong>{s.step}</strong></td><td><span className={"pill " + (s.status === "present" || s.status === "in-evidence" ? "ok" : s.status === "monitor" ? "info" : "muted")}>{s.status}</span></td><td className="txt-xs">{s.detail}</td></tr>; })}
        </tbody></table>
        <div className="muted txt-xs mt-2">Reuses the region pub/sub/slot SOP patterns. All DDL and cutover steps execute only through guarded approval jobs.</div>
      </div></div>
      <div className="grid-2">
        <div className="card"><div className="hd">Subscriptions</div><div className="bd">
          <table className="tbl"><thead><tr><th>Name</th><th>Enabled</th><th className="num">Lag</th></tr></thead><tbody>
            {subs.slice(0, 10).map(function(s, i) { return <tr key={i}><td className="mono txt-xs">{s.subname || s.name || "—"}</td><td><span className={"pill " + (s.enabled !== false ? "ok" : "muted")}>{s.enabled !== false ? "yes" : "no"}</span></td><td className="num">{s.lag_bytes != null ? fmtBytes(s.lag_bytes) : "—"}</td></tr>; })}
            {!subs.length && <tr><td colSpan="3" className="muted">No subscriptions.</td></tr>}
          </tbody></table>
        </div></div>
        <div className="card"><div className="hd">Migration jobs</div><div className="bd">
          <table className="tbl"><thead><tr><th>Submitted</th><th>Kind</th><th>State</th></tr></thead><tbody>
            {migJobs.slice(0, 10).map(function(j) { return <tr key={j.id}><td className="mono txt-xs">{rmDate(j.submitted_at)}</td><td>{j.kind}</td><td><span className={"pill " + rmPill(j.state)}>{j.state}</span></td></tr>; })}
            {!migJobs.length && <tr><td colSpan="3" className="muted">No migration jobs recorded.</td></tr>}
          </tbody></table>
        </div></div>
      </div>
      <CcSafetyNote text="The wizard is preflight/checklist only. Publication/subscription/slot DDL, dump/restore, and cutover all run through the existing guarded job framework."/>
    </div>
  );
}

/* ===================== Exports ===================== */
Object.assign(window, {
  ConnectHubScreen: ConnectHubScreen,
  EndpointsScreen: EndpointsScreen,
  AccessRulesScreen: AccessRulesScreen,
  HostMonitoringScreen: HostMonitoringScreen,
  AnomaliesScreen: AnomaliesScreen,
  PlanExplorerScreen: PlanExplorerScreen,
  CapacityPlanningScreen: CapacityPlanningScreen,
  SnapshotsScreen: SnapshotsScreen,
  EncryptionScreen: EncryptionScreen,
  StorageAutoscaleScreen: StorageAutoscaleScreen,
  ReplicaWorkflowScreen: ReplicaWorkflowScreen,
  MaintenanceSchedulerScreen: MaintenanceSchedulerScreen,
  AlertRulesScreen: AlertRulesScreen,
  LogsExplorerScreen: LogsExplorerScreen,
  TagsScreen: TagsScreen,
  SqlWorkbenchScreen: SqlWorkbenchScreen,
  CostShowbackScreen: CostShowbackScreen,
  AutoTuningScreen: AutoTuningScreen,
  ExtensionGovernanceScreen: ExtensionGovernanceScreen,
  PlatformHealthScreen: PlatformHealthScreen,
  QuotasScreen: QuotasScreen,
  EventStreamingScreen: EventStreamingScreen,
  AccessReviewScreen: AccessReviewScreen,
  MigrationWizardScreen: MigrationWizardScreen,
});
