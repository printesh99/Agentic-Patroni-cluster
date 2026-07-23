// Main App - HBZ theme with selected environment
const { useState: useS, useEffect: useE, useMemo: useM } = React;

function parseConsoleLocation() {
  var out = { route: "overview", clusterId: "uat", timeRange: "24h" };
  try {
    var params = new URLSearchParams(window.location.search || "");
    var path = (window.location.pathname || "").replace(/^\/+|\/+$/g, "");
    var parts = path.split("/").filter(Boolean);
    if (parts[0] === "clusters" && parts[1]) {
      out.clusterId = parts[1];
      if (parts[2]) out.route = parts.slice(2).join("_");
    } else if (parts[0] === "console" && parts[1]) {
      out.route = parts.slice(1).join("_");
    } else if (params.get("route")) {
      out.route = params.get("route");
    }
    if (params.get("cluster")) out.clusterId = params.get("cluster");
    if (params.get("range")) out.timeRange = params.get("range");
  } catch (e) {}
  if (!CLUSTERS[out.clusterId]) out.clusterId = "uat";
  if (!/^[a-z0-9_]+$/.test(out.route || "")) out.route = "overview";
  if (["1h", "24h", "7d", "30d"].indexOf(out.timeRange) < 0) out.timeRange = "24h";
  return out;
}

function routeToPath(clusterId, route, timeRange) {
  var params = new URLSearchParams();
  if (timeRange && timeRange !== "24h") params.set("range", timeRange);
  var path = "/clusters/" + encodeURIComponent(clusterId || "uat") + "/" + encodeURIComponent(route || "overview");
  var query = params.toString();
  return path + (query ? "?" + query : "");
}

