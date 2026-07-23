// Shared components: TopBar, LeftRail, KPI tiles, Gauges, Charts, Modal, Drawer, RunStatus

const { useState, useEffect, useRef, useMemo, useCallback } = React;

/* ===================== TopBar ===================== */
function HBZCrest({ size = 36 }) {
  // Simplified heraldic crest in HBZ green — lion-and-ribbon nod
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="hbzg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1E8467"/>
          <stop offset="1" stopColor="#36b37e"/>
        </linearGradient>
      </defs>
      {/* shield body */}
      <path d="M32 4 L56 10 L56 32 C56 45 45 56 32 60 C19 56 8 45 8 32 L8 10 Z"
            fill="url(#hbzg)" stroke="#36b37e" strokeWidth="1.2"/>
      {/* inner panel */}
      <path d="M32 9 L51 13.6 L51 32 C51 42.5 42.5 52 32 55.4 C21.5 52 13 42.5 13 32 L13 13.6 Z"
            fill="none" stroke="#FFFFFF" strokeWidth="1" opacity=".7"/>
      {/* arch / calligraphic flourish */}
      <path d="M22 18 Q32 8 42 18" fill="none" stroke="#FFFFFF" strokeWidth="1.2" opacity=".9"/>
      <circle cx="32" cy="14" r="1.6" fill="#FFFFFF"/>
      {/* stylized lion silhouette */}
      <path d="M18 36 Q19 30 24 29 L26 27 L29 28 L32 26 Q36 27 38 30 L42 30 L44 33 L43 36 L41 38 L42 42 L38 41 L36 43 L33 42 L30 43 L26 42 L23 43 L20 41 Z"
            fill="#FFFFFF"/>
      {/* stars below */}
      <g fill="#FFFFFF">
        <circle cx="24" cy="48" r="1.2"/>
        <circle cx="28" cy="49" r="1.2"/>
        <circle cx="32" cy="49.5" r="1.4"/>
        <circle cx="36" cy="49" r="1.2"/>
        <circle cx="40" cy="48" r="1.2"/>
      </g>
    </svg>
  );
}

