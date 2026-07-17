// Phase 5 Replication & HA screens.

function replFetch(path, params) {
  return v1Json(path, params || {});
}

function replPost(path, body, role) {
  return fetch(path, {
    method: "POST",
    headers: {"content-type": "application/json", "x-console-role": role || "dba"},
    body: JSON.stringify(body || {})
  }).then(hbzJsonResponse);
}

function replDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("en-GB", { hour12: false });
}

function replSec(value) {
  if (value === null || value === undefined) return "-";
  return fmtSec(Number(value || 0));
}

function replMemberShort(name) {
  return name ? String(name).replace(clusterPrefix(), "") : "-";
}

function replRoleTone(role) {
  if (role === "leader") return "ok";
  if (role === "sync_standby") return "info";
  if (role === "replica") return "muted";
  return "muted";
}

function ReplToolbar({ children, loading, error, source }) {
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

function ReplHero({ title, detail, tone, children }) {
  return (
    <div className={"repl-hero " + (tone || "ok")}>
      <div>
        <div className="repl-hero-kicker">Phase 5 Replication & HA</div>
        <div className="repl-hero-title">{title}</div>
        <div className="repl-hero-detail">{detail}</div>
      </div>
      <div className="repl-hero-side">{children}</div>
    </div>
  );
}

function ReplJobDrawer({ item, onClose }) {
  if (!item) return null;
  return (
    <Drawer onClose={onClose}>
      <div className="hd">
        <Icon.GitBranch size={16}/>
        <div>
          <div style={{fontWeight: 600, fontSize: 14}}>Replication & HA detail</div>
          <div className="muted txt-xs">{item.job ? item.job.state : item.title || "detail"}</div>
        </div>
        <button className="btn ghost icon" style={{marginLeft: "auto"}} onClick={onClose} aria-label="Close"><Icon.X size={14}/></button>
      </div>
      <div className="bd">
        {item.job && (
          <div className="risk-banner">
            <Icon.Info size={14}/>
            <div>
              <strong>Dry-run HA job created</strong>
              <div className="txt-xs mt-2">No Patroni, PostgreSQL, pod, or replication operation was executed.</div>
            </div>
          </div>
        )}
        <pre className="logbox mt-3" style={{whiteSpace: "pre-wrap"}}>{JSON.stringify(item, null, 2)}</pre>
      </div>
    </Drawer>
  );
}

function ReplMembers({ members, replication }) {
  var repRows = replication || [];
  function findRep(member) {
    for (var i = 0; i < repRows.length; i++) {
      if (repRows[i].application_name === member.name) return repRows[i];
    }
    return null;
  }
  return (
    <div className="repl-node-grid">
      {(members || []).map(function(member) {
        var rep = findRep(member) || {};
        var lag = rep.replay_lag_bytes != null ? rep.replay_lag_bytes : (member.lag || member.replay_lag || 0);
        return (
          <div key={member.name} className={"repl-node " + (member.role === "leader" ? "leader" : "")}>
            <div className="repl-node-top">
              <span className={"pill " + replRoleTone(member.role)}><span className="dot"/>{member.role || "member"}</span>
              <span className="pill muted">TL {member.timeline || "-"}</span>
            </div>
            <div className="repl-node-name">{replMemberShort(member.name)}</div>
            <div className="repl-node-state">
              <span className={"led " + (member.state === "running" || member.state === "streaming" ? "ok" : "warn")}/>
              {member.state || "-"}
            </div>
            <div className="repl-node-meta">
              <span>LSN</span>
              <strong>{member.lsn || rep.replay_lsn || "-"}</strong>
            </div>
            {member.role !== "leader" && (
              <div className="repl-node-meta">
                <span>Replay lag</span>
                <strong>{fmtBytes(Number(lag || 0))}</strong>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ReplActionPanel({ members, onJob }) {
  var busyState = React.useState(null);
  var errorState = React.useState(null);
  var targetState = React.useState("");
  var reasonState = React.useState("Phase 5 HA action validation");
  var busy = busyState[0], setBusy = busyState[1];
  var error = errorState[0], setError = errorState[1];
  var target = targetState[0], setTarget = targetState[1];
  var reason = reasonState[0], setReason = reasonState[1];
  var rows = members || [];
  var nonLeaders = rows.filter(function(member) { return member.role !== "leader"; });
  var defaultTarget = target || (nonLeaders[0] ? nonLeaders[0].name : (rows[0] ? rows[0].name : ""));
  var reasonText = String(reason || "").trim();
  var reasonOk = reasonText.length >= 6;
  var canValidateHa = hbzRequired(defaultTarget) && reasonOk;

  function validate(action, role) {
    if (!hbzRequired(defaultTarget)) {
      setError("Select a target member before validating a HA action.");
      return;
    }
    if (String(reason || "").trim().length < 6) {
      setError("Enter a meaningful reason (at least 6 characters) for the audit trail before validating.");
      return;
    }
    setBusy(action);
    setError(null);
    replPost(clusterPath("/replication/actions/") + action + "/validate", {
      target: defaultTarget,
      reason: String(reason).trim()
    }, role || "dba")
      .then(function(job) { setBusy(null); if (onJob) onJob({ job: job, title: action }); })
      .catch(function(err) { setBusy(null); setError(hbzErrorText(err)); });
  }

  return (
    <div className="card repl-action">
      <div className="hd">Validate HA Action <span className="meta">dry-run only</span></div>
      <div className="bd">
        {error && <div className="pill danger mb-2"><span className="dot"/>{error}</div>}
        <div className="repl-action-grid">
          <div className="field">
            <label>Target member</label>
            <select value={defaultTarget} onChange={function(e) { setTarget(e.target.value); }}>
              {rows.map(function(member) {
                return <option key={member.name} value={member.name}>{replMemberShort(member.name)} / {member.role}</option>;
              })}
            </select>
          </div>
          <div className="field wide">
            <label>Reason</label>
            <input type="text" value={reason} maxLength={160} placeholder="e.g. Scheduled DC failover drill" onChange={function(e) { setReason(e.target.value); }}/>
            {!reasonOk && <div className="field-hint warn">Required for the audit trail — at least 6 characters.</div>}
          </div>
          <button className="btn sm primary" disabled={!!busy || !canValidateHa} onClick={function() { validate("switchover", "dba"); }}>
            <Icon.GitBranch size={12}/> {busy === "switchover" ? "Validating\u2026" : "Validate switchover"}
          </button>
          <button className="btn sm" disabled={!!busy || !canValidateHa} onClick={function() { validate("restart", "dba"); }}>
            <Icon.RefreshCw size={12}/> {busy === "restart" ? "Validating\u2026" : "Validate restart"}
          </button>
          <button className="btn sm" disabled={!!busy || !canValidateHa} onClick={function() { validate("reinit", "dba"); }}>
            <Icon.HardDrive size={12}/> {busy === "reinit" ? "Validating\u2026" : "Validate reinit"}
          </button>
          <button className="btn sm danger" disabled={!!busy || !canValidateHa} onClick={function() { validate("failover", "admin"); }}>
            <Icon.ShieldAlert size={12}/> {busy === "failover" ? "Validating\u2026" : "Validate failover"}
          </button>
        </div>
        <div className="repl-action-note">
          <Icon.Info size={12}/>
          <span>Dry-run only — these create a validation job and report whether the action is safe. No switchover, failover, restart, or reinit is executed on the cluster.</span>
        </div>
      </div>
    </div>
  );
}

var _lagHistory = [];

function ReplTopologyScreen({ lastRefresh }) {
  var dataState = React.useState({ summary: {}, members: [], replication: [], slots: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    replFetch(clusterPath("/replication/topology"))
      .then(function(payload) {
        setData(payload);
        setLoading(false);
        var repl = payload.replication || [];
        var ts = new Date().toLocaleTimeString();
        var sample = { time: ts };
        for (var ri = 0; ri < repl.length; ri++) {
          var name = repl[ri].application_name || ("standby_" + ri);
          sample[name] = Number(repl[ri].replay_lag_bytes || 0);
        }
        _lagHistory.push(sample);
        if (_lagHistory.length > 60) _lagHistory.shift();
      })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [lastRefresh]);

  var summary = data.summary || {};
  var members = data.members || [];
  var replication = data.replication || [];
  var slots = data.slots || [];
  var lagTone = Number(summary.max_lag_bytes || 0) > 0 ? "warn" : "ok";
  var memberRoleRows = phaseCountRows(members, function(member) { return member.role || "member"; }, function(role) { return replRoleTone(role); });
  var replLagRows = replication.map(function(row) { return { label: row.application_name, value: Number(row.replay_lag_bytes || 0), sub: row.sync_state || "-", tone: Number(row.replay_lag_bytes || 0) > 0 ? "warn" : "ok" }; });
  var slotWalRows = slots.map(function(slot) { return { label: slot.slot_name, value: Math.max(1, Number(slot.retained_wal_bytes || 0)), sub: fmtBytes(Number(slot.retained_wal_bytes || 0)), tone: slot.active ? "ok" : "warn" }; });
  var syncStateRows = phaseCountRows(replication, function(row) { return row.sync_state || "unknown"; }, function(state) { return state === "sync" ? "ok" : state === "async" ? "muted" : "info"; });
  var dataCenters = data.data_centers || [];
  var pgo = data.pgo || {};
  var pgbackrest = pgo.pgbackrest || {};
  var pgoRepos = pgbackrest.repos || [];
  var standby = pgo.standby || {};
  var dcMemberRows = dataCenters.map(function(dc) { return { label: dc.name, value: Number(dc.members || 0), sub: (dc.ready_members || 0) + "/" + (dc.configured_replicas || dc.members || 0) + " ready", tone: dc.status === "ok" ? "ok" : dc.status === "missing" ? "danger" : "warn" }; });

  return (
    <div className="page">
      <ReplToolbar loading={loading} error={error} source={data.source || "live replication state"}/>

      <ReplHero title="Replication Topology"
                detail="Live Patroni membership with PostgreSQL streaming and slot health."
                tone={summary.patroni_ok ? lagTone : "danger"}>
        <span className={"pill " + (summary.patroni_ok ? "ok" : "danger")}><span className="dot"/>{summary.patroni_ok ? "Patroni reachable" : "Patroni unreachable"}</span>
        <span className={"pill " + lagTone}>max lag {fmtBytes(Number(summary.max_lag_bytes || 0))}</span>
      </ReplHero>

      <div className="grid-4">
        <Stat label="Leader" value={replMemberShort(summary.leader)}/>
        <Stat label="Members" value={fmtInt(summary.members || 0)}/>
        <Stat label="Streaming" value={fmtInt(summary.streaming_connections || 0)}/>
        <Stat label="Slots" value={fmtInt(summary.replication_slots || 0)} sub={fmtInt(summary.inactive_slots || 0) + " inactive"}/>
      </div>

      <div className="grid-4">
        <div className="card"><div className="bd"><DonutChart title="Member Roles" rows={memberRoleRows} center={members.length} sub="members"/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Sync State" rows={syncStateRows} center={replication.length} sub="connections"/></div></div>
        <div className="card"><div className="bd"><BarList title="Replay Lag" rows={replLagRows} valueFormatter={fmtBytes}/></div></div>
        <div className="card"><div className="bd"><BarList title="Retained WAL" rows={slotWalRows} valueFormatter={fmtBytes}/></div></div>
      </div>

      {_lagHistory.length >= 2 && (function() {
        var standbyNames = [];
        var namesSeen = {};
        for (var hi = 0; hi < _lagHistory.length; hi++) {
          var keys = Object.keys(_lagHistory[hi]);
          for (var ki = 0; ki < keys.length; ki++) {
            if (keys[ki] !== "time" && !namesSeen[keys[ki]]) {
              standbyNames.push(keys[ki]);
              namesSeen[keys[ki]] = true;
            }
          }
        }
        var lagColors = ["#8b5cf6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6"];
        var series = standbyNames.map(function(name, idx) {
          return {
            name: name,
            type: "line",
            smooth: true,
            symbol: "circle",
            symbolSize: 4,
            lineStyle: { width: 2 },
            areaStyle: { opacity: 0.08 },
            itemStyle: { color: lagColors[idx % lagColors.length] },
            data: _lagHistory.map(function(s) { return s[name] || 0; })
          };
        });
        var option = {
          tooltip: { trigger: "axis", formatter: function(params) {
            var lines = [params[0].axisValueLabel];
            for (var pi = 0; pi < params.length; pi++) {
              lines.push(params[pi].marker + " " + params[pi].seriesName + ": " + fmtBytes(params[pi].value));
            }
            return lines.join("<br/>");
          }},
          legend: { data: standbyNames, bottom: 0, textStyle: { fontSize: 11 } },
          grid: { top: 30, right: 20, bottom: 40, left: 65 },
          xAxis: { type: "category", data: _lagHistory.map(function(s) { return s.time; }), axisLabel: { fontSize: 10 } },
          yAxis: { type: "value", name: "Lag (bytes)", axisLabel: { formatter: function(v) { return fmtBytes(v); }, fontSize: 10 }, nameTextStyle: { fontSize: 11 } },
          series: series
        };
        return React.createElement("div", { className: "card" },
          React.createElement("div", { className: "hd" }, "Replication Lag Timeline ", React.createElement("span", { className: "meta" }, _lagHistory.length + " samples (auto-refresh)")),
          React.createElement("div", { ref: function(el) {
            if (el && typeof echarts !== "undefined") {
              var chart = echarts.getInstanceByDom(el) || echarts.init(el);
              chart.setOption(option, true);
            }
          }, style: { width: "100%", height: 280 } })
        );
      })()}


      {(dataCenters.length > 0 || pgo.available) && (
        <div className="grid-2">
          <div className="card">
            <div className="hd">DC / Instance Sets <span className="meta">PGO live</span></div>
            <div className="bd">
              <BarList title="Members by DC" rows={dcMemberRows}/>
              <div style={{overflowX: "auto", marginTop: 12}}>
                <table className="tbl">
                  <thead><tr><th>Set</th><th>Ready</th><th>Streaming</th><th>Roles</th><th className="num">Max lag</th></tr></thead>
                  <tbody>
                    {dataCenters.map(function(dc) {
                      return <tr key={dc.name}><td className="mono">{dc.name}</td><td>{dc.ready_members || 0}/{dc.configured_replicas || dc.members || 0}</td><td>{dc.streaming_connections || 0}</td><td>{Object.keys(dc.roles || {}).map(function(role) { return role + ":" + dc.roles[role]; }).join(", ") || "-"}</td><td className="num">{fmtBytes(Number(dc.max_lag_bytes || 0))}</td></tr>;
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="hd">PGO / pgBackRest Fallback <span className="meta">repo based</span></div>
            <div className="bd">
              <div className="grid-2">
                <Stat label="PGO CR" value={pgo.available ? "Reachable" : "Unavailable"} sub={pgo.error || pgo.name || "-"}/>
                <Stat label="Standby mode" value={standby.enabled ? "Enabled" : "Disabled"} sub={standby.repoName || "repo not set"}/>
              </div>
              <table className="tbl mt-3">
                <thead><tr><th>Repo</th><th>Bucket</th><th>Endpoint</th><th>Schedules</th></tr></thead>
                <tbody>
                  {pgoRepos.map(function(repo) {
                    var s3 = repo.s3 || {};
                    var schedules = repo.schedules || {};
                    return <tr key={repo.name || s3.bucket}><td className="mono">{repo.name || "repo"}</td><td>{s3.bucket || "-"}</td><td>{s3.endpoint || "-"}</td><td className="mono txt-xs">{[schedules.full, schedules.diff, schedules.incr].filter(Boolean).join(" | ") || "-"}</td></tr>;
                  })}
                  {pgoRepos.length === 0 && <tr><td colSpan="4" style={{textAlign: "center", padding: 18}} className="muted">No pgBackRest repo details visible from the PostgresCluster CR.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      <ReplMembers members={members} replication={replication}/>
      <ReplActionPanel members={members} onJob={setSelected}/>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Streaming Replication <span className="meta">{replication.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Application</th><th>Client</th><th>State</th><th>Sync</th><th className="num">Replay lag</th><th className="num">Reply age</th></tr></thead>
              <tbody>
                {replication.map(function(row) {
                  return <tr key={row.pid}><td className="mono">{row.application_name}</td><td>{row.client_addr || "-"}</td><td>{row.state}</td><td><span className={"pill " + (row.sync_state === "sync" ? "ok" : row.sync_state === "async" ? "muted" : "info")}>{row.sync_state || "-"}</span></td><td className="num">{fmtBytes(Number(row.replay_lag_bytes || 0))}</td><td className="num">{replSec(row.reply_age_sec)}</td></tr>;
                })}
                {!loading && replication.length === 0 && <tr><td colSpan="6" style={{textAlign: "center", padding: 24}} className="muted">No streaming replication rows visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Replication Slots <span className="meta">{slots.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Slot</th><th>Type</th><th>Database</th><th>Active</th><th className="num">Retained WAL</th><th>Status</th></tr></thead>
              <tbody>
                {slots.map(function(slot) {
                  return <tr key={slot.slot_name} className={!slot.active ? "row-warn" : ""}><td className="mono">{slot.slot_name}</td><td>{slot.slot_type}</td><td>{slot.database || "-"}</td><td>{slot.active ? <span className="pill ok">active</span> : <span className="pill warn">inactive</span>}</td><td className="num">{fmtBytes(Number(slot.retained_wal_bytes || 0))}</td><td>{slot.wal_status || "-"}</td></tr>;
                })}
                {!loading && slots.length === 0 && <tr><td colSpan="6" style={{textAlign: "center", padding: 24}} className="muted">No replication slots visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <ReplJobDrawer item={selected} onClose={function() { setSelected(null); }}/>
    </div>
  );
}

function ReplSyncScreen({ lastRefresh }) {
  var dataState = React.useState({ summary: {}, replication: [], sync_members: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    replFetch(clusterPath("/replication/sync"))
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [lastRefresh]);

  var summary = data.summary || {};
  var rows = data.replication || [];
  var syncRows = data.sync_candidates || [];
  var candidateRows = phaseCountRows(rows, function(row) { return row.sync_state || "unknown"; }, function(state) { return state === "sync" ? "ok" : state === "potential" ? "info" : "muted"; });
  var lagRows = rows.map(function(row) { return { label: row.application_name, value: Number(row.replay_lag_sec || row.reply_age_sec || 0), sub: row.sync_state || "-", tone: Number(row.replay_lag_sec || 0) > 1 ? "warn" : "ok" }; });

  return (
    <div className="page">
      <ReplToolbar loading={loading} error={error} source={data.source || "sync replication state"}/>
      <ReplHero title="Sync Standbys"
                detail="Patroni synchronous mode and PostgreSQL sync candidate state."
                tone={summary.synchronous_mode ? "ok" : "warn"}>
        <span className={"pill " + (summary.synchronous_mode ? "ok" : "warn")}><span className="dot"/>sync mode {String(summary.synchronous_mode)}</span>
        <span className="pill muted">commit {summary.synchronous_commit || "-"}</span>
      </ReplHero>

      <div className="grid-4">
        <Stat label="Leader" value={replMemberShort(summary.leader)}/>
        <Stat label="Patroni sync" value={fmtInt(summary.patroni_sync_standbys || 0)}/>
        <Stat label="PG sync candidates" value={fmtInt(summary.pg_sync_candidates || 0)}/>
        <Stat label="Sync node count" value={summary.synchronous_node_count == null ? "-" : summary.synchronous_node_count}/>
      </div>

      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Candidate State" rows={candidateRows} center={rows.length} sub="senders"/></div></div>
        <div className="card"><div className="bd"><BarList title="Reply Age" rows={lagRows} valueFormatter={function(v) { return replSec(v); }}/></div></div>
      </div>

      <div className="card">
        <div className="hd">Synchronous Standby Names <span className="meta">pg_settings</span></div>
        <div className="bd">
          <pre className="logbox" style={{whiteSpace: "pre-wrap"}}>{summary.synchronous_standby_names || "-"}</pre>
        </div>
      </div>

      <div className="card">
        <div className="hd">Replication Candidates <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Application</th><th>State</th><th>Sync state</th><th className="num">Priority</th><th className="num">Write lag</th><th className="num">Flush lag</th><th className="num">Replay lag</th></tr></thead>
            <tbody>
              {rows.map(function(row) {
                return <tr key={row.pid}><td className="mono">{row.application_name}</td><td>{row.state}</td><td><span className={"pill " + (row.sync_state === "sync" ? "ok" : row.sync_state === "potential" ? "info" : "muted")}>{row.sync_state || "-"}</span></td><td className="num">{row.sync_priority || 0}</td><td className="num">{replSec(row.write_lag_sec)}</td><td className="num">{replSec(row.flush_lag_sec)}</td><td className="num">{replSec(row.replay_lag_sec)}</td></tr>;
              })}
              {!loading && rows.length === 0 && <tr><td colSpan="7" style={{textAlign: "center", padding: 24}} className="muted">No sync candidate rows visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="hd">Current Sync Candidates <span className="meta">{syncRows.length} rows</span></div>
        <div className="bd repl-chip-list">
          {syncRows.map(function(row) {
            return <span key={row.pid} className="pill info">{row.application_name} / {row.sync_state}</span>;
          })}
          {!loading && syncRows.length === 0 && <span className="muted txt-xs">No PostgreSQL sync candidates visible.</span>}
        </div>
      </div>
    </div>
  );
}

function ReplLogicalScreen({ lastRefresh }) {
  var dbState = React.useState("postgres");
  var dataState = React.useState({ summary: {}, publications: [], subscriptions: [], logical_slots: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var database = dbState[0], setDatabase = dbState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    replFetch(clusterPath("/replication/logical"), { database: database })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [database, lastRefresh]);

  var summary = data.summary || {};
  var publications = data.publications || [];
  var subscriptions = data.subscriptions || [];
  var subHealth = data.subscription_health || [];
  var subTablesNotReady = data.subscription_tables_not_ready || [];
  var slots = data.logical_slots || [];
  var tableStates = summary.subscription_table_states || {};
  var totalSubTables = Number(summary.subscription_total_tables || 0);
  var readySubTables = Number(summary.subscription_tables_ready || 0);
  var notReadyCount = Number(summary.subscription_tables_not_ready || 0);
  var hasTableIssues = notReadyCount > 0;
  var logicalMixRows = [
    { label: "Publications", value: Number(summary.publications || 0), tone: "info" },
    { label: "Subscriptions", value: Number(summary.subscriptions || 0), tone: "ok" },
    { label: "Workers", value: Number(summary.subscription_workers || 0), tone: "teal" },
    { label: "Logical slots", value: Number(summary.logical_slots || 0), tone: summary.inactive_logical_slots ? "warn" : "purple" },
  ];
  var tableStateRows = Object.keys(tableStates).map(function(state) {
    return { label: state, value: tableStates[state], tone: state === "ready" ? "ok" : state === "synchronized" ? "ok" : state === "data_copy" ? "info" : state === "initialize" ? "warn" : state === "finalize" ? "teal" : "warn" };
  });
  var slotWalRows = slots.map(function(slot) { return { label: slot.slot_name, value: Math.max(1, Number(slot.retained_wal_bytes || 0)), sub: fmtBytes(Number(slot.retained_wal_bytes || 0)), tone: slot.active ? "ok" : "warn" }; });
  var subHealthRows = subHealth.map(function(sh) { return { label: sh.subname, value: sh.total_tables > 0 ? sh.pct_ready : 0, sub: sh.ready + "/" + sh.total_tables + " ready", tone: sh.status === "ok" ? "ok" : sh.status === "syncing" ? "warn" : "danger" }; });

  return (
    <div className="page">
      <ReplToolbar loading={loading} error={error} source={data.source || "logical replication catalog"}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Database</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }}/>
        </div>
      </ReplToolbar>

      <ReplHero title="Logical Replication"
                detail="Publications, subscriptions, table sync status, and logical slots for the selected database."
                tone={hasTableIssues ? "warn" : summary.inactive_logical_slots ? "warn" : "ok"}>
        <span className="pill info">{fmtInt(summary.publications || 0)} publications</span>
        <span className={"pill " + (hasTableIssues ? "warn" : "ok")}>{readySubTables}/{totalSubTables} tables ready</span>
        <span className={"pill " + (summary.inactive_logical_slots ? "warn" : "ok")}>{fmtInt(summary.logical_slots || 0)} logical slots</span>
      </ReplHero>

      <div className="grid-4" style={{gridTemplateColumns: "repeat(6, 1fr)"}}>
        <Stat label="Publications" value={fmtInt(summary.publications || 0)}/>
        <Stat label="Published tables" value={fmtInt(summary.publication_tables || 0)}/>
        <Stat label="Subscriptions" value={fmtInt(summary.subscriptions || 0)}/>
        <Stat label="Workers" value={fmtInt(summary.subscription_workers || 0)}/>
        <Stat label="Tables ready" value={fmtInt(readySubTables)} tone="ok"/>
        <Stat label="Tables not ready" value={fmtInt(notReadyCount)} tone={notReadyCount > 0 ? "warn" : "ok"}/>
      </div>

      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Table Sync States" rows={tableStateRows.length > 0 ? tableStateRows : [{label: "none", value: 1, tone: "muted"}]} center={fmtInt(totalSubTables)} sub="tables"/></div></div>
        <div className="card"><div className="bd"><BarList title="Subscription Health (% ready)" rows={subHealthRows} valueFormatter={function(v) { return v.toFixed(0) + "%"; }}/></div></div>
      </div>

      {subHealth.length > 0 && <div className="card">
        <div className="hd">Subscription Health <span className="meta">{subHealth.length} subscriptions</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Subscription</th><th>Enabled</th><th>Slot</th><th className="num">Total tables</th><th className="num">Ready</th><th className="num">Not ready</th><th>% Ready</th><th>Status</th></tr></thead>
            <tbody>
              {subHealth.map(function(sh) {
                var statusPill = sh.status === "ok" ? "ok" : sh.status === "syncing" ? "warn" : sh.status === "empty" ? "muted" : "danger";
                return <tr key={sh.subname} className={sh.not_ready > 0 ? "row-warn" : ""}><td className="mono">{sh.subname}</td><td>{sh.enabled ? <span className="pill ok">yes</span> : <span className="pill warn">no</span>}</td><td className="mono">{sh.slot || "-"}</td><td className="num">{sh.total_tables}</td><td className="num">{sh.ready}</td><td className="num" style={{fontWeight: sh.not_ready > 0 ? 600 : 400, color: sh.not_ready > 0 ? "var(--clr-warn)" : "inherit"}}>{sh.not_ready}</td><td><div style={{background: "var(--clr-surface)", borderRadius: 4, height: 8, width: 80, position: "relative"}}><div style={{background: sh.pct_ready === 100 ? "var(--clr-ok)" : "var(--clr-warn)", borderRadius: 4, height: 8, width: sh.pct_ready + "%", position: "absolute"}}></div></div> {sh.pct_ready}%</td><td><span className={"pill " + statusPill}>{sh.status}</span></td></tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>}

      {subTablesNotReady.length > 0 && <div className="card" style={{borderLeft: "3px solid var(--clr-warn)"}}>
        <div className="hd" style={{color: "var(--clr-warn)"}}>Tables Not Ready <span className="meta">{subTablesNotReady.length} tables need attention</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Subscription</th><th>Table</th><th>State</th><th>LSN</th></tr></thead>
            <tbody>
              {subTablesNotReady.map(function(t, i) {
                var statePill = t.sync_state === "d" ? "info" : t.sync_state === "f" ? "teal" : t.sync_state === "s" ? "ok" : t.sync_state === "i" ? "warn" : "danger";
                return <tr key={i}><td className="mono">{t.subname}</td><td className="mono">{t.table_name}</td><td><span className={"pill " + statePill}>{t.sync_state_label || t.sync_state}</span></td><td className="mono txt-xs">{t.sync_lsn || "-"}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>}

      <div className="grid-2">
        <div className="card"><div className="bd"><BarList title="Logical Slot WAL" rows={slotWalRows} valueFormatter={fmtBytes}/></div></div>
        <div className="card" style={{visibility: "hidden"}}></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Publications <span className="meta">{publications.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Name</th><th>Owner</th><th className="num">Tables</th><th>Ops</th></tr></thead>
              <tbody>
                {publications.map(function(pub) {
                  return <tr key={pub.pubname}><td className="mono">{pub.pubname}</td><td className="mono">{pub.owner}</td><td className="num">{pub.table_count}</td><td>{pub.puballtables && <span className="pill ok">all</span>} {pub.pubinsert && <span className="pill info">insert</span>} {pub.pubupdate && <span className="pill info">update</span>} {pub.pubdelete && <span className="pill info">delete</span>}</td></tr>;
                })}
                {!loading && publications.length === 0 && <tr><td colSpan="4" style={{textAlign: "center", padding: 24}} className="muted">No publications visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Subscriptions <span className="meta">{subscriptions.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Name</th><th>Owner</th><th>Enabled</th><th>Slot</th><th>Publications</th></tr></thead>
              <tbody>
                {subscriptions.map(function(sub) {
                  return <tr key={sub.subname}><td className="mono">{sub.subname}</td><td className="mono">{sub.owner}</td><td>{sub.subenabled ? <span className="pill ok">enabled</span> : <span className="pill warn">disabled</span>}</td><td className="mono">{sub.subslotname || "-"}</td><td className="txt-xs">{(sub.subpublications || []).join ? sub.subpublications.join(", ") : String(sub.subpublications || "-")}</td></tr>;
                })}
                {!loading && subscriptions.length === 0 && <tr><td colSpan="5" style={{textAlign: "center", padding: 24}} className="muted">No subscriptions visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">Logical Slots <span className="meta">{slots.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Slot</th><th>Plugin</th><th>Database</th><th>Active</th><th className="num">Retained WAL</th><th>Status</th></tr></thead>
            <tbody>
              {slots.map(function(slot) {
                return <tr key={slot.slot_name} className={!slot.active ? "row-warn" : ""}><td className="mono">{slot.slot_name}</td><td>{slot.plugin || "-"}</td><td>{slot.database || "-"}</td><td>{slot.active ? <span className="pill ok">active</span> : <span className="pill warn">inactive</span>}</td><td className="num">{fmtBytes(Number(slot.retained_wal_bytes || 0))}</td><td>{slot.wal_status || "-"}</td></tr>;
              })}
              {!loading && slots.length === 0 && <tr><td colSpan="6" style={{textAlign: "center", padding: 24}} className="muted">No logical slots visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ReplFdwScreen({ lastRefresh }) {
  var dbState = React.useState("postgres");
  var dataState = React.useState({ summary: {}, wrappers: [], servers: [], foreign_tables: [], user_mappings: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var database = dbState[0], setDatabase = dbState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    replFetch(clusterPath("/replication/fdw"), { database: database })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [database, lastRefresh]);

  var summary = data.summary || {};
  var servers = data.servers || [];
  var tables = data.foreign_tables || [];
  var mappings = data.user_mappings || [];
  var fdwRows = [
    { label: "Wrappers", value: Number(summary.wrappers || 0), tone: "info" },
    { label: "Servers", value: Number(summary.servers || 0), tone: "ok" },
    { label: "Foreign tables", value: Number(summary.foreign_tables || 0), tone: "teal" },
    { label: "Mappings", value: Number(summary.user_mappings || 0), tone: "purple" },
  ];
  var serverRows = phaseCountRows(servers, function(server) { return server.fdwname || "unknown"; }, function() { return "info"; });

  return (
    <div className="page">
      <ReplToolbar loading={loading} error={error || data.error} source={data.source || "FDW catalog"}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Database</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }}/>
        </div>
      </ReplToolbar>

      <ReplHero title="Foreign Data Wrappers"
                detail="FDW wrappers, foreign servers, foreign tables, and user mappings for the selected database."
                tone={summary.servers ? "info" : "ok"}>
        <span className="pill info">{fmtInt(summary.servers || 0)} servers</span>
        <span className="pill muted">{fmtInt(summary.foreign_tables || 0)} foreign tables</span>
      </ReplHero>

      <div className="grid-4">
        <Stat label="Wrappers" value={fmtInt(summary.wrappers || 0)}/>
        <Stat label="Servers" value={fmtInt(summary.servers || 0)}/>
        <Stat label="Foreign tables" value={fmtInt(summary.foreign_tables || 0)}/>
        <Stat label="Mappings" value={fmtInt(summary.user_mappings || 0)}/>
      </div>

      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="FDW Inventory" rows={fdwRows} center={fmtInt((summary.servers || 0) + (summary.foreign_tables || 0))} sub="server/table"/></div></div>
        <div className="card"><div className="bd"><BarList title="Servers by FDW" rows={serverRows}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Foreign Servers <span className="meta">{servers.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Server</th><th>FDW</th><th>Owner</th><th>Type</th><th>Options</th></tr></thead>
              <tbody>
                {servers.map(function(server) {
                  return <tr key={server.srvname}><td className="mono">{server.srvname}</td><td>{server.fdwname}</td><td className="mono">{server.owner}</td><td>{server.srvtype || "-"}</td><td className="mono txt-xs">{(server.options || []).join ? server.options.join(", ") : "-"}</td></tr>;
                })}
                {!loading && servers.length === 0 && <tr><td colSpan="5" style={{textAlign: "center", padding: 24}} className="muted">No foreign servers visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Foreign Tables <span className="meta">{tables.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Schema</th><th>Table</th><th>Server</th><th>FDW</th></tr></thead>
              <tbody>
                {tables.map(function(table) {
                  return <tr key={table.schema_name + "." + table.table_name}><td className="mono">{table.schema_name}</td><td className="mono">{table.table_name}</td><td>{table.server_name}</td><td>{table.fdwname}</td></tr>;
                })}
                {!loading && tables.length === 0 && <tr><td colSpan="4" style={{textAlign: "center", padding: 24}} className="muted">No foreign tables visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">User Mappings <span className="meta">{mappings.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>User</th><th>Server</th><th>FDW</th><th>Options</th></tr></thead>
            <tbody>
              {mappings.map(function(mapping, idx) {
                return <tr key={idx}><td className="mono">{mapping.username}</td><td>{mapping.server_name}</td><td>{mapping.fdwname}</td><td className="mono txt-xs">{(mapping.options || []).join ? mapping.options.join(", ") : "-"}</td></tr>;
              })}
              {!loading && mappings.length === 0 && <tr><td colSpan="4" style={{textAlign: "center", padding: 24}} className="muted">No user mappings visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ReplHistoryScreen({ lastRefresh }) {
  var dataState = React.useState({ patroni_history: [], jobs: [], summary: {} });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    replFetch(clusterPath("/replication/history"), { limit: 75 })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [lastRefresh]);

  var history = data.patroni_history || [];
  var jobs = data.jobs || [];
  var summary = data.summary || {};
  var historyTimeline = history.slice(0, 10).map(function(row) { return { title: "TL " + row[0], sub: replDate(row[3]), tone: "info", label: row[2] || "timeline" }; });
  var jobRows = phaseCountRows(jobs, function(job) { return job.state; });

  return (
    <div className="page">
      <ReplToolbar loading={loading} error={error} source={data.source || "Patroni /history, console jobs"}/>
      <ReplHero title="Switchover History"
                detail="Patroni timeline transitions plus console HA validation jobs."
                tone="blue">
        <span className="pill info">{fmtInt(summary.patroni_events || 0)} Patroni events</span>
        <span className="pill muted">{fmtInt(summary.console_ha_jobs || 0)} console jobs</span>
      </ReplHero>

      <div className="grid-2">
        <div className="card"><div className="bd"><div className="chart-title">Timeline Events</div><TimelineStrip rows={historyTimeline}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="HA Job State" rows={jobRows} center={jobs.length} sub="jobs"/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Patroni Timeline History <span className="meta">{history.length} events</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th className="num">Timeline</th><th>Reason</th><th>Timestamp</th><th>Leader</th><th>LSN</th></tr></thead>
              <tbody>
                {history.map(function(row, idx) {
                  return <tr key={idx}><td className="num">{row[0]}</td><td>{row[2] || "-"}</td><td>{replDate(row[3])}</td><td className="mono">{replMemberShort(row[4])}</td><td className="mono txt-xs">{row[1]}</td></tr>;
                })}
                {!loading && history.length === 0 && <tr><td colSpan="5" style={{textAlign: "center", padding: 24}} className="muted">No Patroni history visible.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Console HA Jobs <span className="meta">{jobs.length} jobs</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Kind</th><th>State</th><th>Target</th><th>Submitted</th><th></th></tr></thead>
              <tbody>
                {jobs.map(function(job) {
                  return <tr key={job.id}><td>{job.kind}</td><td><span className={"pill " + (job.state === "succeeded" ? "ok" : job.state === "pending_approval" ? "warn" : "muted")}>{job.state}</span></td><td className="mono txt-xs">{job.target || "-"}</td><td>{replDate(job.submitted_at)}</td><td><button className="btn ghost sm" onClick={function() { setSelected({ job_record: job }); }}><Icon.Eye size={12}/>Detail</button></td></tr>;
                })}
                {!loading && jobs.length === 0 && <tr><td colSpan="5" style={{textAlign: "center", padding: 24}} className="muted">No console HA jobs found.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <ReplJobDrawer item={selected} onClose={function() { setSelected(null); }}/>
    </div>
  );
}

function ReplicationHAScreen({ view, lastRefresh }) {
  if (view === "sync") return <ReplSyncScreen lastRefresh={lastRefresh}/>;
  if (view === "logical") return <ReplLogicalScreen lastRefresh={lastRefresh}/>;
  if (view === "fdw") return <ReplFdwScreen lastRefresh={lastRefresh}/>;
  if (view === "history") return <ReplHistoryScreen lastRefresh={lastRefresh}/>;
  return <ReplTopologyScreen lastRefresh={lastRefresh}/>;
}
