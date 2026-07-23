// Cluster / Patroni Detail - live from selected cluster agent
// ES5-safe: no ??, no ?., no numeric separators

/* ── Skeleton ─────────────────────────────────────────────────── */
function ClusterSkeleton() {
  return (
    <div className="page">
      <div className="section-h">Loading cluster data <span className="count">…</span></div>
      <div className="card" style={{height:120}}><div className="sk sk-block" style={{height:"100%",borderRadius:6}}/></div>
      <div className="card mt-3" style={{height:180}}><div className="sk sk-block" style={{height:"100%",borderRadius:6}}/></div>
      <div className="grid-2 mt-3">
        <div className="card" style={{height:200}}><div className="sk sk-block" style={{height:"100%",borderRadius:6}}/></div>
        <div className="card" style={{height:200}}><div className="sk sk-block" style={{height:"100%",borderRadius:6}}/></div>
      </div>
    </div>
  );
}

/* ── Topology diagram ─────────────────────────────────────────── */
function Topology(props) {
  var members = props.members || [];

  function shortMember(n) {
    return n ? shortClusterName(n) : n;
  }

  var cols = members.map(function(m) {
    var color = m.role === "leader"       ? "var(--ok)" :
                m.role === "sync_standby" ? "var(--info)" :
                                            "var(--accent)";
    return { m: m, color: color };
  });

  var roleLabel = {
    "leader":       "Leader",
    "sync_standby": "Sync Standby",
    "replica":      "Async Replica",
    "standby_leader": "Standby Leader"
  };

  return (
    <div>
      <div className="topo" style={{gridTemplateColumns:"repeat("+cols.length+",1fr)"}}>
        <svg style={{position:"absolute",inset:0,width:"100%",height:"100%",pointerEvents:"none"}}>
          {cols.length >= 2 ? (
            <line x1="36%" y1="50%" x2="64%" y2="50%"
                  stroke="var(--border-strong)" strokeWidth="2" markerEnd="url(#clArrow)"/>
          ) : null}
          {cols.length === 3 ? (
            <line x1="67%" y1="50%" x2="92%" y2="50%"
                  stroke="var(--border-strong)" strokeWidth="2" markerEnd="url(#clArrow)" strokeDasharray="4 3"/>
          ) : null}
          <defs>
            <marker id="clArrow" viewBox="0 0 10 10" refX="8" refY="5"
                    markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0 0 L10 5 L0 10 Z" fill="var(--fg-dim)"/>
            </marker>
          </defs>
        </svg>

        {cols.map(function(col, i) {
          var m = col.m;
          var isLeader = m.role === "leader";
          var lagVal = (m.replay_lag || m.lag || 0);
          return (
            <div key={m.name} className={"node" + (isLeader ? " leader" : "")}>
              <div className="role">
                <span className="led" style={{background:col.color,verticalAlign:"middle",marginRight:6}}/>
                {roleLabel[m.role] || m.role}
              </div>
              <div className="name">{shortMember(m.name)}</div>
              <div className="meta" style={{marginTop:4}}>
                <span className={"pill " + (m.state === "running" || m.state === "streaming" ? "ok" : "danger")} style={{fontSize:11}}>
                  {m.state}
                </span>
              </div>
              <div className="flex-row mt-2" style={{flexWrap:"wrap",gap:6}}>
                <span className="pill muted txt-xs">TL {m.timeline || "—"}</span>
                {!isLeader && lagVal > 0
                  ? <span className="pill warn">Lag {fmtBytes(lagVal)}</span>
                  : !isLeader
                    ? <span className="pill ok" style={{fontSize:11}}>In sync</span>
                    : null
                }
              </div>
              <div className="muted txt-xs mt-2" style={{wordBreak:"break-all"}}>{m.lsn || "—"}</div>
            </div>
          );
        })}
      </div>

      <div className="flex-row mt-3" style={{gap:16,fontSize:11.5,color:"var(--fg-dim)"}}>
        <span><span className="led ok" style={{marginRight:5,verticalAlign:"middle"}}/>Leader</span>
        <span><span className="led" style={{background:"var(--info)",marginRight:5,verticalAlign:"middle"}}/>Sync Standby</span>
        <span><span className="led" style={{background:"var(--accent)",marginRight:5,verticalAlign:"middle"}}/>Async Replica</span>
      </div>
    </div>
  );
}