function userInitials(name) {
  var s = String(name || "").trim();
  if (!s) return "—";
  var parts = s.replace(/@.*$/, "").split(/[.\s_-]+/).filter(Boolean);
  if (!parts.length) return s.slice(0, 2).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function TopBar({ crumbs, user, notifications, onSearchOpen, onSettings, theme, onToggleTheme }) {
  // Phase 4: bind identity to the real /api/v1/me payload (no hardcoded user).
  var displayName = (user && (user.email || user.username || user.name)) || "Not signed in";
  var tenant = (user && (user.tenant || user.workspace || user.preferred_role || user.role)) || "UAT PG Ops";
  var initials = userInitials((user && (user.email || user.username || user.name)) || "");
  var notifCount = Number(notifications || 0);
  var darkOn = theme === "dark";
  return (
    <div className="topbar">
      {/* Title — exact hplus #title: h2 { color:#7c3aed; font:Times New Roman 40px 500; mt:10px } */}
      <div className="topbar-brand">
        <div className="topbar-title">PostgreSQL Enterprise Console</div>
      </div>
      <div className="topbar-crumbs">
        {crumbs && crumbs.map(function(c, i) {
          return (
            <React.Fragment key={i}>
              <span className={i === crumbs.length - 1 ? "current" : ""}>{c}</span>
              {i < crumbs.length - 1 && <span className="sep"><Icon.ChevronRight size={12}/></span>}
            </React.Fragment>
          );
        })}
      </div>
      <div className="spacer"/>
      <div className="topbar-rightbrand"></div>
      <div className="spacer"/>
      <button className="icon-btn" aria-label="Global search (Ctrl-K)" title="Global search (Ctrl-K)" onClick={onSearchOpen}><Icon.Search size={16}/></button>
      <button className="icon-btn" aria-label="Command palette (Ctrl-K)" title="Command palette (Ctrl-K)" onClick={onSearchOpen}><Icon.Terminal size={16}/></button>
      <button className="icon-btn" aria-label={notifCount > 0 ? notifCount + " notifications" : "Notifications"} title="Notifications" onClick={onSettings}>
        <Icon.Bell size={16}/>
        {notifCount > 0 && <span className="badge">{notifCount > 99 ? "99+" : notifCount}</span>}
      </button>
      <button className="icon-btn" aria-label={darkOn ? "Switch to light theme" : "Switch to dark theme"} title={darkOn ? "Light theme" : "Dark theme"} onClick={onToggleTheme}>
        {darkOn ? <Icon.Sun size={16}/> : <Icon.Moon size={16}/>}
      </button>
      <button className="icon-btn" aria-label="Settings" title="Settings" onClick={onSettings}><Icon.Settings size={16}/></button>
      <div className="user-chip">
        <div className="avatar">{initials}</div>
        <div className="tenant">
          <strong>{displayName}</strong>
          <span>{tenant}</span>
        </div>
      </div>
    </div>
  );
}

function BandBar({ items, value, onChange }) {
  return (
    <div className="bandbar" role="tablist">
      {items.map(it => (
        <button key={it.key}
                className={"band-btn" + (value === it.key ? " active" : "")}
                aria-current={value === it.key ? "page" : undefined}
                onClick={() => onChange(it.key)}>
          <it.icon size={16}/>
          <span>{it.label}</span>
        </button>
      ))}
    </div>
  );
}

/* ===================== Navigation model ===================== */
// Single source of truth for all rail groups. Used by both LeftRail and the
// command palette so the two never drift apart.
function buildNavGroups(user) {
  const roleRank = { viewer: 0, operator: 1, dba: 2, admin: 3 };
  const userRole = user && (user.role || user.preferred_role)
    ? String(user.role || user.preferred_role).toLowerCase()
    : "viewer";
  const canDba = (roleRank[userRole] || 0) >= roleRank.dba;
  return [
    { id: "monitoring", title: "Monitoring", items: [
      { key: "overview",    label: "Overview",          icon: Icon.LayoutDashboard },
      { key: "openshift_overview", label: "OpenShift Overview", icon: Icon.Cloud },
      { key: "connect_hub", label: "Connect", icon: Icon.Terminal },
      { key: "endpoints",   label: "Endpoints & Listeners", icon: Icon.GitBranch },
      { key: "host_monitoring", label: "Host / OS Monitoring", icon: Icon.Activity },
      { key: "health_grid", label: "Cluster Health Grid", icon: Icon.Activity },
      { key: "memory_sga", label: "Memory / SGA", icon: Icon.Database },
      { key: "logs_explorer", label: "Logs Explorer", icon: Icon.FileText },
      { key: "cluster",     label: "Cluster / Patroni", icon: Icon.Layers },
      { key: "performance", label: "Performance",       icon: Icon.Activity },
      { key: "objects",     label: "Object Metrics",    icon: Icon.Database },
      { key: "pgbouncer_deep", label: "PgBouncer Pools", icon: Icon.Users },
      { key: "pgbouncer_advanced", label: "PgBouncer Diagnostics", icon: Icon.Users },
      { key: "wal_archive", label: "WAL & Archive", icon: Icon.HardDrive },
      { key: "storage_health", label: "Storage Health", icon: Icon.Cloud },
      { key: "alerts",      label: "Alerts & Insights", icon: Icon.AlertTriangle },
    ]},
    { id: "appmon", title: "Application Monitoring", items: [
      { key: "appmon", label: "Estate Overview", icon: Icon.Activity },
      { key: "appmon_tps", label: "TPS & Warehouse", icon: Icon.TrendingUp },
      { key: "appmon_service", label: "Service & Gateway", icon: Icon.Users },
      { key: "appmon_apps", label: "Charge · Locker · Mobile · Doc", icon: Icon.Database },
      { key: "appmon_repl", label: "Replication & DBA", icon: Icon.RefreshCw },
      { key: "appmon_business", label: "Banking Business", icon: Icon.TrendingUp },
      { key: "appmon_mgmt", label: "Management Scorecard", icon: Icon.LayoutDashboard },
    ]},
    { id: "ai", title: "AI Operations", items: [
      { key: "ai_ops", label: "AI Ops Console", icon: Icon.Zap },
      { key: "ai_agent", label: "AI DBA Agent", icon: Icon.Zap },
      { key: "assistant", label: "AI Assistant", icon: Icon.MessageCircle || Icon.Zap },
      { key: "ai_dba_recommendations", label: "AI DBA Recommendations", icon: Icon.CheckCircle },
      { key: "aip_overview", label: "AI Overview", icon: Icon.LayoutDashboard },
      { key: "aip_inbox", label: "Recommendations Inbox", icon: Icon.AlertTriangle },
      { key: "aip_agents", label: "Agent Scheduler", icon: Icon.Activity },
      { key: "aip_executor", label: "Controlled Executor", icon: Icon.Terminal },
      { key: "aip_approvals", label: "DBA Approvals", icon: Icon.Users },
      { key: "aip_gateway", label: "Model Gateway", icon: Icon.Zap },
      { key: "aip_rag", label: "RAG Knowledge Base", icon: Icon.Database },
      { key: "aip_evidence", label: "Evidence Packs", icon: Icon.FileText },
      { key: "aip_audit", label: "AI Audit Logs", icon: Icon.FileText },
      { key: "aip_governance", label: "AI Governance", icon: Icon.Layers },
      { key: "ai_nlsql", label: "Ask Your Database", icon: Icon.Terminal },
      { key: "ai_vector", label: "Vector & RAG Monitor", icon: Icon.Database },
      { key: "ai_agents", label: "AI Agent Governance", icon: Icon.Users },
      { key: "ai_branching", label: "Branching & Forks", icon: Icon.GitBranch },
    ]},
    { id: "perf", title: "Performance Insights", items: [
      { key: "metrics_explorer", label: "Metrics Explorer", icon: Icon.TrendingUp },
      { key: "db_load_timeline", label: "DB Load Timeline", icon: Icon.Activity },
      { key: "plan_explorer", label: "Plan Explorer", icon: Icon.FileText },
      { key: "sql_insight", label: "SQL Insight", icon: Icon.Zap },
      { key: "pg_profile", label: "Performance History", icon: Icon.Clock },
      { key: "auto_tuning", label: "Index & Auto-Tuning", icon: Icon.Sliders },
      { key: "perf_activity", label: "Application Activity", icon: Icon.Users },
      { key: "perf_topsql",  label: "Top SQL",          icon: Icon.TrendingUp },
      { key: "perf_waits",   label: "Wait Events",      icon: Icon.Activity },
      { key: "perf_plans",   label: "Plan Cache",       icon: Icon.FileText },
      { key: "perf_indexes", label: "Index Advisor",    icon: Icon.Search },
      { key: "perf_bloat",   label: "Bloat",            icon: Icon.Database },
      { key: "perf_vacuum",  label: "Vacuum Insights",  icon: Icon.RefreshCw },
      { key: "perf_slow",    label: "Slow Queries",     icon: Icon.Clock },
    ]},
    { id: "advisor", title: "Advisor & Health", items: [
      { key: "advisor",         label: "Advisor",         icon: Icon.CheckCircle },
      { key: "cloud_advisor",   label: "Cloud Advisor",   icon: Icon.CheckCircle },
      { key: "capacity_optimizer", label: "Cost / Capacity Optimizer", icon: Icon.Sliders },
      { key: "capacity_planning", label: "Capacity Planning", icon: Icon.TrendingUp },
      { key: "anomalies", label: "Anomaly Detection", icon: Icon.AlertTriangle },
      { key: "cost_showback", label: "Cost Showback & Budgets", icon: Icon.Sliders },
      { key: "resource_health", label: "Resource Health", icon: Icon.Activity },
      { key: "collector_health", label: "Collector Health", icon: Icon.Shield },
    ]},
    { id: "admin", title: "Database Administration", items: [
      { key: "admin_databases",  label: "Databases",         icon: Icon.Database },
      ...(canDba ? [{ key: "admin_live", label: "Live Connect", icon: Icon.Terminal }] : []),
      ...(canDba ? [{ key: "sql_workbench", label: "SQL Workbench", icon: Icon.Terminal }] : []),
      { key: "admin_schemas",    label: "Schemas & Objects", icon: Icon.Box },
      { key: "admin_roles",      label: "Users & Roles",     icon: Icon.Users },
      { key: "admin_privileges", label: "Privileges",        icon: Icon.Shield },
      { key: "admin_hba",        label: "HBA Rules",         icon: Icon.Lock },
      { key: "admin_extensions", label: "Extensions",        icon: Icon.Plus },
    ]},
    { id: "config", title: "Configuration", items: [
      { key: "config_parameters",  label: "Server Parameters", icon: Icon.Sliders },
      { key: "config_patroni",     label: "Patroni DCS Config", icon: Icon.Settings },
      { key: "config_roles",       label: "Per-Role Settings", icon: Icon.Users },
      { key: "config_databases",   label: "Per-Database Settings", icon: Icon.Database },
      { key: "config_maintenance", label: "Maintenance Mode", icon: Icon.Pause },
      { key: "parameter_drift", label: "Parameter Drift", icon: Icon.Sliders },
      { key: "extension_governance", label: "Extension Governance", icon: Icon.Plus },
    ]},
    { id: "repl", title: "Replication & HA", items: [
      { key: "repl_topology", label: "Topology", icon: Icon.GitBranch },
      { key: "repl_sync",     label: "Sync Standbys", icon: Icon.Activity },
      { key: "repl_logical",  label: "Logical Replication", icon: Icon.LinkIcon },
      { key: "repl_fdw",      label: "Foreign Data Wrappers", icon: Icon.Globe },
      { key: "repl_history",  label: "Switchover History", icon: Icon.Clock },
      { key: "geo_topology",  label: "Geo Replica Topology", icon: Icon.Globe },
      { key: "replica_workflow", label: "Replica & Promotion", icon: Icon.GitBranch },
      { key: "migration_wizard", label: "Migration Wizard", icon: Icon.ArrowRight },
    ]},
    { id: "security", title: "Security & Compliance", items: [
      { key: "sec_auth",       label: "Authentication", icon: Icon.Lock },
      { key: "sec_tls",        label: "TLS Certificates", icon: Icon.Shield },
      { key: "sec_pgaudit",    label: "pgaudit Settings", icon: Icon.Sliders },
      { key: "sec_compliance", label: "Compliance Reports", icon: Icon.CheckCircle },
      { key: "sec_sensitive",  label: "Sensitive Data", icon: Icon.Search },
      { key: "security_posture", label: "Security Posture", icon: Icon.Shield },
      { key: "network_access",  label: "Network / Private Access", icon: Icon.Globe },
      { key: "access_rules",    label: "Firewall / Access Rules", icon: Icon.Lock },
      { key: "encryption",      label: "Encryption & Keys", icon: Icon.Shield },
      { key: "event_streaming", label: "Event Streaming / SIEM", icon: Icon.ArrowRight },
      { key: "access_review",   label: "Access Recertification", icon: Icon.CheckCircle },
    ]},
    { id: "lifecycle", title: "Lifecycle", items: [
      { key: "life_provision",    label: "Provisioning", icon: Icon.Plus },
      { key: "life_scale",        label: "Scaling", icon: Icon.Sliders },
      { key: "life_replicas",     label: "Read Replicas", icon: Icon.GitBranch },
      { key: "life_upgrade",      label: "Upgrades", icon: Icon.ArrowRight },
      { key: "blue_green_upgrade", label: "Blue/Green Upgrade", icon: Icon.ArrowRight },
      { key: "storage_autoscale", label: "Storage Autoscale", icon: Icon.HardDrive },
      { key: "life_decommission", label: "Decommission", icon: Icon.StopCircle },
    ]},
    { id: "dr", title: "DR & Cutover", items: [
      { key: "dr_readiness", label: "DR Readiness", icon: Icon.Shield },
      { key: "recovery_assurance", label: "Recovery Assurance", icon: Icon.HardDrive },
      { key: "restore_window", label: "Restore Window (PITR)", icon: Icon.Clock },
      { key: "snapshots", label: "Snapshots & Clone/Fork", icon: Icon.HardDrive },
      { key: "sla_compliance", label: "SLA / RTO / RPO", icon: Icon.CheckCircle },
      { key: "cutover", label: "Cutover & Switchover", icon: Icon.RefreshCw },
    ]},
    { id: "ops", title: "Operations", items: [
      { key: "readiness",     label: "Environment Readiness", icon: Icon.CheckCircle },
      { key: "ops_inbox",     label: "Ops Inbox",        icon: Icon.Bell },
      { key: "change_calendar", label: "Change Calendar", icon: Icon.Clock },
      { key: "maintenance_feed", label: "Maintenance & Patch Feed", icon: Icon.Clock },
      { key: "maintenance_scheduler", label: "Maintenance Scheduler", icon: Icon.Clock },
      { key: "alert_rules", label: "Alert Rule Builder", icon: Icon.AlertTriangle },
      { key: "platform_health", label: "Platform Health", icon: Icon.Shield },
      { key: "quotas", label: "Quotas & Limits", icon: Icon.Sliders },
      { key: "tags", label: "Tags & Ownership", icon: Icon.Box },
      { key: "activity_stream", label: "Activity Stream", icon: Icon.Activity },
      { key: "log_analytics", label: "Log Analytics", icon: Icon.FileText },
      { key: "incident_packs", label: "Incident Packs", icon: Icon.FileText },
      { key: "estate_matrix", label: "Estate Matrix",    icon: Icon.Database },
      { key: "version_readiness", label: "Version Readiness", icon: Icon.ArrowRight },
      { key: "evidence_export", label: "Evidence Export", icon: Icon.Download },
      { key: "logs",          label: "Pod Logs",          icon: Icon.FileText },
      { key: "backups",       label: "Backups",           icon: Icon.HardDrive },
      { key: "runs",          label: "Run History",       icon: Icon.Terminal },
      { key: "audit",         label: "Audit & Commands",  icon: Icon.FileText },
      { key: "settings",      label: "Settings",          icon: Icon.Settings },
      { key: "notifications", label: "Notifications",     icon: Icon.Bell },
      { key: "tokens",        label: "API Tokens",        icon: Icon.Lock },
      { key: "tenants",       label: "Tenants",           icon: Icon.Users },
      { key: "help",          label: "Help & Runbooks",   icon: Icon.FileText },
    ]},
  ];
}

function flattenNav(groups) {
  var out = [];
  groups.forEach(function(g) {
    g.items.forEach(function(it) { out.push({ ...it, group: g.title }); });
  });
  return out;
}

/* ===================== Left Rail ===================== */
function LeftRail({ open, onToggle, route, onRoute, cluster, user }) {
  const groups = useMemo(function() { return buildNavGroups(user); }, [user]);
  const groupForRoute = useMemo(function() {
    for (var i = 0; i < groups.length; i++) {
      if (groups[i].items.some(function(it) { return it.key === route; })) return groups[i].id;
    }
    return groups[0].id;
  }, [groups, route]);

  const [collapsed, setCollapsed] = useState(function() {
    try { return JSON.parse(localStorage.getItem("hbz-rail-collapsed") || "{}") || {}; }
    catch (e) { return {}; }
  });
  // Always keep the group containing the active route open.
  const isOpen = function(id) { return id === groupForRoute ? true : !collapsed[id]; };
  const toggleGroup = function(id) {
    setCollapsed(function(prev) {
      var next = { ...prev, [id]: !prev[id] };
      try { localStorage.setItem("hbz-rail-collapsed", JSON.stringify(next)); } catch (e) {}
      return next;
    });
  };

  return (
    <div className="rail">
      <button className="rail-btn" onClick={onToggle} aria-label="Toggle navigation">
        <Icon.Menu size={18}/><span className="label">Collapse</span>
      </button>
      <div className="rail-sep"/>
      {groups.map(function(g, gi) {
        var opened = isOpen(g.id);
        return (
          <React.Fragment key={g.id}>
            {gi > 0 && <div className="rail-sep"/>}
            {open
              ? <button className={"rail-head rail-head-btn" + (opened ? " open" : "")}
                        aria-expanded={opened}
                        onClick={function() { toggleGroup(g.id); }}>
                  <span>{g.title}</span>
                  <Icon.ChevronDown size={13} className="rail-head-chevron"/>
                </button>
              : null}
            {(opened || !open) && g.items.map(function(n) {
              var I = n.icon;
              var active = route === n.key;
              return (
                <button key={n.key}
                        className={"rail-btn" + (active ? " active" : "")}
                        aria-current={active ? "page" : undefined}
                        data-route={n.key}
                        title={!open ? n.label : undefined}
                        onClick={function() { onRoute(n.key); }}>
                  <I size={18}/><span className="label">{n.label}</span>
                </button>
              );
            })}
          </React.Fragment>
        );
      })}
    </div>
  );
}

/* ===================== Command Palette (Ctrl-K) ===================== */
function CommandPalette({ user, route, onRoute, onClose }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [remoteIndex, setRemoteIndex] = useState([]);
  const inputRef = useRef(null);
  const all = useMemo(function() {
    var nav = flattenNav(buildNavGroups(user)).map(function(it) {
      return { ...it, type: "page", search: [it.label, it.group, it.key].join(" ") };
    });
    var seen = {};
    nav.forEach(function(it) { seen[it.type + ":" + it.key] = true; });
    (remoteIndex || []).forEach(function(it) {
      var key = (it.type || "item") + ":" + (it.key || it.label);
      if (!seen[key]) {
        seen[key] = true;
        nav.push({
          key: it.key || key,
          label: it.label || it.key,
          group: it.group || it.type || "Search",
          route: it.route,
          icon: Icon.Search,
          type: it.type || "item",
          search: [it.label, it.group, it.type, it.route, it.tags].join(" "),
        });
      }
    });
    return nav;
  }, [user, remoteIndex]);
  const results = useMemo(function() {
    var term = q.trim().toLowerCase();
    if (!term) return all.slice(0, 12);
    return all.filter(function(it) {
      return String(it.search || it.label || "").toLowerCase().indexOf(term) >= 0;
    }).slice(0, 20);
  }, [q, all]);

  useEffect(function() { if (inputRef.current) inputRef.current.focus(); }, []);
  useEffect(function() { setActive(0); }, [q]);
  useEffect(function() {
    var alive = true;
    fetch("/api/v1/search-index", { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(payload) { if (alive) setRemoteIndex(payload.items || []); })
      .catch(function() {
        if (!alive) return;
        var local = [];
        try {
          Object.keys(CLUSTERS || {}).forEach(function(cid) {
            var c = CLUSTERS[cid] || {};
            local.push({ type: "cluster", key: "cluster:" + cid, label: c.name || cid, group: "Clusters", route: "overview", tags: [c.role, c.region, c.namespace].join(" ") });
            (c.databases || []).forEach(function(db) {
              local.push({ type: "database", key: "db:" + cid + ":" + db.name, label: db.name, group: "Databases", route: "admin_databases", tags: cid });
            });
          });
        } catch (e) {}
        setRemoteIndex(local);
      });
    return function() { alive = false; };
  }, []);

  const go = function(it) { if (it) { onRoute(it.route || it.key); onClose(); } };
  const onKey = function(e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(function(a) { return Math.min(a + 1, results.length - 1); }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(function(a) { return Math.max(a - 1, 0); }); }
    else if (e.key === "Enter") { e.preventDefault(); go(results[active]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  return (
    <div className="cmdk-bg" onClick={onClose}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Command palette" onClick={function(e) { e.stopPropagation(); }}>
        <div className="cmdk-input">
          <Icon.Search size={16}/>
          <input ref={inputRef} value={q} placeholder="Jump to a page…"
                 aria-label="Search pages"
                 onChange={function(e) { setQ(e.target.value); }} onKeyDown={onKey}/>
          <span className="cmdk-hint">Esc</span>
        </div>
        <div className="cmdk-list" role="listbox">
          {results.map(function(it, i) {
            var I = it.icon;
            return (
              <button key={it.key}
                      className={"cmdk-row" + (i === active ? " active" : "") + (route === it.key ? " current" : "")}
                      role="option" aria-selected={i === active}
                      onMouseEnter={function() { setActive(i); }}
                      onClick={function() { go(it); }}>
                <I size={15}/>
                <span className="cmdk-label">{it.label}</span>
                <span className="cmdk-group">{it.group}</span>
              </button>
            );
          })}
          {!results.length && <div className="cmdk-empty">No matching pages, databases, jobs, alerts, runbooks, or clusters.</div>}
        </div>
      </div>
    </div>
  );
}

/* ===================== Action bar pieces ===================== */
function ClusterPicker({ value, onChange }) {
  var groups = [
    { role: "PROD", label: "Production" },
    { role: "DR",   label: "Disaster Recovery" },
    { role: "UAT",  label: "UAT" },
    { role: "LIVE", label: "Live" },
    { role: "LOCAL", label: "Local Test" },
  ];
  var byRole = { PROD: [], DR: [], UAT: [], LIVE: [], LOCAL: [] };
  Object.keys(CLUSTERS).forEach(function(id) {
    var c = CLUSTERS[id];
    if (byRole[c.role]) byRole[c.role].push(c);
  });
  var current = CLUSTERS[value] || {};
  var tone = current.role === "PROD" || current.role === "LIVE" ? "ok" : current.role === "DR" ? "info" : "muted";
  return (
    <div className="cluster-select-wrap" title={"Active cluster: " + (current.name || value)}>
      <Icon.Database size={14} className="cluster-select-ico"/>
      <select className="cluster-select" aria-label="Select cluster" value={value}
              onChange={function(e) { onChange(e.target.value); }}>
        {groups.map(function(g) {
          var items = byRole[g.role] || [];
          return items.length ? (
            <optgroup key={g.role} label={g.label}>
              {items.map(function(c) {
                return <option key={c.id} value={c.id}>{c.name}</option>;
              })}
            </optgroup>
          ) : null;
        })}
      </select>
      <Icon.ChevronDown size={13} className="cluster-select-caret"/>
      <span className={"pill " + tone + " cluster-env-tag"}>{current.label || "—"}</span>
    </div>
  );
}

function TimeRange({ value, onChange }) {
  const opts = [
    { k: "1h",  l: "Last 1 hour" },
    { k: "24h", l: "Last 24 hours" },
    { k: "7d",  l: "Last 7 days" },
    { k: "30d", l: "Last 30 days" },
  ];
  return (
    <div className="time-range">
      {opts.map(o => (
        <button key={o.k}
                className={value === o.k ? "active" : ""}
                onClick={() => onChange(o.k)}
                title={o.l}>
          {o.k}
        </button>
      ))}
    </div>
  );
}

/* ===================== KPI Tile ===================== */
function KPI({ color = "blue", label, value, sub, info, skeleton, spark }) {
  if (skeleton) {
    return (
      <div className={"kpi tile-skeleton"}>
        <div className="label">…</div>
        <div className="value">…</div>
      </div>
    );
  }
  var sparkColor = color === "red" || color === "pink" ? "var(--viz-critical)"
    : color === "orange" || color === "yellow" ? "var(--viz-warn)"
    : color === "blue" ? "var(--info)" : "var(--accent)";
  return (
    <div className={"kpi " + color + (spark && spark.length ? " has-spark" : "")}>
      <div className="label">
        {label}
        {info && <Icon.Info size={12} className="info" />}
      </div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
      {spark && spark.length ? (
        <div className="kpi-spark">
          <Sparkline data={spark} width={150} height={28} color={sparkColor} fill="transparent" />
        </div>
      ) : null}
    </div>
  );
}

/* ===================== Stat card ===================== */
function Stat({ label, value, unit, sub, delta, deltaDir, chart, info }) {
  return (
    <div className="card stat">
      <div className="lbl">
        {label}
        {info && <Icon.Info size={11} data-tip={info}/>}
      </div>
      <div className="val">{value}{unit && <span className="unit">{unit}</span>}</div>
      {sub && <div className="muted txt-xs">{sub}</div>}
      {delta != null && (
        <div className={"delta " + (deltaDir === "up" ? "up" : "down")}>
          {deltaDir === "up" ? <Icon.TrendingUp size={11}/> : <Icon.TrendingDown size={11}/>}
          {" "}{delta}
        </div>
      )}
      {chart && <div className="spark">{chart}</div>}
    </div>
  );
}

/* ===================== Sparkline ===================== */
function Sparkline({ data, width = 140, height = 36, color = "var(--viz-1)", fill = "var(--viz-area-1)" }) {
  if (!data || !data.length) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const pts = data.map((v, i) => [i * stepX, height - ((v - min) / range) * (height - 4) - 2]);
  const d = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = `M0 ${height} L${pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L")} L${width} ${height} Z`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <path d={area} fill={fill}/>
      <path d={d} fill="none" stroke={color} strokeWidth="1.5"/>
    </svg>
  );
}

/* ===================== Semi-circle Gauge ===================== */
function Gauge({ value, label, sub, thresholds = [60, 80], size = 168 }) {
  // value 0-100
  const v = Math.max(0, Math.min(100, value));
  const r = size / 2 - 14;
  const cx = size / 2, cy = size / 2 + 6;
  // semicircle from -180 to 0 degrees
  const angleAt = (pct) => -180 + (pct / 100) * 180;
  const polar = (deg) => [cx + r * Math.cos(deg * Math.PI / 180), cy + r * Math.sin(deg * Math.PI / 180)];
  const arc = (a1, a2, color, w = 12) => {
    const [x1, y1] = polar(a1), [x2, y2] = polar(a2);
    const large = (a2 - a1) > 180 ? 1 : 0;
    return <path d={`M${x1} ${y1} A${r} ${r} 0 ${large} 1 ${x2} ${y2}`} stroke={color} strokeWidth={w} fill="none" strokeLinecap="butt"/>;
  };
  const [t1, t2] = thresholds; // green up to t1, amber t1..t2, red beyond t2

  // bands
  const valColor = v < t1 ? "var(--gauge-green)" : v < t2 ? "var(--gauge-amber)" : "var(--gauge-red)";

  const a0 = -180, aFull = 0;
  const aGreenEnd = angleAt(t1);
  const aAmberEnd = angleAt(t2);
  const aValue = angleAt(v);

  return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center"}}>
      <svg width={size} height={size * 0.62}>
        {/* track */}
        {arc(a0, aFull, "var(--gauge-track)", 12)}
        {/* threshold bands shown as thinner outer ring */}
        {arc(a0, aGreenEnd, "rgba(45,143,45,.18)", 4)}
        {arc(aGreenEnd, aAmberEnd, "rgba(217,163,0,.30)", 4)}
        {arc(aAmberEnd, aFull, "rgba(197,48,48,.32)", 4)}
        {/* value */}
        {arc(a0, aValue, valColor, 12)}
        {/* center text */}
        <text x={cx} y={cy - 8} textAnchor="middle" fontSize="26" fontWeight="600" fill="var(--fg)">{v.toFixed(0)}%</text>
        <text x={cx} y={cy + 10} textAnchor="middle" fontSize="11" fill="var(--fg-dim)">{label}</text>
      </svg>
      {sub && <div className="muted txt-xs" style={{marginTop:-4}}>{sub}</div>}
    </div>
  );
}

/* ===================== Stacked bar (storage) ===================== */
function StackedBar({ used, total, label }) {
  const pct = (used / total) * 100;
  return (
    <div style={{padding: "6px 0"}}>
      <div className="flex-row" style={{justifyContent:"space-between", marginBottom: 4}}>
        <span className="txt-xs muted">{label}</span>
        <span className="txt-xs"><strong>{used.toFixed(0)} GiB</strong> / {total} GiB</span>
      </div>
      <div style={{height: 18, background: "var(--surface-3)", borderRadius: 3, overflow:"hidden", border:"1px solid var(--border)"}}>
        <div style={{
          width: pct + "%", height: "100%",
          background: pct > 80 ? "var(--danger)" : pct > 60 ? "var(--warn)" : "var(--ok)",
        }}/>
      </div>
      <div className="flex-row" style={{justifyContent:"space-between", marginTop:4}}>
        <span className="txt-xs muted">Used {pct.toFixed(1)}%</span>
        <span className="txt-xs muted">Free {(total - used).toFixed(0)} GiB</span>
      </div>
    </div>
  );
}

/* ===================== Pie chart (database sizes) ===================== */
function PieChart({ data, size = 200 }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const cx = size / 2, cy = size / 2, r = size / 2 - 10, rInner = r * 0.55;
  let acc = 0;
  const slices = data.map((d) => {
    const start = acc / total * 360;
    acc += d.value;
    const end = acc / total * 360;
    return { ...d, start, end };
  });
  const toPt = (deg, rad) => {
    const a = (deg - 90) * Math.PI / 180;
    return [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
  };
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {slices.map((s, i) => {
        const [x1, y1] = toPt(s.start, r);
        const [x2, y2] = toPt(s.end, r);
        const [x3, y3] = toPt(s.end, rInner);
        const [x4, y4] = toPt(s.start, rInner);
        const large = s.end - s.start > 180 ? 1 : 0;
        const d = [
          `M${x1} ${y1}`,
          `A${r} ${r} 0 ${large} 1 ${x2} ${y2}`,
          `L${x3} ${y3}`,
          `A${rInner} ${rInner} 0 ${large} 0 ${x4} ${y4}`,
          "Z"
        ].join(" ");
        return <path key={i} d={d} fill={s.color} stroke="#fff" strokeWidth="1"/>;
      })}
      <text x={cx} y={cy - 4} textAnchor="middle" fontSize="11" fill="var(--fg-dim)">Total</text>
      <text x={cx} y={cy + 14} textAnchor="middle" fontSize="16" fontWeight="600" fill="var(--fg)">
        {total.toLocaleString()} GiB
      </text>
    </svg>
  );
}

/* ===================== Mini line chart ===================== */
function MiniLine({ data, color = "var(--viz-1)", height = 60, fill = "var(--viz-area-1)", axis = false }) {
  if (!data || !data.length) return null;
  const w = 280;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const stepX = w / (data.length - 1);
  const pts = data.map((v, i) => [i * stepX, height - ((v - min) / range) * (height - 6) - 3]);
  const d = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = `M0 ${height} L${pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L")} L${w} ${height} Z`;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} width="100%" height={height} preserveAspectRatio="none">
      {axis && [0.25, 0.5, 0.75].map(p => (
        <line key={p} x1="0" x2={w} y1={height * p} y2={height * p} stroke="var(--divider)" strokeDasharray="2 3"/>
      ))}
      <path d={area} fill={fill}/>
      <path d={d} fill="none" stroke={color} strokeWidth="1.6"/>
    </svg>
  );
}


/* ===================== Shared phase charts ===================== */
const CHART_COLORS = (typeof HBZ_CHART_PALETTE !== "undefined")
  ? HBZ_CHART_PALETTE
  : ["#7c3aed", "#00b8d9", "#f59e0b", "#14b8a6", "#2f9fe8", "#ec4899", "#6c757d", "#ef476f"];

function chartColor(index, tone) {
  if (tone === "ok") return "#107c10";
  if (tone === "warn") return "#f59e0b";
  if (tone === "danger") return "#ef476f";
  if (tone === "info") return "#00b8d9";
  if (tone === "teal") return "#14b8a6";
  if (tone === "purple") return "#2f9fe8";
  return CHART_COLORS[index % CHART_COLORS.length];
}

function normalizeChartRows(rows, limit) {
  var list = (rows || []).filter(function(row) { return Number(row.value || 0) > 0; });
  list.sort(function(a, b) { return Number(b.value || 0) - Number(a.value || 0); });
  return list.slice(0, limit || 8);
}

function phaseCountRows(rows, keyFn, toneFn) {
  var counts = {};
  (rows || []).forEach(function(row) {
    var key = keyFn(row) || "unknown";
    counts[key] = (counts[key] || 0) + 1;
  });
  return Object.keys(counts).map(function(key) {
    var tone = toneFn ? toneFn(key) :
      (key === "succeeded" || key === "active" || key === "ok" || key === "enabled" ? "ok" :
       key === "failed" || key === "critical" || key === "danger" || key === "revoked" ? "danger" :
       key === "pending_approval" || key === "warning" || key === "warn" || key === "disabled" ? "warn" :
       key === "running" || key === "info" ? "info" : "muted");
    return { label: key, value: counts[key], tone: tone };
  });
}

/* ===================== Empty state (B4) ===================== */
function EmptyState({ title, hint, icon, source, actionLabel, onAction }) {
  var IconCmp = icon || Icon.Database;
  return (
    <div className="empty-state">
      <IconCmp size={28}/>
      <div className="es-title">{title || "No data available"}</div>
      {hint && <div className="es-hint">{hint}</div>}
      {source && <SourceBadge source={source}/>}
      {actionLabel && onAction && <button className="btn sm" onClick={onAction}>{actionLabel}</button>}
    </div>
  );
}

function BarList({ title, rows, limit = 8, valueFormatter, emptyText }) {
  var list = normalizeChartRows(rows, limit);
  var max = Math.max(1, ...list.map(function(row) { return Number(row.value || 0); }));
  return (
    <div className="chart-list">
      {title && <div className="chart-title">{title}</div>}
      {list.map(function(row, index) {
        var value = Number(row.value || 0);
        var pct = Math.max(2, (value / max) * 100);
        return (
          <div key={row.key || row.label || index} className="chart-bar-row">
            <div className="chart-bar-head">
              <span title={row.label}>{row.label}</span>
              <strong>{valueFormatter ? valueFormatter(value, row) : fmtInt(value)}</strong>
            </div>
            <div className="chart-bar-track">
              <div className="chart-bar-fill" style={{width: pct + "%", background: chartColor(index, row.tone)}}/>
            </div>
            {row.sub && <div className="chart-sub">{row.sub}</div>}
          </div>
        );
      })}
      {!list.length && <EmptyState icon={Icon.TrendingUp} title="No chart data" hint={emptyText || "There's nothing to chart for the current selection yet."}/>}
    </div>
  );
}

function DonutChart({ title, rows, center, sub, size = 150, valueFormatter }) {
  var list = normalizeChartRows(rows, 10);
  var total = list.reduce(function(sum, row) { return sum + Number(row.value || 0); }, 0);
  var stroke = 18;
  var radius = (size - stroke) / 2;
  var circumference = 2 * Math.PI * radius;
  var offset = 0;
  return (
    <div className="chart-donut-panel">
      {title && <div className="chart-title">{title}</div>}
      <div className="chart-donut-wrap">
        <svg className="chart-donut" width={size} height={size} viewBox={"0 0 " + size + " " + size}>
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#edf1f3" strokeWidth={stroke}/>
          {list.map(function(row, index) {
            var value = Number(row.value || 0);
            var dash = total <= 0 ? 0 : (value / total) * circumference;
            var segment = (
              <circle key={row.key || row.label || index}
                      cx={size / 2}
                      cy={size / 2}
                      r={radius}
                      fill="none"
                      stroke={chartColor(index, row.tone)}
                      strokeWidth={stroke}
                      strokeDasharray={dash + " " + circumference}
                      strokeDashoffset={-offset}
                      strokeLinecap="butt"
                      transform={"rotate(-90 " + (size / 2) + " " + (size / 2) + ")"}/>
            );
            offset += dash;
            return segment;
          })}
        </svg>
        <div className="chart-donut-center">
          <strong>{center || (valueFormatter ? valueFormatter(total) : fmtInt(total))}</strong>
          {sub && <span>{sub}</span>}
        </div>
      </div>
      <div className="chart-legend">
        {list.map(function(row, index) {
          var value = Number(row.value || 0);
          return (
            <span key={row.key || row.label || index}>
              <i style={{background: chartColor(index, row.tone)}}/>
              {row.label} {valueFormatter ? valueFormatter(value, row) : fmtInt(value)}
            </span>
          );
        })}
        {!list.length && <span className="muted">No segments</span>}
      </div>
    </div>
  );
}

function StatusBreakdown({ rows, valueFormatter }) {
  var list = normalizeChartRows(rows, 12);
  var total = list.reduce(function(sum, row) { return sum + Number(row.value || 0); }, 0);
  return (
    <div className="chart-status">
      <div className="chart-status-bar">
        {list.map(function(row, index) {
          var pct = total <= 0 ? 0 : (Number(row.value || 0) / total) * 100;
          return <div key={row.key || row.label || index}
                      style={{width: pct + "%", background: chartColor(index, row.tone)}}
                      title={row.label + ": " + (valueFormatter ? valueFormatter(row.value, row) : fmtInt(row.value))}/>;
        })}
      </div>
      <div className="chart-legend compact">
        {list.map(function(row, index) {
          return <span key={row.key || row.label || index}><i style={{background: chartColor(index, row.tone)}}/>{row.label} {valueFormatter ? valueFormatter(row.value, row) : fmtInt(row.value)}</span>;
        })}
      </div>
    </div>
  );
}

function TimelineStrip({ rows, emptyText }) {
  var list = rows || [];
  return (
    <div className="chart-timeline">
      {list.map(function(row, index) {
        return (
          <div key={row.key || index} className={"chart-time-dot " + (row.tone || "info")} title={row.label || ""}>
            <span/>
            <strong>{row.title}</strong>
            {row.sub && <em>{row.sub}</em>}
          </div>
        );
      })}
      {!list.length && <EmptyState icon={Icon.Clock} title="No timeline data" hint={emptyText || "No events have been recorded for this view yet."}/>}
    </div>
  );
}

function sourceBadgeTone(source) {
  var s = String(source || "").toLowerCase();
  if (s.indexOf("fallback") >= 0 || s.indexOf("default") >= 0) return "warn";
  if (s.indexOf("prometheus") >= 0) return "info";
  if (s.indexOf("object") >= 0 || s.indexOf("sample") >= 0 || s.indexOf("rollup") >= 0) return "info";
  if (s.indexOf("metadata") >= 0 || s.indexOf("audit") >= 0 || s.indexOf("job") >= 0) return "muted";
  if (s.indexOf("remote") >= 0 || s.indexOf("agent") >= 0) return "info";
  if (s.indexOf("postgres") >= 0 || s.indexOf("pg_") >= 0 || s.indexOf("catalog") >= 0) return "ok";
  return "muted";
}

function sourceBadgeLabel(source) {
  var s = String(source || "").trim();
  if (!s) return "source unknown";
  var lower = s.toLowerCase();
  if (lower === "samples") return "object metrics samples";
  if (lower === "metric_sample_rollups") return "object metrics rollups";
  if (lower === "pg_stat_statements") return "pg_stat_statements";
  if (lower === "live postgresql stats") return "live PostgreSQL";
  if (lower === "live-postgresql") return "live PostgreSQL";
  if (lower === "object-metrics") return "object metrics";
  if (lower === "console-metadata") return "console metadata";
  return s.replace(/[-_]/g, " ");
}

function SourceBadge({ source, detail }) {
  return (
    <span className={"pill " + sourceBadgeTone(source)} title={detail || ("Data source: " + sourceBadgeLabel(source))}>
      <span className="dot"/>{sourceBadgeLabel(source)}
    </span>
  );
}

/* ===================== Request / form helpers ===================== */
// Maps an HTTP status to a clear, DBA-grade fallback message. The backend's
// own `detail`/`message` is always preferred when present (API contract intact);
// this only fills in when the server returned no usable text.
function hbzStatusMessage(status, statusText) {
  if (status === 400) return "The request was rejected as invalid (400). Please review the values and try again.";
  if (status === 401) return "Your session is not authenticated (401). Please sign in again.";
  if (status === 403) return "You do not have permission to perform this action (403). Check your role or request approval.";
  if (status === 404) return "The requested resource was not found (404). The API route may be unavailable for this cluster.";
  if (status === 408 || status === 504) return "The cluster API did not respond in time (" + status + "). It may be busy or unreachable.";
  if (status === 409) return "The action conflicts with the current cluster state (409). Refresh and try again.";
  if (status === 422) return "Some values failed validation (422). Please correct the highlighted fields.";
  if (status === 429) return "Too many requests (429). Please wait a moment and retry.";
  if (status === 502 || status === 503) return "The backend service is temporarily unavailable (" + status + "). Please check API and database connectivity.";
  if (status >= 500) return "The server could not complete the request (" + status + "). Please check API connectivity and try again.";
  return "Request failed (" + status + (statusText ? " " + statusText : "") + "). Please check API connectivity.";
}

function hbzJsonResponse(response) {
  return response.text().then(function(text) {
    var payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (e) {
        payload = { detail: text };
      }
    }
    if (!response.ok) {
      var detail = payload.detail || payload.message;
      var message = (typeof detail === "string" && detail.trim())
        ? detail
        : hbzStatusMessage(response.status, response.statusText);
      throw new Error(message);
    }
    return payload;
  });
}