function App() {
  const initialLocation = useM(function() { return parseConsoleLocation(); }, []);
  const [railOpen, setRailOpen] = useS(true);
  const [route,    setRoute]    = useS(initialLocation.route);
  const [timeRange,setTimeRange]= useS(initialLocation.timeRange);
  const [lastRefresh, setLastRefresh] = useS(Date.now());
  const [run, setRun] = useS(null);
  const [me, setMe] = useS(null);
  const [clusterId, setClusterId] = useS(function() {
    try { return initialLocation.clusterId || localStorage.getItem("hbz-active-cluster") || "uat"; } catch (e) { return initialLocation.clusterId || "uat"; }
  });

  const [theme, setTheme] = useS(function() {
    try { return localStorage.getItem("hbz-theme") || "light"; } catch (e) { return "light"; }
  });
  const [paletteOpen, setPaletteOpen] = useS(false);
  const toastApi = useToasts();
  const toasts = toastApi[0], pushToast = toastApi[1], dismissToast = toastApi[2];
  const [clusterCatalogVersion, setClusterCatalogVersion] = useS(0);

  const cluster = CLUSTERS[clusterId] || CLUSTERS["uat"];
  window.ACTIVE_CLUSTER_ID = cluster.id;

  useE(function() {
    var alive = true;
    function normalizeCluster(row) {
      var id = row.id || row.cluster_id || row.name || row.cluster_name || "live";
      var role = row.role || row.label || "LIVE";
      return Object.assign({}, CLUSTERS[id] || {}, row, {
        id: id,
        name: row.name || row.cluster_name || id,
        label: row.label || role,
        role: role,
        namespace: row.namespace || "",
        region: row.region || row.namespace || "OpenShift live",
        pgVersion: row.pgVersion || row.pg_version || "PostgreSQL",
        ramGiB: row.ramGiB || row.ram_gib || 0,
        totalStorageGiB: row.totalStorageGiB || row.total_storage_gib || 0,
        serverState: row.serverState || "Live",
        pods: row.pods || [],
        pgBouncerPods: row.pgBouncerPods || [],
        slots: row.slots || [],
        databases: row.databases || [],
      });
    }
    fetch("/api/v1/clusters", { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(payload) {
        if (!alive || !payload) return;
        var rows = payload.clusters || [];
        if (!rows.length) return;
        var next = {};
        rows.forEach(function(row) {
          var c = normalizeCluster(row || {});
          next[c.id] = c;
        });
        Object.keys(CLUSTERS).forEach(function(id) { delete CLUSTERS[id]; });
        Object.keys(next).forEach(function(id) { CLUSTERS[id] = next[id]; });
        var defaultId = payload.default || Object.keys(next)[0];
        if (defaultId && !CLUSTERS[clusterId]) setClusterId(defaultId);
        setClusterCatalogVersion(function(v) { return v + 1; });
      })
      .catch(function() {});
    return function() { alive = false; };
  }, []);

  useE(function() {
    var onPop = function() {
      var parsed = parseConsoleLocation();
      setRoute(parsed.route);
      setClusterId(parsed.clusterId);
      setTimeRange(parsed.timeRange);
      setLastRefresh(Date.now());
    };
    window.addEventListener("popstate", onPop);
    return function() { window.removeEventListener("popstate", onPop); };
  }, []);

  useE(function() {
    try {
      var next = routeToPath(cluster.id, route, timeRange);
      var current = window.location.pathname + window.location.search;
      if (next !== current) window.history.replaceState({ route: route, clusterId: cluster.id, timeRange: timeRange }, "", next);
    } catch (e) {}
  }, [route, cluster.id, timeRange]);

  // Apply + persist theme on the document root so CSS variables can switch.
  useE(function() {
    try {
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("hbz-theme", theme);
    } catch (e) {}
  }, [theme]);
  const toggleTheme = function() { setTheme(function(t) { return t === "dark" ? "light" : "dark"; }); };

  // Export a small JSON manifest describing the current view context.
  const exportView = function() {
    try {
      var manifest = {
        view: route,
        cluster: cluster.name,
        clusterId: cluster.id,
        timeRange: timeRange,
        generatedAt: new Date().toISOString(),
        source: "PostgreSQL Operations Console"
      };
      var blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "hbz-pg-" + cluster.id + "-" + route + "-" + Date.now() + ".json";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
      pushToast("Exported view manifest");
    } catch (e) { pushToast("Export failed", "danger"); }
  };

  // Ctrl-K / Cmd-K opens the command palette.
  useE(function() {
    var onKey = function(e) {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("keydown", onKey);
    return function() {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  useE(function() {
    window.ACTIVE_CLUSTER_ID = cluster.id;
    try { localStorage.setItem("hbz-active-cluster", cluster.id); } catch (e) {}
  }, [cluster.id]);

  const onCommand = (payload) => {
    setRun(payload);
    if (payload.status === "running") {
      setTimeout(() => {
        setRun(r => r && r.requestId === payload.requestId ? {
          ...r, status: "succeeded",
          times: { ...r.times, done: new Date().toLocaleTimeString("en-GB", { hour12: false }) + " UTC" },
          log: [...r.log, { t: "ok", s: `[${new Date().toLocaleTimeString("en-GB", { hour12: false })}] command completed in 1.4s` }]
        } : r);
      }, 1600);
    }
  };

  const crumbs = useM(() => ({
    overview:    [cluster.name, "Executive Overview"],
    openshift_overview: [cluster.name, "OpenShift", "Cluster Overview"],
    pg_profile:  [cluster.name, "Performance Insights", "Performance History"],
    ai_ops:      [cluster.name, "AI Operations"],
    ai_agent:    [cluster.name, "AI DBA Agent"],
    assistant:   [cluster.name, "AI Assistant"],
    aip_overview:   [cluster.name, "AI Platform", "AI Overview"],
    aip_inbox:      [cluster.name, "AI Platform", "Recommendations Inbox"],
    aip_agents:     [cluster.name, "AI Platform", "Agent Scheduler"],
    aip_executor:   [cluster.name, "AI Platform", "Controlled Executor"],
    aip_approvals:  [cluster.name, "AI Platform", "DBA Approvals"],
    aip_gateway:    [cluster.name, "AI Platform", "Model Gateway"],
    aip_rag:        [cluster.name, "AI Platform", "RAG Knowledge Base"],
    aip_evidence:   [cluster.name, "AI Platform", "Evidence Packs"],
    aip_audit:      [cluster.name, "AI Platform", "AI Audit Logs"],
    aip_governance: [cluster.name, "AI Platform", "AI Governance"],
    ai_nlsql:       [cluster.name, "AI Operations", "Ask Your Database"],
    ai_vector:      [cluster.name, "AI Operations", "Vector & RAG Monitor"],
    ai_agents:      [cluster.name, "AI Operations", "AI Agent Governance"],
    ai_branching:   [cluster.name, "AI Operations", "Branching & Forks"],
    sql_insight:    [cluster.name, "Performance", "SQL Insight"],
    health_grid:    [cluster.name, "Monitoring", "Cluster Health Grid"],
    memory_sga:     [cluster.name, "Monitoring", "Memory / SGA"],
    appmon:        [cluster.name, "Application Monitoring", "Estate Overview"],
    appmon_tps:    [cluster.name, "Application Monitoring", "TPS & Warehouse"],
    appmon_service:[cluster.name, "Application Monitoring", "Service & Gateway"],
    appmon_apps:   [cluster.name, "Application Monitoring", "Charge / Locker / Mobile / Doc"],
    appmon_repl:   [cluster.name, "Application Monitoring", "Replication & DBA"],
    appmon_business: [cluster.name, "Application Monitoring", "Banking Business"],
    appmon_mgmt:   [cluster.name, "Application Monitoring", "Management Scorecard"],
    cluster:     [cluster.name, "Cluster / Patroni"],
    performance: [cluster.name, "Performance"],
    objects:     [cluster.name, "Object Metrics"],
    pgbouncer_deep: [cluster.name, "Monitoring", "PgBouncer Pools"],
    pgbouncer_advanced: [cluster.name, "Monitoring", "PgBouncer Diagnostics"],
    connect_hub: [cluster.name, "Monitoring", "Connect"],
    endpoints: [cluster.name, "Monitoring", "Endpoints & Listeners"],
    host_monitoring: [cluster.name, "Monitoring", "Host / OS Monitoring"],
    logs_explorer: [cluster.name, "Monitoring", "Logs Explorer"],
    wal_archive: [cluster.name, "Monitoring", "WAL & Archive"],
    storage_health: [cluster.name, "Monitoring", "Storage Health"],
    alerts:      [cluster.name, "Alerts & Insights"],
    runs:        [cluster.name, "Run History"],
    audit:       [cluster.name, "Audit Log"],
    backups:     [cluster.name, "Backups & Recovery"],
    logs:        [cluster.name, "Administration", "Pod Logs"],
    settings:    [cluster.name, "Administration", "Settings"],
    notifications: [cluster.name, "Administration", "Notifications"],
    tokens:      [cluster.name, "Administration", "API Tokens"],
    tenants:     [cluster.name, "Administration", "Tenants & Workspaces"],
    help:        [cluster.name, "Administration", "Help & Runbooks"],
    metrics_explorer: [cluster.name, "Performance Insights", "Metrics Explorer"],
    db_load_timeline: [cluster.name, "Performance Insights", "DB Load Timeline"],
    plan_explorer:   [cluster.name, "Performance Insights", "Plan Explorer"],
    auto_tuning:     [cluster.name, "Performance Insights", "Index & Auto-Tuning"],
    advisor:         [cluster.name, "Advisor & Health", "Advisor"],
    ai_dba_recommendations: [cluster.name, "AI Operations", "AI DBA Recommendations"],
    cloud_advisor:   [cluster.name, "Advisor & Health", "Cloud Advisor"],
    capacity_optimizer: [cluster.name, "Advisor & Health", "Cost / Capacity Optimizer"],
    capacity_planning: [cluster.name, "Advisor & Health", "Capacity Planning"],
    anomalies:       [cluster.name, "Advisor & Health", "Anomaly Detection"],
    cost_showback:   [cluster.name, "Advisor & Health", "Cost Showback & Budgets"],
    resource_health: [cluster.name, "Advisor & Health", "Resource Health"],
    collector_health: [cluster.name, "Advisor & Health", "Collector Health"],
    perf_activity: [cluster.name, "Performance Insights", "Application Activity"],
    perf_topsql:  [cluster.name, "Performance Insights", "Top SQL"],
    perf_waits:   [cluster.name, "Performance Insights", "Wait Events"],
    perf_plans:   [cluster.name, "Performance Insights", "Plan Cache"],
    perf_indexes: [cluster.name, "Performance Insights", "Index Advisor"],
    perf_bloat:   [cluster.name, "Performance Insights", "Bloat"],
    perf_vacuum:  [cluster.name, "Performance Insights", "Vacuum"],
    perf_slow:    [cluster.name, "Performance Insights", "Slow Queries"],
    admin_databases:  [cluster.name, "Database Administration", "Databases"],
    admin_live:       [cluster.name, "Database Administration", "Live Connect"],
    sql_workbench:    [cluster.name, "Database Administration", "SQL Workbench"],
    admin_schemas:    [cluster.name, "Database Administration", "Schemas & Objects"],
    admin_roles:      [cluster.name, "Database Administration", "Users & Roles"],
    admin_privileges: [cluster.name, "Database Administration", "Privileges"],
    admin_hba:        [cluster.name, "Database Administration", "HBA Rules"],
    admin_extensions: [cluster.name, "Database Administration", "Extensions"],
    config_parameters:  [cluster.name, "Configuration", "Server Parameters"],
    config_patroni:     [cluster.name, "Configuration", "Patroni DCS Config"],
    config_roles:       [cluster.name, "Configuration", "Per-Role Settings"],
    config_databases:   [cluster.name, "Configuration", "Per-Database Settings"],
    config_maintenance: [cluster.name, "Configuration", "Maintenance Mode"],
    parameter_drift: [cluster.name, "Configuration", "Parameter Drift"],
    extension_governance: [cluster.name, "Configuration", "Extension Governance"],
    repl_topology: [cluster.name, "Replication & HA", "Topology"],
    repl_sync:     [cluster.name, "Replication & HA", "Sync Standbys"],
    repl_logical:  [cluster.name, "Replication & HA", "Logical Replication"],
    repl_fdw:      [cluster.name, "Replication & HA", "Foreign Data Wrappers"],
    repl_history:  [cluster.name, "Replication & HA", "Switchover History"],
    geo_topology:  [cluster.name, "Replication & HA", "Geo Replica Topology"],
    replica_workflow: [cluster.name, "Replication & HA", "Replica & Promotion"],
    migration_wizard: [cluster.name, "Replication & HA", "Migration Wizard"],
    sec_auth:       [cluster.name, "Security & Compliance", "Authentication"],
    sec_tls:        [cluster.name, "Security & Compliance", "TLS Certificates"],
    sec_pgaudit:    [cluster.name, "Security & Compliance", "pgaudit Settings"],
    sec_compliance: [cluster.name, "Security & Compliance", "Compliance Reports"],
    sec_sensitive:  [cluster.name, "Security & Compliance", "Sensitive Data"],
    security_posture: [cluster.name, "Security & Compliance", "Security Posture"],
    network_access: [cluster.name, "Security & Compliance", "Network / Private Access"],
    access_rules: [cluster.name, "Security & Compliance", "Firewall / Access Rules"],
    encryption: [cluster.name, "Security & Compliance", "Encryption & Keys"],
    event_streaming: [cluster.name, "Security & Compliance", "Event Streaming / SIEM"],
    access_review: [cluster.name, "Security & Compliance", "Access Recertification"],
    life_provision:    [cluster.name, "Lifecycle", "Provisioning"],
    life_scale:        [cluster.name, "Lifecycle", "Scaling"],
    life_replicas:     [cluster.name, "Lifecycle", "Read Replicas"],
    life_upgrade:      [cluster.name, "Lifecycle", "Upgrades"],
    blue_green_upgrade: [cluster.name, "Lifecycle", "Blue/Green Upgrade"],
    storage_autoscale: [cluster.name, "Lifecycle", "Storage Autoscale"],
    life_decommission: [cluster.name, "Lifecycle", "Decommission"],
    dr_readiness: [cluster.name, "DR & Cutover", "DR Readiness"],
    recovery_assurance: [cluster.name, "DR & Cutover", "Recovery Assurance"],
    restore_window: [cluster.name, "DR & Cutover", "Restore Window (PITR)"],
    snapshots: [cluster.name, "DR & Cutover", "Snapshots & Clone/Fork"],
    sla_compliance: [cluster.name, "DR & Cutover", "SLA / RTO / RPO"],
    cutover:     [cluster.name, "DR & Cutover", "Cutover & Switchover"],
    readiness:   [cluster.name, "Operations", "Environment Readiness"],
    ops_inbox:   [cluster.name, "Operations", "Ops Inbox"],
    change_calendar: [cluster.name, "Operations", "Change Calendar"],
    maintenance_feed: [cluster.name, "Operations", "Maintenance & Patch Feed"],
    maintenance_scheduler: [cluster.name, "Operations", "Maintenance Scheduler"],
    alert_rules: [cluster.name, "Operations", "Alert Rule Builder"],
    platform_health: [cluster.name, "Operations", "Platform Health"],
    quotas: [cluster.name, "Operations", "Quotas & Limits"],
    tags: [cluster.name, "Operations", "Tags & Ownership"],
    activity_stream: [cluster.name, "Operations", "Activity Stream"],
    log_analytics: [cluster.name, "Operations", "Log Analytics"],
    incident_packs: [cluster.name, "Operations", "Incident Packs"],
    estate_matrix: [cluster.name, "Operations", "Estate Matrix"],
    version_readiness: [cluster.name, "Operations", "Version Readiness"],
    evidence_export: [cluster.name, "Operations", "Evidence Export"],
  }[route]), [route, cluster.name, clusterCatalogVersion]);

  const lastRefreshStr = useM(() => {
    const d = new Date(lastRefresh);
    return d.toLocaleTimeString("en-GB", { hour12: false }) + " UTC";
  }, [lastRefresh]);
  const isAppMonRoute = route.indexOf("appmon") === 0;

  useE(() => {
    if (isAppMonRoute) return undefined;
    const t = setInterval(() => setLastRefresh(Date.now()), 30000);
    return () => clearInterval(t);
  }, [isAppMonRoute]);

  useE(() => {
    let alive = true;
    fetch("/api/v1/me", { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(payload => {
        if (alive && payload) setMe(payload.user || payload);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const title = {
    overview:    "Executive Overview",
    openshift_overview: "OpenShift Overview",
    pg_profile:  "Performance History",
    ai_ops:      "AI Operations",
    ai_agent:    "AI DBA Agent",
    assistant:   "AI Assistant",
    aip_overview:   "AI Overview",
    aip_inbox:      "Recommendations Inbox",
    aip_agents:     "Agent Scheduler",
    aip_executor:   "Controlled Executor",
    aip_approvals:  "DBA Approvals",
    aip_gateway:    "Model Gateway",
    aip_rag:        "RAG Knowledge Base",
    aip_evidence:   "Evidence Packs",
    aip_audit:      "AI Audit Logs",
    aip_governance: "AI Governance",
    ai_nlsql:       "Ask Your Database",
    ai_vector:      "Vector & RAG Monitor",
    ai_agents:      "AI Agent Governance",
    ai_branching:   "Branching & Forks",
    sql_insight:    "SQL Insight",
    health_grid:    "Cluster Health Grid",
    memory_sga:     "Memory / SGA",
    appmon:        "Application Monitoring",
    appmon_tps:    "TPS Posting & Warehouse",
    appmon_service:"Service CRM / Jobs / Kafka & API Gateway",
    appmon_apps:   "Charge / Locker / Mobile / Document",
    appmon_repl:   "Replication & Integration · DBA Evidence",
    appmon_business: "Banking Business Dashboard",
    appmon_mgmt:   "Enterprise Core Banking Master Scorecard",
    cluster:     "Cluster / Patroni",
    performance: "Performance & Sessions",
    objects:     "Object Metrics",
    pgbouncer_deep: "PgBouncer Pools",
    pgbouncer_advanced: "Advanced PgBouncer Diagnostics",
    connect_hub: "Connect Hub",
    endpoints: "Endpoints & Listeners",
    host_monitoring: "Enhanced Host / OS Monitoring",
    logs_explorer: "Logs Explorer & Live Tail",
    wal_archive: "WAL & Archive",
    storage_health: "Storage Health",
    alerts:      "Alerts & Insights",
    runs:        "Run History",
    audit:       "Audit Log",
    backups:     "Backups & Recovery",
    logs:        "Pod Logs",
    settings:    "Settings",
    notifications: "Notifications",
    tokens:      "API Tokens",
    tenants:     "Tenants & Workspaces",
    help:        "Help & Runbooks",
    metrics_explorer: "Metrics Explorer",
    db_load_timeline: "Query Store / DB Load Timeline",
    plan_explorer:   "Visual EXPLAIN / Plan Explorer",
    auto_tuning:     "Index & Auto-Tuning Loop",
    advisor:         "Advisor",
    ai_dba_recommendations: "AI DBA Recommendations",
    cloud_advisor:   "Cloud Advisor / Recommendation Center",
    capacity_optimizer: "Cost / Capacity Optimizer",
    capacity_planning: "Capacity Planning & Forecast",
    anomalies:       "Anomaly Detection & Proactive Insights",
    cost_showback:   "Cost Showback & Budgets",
    resource_health: "Resource Health",
    collector_health: "Collector Health Timeline",
    perf_activity: "Application Activity",
    perf_topsql:  "Top SQL",
    perf_waits:   "Wait Events",
    perf_plans:   "Plan Cache",
    perf_indexes: "Index Advisor",
    perf_bloat:   "Bloat",
    perf_vacuum:  "Vacuum Insights",
    perf_slow:    "Slow Queries",
    admin_databases:  "Databases",
    admin_live:       "Live Connect",
    sql_workbench:    "SQL Workbench",
    admin_schemas:    "Schemas & Objects",
    admin_roles:      "Users & Roles",
    admin_privileges: "Privileges",
    admin_hba:        "HBA Rules",
    admin_extensions: "Extensions",
    config_parameters:  "Server Parameters",
    config_patroni:     "Patroni DCS Config",
    config_roles:       "Per-Role Settings",
    config_databases:   "Per-Database Settings",
    config_maintenance: "Maintenance Mode",
    parameter_drift: "Parameter Profile / Drift Manager",
    extension_governance: "Extension & Preload Governance",
    repl_topology: "Replication Topology",
    repl_sync:     "Sync Standbys",
    repl_logical:  "Logical Replication",
    repl_fdw:      "Foreign Data Wrappers",
    repl_history:  "Switchover History",
    geo_topology:  "Global / Geo Replica Topology",
    replica_workflow: "Read Replica & Promotion Workflow",
    migration_wizard: "Data Migration & Logical-Replication Wizard",
    sec_auth:       "Authentication",
    sec_tls:        "TLS Certificates",
    sec_pgaudit:    "pgaudit Settings",
    sec_compliance: "Compliance Reports",
    sec_sensitive:  "Sensitive Data Inventory",
    security_posture: "Security Posture Center",
    network_access: "Network / Private Access Visualizer",
    access_rules: "Firewall / Access Rules Governance",
    encryption: "Encryption & Key Management Posture",
    event_streaming: "Audit / Event Streaming & SIEM Export",
    access_review: "Access Recertification & Console RBAC",
    life_provision:    "Provisioning",
    life_scale:        "Scaling",
    life_replicas:     "Read Replicas",
    life_upgrade:      "Upgrades",
    blue_green_upgrade: "Blue/Green Upgrade Workflow",
    storage_autoscale: "Storage Autoscale & IOPS Policy",
    life_decommission: "Decommission",
    dr_readiness: "DR Readiness",
    recovery_assurance: "Recovery Assurance",
    restore_window: "Earliest / Latest Restorable Time",
    snapshots: "Snapshot & Clone/Fork Catalog",
    sla_compliance: "SLA / RTO / RPO Compliance",
    cutover:     "DR & Cutover",
    readiness:   "Environment Readiness",
    ops_inbox:   "Ops Inbox",
    change_calendar: "Change Calendar",
    maintenance_feed: "Maintenance & Patch Feed",
    maintenance_scheduler: "Maintenance Window Scheduler",
    alert_rules: "Alert Rule Builder & Action Groups",
    platform_health: "Platform & Provider Service Health",
    quotas: "Quotas & Limits",
    tags: "Tags, Ownership & Organization",
    activity_stream: "Database Activity Stream",
    log_analytics: "Log Analytics Center",
    incident_packs: "Support Case / Incident Pack Tracker",
    estate_matrix: "Estate Matrix",
    version_readiness: "Version Readiness",
    evidence_export: "Evidence Export",
  }[route];

  const subtitle = {
    overview:    "PostgreSQL 18.3 · Patroni · CrunchyData PGO · OpenShift",
    openshift_overview: "Live Grafana cluster overview · dashboard inventory · Loki ML logs",
    pg_profile:  "Central pg_profile history · sanitized reports · robust query baselines",
    ai_ops:      "Unified AI operations, recommendations, scheduler, readiness, and assistant evidence",
    ai_agent:    "Agentic DBA recommendations · approval workflow · execution guarded",
    assistant:   "Provider-attributed · read-only DBA assistant · audit-logged",
    appmon:        "Live application sessions, waits, and business-domain activity · Prometheus",
    appmon_tps:    "TPS posting & warehouse table footprint, dead tuples, ETL churn, sessions · Prometheus",
    appmon_service:"Service CRM/Jobs/Kafka & API Gateway footprint, dead tuples, sessions · Prometheus",
    appmon_apps:   "Charge/Locker/Mobile/Document schema footprint, dead tuples, churn, sessions · Prometheus",
    appmon_repl:   "Logical subscriptions, slot WAL, workers & DBA evidence · Prometheus",
    appmon_business: "Customers, accounts, postings, VAT, Kafka & reconciliation · live read-only SQL",
    appmon_mgmt:   "Executive scorecard: volumes, channels, risk, liquidity & adoption · live read-only SQL",
    cluster:     "Patroni cluster detail · " + cluster.name,
    performance: "pg_stat_activity / pg_locks / pg_stat_statements",
    objects:     "pg_inspector snapshots · region/database scope",
    pgbouncer_deep: "Pool readiness, direct-session detection, and PgBouncer pod visibility",
    pgbouncer_advanced: "SHOW POOLS-style saturation, waiting clients, and pinning hints · evidence not collected when absent",
    connect_hub: "Connection strings + psql/JDBC/Python/.NET snippets for primary/replica/pooled endpoints · password from Secret",
    endpoints: "Writer/reader/pooled services + LoadBalancers, what each routes to, TLS, and health",
    host_monitoring: "Per-pod CPU/mem/restarts + top backends · derived from object-metrics, no node shell",
    logs_explorer: "Query-able, sanitized pod log tail across PG/Patroni/PGO/PgBouncer/pgBackRest",
    wal_archive: "WAL generation, archive posture, retained slot WAL, and replication lag signals",
    storage_health: "PVC, ODF, object-store, and storage-growth evidence from local collectors",
    alerts:      "Phase 1 derived alert checks · Prometheus/Alertmanager wiring pending",
    runs:        "Console job history · approvals and worker execution foundation",
    audit:       "Derived audit stream · metadata audit table pending",
    backups:     "pgBackRest posture, PITR planning, schedules, validation, and clone approval requests",
    logs:        "OpenShift pod log inventory and Kubernetes pods/log preflight",
    settings:    "Console profile, tenant membership, and personal preferences",
    notifications: "Notification channels and alert-rule administration",
    tokens:      "Personal API token lifecycle with hashed metadata storage",
    tenants:     "Tenant and workspace bootstrap metadata",
    help:        "Built-in operational runbooks for console administration",
    metrics_explorer: "Pick a metric, aggregation, and range · real ingested object-metrics samples",
    db_load_timeline: "Cloud-style DB load by SQL, waits, and history · degrades to partial/unknown when pg_stat_statements is absent",
    plan_explorer:   "Captured execution-plan tree with cost/rows and slow-node hints · live EXPLAIN stays disabled",
    auto_tuning:     "Recommend → validate → guarded apply loop with tuning history · never auto-applies",
    advisor:         "Parameter tuning recommendations + unused/bloated objects · guarded validate-and-apply",
    ai_dba_recommendations: "AI-generated recommendations from pg_stat_statements, combined with the same parameter/index/bloat advisor · guarded validate-and-apply",
    cloud_advisor:   "Consolidated availability, DR, performance, security, cost, and ops recommendations · read-only",
    capacity_optimizer: "Over/under-provisioning, connection headroom, and growth signals · cost proxy, DBA review items",
    capacity_planning: "Storage/connection time-to-exhaustion projections from 30-day trend · planning signal only",
    anomalies:       "z-score baseline scan over ingested series · proactive insights, no ML service",
    cost_showback:   "Per-cluster/region cost-proxy breakdown and advisory budgets · no real billing source",
    resource_health: "Failover / switchover timeline from Patroni history and console HA jobs",
    collector_health: "Read-only hourly facts and deduped findings from the support collector",
    perf_activity: "Connections by source, application, user, database, client, and DML counters",
    perf_topsql:  "pg_stat_statements live leaderboard · read-only",
    perf_waits:   "Current wait-event breakdown from pg_stat_activity",
    perf_plans:   "Plan history route foundation · EXPLAIN disabled",
    perf_indexes: "Unused-index review from pg_stat_user_indexes",
    perf_bloat:   "Dead tuple leaderboard from latest object-metrics snapshot",
    perf_vacuum:  "Autovacuum and analyze health from pg_stat_user_tables",
    perf_slow:    "Current long-running active queries",
    admin_databases:  "Live pg_database inventory · write actions validate as dry-run jobs",
    admin_live:       "DBA-only live PostgreSQL connection · read-only SQL",
    sql_workbench:    "Saved-query library, history, and export · execution delegated to read-only Live Connect",
    admin_schemas:    "Schema browser · tables and indexes from PostgreSQL catalogs",
    admin_roles:      "Role inventory and membership · read-only in Phase 2 start",
    admin_privileges: "GRANT matrix foundation · read-only privilege view",
    admin_hba:        "pg_hba_file_rules visibility · apply path remains guarded",
    admin_extensions: "Installed vs available extensions · install/delete validate as dry-run jobs",
    config_parameters:  "Live pg_settings inventory · changes validate as dry-run jobs",
    config_patroni:     "Patroni dynamic DCS config · apply path remains guarded",
    config_roles:       "Role-level GUC defaults from pg_db_role_setting",
    config_databases:   "Database-level GUC defaults from pg_db_role_setting",
    config_maintenance: "Patroni pause state · enter/exit validates as guarded jobs",
    parameter_drift: "Baseline vs current profile, drift severity, and restart-required deltas · no direct patch",
    extension_governance: "Installed vs allowlist, shared_preload_libraries, and pgvector/AI readiness · dry-run install",
    repl_topology: "Patroni members, streaming replication, slots, and guarded HA validation",
    repl_sync:     "Synchronous mode, sync standby candidates, and replication lag",
    repl_logical:  "Publications, subscriptions, workers, and logical slots",
    repl_fdw:      "Foreign data wrappers, servers, foreign tables, and mappings",
    repl_history:  "Patroni timeline transitions and console HA jobs",
    geo_topology:  "PROD/DC1, DR/DC2, UAT, and unknown clusters with role, lag, and promotion readiness · no promotion from map",
    replica_workflow: "Guided add/promote/decouple with lag/sync gate and endpoint-cutover checklist · preflight only",
    migration_wizard: "Publication/subscription/slot setup + cutover checklist · all DDL via guarded jobs",
    sec_auth:       "HBA, password policy, role posture, and guarded auth validation",
    sec_tls:        "TLS settings, pg_stat_ssl sessions, and guarded certificate rotation requests",
    sec_pgaudit:    "pgaudit extension readiness and guarded audit settings validation",
    sec_compliance: "SOC 2, ISO 27001, PCI DSS, and operational evidence rollups",
    sec_sensitive:  "Metadata-only sensitive column inventory by naming heuristic",
    security_posture: "TLS, auth/HBA, pgaudit, sensitive-data, and cert posture with category scores · presence/metadata only",
    network_access: "App → PgBouncer → PostgreSQL → object-store path · symbolic/redacted labels, no live probe",
    access_rules: "HBA + CIDR + listen_addresses governance with trust/any-source flags · redacted sources",
    encryption: "In-transit/at-rest/cert/cipher posture · presence & expiry metadata only, no key material",
    event_streaming: "Outbound audit/event stream config preview + redacted sample export · no external upload",
    access_review: "Role/privilege recertification with attestations + superuser flags · revokes via guarded jobs",
    life_provision:    "Crunchy PGO cluster creation preflight · dry-run only",
    life_scale:        "Replica, CPU, memory, storage, and PgBouncer scale validation",
    life_replicas:     "Read-replica add/remove/rebalance validation",
    life_upgrade:      "Upgrade preflight and approval request foundation",
    blue_green_upgrade: "Guided blue/green rehearsal: checklist, cutover readiness, rollback · preflight-only, guarded jobs",
    storage_autoscale: "Autogrow headroom, IOPS class, and scale-event signals · PVC resize via guarded jobs",
    life_decommission: "Final backup, archive evidence, and decommission approval request",
    dr_readiness: "Read-only DR score from readiness, remote agents, cutover config, backup posture, and replication evidence",
    recovery_assurance: "pgBackRest repo, WAL archive, schedules, PITR posture, and validation evidence",
    restore_window: "Earliest/latest restorable time, RPO, and read-only PITR target validation · execution stays in Backups",
    snapshots: "Backup/snapshot catalog + fork-to-PITR sandbox readiness · clone executes via guarded jobs",
    sla_compliance: "RTO/RPO target vs actual, backup freshness, lag, and drill age · explicit unknown, never claims from gaps",
    cutover:     "Planned switchover / switchback drills · 4-eyes approval · vendored UK orchestrator",
    readiness:   "DB, Patroni, Prometheus, Kubernetes, pgBackRest, remote agents, and ingest freshness",
    ops_inbox:   "Unified queue for alerts, approvals, failed jobs, collector findings, and bundle requests",
    change_calendar: "Read-only change-window view from jobs, audit, and maintenance metadata",
    maintenance_feed: "Windows, blackouts, pending minor upgrades, image/restart posture, and likely app impact · guarded jobs only",
    maintenance_scheduler: "Define maintenance windows + exclusions (cron preview) · persist via guarded validation job",
    alert_rules: "Build metric-threshold rules + routing/action groups · preview only, no paging from here",
    platform_health: "OpenShift/ODF/Ceph/NooBaa/PGO layer rollup from readiness + findings · no live cluster command",
    quotas: "Connections, slots, WAL senders, workers vs configured limits with headroom · unknown stays unknown",
    tags: "Tag clusters by owner/app/environment/cost-center and group the estate · local console metadata",
    activity_stream: "Chronological jobs, alerts, cutover, backups, findings, and collector events · secret-free",
    log_analytics: "PostgreSQL/Patroni/PGO/PgBouncer/pgBackRest categories, signatures, and sanitized findings · no raw logs",
    incident_packs: "Incident/support pack index, linked evidence, redaction status, and local redacted export · no upload",
    estate_matrix: "Cross-cluster inventory for PROD, DR, UAT, remote agents, snapshots, and health",
    version_readiness: "PostgreSQL, PGO/runtime, image, pending-restart, and upgrade blocker evidence",
    evidence_export: "Redacted JSON evidence manifest for audit, incident, and change review",
  }[route];

  const bandItems = [
    { key: "overview",    label: "Executive Overview", icon: Icon.LayoutDashboard },
    { key: "openshift_overview", label: "OpenShift", icon: Icon.Cloud },
    { key: "cluster",     label: "Cluster / Patroni",  icon: Icon.Layers },
    { key: "performance", label: "Performance",        icon: Icon.Activity },
    { key: "objects",     label: "Object Metrics",     icon: Icon.Database },
    { key: "ai_ops",      label: "AI Ops",             icon: Icon.Zap },
    { key: "assistant",   label: "AI Assistant",       icon: Icon.Zap },
  ];

  const routeSource = {
    overview: "live PostgreSQL + object metrics",
    openshift_overview: "Grafana + Thanos + Loki",
    cluster: "Patroni + live PostgreSQL",
    performance: "live PostgreSQL",
    objects: "object-metrics",
    alerts: "console metadata + derived checks",
    runs: "console-metadata",
    audit: "console-metadata",
    backups: "pgBackRest config + console metadata",
    pgbouncer_deep: "pg_stat_activity + cluster metadata",
    wal_archive: "backups + replication + metrics",
    storage_health: "object-metrics + readiness",
    logs: "Kubernetes API",
    settings: "console-metadata",
    notifications: "console-metadata",
    tokens: "console-metadata",
    tenants: "console-metadata",
    help: "console-metadata",
    readiness: "readiness-checks",
    collector_health: "collector-history",
    metrics_explorer: "object-metrics",
    advisor: "live PostgreSQL",
    ai_dba_recommendations: "live PostgreSQL + pg_stat_statements",
    resource_health: "Patroni + console metadata",
    dr_readiness: "readiness + cutover + backup + replication",
    recovery_assurance: "pgBackRest + WAL archive",
    cutover: "console metadata + cutover engine",
    ops_inbox: "alerts + jobs + collector",
    change_calendar: "jobs + audit + maintenance",
    estate_matrix: "console clusters + readiness",
    version_readiness: "lifecycle + pg_settings",
    evidence_export: "redacted evidence manifest",
    cloud_advisor: "readiness + backups + advisor + collector",
    db_load_timeline: "pg_stat_statements + activity",
    pgbouncer_advanced: "pg_stat_activity + cluster metadata",
    restore_window: "pgBackRest + WAL archive",
    blue_green_upgrade: "lifecycle + jobs",
    geo_topology: "live PostgreSQL + Patroni",
    maintenance_feed: "jobs + audit + maintenance",
    capacity_optimizer: "object-metrics + lifecycle",
    network_access: "cluster metadata + manifest patterns",
    activity_stream: "alerts + jobs + cutover + collector",
    parameter_drift: "live PostgreSQL + advisor",
    log_analytics: "pods + findings + alerts",
    security_posture: "live PostgreSQL + console metadata",
    incident_packs: "collector + findings + jobs",
    sla_compliance: "backups + replication + cutover",
    connect_hub: "cluster metadata + auth",
    endpoints: "cluster metadata + Patroni",
    host_monitoring: "object-metrics + cluster",
    logs_explorer: "Kubernetes API",
    plan_explorer: "pg_stat_statements + plan history",
    auto_tuning: "advisor + index-advisor + jobs",
    capacity_planning: "object-metrics + lifecycle",
    anomalies: "object-metrics series",
    cost_showback: "console clusters + lifecycle",
    sql_workbench: "console-metadata",
    extension_governance: "live PostgreSQL",
    replica_workflow: "live PostgreSQL + Patroni",
    migration_wizard: "live PostgreSQL + jobs",
    access_rules: "pg_hba + pg_settings",
    encryption: "pg_settings + TLS + backups",
    event_streaming: "audit + console metadata",
    access_review: "pg_roles + privileges",
    storage_autoscale: "lifecycle + object-metrics",
    snapshots: "pgBackRest",
    maintenance_scheduler: "maintenance metadata",
    alert_rules: "alert-rules + notifications",
    platform_health: "readiness + collector",
    quotas: "pg_settings + activity",
    tags: "console-metadata",
    assistant: "Live evidence + configured AI provider · read-only",
    ai_ops: "AI scheduler + recommendations + assistant",
    ai_agent: "AI agent + approvals",
  };
  if (route.indexOf("appmon") === 0) routeSource[route] = "prometheus";
  if (route.indexOf("perf_") === 0) routeSource[route] = "live PostgreSQL";
  if (route.indexOf("admin_") === 0) routeSource[route] = "live PostgreSQL";
  if (route.indexOf("config_") === 0) routeSource[route] = "live PostgreSQL + Patroni";
  if (route.indexOf("repl_") === 0) routeSource[route] = "live PostgreSQL + Patroni";
  if (route.indexOf("sec_") === 0) routeSource[route] = "live PostgreSQL + console metadata";
  if (route.indexOf("life_") === 0) routeSource[route] = "console metadata + Kubernetes API";

  useE(function() {
    if (!title) setRoute("overview");
  }, [title]);

  return (
    <div className="app">
      <TopBar crumbs={crumbs} user={me}
              onSearchOpen={() => setPaletteOpen(true)}
              onSettings={() => setRoute("settings")}
              theme={theme} onToggleTheme={toggleTheme}/>
      <BandBar items={bandItems} value={route} onChange={setRoute}/>
      <div className={"shell" + (railOpen ? " rail-open" : "")}>
        <LeftRail open={railOpen} onToggle={() => setRailOpen(o => !o)}
                  route={route} onRoute={setRoute} cluster={cluster} user={me}/>
        <div className="workspace">

          <div className="action-bar">
            <h1>
              {title}
              <span className="sub">— {subtitle}</span>
            </h1>
            <div className="grow"/>

            <div className="action-controls">
            <ClusterPicker value={cluster.id} onChange={function(id) { setClusterId(id); setLastRefresh(Date.now()); }}/>{cluster.id !== "uat" && <span className="pill warn"><span className="dot"/>Read only</span>}
            <SourceBadge source={routeSource[route] || "console"}/>
            <TimeRange value={timeRange} onChange={setTimeRange}/>
            <button className="btn sm" onClick={() => { setLastRefresh(Date.now()); pushToast("View refreshed"); }}>
              <Icon.RefreshCw size={12}/> Refresh
            </button>
            <span className="auto-hint">
              <span className="dot"/>
              {isAppMonRoute ? "Manual refresh" : "Auto-refresh 30s"} · last {lastRefreshStr}
            </span>
            <button className="btn sm ghost" aria-label="Open command palette" title="Command palette (Ctrl-K)" onClick={() => setPaletteOpen(true)}><Icon.Command size={12}/></button>
            <button className="btn sm ghost" aria-label="Export current view as JSON" title="Export view (JSON)" onClick={exportView}><Icon.Download size={12}/> Export</button>
            </div>
          </div>

          {route === "overview"    && <OverviewScreen    cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh} onCommand={onCommand}/>}
          {route === "openshift_overview" && <OpenShiftOverviewScreen lastRefresh={lastRefresh}/>}
          {route === "sql_insight" && window.SqlInsightScreen && React.createElement(window.SqlInsightScreen, { cluster: cluster, lastRefresh: lastRefresh })}
          {route === "pg_profile" && window.PerformanceHistoryScreen && React.createElement(window.PerformanceHistoryScreen, { cluster: cluster, lastRefresh: lastRefresh })}
          {route === "health_grid" && window.HealthGridScreen && React.createElement(window.HealthGridScreen, { cluster: cluster, timeRange: timeRange, lastRefresh: lastRefresh })}
          {route === "memory_sga" && window.MemorySgaScreen && React.createElement(window.MemorySgaScreen, { cluster: cluster, lastRefresh: lastRefresh })}
          {["aip_overview","aip_inbox","aip_agents","aip_executor","aip_approvals","aip_gateway","aip_rag","aip_evidence","aip_audit","aip_governance"].indexOf(route) >= 0 && window.AIPlatformScreen && React.createElement(window.AIPlatformScreen, { view: route.replace("aip_", ""), cluster: cluster, lastRefresh: lastRefresh })}
          {["ai_nlsql","ai_vector","ai_agents","ai_branching"].indexOf(route) >= 0 && window.AIOpsScreen && React.createElement(window.AIOpsScreen, { view: route.replace("ai_", ""), cluster: cluster, lastRefresh: lastRefresh })}
          {route === "appmon"      && <AppMonitoringScreen cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "appmon_tps"  && <AppMonTpsScreen     cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "appmon_service" && <AppMonServiceScreen cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "appmon_apps"    && <AppMonAppsScreen    cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "appmon_repl"    && <AppMonReplScreen    cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "appmon_business" && <AppMonBusinessScreen cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "appmon_mgmt"    && <AppMonMgmtScreen    cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "cluster"     && <ClusterScreen     cluster={cluster} lastRefresh={lastRefresh} onCommand={onCommand}/>}
          {route === "performance" && <PerformanceScreen cluster={cluster} lastRefresh={lastRefresh} onCommand={onCommand}/>}
          {route === "objects"     && <ObjectMetricsScreen lastRefresh={lastRefresh}/>}
          {route === "pgbouncer_deep" && <PgBouncerDeepDiveScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "pgbouncer_advanced" && <PgBouncerAdvancedScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "connect_hub" && <ConnectHubScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "endpoints" && <EndpointsScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "host_monitoring" && <HostMonitoringScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "logs_explorer" && <LogsExplorerScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "wal_archive" && <WalArchivePressureScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "storage_health" && <StorageHealthScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "alerts"      && <AlertsScreen      lastRefresh={lastRefresh}/>}
          {route === "collector_health" && <CollectorHealthScreen cluster={cluster} timeRange={timeRange} lastRefresh={lastRefresh}/>}
          {route === "runs"        && <RunHistoryScreen  lastRefresh={lastRefresh}/>}
          {route === "audit"       && <AuditLogScreen    lastRefresh={lastRefresh}/>}
          {route === "backups"     && <BackupRecoveryScreen lastRefresh={lastRefresh}/>}
          {route === "logs"          && <AdministrationScreen view="logs"          lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "settings"      && <AdministrationScreen view="settings"      lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "notifications" && <AdministrationScreen view="notifications" lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "tokens"        && <AdministrationScreen view="tokens"        lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "tenants"       && <AdministrationScreen view="tenants"       lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "help"          && <AdministrationScreen view="help"          lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "metrics_explorer" && <MetricsExplorerScreen lastRefresh={lastRefresh}/>}
          {route === "db_load_timeline" && <DbLoadTimelineScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "plan_explorer"   && <PlanExplorerScreen   cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "auto_tuning"     && <AutoTuningScreen     cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "advisor"         && <AdvisorScreen        lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "ai_dba_recommendations" && <AdvisorScreen lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "cloud_advisor"   && <CloudAdvisorScreen   cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "capacity_optimizer" && <CapacityOptimizerScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "capacity_planning" && <CapacityPlanningScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "anomalies"       && <AnomaliesScreen      cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "cost_showback"   && <CostShowbackScreen   cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "resource_health" && <ResourceHealthScreen  lastRefresh={lastRefresh}/>}
          {route === "perf_activity" && <PerformanceInsightsScreen view="activity" lastRefresh={lastRefresh}/>}
          {route === "perf_topsql"  && <PerformanceInsightsScreen view="topsql"  lastRefresh={lastRefresh}/>}
          {route === "perf_waits"   && <PerformanceInsightsScreen view="waits"   lastRefresh={lastRefresh}/>}
          {route === "perf_plans"   && <PerformanceInsightsScreen view="plans"   lastRefresh={lastRefresh}/>}
          {route === "perf_indexes" && <PerformanceInsightsScreen view="indexes" lastRefresh={lastRefresh}/>}
          {route === "perf_bloat"   && <PerformanceInsightsScreen view="bloat"   lastRefresh={lastRefresh}/>}
          {route === "perf_vacuum"  && <PerformanceInsightsScreen view="vacuum"  lastRefresh={lastRefresh}/>}
          {route === "perf_slow"    && <PerformanceInsightsScreen view="slow"    lastRefresh={lastRefresh}/>}
          {route === "admin_databases"  && <DatabaseAdminScreen      lastRefresh={lastRefresh} onRoute={setRoute}/>}
          {route === "admin_live"       && <LiveDatabaseConnectScreen lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "sql_workbench"    && <SqlWorkbenchScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "admin_schemas"    && <SchemaObjectsAdminScreen lastRefresh={lastRefresh}/>}
          {route === "admin_roles"      && <RolesAdminScreen         lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "admin_privileges" && <PrivilegesAdminScreen    lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "admin_hba"        && <HbaAdminScreen           lastRefresh={lastRefresh}/>}
          {route === "admin_extensions" && <ExtensionsAdminScreen    lastRefresh={lastRefresh}/>}
          {route === "config_parameters"  && <ConfigurationScreen view="parameters"  lastRefresh={lastRefresh}/>}
          {route === "config_patroni"     && <ConfigurationScreen view="patroni"     lastRefresh={lastRefresh}/>}
          {route === "config_roles"       && <ConfigurationScreen view="roles"       lastRefresh={lastRefresh}/>}
          {route === "config_databases"   && <ConfigurationScreen view="databases"   lastRefresh={lastRefresh}/>}
          {route === "config_maintenance" && <ConfigurationScreen view="maintenance" lastRefresh={lastRefresh}/>}
          {route === "parameter_drift" && <ParameterDriftScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "extension_governance" && <ExtensionGovernanceScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "repl_topology" && <ReplicationHAScreen view="topology" lastRefresh={lastRefresh}/>}
          {route === "repl_sync"     && <ReplicationHAScreen view="sync"     lastRefresh={lastRefresh}/>}
          {route === "repl_logical"  && <ReplicationHAScreen view="logical"  lastRefresh={lastRefresh}/>}
          {route === "repl_fdw"      && <ReplicationHAScreen view="fdw"      lastRefresh={lastRefresh}/>}
          {route === "repl_history"  && <ReplicationHAScreen view="history"  lastRefresh={lastRefresh}/>}
          {route === "geo_topology"  && <GeoTopologyScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "replica_workflow" && <ReplicaWorkflowScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "migration_wizard" && <MigrationWizardScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "sec_auth"       && <SecurityComplianceScreen view="auth"       lastRefresh={lastRefresh}/>}
          {route === "sec_tls"        && <SecurityComplianceScreen view="tls"        lastRefresh={lastRefresh}/>}
          {route === "sec_pgaudit"    && <SecurityComplianceScreen view="pgaudit"    lastRefresh={lastRefresh}/>}
          {route === "sec_compliance" && <SecurityComplianceScreen view="compliance" lastRefresh={lastRefresh}/>}
          {route === "sec_sensitive"  && <SecurityComplianceScreen view="sensitive"  lastRefresh={lastRefresh}/>}
          {route === "security_posture" && <SecurityPostureScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "network_access"   && <NetworkAccessScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "access_rules"     && <AccessRulesScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "encryption"       && <EncryptionScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "event_streaming"  && <EventStreamingScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "access_review"    && <AccessReviewScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "life_provision"    && <LifecycleScreen view="provision"    lastRefresh={lastRefresh}/>}
          {route === "life_scale"        && <LifecycleScreen view="scale"        lastRefresh={lastRefresh}/>}
          {route === "life_replicas"     && <LifecycleScreen view="replicas"     lastRefresh={lastRefresh}/>}
          {route === "life_upgrade"      && <LifecycleScreen view="upgrade"      lastRefresh={lastRefresh}/>}
          {route === "blue_green_upgrade" && <BlueGreenUpgradeScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "storage_autoscale" && <StorageAutoscaleScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "life_decommission" && <LifecycleScreen view="decommission" lastRefresh={lastRefresh}/>}
          {route === "dr_readiness" && <DrReadinessScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "recovery_assurance" && <RecoveryAssuranceScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "restore_window" && <RestoreWindowScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "snapshots"   && <SnapshotsScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "sla_compliance" && <SlaComplianceScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "cutover"     && <CutoverScreen     lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "readiness"   && <ReadinessScreen   lastRefresh={lastRefresh}/>}
          {route === "ops_inbox"   && <OpsInboxScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "change_calendar" && <ChangeCalendarScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "maintenance_feed" && <MaintenanceFeedScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "maintenance_scheduler" && <MaintenanceSchedulerScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "alert_rules" && <AlertRulesScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "platform_health" && <PlatformHealthScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "quotas" && <QuotasScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "tags" && <TagsScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "activity_stream" && <ActivityStreamScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "log_analytics" && <LogAnalyticsScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "incident_packs" && <IncidentPacksScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "estate_matrix" && <EstateInventoryScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "version_readiness" && <VersionReadinessScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "evidence_export" && <EvidenceExportScreen cluster={cluster} lastRefresh={lastRefresh}/>}
          {route === "ai_ops"        && <AIOpsScreen         cluster={cluster} lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "ai_agent"      && <AiAgentScreen       cluster={cluster} lastRefresh={lastRefresh} currentUser={me}/>}
          {route === "assistant"      && <AssistantScreen      cluster={cluster} lastRefresh={lastRefresh} currentUser={me}/>}

          <div className="workspace-footer">
            <img src="assets/hbz-logo.png" alt="" className="footer-logo"/>
            <span><strong style={{color:"var(--hbz-green)"}}>PostgreSQL Enterprise Console</strong></span>
            <span className="footer-sep">·</span>
            <span className="footer-tag">hplus gen2 corporate platform</span>
            <span className="footer-sep">·</span>
            <span>PostgreSQL Operations Console v2.5.0</span>
            <span className="footer-sep">·</span>
            <span>cluster <span className="mono">{cluster.name}</span></span>
            <div className="footer-right">
              <a href="#" onClick={(e) => { e.preventDefault(); setRoute("help"); }}>Runbooks</a>
              <a href="#">Security Advisory</a>
              <a href="#" onClick={(e) => { e.preventDefault(); setRoute("audit"); }}>Audit Log</a>
              <a href="#" onClick={(e) => { e.preventDefault(); setRoute("settings"); }}>Support</a>
            </div>
          </div>

        </div>
      </div>

      {run && <RunStatus run={run} onClose={() => setRun(null)} currentUser={me}/>}
      {paletteOpen && <CommandPalette user={me} route={route} onRoute={setRoute} onClose={() => setPaletteOpen(false)}/>}
      <ToastHost toasts={toasts} onDismiss={dismissToast}/>
    </div>
  );
}

class ConsoleErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error: error }; }
  componentDidCatch(error, info) { console.error("Object Monitor route render failed", error, info); }
  render() {
    if (!this.state.error) return this.props.children;
    return <div className="page" style={{padding: 24}}><div className="tile-error"><strong>Panel rendering failed.</strong><span className="mono">{String(this.state.error.message || this.state.error)}</span><button className="btn sm" onClick={() => window.location.reload()}>Reload console</button></div></div>;
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(<ConsoleErrorBoundary><App/></ConsoleErrorBoundary>);