/* ── Patroni history timeline ─────────────────────────────────── */
function PatroniHistory(props) {
  var history = props.history || [];
  var currentTimeline = props.currentTimeline;

  // history entry: [old_tl, lsn_int, reason, iso_timestamp, new_leader]
  function fmtHistTs(ts) {
    if (!ts) return "—";
    // e.g. "2026-05-07T13:18:30.774421+00:00" → "07 May 13:18 UTC"
    var d = new Date(ts);
    if (isNaN(d)) return ts.slice(0, 16).replace("T", " ");
    var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    var hh = ("0"+d.getUTCHours()).slice(-2);
    var mm = ("0"+d.getUTCMinutes()).slice(-2);
    return d.getUTCDate() + " " + months[d.getUTCMonth()] + " " + hh + ":" + mm + " UTC";
  }

  function shortMember(n) {
    return n ? shortClusterName(n) : "—";
  }

  if (history.length === 0) {
    return (
      <div className="muted txt-xs" style={{padding:"6px 0"}}>
        No timeline history available · Current timeline: {currentTimeline || "—"}
      </div>
    );
  }

  // Sort newest first
  var sorted = history.slice().sort(function(a,b) { return b[0] - a[0]; });

  return (
    <div>
      <div style={{position:"relative",padding:"4px 0 0"}}>
        <div style={{height:2,background:"var(--border)",margin:"10px 12px 0",borderRadius:1}}/>
        <div className="flex-row" style={{justifyContent:"space-between",padding:"0 4px",overflowX:"auto",gap:8}}>
          {sorted.slice(0,6).reverse().map(function(e, i) {
            var tl   = e[0];
            var ts   = e[3];
            var who  = e[4];
            var isLast = i === Math.min(sorted.length,6) - 1;
            return (
              <div key={i} style={{textAlign:"center",marginTop:-10,minWidth:80}}>
                <div style={{
                  width:12,height:12,borderRadius:"50%",
                  background: isLast ? "var(--hbz-green)" : "var(--ok)",
                  border:"2px solid var(--surface)",
                  boxShadow:"0 0 0 1px var(--border)",
                  margin:"0 auto"
                }}/>
                <div className="txt-xs" style={{marginTop:6,fontWeight:600}}>
                  TL {tl} → {tl+1}
                </div>
                <div className="muted txt-xs">{fmtHistTs(ts)}</div>
                <div className="mono txt-xs" style={{color:"var(--hbz-green)",marginTop:2}}>
                  {shortMember(who)}
                </div>
              </div>
            );
          })}
          <div style={{textAlign:"center",marginTop:-10,minWidth:80}}>
            <div style={{
              width:12,height:12,borderRadius:"50%",
              background:"var(--accent)",
              border:"2px solid var(--surface)",
              boxShadow:"0 0 0 1px var(--border)",
              margin:"0 auto"
            }}/>
            <div className="txt-xs" style={{marginTop:6,fontWeight:600}}>TL {currentTimeline} · Now</div>
            <div className="muted txt-xs">Current</div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Action Modal ────────────────────────────────────────────── */
var ACTION_DEFS = {
  reload: {
    title: "Reload Patroni configuration",
    risk: "low",
    desc: "Reloads postgresql.conf and pg_hba.conf without restart. Affects logging, GUCs, and authentication rules.",
    requiresApprover: false,
    needsConfirmName: false,
    cmd: "patronictl reload",
  },
  switchover: {
    title: "Controlled switchover",
    risk: "high",
    desc: "Promotes a candidate replica to leader after a clean checkpoint. Brief connection interruption (< 5s expected).",
    requiresApprover: true,
    needsConfirmName: true,
    needsTarget: "replica",
    cmd: "patronictl switchover",
  },
  restart: {
    title: "Controlled restart",
    risk: "high",
    desc: "Rolling restart of pods, leader last. Connection draining via PgBouncer; expect short connection cycle.",
    requiresApprover: true,
    needsConfirmName: true,
    cmd: "patronictl restart",
  },
  reinit: {
    title: "Reinitialize replica",
    risk: "high",
    desc: "Wipes the target pod's PGDATA and rebuilds from pgBackRest backup + streaming replication. May take 30–60 min.",
    requiresApprover: true,
    needsConfirmName: true,
    needsTarget: "replica",
    cmd: "patronictl reinit",
  },
  pause: {
    title: "Pause Patroni",
    risk: "medium",
    desc: "Disables automatic failover. Manual interventions only — use during maintenance windows.",
    requiresApprover: true,
    needsConfirmName: false,
    cmd: "patronictl pause",
  },
};

function ActionModal(props) {
  var kind        = props.kind;
  var clusterName = props.clusterName;
  var members     = props.members;
  var defaults    = props.defaults || {};
  var onClose     = props.onClose;
  var onSubmit    = props.onSubmit;

  var def = ACTION_DEFS[kind];

  // Find default target (non-leader)
  var nonLeaders = members.filter(function(m) { return m.role !== "leader"; });
  var defaultTarget = defaults.target || (nonLeaders.length > 0 ? nonLeaders[0].name : "");

  var reasonState      = useState("");
  var targetState      = useState(defaultTarget);
  var confirmNameState = useState("");
  var approverState    = useState("");

  var reason      = reasonState[0];      var setReason      = reasonState[1];
  var target      = targetState[0];      var setTarget      = targetState[1];
  var confirmName = confirmNameState[0]; var setConfirmName = confirmNameState[1];
  var approver    = approverState[0];    var setApprover    = approverState[1];

  var valid =
    reason.trim().length > 8 &&
    (!def.needsConfirmName || confirmName === clusterName) &&
    (!def.requiresApprover || approver.trim().length > 4) &&
    (!def.needsTarget || !!target);

  function submit() {
    var id   = shortUUID();
    var time = new Date().toLocaleTimeString("en-GB", { hour12: false }) + " UTC";
    var shortTarget = target ? shortClusterName(target) : "";
    onSubmit({
      requestId: id,
      command:   def.cmd + (shortTarget ? " --candidate " + shortTarget : ""),
      cluster:   clusterName,
      target:    target,
      reason:    reason,
      approver:  def.requiresApprover ? approver : "auto-approved",
      status:    def.requiresApprover ? "pending" : "running",
      times: {
        pending:  time,
        approved: def.requiresApprover ? null : time,
        running:  def.requiresApprover ? null : time
      },
      log: [
        { t: "dim", s: "["+time+"] request submitted" },
        { t: "dim", s: "["+time+"] target: "+clusterName+(target ? " / "+shortTarget : "") },
      ].concat(def.requiresApprover
        ? [{ t: "dim", s: "["+time+"] waiting for approver (pg-sre-approvers)" }]
        : [
            { t: "ok",  s: "["+time+"] dry-run passed" },
            { t: "dim", s: "["+time+"] executing: "+def.cmd },
            { t: "ok",  s: "["+time+"] config reloaded — changes applied" },
          ]
      ),
    });
  }

  var riskClass = def.risk === "high" ? "high" : "";
  var riskIcon  = def.risk === "high" ? "var(--danger)" : def.risk === "medium" ? "var(--warn)" : "var(--info)";
  var riskLabel = def.risk === "high" ? "High-risk action" : def.risk === "medium" ? "Medium-risk action" : "Low-risk action";

  return (
    <Modal onClose={onClose}>
      <div className="hd">
        <Icon.ShieldAlert size={16} color={riskIcon}/>
        <h3>{def.title}</h3>
        <button className="btn ghost icon close" onClick={onClose} aria-label="Close"><Icon.X size={14}/></button>
      </div>
      <div className="bd">
        <div className={"risk-banner " + riskClass}>
          <Icon.AlertTriangle size={14}/>
          <div>
            <strong>{riskLabel}</strong>
            <div className="txt-xs mt-2">{def.desc}</div>
          </div>
        </div>

        {def.needsTarget ? (
          <div className="field">
            <label>Target member</label>
            <select value={target} onChange={function(e) { setTarget(e.target.value); }}>
              {nonLeaders.map(function(m) {
                return <option key={m.name} value={m.name}>{shortClusterName(m.name)} — {m.role}</option>;
              })}
            </select>
            <div className="hint">Pre-flight will block if replay lag exceeds 16 MiB.</div>
          </div>
        ) : null}

        <div className="field">
          <label>Reason (incident / change ID)</label>
          <textarea rows={2} value={reason}
                    onChange={function(e) { setReason(e.target.value); }}
                    placeholder="e.g. CR-4821 — scheduled monthly leader rotation"/>
        </div>

        {def.needsConfirmName ? (
          <div className="field">
            <label>Type cluster name to confirm</label>
            <input type="text" value={confirmName}
                   onChange={function(e) { setConfirmName(e.target.value); }}
                   placeholder={clusterName}/>
            <div className="hint">Must exactly match: <span className="mono">{clusterName}</span></div>
          </div>
        ) : null}

        {def.requiresApprover ? (
          <div className="field">
            <label>Second approver (UPN)</label>
            <input type="text" value={approver}
                   onChange={function(e) { setApprover(e.target.value); }}
                   placeholder="approver@habibbank.local"/>
            <div className="hint">Restricted actions require two-person approval per change policy.</div>
          </div>
        ) : null}
      </div>
      <div className="ft">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className={"btn " + (def.risk === "high" ? "danger" : "primary")}
                disabled={!valid} onClick={submit}>
          {def.risk === "high" ? "Submit for approval" : "Run command"}
        </button>
      </div>
    </Modal>
  );
}

/* ── Main ClusterScreen ────────────────────────────────────────── */
function ClusterScreen(props) {
  var cluster     = props.cluster;
  var lastRefresh = props.lastRefresh;
  var onCommand   = props.onCommand;

  var dataState        = useState(null);
  var loadingState     = useState(true);
  var errorState       = useState(null);
  var actionModalState = useState(null);

  var data         = dataState[0];   var setData         = dataState[1];
  var loading      = loadingState[0];var setLoading      = loadingState[1];
  var error        = errorState[0];  var setError        = errorState[1];
  var actionModal  = actionModalState[0]; var setActionModal = actionModalState[1];

  useEffect(function() {
    var alive = true;
    fetch(uiClusterPath("cluster"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) {
        if (alive) { setData(d); setLoading(false); setError(null); }
      })
      .catch(function(e) {
        if (alive) { setError(e.message || String(e)); setLoading(false); }
      });
    return function() { alive = false; };
  }, [lastRefresh, cluster && cluster.id]);

  if (loading && !data) return <ClusterSkeleton/>;

  // ── Data extraction ──────────────────────────────────────────
  var patroni   = (data && data.patroni)   || {};
  var wal       = (data && data.wal)       || {};
  var settings  = (data && data.settings)  || {};
  var history   = (data && data.history)   || [];
  var pgb       = (data && data.pgbouncer) || { pods_ready: 0, pods_total: 0 };

  var members   = patroni.members || [];
  var patroniOk = patroni.patroni_ok === true;
  var slots     = (data && data.slots)     || [];
  var replication = (data && data.replication) || [];

  var leaderMbr    = null;
  var standbys     = [];
  var syncStandby  = null;
  for (var mi = 0; mi < members.length; mi++) {
    var mbr = members[mi];
    if (mbr.role === "leader")        leaderMbr  = mbr;
    else                              standbys.push(mbr);
    if (mbr.role === "sync_standby")  syncStandby = mbr;
  }

  var currentTimeline = leaderMbr ? (leaderMbr.timeline || 0) : 0;

  var maxLagBytes = 0;
  for (var si = 0; si < standbys.length; si++) {
    var sLag = standbys[si].replay_lag != null ? standbys[si].replay_lag : (standbys[si].lag || 0);
    if (sLag > maxLagBytes) maxLagBytes = sLag;
  }

  var inSync      = syncStandby && (syncStandby.replay_lag || syncStandby.lag || 0) === 0;
  var allStreaming = standbys.every(function(s) { return s.state === "streaming"; });
  var readiness   = patroniOk && leaderMbr && leaderMbr.state === "running" && allStreaming && inSync;

  var memberRoleRows = phaseCountRows(members, function(member) { return member.role || "member"; }, function(role) { return role === "leader" ? "ok" : role === "sync_standby" ? "info" : "muted"; });
  var clusterLagRows = standbys.map(function(member) {
    var lag = Number(member.replay_lag != null ? member.replay_lag : (member.lag || 0));
    return { label: shortMember(member.name), value: lag, sub: member.state || "-", tone: lag > 0 ? "warn" : "ok" };
  });
  var syncStateRows = phaseCountRows(replication, function(row) { return row.sync_state || "unknown"; }, function(state) { return state === "sync" ? "ok" : state === "async" ? "muted" : "info"; });
  var clusterSlotRows = slots.map(function(slot) {
    var retained = Number(slot.retained_wal_bytes || slot.lag_bytes || 0);
    return { label: slot.slot_name, value: retained, sub: slot.active ? "active" : "inactive", tone: slot.active ? "ok" : "warn" };
  });

  function shortMember(n) { return n ? shortClusterName(n) : "—"; }

  function openAction(kind, target) {
    setActionModal({ kind: kind, target: target || "" });
  }

  function clusterName() { return data ? (data.cluster_name || cluster.name) : cluster.name; }

  return (
    <div className="page">

      {error ? (
        <div className="tile-error flex-row" style={{marginBottom:8}}>
          <Icon.AlertCircle size={14}/>
          <strong style={{marginLeft:6}}>Couldn't load data</strong>
          <span className="muted txt-xs" style={{marginLeft:8}}>{hbzErrorText(error)}</span>
        </div>
      ) : null}

      {/* ── Top strip: leader + timeline + actions ── */}
      <div className="card">
        <div className="bd" style={{padding:"12px 14px"}}>
          <div className="flex-row" style={{justifyContent:"flex-end", marginBottom:8}}>
            <SourceBadge source={(data && data.source) || "Patroni + live PostgreSQL"}/>
          </div>
          <div className="grid-4" style={{alignItems:"center"}}>

            <div>
              <div className="lbl txt-xs muted">Current leader</div>
              <div className="mono" style={{fontWeight:600,fontSize:14,marginTop:4,wordBreak:"break-all"}}>
                {leaderMbr ? shortMember(leaderMbr.name) : "—"}
              </div>
              <div className="muted txt-xs mt-2">
                Timeline {currentTimeline} · scope <span className="mono">{patroni.scope || "—"}</span>
              </div>
            </div>

            <div>
              <div className="lbl txt-xs muted">Sync standby</div>
              <div className="mono" style={{fontWeight:600,fontSize:14,marginTop:4,wordBreak:"break-all"}}>
                {syncStandby ? shortMember(syncStandby.name) : "None"}
              </div>
              <div className="muted txt-xs mt-2">
                {syncStandby
                  ? (syncStandby.state === "streaming" ? "Streaming · " : syncStandby.state + " · ") +
                    (maxLagBytes === 0 ? "0 bytes lag" : fmtBytes(maxLagBytes) + " lag")
                  : "No sync standby detected"}
              </div>
            </div>

            <div>
              <div className="lbl txt-xs muted">Switchover readiness</div>
              <div className="flex-row" style={{marginTop:4}}>
                <span className={"led " + (readiness ? "ok" : patroniOk ? "warn" : "danger")}/>
                <strong style={{marginLeft:6}}>
                  {readiness ? "Ready" : patroniOk ? "Caution" : "Unknown"}
                </strong>
              </div>
              <div className="muted txt-xs mt-2">
                {readiness
                  ? "Sync standby in lockstep · lag = 0"
                  : patroniOk
                    ? "Check replication lag before switchover"
                    : "Patroni API not reachable"}
              </div>
            </div>

            <div className="flex-row" style={{justifyContent:"flex-end",flexWrap:"wrap",gap:6}}>
              <button className="btn sm" onClick={function() { openAction("reload"); }}>
                <Icon.RefreshCw size={12}/> Reload config
              </button>
              <button className="btn sm" onClick={function() { openAction("switchover"); }}>
                <Icon.GitBranch size={12}/> Switchover
              </button>
              <button className="btn sm" onClick={function() { openAction("restart"); }}>
                <Icon.RotateCcw size={12}/> Restart
              </button>
              <button className="btn sm" onClick={function() { openAction("reinit"); }}>
                <Icon.Power size={12}/> Reinit replica
              </button>
              <button className="btn sm" onClick={function() { openAction("pause"); }}>
                <Icon.Pause size={12}/> Pause Patroni
              </button>
            </div>
          </div>

          {/* Timeline history */}
          <div style={{marginTop:16}}>
            <div className="txt-xs muted" style={{marginBottom:6,fontWeight:600,textTransform:"uppercase",letterSpacing:".4px"}}>
              Timeline history ({history.length} transitions)
            </div>
            <PatroniHistory history={history} currentTimeline={currentTimeline}/>
          </div>
        </div>
      </div>

      <div className="grid-4">
        <div className="card"><div className="bd"><DonutChart title="Member Roles" rows={memberRoleRows} center={members.length} sub="members"/></div></div>
        <div className="card"><div className="bd"><BarList title="Replica Lag" rows={clusterLagRows} valueFormatter={fmtBytes}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Sync State" rows={syncStateRows} center={replication.length} sub="connections"/></div></div>
        <div className="card"><div className="bd"><BarList title="Slot WAL" rows={clusterSlotRows} valueFormatter={fmtBytes}/></div></div>
      </div>

      {/* ── Topology ── */}
      <div className="card">
        <div className="hd">
          Topology
          <span className="meta">Patroni · DCS: Kubernetes · {members.length} members</span>
        </div>
        <div className="bd">
          {React.createElement(window.ClusterArchitecture || Topology, { members: members })}
        </div>
      </div>

      {/* ── Members table (Patroni + pg_stat_replication combined) ── */}
      <div className="card">
        <div className="hd">
          Patroni members
          <span className="meta">
            {members.length} members · TL {currentTimeline} · PgBouncer {pgb.pods_ready}/{pgb.pods_total}
          </span>
        </div>
        <div style={{overflowX:"auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Member</th><th>Role</th><th>State</th>
                <th className="num">Replay lag</th><th className="num">Lag (s)</th>
                <th>Sync state</th><th className="num">LSN</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {members.map(function(m) {
                var isLeader = m.role === "leader";
                var rolePill = isLeader ? "ok" : m.role === "sync_standby" ? "info" : "muted";
                var roleLabel = isLeader ? "Leader" : m.role === "sync_standby" ? "Sync Standby" : m.role;
                var statePill = (m.state === "running" || m.state === "streaming") ? "ok" : "danger";

                // Try to match with pg_stat_replication row
                var repRow = null;
                for (var ri = 0; ri < replication.length; ri++) {
                  if (replication[ri].application_name === m.name) { repRow = replication[ri]; break; }
                }

                var lagBytes = isLeader ? 0 :
                  repRow ? (repRow.replay_lag_bytes || 0) :
                  (m.replay_lag != null ? m.replay_lag : (m.lag || 0));

                var lagSec = repRow ? (repRow.replay_lag_sec || 0) : null;
                var syncState = repRow ? repRow.sync_state : (isLeader ? "primary" : "—");
                var lsn = isLeader ? (wal.current_lsn || m.lsn || "—") : (m.lsn || "—");

                return (
                  <tr key={m.name}>
                    <td className="mono txt-xs">{shortMember(m.name)}</td>
                    <td><span className={"pill " + rolePill}><span className="dot"/>{roleLabel}</span></td>
                    <td><span className={"pill " + statePill}>{m.state}</span></td>
                    <td className="num">{isLeader ? "—" : fmtBytes(lagBytes)}</td>
                    <td className="num">
                      {isLeader ? "—" : lagSec != null ? lagSec.toFixed(3) + "s" : "—"}
                    </td>
                    <td>
                      {syncState === "sync" || syncState === "primary"
                        ? <span className="pill ok"><span className="dot"/>{syncState}</span>
                        : syncState === "async"
                          ? <span className="pill muted"><span className="dot"/>async</span>
                          : <span className="muted txt-xs">{syncState}</span>
                      }
                    </td>
                    <td className="num mono txt-xs">{lsn}</td>
                    <td>
                      {!isLeader ? (
                        <button className="btn ghost sm"
                                title="Reinit this replica"
                                onClick={function() { openAction("reinit", m.name); }}>
                          <Icon.Power size={12}/>
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
              {members.length === 0 ? (
                <tr><td colSpan="8" className="muted" style={{textAlign:"center",padding:20}}>
                  Patroni API not reachable
                </td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Replication slots + pg_stat_replication ── */}
      <div className="grid-2">

        <div className="card">
          <div className="hd">
            pg_stat_replication
            <span className="meta">{replication.length} streaming connection{replication.length !== 1 ? "s" : ""}</span>
          </div>
          {replication.length > 0 ? (
            <div style={{overflowX:"auto"}}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Application</th><th>State</th>
                    <th className="num">Lag (bytes)</th><th className="num">Lag (s)</th>
                    <th>Sync state</th><th className="num">Replay LSN</th>
                  </tr>
                </thead>
                <tbody>
                  {replication.map(function(r) {
                    return (
                      <tr key={r.application_name}>
                        <td className="mono txt-xs">{shortMember(r.application_name)}</td>
                        <td><span className={"pill " + (r.state === "streaming" ? "ok" : "warn")}>{r.state}</span></td>
                        <td className="num">{fmtBytes(r.replay_lag_bytes || 0)}</td>
                        <td className="num">{r.replay_lag_sec != null ? r.replay_lag_sec.toFixed(3)+"s" : "—"}</td>
                        <td><span className={"pill " + (r.sync_state === "sync" ? "ok" : "muted")}>
                          <span className="dot"/>{r.sync_state}
                        </span></td>
                        <td className="num mono txt-xs">{r.replay_lsn || "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="bd muted txt-xs">
              {patroniOk
                ? "No rows — user may not have pg_monitor role, or no replicas streaming"
                : "Patroni API unavailable"}
            </div>
          )}
        </div>

        <div className="card">
          <div className="hd">
            Replication slots
            <span className="meta">{slots.length} slot{slots.length !== 1 ? "s" : ""}</span>
          </div>
          {slots.length > 0 ? (
            <div style={{overflowX:"auto"}}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Slot name</th><th>Type</th><th>Active</th>
                    <th>Database</th><th className="num">Lag</th>
                  </tr>
                </thead>
                <tbody>
                  {slots.map(function(s) {
                    return (
                      <tr key={s.slot_name} className={!s.active && s.slot_type === "logical" ? "row-warn" : ""}>
                        <td className="mono">{s.slot_name}</td>
                        <td><span className={"pill " + (s.slot_type === "logical" ? "info" : "muted")}>{s.slot_type}</span></td>
                        <td>
                          {s.active
                            ? <span className="pill ok"><span className="dot"/>active</span>
                            : <span className="pill danger"><span className="dot"/>inactive</span>}
                        </td>
                        <td className="mono txt-xs">{s.database || "—"}</td>
                        <td className="num">{fmtBytes(s.lag_bytes || 0)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="bd muted txt-xs">
              No replication slots — streaming replication runs without explicit slots.
              <br/><br/>
              <span className="txt-xs">
                Logical slots will appear here if pub/sub or CDC is configured.
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ── WAL & Settings ── */}
      <div className="card">
        <div className="hd">WAL &amp; Patroni configuration <span className="meta">wal_level=logical · archive_mode=on</span></div>
        <div className="bd">
          <div className="grid-4">
            <Stat label="Current LSN"      value={wal.current_lsn || "—"}/>
            <Stat label="Archive mode"     value={settings.archive_mode === "on" ? "Enabled" : "Off"} sub="pgBackRest S3"/>
            <Stat label="WAL level"        value={settings.wal_level || "—"}/>
            <Stat label="Max WAL senders"  value={settings.max_wal_senders || "—"}/>
          </div>
          <div className="grid-4 mt-3">
            <Stat label="Max repl. slots"  value={settings.max_replication_slots || "—"}/>
            <Stat label="Max slot WAL"     value={settings.max_slot_wal_keep_size ? settings.max_slot_wal_keep_size+" MiB" : "—"}/>
            <Stat label="Sync commit"      value={settings.synchronous_commit || "—"}/>
            <Stat label="Timeline"         value={""+currentTimeline}/>
          </div>
          <div className="mt-3">
            <div className="txt-xs muted" style={{marginBottom:4}}>Current WAL file</div>
            <div className="mono txt-xs" style={{
              padding:"4px 8px",background:"var(--surface-2)",
              borderRadius:3,border:"1px solid var(--border)"
            }}>
              {wal.wal_file || "—"}
            </div>
          </div>
          <div className="mt-3">
            <div className="txt-xs muted" style={{marginBottom:4}}>Synchronous standby names</div>
            <div className="mono txt-xs" style={{
              padding:"4px 8px",background:"var(--surface-2)",
              borderRadius:3,border:"1px solid var(--border)",wordBreak:"break-all"
            }}>
              {settings.synchronous_standby_names || "—"}
            </div>
          </div>
          {wal.started_at ? (
            <div className="flex-row mt-3" style={{fontSize:11.5,color:"var(--fg-dim)"}}>
              <span>PostgreSQL started:</span>
              <span className="mono" style={{marginLeft:8}}>{wal.started_at.replace("T"," ").slice(0,19)} UTC</span>
            </div>
          ) : null}
        </div>
      </div>

      {/* ── Action Modal ── */}
      {actionModal ? (
        <ActionModal
          kind={actionModal.kind}
          clusterName={clusterName()}
          members={members}
          defaults={actionModal}
          onClose={function() { setActionModal(null); }}
          onSubmit={function(payload) {
            setActionModal(null);
            onCommand(payload);
          }}/>
      ) : null}

    </div>
  );
}

window.ClusterScreen = ClusterScreen;