// Normalizes any thrown error (incl. network failures) to friendly text for
// display. Use at .catch render sites that currently show raw err.message.
function hbzErrorText(err) {
  var msg = err && (err.message || String(err));
  if (!msg) return "An unexpected error occurred. Please try again.";
  if (/failed to fetch|networkerror|load failed/i.test(msg)) {
    return "Unable to reach the console API. Please check network connectivity and that the backend is running.";
  }
  return msg;
}

function hbzRequired(value) {
  return String(value === null || value === undefined ? "" : value).trim().length > 0;
}

function hbzPositiveNumber(value) {
  var n = Number(value);
  return Number.isFinite(n) && n > 0;
}

function hbzNameLike(value) {
  return /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(String(value || "").trim());
}

/* ===================== Modal ===================== */
function Modal({ children, onClose }) {
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>{children}</div>
    </div>
  );
}

/* ===================== Drawer (right slide) ===================== */
function Drawer({ children, onClose }) {
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <>
      <div className="drawer-bg" onClick={onClose}/>
      <div className="drawer" role="dialog" aria-modal="true">{children}</div>
    </>
  );
}

/* ===================== Toast ===================== */
function ToastHost({ toasts, onDismiss }) {
  if (!toasts || !toasts.length) return null;
  return (
    <div className="toast-host" role="status" aria-live="polite">
      {toasts.map(function(t) {
        var I = t.tone === "danger" ? Icon.AlertTriangle : t.tone === "warn" ? Icon.AlertCircle : Icon.CheckCircle;
        return (
          <div key={t.id} className={"toast " + (t.tone || "ok")}>
            <I size={15}/>
            <span>{t.msg}</span>
            <button className="toast-x" aria-label="Dismiss" onClick={function() { onDismiss(t.id); }}><Icon.X size={12}/></button>
          </div>
        );
      })}
    </div>
  );
}

