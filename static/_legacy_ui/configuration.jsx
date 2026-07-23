// Phase 3 Configuration screens.

function configFetch(path, params) {
  return v1Json(path, params || {});
}

function configPost(path, body, role) {
  return fetch(path, {
    method: "POST",
    headers: {"content-type": "application/json", "x-console-role": role || "dba"},
    body: JSON.stringify(body || {})
  }).then(hbzJsonResponse);
}

function configDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("en-GB", { hour12: false });
}

function configContextTone(context) {
  if (context === "postmaster") return "danger";
  if (context === "sighup") return "warn";
  if (context === "user") return "ok";
  if (context === "superuser" || context === "superuser-backend") return "info";
  return "muted";
}

function ConfigToolbar({ children, loading, error, source }) {
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

function ConfigJobDrawer({ item, onClose }) {
  if (!item) return null;
  return (
    <Drawer onClose={onClose}>
      <div className="hd">
        <Icon.Settings size={16}/>
        <div>
          <div style={{fontWeight: 600, fontSize: 14}}>Configuration detail</div>
          <div className="muted txt-xs">{item.job ? item.job.state : item.title || "detail"}</div>
        </div>
        <button className="btn ghost icon" style={{marginLeft: "auto"}} onClick={onClose} aria-label="Close"><Icon.X size={14}/></button>
      </div>
      <div className="bd">
        {item.job && (
          <div className="risk-banner">
            <Icon.Info size={14}/>
            <div>
              <strong>Dry-run job created</strong>
              <div className="txt-xs mt-2">No PostgreSQL or Patroni configuration was changed.</div>
            </div>
          </div>
        )}
        <pre className="logbox mt-3" style={{whiteSpace: "pre-wrap"}}>{JSON.stringify(item, null, 2)}</pre>
      </div>
    </Drawer>
  );
}

function ConfigHero({ title, detail, tone, children }) {
  return (
    <div className={"config-hero " + (tone || "ok")}>
      <div>
        <div className="config-hero-kicker">Phase 3 Configuration</div>
        <div className="config-hero-title">{title}</div>
        <div className="config-hero-detail">{detail}</div>
      </div>
      <div className="config-hero-side">{children}</div>
    </div>
  );
}

function ConfigParametersScreen({ lastRefresh }) {
  var searchState = React.useState("");
  var contextState = React.useState("all");
  var pendingState = React.useState("all");
  var dataState = React.useState({ parameters: [], summary: {}, contexts: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var actionState = React.useState(null);
  var selectedState = React.useState(null);
  var paramState = React.useState("work_mem");
  var valueState = React.useState("64MB");
  var reasonState = React.useState("Phase 3 parameter change validation");

  var search = searchState[0], setSearch = searchState[1];
  var context = contextState[0], setContext = contextState[1];
  var pending = pendingState[0], setPending = pendingState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var action = actionState[0], setAction = actionState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var parameter = paramState[0], setParameter = paramState[1];
  var value = valueState[0], setValue = valueState[1];
  var reason = reasonState[0], setReason = reasonState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    configFetch(clusterPath("/config/parameters"), {
      search: search,
      context: context,
      pending_restart: pending === "pending" ? "true" : "all"
    })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [search, context, pending, lastRefresh]);

  function validateParameter() {
    if (!hbzRequired(parameter) || !hbzRequired(value) || !hbzRequired(reason)) {
      setError("Parameter, proposed value, and reason are required.");
      return;
    }
    setAction("parameter");
    setError(null);
    configPost(clusterPath("/config/parameters/validate"), {
      name: parameter,
      value: value,
      reason: reason
    }, "dba")
      .then(function(job) { setAction(null); setSelected({ job: job, title: parameter }); })
      .catch(function(err) { setAction(null); setError(err.message || String(err)); });
  }

  var rows = data.parameters || [];
  var summary = data.summary || {};
  var contextRows = phaseCountRows(rows, function(row) { return row.context; }, function(ctx) { return configContextTone(ctx); });
  var sourceRows = phaseCountRows(rows, function(row) { return row.source; }, function() { return "info"; });
  var canValidateParameter = hbzRequired(parameter) && hbzRequired(value) && hbzRequired(reason);
  var restartRows = [
    { label: "Clean", value: Math.max(0, rows.length - Number(summary.pending_restart || 0)), tone: "ok" },
    { label: "Pending restart", value: Number(summary.pending_restart || 0), tone: "warn" },
  ];

  return (
    <div className="page">
      <ConfigToolbar loading={loading} error={error} source={data.source || "pg_settings"}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Search</label>
          <input type="text" value={search} onChange={function(e) { setSearch(e.target.value); }} placeholder="parameter or category"/>
        </div>
        <div className="field" style={{margin: 0, minWidth: 180}}>
          <label>Context</label>
          <select value={context} onChange={function(e) { setContext(e.target.value); }}>
            <option value="all">all</option>
            {(data.contexts || []).map(function(ctx) { return <option key={ctx} value={ctx}>{ctx}</option>; })}
          </select>
        </div>
        <div className="field" style={{margin: 0, minWidth: 180}}>
          <label>Restart</label>
          <select value={pending} onChange={function(e) { setPending(e.target.value); }}>
            <option value="all">all</option>
            <option value="pending">pending restart</option>
          </select>
        </div>
      </ConfigToolbar>

      <ConfigHero title="Server Parameters"
                  detail="Live PostgreSQL GUC inventory from pg_settings. Change requests create dry-run jobs only."
                  tone={summary.pending_restart ? "warn" : "ok"}>
        <span className={"pill " + (summary.pending_restart ? "warn" : "ok")}><span className="dot"/>{fmtInt(summary.pending_restart || 0)} pending restart</span>
        <span className="pill muted">observed {configDate(data.observed_at)}</span>
      </ConfigHero>

      <div className="grid-4">
        <Stat label="Parameters" value={fmtInt(summary.parameters || rows.length)}/>
        <Stat label="Important" value={fmtInt(summary.important || 0)}/>
        <Stat label="Postmaster" value={fmtInt(summary.postmaster || 0)} sub="restart required"/>
        <Stat label="Changed from boot" value={fmtInt(summary.changed_from_boot || 0)}/>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><DonutChart title="Context" rows={contextRows} center={rows.length} sub="parameters"/></div></div>
        <div className="card"><div className="bd"><BarList title="Source" rows={sourceRows}/></div></div>
        <div className="card"><div className="bd"><StatusBreakdown rows={restartRows}/></div></div>
      </div>

      <div className="card config-action">
        <div className="hd">Validate Parameter Change <span className="meta">dry-run only</span></div>
        <div className="bd config-action-grid">
          <div className="field">
            <label>Parameter</label>
            <input type="text" value={parameter} onChange={function(e) { setParameter(e.target.value); }}/>
          </div>
          <div className="field">
            <label>Proposed value</label>
            <input type="text" value={value} onChange={function(e) { setValue(e.target.value); }}/>
          </div>
          <div className="field wide">
            <label>Reason</label>
            <input type="text" value={reason} onChange={function(e) { setReason(e.target.value); }}/>
          </div>
          <button className="btn sm primary" onClick={validateParameter} disabled={!!action || !canValidateParameter}>
            <Icon.CheckCircle size={12}/> {action === "parameter" ? "Validating" : "Validate"}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="hd">Parameter Inventory <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Parameter</th><th>Value</th><th>Context</th><th>Source</th><th>Restart</th><th>Unit</th><th>Description</th><th></th></tr></thead>
            <tbody>
              {rows.map(function(row) {
                return (
                  <tr key={row.name} className={row.pending_restart ? "row-warn" : ""}>
                    <td className="mono">{row.name}</td>
                    <td className="mono txt-xs" style={{maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{row.setting}</td>
                    <td><span className={"pill " + configContextTone(row.context)}>{row.context}</span></td>
                    <td>{row.source}</td>
                    <td>{row.pending_restart ? <span className="pill warn">pending</span> : <span className="pill ok">clean</span>}</td>
                    <td>{row.unit || "-"}</td>
                    <td className="txt-xs" style={{maxWidth: 420}}>{row.short_desc}</td>
                    <td><button className="btn ghost sm" onClick={function() { setSelected({ parameter: row }); }}><Icon.Eye size={12}/>Detail</button></td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && <tr><td colSpan="8" style={{textAlign: "center", padding: 24}} className="muted">No parameters match the filters.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <ConfigJobDrawer item={selected} onClose={function() { setSelected(null); }}/>
    </div>
  );
}

function ConfigPatroniScreen({ lastRefresh }) {
  var dataState = React.useState({ summary: {}, config: {}, members: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var actionState = React.useState(false);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var action = actionState[0], setAction = actionState[1];

  function refresh() {
    setLoading(true);
    setError(null);
    configFetch(clusterPath("/config/patroni"))
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  React.useEffect(refresh, [lastRefresh]);

  function validateDcs() {
    setAction(true);
    setError(null);
    configPost(clusterPath("/config/patroni/validate"), {
      reason: "Phase 3 Patroni DCS config validation",
      patch: { loop_wait: (data.summary || {}).loop_wait }
    }, "admin")
      .then(function(job) { setAction(false); setSelected({ job: job, title: "Patroni DCS config" }); })
      .catch(function(err) { setAction(false); setError(err.message || String(err)); });
  }

  var summary = data.summary || {};
  var config = data.config || {};
  var postgresql = config.postgresql || {};
  var parameters = postgresql.parameters || {};
  var dcsRows = [
    { label: "TTL", value: Number(summary.ttl || 0), tone: "info" },
    { label: "Loop wait", value: Number(summary.loop_wait || 0), tone: "ok" },
    { label: "Retry timeout", value: Number(summary.retry_timeout || 0), tone: "warn" },
  ];
  var modeRows = [
    { label: "Normal", value: summary.paused ? 0 : 1, tone: "ok" },
    { label: "Paused", value: summary.paused ? 1 : 0, tone: "warn" },
  ];

  return (
    <div className="page">
      <ConfigToolbar loading={loading} error={error} source={data.source || "Patroni /config"}>
        <button className="btn sm primary" onClick={validateDcs} disabled={!!action}>
          <Icon.CheckCircle size={12}/> Validate DCS Change
        </button>
      </ConfigToolbar>

      <ConfigHero title="Patroni DCS Config"
                  detail="Live dynamic configuration from Patroni. Apply path is approval-gated and not executed in Phase 3."
                  tone={summary.paused ? "warn" : data.available ? "ok" : "danger"}>
        <span className={"pill " + (data.available ? "ok" : "danger")}><span className="dot"/>{data.available ? "available" : "unreachable"}</span>
        <span className={"pill " + (summary.paused ? "warn" : "ok")}><span className="dot"/>{summary.paused ? "paused" : "normal"}</span>
      </ConfigHero>

      <div className="grid-4">
        <Stat label="Leader" value={summary.leader || "-"}/>
        <Stat label="Loop wait" value={summary.loop_wait == null ? "-" : summary.loop_wait} unit={summary.loop_wait == null ? null : "s"}/>
        <Stat label="TTL" value={summary.ttl == null ? "-" : summary.ttl} unit={summary.ttl == null ? null : "s"}/>
        <Stat label="PG params" value={fmtInt(summary.postgresql_parameters || 0)}/>
      </div>

      <div className="grid-2">
        <div className="card"><div className="bd"><BarList title="Timing Settings" rows={dcsRows} valueFormatter={function(v) { return v + "s"; }}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Maintenance Mode" rows={modeRows} center={summary.paused ? "Paused" : "Normal"} sub="Patroni"/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">PostgreSQL Parameters <span className="meta">{Object.keys(parameters).length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Parameter</th><th>Value</th></tr></thead>
              <tbody>
                {Object.keys(parameters).sort().map(function(key) {
                  return <tr key={key}><td className="mono">{key}</td><td className="mono txt-xs">{String(parameters[key])}</td></tr>;
                })}
                {!loading && Object.keys(parameters).length === 0 && <tr><td colSpan="2" style={{textAlign: "center", padding: 24}} className="muted">No Patroni PostgreSQL parameters visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Raw DCS Config <span className="meta">read-only</span></div>
          <div className="bd">
            <pre className="logbox" style={{whiteSpace: "pre-wrap"}}>{JSON.stringify(config, null, 2)}</pre>
          </div>
        </div>
      </div>

      <ConfigJobDrawer item={selected} onClose={function() { setSelected(null); }}/>
    </div>
  );
}

function ConfigScopedSettingsScreen({ kind, lastRefresh }) {
  var roleFilterState = React.useState("");
  var dbFilterState = React.useState(kind === "database" ? "postgres" : "");
  var dataState = React.useState({ settings: [], count: 0 });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var actionState = React.useState(false);
  var actionRoleState = React.useState("");
  var actionDbState = React.useState("postgres");
  var actionParamState = React.useState("statement_timeout");
  var actionValueState = React.useState("15000");
  var reasonState = React.useState(kind === "role" ? "Phase 3 role setting validation" : "Phase 3 database setting validation");

  var roleFilter = roleFilterState[0], setRoleFilter = roleFilterState[1];
  var dbFilter = dbFilterState[0], setDbFilter = dbFilterState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var action = actionState[0], setAction = actionState[1];
  var actionRole = actionRoleState[0], setActionRole = actionRoleState[1];
  var actionDb = actionDbState[0], setActionDb = actionDbState[1];
  var actionParam = actionParamState[0], setActionParam = actionParamState[1];
  var actionValue = actionValueState[0], setActionValue = actionValueState[1];
  var reason = reasonState[0], setReason = reasonState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    var path = kind === "role"
      ? clusterPath("/config/role-settings")
      : clusterPath("/config/database-settings");
    configFetch(path, { role: roleFilter, database: dbFilter })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [kind, roleFilter, dbFilter, lastRefresh]);

  function validateSetting() {
    if (!hbzRequired(actionParam) || !hbzRequired(actionValue) || !hbzRequired(reason) || (kind === "role" && !hbzRequired(actionRole)) || (kind === "database" && !hbzNameLike(actionDb))) {
      setError(kind === "role" ? "Role, parameter, value, and reason are required." : "Valid database, parameter, value, and reason are required.");
      return;
    }
    setAction(true);
    setError(null);
    var path = kind === "role"
      ? clusterPath("/config/role-settings/validate")
      : clusterPath("/config/database-settings/validate");
    configPost(path, {
      role: actionRole,
      database: actionDb,
      parameter: actionParam,
      value: actionValue,
      reason: reason
    }, "dba")
      .then(function(job) { setAction(false); setSelected({ job: job, title: kind + " setting" }); })
      .catch(function(err) { setAction(false); setError(err.message || String(err)); });
  }

  var rows = data.settings || [];
  var title = kind === "role" ? "Per-Role Settings" : "Per-Database Settings";
  var detail = kind === "role"
    ? "ALTER ROLE and role/database scoped settings from pg_db_role_setting."
    : "ALTER DATABASE and database/role scoped settings from pg_db_role_setting.";
  var scopedRoleRows = phaseCountRows(rows, function(row) { return row.role_name || "<none>"; }, function() { return "info"; });
  var scopedParamRows = phaseCountRows(rows, function(row) { return row.parameter || "unknown"; }, function() { return "teal"; });
  var canValidateScoped = hbzRequired(actionParam) && hbzRequired(actionValue) && hbzRequired(reason) && (kind === "role" ? hbzRequired(actionRole) : hbzNameLike(actionDb));

  return (
    <div className="page">
      <ConfigToolbar loading={loading} error={error} source={data.source || "pg_db_role_setting"}>
        <div className="field" style={{margin: 0, minWidth: 220}}>
          <label>Role filter</label>
          <input type="text" value={roleFilter} onChange={function(e) { setRoleFilter(e.target.value); }} placeholder="optional role"/>
        </div>
        <div className="field" style={{margin: 0, minWidth: 220}}>
          <label>Database filter</label>
          <input type="text" value={dbFilter} onChange={function(e) { setDbFilter(e.target.value); }} placeholder="optional database"/>
        </div>
      </ConfigToolbar>

      <ConfigHero title={title}
                  detail={detail + " Validate actions create dry-run jobs only."}
                  tone="blue">
        <span className="pill muted">observed {configDate(data.observed_at)}</span>
        <span className="pill info">{fmtInt(rows.length)} settings</span>
      </ConfigHero>

      <div className="grid-2">
        <div className="card"><div className="bd"><BarList title="Settings by Role" rows={scopedRoleRows}/></div></div>
        <div className="card"><div className="bd"><BarList title="Parameters" rows={scopedParamRows}/></div></div>
      </div>

      <div className="card config-action">
        <div className="hd">Validate Scoped Setting <span className="meta">dry-run only</span></div>
        <div className="bd config-action-grid">
          <div className="field">
            <label>Role</label>
            <input type="text" value={actionRole} onChange={function(e) { setActionRole(e.target.value); }} placeholder={kind === "database" ? "optional" : "role name"}/>
          </div>
          <div className="field">
            <label>Database</label>
            <input type="text" value={actionDb} onChange={function(e) { setActionDb(e.target.value); }} placeholder="database"/>
          </div>
          <div className="field">
            <label>Parameter</label>
            <input type="text" value={actionParam} onChange={function(e) { setActionParam(e.target.value); }}/>
          </div>
          <div className="field">
            <label>Value</label>
            <input type="text" value={actionValue} onChange={function(e) { setActionValue(e.target.value); }}/>
          </div>
          <div className="field wide">
            <label>Reason</label>
            <input type="text" value={reason} onChange={function(e) { setReason(e.target.value); }}/>
          </div>
          <button className="btn sm primary" onClick={validateSetting} disabled={!!action || !canValidateScoped}>
            <Icon.CheckCircle size={12}/> {action ? "Validating" : "Validate"}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="hd">Scoped Settings <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Role</th><th>Database</th><th>Parameter</th><th>Value</th><th>Raw setting</th></tr></thead>
            <tbody>
              {rows.map(function(row, idx) {
                return (
                  <tr key={idx}>
                    <td className="mono">{row.role_name}</td>
                    <td className="mono">{row.database}</td>
                    <td className="mono">{row.parameter}</td>
                    <td className="mono txt-xs">{row.value || "-"}</td>
                    <td className="mono txt-xs">{row.setting}</td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && <tr><td colSpan="5" style={{textAlign: "center", padding: 24}} className="muted">No scoped settings visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <ConfigJobDrawer item={selected} onClose={function() { setSelected(null); }}/>
    </div>
  );
}

function ConfigMaintenanceScreen({ lastRefresh }) {
  var dataState = React.useState({ members: [], guardrails: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var actionState = React.useState(false);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var action = actionState[0], setAction = actionState[1];

  function refresh() {
    setLoading(true);
    setError(null);
    configFetch(clusterPath("/config/maintenance"))
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  React.useEffect(refresh, [lastRefresh]);

  function validateMaintenance(enabled) {
    setAction(true);
    setError(null);
    configPost(clusterPath("/config/maintenance/validate"), {
      enabled: enabled,
      reason: "Phase 3 maintenance mode " + (enabled ? "enable" : "disable") + " validation"
    }, "admin")
      .then(function(job) { setAction(false); setSelected({ job: job, title: enabled ? "enter maintenance" : "exit maintenance" }); })
      .catch(function(err) { setAction(false); setError(err.message || String(err)); });
  }

  var members = data.members || [];
  var memberRoleRows = phaseCountRows(members, function(member) { return member.role || "member"; }, function(role) { return role === "leader" ? "ok" : role === "sync_standby" ? "info" : "muted"; });
  var memberLagRows = members.map(function(member) { return { label: member.name, value: Number(member.lag || 0), sub: member.state || "-", tone: Number(member.lag || 0) > 0 ? "warn" : "ok" }; });

  return (
    <div className="page">
      <ConfigToolbar loading={loading} error={error} source={data.source || "Patroni /config"}>
        <button className="btn sm primary" onClick={function() { validateMaintenance(true); }} disabled={!!action}>
          <Icon.Pause size={12}/> Validate Enter
        </button>
        <button className="btn sm" onClick={function() { validateMaintenance(false); }} disabled={!!action}>
          <Icon.Play size={12}/> Validate Exit
        </button>
      </ConfigToolbar>

      <ConfigHero title="Maintenance Mode"
                  detail="Patroni pause state and guardrails. Phase 3 validates maintenance actions but does not toggle the cluster."
                  tone={data.paused ? "warn" : data.patroni_ok ? "ok" : "danger"}>
        <span className={"pill " + (data.paused ? "warn" : "ok")}><span className="dot"/>{data.mode || "unknown"}</span>
        <span className={"pill " + (data.patroni_ok ? "ok" : "danger")}>{data.patroni_ok ? "Patroni reachable" : "Patroni unreachable"}</span>
      </ConfigHero>

      <div className="grid-4">
        <Stat label="Mode" value={data.mode || "-"}/>
        <Stat label="Leader" value={data.leader || "-"}/>
        <Stat label="Members" value={fmtInt(members.length)}/>
        <Stat label="Config" value={data.config_available ? "available" : "missing"}/>
      </div>

      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Member Roles" rows={memberRoleRows} center={members.length} sub="members"/></div></div>
        <div className="card"><div className="bd"><BarList title="Member Lag" rows={memberLagRows} valueFormatter={fmtBytes}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Members <span className="meta">{members.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Name</th><th>Role</th><th>State</th><th className="num">Lag</th><th>Timeline</th></tr></thead>
              <tbody>
                {members.map(function(member) {
                  return (
                    <tr key={member.name}>
                      <td className="mono">{member.name}</td>
                      <td><span className={"pill " + (member.role === "leader" ? "ok" : member.role === "sync_standby" ? "info" : "muted")}>{member.role || "-"}</span></td>
                      <td>{member.state || "-"}</td>
                      <td className="num">{fmtInt(member.lag || 0)}</td>
                      <td>{member.timeline || "-"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Guardrails <span className="meta">approval required</span></div>
          <div className="bd">
            <div className="config-list">
              {(data.guardrails || []).map(function(item, idx) {
                return <div key={idx}><Icon.ShieldAlert size={14}/><span>{item}</span></div>;
              })}
            </div>
          </div>
        </div>
      </div>

      <ConfigJobDrawer item={selected} onClose={function() { setSelected(null); }}/>
    </div>
  );
}

function ConfigurationScreen({ view, lastRefresh }) {
  if (view === "patroni") return <ConfigPatroniScreen lastRefresh={lastRefresh}/>;
  if (view === "roles") return <ConfigScopedSettingsScreen kind="role" lastRefresh={lastRefresh}/>;
  if (view === "databases") return <ConfigScopedSettingsScreen kind="database" lastRefresh={lastRefresh}/>;
  if (view === "maintenance") return <ConfigMaintenanceScreen lastRefresh={lastRefresh}/>;
  return <ConfigParametersScreen lastRefresh={lastRefresh}/>;
}
