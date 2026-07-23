// Phase 2 Database Administration screens.

function AdminToolbar({ children, loading, error, source }) {
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

function adminFetch(path, params) {
  return v1Json(path, params || {});
}

function adminPost(path, body, role) {
  return fetch(path, {
    method: "POST",
    headers: {"content-type": "application/json", "x-console-role": role || "dba"},
    body: JSON.stringify(body || {})
  }).then(hbzJsonResponse);
}

function adminRoleAtLeast(role, required) {
  var rank = { viewer: 0, operator: 1, dba: 2, admin: 3 };
  var actual = String(role || "viewer").toLowerCase();
  return (rank[actual] || 0) >= (rank[required] || 0);
}

function roleNameValid(value) {
  return /^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(String(value || "").trim());
}

function liveRequest(path, options) {
  options = options || {};
  var init = {
    method: options.method || "GET",
    cache: "no-store",
    headers: {}
  };
  if (options.body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(options.body || {});
  }
  return fetch(path, init).then(hbzJsonResponse);
}

function liveCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function DatabaseAdminScreen({ lastRefresh, onRoute }) {
  var dataState = React.useState({ databases: [], count: 0, source: "" });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var actionState = React.useState(null);
  var selectedState = React.useState(null);

  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var action = actionState[0], setAction = actionState[1];
  var selected = selectedState[0], setSelected = selectedState[1];

  function refresh() {
    setLoading(true);
    setError(null);
    adminFetch(clusterPath("/databases"))
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  React.useEffect(refresh, [lastRefresh]);

  function validateCreate() {
    setAction("create");
    adminPost(clusterPath("/databases"), {
      reason: "Phase 2 database create validation",
      name: "phase2_validation_db"
    }, "dba")
      .then(function(job) { setAction(null); setSelected({ dryRun: job }); })
      .catch(function(err) { setAction(null); setError(err.message || String(err)); });
  }

  var rows = data.databases || [];
  var templates = rows.filter(function(db) { return db.is_template; }).length;
  var connectable = rows.filter(function(db) { return db.allow_connections; }).length;
  var totalBytes = rows.reduce(function(sum, db) { return sum + Number(db.size_bytes || 0); }, 0);
  var dbSizeRows = rows.map(function(db) { return { label: db.datname, value: Number(db.size_bytes || 0), sub: db.owner, tone: db.is_template ? "muted" : "ok" }; });
  var dbConnRows = rows.map(function(db) { return { label: db.datname, value: Number(db.active_connections || 0), sub: fmtBytes(Number(db.size_bytes || 0)), tone: db.allow_connections ? "info" : "warn" }; });
  var dbFlagRows = [
    { label: "Connectable", value: connectable, tone: "ok" },
    { label: "Templates", value: templates, tone: "muted" },
    { label: "Blocked", value: rows.length - connectable, tone: "warn" },
  ];

  return (
    <div className="page">
      <AdminToolbar loading={loading} error={error} source={data.source}>
        <button className="btn sm primary" onClick={validateCreate} disabled={!!action}>
          <Icon.Plus size={12}/> Validate Create
        </button>
      </AdminToolbar>

      <div className="section-h">Databases</div>
      <div className="grid-4">
        <Stat label="Databases" value={rows.length}/>
        <Stat label="Connectable" value={connectable}/>
        <Stat label="Templates" value={templates}/>
        <Stat label="Total size" value={fmtBytes(totalBytes)}/>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><BarList title="Database Size" rows={dbSizeRows} valueFormatter={fmtBytes}/></div></div>
        <div className="card"><div className="bd"><BarList title="Active Connections" rows={dbConnRows}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Connection Posture" rows={dbFlagRows} center={rows.length} sub="databases"/></div></div>
      </div>

      <div className="card">
        <div className="hd">Database Inventory <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Name</th><th>Owner</th><th>Encoding</th><th>Collation</th>
                <th className="num">Size</th><th className="num">Conn</th><th>Flags</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(function(db) {
                return (
                  <tr key={db.datname}>
                    <td className="mono">{db.datname}</td>
                    <td className="mono">{db.owner}</td>
                    <td>{db.encoding}</td>
                    <td className="txt-xs">{db.lc_collate}</td>
                    <td className="num">{fmtBytes(db.size_bytes || 0)}</td>
                    <td className="num">{db.active_connections || 0}</td>
                    <td>
                      {db.is_template && <span className="pill muted">template</span>}
                      {db.allow_connections ? <span className="pill ok">connect</span> : <span className="pill warn">blocked</span>}
                    </td>
                    <td className="nowrap">
                      <button className="btn ghost sm" onClick={function() { setSelected({ database: db }); }}><Icon.Eye size={12}/>Detail</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <Drawer onClose={function() { setSelected(null); }}>
          <div className="hd">
            <Icon.Database size={16}/>
            <div>
              <div style={{fontWeight: 600, fontSize: 14}}>Database admin detail</div>
              <div className="muted txt-xs">{selected.database ? selected.database.datname : "dry-run"}</div>
            </div>
            <button className="btn ghost icon" style={{marginLeft: "auto"}} onClick={function() { setSelected(null); }}><Icon.X size={14}/></button>
          </div>
          <div className="bd">
            <div className="logbox"><div className="ok">{JSON.stringify(selected, null, 2)}</div></div>
          </div>
        </Drawer>
      )}
    </div>
  );
}

function SchemaObjectsAdminScreen({ lastRefresh }) {
  var dbState = React.useState("postgres");
  var schemaState = React.useState("public");
  var dataState = React.useState({ databases: [], schemas: [], tables: [], indexes: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);

  var database = dbState[0], setDatabase = dbState[1];
  var schema = schemaState[0], setSchema = schemaState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    Promise.all([
      adminFetch(clusterPath("/databases")),
      adminFetch(clusterPath("/databases/") + encodeURIComponent(database) + "/schemas"),
      adminFetch(clusterPath("/databases/") + encodeURIComponent(database) + "/schemas/" + encodeURIComponent(schema) + "/tables"),
      adminFetch(clusterPath("/databases/") + encodeURIComponent(database) + "/schemas/" + encodeURIComponent(schema) + "/indexes")
    ])
      .then(function(parts) {
        if (!alive) return;
        var schemas = parts[1].schemas || [];
        setData({
          databases: parts[0].databases || [],
          schemas: schemas,
          tables: parts[2].tables || [],
          indexes: parts[3].indexes || []
        });
        if (schemas.length && !schemas.some(function(s) { return s.schema_name === schema; })) {
          setSchema(schemas[0].schema_name);
        }
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [database, schema, lastRefresh]);

  var schemaObjectRows = [
    { label: "Schemas", value: (data.schemas || []).length, tone: "teal" },
    { label: "Tables", value: (data.tables || []).length, tone: "ok" },
    { label: "Indexes", value: (data.indexes || []).length, tone: "info" },
  ];
  var tableSizeRows = (data.tables || []).map(function(t) { return { label: t.table_name, value: Number(t.total_size_bytes || 0), sub: t.owner, tone: "ok" }; });
  var tableRowRows = (data.tables || []).map(function(t) { return { label: t.table_name, value: Math.max(0, Number(t.estimated_rows || 0)), sub: fmtBytes(Number(t.total_size_bytes || 0)), tone: "info" }; });

  return (
    <div className="page">
      <AdminToolbar loading={loading} error={error} source="live catalog">
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Database</label>
          <select value={database} onChange={function(e) { setDatabase(e.target.value); }}>
            {(data.databases || []).map(function(db) {
              return <option key={db.datname} value={db.datname}>{db.datname}</option>;
            })}
          </select>
        </div>
        <div className="field" style={{margin: 0, minWidth: 220}}>
          <label>Schema</label>
          <select value={schema} onChange={function(e) { setSchema(e.target.value); }}>
            {(data.schemas || []).map(function(s) {
              return <option key={s.schema_name} value={s.schema_name}>{s.schema_name}</option>;
            })}
          </select>
        </div>
      </AdminToolbar>

      <div className="section-h">Schemas & Objects</div>
      <div className="grid-4">
        <Stat label="Schemas" value={(data.schemas || []).length}/>
        <Stat label="Tables" value={(data.tables || []).length}/>
        <Stat label="Indexes" value={(data.indexes || []).length}/>
        <Stat label="Selected schema" value={schema}/>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><DonutChart title="Object Mix" rows={schemaObjectRows} center={(data.tables || []).length + (data.indexes || []).length} sub="tables + indexes"/></div></div>
        <div className="card"><div className="bd"><BarList title="Table Size" rows={tableSizeRows} valueFormatter={fmtBytes}/></div></div>
        <div className="card"><div className="bd"><BarList title="Estimated Rows" rows={tableRowRows}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Tables <span className="meta">{(data.tables || []).length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Name</th><th>Type</th><th>Owner</th><th className="num">Rows est.</th><th className="num">Size</th></tr></thead>
              <tbody>
                {(data.tables || []).map(function(t) {
                  return (
                    <tr key={t.table_name}>
                      <td className="mono">{t.table_name}</td>
                      <td>{t.table_type}</td>
                      <td className="mono">{t.owner}</td>
                      <td className="num">{fmtInt(t.estimated_rows || 0)}</td>
                      <td className="num">{fmtBytes(t.total_size_bytes || 0)}</td>
                    </tr>
                  );
                })}
                {!loading && (!data.tables || data.tables.length === 0) && (
                  <tr><td colSpan="5" style={{textAlign: "center", padding: 24}} className="muted">No tables visible in this schema.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="hd">Indexes <span className="meta">{(data.indexes || []).length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Index</th><th>Table</th><th>Definition</th></tr></thead>
              <tbody>
                {(data.indexes || []).map(function(i) {
                  return (
                    <tr key={i.index_name}>
                      <td className="mono">{i.index_name}</td>
                      <td className="mono">{i.table_name}</td>
                      <td className="mono txt-xs" style={{maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{i.indexdef}</td>
                    </tr>
                  );
                })}
                {!loading && (!data.indexes || data.indexes.length === 0) && (
                  <tr><td colSpan="3" style={{textAlign: "center", padding: 24}} className="muted">No indexes visible in this schema.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function usersRolesRequest(path, options) {
  options = options || {};
  var init = {
    method: options.method || "GET",
    cache: "no-store",
    headers: {}
  };
  if (options.body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(options.body || {});
  }
  return fetch(path, init).then(hbzJsonResponse);
}

function dbUserProtected(username) {
  return ["postgres", "_crunchypgbouncer", "_crunchyrepl", "ccp_monitoring"].indexOf(String(username || "").toLowerCase()) >= 0;
}

function dbUserStatusTone(status) {
  if (status === "ACTIVE") return "ok";
  if (status === "NO_PASSWORD") return "danger";
  if (status === "EXPIRED") return "warn";
  return "muted";
}

function passwordStrength(password) {
  var p = password || "";
  var score = 0;
  if (p.length >= 8) score++;
  if (/[A-Z]/.test(p) && /[a-z]/.test(p)) score++;
  if (/[0-9]/.test(p)) score++;
  if (/[^A-Za-z0-9]/.test(p)) score++;
  if (score >= 4) return { label: "strong", tone: "ok", pct: 100 };
  if (score >= 2) return { label: "fair", tone: "warn", pct: 65 };
  return { label: "weak", tone: "danger", pct: 30 };
}

function PillList({ items, max }) {
  var list = items || [];
  var shown = list.slice(0, max || 3);
  var hidden = Math.max(0, list.length - shown.length);
  return (
    <span className="pill-list" title={list.join(", ")}>
      {shown.map(function(item) { return <span key={item} className="pill muted">{item}</span>; })}
      {hidden > 0 && <span className="pill info">+{hidden} more</span>}
      {list.length === 0 && <span className="muted">-</span>}
    </span>
  );
}

function UsersRolesToast({ toast, onClose }) {
  React.useEffect(function() {
    if (!toast) return;
    var t = setTimeout(onClose, 4500);
    return function() { clearTimeout(t); };
  }, [toast && toast.message]);
  if (!toast) return null;
  return (
    <div className={"session-toast " + (toast.tone || "ok")}>
      <span>{toast.message}</span>
      <button className="btn ghost icon" onClick={onClose}><Icon.X size={12}/></button>
    </div>
  );
}

function ResetPasswordModal({ user, busy, error, onClose, onSubmit }) {
  var passwordState = React.useState("");
  var confirmState = React.useState("");
  var reasonState = React.useState("");
  var showState = React.useState(false);
  var password = passwordState[0], setPassword = passwordState[1];
  var confirmPassword = confirmState[0], setConfirmPassword = confirmState[1];
  var reason = reasonState[0], setReason = reasonState[1];
  var show = showState[0], setShow = showState[1];
  var strength = passwordStrength(password);
  var valid = password.length >= 8 && password === confirmPassword && reason.trim().length > 0;

  return (
    <Modal onClose={function() { if (!busy) onClose(); }}>
      <div className="hd">
        <Icon.Lock size={16}/>
        <h3>Reset Password — {user.username}</h3>
        <button className="btn ghost icon close" onClick={function() { if (!busy) onClose(); }}><Icon.X size={14}/></button>
      </div>
      <div className="bd">
        <div className="risk-banner info">
          <Icon.Info size={14}/>
          <div>Resetting this password immediately updates PgBouncer authentication. No database or PgBouncer restart is required.</div>
        </div>
        <div className="grid-2 mt-3">
          <Stat label="Username" value={user.username}/>
          <Stat label="Active Sessions" value={user.activeSessions || 0}/>
          <Stat label="PgBouncer" value={user.pgbouncerAuthReady ? "Ready" : "Not Ready"}/>
          <Stat label="Databases" value={(user.accessibleDatabases || []).length}/>
        </div>
        {Number(user.activeSessions || 0) > 0 && (
          <div className="risk-banner mt-3">
            <Icon.AlertTriangle size={14}/>
            <div>This user has {user.activeSessions} active session(s). Existing connections will not be affected, but new connections will use the new password.</div>
          </div>
        )}
        {error && <div className="pill danger mt-3"><span className="dot"/>{error}</div>}
        <div className="field">
          <label>New Password</label>
          <input type={show ? "text" : "password"} value={password} onChange={function(e) { setPassword(e.target.value); }}/>
        </div>
        <div className="field">
          <label>Confirm Password</label>
          <input type={show ? "text" : "password"} value={confirmPassword} onChange={function(e) { setConfirmPassword(e.target.value); }}/>
          <div className="hint">
            <button className="btn ghost sm" type="button" onClick={function() { setShow(!show); }}>
              <Icon.Eye size={12}/>{show ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        <div className="password-meter">
          <div className={"password-meter-fill " + strength.tone} style={{width: strength.pct + "%"}}/>
        </div>
        <div className={"pill " + strength.tone}>Password strength: {strength.label}</div>
        <div className="field">
          <label>Reason</label>
          <input type="text" value={reason} onChange={function(e) { setReason(e.target.value); }} placeholder="e.g. User requested password reset"/>
        </div>
      </div>
      <div className="ft">
        <button className="btn sm" disabled={busy} onClick={onClose}>Cancel</button>
        <button className="btn sm danger" disabled={!valid || busy} onClick={function() { onSubmit(user, password, reason); }}>
          <Icon.Save size={12}/>{busy ? "Resetting" : "Reset Password"}
        </button>
      </div>
    </Modal>
  );
}

function RolesAdminScreen({ lastRefresh, currentUser }) {
  var tabState = React.useState("manage");
  var searchState = React.useState("");
  var usersState = React.useState([]);
  var rolesState = React.useState([]);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var toastState = React.useState(null);
  var resetState = React.useState(null);
  var resetErrorState = React.useState(null);
  var busyState = React.useState(false);
  var rolePanelState = React.useState(null);
  var infoOpenState = React.useState(true);

  var pgRolesState = React.useState([]);
  var pgRolesLoadingState = React.useState(true);
  var manageErrorState = React.useState(null);
  var manageBusyState = React.useState(null);
  var manageJobState = React.useState(null);
  var createFormState = React.useState({
    roleName: "", login: true, createdb: false, createrole: false, replication: false,
    connectionLimit: "", validUntil: "", memberOf: "", reason: ""
  });
  var alterFormState = React.useState({
    roleName: "", login: "", createdb: "", createrole: "", replication: "", inherit: "",
    connectionLimit: "", clearValidUntil: false, validUntil: "", reason: ""
  });
  var dropFormState = React.useState({ roleName: "", reason: "" });

  var tab = tabState[0], setTab = tabState[1];
  var search = searchState[0], setSearch = searchState[1];
  var users = usersState[0], setUsers = usersState[1];
  var roles = rolesState[0], setRoles = rolesState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var toast = toastState[0], setToast = toastState[1];
  var resetUser = resetState[0], setResetUser = resetState[1];
  var resetError = resetErrorState[0], setResetError = resetErrorState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var rolePanel = rolePanelState[0], setRolePanel = rolePanelState[1];
  var infoOpen = infoOpenState[0], setInfoOpen = infoOpenState[1];

  var pgRoles = pgRolesState[0], setPgRoles = pgRolesState[1];
  var pgRolesLoading = pgRolesLoadingState[0], setPgRolesLoading = pgRolesLoadingState[1];
  var manageError = manageErrorState[0], setManageError = manageErrorState[1];
  var manageBusy = manageBusyState[0], setManageBusy = manageBusyState[1];
  var manageJob = manageJobState[0], setManageJob = manageJobState[1];
  var createForm = createFormState[0], setCreateForm = createFormState[1];
  var alterForm = alterFormState[0], setAlterForm = alterFormState[1];
  var dropForm = dropFormState[0], setDropForm = dropFormState[1];

  function refresh() {
    // Console identity management is not configured in this bundle. Keep that
    // inventory honestly empty; the separate pg_roles inventory below is live.
    setUsers([]);
    setRoles([]);
    setError(null);
    setLoading(false);
  }

  React.useEffect(refresh, [lastRefresh]);

  function refreshPgRoles() {
    setPgRolesLoading(true);
    adminFetch(clusterPath("/roles"))
      .then(function(payload) { setPgRoles(payload.roles || []); setPgRolesLoading(false); })
      .catch(function(err) { setManageError(err.message || String(err)); setPgRolesLoading(false); });
  }

  React.useEffect(refreshPgRoles, [lastRefresh]);

  function setCreateField(name, value) { setCreateForm(Object.assign({}, createForm, { [name]: value })); }
  function setAlterField(name, value) { setAlterForm(Object.assign({}, alterForm, { [name]: value })); }
  function setDropField(name, value) { setDropForm(Object.assign({}, dropForm, { [name]: value })); }

  function submitCreateRole() {
    if (!roleNameValid(createForm.roleName)) {
      setManageError("A valid role name is required (letters, digits, underscore; must not start with a digit).");
      return;
    }
    setManageBusy("create");
    setManageError(null);
    var memberOf = createForm.memberOf.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
    adminPost(clusterPath("/roles/create/validate"), {
      role_name: createForm.roleName,
      login: !!createForm.login,
      createdb: !!createForm.createdb,
      createrole: !!createForm.createrole,
      replication: !!createForm.replication,
      connection_limit: createForm.connectionLimit !== "" ? Number(createForm.connectionLimit) : null,
      valid_until: createForm.validUntil || null,
      member_of: memberOf,
      reason: createForm.reason
    }, "dba")
      .then(function(job) {
        setManageBusy(null);
        setManageJob({ job: job, title: "Create role " + createForm.roleName });
        setCreateForm({ roleName: "", login: true, createdb: false, createrole: false, replication: false, connectionLimit: "", validUntil: "", memberOf: "", reason: "" });
        refreshPgRoles();
      })
      .catch(function(err) { setManageBusy(null); setManageError(err.message || String(err)); });
  }

  function submitAlterRole() {
    if (!alterForm.roleName) {
      setManageError("Select a role to alter.");
      return;
    }
    var body = { reason: alterForm.reason };
    var changed = false;
    ["login", "createdb", "createrole", "replication", "inherit"].forEach(function(attr) {
      if (alterForm[attr] === "true") { body[attr] = true; changed = true; }
      else if (alterForm[attr] === "false") { body[attr] = false; changed = true; }
    });
    if (alterForm.connectionLimit !== "") { body.connection_limit = Number(alterForm.connectionLimit); changed = true; }
    if (alterForm.clearValidUntil) { body.valid_until = null; changed = true; }
    else if (alterForm.validUntil) { body.valid_until = alterForm.validUntil; changed = true; }
    if (!changed) {
      setManageError("Select at least one attribute to change.");
      return;
    }
    setManageBusy("alter");
    setManageError(null);
    adminPost(clusterPath("/roles/" + encodeURIComponent(alterForm.roleName) + "/alter/validate"), body, "dba")
      .then(function(job) {
        setManageBusy(null);
        setManageJob({ job: job, title: "Alter role " + alterForm.roleName });
        setAlterForm({ roleName: "", login: "", createdb: "", createrole: "", replication: "", inherit: "", connectionLimit: "", clearValidUntil: false, validUntil: "", reason: "" });
        refreshPgRoles();
      })
      .catch(function(err) { setManageBusy(null); setManageError(err.message || String(err)); });
  }

  function submitDropRole() {
    if (!dropForm.roleName) {
      setManageError("Select a role to drop.");
      return;
    }
    setManageBusy("drop");
    setManageError(null);
    adminPost(clusterPath("/roles/" + encodeURIComponent(dropForm.roleName) + "/drop/validate"), { reason: dropForm.reason }, "admin")
      .then(function(job) {
        setManageBusy(null);
        setManageJob({ job: job, title: "Drop role " + dropForm.roleName });
        setDropForm({ roleName: "", reason: "" });
        refreshPgRoles();
      })
      .catch(function(err) { setManageBusy(null); setManageError(err.message || String(err)); });
  }

  function resetPassword(user, password, reason) {
    setBusy(true);
    setResetError(null);
    usersRolesRequest("/api/users-roles/users/" + encodeURIComponent(user.username) + "/reset-password", {
      method: "POST",
      body: { newPassword: password, reason: reason }
    }).then(function(result) {
      setBusy(false);
      setResetUser(null);
      setToast({ tone: "ok", message: "Password reset for " + user.username + ". PgBouncer updated automatically." });
      refresh();
    }).catch(function(err) {
      setBusy(false);
      setResetError(err.message || "Password reset failed");
    });
  }

  var currentRole = currentUser ? (currentUser.role || currentUser.preferred_role) : null;
  var canManageRoles = adminRoleAtLeast(currentRole, "dba");
  var canDropRole = adminRoleAtLeast(currentRole, "admin");
  var alterableRoles = pgRoles.filter(function(r) { return !r.rolsuper && !/^pg_/.test(r.rolname); });

  var filteredUsers = users.filter(function(user) {
    if (!search) return true;
    var hay = [
      user.username,
      (user.accessibleDatabases || []).join(" "),
      (user.grantedRoles || []).join(" ")
    ].join(" ").toLowerCase();
    return hay.indexOf(search.toLowerCase()) >= 0;
  });
  var filteredRoles = roles.filter(function(role) {
    if (!search) return true;
    return [role.roleName, (role.members || []).join(" ")].join(" ").toLowerCase().indexOf(search.toLowerCase()) >= 0;
  });
  var noPassword = users.filter(function(user) { return !user.hasPassword; }).length;
  var expired = users.filter(function(user) { return user.status === "EXPIRED"; }).length;
  var pgbIssues = users.filter(function(user) { return !user.pgbouncerAuthReady; }).length;
  var userStatusRows = phaseCountRows(users, function(user) { return user.status; }, function(status) { return dbUserStatusTone(status); });
  var userSessionRows = users.map(function(user) { return { label: user.username, value: Number(user.activeSessions || 0), sub: (user.accessibleDatabases || []).length + " DBs", tone: user.status === "ACTIVE" ? "ok" : "warn" }; });
  var roleMemberRows = roles.map(function(role) { return { label: role.roleName, value: Number(role.memberCount || 0), tone: role.isSuperuser ? "danger" : "info" }; });

  return (
    <div className="page">
      <UsersRolesToast toast={toast} onClose={function() { setToast(null); }}/>
      <AdminToolbar loading={loading} error={error} source="pg_roles / pg_authid / pgbouncer.get_auth">
        <div className="field" style={{margin: 0, minWidth: 300}}>
          <label>Search users, databases, roles</label>
          <input type="text" value={search} onChange={function(e) { setSearch(e.target.value); }} placeholder="username, database, role"/>
        </div>
        <button className="btn sm" onClick={refresh}><Icon.RefreshCw size={12}/>Refresh</button>
      </AdminToolbar>

      <div className="section-h">Users & Roles</div>
      <div className="grid-4">
        <Stat label="Total Users" value={users.length}/>
        <Stat label="Users Without Password" value={noPassword}/>
        <Stat label="Expired Accounts" value={expired}/>
        <Stat label="PgBouncer Auth Issues" value={pgbIssues}/>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><DonutChart title="User Status" rows={userStatusRows} center={users.length} sub="users"/></div></div>
        <div className="card"><div className="bd"><BarList title="Sessions by User" rows={userSessionRows}/></div></div>
        <div className="card"><div className="bd"><BarList title="Role Members" rows={roleMemberRows}/></div></div>
      </div>

      {error && (
        <div className="risk-banner high mb-2">
          <Icon.AlertTriangle size={14}/>
          <div>{error}</div>
          <button className="btn sm" style={{marginLeft: "auto"}} onClick={refresh}>Retry</button>
        </div>
      )}

      <div className="tabs" style={{borderRadius: "var(--r-md)", border:"1px solid var(--border)"}}>
        <button className="active" onClick={function() { setTab("manage"); }}><Icon.Settings size={13} style={{verticalAlign:"-2px", marginRight: 6}}/>PostgreSQL Roles</button>
        <span className="pill muted">Console identity/password API not configured</span>
      </div>

      {tab === "users" && (
        <div className="card">
          <div className="hd">Database Users <span className="meta">{filteredUsers.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead>
                <tr><th>Username</th><th>Databases</th><th>Granted Roles</th><th className="num">Sessions</th><th>Status</th><th>PgBouncer</th><th>Expires</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {filteredUsers.map(function(user) {
                  var protectedUser = dbUserProtected(user.username) || user.isSuperuser;
                  return (
                    <tr key={user.username} className={user.status === "NO_PASSWORD" ? "row-danger" : user.status === "EXPIRED" ? "row-warn" : ""}>
                      <td className="mono">{user.username}</td>
                      <td><PillList items={user.accessibleDatabases || []} max={3}/></td>
                      <td><PillList items={user.grantedRoles || []} max={3}/></td>
                      <td className="num"><span className={"pill " + (Number(user.activeSessions || 0) > 0 ? "info" : "muted")}>{user.activeSessions || 0}</span></td>
                      <td><span className={"pill " + dbUserStatusTone(user.status)}><span className="dot"/>{user.status === "LOCKED" ? "NO LOGIN" : user.status}</span></td>
                      <td>{user.pgbouncerAuthReady ? <span className="pill ok"><Icon.Check size={12}/>Ready</span> : <span className="pill danger"><Icon.X size={12}/>Not Ready</span>}</td>
                      <td>{user.expiresAt ? perfDate(user.expiresAt) : "never"}</td>
                      <td>
                        <button className="btn ghost sm" disabled={protectedUser} onClick={function() { setResetError(null); setResetUser(user); }}>
                          <Icon.Lock size={12}/>Reset Password
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {!loading && filteredUsers.length === 0 && <tr><td colSpan="8" style={{textAlign: "center", padding: 24}} className="muted">No database users match the filter.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "roles" && (
        <div className="card">
          <div className="hd">Group Roles <span className="meta">{filteredRoles.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Role Name</th><th>Members</th><th>Is Superuser</th><th>Actions</th></tr></thead>
              <tbody>
                {filteredRoles.map(function(role) {
                  return (
                    <tr key={role.roleName}>
                      <td className="mono">{role.roleName}</td>
                      <td title={(role.members || []).join(", ")}><span className="pill info">{role.memberCount || 0}</span> members</td>
                      <td>{role.isSuperuser ? <span className="pill danger">yes</span> : <span className="pill ok">no</span>}</td>
                      <td><button className="btn ghost sm" onClick={function() { setRolePanel(role); }}><Icon.Eye size={12}/>View Members</button></td>
                    </tr>
                  );
                })}
                {!loading && filteredRoles.length === 0 && <tr><td colSpan="4" style={{textAlign: "center", padding: 24}} className="muted">No group roles match the filter.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "manage" && (
        <React.Fragment>
          <div className="card">
            <div className="hd">Role Attributes <span className="meta">{pgRoles.length} roles</span></div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl">
                <thead>
                  <tr><th>Role</th><th>Login</th><th>Superuser</th><th>Createdb</th><th>Createrole</th><th>Replication</th><th>Inherit</th><th className="num">Conn Limit</th><th>Valid Until</th><th>Member Of</th></tr>
                </thead>
                <tbody>
                  {pgRoles.map(function(r) {
                    return (
                      <tr key={r.rolname} className={r.rolsuper ? "row-danger" : ""}>
                        <td className="mono">{r.rolname}</td>
                        <td>{r.rolcanlogin ? <span className="pill ok">yes</span> : <span className="pill muted">no</span>}</td>
                        <td>{r.rolsuper ? <span className="pill danger">yes</span> : <span className="pill muted">no</span>}</td>
                        <td>{r.rolcreatedb ? <span className="pill info">yes</span> : <span className="pill muted">no</span>}</td>
                        <td>{r.rolcreaterole ? <span className="pill info">yes</span> : <span className="pill muted">no</span>}</td>
                        <td>{r.rolreplication ? <span className="pill info">yes</span> : <span className="pill muted">no</span>}</td>
                        <td>{r.rolinherit ? <span className="pill ok">yes</span> : <span className="pill muted">no</span>}</td>
                        <td className="num">{Number(r.rolconnlimit) < 0 ? "unlimited" : r.rolconnlimit}</td>
                        <td className="mono txt-xs">{r.rolvaliduntil ? perfDate(r.rolvaliduntil) : "never"}</td>
                        <td><PillList items={r.member_of || []} max={3}/></td>
                      </tr>
                    );
                  })}
                  {!pgRolesLoading && pgRoles.length === 0 && <tr><td colSpan="10" style={{textAlign: "center", padding: 24}} className="muted">No roles found.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          {manageError && (
            <div className="risk-banner high mb-2">
              <Icon.AlertTriangle size={14}/>
              <div>{manageError}</div>
              <button className="btn sm" style={{marginLeft: "auto"}} onClick={function() { setManageError(null); }}>Dismiss</button>
            </div>
          )}

          {!canManageRoles && (
            <div className="risk-banner info mb-2">
              <Icon.Info size={14}/>
              <div>Creating or altering roles requires the DBA role; dropping roles requires Admin. Submitted changes create approval jobs and do not take effect immediately unless auto-approved.</div>
            </div>
          )}

          <div className="grid-3">
            <div className="card config-action">
              <div className="hd">Create Role <span className="meta">dry-run job</span></div>
              <div className="bd config-action-grid">
                <div className="field wide">
                  <label>Role name</label>
                  <input type="text" value={createForm.roleName} disabled={!canManageRoles}
                         onChange={function(e) { setCreateField("roleName", e.target.value); }}
                         placeholder="app_readonly"/>
                </div>
                <div className="field">
                  <label style={{display: "flex", gap: 6, alignItems: "center"}}>
                    <input type="checkbox" checked={!!createForm.login} disabled={!canManageRoles}
                           onChange={function(e) { setCreateField("login", e.target.checked); }}/>
                    LOGIN
                  </label>
                </div>
                <div className="field">
                  <label style={{display: "flex", gap: 6, alignItems: "center"}}>
                    <input type="checkbox" checked={!!createForm.createdb} disabled={!canManageRoles}
                           onChange={function(e) { setCreateField("createdb", e.target.checked); }}/>
                    CREATEDB
                  </label>
                </div>
                <div className="field">
                  <label style={{display: "flex", gap: 6, alignItems: "center"}}>
                    <input type="checkbox" checked={!!createForm.createrole} disabled={!canManageRoles}
                           onChange={function(e) { setCreateField("createrole", e.target.checked); }}/>
                    CREATEROLE
                  </label>
                </div>
                <div className="field">
                  <label style={{display: "flex", gap: 6, alignItems: "center"}}>
                    <input type="checkbox" checked={!!createForm.replication} disabled={!canManageRoles}
                           onChange={function(e) { setCreateField("replication", e.target.checked); }}/>
                    REPLICATION
                  </label>
                </div>
                <div className="field">
                  <label>Connection limit</label>
                  <input type="number" value={createForm.connectionLimit} disabled={!canManageRoles}
                         onChange={function(e) { setCreateField("connectionLimit", e.target.value); }}
                         placeholder="unlimited"/>
                </div>
                <div className="field">
                  <label>Valid until</label>
                  <input type="text" value={createForm.validUntil} disabled={!canManageRoles}
                         onChange={function(e) { setCreateField("validUntil", e.target.value); }}
                         placeholder="optional, e.g. 2027-01-01"/>
                </div>
                <div className="field wide">
                  <label>Member of (comma-separated roles)</label>
                  <input type="text" value={createForm.memberOf} disabled={!canManageRoles}
                         onChange={function(e) { setCreateField("memberOf", e.target.value); }}
                         placeholder="app_readonly, reporting"/>
                </div>
                <div className="field wide">
                  <label>Reason</label>
                  <input type="text" value={createForm.reason} disabled={!canManageRoles}
                         onChange={function(e) { setCreateField("reason", e.target.value); }}
                         placeholder="why this role is needed"/>
                </div>
                <button className="btn sm primary" disabled={!canManageRoles || manageBusy === "create" || !roleNameValid(createForm.roleName)} onClick={submitCreateRole}>
                  <Icon.Plus size={12}/> {manageBusy === "create" ? "Submitting" : "Create Role"}
                </button>
                <div className="muted txt-xs">SUPERUSER is never granted. Password management is unavailable in this bundle.</div>
              </div>
            </div>

            <div className="card config-action">
              <div className="hd">Alter Role <span className="meta">dry-run job</span></div>
              <div className="bd config-action-grid">
                <div className="field wide">
                  <label>Role</label>
                  <select value={alterForm.roleName} disabled={!canManageRoles}
                          onChange={function(e) { setAlterField("roleName", e.target.value); }}>
                    <option value="">select role...</option>
                    {alterableRoles.map(function(r) {
                      return <option key={r.rolname} value={r.rolname}>{r.rolname}</option>;
                    })}
                  </select>
                </div>
                {["login", "createdb", "createrole", "replication", "inherit"].map(function(attr) {
                  return (
                    <div className="field" key={attr}>
                      <label>{attr.toUpperCase()}</label>
                      <select value={alterForm[attr]} disabled={!canManageRoles}
                              onChange={function(e) { setAlterField(attr, e.target.value); }}>
                        <option value="">(no change)</option>
                        <option value="true">enable</option>
                        <option value="false">disable</option>
                      </select>
                    </div>
                  );
                })}
                <div className="field">
                  <label>Connection limit</label>
                  <input type="number" value={alterForm.connectionLimit} disabled={!canManageRoles}
                         onChange={function(e) { setAlterField("connectionLimit", e.target.value); }}
                         placeholder="(no change)"/>
                </div>
                <div className="field">
                  <label>Valid until</label>
                  <input type="text" value={alterForm.validUntil} disabled={!canManageRoles || !!alterForm.clearValidUntil}
                         onChange={function(e) { setAlterField("validUntil", e.target.value); }}
                         placeholder="(no change)"/>
                </div>
                <div className="field">
                  <label style={{display: "flex", gap: 6, alignItems: "center"}}>
                    <input type="checkbox" checked={!!alterForm.clearValidUntil} disabled={!canManageRoles}
                           onChange={function(e) { setAlterField("clearValidUntil", e.target.checked); }}/>
                    Clear expiry
                  </label>
                </div>
                <div className="field wide">
                  <label>Reason</label>
                  <input type="text" value={alterForm.reason} disabled={!canManageRoles}
                         onChange={function(e) { setAlterField("reason", e.target.value); }}/>
                </div>
                <button className="btn sm primary" disabled={!canManageRoles || manageBusy === "alter" || !alterForm.roleName} onClick={submitAlterRole}>
                  <Icon.Settings size={12}/> {manageBusy === "alter" ? "Submitting" : "Alter Role"}
                </button>
                <div className="muted txt-xs">SUPERUSER roles cannot be altered here. Password management is unavailable in this bundle.</div>
              </div>
            </div>

            <div className="card config-action">
              <div className="hd">Drop Role <span className="meta">admin approval</span></div>
              <div className="bd config-action-grid">
                <div className="field wide">
                  <label>Role</label>
                  <select value={dropForm.roleName} disabled={!canDropRole}
                          onChange={function(e) { setDropField("roleName", e.target.value); }}>
                    <option value="">select role...</option>
                    {alterableRoles.map(function(r) {
                      return <option key={r.rolname} value={r.rolname}>{r.rolname}</option>;
                    })}
                  </select>
                </div>
                <div className="field wide">
                  <label>Reason</label>
                  <input type="text" value={dropForm.reason} disabled={!canDropRole}
                         onChange={function(e) { setDropField("reason", e.target.value); }}/>
                </div>
                <button className="btn sm danger" disabled={!canDropRole || manageBusy === "drop" || !dropForm.roleName} onClick={submitDropRole}>
                  <Icon.X size={12}/> {manageBusy === "drop" ? "Submitting" : "Drop Role"}
                </button>
                {!canDropRole && <div className="muted txt-xs">Dropping roles requires the Admin role.</div>}
              </div>
            </div>
          </div>

          <ConfigJobDrawer item={manageJob} onClose={function() { setManageJob(null); }}/>
        </React.Fragment>
      )}

      <div className="card">
        <div className="hd">
          <Icon.Info size={14}/>How PgBouncer Authentication Works
          <div className="grow"/>
          <button className="btn ghost sm" onClick={function() { setInfoOpen(!infoOpen); }}>
            {infoOpen ? <Icon.ChevronDown size={12}/> : <Icon.ChevronRight size={12}/>}
          </button>
        </div>
        {infoOpen && (
          <div className="bd txt-sm">
            <div>PgBouncer uses <span className="mono">auth_query</span> to look up passwords dynamically from PostgreSQL.</div>
            <div>Password resets take effect immediately; no PgBouncer restart is required.</div>
            <div>Only users with a stored SCRAM password show <span className="pill ok">Ready</span> in the PgBouncer column.</div>
            <div>The users.txt file is only used for the internal <span className="mono">_crunchypgbouncer</span> service account.</div>
          </div>
        )}
      </div>

      {resetUser && (
        <ResetPasswordModal user={resetUser}
                            busy={busy}
                            error={resetError}
                            onClose={function() { setResetUser(null); }}
                            onSubmit={resetPassword}/>
      )}

      {rolePanel && (
        <Drawer onClose={function() { setRolePanel(null); }}>
          <div className="hd">
            <Icon.Users size={16}/>
            <div>
              <div style={{fontWeight: 600, fontSize: 14}}>Members of {rolePanel.roleName}</div>
              <div className="muted txt-xs">{rolePanel.memberCount || 0} member(s)</div>
            </div>
            <button className="btn ghost icon" style={{marginLeft: "auto"}} onClick={function() { setRolePanel(null); }}><Icon.X size={14}/></button>
          </div>
          <div className="bd">
            {(rolePanel.members || []).map(function(member) {
              return <div key={member} className="mono" style={{padding: "8px 0", borderBottom: "1px solid var(--divider)"}}>{member}</div>;
            })}
            {(rolePanel.members || []).length === 0 && <div className="muted">No members.</div>}
          </div>
        </Drawer>
      )}
    </div>
  );
}

var PRIVILEGE_OPTIONS = {
  table: ["select", "insert", "update", "delete", "truncate", "references", "trigger", "all"],
  sequence: ["usage", "select", "update", "all"],
  schema: ["usage", "create", "all"],
  database: ["connect", "create", "temporary", "all"]
};

function PrivilegesAdminScreen({ lastRefresh, currentUser }) {
  var roleState = React.useState("");
  var schemaState = React.useState("");
  var dataState = React.useState({ privileges: [], count: 0 });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var role = roleState[0], setRole = roleState[1];
  var schema = schemaState[0], setSchema = schemaState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  var pivFormState = React.useState({
    roleName: "", objectType: "table", database: "postgres", schema: "public",
    object: "", parentRole: "", privileges: [], reason: ""
  });
  var pivBusyState = React.useState(null);
  var pivErrorState = React.useState(null);
  var pivJobState = React.useState(null);
  var pivForm = pivFormState[0], setPivForm = pivFormState[1];
  var pivBusy = pivBusyState[0], setPivBusy = pivBusyState[1];
  var pivError = pivErrorState[0], setPivError = pivErrorState[1];
  var pivJob = pivJobState[0], setPivJob = pivJobState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    adminFetch(clusterPath("/privileges"), { database: "postgres", role: role, schema: schema })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [role, schema, lastRefresh]);

  function setPivField(name, value) {
    var next = Object.assign({}, pivForm, { [name]: value });
    if (name === "objectType") next.privileges = [];
    setPivForm(next);
  }

  function togglePrivilege(p) {
    var current = pivForm.privileges;
    var next = current.indexOf(p) >= 0 ? current.filter(function(x) { return x !== p; }) : current.concat([p]);
    setPivForm(Object.assign({}, pivForm, { privileges: next }));
  }

  function submitPrivilege(action) {
    if (!roleNameValid(pivForm.roleName)) {
      setPivError("A valid role name is required.");
      return;
    }
    var body = { role_name: pivForm.roleName, object_type: pivForm.objectType, reason: pivForm.reason };
    if (pivForm.objectType === "role") {
      if (!roleNameValid(pivForm.parentRole)) {
        setPivError("A valid parent role name is required.");
        return;
      }
      body.object = pivForm.parentRole;
    } else {
      if (!pivForm.privileges.length) {
        setPivError("Select at least one privilege.");
        return;
      }
      body.privileges = pivForm.privileges;
      body.database = pivForm.database;
      if (pivForm.objectType === "table" || pivForm.objectType === "sequence") {
        body.schema = pivForm.schema;
        body.object = pivForm.object;
      } else if (pivForm.objectType === "schema") {
        body.schema = pivForm.schema || pivForm.object;
      } else if (pivForm.objectType === "database") {
        body.database = pivForm.database || pivForm.object;
      }
    }
    setPivBusy(action);
    setPivError(null);
    adminPost(clusterPath("/privileges/" + action + "/validate"), body, "dba")
      .then(function(job) {
        setPivBusy(null);
        setPivJob({ job: job, title: (action === "grant" ? "Grant" : "Revoke") + " privilege" });
      })
      .catch(function(err) { setPivBusy(null); setPivError(err.message || String(err)); });
  }

  var currentRole = currentUser ? (currentUser.role || currentUser.preferred_role) : null;
  var canManagePrivileges = adminRoleAtLeast(currentRole, "dba");
  var privilegeRows = data.privileges || [];
  var privilegeTypeRows = phaseCountRows(privilegeRows, function(p) { return p.privilege_type; }, function() { return "info"; });
  var granteeRows = phaseCountRows(privilegeRows, function(p) { return p.grantee; }, function() { return "teal"; });

  return (
    <div className="page">
      <AdminToolbar loading={loading} error={error} source="information_schema.table_privileges">
        <div className="field" style={{margin: 0, minWidth: 220}}>
          <label>Role</label>
          <input type="text" value={role} onChange={function(e) { setRole(e.target.value); }} placeholder="optional grantee"/>
        </div>
        <div className="field" style={{margin: 0, minWidth: 220}}>
          <label>Schema</label>
          <input type="text" value={schema} onChange={function(e) { setSchema(e.target.value); }} placeholder="optional schema"/>
        </div>
      </AdminToolbar>
      <div className="section-h">Privileges</div>
      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Privilege Types" rows={privilegeTypeRows} center={privilegeRows.length} sub="grants"/></div></div>
        <div className="card"><div className="bd"><BarList title="Top Grantees" rows={granteeRows}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Table Privileges <span className="meta">{(data.privileges || []).length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Schema</th><th>Object</th><th>Grantee</th><th>Privilege</th><th>Grantable</th><th>Grantor</th></tr></thead>
            <tbody>
              {(data.privileges || []).map(function(p, idx) {
                return (
                  <tr key={idx}>
                    <td className="mono">{p.table_schema}</td>
                    <td className="mono">{p.table_name}</td>
                    <td className="mono">{p.grantee}</td>
                    <td><span className="pill info">{p.privilege_type}</span></td>
                    <td>{p.is_grantable}</td>
                    <td className="mono">{p.grantor}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {pivError && (
        <div className="risk-banner high mb-2">
          <Icon.AlertTriangle size={14}/>
          <div>{pivError}</div>
          <button className="btn sm" style={{marginLeft: "auto"}} onClick={function() { setPivError(null); }}>Dismiss</button>
        </div>
      )}

      {!canManagePrivileges && (
        <div className="risk-banner info mb-2">
          <Icon.Info size={14}/>
          <div>Granting or revoking privileges requires the DBA role. Submitted changes create approval jobs.</div>
        </div>
      )}

      <div className="card config-action">
        <div className="hd">Grant / Revoke Privilege <span className="meta">dry-run job</span></div>
        <div className="bd config-action-grid">
          <div className="field">
            <label>Role</label>
            <input type="text" value={pivForm.roleName} disabled={!canManagePrivileges}
                   onChange={function(e) { setPivField("roleName", e.target.value); }}
                   placeholder="grantee role name"/>
          </div>
          <div className="field">
            <label>Object type</label>
            <select value={pivForm.objectType} disabled={!canManagePrivileges}
                    onChange={function(e) { setPivField("objectType", e.target.value); }}>
              <option value="table">Table</option>
              <option value="sequence">Sequence</option>
              <option value="schema">Schema</option>
              <option value="database">Database</option>
              <option value="role">Role membership</option>
            </select>
          </div>

          {pivForm.objectType === "role" && (
            <div className="field">
              <label>Parent role (membership granted)</label>
              <input type="text" value={pivForm.parentRole} disabled={!canManagePrivileges}
                     onChange={function(e) { setPivField("parentRole", e.target.value); }}
                     placeholder="e.g. app_readonly"/>
            </div>
          )}

          {pivForm.objectType !== "role" && pivForm.objectType !== "database" && (
            <div className="field">
              <label>Database</label>
              <input type="text" value={pivForm.database} disabled={!canManagePrivileges}
                     onChange={function(e) { setPivField("database", e.target.value); }}
                     placeholder="postgres"/>
            </div>
          )}

          {(pivForm.objectType === "table" || pivForm.objectType === "sequence") && (
            <React.Fragment>
              <div className="field">
                <label>Schema</label>
                <input type="text" value={pivForm.schema} disabled={!canManagePrivileges}
                       onChange={function(e) { setPivField("schema", e.target.value); }}
                       placeholder="public"/>
              </div>
              <div className="field">
                <label>{pivForm.objectType === "table" ? "Table" : "Sequence"} (blank = ALL in schema)</label>
                <input type="text" value={pivForm.object} disabled={!canManagePrivileges}
                       onChange={function(e) { setPivField("object", e.target.value); }}
                       placeholder={"blank for ALL " + pivForm.objectType.toUpperCase() + "S IN SCHEMA"}/>
              </div>
            </React.Fragment>
          )}

          {pivForm.objectType === "schema" && (
            <div className="field">
              <label>Schema</label>
              <input type="text" value={pivForm.schema} disabled={!canManagePrivileges}
                     onChange={function(e) { setPivField("schema", e.target.value); }}
                     placeholder="public"/>
            </div>
          )}

          {pivForm.objectType === "database" && (
            <div className="field">
              <label>Database</label>
              <input type="text" value={pivForm.database} disabled={!canManagePrivileges}
                     onChange={function(e) { setPivField("database", e.target.value); }}
                     placeholder="postgres"/>
            </div>
          )}

          {pivForm.objectType !== "role" && (
            <div className="field wide">
              <label>Privileges</label>
              <div style={{display: "flex", gap: 12, flexWrap: "wrap"}}>
                {(PRIVILEGE_OPTIONS[pivForm.objectType] || []).map(function(p) {
                  return (
                    <label key={p} style={{display: "flex", gap: 4, alignItems: "center", fontWeight: 400}}>
                      <input type="checkbox" checked={pivForm.privileges.indexOf(p) >= 0} disabled={!canManagePrivileges}
                             onChange={function() { togglePrivilege(p); }}/>
                      {p.toUpperCase()}
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          <div className="field wide">
            <label>Reason</label>
            <input type="text" value={pivForm.reason} disabled={!canManagePrivileges}
                   onChange={function(e) { setPivField("reason", e.target.value); }}
                   placeholder="why this access is needed"/>
          </div>

          <div style={{display: "flex", gap: 8}}>
            <button className="btn sm primary" disabled={!canManagePrivileges || !!pivBusy} onClick={function() { submitPrivilege("grant"); }}>
              <Icon.Check size={12}/> {pivBusy === "grant" ? "Submitting" : "Grant"}
            </button>
            <button className="btn sm danger" disabled={!canManagePrivileges || !!pivBusy} onClick={function() { submitPrivilege("revoke"); }}>
              <Icon.X size={12}/> {pivBusy === "revoke" ? "Submitting" : "Revoke"}
            </button>
          </div>
        </div>
      </div>

      <ConfigJobDrawer item={pivJob} onClose={function() { setPivJob(null); }}/>
    </div>
  );
}

function HbaAdminScreen({ lastRefresh }) {
  var dataState = React.useState({ hba: [], count: 0, readable: true });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  React.useEffect(function() {
    setLoading(true);
    setError(null);
    adminFetch(clusterPath("/hba"))
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [lastRefresh]);
  var hbaRows = data.hba || [];
  var hbaMethodRows = phaseCountRows(hbaRows, function(r) { return r.auth_method || "none"; }, function(method) { return method === "trust" || method === "md5" || method === "password" ? "warn" : "ok"; });
  var hbaTypeRows = phaseCountRows(hbaRows, function(r) { return r.type || "unknown"; }, function() { return "info"; });
  return (
    <div className="page">
      <AdminToolbar loading={loading} error={error} source="pg_hba_file_rules"/>
      <div className="section-h">HBA Rules</div>
      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Auth Methods" rows={hbaMethodRows} center={hbaRows.length} sub="rules"/></div></div>
        <div className="card"><div className="bd"><BarList title="Rule Types" rows={hbaTypeRows}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Loaded Rules <span className="meta">{(data.hba || []).length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th className="num">Line</th><th>Type</th><th>Database</th><th>User</th><th>Address</th><th>Auth</th><th>Error</th></tr></thead>
            <tbody>
              {(data.hba || []).map(function(r) {
                return (
                  <tr key={r.line_number} className={r.error ? "row-warn" : ""}>
                    <td className="num">{r.line_number}</td>
                    <td>{r.type}</td>
                    <td className="mono txt-xs">{(r.database || []).join ? r.database.join(",") : r.database}</td>
                    <td className="mono txt-xs">{(r.user_name || []).join ? r.user_name.join(",") : r.user_name}</td>
                    <td className="mono txt-xs">{r.address || "-"}</td>
                    <td>{r.auth_method || "-"}</td>
                    <td>{r.error || "-"}</td>
                  </tr>
                );
              })}
              {!loading && (!data.hba || data.hba.length === 0) && (
                <tr><td colSpan="7" style={{textAlign: "center", padding: 24}} className="muted">{data.error || "No HBA rows visible."}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ExtensionsAdminScreen({ lastRefresh }) {
  var dbState = React.useState("postgres");
  var dataState = React.useState({ databases: [], extensions: [], count: 0 });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var actionState = React.useState(null);
  var database = dbState[0], setDatabase = dbState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var action = actionState[0], setAction = actionState[1];

  function refresh() {
    setLoading(true);
    setError(null);
    Promise.all([
      adminFetch(clusterPath("/databases")),
      adminFetch(clusterPath("/databases/") + encodeURIComponent(database) + "/extensions")
    ])
      .then(function(parts) {
        setData({ databases: parts[0].databases || [], extensions: parts[1].extensions || [] });
        setLoading(false);
      })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  React.useEffect(refresh, [database, lastRefresh]);

  function validateInstall() {
    setAction("install");
    adminPost(clusterPath("/databases/") + encodeURIComponent(database) + "/extensions", {
      reason: "Phase 2 extension install validation",
      name: "pg_stat_statements"
    }, "dba")
      .then(function() { setAction(null); refresh(); })
      .catch(function(err) { setAction(null); setError(err.message || String(err)); });
  }

  var installed = (data.extensions || []).filter(function(e) { return e.installed_version; }).length;
  var extensionRows = [
    { label: "Installed", value: installed, tone: "ok" },
    { label: "Available only", value: (data.extensions || []).length - installed, tone: "muted" },
  ];
  var schemaRows = phaseCountRows((data.extensions || []).filter(function(e) { return e.installed_version; }), function(e) { return e.schema_name || "unknown"; }, function() { return "info"; });

  return (
    <div className="page">
      <AdminToolbar loading={loading} error={error} source="pg_available_extensions">
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Database</label>
          <select value={database} onChange={function(e) { setDatabase(e.target.value); }}>
            {(data.databases || []).map(function(db) {
              return <option key={db.datname} value={db.datname}>{db.datname}</option>;
            })}
          </select>
        </div>
        <button className="btn sm primary" onClick={validateInstall} disabled={!!action}>
          <Icon.Plus size={12}/> Validate Install
        </button>
      </AdminToolbar>
      <div className="section-h">Extensions</div>
      <div className="grid-4">
        <Stat label="Available" value={(data.extensions || []).length}/>
        <Stat label="Installed" value={installed}/>
        <Stat label="Database" value={database}/>
        <Stat label="Dry-run mode" value="on"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Extension State" rows={extensionRows} center={(data.extensions || []).length} sub="extensions"/></div></div>
        <div className="card"><div className="bd"><BarList title="Installed Schemas" rows={schemaRows}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Extension Catalog <span className="meta">{(data.extensions || []).length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Name</th><th>Installed</th><th>Default</th><th>Schema</th><th>Comment</th></tr></thead>
            <tbody>
              {(data.extensions || []).map(function(ext) {
                return (
                  <tr key={ext.name}>
                    <td className="mono">{ext.name}</td>
                    <td>{ext.installed_version ? <span className="pill ok">{ext.installed_version}</span> : <span className="pill muted">not installed</span>}</td>
                    <td>{ext.default_version}</td>
                    <td className="mono">{ext.schema_name || "-"}</td>
                    <td className="txt-xs">{ext.comment}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function LiveDatabaseConnectScreen({ lastRefresh, currentUser }) {
  var defaultsState = React.useState({ database: "postgres", max_rows: 200, statement_timeout_ms: 15000 });
  var inventoryState = React.useState({ databases: [], read_only: true });
  var databaseState = React.useState("postgres");
  var queryState = React.useState("select current_database(), current_user, now();");
  var maxRowsState = React.useState(100);
  var resultState = React.useState(null);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var defaults = defaultsState[0], setDefaults = defaultsState[1];
  var inventory = inventoryState[0], setInventory = inventoryState[1];
  var database = databaseState[0], setDatabase = databaseState[1];
  var query = queryState[0], setQuery = queryState[1];
  var maxRows = maxRowsState[0], setMaxRows = maxRowsState[1];
  var result = resultState[0], setResult = resultState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true); setError(null);
    Promise.all([
      liveRequest("/api/v1/live-connections/defaults"),
      liveRequest("/api/v1/live-connections")
    ]).then(function(parts) {
      if (!alive) return;
      var cfg = parts[0].defaults || parts[0];
      setDefaults(cfg);
      setInventory(parts[1]);
      setDatabase(cfg.database || "postgres");
      setMaxRows(Math.min(100, Number(cfg.max_rows || cfg.row_limit || 200)));
      setLoading(false);
    }).catch(function(err) {
      if (!alive) return;
      setError(err.message || String(err)); setLoading(false);
    });
    return function() { alive = false; };
  }, [lastRefresh]);

  var currentRole = currentUser ? (currentUser.role || currentUser.preferred_role) : null;
  if (currentUser && !adminRoleAtLeast(currentRole, "dba")) {
    return <div className="page"><div className="section-h">Read-only SQL</div><div className="card"><div className="hd"><Icon.Lock size={16}/> DBA access required</div></div></div>;
  }

  var limit = Number(defaults.max_rows || defaults.row_limit || 200);
  var canRun = hbzRequired(query) && hbzPositiveNumber(maxRows) && Number(maxRows) <= limit;
  function runQuery() {
    if (!canRun) return;
    setLoading(true); setError(null); setResult(null);
    liveRequest("/api/v1/live-connections", {
      method: "POST",
      body: { database: database, query: query, row_limit: Number(maxRows) }
    }).then(function(payload) {
      if (!payload.ok) throw new Error(payload.error || "Read-only query failed.");
      setResult(payload); setLoading(false);
    }).catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }

  var columns = result && result.columns ? result.columns : [];
  var rows = result && result.rows ? result.rows : [];
  return (
    <div className="page">
      <AdminToolbar loading={loading} error={error} source={inventory.source || "DBA read-only query"}>
        <span className="pill ok"><Icon.Lock size={12}/>Read-only enforced</span>
        <span className="pill muted">{Math.round(Number(defaults.statement_timeout_ms || 0) / 1000)}s timeout</span>
        {inventory.generated_at && <span className="pill muted">refreshed {inventory.generated_at}</span>}
      </AdminToolbar>
      <div className="grid-4">
        <Stat label="Mode" value="read-only"/>
        <Stat label="Databases" value={(inventory.databases || []).length}/>
        <Stat label="Database" value={database}/>
        <Stat label="Row limit" value={limit}/>
      </div>
      <div className="card"><div className="hd"><Icon.Terminal size={16}/> Guarded SQL</div><div className="bd">
        <div className="field"><label>Database</label><select value={database} onChange={function(e) { setDatabase(e.target.value); }}>{(inventory.databases || []).map(function(name) { return <option key={name} value={name}>{name}</option>; })}</select></div>
        <textarea value={query} onChange={function(e) { setQuery(e.target.value); }} spellCheck="false" style={{width:"100%",minHeight:120,fontFamily:"var(--font-mono)",fontSize:12}}/>
        <div style={{display:"flex",gap:10,alignItems:"end",marginTop:10}}><div className="field" style={{margin:0,width:150}}><label>Max rows</label><input type="number" min="1" max={limit} value={maxRows} onChange={function(e) { setMaxRows(e.target.value); }}/></div><button className="btn sm primary" onClick={runQuery} disabled={loading || !canRun}><Icon.Play size={12}/> Run read-only</button></div>
      </div></div>
      {result && <div className="card"><div className="hd">Result <span className="meta">{result.rowcount || 0} rows</span></div><div style={{overflowX:"auto"}}><table className="tbl"><thead><tr>{columns.map(function(col) { return <th key={col}>{col}</th>; })}</tr></thead><tbody>{rows.map(function(row, idx) { return <tr key={idx}>{columns.map(function(col, colIdx) { return <td key={col} className="mono txt-xs">{liveCell(row[colIdx])}</td>; })}</tr>; })}{rows.length === 0 && <tr><td colSpan={Math.max(columns.length,1)} className="muted">No rows returned.</td></tr>}</tbody></table></div></div>}
    </div>
  );
}