// Hook returning [toasts, push, dismiss]; auto-dismisses after 3s.
function useToasts() {
  const [toasts, setToasts] = useState([]);
  const dismiss = useCallback(function(id) {
    setToasts(function(list) { return list.filter(function(t) { return t.id !== id; }); });
  }, []);
  const push = useCallback(function(msg, tone) {
    var id = Date.now() + "-" + Math.random().toString(36).slice(2, 7);
    setToasts(function(list) { return list.concat([{ id: id, msg: msg, tone: tone || "ok" }]); });
    setTimeout(function() { dismiss(id); }, 3000);
  }, [dismiss]);
  return [toasts, push, dismiss];
}

/* ===================== Approval banner ===================== */
function ApprovalBanner({ requestId, command, onView }) {
  return (
    <div className="approval-banner">
      <Icon.ShieldAlert size={16}/>
      <div style={{flex: 1}}>
        <strong>Restricted command pending approval</strong>
        {" — "}
        <span className="muted">{command}</span>
        {" "}<span className="req">req_{requestId}</span>
      </div>
      <button className="btn sm" onClick={onView}>View status</button>
    </div>
  );
}

/* ===================== Run Status (inside drawer) ===================== */
function RunStatus({ run, onClose, currentUser }) {
  if (!run) return null;
  var submittedBy = (currentUser && (currentUser.email || currentUser.username || currentUser.name)) || run.submittedBy || "—";
  const stages = ["pending", "approved", "running", run.status === "failed" ? "failed" : "succeeded"];
  const reached = (s) => stages.indexOf(s) <= stages.indexOf(run.status);
  return (
    <Drawer onClose={onClose}>
      <div className="hd">
        <Icon.Terminal size={16}/>
        <div>
          <div style={{fontWeight: 600, fontSize: 14}}>Command run</div>
          <div className="muted txt-xs">request_id <span className="mono">{run.requestId}</span></div>
        </div>
        <button className="btn ghost icon" style={{marginLeft:"auto"}} onClick={onClose} aria-label="Close"><Icon.X size={14}/></button>
      </div>
      <div className="bd">
        <div className="card" style={{padding: "10px 12px"}}>
          <div className="flex-row" style={{justifyContent: "space-between"}}>
            <div>
              <div className="txt-xs muted">Command</div>
              <div style={{fontWeight: 600}}>{run.command}</div>
            </div>
            <span className={"pill " + (run.status === "succeeded" ? "ok" : run.status === "failed" ? "danger" : "info")}>
              <span className="dot"/>
              {run.status.toUpperCase()}
            </span>
          </div>
          <div className="grid-2 mt-3 txt-sm">
            <div><div className="txt-xs muted">Cluster</div>{run.cluster}</div>
            <div><div className="txt-xs muted">Target</div>{run.target || "—"}</div>
            <div><div className="txt-xs muted">Submitted by</div>{submittedBy}</div>
            <div><div className="txt-xs muted">Approved by</div>{run.approver || "—"}</div>
          </div>
          {run.reason && <div className="mt-2"><div className="txt-xs muted">Reason</div>{run.reason}</div>}
        </div>

        <div className="section-h mt-3">Transitions</div>
        <div className="card" style={{padding: "8px 12px"}}>
          {[
            { k: "pending",   l: "Submitted",  t: run.times?.pending },
            { k: "approved",  l: "Approved",   t: run.times?.approved },
            { k: "running",   l: "Running",    t: run.times?.running },
            { k: run.status === "failed" ? "failed" : "succeeded", l: run.status === "failed" ? "Failed" : "Succeeded", t: run.times?.done },
          ].map((step, i) => {
            const done = reached(step.k);
            const cls = done ? "done" : i === stages.indexOf(run.status) + 1 ? "active" : "";
            const isFail = step.k === "failed";
            return (
              <div key={i} className={"run-step " + cls + (isFail && done ? " fail" : "")}>
                <span className="ico">
                  {isFail && done ? <Icon.X size={11}/> : done ? <Icon.Check size={11}/> : <Icon.Clock size={11}/>}
                </span>
                <span className="lbl">{step.l}</span>
                <span className="t">{step.t || "—"}</span>
              </div>
            );
          })}
        </div>

        <div className="section-h mt-3">Output</div>
        <div className="logbox">
          {run.log.map((line, i) => (
            <div key={i} className={line.t}>{line.s}</div>
          ))}
        </div>
      </div>
    </Drawer>
  );
}

