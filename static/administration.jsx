// Phase 9 Administration screens: logs, settings, notifications, tokens, tenants, runbooks.

function admin9Headers(role) {
  var headers = { "content-type": "application/json" };
  if (role) headers["x-console-role"] = role;
  return headers;
}

async function admin9Get(path, params, role) {
  var response = await fetch(v1Url(path, params), { cache: "no-store", headers: role ? { "x-console-role": role } : {} });
  return hbzJsonResponse(response);
}

async function admin9Post(path, body, role) {
  var response = await fetch(path, {
    method: "POST",
    headers: admin9Headers(role || "admin"),
    body: JSON.stringify(body || {})
  });
  return hbzJsonResponse(response);
}

async function admin9Patch(path, body, role) {
  var response = await fetch(path, {
    method: "PATCH",
    headers: admin9Headers(role),
    body: JSON.stringify(body || {})
  });
  return hbzJsonResponse(response);
}

async function admin9Delete(path, role) {
  var response = await fetch(path, { method: "DELETE", headers: role ? { "x-console-role": role } : {} });
  return hbzJsonResponse(response);
}

function admin9Text(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function admin9ShortToken(token) {
  if (!token) return "";
  if (token.length <= 28) return token;
  return token.slice(0, 18) + "..." + token.slice(-8);
}

function PodLogsScreen({ lastRefresh }) {
  var dataState = React.useState({ pods: [], count: 0, source: "" });
  var selectedState = React.useState("");
  var containerState = React.useState("database");
  var tailState = React.useState(200);
  var previewState = React.useState(null);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var actionState = React.useState(false);

  var data = dataState[0], setData = dataState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var container = containerState[0], setContainer = containerState[1];
  var tail = tailState[0], setTail = tailState[1];
  var preview = previewState[0], setPreview = previewState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var actionBusy = actionState[0], setActionBusy = actionState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    admin9Get(clusterPath("/pods"))
      .then(function(payload) {
        if (!alive) return;
        var pods = payload.pods || [];
        setData(payload);
        if (!selected && pods.length) {
          setSelected(pods[0].name);
          setContainer(pods[0].default_container || "database");
        }
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  function loadPreview() {
    if (!selected) return;
    setActionBusy(true);
    setError(null);
    admin9Get(clusterPath("/pods/") + encodeURIComponent(selected) + "/logs/preview", {
      container: container,
      tail: tail
    }, "operator")
      .then(function(payload) {
        setPreview(payload);
        setActionBusy(false);
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setActionBusy(false);
      });
  }

  var pods = data.pods || [];
  var selectedPod = pods.find(function(pod) { return pod.name === selected; }) || pods[0] || null;
  var containers = selectedPod ? (selectedPod.containers || ["database"]) : ["database"];
  var logLines = preview && preview.logs ? preview.logs : [];
  var podRoleRows = phaseCountRows(pods, function(pod) { return pod.role || "pod"; }, function(role) { return role === "leader" ? "ok" : role === "sync_standby" ? "info" : "muted"; });
  var podLagRows = pods.map(function(pod) { return { label: pod.name, value: Number(pod.lag || 0), sub: pod.state || "-", tone: Number(pod.lag || 0) > 0 ? "warn" : "ok" }; });

  return (
    <div className="page">
      <Phase1Toolbar loading={loading || actionBusy} error={error} source={data.source || (preview && preview.source)}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Pod</label>
          <select value={selected} onChange={function(e) { setSelected(e.target.value); }}>
            {pods.map(function(pod) { return <option key={pod.name} value={pod.name}>{pod.name}</option>; })}
          </select>
        </div>
        <div className="field" style={{margin: 0, minWidth: 150}}>
          <label>Container</label>
          <select value={container} onChange={function(e) { setContainer(e.target.value); }}>
            {containers.map(function(name) { return <option key={name} value={name}>{name}</option>; })}
          </select>
        </div>
        <div className="field" style={{margin: 0, width: 110}}>
          <label>Tail</label>
          <input type="number" min="1" max="5000" value={tail} onChange={function(e) { setTail(e.target.value); }}/>
        </div>
        <button className="btn sm primary" onClick={loadPreview} disabled={!selected || actionBusy}>
          <Icon.FileText size={12}/> Load Logs
        </button>
      </Phase1Toolbar>

      <div className="grid-4">
        <Stat label="Pods" value={pods.length}/>
        <Stat label="Namespace" value={data.namespace || "-"}/>
        <Stat label="Log status" value={preview ? (preview.available ? "available" : "restricted") : "not loaded"}/>
        <Stat label="Permission" value={(data.log_permission_needed || ["get pods/log"])[0]}/>
      </div>

      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Pod Roles" rows={podRoleRows} center={pods.length} sub="pods"/></div></div>
        <div className="card"><div className="bd"><BarList title="Replay Lag" rows={podLagRows} valueFormatter={fmtBytes}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">PostgreSQL Pods <span className="meta">Patroni members</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Pod</th><th>Role</th><th>State</th><th>Lag</th><th></th></tr></thead>
              <tbody>
                {pods.map(function(pod) {
                  return (
                    <tr key={pod.name} className={pod.name === selected ? "row-ok" : ""}>
                      <td className="mono txt-xs">{pod.name}</td>
                      <td>{admin9Text(pod.role)}</td>
                      <td><span className={"pill " + phase1Pill(pod.state === "running" || pod.state === "streaming" ? "ok" : "warn")}>{admin9Text(pod.state)}</span></td>
                      <td className="num">{pod.lag || 0}</td>
                      <td><button className="btn ghost sm" onClick={function() { setSelected(pod.name); setContainer(pod.default_container || "database"); }}><Icon.Eye size={12}/>Select</button></td>
                    </tr>
                  );
                })}
                {!loading && pods.length === 0 && <tr><td colSpan="5" className="muted" style={{textAlign: "center", padding: 24}}>No Patroni member pods were returned.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="hd">Log Preview <span className="meta">{preview ? preview.line_count + " lines" : "not loaded"}</span></div>
          <div className="bd">
            {preview && preview.permission_needed && preview.permission_needed.length > 0 && (
              <div className="pill warn" style={{marginBottom: 10}}><Icon.Lock size={12}/>Needs {preview.permission_needed.join(", ")}</div>
            )}
            <pre className="mono txt-xs" style={{whiteSpace: "pre-wrap", maxHeight: 420, overflow: "auto", margin: 0, background: "#0b1720", color: "#d7f5e9", padding: 12, borderRadius: 6}}>
{logLines.length ? logLines.join("\n") : "Select a pod and load logs."}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}

function SettingsAdminScreen({ lastRefresh, currentUser }) {
  var profileState = React.useState(null);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var savedState = React.useState(false);
  var timezoneState = React.useState("Europe/Zurich");
  var densityState = React.useState("comfortable");
  var defaultViewState = React.useState("overview");
  var refreshState = React.useState(30);
  var notificationsState = React.useState(true);

  var profile = profileState[0], setProfile = profileState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var saved = savedState[0], setSaved = savedState[1];
  var timezone = timezoneState[0], setTimezone = timezoneState[1];
  var density = densityState[0], setDensity = densityState[1];
  var defaultView = defaultViewState[0], setDefaultView = defaultViewState[1];
  var refresh = refreshState[0], setRefresh = refreshState[1];
  var notifications = notificationsState[0], setNotifications = notificationsState[1];

  function applyProfile(payload) {
    var user = payload.user || payload;
    var settings = user.settings || {};
    setProfile(user);
    setTimezone(settings.timezone || "Europe/Zurich");
    setDensity(settings.density || "comfortable");
    setDefaultView(settings.default_view || "overview");
    setRefresh(settings.auto_refresh_seconds || 30);
    setNotifications(settings.notifications !== false);
  }

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    admin9Get("/api/v1/me")
      .then(function(payload) {
        if (!alive) return;
        applyProfile(payload);
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  function saveSettings() {
    setLoading(true);
    setError(null);
    setSaved(false);
    admin9Patch("/api/v1/me", {
      settings: {
        timezone: timezone,
        density: density,
        default_view: defaultView,
        auto_refresh_seconds: refresh,
        notifications: notifications
      }
    })
      .then(function(payload) {
        applyProfile(payload);
        setSaved(true);
        setLoading(false);
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setLoading(false);
      });
  }

  var tenants = profile ? (profile.tenants || []) : [];
  var user = profile || currentUser || {};

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={profile && profile.source}>
        <button className="btn sm primary" onClick={saveSettings} disabled={loading}><Icon.Save size={12}/>Save Settings</button>
        {saved && <span className="pill ok"><Icon.Check size={12}/>Saved</span>}
      </Phase1Toolbar>

      <div className="grid-4">
        <Stat label="User" value={user.display_name || "-"} sub={user.email || ""}/>
        <Stat label="Role" value={user.role || user.preferred_role || "-"}/>
        <Stat label="Tenants" value={tenants.length}/>
        <Stat label="OIDC source" value={user.oidc_sub ? "active" : "bootstrap"}/>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Profile Settings</div>
          <div className="bd">
            <div className="grid-2">
              <div className="field"><label>Timezone</label><input value={timezone} onChange={function(e) { setTimezone(e.target.value); }}/></div>
              <div className="field"><label>Density</label><select value={density} onChange={function(e) { setDensity(e.target.value); }}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></div>
              <div className="field"><label>Default view</label><select value={defaultView} onChange={function(e) { setDefaultView(e.target.value); }}><option value="overview">Overview</option><option value="cluster">Cluster</option><option value="runs">Run History</option><option value="logs">Pod Logs</option></select></div>
              <div className="field"><label>Auto refresh seconds</label><input type="number" min="10" max="3600" value={refresh} onChange={function(e) { setRefresh(e.target.value); }}/></div>
            </div>
            <label className="flex-row" style={{gap: 8, marginTop: 8}}><input type="checkbox" checked={notifications} onChange={function(e) { setNotifications(e.target.checked); }}/>Enable console notifications</label>
          </div>
        </div>

        <div className="card">
          <div className="hd">Tenant Membership</div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl"><thead><tr><th>Slug</th><th>Name</th><th>Role</th></tr></thead><tbody>
              {tenants.map(function(tenant) { return <tr key={tenant.id}><td className="mono">{tenant.slug}</td><td>{tenant.name}</td><td><span className="pill info">{tenant.role || user.role || "viewer"}</span></td></tr>; })}
              {tenants.length === 0 && <tr><td colSpan="3" className="muted" style={{textAlign: "center", padding: 24}}>No tenant memberships found.</td></tr>}
            </tbody></table>
          </div>
        </div>
      </div>
    </div>
  );
}

function NotificationsAdminScreen({ lastRefresh }) {
  var dataState = React.useState({ channels: [], alert_rules: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var channelNameState = React.useState("UAT DBA Webhook");
  var channelKindState = React.useState("webhook");
  var channelTargetState = React.useState("");
  var ruleNameState = React.useState("UAT custom watch");
  var ruleSeverityState = React.useState("warning");
  var ruleExpressionState = React.useState("custom_condition == true");

  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var channelName = channelNameState[0], setChannelName = channelNameState[1];
  var channelKind = channelKindState[0], setChannelKind = channelKindState[1];
  var channelTarget = channelTargetState[0], setChannelTarget = channelTargetState[1];
  var ruleName = ruleNameState[0], setRuleName = ruleNameState[1];
  var ruleSeverity = ruleSeverityState[0], setRuleSeverity = ruleSeverityState[1];
  var ruleExpression = ruleExpressionState[0], setRuleExpression = ruleExpressionState[1];

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([
      admin9Get("/api/v1/notifications/channels"),
      admin9Get("/api/v1/alert-rules")
    ])
      .then(function(results) {
        setData({ channels: results[0].channels || [], alert_rules: results[1].alert_rules || [], source: results[0].source });
        setLoading(false);
      })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  React.useEffect(function() { load(); }, [lastRefresh]);

  function createChannel() {
    if (!hbzRequired(channelName) || !hbzRequired(channelTarget)) {
      setError("Channel name and target are required.");
      return;
    }
    var config = channelKind === "email" ? { email: channelTarget } : { url: channelTarget };
    setLoading(true);
    setError(null);
    admin9Post("/api/v1/notifications/channels", {
      name: channelName,
      kind: channelKind,
      config: config,
      enabled: true
    }, "admin").then(load).catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  function createRule() {
    if (!hbzRequired(ruleName) || !hbzRequired(ruleExpression)) {
      setError("Rule name and expression are required.");
      return;
    }
    setLoading(true);
    setError(null);
    admin9Post("/api/v1/alert-rules", {
      name: ruleName,
      severity: ruleSeverity,
      expression: ruleExpression,
      enabled: true,
      source: "phase9"
    }, "dba").then(load).catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  var channels = data.channels || [];
  var rules = data.alert_rules || [];
  var channelKindRows = phaseCountRows(channels, function(ch) { return ch.kind; }, function() { return "info"; });
  var ruleSeverityRows = phaseCountRows(rules, function(rule) { return rule.severity; });
  var ruleStatusRows = phaseCountRows(rules, function(rule) { return rule.enabled ? "enabled" : "disabled"; }, function(key) { return key === "enabled" ? "ok" : "muted"; });
  var canCreateChannel = hbzRequired(channelName) && hbzRequired(channelTarget);
  var canCreateRule = hbzRequired(ruleName) && hbzRequired(ruleExpression);

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source || "console settings"}>
        <span className="pill muted"><Icon.Bell size={12}/>Channels {channels.length}</span>
        <span className="pill muted"><Icon.AlertTriangle size={12}/>Rules {rules.length}</span>
      </Phase1Toolbar>

      <div className="grid-3">
        <div className="card"><div className="bd"><DonutChart title="Channel Kinds" rows={channelKindRows} center={channels.length} sub="channels"/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Rule Severity" rows={ruleSeverityRows} center={rules.length} sub="rules"/></div></div>
        <div className="card"><div className="bd"><StatusBreakdown rows={ruleStatusRows}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card"><div className="hd">Create Notification Channel</div><div className="bd">
          <div className="grid-2">
            <div className="field"><label>Name</label><input value={channelName} onChange={function(e) { setChannelName(e.target.value); }}/></div>
            <div className="field"><label>Kind</label><select value={channelKind} onChange={function(e) { setChannelKind(e.target.value); }}><option value="webhook">Webhook</option><option value="email">Email</option><option value="slack">Slack</option><option value="teams">Teams</option><option value="pagerduty">PagerDuty</option></select></div>
          </div>
          <div className="field"><label>Target</label><input value={channelTarget} onChange={function(e) { setChannelTarget(e.target.value); }} placeholder="URL or address"/></div>
          <button className="btn sm primary" onClick={createChannel} disabled={loading || !canCreateChannel}><Icon.Plus size={12}/>Add Channel</button>
        </div></div>

        <div className="card"><div className="hd">Create Alert Rule</div><div className="bd">
          <div className="grid-2">
            <div className="field"><label>Name</label><input value={ruleName} onChange={function(e) { setRuleName(e.target.value); }}/></div>
            <div className="field"><label>Severity</label><select value={ruleSeverity} onChange={function(e) { setRuleSeverity(e.target.value); }}><option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option></select></div>
          </div>
          <div className="field"><label>Expression</label><input className="mono" value={ruleExpression} onChange={function(e) { setRuleExpression(e.target.value); }}/></div>
          <button className="btn sm primary" onClick={createRule} disabled={loading || !canCreateRule}><Icon.Plus size={12}/>Add Rule</button>
        </div></div>
      </div>

      <div className="grid-2">
        <div className="card"><div className="hd">Notification Channels</div><div style={{overflowX: "auto"}}><table className="tbl"><thead><tr><th>Name</th><th>Kind</th><th>Enabled</th><th>Created</th></tr></thead><tbody>
          {channels.map(function(ch) { return <tr key={ch.id}><td>{ch.name}</td><td><span className="pill info">{ch.kind}</span></td><td>{String(ch.enabled)}</td><td>{phase1Date(ch.created_at)}</td></tr>; })}
          {channels.length === 0 && <tr><td colSpan="4" className="muted" style={{textAlign: "center", padding: 24}}>No notification channels configured yet.</td></tr>}
        </tbody></table></div></div>
        <div className="card"><div className="hd">Alert Rules</div><div style={{overflowX: "auto"}}><table className="tbl"><thead><tr><th>Name</th><th>Severity</th><th>Enabled</th><th>Expression</th></tr></thead><tbody>
          {rules.map(function(rule) { return <tr key={rule.id}><td>{rule.name}</td><td><span className={"pill " + phase1Pill(rule.severity)}>{rule.severity}</span></td><td>{String(rule.enabled)}</td><td className="mono txt-xs">{rule.expression}</td></tr>; })}
          {rules.length === 0 && <tr><td colSpan="4" className="muted" style={{textAlign: "center", padding: 24}}>No alert rules found.</td></tr>}
        </tbody></table></div></div>
      </div>
    </div>
  );
}

function ApiTokensScreen({ lastRefresh }) {
  var dataState = React.useState({ tokens: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var nameState = React.useState("UAT automation token");
  var scopesState = React.useState("read,jobs");
  var expiresState = React.useState(90);
  var createdState = React.useState(null);

  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var name = nameState[0], setName = nameState[1];
  var scopes = scopesState[0], setScopes = scopesState[1];
  var expires = expiresState[0], setExpires = expiresState[1];
  var created = createdState[0], setCreated = createdState[1];

  function load() {
    setLoading(true);
    setError(null);
    admin9Get("/api/v1/tokens")
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  React.useEffect(function() { load(); }, [lastRefresh]);

  function createToken() {
    if (!hbzRequired(name) || !hbzRequired(scopes) || !hbzPositiveNumber(expires)) {
      setError("Token name, scopes, and a positive expiry are required.");
      return;
    }
    setLoading(true);
    setError(null);
    setCreated(null);
    admin9Post("/api/v1/tokens", { name: name, scopes: scopes, expires_in_days: expires }, "operator")
      .then(function(payload) { setCreated(payload); load(); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  function revokeToken(token) {
    setLoading(true);
    setError(null);
    admin9Delete("/api/v1/tokens/" + encodeURIComponent(token.id), "operator")
      .then(load)
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  var tokens = data.tokens || [];
  var tokenStatusRows = phaseCountRows(tokens, function(token) { return token.revoked_at ? "revoked" : "active"; }, function(key) { return key === "active" ? "ok" : "warn"; });
  var tokenScopeRows = [];
  tokens.forEach(function(token) { (token.scopes || []).forEach(function(scope) { tokenScopeRows.push({ scope: scope }); }); });
  tokenScopeRows = phaseCountRows(tokenScopeRows, function(row) { return row.scope; }, function() { return "info"; });
  var canCreateToken = hbzRequired(name) && hbzRequired(scopes) && hbzPositiveNumber(expires);

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <span className="pill muted"><Icon.Lock size={12}/>Personal tokens {tokens.length}</span>
      </Phase1Toolbar>

      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Token State" rows={tokenStatusRows} center={tokens.length} sub="tokens"/></div></div>
        <div className="card"><div className="bd"><BarList title="Scopes" rows={tokenScopeRows}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card"><div className="hd">Create API Token</div><div className="bd">
          <div className="field"><label>Name</label><input value={name} onChange={function(e) { setName(e.target.value); }}/></div>
          <div className="grid-2">
            <div className="field"><label>Scopes</label><input value={scopes} onChange={function(e) { setScopes(e.target.value); }}/></div>
            <div className="field"><label>Expires in days</label><input type="number" min="1" max="366" value={expires} onChange={function(e) { setExpires(e.target.value); }}/></div>
          </div>
          <button className="btn sm primary" onClick={createToken} disabled={loading || !canCreateToken}><Icon.Plus size={12}/>Create Token</button>
          {created && (
            <div style={{marginTop: 12}}>
              <div className="pill warn"><Icon.Lock size={12}/>Shown once</div>
              <pre className="mono txt-xs" style={{whiteSpace: "pre-wrap", background: "#f6f8fa", padding: 10, borderRadius: 6, border: "1px solid var(--border)", wordBreak: "break-all"}}>{created.token_value || created.plain_token}</pre>
            </div>
          )}
        </div></div>

        <div className="card"><div className="hd">Token Inventory</div><div style={{overflowX: "auto"}}><table className="tbl"><thead><tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Expires</th><th></th></tr></thead><tbody>
          {tokens.map(function(token) {
            var revoked = !!token.revoked_at;
            return <tr key={token.id} className={revoked ? "row-warn" : ""}><td>{token.name}</td><td className="mono txt-xs">{token.token_prefix}</td><td className="txt-xs">{(token.scopes || []).join(", ")}</td><td>{phase1Date(token.expires_at)}</td><td><button className="btn ghost sm" onClick={function() { revokeToken(token); }} disabled={loading || revoked}><Icon.XCircle size={12}/>Revoke</button></td></tr>;
          })}
          {tokens.length === 0 && <tr><td colSpan="5" className="muted" style={{textAlign: "center", padding: 24}}>No API tokens have been created.</td></tr>}
        </tbody></table></div></div>
      </div>
    </div>
  );
}

function TenantsScreen({ lastRefresh }) {
  var dataState = React.useState({ tenants: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var slugState = React.useState("uat-workspace");
  var nameState = React.useState("UAT Workspace");

  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var slug = slugState[0], setSlug = slugState[1];
  var name = nameState[0], setName = nameState[1];

  function load() {
    setLoading(true);
    setError(null);
    admin9Get("/api/v1/tenants", null, "admin")
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  React.useEffect(function() { load(); }, [lastRefresh]);

  function createTenant() {
    if (!hbzNameLike(slug) || !hbzRequired(name)) {
      setError("Tenant slug must be valid and name is required.");
      return;
    }
    setLoading(true);
    setError(null);
    admin9Post("/api/v1/tenants", { slug: slug, name: name, theme: {} }, "admin")
      .then(load)
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  var tenants = data.tenants || [];
  var tenantUserRows = tenants.map(function(tenant) { return { label: tenant.slug, value: Number(tenant.user_count || 0), sub: tenant.name, tone: "info" }; });
  var tenantClusterRows = tenants.map(function(tenant) { return { label: tenant.slug, value: Number(tenant.cluster_count || 0), sub: tenant.name, tone: "ok" }; });
  var canCreateTenant = hbzNameLike(slug) && hbzRequired(name);

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <span className="pill muted"><Icon.Users size={12}/>Tenants {tenants.length}</span>
      </Phase1Toolbar>
      <div className="grid-2">
        <div className="card"><div className="bd"><BarList title="Users by Tenant" rows={tenantUserRows}/></div></div>
        <div className="card"><div className="bd"><BarList title="Clusters by Tenant" rows={tenantClusterRows}/></div></div>
      </div>
      <div className="grid-2">
        <div className="card"><div className="hd">Create Tenant</div><div className="bd">
          <div className="field"><label>Slug</label><input className="mono" value={slug} onChange={function(e) { setSlug(e.target.value); }}/></div>
          <div className="field"><label>Name</label><input value={name} onChange={function(e) { setName(e.target.value); }}/></div>
          <button className="btn sm primary" onClick={createTenant} disabled={loading || !canCreateTenant}><Icon.Plus size={12}/>Create Tenant</button>
        </div></div>
        <div className="card"><div className="hd">Tenants & Workspaces</div><div style={{overflowX: "auto"}}><table className="tbl"><thead><tr><th>Slug</th><th>Name</th><th>Users</th><th>Clusters</th><th>Created</th></tr></thead><tbody>
          {tenants.map(function(tenant) { return <tr key={tenant.id}><td className="mono">{tenant.slug}</td><td>{tenant.name}</td><td className="num">{tenant.user_count || 0}</td><td className="num">{tenant.cluster_count || 0}</td><td>{phase1Date(tenant.created_at)}</td></tr>; })}
          {tenants.length === 0 && <tr><td colSpan="5" className="muted" style={{textAlign: "center", padding: 24}}>No tenants found.</td></tr>}
        </tbody></table></div></div>
      </div>
    </div>
  );
}

function HelpRunbooksScreen({ lastRefresh }) {
  var listState = React.useState({ runbooks: [] });
  var selectedState = React.useState("");
  var detailState = React.useState(null);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);

  var list = listState[0], setList = listState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var detail = detailState[0], setDetail = detailState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    admin9Get("/api/v1/help/runbooks")
      .then(function(payload) {
        if (!alive) return;
        setList(payload);
        if (!selected && (payload.runbooks || []).length) setSelected(payload.runbooks[0].slug);
        setLoading(false);
      })
      .catch(function(err) { if (alive) { setError(err.message || String(err)); setLoading(false); } });
    return function() { alive = false; };
  }, [lastRefresh]);

  React.useEffect(function() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    admin9Get("/api/v1/help/runbooks/" + encodeURIComponent(selected))
      .then(function(payload) { setDetail(payload.runbook); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [selected]);

  var runbooks = list.runbooks || [];

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={list.source || "built-in runbooks"}>
        <span className="pill muted"><Icon.FileText size={12}/>Runbooks {runbooks.length}</span>
      </Phase1Toolbar>
      <div className="grid-2">
        <div className="card"><div className="hd">Runbooks</div><div className="bd" style={{display: "grid", gap: 8}}>
          {runbooks.map(function(item) { return <button key={item.slug} className={"btn ghost" + (selected === item.slug ? " active" : "")} onClick={function() { setSelected(item.slug); }}><Icon.FileText size={14}/>{item.title}</button>; })}
        </div></div>
        <div className="card"><div className="hd">{detail ? detail.title : "Runbook"} <span className="meta">{detail ? detail.category : ""}</span></div><div className="bd">
          <pre className="mono txt-xs" style={{whiteSpace: "pre-wrap", margin: 0, lineHeight: 1.55}}>{detail ? detail.body : "Select a runbook."}</pre>
        </div></div>
      </div>
    </div>
  );
}

function AdministrationScreen({ view, lastRefresh, currentUser }) {
  if (view === "logs") return <PodLogsScreen lastRefresh={lastRefresh}/>;
  if (view === "settings") return <SettingsAdminScreen lastRefresh={lastRefresh} currentUser={currentUser}/>;
  if (view === "notifications") return <NotificationsAdminScreen lastRefresh={lastRefresh}/>;
  if (view === "tokens") return <ApiTokensScreen lastRefresh={lastRefresh}/>;
  if (view === "tenants") return <TenantsScreen lastRefresh={lastRefresh}/>;
  if (view === "help") return <HelpRunbooksScreen lastRefresh={lastRefresh}/>;
  return <SettingsAdminScreen lastRefresh={lastRefresh} currentUser={currentUser}/>;
}
window.PodLogsScreen = PodLogsScreen;
window.AdministrationScreen = AdministrationScreen;
