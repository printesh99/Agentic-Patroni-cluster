// Phase 7 Security & Compliance screens.

function securityPost(path, body, role, method) {
  return fetch(path, {
    method: method || "PATCH",
    headers: { "content-type": "application/json", "x-console-role": role || "dba" },
    body: JSON.stringify(body || {})
  }).then(hbzJsonResponse);
}

function securitySettingValue(settings, name) {
  var row = settings && settings[name];
  if (!row) return "-";
  return row.setting === null || row.setting === undefined ? "-" : String(row.setting);
}

function SecurityComplianceScreen({ view, lastRefresh }) {
  var dataState = React.useState({ summary: {}, source: "" });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var toastState = React.useState(null);
  var busyState = React.useState(null);
  var databaseState = React.useState("postgres");
  var frameworkState = React.useState("soc2");
  var reasonState = React.useState("Phase 7 security validation");
  var authActionState = React.useState("auth_policy_update");
  var tlsScopeState = React.useState("server");
  var auditClassesState = React.useState("ddl,role");

  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var toast = toastState[0], setToast = toastState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var database = databaseState[0], setDatabase = databaseState[1];
  var framework = frameworkState[0], setFramework = frameworkState[1];
  var reason = reasonState[0], setReason = reasonState[1];
  var authAction = authActionState[0], setAuthAction = authActionState[1];
  var tlsScope = tlsScopeState[0], setTlsScope = tlsScopeState[1];
  var auditClasses = auditClassesState[0], setAuditClasses = auditClassesState[1];

  function showToast(kind, message) {
    setToast({ kind: kind, message: message });
    window.setTimeout(function() { setToast(null); }, 4200);
  }

  function endpoint() {
    if (view === "auth") return [clusterPath("/auth"), {}];
    if (view === "tls") return [clusterPath("/tls"), {}];
    if (view === "pgaudit") return [clusterPath("/pgaudit"), {}];
    if (view === "sensitive") return [clusterPath("/sensitive-data"), { database: database, limit: 250 }];
    return ["/api/v1/compliance/" + framework, { cluster: activeClusterId(), database: database }];
  }

  function loadSecurity() {
    var target = endpoint();
    setLoading(true);
    setError(null);
    return v1Json(target[0], target[1])
      .then(function(payload) {
        setData(payload || {});
        setLoading(false);
        return payload;
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setLoading(false);
      });
  }

  React.useEffect(function() {
    var alive = true;
    var target = endpoint();
    setLoading(true);
    setError(null);
    v1Json(target[0], target[1])
      .then(function(payload) {
        if (!alive) return;
        setData(payload || {});
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [view, lastRefresh, database, framework]);

  function refreshAfter(message, kind) {
    return loadSecurity().then(function() {
      if (message) showToast(kind || "ok", message);
    });
  }

  function submitAuth() {
    if (!hbzRequired(reason)) { showToast("danger", "Reason is required for authentication validation."); return; }
    setBusy("auth");
    securityPost(clusterPath("/auth"), { action: authAction, reason: reason }, "admin", "PATCH")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("Authentication validation " + payload.state, "warn");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", err.message || String(err));
      });
  }

  function submitTls() {
    if (!hbzRequired(reason)) { showToast("danger", "Reason is required for TLS rotation validation."); return; }
    setBusy("tls");
    securityPost(clusterPath("/tls/rotate"), { scope: tlsScope, reason: reason }, "admin", "POST")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("TLS rotation request " + payload.state, "warn");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", err.message || String(err));
      });
  }

  function submitPgaudit() {
    if (!hbzRequired(auditClasses) || !hbzRequired(reason)) { showToast("danger", "Audit classes and reason are required."); return; }
    setBusy("pgaudit");
    securityPost(clusterPath("/pgaudit"), { classes: auditClasses, reason: reason }, "dba", "PATCH")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("pgaudit validation " + payload.state, payload.approval_required ? "warn" : "ok");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", err.message || String(err));
      });
  }

  var summary = data.summary || {};
  var status = summary.status || "unknown";
  var hba = data.hba || [];
  var roles = data.roles || [];
  var settings = data.settings || {};
  var jobs = data.jobs || [];
  var sessions = data.sessions || [];
  var controls = data.controls || [];
  var matches = data.matches || [];
  var pgauditSettings = Array.isArray(data.settings) ? data.settings : [];
  var hbaMethodRows = Object.keys(summary.auth_methods || {}).map(function(key) { return { label: key, value: summary.auth_methods[key], tone: key === "trust" || key === "md5" || key === "password" ? "warn" : "ok" }; });
  var roleRiskRows = [
    { label: "Login roles", value: Number(summary.login_roles || 0), tone: "info" },
    { label: "Privileged", value: Number(summary.privileged_roles || 0), tone: "warn" },
    { label: "Superusers", value: Number(summary.superusers || 0), tone: "danger" },
    { label: "No expiry", value: Number(summary.no_expiry_login_roles || 0), tone: "muted" },
  ];
  var tlsRows = [
    { label: "SSL", value: Number(summary.ssl_sessions || 0), tone: "ok" },
    { label: "Non-SSL", value: Number(summary.non_ssl_sessions || 0), tone: Number(summary.non_ssl_sessions || 0) ? "warn" : "muted" },
  ];
  var tlsProtocolRows = phaseCountRows(sessions, function(row) { return row.version || "none"; }, function(key) { return key === "none" ? "warn" : "ok"; });
  var pgauditRows = [
    { label: "Installed", value: summary.installed ? 1 : 0, tone: "ok" },
    { label: "Available", value: summary.available ? 1 : 0, tone: "info" },
    { label: "Preloaded", value: summary.preloaded ? 1 : 0, tone: summary.preloaded ? "ok" : "warn" },
    { label: "Settings", value: Number(summary.settings || 0), tone: "teal" },
  ];
  var complianceRows = [
    { label: "OK", value: Number(summary.ok || 0), tone: "ok" },
    { label: "Warning", value: Number(summary.warning || 0), tone: "warn" },
    { label: "Critical", value: Number(summary.critical || 0), tone: "danger" },
  ];
  var sensitiveSeverityRows = [
    { label: "Critical", value: Number(summary.critical || 0), tone: "danger" },
    { label: "Warning", value: Number(summary.warning || 0), tone: "warn" },
  ];
  var sensitiveCategoryRows = Object.keys(summary.categories || {}).map(function(key) { return { label: key, value: summary.categories[key], tone: key === "secret" ? "danger" : "info" }; });
  var canSubmitSecurityReason = hbzRequired(reason);
  var canSubmitPgaudit = hbzRequired(auditClasses) && hbzRequired(reason);
  var canQueryDatabase = hbzNameLike(database);

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <span className={"pill " + phase1Pill(status)}><span className="dot"/>{status}</span>
        <span className="pill muted"><Icon.Shield size={12}/>Phase 7</span>
        <span className="pill muted"><Icon.Database size={12}/>{database}</span>
        <button className="btn sm" onClick={loadSecurity} disabled={loading}>
          <Icon.RefreshCw size={12}/> Refresh
        </button>
      </Phase1Toolbar>

      <div className="risk-banner info">
        <Icon.ShieldAlert size={16}/>
        <div>Phase 7 starts with read-only security evidence and guarded dry-run requests. No HBA, TLS, pgaudit, certificate, secret, or OpenShift object is changed by these screens.</div>
      </div>

      {view === "auth" && (
        <>
          <div className="grid-4">
            <Stat label="Auth status" value={String(status).toUpperCase()} sub={summary.password_encryption || "password encryption unknown"}/>
            <Stat label="HBA rules" value={summary.hba_rules || 0} sub={(summary.hba_errors || 0) + " parser errors"}/>
            <Stat label="Login roles" value={summary.login_roles || 0} sub={(summary.privileged_roles || 0) + " privileged"}/>
            <Stat label="Weak HBA" value={summary.weak_hba_rules || 0} sub="trust/password/md5"/>
          </div>

          <div className="grid-2">
            <div className="card"><div className="bd"><DonutChart title="HBA Methods" rows={hbaMethodRows} center={summary.hba_rules || hba.length} sub="rules"/></div></div>
            <div className="card"><div className="bd"><BarList title="Role Risk" rows={roleRiskRows}/></div></div>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="hd">Authentication Policy Validation</div>
              <div className="bd">
                <div className="grid-2">
                  <div className="field" style={{marginTop: 0}}>
                    <label>Action</label>
                    <select value={authAction} onChange={function(e) { setAuthAction(e.target.value); }}>
                      <option value="auth_policy_update">Authentication policy</option>
                      <option value="hba_policy_update">HBA policy</option>
                      <option value="password_policy_update">Password policy</option>
                      <option value="ldap_policy_update">LDAP policy</option>
                    </select>
                  </div>
                  <div className="field" style={{marginTop: 0}}>
                    <label>Reason</label>
                    <input type="text" value={reason} onChange={function(e) { setReason(e.target.value); }}/>
                  </div>
                </div>
                <button className="btn sm primary mt-3" onClick={submitAuth} disabled={busy === "auth" || !canSubmitSecurityReason}>
                  <Icon.Save size={12}/> {busy === "auth" ? "Validating" : "Validate Change"}
                </button>
              </div>
            </div>
            <div className="card">
              <div className="hd">Security Jobs <span className="meta">{jobs.length} rows</span></div>
              <div className="bd flex-col txt-sm">
                {jobs.slice(0, 6).map(function(job) {
                  return <div key={job.id} className="flex-row"><span className={"pill " + phase1Pill(job.state)}>{job.state}</span><span className="mono">{job.kind}</span><span className="muted">{phase1Date(job.submitted_at)}</span></div>;
                })}
                {jobs.length === 0 && <div className="muted">No Phase 7 security jobs yet.</div>}
              </div>
            </div>
          </div>

          <div className="card">
            <div className="hd">HBA Rules <span className="meta">{hba.length} rows</span></div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl">
                <thead><tr><th>Line</th><th>Type</th><th>Database</th><th>User</th><th>Address</th><th>Method</th><th>Error</th></tr></thead>
                <tbody>
                  {hba.map(function(row) {
                    var method = row.auth_method || "-";
                    var weak = ["trust", "password", "md5"].indexOf(String(method).toLowerCase()) >= 0;
                    return (
                      <tr key={row.line_number} className={row.error ? "row-danger" : weak ? "row-warn" : ""}>
                        <td>{row.line_number}</td><td className="mono">{row.type || "-"}</td><td>{String(row.database || "-")}</td><td>{String(row.user_name || "-")}</td><td>{row.address || row.netmask || "-"}</td><td><span className={"pill " + (weak ? "warn" : "ok")}>{method}</span></td><td>{row.error || "-"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="hd">Roles <span className="meta">first {Math.min(roles.length, 80)} of {roles.length}</span></div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl">
                <thead><tr><th>Role</th><th>Login</th><th>Superuser</th><th>Create DB</th><th>Create Role</th><th>Replication</th><th>Valid Until</th></tr></thead>
                <tbody>
                  {roles.slice(0, 80).map(function(row) {
                    return <tr key={row.rolname} className={row.rolsuper ? "row-warn" : ""}><td className="mono">{row.rolname}</td><td>{String(row.rolcanlogin)}</td><td>{String(row.rolsuper)}</td><td>{String(row.rolcreatedb)}</td><td>{String(row.rolcreaterole)}</td><td>{String(row.rolreplication)}</td><td>{row.rolvaliduntil || "-"}</td></tr>;
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {view === "tls" && (
        <>
          <div className="grid-4">
            <Stat label="TLS status" value={String(status).toUpperCase()} sub={securitySettingValue(settings, "ssl")}/>
            <Stat label="SSL sessions" value={summary.ssl_sessions || 0} sub={(summary.protocols || []).join(", ") || "no protocol observed"}/>
            <Stat label="Non-SSL sessions" value={summary.non_ssl_sessions || 0} sub="pg_stat_ssl"/>
            <Stat label="Min protocol" value={securitySettingValue(settings, "ssl_min_protocol_version")} sub="certificate expiry pending"/>
          </div>
          <div className="grid-2">
            <div className="card"><div className="bd"><DonutChart title="TLS Adoption" rows={tlsRows} center={(summary.ssl_sessions || 0) + (summary.non_ssl_sessions || 0)} sub="sessions"/></div></div>
            <div className="card"><div className="bd"><BarList title="Protocol Mix" rows={tlsProtocolRows}/></div></div>
          </div>
          <div className="grid-2">
            <div className="card">
              <div className="hd">TLS Rotation Validation</div>
              <div className="bd">
                <div className="grid-2">
                  <div className="field" style={{marginTop: 0}}>
                    <label>Scope</label>
                    <select value={tlsScope} onChange={function(e) { setTlsScope(e.target.value); }}>
                      <option value="server">Server certificate</option>
                      <option value="client">Client certificates</option>
                      <option value="ca">CA bundle</option>
                      <option value="route">OpenShift route</option>
                    </select>
                  </div>
                  <div className="field" style={{marginTop: 0}}>
                    <label>Reason</label>
                    <input type="text" value={reason} onChange={function(e) { setReason(e.target.value); }}/>
                  </div>
                </div>
                <button className="btn sm danger mt-3" onClick={submitTls} disabled={busy === "tls" || !canSubmitSecurityReason}>
                  <Icon.RotateCcw size={12}/> {busy === "tls" ? "Requesting" : "Request Rotation"}
                </button>
              </div>
            </div>
            <div className="card">
              <div className="hd">TLS Files</div>
              <div className="bd grid-2 txt-sm">
                <div><div className="txt-xs muted">cert</div><span className="mono">{securitySettingValue(settings, "ssl_cert_file")}</span></div>
                <div><div className="txt-xs muted">key</div><span className="mono">{securitySettingValue(settings, "ssl_key_file")}</span></div>
                <div><div className="txt-xs muted">ca</div><span className="mono">{securitySettingValue(settings, "ssl_ca_file")}</span></div>
                <div><div className="txt-xs muted">crl</div><span className="mono">{securitySettingValue(settings, "ssl_crl_file")}</span></div>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="hd">SSL Sessions <span className="meta">{sessions.length} groups</span></div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl"><thead><tr><th>SSL</th><th>Version</th><th>Cipher</th><th>Bits</th><th>Client DN</th><th>Sessions</th></tr></thead><tbody>
                {sessions.map(function(row, index) {
                  return <tr key={index} className={row.ssl ? "" : "row-warn"}><td>{String(row.ssl)}</td><td>{row.version || "-"}</td><td>{row.cipher || "-"}</td><td>{row.bits || "-"}</td><td>{row.client_dn || "-"}</td><td>{row.sessions}</td></tr>;
                })}
              </tbody></table>
            </div>
          </div>
        </>
      )}

      {view === "pgaudit" && (
        <>
          <div className="grid-4">
            <Stat label="pgaudit status" value={String(status).toUpperCase()} sub={summary.installed ? "installed" : "not installed"}/>
            <Stat label="Available" value={String(!!summary.available)} sub="pg_available_extensions"/>
            <Stat label="Preloaded" value={String(!!summary.preloaded)} sub="shared_preload_libraries"/>
            <Stat label="Settings" value={summary.settings || 0} sub="pg_settings"/>
          </div>
          <div className="grid-2">
            <div className="card"><div className="bd"><BarList title="pgaudit Readiness" rows={pgauditRows}/></div></div>
            <div className="card"><div className="bd"><StatusBreakdown rows={pgauditRows}/></div></div>
          </div>
          <div className="grid-2">
            <div className="card">
              <div className="hd">pgaudit Validation</div>
              <div className="bd">
                <div className="grid-2">
                  <div className="field" style={{marginTop: 0}}>
                    <label>Classes</label>
                    <input type="text" value={auditClasses} onChange={function(e) { setAuditClasses(e.target.value); }}/>
                  </div>
                  <div className="field" style={{marginTop: 0}}>
                    <label>Reason</label>
                    <input type="text" value={reason} onChange={function(e) { setReason(e.target.value); }}/>
                  </div>
                </div>
                <button className="btn sm primary mt-3" onClick={submitPgaudit} disabled={busy === "pgaudit" || !canSubmitPgaudit}>
                  <Icon.Save size={12}/> {busy === "pgaudit" ? "Validating" : "Validate Settings"}
                </button>
              </div>
            </div>
            <div className="card">
              <div className="hd">Extension</div>
              <div className="bd grid-2 txt-sm">
                <div><div className="txt-xs muted">Name</div><span className="mono">{(data.extension || {}).name || "pgaudit"}</span></div>
                <div><div className="txt-xs muted">Installed</div><span className="mono">{String(!!(data.extension || {}).installed)}</span></div>
                <div><div className="txt-xs muted">Default version</div><span className="mono">{(data.extension || {}).default_version || "-"}</span></div>
                <div><div className="txt-xs muted">Installed version</div><span className="mono">{(data.extension || {}).installed_version || "-"}</span></div>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="hd">Audit Settings <span className="meta">{pgauditSettings.length} rows</span></div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl"><thead><tr><th>Name</th><th>Setting</th><th>Context</th><th>Source</th><th>Pending Restart</th></tr></thead><tbody>
                {pgauditSettings.map(function(row) {
                  return <tr key={row.name}><td className="mono">{row.name}</td><td className="mono">{row.setting}</td><td>{row.context}</td><td>{row.source}</td><td>{String(row.pending_restart)}</td></tr>;
                })}
              </tbody></table>
            </div>
          </div>
        </>
      )}

      {view === "compliance" && (
        <>
          <div className="grid-4">
            <Stat label="Framework" value={data.label || framework} sub="Phase 7 evidence pack"/>
            <Stat label="Overall" value={String(status).toUpperCase()} sub={(summary.controls || 0) + " controls"}/>
            <Stat label="OK" value={summary.ok || 0} sub="passing controls"/>
            <Stat label="Attention" value={(summary.warning || 0) + (summary.critical || 0)} sub="warning or critical"/>
          </div>
          <div className="grid-2">
            <div className="card"><div className="bd"><DonutChart title="Control Status" rows={complianceRows} center={summary.controls || controls.length} sub="controls"/></div></div>
            <div className="card"><div className="bd"><StatusBreakdown rows={complianceRows}/></div></div>
          </div>
          <div className="card">
            <div className="bd grid-3">
              <div className="field" style={{marginTop: 0}}><label>Framework</label><select value={framework} onChange={function(e) { setFramework(e.target.value); }}><option value="soc2">SOC 2</option><option value="iso27001">ISO 27001</option><option value="pci">PCI DSS</option><option value="operational">Operational</option></select></div>
              <div className="field" style={{marginTop: 0}}><label>Database</label><input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }}/></div>
              <div className="field" style={{marginTop: 0}}><label>&nbsp;</label><button className="btn sm" onClick={loadSecurity} disabled={!canQueryDatabase}><Icon.RefreshCw size={12}/> Refresh Evidence</button></div>
            </div>
          </div>
          <div className="card">
            <div className="hd">Controls</div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl"><thead><tr><th>Control</th><th>Status</th><th>Evidence</th></tr></thead><tbody>
                {controls.map(function(row) {
                  return <tr key={row.id} className={row.status === "critical" ? "row-danger" : row.status === "ok" ? "" : "row-warn"}><td>{row.name}</td><td><span className={"pill " + phase1Pill(row.status)}>{row.status}</span></td><td>{row.evidence}</td></tr>;
                })}
              </tbody></table>
            </div>
          </div>
        </>
      )}

      {view === "sensitive" && (
        <>
          <div className="grid-4">
            <Stat label="Inventory" value={String(status).toUpperCase()} sub="column-name heuristic"/>
            <Stat label="Matches" value={summary.matches || 0} sub={(summary.scanned_columns || 0) + " columns scanned"}/>
            <Stat label="Critical" value={summary.critical || 0} sub="secret/cardholder"/>
            <Stat label="Schemas" value={summary.scanned_schemas || 0} sub={database}/>
          </div>
          <div className="grid-2">
            <div className="card"><div className="bd"><DonutChart title="Severity" rows={sensitiveSeverityRows} center={summary.matches || matches.length} sub="matches"/></div></div>
            <div className="card"><div className="bd"><BarList title="Categories" rows={sensitiveCategoryRows}/></div></div>
          </div>
          <div className="card">
            <div className="bd grid-3">
              <div className="field" style={{marginTop: 0}}><label>Database</label><input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }}/></div>
              <div className="field" style={{marginTop: 0}}><label>&nbsp;</label><button className="btn sm" onClick={loadSecurity} disabled={!canQueryDatabase}><Icon.Search size={12}/> Scan Columns</button></div>
              <div className="muted txt-sm" style={{alignSelf: "end"}}>This screen inspects metadata only; table values are not read.</div>
            </div>
          </div>
          <div className="card">
            <div className="hd">Candidate Sensitive Columns <span className="meta">{matches.length} rows</span></div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl"><thead><tr><th>Severity</th><th>Category</th><th>Schema</th><th>Table</th><th>Column</th><th>Type</th></tr></thead><tbody>
                {matches.map(function(row, index) {
                  return <tr key={index} className={row.severity === "critical" ? "row-danger" : "row-warn"}><td><span className={"pill " + phase1Pill(row.severity)}>{row.severity}</span></td><td>{row.category}</td><td className="mono">{row.schema}</td><td className="mono">{row.table}</td><td className="mono">{row.column}</td><td>{row.data_type}</td></tr>;
                })}
                {!loading && matches.length === 0 && <tr><td colSpan="6" className="muted" style={{textAlign: "center", padding: 26}}>No candidate sensitive columns found by the Phase 7 heuristic.</td></tr>}
              </tbody></table>
            </div>
          </div>
        </>
      )}

      {toast && (
        <div className={"session-toast " + toast.kind}>
          {toast.kind === "danger" ? <Icon.AlertTriangle size={15}/> : <Icon.CheckCircle size={15}/>}
          <span>{toast.message}</span>
          <button className="btn ghost icon" onClick={function() { setToast(null); }}><Icon.X size={12}/></button>
        </div>
      )}
    </div>
  );
}