/* ===================== ECharts wrapper (vendored, Phase A) =====================
   Apache ECharts is loaded as a global UMD script (static/vendor/echarts.min.js),
   matching the no-bundler multi-script architecture. Colors are resolved from
   the CSS custom properties at option-build time (canvas can't use var()), so
   charts follow the active light/dark theme; a MutationObserver on
   <html data-theme> rebuilds options when the theme flips. */

function vizVar(name, fallback) {
  try {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch (e) { return fallback; }
}

function vizColor(n) { return vizVar("--viz-" + (((n - 1) % 8) + 1), "#7c3aed"); }

function vizAlpha(hex, alpha) {
  // #RRGGBB -> rgba(); pass through anything already rgb/rgba.
  if (!hex || hex.charAt(0) !== "#" || hex.length !== 7) return hex;
  var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
  return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
}

function hbzEChartsBase() {
  var fgDim = vizVar("--fg-dim", "#6c757d");
  var fg = vizVar("--fg", "#212529");
  var border = vizVar("--border", "#e0e0e0");
  var divider = vizVar("--divider", "#eeeeee");
  var surface = vizVar("--surface", "#ffffff");
  return {
    textStyle: { fontFamily: "Poppins, -apple-system, 'Segoe UI', Roboto, sans-serif", color: fgDim },
    animationDuration: 350,
    grid: { left: 8, right: 14, top: 30, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", label: { backgroundColor: vizVar("--hbz-navy", "#2a2150") } },
      backgroundColor: surface,
      borderColor: border,
      textStyle: { color: fg, fontSize: 12 },
      extraCssText: "box-shadow: var(--shadow-2); border-radius: 4px;"
    },
    legend: { top: 2, left: 4, icon: "roundRect", itemWidth: 10, itemHeight: 10, textStyle: { color: fgDim, fontSize: 11 } },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: border } },
      axisTick: { show: false },
      axisLabel: { color: fgDim, fontSize: 10, hideOverlap: true },
      splitLine: { show: false }
    },
    yAxis: {
      type: "value",
      axisLabel: { color: fgDim, fontSize: 10 },
      splitLine: { lineStyle: { color: divider, type: "dashed" } }
    }
  };
}

/* Gradient-area line series (the "Azure look"): solid 2px line, fill fading
   from the series color @28% opacity to transparent. points = [[ts_ms, v], …];
   colorIndex is 1-based into the --viz-N palette (or pass {color: "#hex"}). */
function hbzAreaSeries(name, points, colorIndex, extra) {
  var color = (extra && extra.color) || vizColor(colorIndex || 1);
  var grad = (typeof echarts !== "undefined" && echarts.graphic)
    ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: vizAlpha(color, 0.28) },
        { offset: 1, color: vizAlpha(color, 0) }
      ])
    : vizAlpha(color, 0.15);
  return Object.assign({
    name: name,
    type: "line",
    data: points || [],
    smooth: 0.25,
    showSymbol: false,
    lineStyle: { width: 2, color: color },
    itemStyle: { color: color },
    areaStyle: { color: grad },
    emphasis: { focus: "series" }
  }, extra || {});
}

/* Unit-aware chart axis + tooltip. Reuses the global fmt* helpers so y-axis
   ticks and tooltips read in DBA units. unit ∈ "%","ms","s","bytes","bytes/s",
   "conn","tps","qps", or any literal suffix. Returns a formatter function. */
function hbzUnitFmt(unit) {
  return function(v) {
    if (v == null || (typeof v === "number" && isNaN(v))) return "—";
    var n = Number(v);
    switch (unit) {
      case "%":       return (typeof fmtPct === "function") ? fmtPct(n) : Math.round(n) + "%";
      case "ms":      return (typeof fmtMs === "function") ? fmtMs(n) : Math.round(n) + " ms";
      case "s":       return (typeof fmtSec === "function") ? fmtSec(n) : Math.round(n) + " s";
      case "bytes":   return (typeof fmtBytes === "function") ? fmtBytes(n) : Math.round(n) + " B";
      case "bytes/s": return ((typeof fmtBytes === "function") ? fmtBytes(n) : Math.round(n)) + "/s";
      case "conn":    return Math.round(n).toLocaleString() + (Math.abs(n) === 1 ? " connection" : " connections");
      case "tps":     return Math.round(n).toLocaleString() + " TPS";
      case "qps":     return Math.round(n).toLocaleString() + " QPS";
      default:        return Math.round(n).toLocaleString() + (unit ? " " + unit : "");
    }
  };
}

/* Merge a unit-aware y-axis (with optional name) + tooltip onto a base option
   built from hbzEChartsBase(). Keeps the dashed splitLine and theme colors. */
function hbzApplyUnit(base, unit, yName, yExtra) {
  var fmt = hbzUnitFmt(unit);
  base.yAxis = Object.assign({}, base.yAxis, {
    name: yName || "",
    nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10, align: "left" },
    nameGap: 8,
    axisLabel: Object.assign({}, base.yAxis && base.yAxis.axisLabel, { formatter: function(v) { return fmt(v); } })
  }, yExtra || {});
  base.tooltip = Object.assign({}, base.tooltip, { valueFormatter: function(v) { return fmt(v); } });
  return base;
}

function EChart(props) {
  var ref = useRef(null);
  var instRef = useRef(null);
  var themeState = useState(0);
  var themeTick = themeState[0]; var setThemeTick = themeState[1];

  useEffect(function() {
    if (!ref.current || typeof echarts === "undefined") return undefined;
    var inst = echarts.init(ref.current);
    instRef.current = inst;
    var ro = (typeof ResizeObserver !== "undefined")
      ? new ResizeObserver(function() { inst.resize(); })
      : null;
    if (ro) ro.observe(ref.current);
    var mo = new MutationObserver(function() {
      setThemeTick(function(t) { return t + 1; });
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return function() {
      mo.disconnect();
      if (ro) ro.disconnect();
      inst.dispose();
      instRef.current = null;
    };
  }, []);

  useEffect(function() {
    var inst = instRef.current;
    if (!inst) return;
    var opt = (typeof props.option === "function") ? props.option() : props.option;
    if (!opt) return;
    inst.setOption(Object.assign({}, hbzEChartsBase(), opt), true);
  }, [props.option, themeTick]);

  return <div ref={ref} style={{ width: "100%", height: props.height || 220, minWidth: 0 }}/>;
}

Object.assign(window, {
  TopBar, BandBar, LeftRail, ClusterPicker, TimeRange, KPI, Stat, Sparkline, Gauge,
  EChart, hbzEChartsBase, hbzAreaSeries, vizVar, vizColor, vizAlpha,
  StackedBar, PieChart, MiniLine, BarList, DonutChart, StatusBreakdown, TimelineStrip, SourceBadge, phaseCountRows,
  hbzJsonResponse, hbzRequired, hbzPositiveNumber, hbzNameLike, hbzStatusMessage, hbzErrorText,
  Modal, Drawer, ApprovalBanner, RunStatus,
  CommandPalette, ToastHost, useToasts, buildNavGroups, flattenNav, userInitials,
  EmptyState
});


/* ============================================================================
   Azure chart theme (integrated) — refines the two global ECharts helpers
   defined above with gradient area fills, soft line lift, rounded tooltips and
   quiet dashed gridlines. Runs after the window export so every view picks it up.
   ============================================================================ */
(function () {
  function vv(name, fb) { return (typeof window.vizVar === "function") ? window.vizVar(name, fb) : fb; }
  function va(c, a) { return (typeof window.vizAlpha === "function") ? window.vizAlpha(c, a) : c; }
  function vc(i) { return (typeof window.vizColor === "function") ? window.vizColor(i) : "#7c3aed"; }

  window.hbzEChartsBase = function () {
    var fgDim = vv("--fg-dim", "#6c757d");
    var fg = vv("--fg", "#212529");
    var border = vv("--border", "#e0e0e0");
    var divider = vv("--divider", "#eeeeee");
    var surface = vv("--surface", "#ffffff");
    var accent = vv("--accent", "#7c3aed");
    return {
      textStyle: { fontFamily: "Poppins, -apple-system, 'Segoe UI', Roboto, sans-serif", color: fgDim },
      animationDuration: 650,
      animationEasing: "cubicOut",
      grid: { left: 8, right: 18, top: 36, bottom: 4, containLabel: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: va(accent, 0.55), width: 1, type: "solid" }, z: 0 },
        backgroundColor: surface,
        borderColor: border,
        borderWidth: 1,
        padding: [9, 13],
        textStyle: { color: fg, fontSize: 12 },
        extraCssText: "box-shadow: 0 10px 28px rgba(15,23,34,.16), 0 2px 7px rgba(15,23,34,.08); border-radius: 11px;"
      },
      legend: { top: 3, left: 4, icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 15, textStyle: { color: fgDim, fontSize: 11 } },
      xAxis: {
        type: "time",
        boundaryGap: false,
        axisLine: { lineStyle: { color: border } },
        axisTick: { show: false },
        axisLabel: { color: fgDim, fontSize: 10, hideOverlap: true, margin: 12 },
        splitLine: { show: false }
      },
      yAxis: {
        type: "value",
        axisLabel: { color: fgDim, fontSize: 10, margin: 12 },
        splitLine: { lineStyle: { color: divider, width: 1, type: [3, 5] } }
      }
    };
  };

  window.hbzAreaSeries = function (name, points, colorIndex, extra) {
    var color = (extra && extra.color) || vc(colorIndex || 1);
    var surface = vv("--surface", "#ffffff");
    var grad = (typeof echarts !== "undefined" && echarts.graphic)
      ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: va(color, 0.40) },
          { offset: 0.55, color: va(color, 0.13) },
          { offset: 1, color: va(color, 0) }
        ])
      : va(color, 0.18);
    return Object.assign({
      name: name, type: "line", data: points || [],
      smooth: 0.45, smoothMonotone: "x", showSymbol: false, symbol: "circle", symbolSize: 7,
      lineStyle: { width: 2.5, color: color, cap: "round", join: "round", shadowColor: va(color, 0.34), shadowBlur: 10, shadowOffsetY: 5 },
      itemStyle: { color: color, borderColor: surface, borderWidth: 2 },
      areaStyle: { color: grad, origin: "start" },
      emphasis: { focus: "series", scale: true, lineStyle: { width: 3 }, itemStyle: { shadowBlur: 9, shadowColor: va(color, 0.5) } }
    }, extra || {});
  };
})();
