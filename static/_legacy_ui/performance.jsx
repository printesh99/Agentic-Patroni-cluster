// Performance / Sessions screen

function PerformanceScreen({ cluster, onCommand }) {
  const c = cluster;
  const [tab, setTab] = useState("sessions");

  return (
    <div className="page">
      <div className="tabs" style={{borderRadius: "var(--r-md)", border:"1px solid var(--border)"}}>
        <button className={tab === "sessions" ? "active" : ""}  onClick={() => setTab("sessions")}><Icon.Users size={13} style={{verticalAlign:"-2px", marginRight: 6}}/>Sessions</button>
        <button className={tab === "locks" ? "active" : ""}     onClick={() => setTab("locks")}><Icon.Lock size={13} style={{verticalAlign:"-2px", marginRight: 6}}/>Lock tree</button>
        <button className={tab === "top" ? "active" : ""}       onClick={() => setTab("top")}><Icon.TrendingUp size={13} style={{verticalAlign:"-2px", marginRight: 6}}/>Top SQL</button>
      </div>

      {tab === "sessions" && <SessionsTab cluster={c} onCommand={onCommand}/>}
      {tab === "locks"    && <LocksTab    cluster={c}/>}
      {tab === "top"      && <TopSQLTab   cluster={c}/>}
    </div>
  );
}

/* =================== Sessions =================== */
// Map the live /perf/sessions API row -> the shape this grid renders.
function mapSession(r) {
  return {
    pid: r.pid, user: r.user, db: r.database, app: r.application, addr: r.client_addr,
    state: r.state, wet: r.wait_event_type, we: r.wait_event,
    qstart: r.query_age_sec == null ? 0 : r.query_age_sec,
    xstart: r.xact_age_sec, xmin: r.backend_xmin, q: r.query,
    queryId: r.query_id, backendType: r.backend_type,
  };
}

function SessionsTab({ cluster, onCommand }) {
  const [all, setAll] = useState([]);
  const [loading, setLoading] = useState(true);
  const [insight, setInsight] = useState(null); // { loading, payload, error, pid }
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState({});
  const [confirm, setConfirm] = useState(null); // { kind, pid, user, db, q }

  // Live pg_stat_activity across ALL databases (was a static fixture).
  React.useEffect(function() {
    let alive = true;
    setLoading(true);
    perfJson("/api/v1/clusters/" + cluster.id + "/perf/sessions", { limit: 500 })
      .then(function(res) {
        if (!alive) return;
        setAll((res && res.sessions ? res.sessions : []).map(mapSession));
        setLoading(false);
      })
      .catch(function() { if (alive) { setAll([]); setLoading(false); } });
    return function() { alive = false; };
  }, [cluster.id]);

  // Oracle-style SQL drill-down: plan + stats + indexes + locks for one backend.
  function openInsight(s) {
    setInsight({ loading: true, pid: s.pid });
    perfJson("/api/v1/clusters/" + cluster.id + "/perf/session/" + s.pid + "/insight")
      .then(function(p) { setInsight({ loading: false, payload: p, pid: s.pid }); })
      .catch(function(e) { setInsight({ loading: false, error: String(e), pid: s.pid }); });
  }

  const filtered = all.filter(s => {
    if (filter !== "all" && s.state !== filter) return false;
    if (search) {
      const t = (s.user + " " + s.db + " " + s.app + " " + s.q).toLowerCase();
      if (!t.includes(search.toLowerCase())) return false;
    }
    return true;
  });

  const counts = all.reduce((m, s) => { m[s.state] = (m[s.state] || 0) + 1; return m; }, {});
  const longs = all.filter(s => s.state === "active" && s.qstart > 30).length;

  return (
    <>
      <div className="card">
        <div className="bd" style={{display: "flex", gap: 12, flexWrap: "wrap", alignItems:"center"}}>
          <div className="flex-row">
            <span className="txt-xs muted">Filter:</span>
            {["all", "active", "idle", "idle in transaction"].map(k => (
              <button key={k}
                      className={"btn sm " + (filter === k ? "primary" : "")}
                      onClick={() => setFilter(k)}>
                {k} <span className="muted">{k === "all" ? all.length : (counts[k] || 0)}</span>
              </button>
            ))}
          </div>
          <div className="flex-row" style={{position: "relative"}}>
            <Icon.Search size={12} color="var(--fg-dim)" className="" />
            <input style={{
              padding: "5px 10px", border:"1px solid var(--border-strong)",
              borderRadius: "var(--r)", fontSize: 12.5, width: 220
            }} placeholder="Search user, db, app, query…" value={search} onChange={e => setSearch(e.target.value)}/>
          </div>
          <div className="grow"/>
          <div className="flex-row txt-xs muted">
            <span className="led warn"/> Active &gt; 30s
            <span className="led danger" style={{marginLeft: 8}}/> Active &gt; 5m
            <span className="muted">{longs} long-running</span>
          </div>
          <button className="btn sm"><Icon.Download size={12}/> Export CSV</button>
        </div>
      </div>

      <div className="card">
        <div className="hd">pg_stat_activity <span className="meta">{filtered.length} of {all.length} sessions</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th className="num">pid</th><th>user</th><th>db</th><th>app</th><th>client_addr</th>
                <th>state</th><th>wait</th>
                <th className="num">query_start</th><th className="num">xact_start</th><th>backend_xmin</th>
                <th>query</th><th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(s => {
                const isLong5 = s.state === "active" && s.qstart > 300;
                const isLong30 = s.state === "active" && s.qstart > 30 && !isLong5;
                const cls = isLong5 ? "row-danger" : isLong30 ? "row-warn" : "";
                const ex = expanded[s.pid];
                return (
                  <React.Fragment key={s.pid}>
                    <tr className={cls}>
                      <td className="num">{s.pid}</td>
                      <td className="mono">{s.user}</td>
                      <td className="mono">{s.db}</td>
                      <td className="mono txt-xs">{s.app}</td>
                      <td className="mono txt-xs">{s.addr}</td>
                      <td>
                        <span className={"pill " + (s.state === "active" ? "ok" : s.state === "idle in transaction" ? "warn" : "muted")}>
                          <span className="dot"/>{s.state}
                        </span>
                      </td>
                      <td className="txt-xs">
                        {s.we ? <><span className="muted">{s.wet}:</span> {s.we}</> : "—"}
                      </td>
                      <td className="num">{fmtSec(s.qstart)}</td>
                      <td className="num">{s.xstart != null ? fmtSec(s.xstart) : "—"}</td>
                      <td className="mono txt-xs">{s.xmin || "—"}</td>
                      <td style={{maxWidth: 320}}>
                        <button className="btn ghost sm" onClick={() => setExpanded({ ...expanded, [s.pid]: !ex })}>
                          {ex ? <Icon.ChevronDown size={12}/> : <Icon.ChevronRight size={12}/>}
                        </button>
                        <span className="mono txt-xs" style={{overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", display:"inline-block", maxWidth: 280, verticalAlign: "middle"}}>
                          {s.q}
                        </span>
                      </td>
                      <td className="nowrap">
                        <button className="btn ghost sm" title="SQL Insight — plan, stats, indexes, locks"
                                onClick={() => openInsight(s)}>
                          <Icon.FileText size={12}/>
                        </button>
                        <button className="btn ghost sm" title="Cancel backend"
                                onClick={() => setConfirm({ kind: "cancel", ...s })}>
                          <Icon.StopCircle size={12}/>
                        </button>
                        <button className="btn ghost sm" title="Terminate backend" style={{color: "var(--danger)"}}
                                onClick={() => setConfirm({ kind: "terminate", ...s })}>
                          <Icon.XCircle size={12}/>
                        </button>
                      </td>
                    </tr>
                    {ex && (
                      <tr>
                        <td colSpan="12" style={{background: "#FAFAFA", padding: "12px 14px 14px"}}>
                          <div className="grid-4">
                            <Stat label="Backend type" value="client backend"/>
                            <Stat label="Application" value={s.app}/>
                            <Stat label="Client" value={s.addr}/>
                            <Stat label="Wait" value={s.we ? `${s.wet} / ${s.we}` : "—"}/>
                          </div>
                          <div className="mt-3">
                            <div className="txt-xs muted">Query text</div>
                            <pre style={{
                              margin: 0, padding: 10, background: "#0E1116", color: "#C8D1DC",
                              borderRadius: 4, fontFamily: "var(--font-mono)", fontSize: 11.5,
                              overflowX: "auto", whiteSpace: "pre-wrap"
                            }}>{s.q}</pre>
                          </div>
                          <div className="flex-row mt-3">
                            <button className="btn sm" onClick={() => setConfirm({ kind: "cancel", ...s })}>
                              <Icon.StopCircle size={12}/> pg_cancel_backend({s.pid})
                            </button>
                            <button className="btn sm danger" onClick={() => setConfirm({ kind: "terminate", ...s })}>
                              <Icon.XCircle size={12}/> pg_terminate_backend({s.pid})
                            </button>
                            <button className="btn ghost sm" onClick={() => openInsight(s)}><Icon.FileText size={12}/> SQL Insight</button>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {filtered.length === 0 && (
                <tr><td colSpan="12" style={{textAlign:"center", padding: 24}} className="muted">{loading ? "Loading live sessions…" : "No sessions match the current filter."}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {confirm && (
        <BackendActionModal s={confirm} cluster={cluster}
                            onClose={() => setConfirm(null)}
                            onSubmit={(p) => { setConfirm(null); onCommand(p); }}/>
      )}

      {insight && (
        <PerfPlanDrawer title={"SQL Insight · pid " + insight.pid}
                        payload={insight.payload}
                        fallback={{ queryid: insight.payload && insight.payload.queryid }}
                        loading={insight.loading} error={insight.error}
                        onClose={() => setInsight(null)}/>
      )}
    </>
  );
}

function BackendActionModal({ s, cluster, onClose, onSubmit }) {
  const isTerm = s.kind === "terminate";
  const [reason, setReason] = useState("");
  const valid = reason.trim().length > 8;
  const submit = () => {
    const id = shortUUID();
    const time = new Date().toLocaleTimeString("en-GB", { hour12: false }) + " GST";
    onSubmit({
      requestId: id,
      command: (isTerm ? "pg_terminate_backend" : "pg_cancel_backend") + "(" + s.pid + ")",
      cluster: cluster.name, target: `pid ${s.pid} · ${s.user}@${s.db}`,
      reason, status: "succeeded",
      approver: "auto-approved",
      times: { pending: time, approved: time, running: time, done: time },
      log: [
        { t: "dim", s: `[${time}] target: pid ${s.pid} user=${s.user} db=${s.db}` },
        { t: "dim", s: `[${time}] executing on leader ${cluster.leader}` },
        { t: "ok",  s: `[${time}] ${isTerm ? "pg_terminate_backend" : "pg_cancel_backend"} returned t` },
        { t: "ok",  s: `[${time}] backend ${isTerm ? "terminated" : "cancelled"}` },
      ],
    });
  };
  return (
    <Modal onClose={onClose}>
      <div className="hd">
        <Icon.ShieldAlert size={16} color={isTerm ? "var(--danger)" : "var(--warn)"}/>
        <h3>{isTerm ? "Terminate backend" : "Cancel running query"}</h3>
        <button className="btn ghost icon close" onClick={onClose}><Icon.X size={14}/></button>
      </div>
      <div className="bd">
        <div className={"risk-banner" + (isTerm ? " high" : "")}>
          <Icon.AlertTriangle size={14}/>
          <div>
            <strong>{isTerm ? "Terminates the connection" : "Cancels the current query"}</strong>
            <div className="txt-xs mt-2">
              {isTerm
                ? "Forcibly drops the backend connection. Any in-flight transaction is rolled back."
                : "Sends SIGINT to the backend. The connection stays open; the current query stops."}
            </div>
          </div>
        </div>
        <div className="grid-2 mt-3">
          <Stat label="pid" value={s.pid}/>
          <Stat label="User / DB" value={`${s.user} / ${s.db}`}/>
          <Stat label="App" value={s.app}/>
          <Stat label="Running for" value={fmtSec(s.qstart)}/>
        </div>
        <div className="field">
          <label>Reason (incident / ticket)</label>
          <textarea rows={2} value={reason} onChange={e => setReason(e.target.value)}
                    placeholder="e.g. INC-1284 — runaway query holding ledger lock"/>
        </div>
      </div>
      <div className="ft">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className={"btn " + (isTerm ? "danger" : "primary")} disabled={!valid} onClick={submit}>
          {isTerm ? "Terminate backend" : "Cancel query"}
        </button>
      </div>
    </Modal>
  );
}

/* =================== Locks =================== */
function LocksTab({ cluster }) {
  const [tree, setTree] = useState([]);
  const [loading, setLoading] = useState(true);

  // Live blocker->blocked tree from pg_blocking_pids (was an always-empty fixture).
  React.useEffect(function() {
    let alive = true;
    setLoading(true);
    perfJson("/api/v1/clusters/" + cluster.id + "/perf/locks")
      .then(function(res) {
        if (!alive) return;
        setTree(res && res.tree ? res.tree : []);
        setLoading(false);
      })
      .catch(function() { if (alive) { setTree([]); setLoading(false); } });
    return function() { alive = false; };
  }, [cluster.id]);

  return (
    <div className="card">
      <div className="hd">Lock tree
        <span className="meta">blocker → blocked · pg_locks + pg_stat_activity</span>
      </div>
      <div className="bd">
        {loading ? (
          <div className="flex-row" style={{padding: 24, justifyContent:"center"}}>
            <Icon.Loader size={16}/><span className="muted">Loading lock tree…</span>
          </div>
        ) : tree.length === 0 ? (
          <div className="flex-row" style={{padding: 24, justifyContent:"center"}}>
            <Icon.CheckCircle size={16} color="var(--ok)"/>
            <span className="muted">No blocking locks detected.</span>
          </div>
        ) : tree.map(node => (
          <div key={node.id} className="card mt-2" style={{boxShadow:"none"}}>
            <div className="bd">
              <div className="flex-row" style={{justifyContent:"space-between"}}>
                <div>
                  <div className="lbl txt-xs muted">Blocker</div>
                  <div className="flex-row mt-2">
                    <span className="pill warn"><span className="dot"/>holds lock</span>
                    <span className="mono"><strong>pid {node.blocker.pid}</strong> · {node.blocker.user}@{node.blocker.db}</span>
                  </div>
                  <div className="txt-xs mt-2">
                    <span className="muted">relation</span> <span className="mono">{node.blocker.relation}</span>
                    <span className="muted" style={{marginLeft: 12}}>mode</span> <span className="mono">{node.blocker.mode}</span>
                    <span className="muted" style={{marginLeft: 12}}>held for</span> {fmtSec(node.blocker.waitSec)}
                  </div>
                </div>
                <div className="flex-row">
                  <button className="btn sm"><Icon.StopCircle size={12}/> Cancel</button>
                  <button className="btn sm danger"><Icon.XCircle size={12}/> Terminate</button>
                </div>
              </div>

              <div style={{marginLeft: 22, marginTop: 12, borderLeft: "2px solid var(--border-strong)", paddingLeft: 12}}>
                <div className="lbl txt-xs muted">Blocked ({node.blocked.length})</div>
                {node.blocked.map(b => (
                  <div key={b.pid} className="flex-row mt-2" style={{justifyContent:"space-between"}}>
                    <div className="flex-row">
                      <Icon.ArrowRight size={12} color="var(--fg-dim)"/>
                      <span className="mono"><strong>pid {b.pid}</strong> · {b.user}@{b.db}</span>
                      <span className="pill danger"><span className="dot"/>waiting {fmtSec(b.waitSec)}</span>
                      <span className="txt-xs muted">on <span className="mono">{b.relation}</span> ({b.mode})</span>
                    </div>
                    <button className="btn ghost sm"><Icon.ExternalLink size={12}/></button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* =================== Top SQL =================== */
// Map the live /perf/topsql API row -> the shape this grid renders.
function mapTopSql(r) {
  return {
    id: r.queryid, db: r.database || "-", q: r.query,
    calls: r.calls || 0, total: (r.total_exec_ms || 0) / 1000,  // ms -> "total (s)"
    mean: r.mean_exec_ms || 0, p95: r.p95_ms == null ? 0 : r.p95_ms,
    rows: r.rows || 0, hit: r.cache_hit_pct == null ? 0 : r.cache_hit_pct,
  };
}

function TopSQLTab({ cluster }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState("total");
  const [dbFilter, setDbFilter] = useState("all");

  // Live pg_stat_statements across ALL databases (was a static fixture that
  // only showed the console's own metadata queries).
  React.useEffect(function() {
    let alive = true;
    setLoading(true);
    perfJson("/api/v1/clusters/" + cluster.id + "/perf/topsql", { sort: sortKey, limit: 100 })
      .then(function(res) {
        if (!alive) return;
        setRows((res && res.top_sql ? res.top_sql : []).map(mapTopSql));
        setLoading(false);
      })
      .catch(function() { if (alive) { setRows([]); setLoading(false); } });
    return function() { alive = false; };
  }, [cluster.id, sortKey]);

  const dbs = Array.from(new Set(rows.map(r => r.db))).sort();
  const visible = dbFilter === "all" ? rows : rows.filter(r => r.db === dbFilter);
  const sorted = [...visible].sort((a, b) => b[sortKey] - a[sortKey]);
  const max = Math.max(1, ...visible.map(r => r[sortKey]));

  return (
    <>
      <div className="card">
        <div className="bd" style={{display:"flex", gap: 12, alignItems:"center", flexWrap:"wrap"}}>
          <span className="txt-xs muted">Order by:</span>
          {[
            { k: "total", l: "Total time" },
            { k: "mean",  l: "Mean time" },
            { k: "calls", l: "Calls" },
            { k: "rows",  l: "Rows" },
            { k: "p95",   l: "p95" },
          ].map(o => (
            <button key={o.k}
                    className={"btn sm " + (sortKey === o.k ? "primary" : "")}
                    onClick={() => setSortKey(o.k)}>{o.l}</button>
          ))}
          <span className="txt-xs muted" style={{marginLeft: 12}}>Database:</span>
          <select value={dbFilter} onChange={e => setDbFilter(e.target.value)}
                  style={{padding:"4px 8px", borderRadius:"var(--r)", border:"1px solid var(--border)", fontSize:12.5}}>
            <option value="all">All databases ({rows.length})</option>
            {dbs.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
          <div className="grow"/>
          <span className="txt-xs muted">Source: pg_stat_statements · normalized · p95 est.</span>
        </div>
      </div>

      <div className="card">
        <div className="hd">Top SQL <span className="meta">{loading ? "loading…" : visible.length + " statements"}</span></div>
        <div style={{overflowX:"auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>queryid</th><th>db</th><th>normalized query</th>
                <th className="num">calls</th><th className="num">total (s)</th>
                <th className="num">mean (ms)</th><th className="num">p95 (ms)</th>
                <th className="num">rows</th><th className="num">cache hit</th>
                <th>trend</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(r => (
                <tr key={r.id + "·" + r.db}>
                  <td className="mono txt-xs">{r.id}</td>
                  <td className="mono txt-xs">{r.db}</td>
                  <td>
                    <div className="mono txt-xs" style={{
                      maxWidth: 460, overflow:"hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"
                    }}>{r.q}</div>
                    <div style={{height: 3, background: "var(--surface-3)", borderRadius: 2, marginTop: 4, width: 240}}>
                      <div style={{
                        height: "100%", width: ((r[sortKey] / max) * 100) + "%",
                        background: "var(--accent)", borderRadius: 2
                      }}/>
                    </div>
                  </td>
                  <td className="num">{fmtInt(r.calls)}</td>
                  <td className="num">{r.total.toFixed(1)}</td>
                  <td className="num">{r.mean < 1 ? r.mean.toFixed(3) : r.mean.toFixed(2)}</td>
                  <td className="num">{r.p95.toFixed(2)}</td>
                  <td className="num">{fmtInt(r.rows)}</td>
                  <td className="num">
                    <span className={"pill " + (r.hit > 95 ? "ok" : r.hit > 85 ? "warn" : "danger")}>
                      {r.hit.toFixed(1)}%
                    </span>
                  </td>
                  <td>
                    <Sparkline data={Array.from({length: 24}, (_, i) => Math.max(0, r.mean * (1 + 0.4 * Math.sin(i + (r.id ? String(r.id).length : 3)))))}
                               width={120} height={28}/>
                  </td>
                </tr>
              ))}
              {sorted.length === 0 && (
                <tr><td colSpan="10" style={{textAlign:"center", padding: 24}} className="muted">
                  {loading ? "Loading top statements…" : "No statements (pg_stat_statements empty or unavailable)."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

/* =================== Phase 4 Performance Insights =================== */
function perfUrl(path, params) {
  var url = new URL(path, window.location.origin);
  Object.entries(params || {}).forEach(function(entry) {
    var key = entry[0], value = entry[1];
    if (value !== null && value !== undefined && value !== "" && value !== "all") {
      url.searchParams.set(key, value);
    }
  });
  return url.toString();
}

async function perfJson(path, params, init) {
  var response = await fetch(perfUrl(path, params), Object.assign({ cache: "no-store" }, init || {}));
  return hbzJsonResponse(response);
}

function perfDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", { hour12: false });
}

function PerfToolbar({ children, loading, error, source }) {
  return (
    <div className="card">
      <div className="bd" style={{display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap"}}>
        {children}
        <div className="grow"/>
        {source && <SourceBadge source={source}/>}
        {loading && <span className="pill muted"><Icon.Loader size={12}/>Loading</span>}
        {error && <span className="pill danger"><span className="dot"/>{error}</span>}
      </div>
    </div>
  );
}

function perfSeverityTone(severity) {
  if (severity === "critical") return "danger";
  if (severity === "warning") return "warn";
  if (severity === "ok") return "ok";
  return "info";
}

function perfNodeSummary(summary) {
  var counts = summary && summary.node_counts ? summary.node_counts : {};
  return Object.keys(counts).map(function(key) { return key + " " + counts[key]; }).join(" / ") || "-";
}

function PerfFindings({ analysis }) {
  var findings = analysis && analysis.findings ? analysis.findings : [];
  return (
    <div style={{display: "grid", gap: 8}}>
      {findings.map(function(item, idx) {
        return (
          <div key={idx} className={"risk-banner " + (item.severity === "critical" ? "high" : item.severity === "ok" ? "info" : "")}>
            <Icon.AlertTriangle size={14}/>
            <div>
              <div style={{display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap"}}>
                <span className={"pill " + perfSeverityTone(item.severity)}>{item.severity}</span>
                <strong>{item.title}</strong>
                <span className="muted txt-xs">{item.category}</span>
              </div>
              <div className="txt-sm" style={{marginTop: 4}}>{item.detail}</div>
              <div className="txt-xs muted" style={{marginTop: 4}}>{item.recommendation}</div>
            </div>
          </div>
        );
      })}
      {findings.length === 0 && <div className="muted txt-sm">No rule findings returned.</div>}
    </div>
  );
}

function PerfPlanDetailPanel({ payload, fallback, loading, error }) {
  var statement = (payload && (payload.statement || payload.statistics)) || fallback || {};
  var session = payload && payload.session ? payload.session : null;
  var analysis = payload && payload.analysis ? payload.analysis : null;
  var stats = analysis && analysis.stats_summary ? analysis.stats_summary : {};
  var planSummary = analysis && analysis.plan_summary ? analysis.plan_summary : {};
  var explain = payload && payload.explain ? payload.explain : null;
  var history = payload && payload.history ? payload.history : null;
  var query = (payload && payload.query) || statement.query || (session && session.query) || "";
  var risk = analysis ? analysis.risk_score : null;
  var severity = analysis ? analysis.severity : null;
  return (
    <div style={{display: "grid", gap: 12}}>
      {loading && <div className="pill muted"><Icon.Loader size={12}/>Loading plan analysis</div>}
      {error && <div className="pill danger"><span className="dot"/>{error}</div>}
      <div className="grid-4">
        <Stat label="Risk" value={risk == null ? "-" : risk} sub={severity || "pending"}/>
        <Stat label="Mean" value={fmtMs(stats.mean_exec_ms || statement.mean_exec_ms || 0)}/>
        <Stat label="Max" value={fmtMs(stats.max_exec_ms || statement.max_exec_ms || 0)}/>
        <Stat label="Calls" value={fmtInt(stats.calls || statement.calls || 0)}/>
      </div>
      <div className="grid-4">
        <Stat label="Rows / call" value={stats.rows_per_call == null ? "-" : stats.rows_per_call}/>
        <Stat label="Cache hit" value={stats.cache_hit_pct == null ? "-" : stats.cache_hit_pct + "%"}/>
        <Stat label="Temp blocks" value={fmtInt(stats.temp_blocks || 0)}/>
        <Stat label="Plan root" value={planSummary.root_node || "-"}/>
      </div>
      {session && (
        <div className="grid-4">
          <Stat label="PID" value={session.pid}/>
          <Stat label="Runtime" value={fmtSec(session.query_age_sec || 0)}/>
          <Stat label="User" value={session.username || "-"}/>
          <Stat label="Database" value={session.database || "-"}/>
        </div>
      )}
      <div className="card">
        <div className="hd">Rule Analysis <span className="meta">{analysis ? analysis.ruleset : "pending"}</span></div>
        <div className="bd"><PerfFindings analysis={analysis}/></div>
      </div>
      <div className="card">
        <div className="hd">Plan Summary <span className="meta">{explain && explain.available ? "estimated EXPLAIN" : "not available"}</span></div>
        <div className="bd">
          {explain && !explain.available && <div className="risk-banner info"><Icon.Info size={14}/><div>{explain.reason || explain.error || "Estimated plan unavailable."}</div></div>}
          {explain && explain.available && <div className="risk-banner info"><Icon.Info size={14}/><div>{explain.safety}</div></div>}
          <div className="grid-2 txt-sm" style={{marginTop: 10}}>
            <div><div className="txt-xs muted">Nodes</div>{perfNodeSummary(planSummary)}</div>
            <div><div className="txt-xs muted">Relations</div>{(planSummary.relations || []).join(", ") || "-"}</div>
            <div><div className="txt-xs muted">Indexes</div>{(planSummary.indexes || []).join(", ") || "-"}</div>
            <div><div className="txt-xs muted">Max cost / rows</div>{(planSummary.max_cost || 0) + " / " + fmtInt(planSummary.max_plan_rows || 0)}</div>
          </div>
        </div>
      </div>
      <div className="card">
        <div className="hd">Query</div>
        <pre className="logbox" style={{whiteSpace: "pre-wrap", maxHeight: 220}}>{query || "No query text available."}</pre>
      </div>
      {payload && payload.plan && (
        <div className="card">
          <div className="hd">Estimated Plan JSON</div>
          <pre className="logbox" style={{whiteSpace: "pre-wrap", maxHeight: 360}}>{JSON.stringify(payload.plan, null, 2)}</pre>
        </div>
      )}
      {history && (
        <div className="card">
          <div className="hd">Query History <span className="meta">{history.source || "query_stat_snapshots"} · {history.range || "7d"}</span></div>
          <div className="bd">
            {history.regression && <div className="risk-banner warn"><Icon.AlertTriangle size={14}/><div>Regression suspected: {history.regression.increase_pct}% increase in delta runtime.</div></div>}
            {history.available && (history.points || []).length ? (
              <EChart height={220} option={function() {
                var base = hbzEChartsBase();
                return {
                  tooltip: Object.assign({}, base.tooltip, { valueFormatter: function(v) { return fmtMs(v); } }),
                  yAxis: Object.assign({}, base.yAxis, {
                    name: "Runtime (ms)",
                    nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10, align: "left" },
                    nameGap: 8,
                    axisLabel: Object.assign({}, base.yAxis.axisLabel, { formatter: function(v) { return fmtMs(v); } }),
                  }),
                  series: [
                    hbzAreaSeries("Delta runtime", history.points || [], 1),
                    hbzAreaSeries("Mean runtime", history.mean_points || [], 3, { areaStyle: { opacity: 0.06 } }),
                  ],
                };
              }}/>
            ) : (
              <EmptyState icon={Icon.TrendingUp} title="No history for this query" hint="No persisted pg_stat_statements history store is configured."/>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function PerfPlanDrawer({ title, payload, fallback, loading, error, onClose }) {
  return (
    <Drawer onClose={onClose}>
      <div className="hd">
        <Icon.TrendingUp size={16}/>
        <div>
          <div style={{fontWeight: 600, fontSize: 14}}>{title || "Plan analysis"}</div>
          <div className="muted txt-xs">{(payload && payload.queryid) || (fallback && fallback.queryid) || "active statement"}</div>
        </div>
        <button className="btn ghost icon" style={{marginLeft: "auto"}} onClick={onClose}><Icon.X size={14}/></button>
      </div>
      <div className="bd">
        <PerfPlanDetailPanel payload={payload} fallback={fallback} loading={loading} error={error}/>
      </div>
    </Drawer>
  );
}

function PerformanceInsightsScreen({ view, lastRefresh }) {
  if (view === "activity") return <PerfApplicationActivityScreen lastRefresh={lastRefresh}/>;
  if (view === "waits") return <PerfWaitsScreen lastRefresh={lastRefresh}/>;
  if (view === "plans") return <PerfPlanCacheScreen lastRefresh={lastRefresh}/>;
  if (view === "indexes") return <PerfIndexAdvisorScreen lastRefresh={lastRefresh}/>;
  if (view === "bloat") return <PerfBloatScreen lastRefresh={lastRefresh}/>;
  if (view === "vacuum") return <PerfVacuumScreen lastRefresh={lastRefresh}/>;
  if (view === "slow") return <PerfSlowQueriesScreen lastRefresh={lastRefresh}/>;
  return <PerfTopSqlScreen lastRefresh={lastRefresh}/>;
}

function activityPct(value, total) {
  var n = Number(value || 0), d = Number(total || 0);
  return d <= 0 ? 0 : Math.max(0, Math.min(100, (n / d) * 100));
}

function activityToneForSource(source) {
  if (source === "pgbouncer") return "ok";
  if (source === "direct") return "warn";
  if (source === "local_socket") return "muted";
  return "info";
}

function activityColor(tone) {
  if (tone === "ok" || tone === "pgbouncer") return "#7c3aed";
  if (tone === "warn" || tone === "direct") return "#B8893C";
  if (tone === "danger") return "#dc3545";
  if (tone === "blue") return "#2A6FAA";
  if (tone === "teal") return "#1E7F7E";
  return "#6c757d";
}

function ActivityMetric({ label, value, sub, tone, icon }) {
  return (
    <div className={"activity-metric " + (tone || "blue")}>
      <div className="activity-metric-top">
        <span>{label}</span>
        <span className="activity-metric-icon">{icon}</span>
      </div>
      <div className="activity-metric-value">{value}</div>
      {sub && <div className="activity-metric-sub">{sub}</div>}
    </div>
  );
}

function ActivityMiniBar({ label, value, total, sub, tone }) {
  var pct = activityPct(value, total);
  return (
    <div className="activity-mini-bar">
      <div className="activity-mini-row">
        <span className="activity-mini-label">{label}</span>
        <span className="activity-mini-value">{fmtInt(Number(value || 0))}</span>
      </div>
      <div className="activity-track">
        <div className={"activity-fill " + (tone || "blue")} style={{width: Math.max(2, pct) + "%"}}/>
      </div>
      {sub && <div className="activity-mini-sub">{sub}</div>}
    </div>
  );
}

function ActivityStackedBar({ segments, total }) {
  var safeTotal = Math.max(1, Number(total || 0));
  return (
    <div className="activity-stack" aria-hidden="true">
      {segments.filter(function(seg) { return Number(seg.value || 0) > 0; }).map(function(seg) {
        return (
          <div key={seg.label}
               className={"activity-stack-seg " + (seg.tone || "blue")}
               style={{width: activityPct(seg.value, safeTotal) + "%"}}
               title={seg.label + ": " + fmtInt(Number(seg.value || 0))}/>
        );
      })}
    </div>
  );
}

function ActivityDonut({ segments, center, sub }) {
  var size = 148;
  var stroke = 18;
  var r = (size - stroke) / 2;
  var c = 2 * Math.PI * r;
  var total = segments.reduce(function(sum, seg) { return sum + Number(seg.value || 0); }, 0);
  var offset = 0;
  return (
    <div className="activity-donut-wrap">
      <svg className="activity-donut" width={size} height={size} viewBox={"0 0 " + size + " " + size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#edf1f3" strokeWidth={stroke}/>
        {segments.filter(function(seg) { return Number(seg.value || 0) > 0; }).map(function(seg) {
          var value = Number(seg.value || 0);
          var dash = total <= 0 ? 0 : (value / total) * c;
          var circle = (
            <circle key={seg.label}
                    cx={size / 2}
                    cy={size / 2}
                    r={r}
                    fill="none"
                    stroke={activityColor(seg.tone)}
                    strokeWidth={stroke}
                    strokeDasharray={dash + " " + c}
                    strokeDashoffset={-offset}
                    strokeLinecap="butt"
                    transform={"rotate(-90 " + (size / 2) + " " + (size / 2) + ")"}/>
          );
          offset += dash;
          return circle;
        })}
      </svg>
      <div className="activity-donut-center">
        <strong>{center}</strong>
        <span>{sub}</span>
      </div>
      <div className="activity-donut-legend">
        {segments.map(function(seg) {
          return (
            <span key={seg.label}>
              <i style={{background: activityColor(seg.tone)}}/> {seg.label} {fmtInt(Number(seg.value || 0))}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function ActivityInsight({ title, value, detail, tone, icon }) {
  return (
    <div className={"activity-insight " + (tone || "blue")}>
      <div className="activity-insight-icon">{icon}</div>
      <div>
        <div className="activity-insight-title">{title}</div>
        <div className="activity-insight-value">{value}</div>
        <div className="activity-insight-detail">{detail}</div>
      </div>
    </div>
  );
}

function monitorJson(path, options) {
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

function fmtSessionDuration(seconds) {
  var n = Math.max(0, Number(seconds || 0));
  var m = Math.floor(n / 60);
  var s = Math.floor(n % 60);
  return m + ":" + String(s).padStart(2, "0");
}

function sessionSeverityTone(severity) {
  if (severity === "CRITICAL") return "danger";
  if (severity === "WARNING") return "warn";
  return "ok";
}

function DbSessionToast({ toast, onClose }) {
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

function DbSessionMonitor() {
  var summaryState = React.useState({ idleInTransaction: 0, idle: 0, active: 0, total: 0, maxIdleSeconds: 0, maxConnections: 0, usagePercent: 0 });
  var sessionsState = React.useState([]);
  var loadingState = React.useState(true);
  var listLoadingState = React.useState(true);
  var errorState = React.useState(null);
  var toastState = React.useState(null);
  var confirmState = React.useState(null);
  var bulkState = React.useState(false);
  var thresholdState = React.useState(60);
  var busyState = React.useState(false);
  var refreshedState = React.useState(null);
  var rapidState = React.useState(false);
  var previousIdleCountRef = React.useRef(null);

  var summary = summaryState[0], setSummary = summaryState[1];
  var sessions = sessionsState[0], setSessions = sessionsState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var listLoading = listLoadingState[0], setListLoading = listLoadingState[1];
  var error = errorState[0], setError = errorState[1];
  var toast = toastState[0], setToast = toastState[1];
  var confirm = confirmState[0], setConfirm = confirmState[1];
  var bulkOpen = bulkState[0], setBulkOpen = bulkState[1];
  var threshold = thresholdState[0], setThreshold = thresholdState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var refreshedAt = refreshedState[0], setRefreshedAt = refreshedState[1];
  var rapidGrowth = rapidState[0], setRapidGrowth = rapidState[1];

  function loadSummary() {
    return monitorJson("/api/monitor/db-sessions/summary")
      .then(function(payload) {
        var previous = previousIdleCountRef.current;
        if (previous !== null && Number(payload.idleInTransaction || 0) > previous + 20) {
          setRapidGrowth(true);
        }
        previousIdleCountRef.current = Number(payload.idleInTransaction || 0);
        setSummary(payload);
        setLoading(false);
        setError(null);
        setRefreshedAt(new Date());
        return payload;
      })
      .catch(function(err) {
        setLoading(false);
        setError("Unable to reach monitoring database");
        setToast({ tone: "danger", message: err.message || "Unable to reach monitoring database" });
      });
  }

  function loadSessions() {
    setListLoading(true);
    return monitorJson("/api/monitor/db-sessions/idle-in-transaction")
      .then(function(payload) {
        setSessions(Array.isArray(payload) ? payload : (payload.sessions || []));
        setListLoading(false);
        setError(null);
      })
      .catch(function(err) {
        setSessions([]);
        setListLoading(false);
        setError("Unable to reach monitoring database");
        setToast({ tone: "danger", message: err.message || "Unable to reach monitoring database" });
      });
  }

  function refreshAll() {
    loadSummary();
    loadSessions();
  }

  React.useEffect(function() {
    refreshAll();
    var summaryTimer = setInterval(loadSummary, 15000);
    var sessionTimer = setInterval(loadSessions, 30000);
    return function() {
      clearInterval(summaryTimer);
      clearInterval(sessionTimer);
    };
  }, []);

  function terminateSession(row) {
    setBusy(true);
    monitorJson("/api/monitor/db-sessions/terminate/" + row.pid, { method: "POST" })
      .then(function(result) {
        setBusy(false);
        setConfirm(null);
        setToast({ tone: result.success ? "ok" : "warn", message: result.message || ("Session " + row.pid + " already closed") });
        refreshAll();
      })
      .catch(function(err) {
        setBusy(false);
        setToast({ tone: "danger", message: err.message || ("Failed to terminate session " + row.pid) });
      });
  }

  function terminateBulk() {
    var safeThreshold = Math.max(30, Number(threshold || 60));
    setBusy(true);
    loadSummary().then(function() {
      return monitorJson("/api/monitor/db-sessions/terminate-bulk", {
        method: "POST",
        body: { idleThresholdSeconds: safeThreshold }
      });
    }).then(function(result) {
      setBusy(false);
      setBulkOpen(false);
      setToast({ tone: "ok", message: "Terminated " + result.totalTerminated + " sessions successfully" });
      refreshAll();
    }).catch(function(err) {
      setBusy(false);
      setToast({ tone: "danger", message: err.message || "Bulk termination failed" });
    });
  }

  var staleCount = sessions.filter(function(row) { return Number(row.idleSeconds || 0) > Number(threshold || 60); }).length;
  var clusterLabel = (typeof activeCluster === "function" && activeCluster()) ? activeCluster().name : "the active cluster";
  var usageTone = Number(summary.usagePercent || 0) > 80 ? "danger" : "blue";
  var idleTone = Number(summary.idleInTransaction || 0) > 20 ? "danger" : Number(summary.idleInTransaction || 0) > 0 ? "warn" : "ok";
  var maxTone = Number(summary.maxIdleSeconds || 0) > 90 ? "danger" : Number(summary.maxIdleSeconds || 0) > 45 ? "warn" : "ok";

  return (
    <div className="db-session-monitor">
      <DbSessionToast toast={toast} onClose={function() { setToast(null); }}/>
      <div className="section-h">Idle-In-Transaction Session Monitor <span className="count">audited DBA action</span></div>

      {error && (
        <div className="risk-banner high mb-2">
          <Icon.AlertTriangle size={14}/>
          <div>{error}</div>
        </div>
      )}
      {rapidGrowth && (
        <div className="risk-banner mb-2">
          <Icon.AlertTriangle size={14}/>
          <div>
            <strong>Idle-in-transaction sessions are growing rapidly ({summary.idleInTransaction}).</strong>
            <div className="txt-xs mt-2">Consider bulk termination after confirming application impact.</div>
          </div>
          <button className="btn ghost sm" style={{marginLeft: "auto"}} onClick={function() { setRapidGrowth(false); }}>
            <Icon.X size={12}/>
          </button>
        </div>
      )}

      <div className="activity-metric-grid">
        <ActivityMetric tone={usageTone} label="Total Connections"
                        value={fmtInt(summary.total || 0) + " / " + fmtInt(summary.maxConnections || 0)}
                        sub={Number(summary.usagePercent || 0).toFixed(1) + "% used"} icon={<Icon.Users size={17}/>}/>
        <ActivityMetric tone={idleTone} label="Idle In Transaction"
                        value={fmtInt(summary.idleInTransaction || 0)}
                        sub={Number(summary.idleInTransaction || 0) > 20 ? "above threshold" : "current sessions"} icon={<Icon.AlertTriangle size={17}/>}/>
        <ActivityMetric tone="ok" label="Active Queries"
                        value={fmtInt(summary.active || 0)}
                        sub="pg_stat_activity active" icon={<Icon.Activity size={17}/>}/>
        <ActivityMetric tone={maxTone} label="Max Idle Duration"
                        value={fmtSessionDuration(summary.maxIdleSeconds || 0)}
                        sub={refreshedAt ? "last refreshed " + refreshedAt.toLocaleTimeString("en-GB", { hour12: false }) : "waiting for poll"} icon={<Icon.Clock size={17}/>}/>
      </div>

      <div className="card">
        <div className="hd">
          Idle-In-Transaction Sessions <span className="meta">{sessions.length} rows</span>
          <div className="grow"/>
          {listLoading && <span className="pill muted"><Icon.Loader size={12}/>Loading</span>}
          <button className="btn sm" onClick={refreshAll}><Icon.RefreshCw size={12}/>Refresh</button>
          <button className="btn sm danger" onClick={function() { loadSummary(); setBulkOpen(true); }} disabled={sessions.length === 0 || busy}>
            <Icon.XCircle size={12}/>Kill All Stale Sessions
          </button>
        </div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th className="num">PID</th><th>Database</th><th>User</th><th>Client IP</th>
                <th className="num">Transaction Age</th><th className="num">Idle Duration</th>
                <th>Last Query</th><th>Severity</th><th>Action</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(function(row) {
                var tone = sessionSeverityTone(row.severity);
                var cls = tone === "danger" ? "row-danger" : tone === "warn" ? "row-warn" : "";
                var query = row.lastQuery || "";
                var shortQuery = query.length > 60 ? query.slice(0, 60) + "..." : query;
                return (
                  <tr key={row.pid} className={cls}>
                    <td className="num">{row.pid}</td>
                    <td className="mono">{row.databaseName || "-"}</td>
                    <td className="mono">{row.username || "-"}</td>
                    <td className="mono txt-xs">{row.clientAddress || "<local>"}</td>
                    <td className="num">{fmtSessionDuration(row.transactionAgeSeconds)}</td>
                    <td className={"num idle-duration " + tone}>{fmtSessionDuration(row.idleSeconds)}</td>
                    <td className="mono txt-xs" title={query} style={{maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{shortQuery || "-"}</td>
                    <td><span className={"pill " + tone}><span className="dot"/>{row.severity}</span></td>
                    <td>
                      <button className="btn ghost sm danger" disabled={row.severity === "OK" || busy} onClick={function() { setConfirm(row); }}>
                        <Icon.XCircle size={12}/>Kill
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!listLoading && sessions.length === 0 && (
                <tr><td colSpan="9" style={{textAlign: "center", padding: 24}} className="muted">No idle-in-transaction sessions visible.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {confirm && (
        <Modal onClose={function() { if (!busy) setConfirm(null); }}>
          <div className="hd">
            <Icon.ShieldAlert size={16} color="var(--danger)"/>
            <h3>Terminate session PID {confirm.pid}</h3>
            <button className="btn ghost icon close" onClick={function() { if (!busy) setConfirm(null); }}><Icon.X size={14}/></button>
          </div>
          <div className="bd">
            <p className="modal-lead">This immediately runs <code>pg_terminate_backend({confirm.pid})</code> on the PostgreSQL primary.</p>
            <ul className="impact-list">
              <li><span>Cluster</span><strong>{clusterLabel}</strong></li>
              <li><span>Database</span><strong>{confirm.databaseName || "\u2014"}</strong></li>
              <li><span>Backend PID</span><strong>{confirm.pid}</strong></li>
              <li><span>Idle for</span><strong>{fmtSessionDuration(confirm.idleSeconds)}</strong></li>
            </ul>
            <div className="risk-banner high">
              <Icon.AlertTriangle size={14}/>
              <div>Any open transaction on this backend will be rolled back. This action cannot be undone.</div>
            </div>
          </div>
          <div className="ft">
            <button className="btn sm" disabled={busy} onClick={function() { setConfirm(null); }}>Cancel</button>
            <button className="btn sm danger" disabled={busy} onClick={function() { terminateSession(confirm); }}>
              <Icon.XCircle size={12}/>{busy ? "Terminating" : "Confirm"}
            </button>
          </div>
        </Modal>
      )}

      {bulkOpen && (
        <Modal onClose={function() { if (!busy) setBulkOpen(false); }}>
          <div className="hd">
            <Icon.ShieldAlert size={16} color="var(--danger)"/>
            <h3>Kill All Stale Sessions</h3>
            <button className="btn ghost icon close" onClick={function() { if (!busy) setBulkOpen(false); }}><Icon.X size={14}/></button>
          </div>
          <div className="bd">
            <div className="field">
              <label>Idle-in-transaction threshold (seconds)</label>
              <input type="number" min="30" max="86400" step="30" value={threshold}
                     onChange={function(e) { var n = parseInt(e.target.value, 10); setThreshold(Number.isFinite(n) ? Math.min(86400, Math.max(30, n)) : 30); }}/>
              <div className="field-hint">Sessions idle in transaction longer than this are terminated. Minimum 30 seconds.</div>
            </div>
            <ul className="impact-list mt-3">
              <li><span>Cluster</span><strong>{clusterLabel}</strong></li>
              <li><span>Sessions affected</span><strong>{staleCount}</strong></li>
            </ul>
            <div className={"risk-banner mt-3 " + (staleCount > 0 ? "high" : "info")}>
              <Icon.AlertTriangle size={14}/>
              <div>{staleCount > 0 ? ("This will terminate " + staleCount + " session(s) on " + clusterLabel + ". Open transactions will be rolled back. This action cannot be undone.") : "No sessions currently exceed this threshold \u2014 nothing will be terminated."}</div>
            </div>
          </div>
          <div className="ft">
            <button className="btn sm" disabled={busy} onClick={function() { setBulkOpen(false); }}>Cancel</button>
            <button className="btn sm danger" disabled={busy || staleCount === 0} onClick={terminateBulk}>
              <Icon.XCircle size={12}/>{busy ? "Terminating" : "Confirm"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function PerfApplicationActivityScreen({ lastRefresh }) {
  var databaseState = React.useState("postgres");
  var groupState = React.useState("applications");
  var dataState = React.useState({
    summary: {},
    source_breakdown: [],
    application_breakdown: [],
    user_breakdown: [],
    database_breakdown: [],
    client_breakdown: [],
    dml_by_database: [],
    dml_by_table: [],
    active_dml: [],
    top_dml_sql: []
  });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var database = databaseState[0], setDatabase = databaseState[1];
  var group = groupState[0], setGroup = groupState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    perfJson(clusterPath("/perf/application-activity"), { database: database, limit: 100 })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [database, lastRefresh]);

  var summary = data.summary || {};
  var sourceRows = data.source_breakdown || [];
  var groupRows = group === "users"
    ? (data.user_breakdown || [])
    : group === "databases"
      ? (data.database_breakdown || [])
      : group === "clients"
        ? (data.client_breakdown || [])
        : (data.application_breakdown || []);
  var groupTitle = group === "users" ? "DB User / Database"
    : group === "databases" ? "Database"
      : group === "clients" ? "Client Address"
        : "Application";
  var dmlDbRows = data.dml_by_database || [];
  var dmlTableRows = data.dml_by_table || [];
  var topDmlRows = data.top_dml_sql || [];
  var activeDmlRows = data.active_dml || [];
  var totalSessions = Number(summary.total_client_sessions || 0);
  var idleXact = Number(summary.idle_in_transaction_sessions || 0);
  var idleSessions = Number(summary.idle_sessions || 0);
  var activeSessions = Number(summary.active_sessions || 0);
  var idleXactPct = activityPct(idleXact, totalSessions);
  var pgbouncerPct = activityPct(summary.pgbouncer_sessions, totalSessions);
  var directTotal = Number(summary.direct_sessions || 0) + Number(summary.local_socket_sessions || 0);
  var appRows = (data.application_breakdown || []).slice(0, 8);
  var pgbouncerRows = (data.source_breakdown || []).filter(function(row) { return row.connection_source === "pgbouncer"; });
  var idleRiskRows = (data.user_breakdown || [])
    .filter(function(row) { return Number(row.idle_in_transaction || 0) > 0; })
    .slice()
    .sort(function(a, b) { return Number(b.idle_in_transaction || 0) - Number(a.idle_in_transaction || 0); })
    .slice(0, 8);
  var directRows = (data.source_breakdown || []).filter(function(row) { return row.connection_source === "direct"; }).slice(0, 6);
  var dmlTotals = dmlDbRows.reduce(function(acc, row) {
    acc.inserted += Number(row.tup_inserted || 0);
    acc.updated += Number(row.tup_updated || 0);
    acc.deleted += Number(row.tup_deleted || 0);
    return acc;
  }, { inserted: 0, updated: 0, deleted: 0 });
  var dmlTotal = dmlTotals.inserted + dmlTotals.updated + dmlTotals.deleted;
  var topDbDml = dmlDbRows.slice(0, 6);
  var topTableDml = dmlTableRows.slice(0, 6);
  var idleTone = idleXactPct >= 40 ? "danger" : idleXactPct >= 20 ? "warn" : "ok";
  var directTone = directTotal > 0 ? "warn" : "ok";
  var sourceSegments = [
    { label: "PgBouncer", value: summary.pgbouncer_sessions || 0, tone: "ok" },
    { label: "Direct", value: summary.direct_sessions || 0, tone: "warn" },
    { label: "Local", value: summary.local_socket_sessions || 0, tone: "muted" }
  ];
  var otherStateSessions = Math.max(0, totalSessions - idleSessions - idleXact - activeSessions);
  var stateSegments = [
    { label: "Idle", value: idleSessions, tone: "blue" },
    { label: "Idle xact", value: idleXact, tone: idleTone },
    { label: "Active", value: activeSessions, tone: "ok" },
    { label: "Other", value: otherStateSessions, tone: "muted" }
  ];
  var dmlSegments = [
    { label: "Insert", value: dmlTotals.inserted, tone: "ok" },
    { label: "Update", value: dmlTotals.updated, tone: "blue" },
    { label: "Delete", value: dmlTotals.deleted, tone: "danger" }
  ];

  function groupLabel(row) {
    if (group === "users") return (row.username || "—") + " / " + (row.database || "—");
    if (group === "databases") return row.database || "—";
    if (group === "clients") return row.client_addr || "—";
    return row.application_name || "—";
  }

  function oldest(row) {
    var q = row.oldest_query_age_sec;
    var x = row.oldest_xact_age_sec;
    if (x != null) return fmtSec(Number(x));
    if (q != null) return fmtSec(Number(q));
    return "—";
  }

  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data.source || "live PostgreSQL stats"}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Table DML database</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }} placeholder="database name"/>
        </div>
        {["applications", "users", "databases", "clients"].map(function(item) {
          return (
            <button key={item}
                    className={"btn sm " + (group === item ? "primary" : "")}
                    onClick={function() { setGroup(item); }}>
              {item}
            </button>
          );
        })}
      </PerfToolbar>

      <div className="section-h">Application Activity <span className="count">live read-only</span></div>
      <div className="activity-hero">
        <div className="activity-hero-main">
          <div className="activity-live-line">
            <span className="led ok"/>
            <span>Live from PostgreSQL statistics</span>
            {data.observed_at && <span className="muted">observed {perfDate(data.observed_at)}</span>}
          </div>
          <div className="activity-hero-title">Current application workload and connection posture</div>
          <div className="activity-hero-sub">
            Sessions are read from <span className="mono">pg_stat_activity</span>; DML counters are read from PostgreSQL cumulative stats.
          </div>
        </div>
        <div className="activity-hero-side">
          <span className={"pill " + idleTone}><span className="dot"/>{idleXactPct.toFixed(1)}% idle in transaction</span>
          <span className={"pill " + directTone}><span className="dot"/>{fmtInt(directTotal)} non-PgBouncer sessions</span>
        </div>
        </div>

        <DbSessionMonitor/>

        <div className="activity-metric-grid">
        <ActivityMetric tone="blue" label="Client sessions" value={fmtInt(totalSessions)} sub="current visible backends" icon={<Icon.Users size={17}/>}/>
        <ActivityMetric tone="ok" label="PgBouncer" value={fmtInt(summary.pgbouncer_sessions || 0)} sub={pgbouncerPct.toFixed(1) + "% via pooler"} icon={<Icon.LinkIcon size={17}/>}/>
        <ActivityMetric tone={directTone} label="Direct + local" value={fmtInt(directTotal)} sub={fmtInt(summary.direct_sessions || 0) + " direct / " + fmtInt(summary.local_socket_sessions || 0) + " local"} icon={<Icon.Wifi size={17}/>}/>
        <ActivityMetric tone={idleTone} label="Idle in transaction" value={fmtInt(idleXact)} sub={idleXactPct.toFixed(1) + "% of sessions"} icon={<Icon.AlertTriangle size={17}/>}/>
        <ActivityMetric tone="ok" label="Active sessions" value={fmtInt(activeSessions)} sub="running now" icon={<Icon.Activity size={17}/>}/>
        <ActivityMetric tone="teal" label="Active DML" value={fmtInt(summary.active_dml_sessions || 0)} sub="insert/update/delete now" icon={<Icon.Zap size={17}/>}/>
        <ActivityMetric tone="blue" label="DB DML total" value={fmtInt(summary.database_dml_total || 0)} sub="pg_stat_database" icon={<Icon.Database size={17}/>}/>
        <ActivityMetric tone="teal" label="Table DML total" value={fmtInt(summary.selected_database_table_dml_total || 0)} sub={data.database || database} icon={<Icon.Box size={17}/>}/>
      </div>

      <div className="activity-insight-grid">
        <ActivityInsight tone={idleTone}
                         icon={<Icon.Clock size={17}/>}
                         title="Transaction hygiene"
                         value={fmtInt(idleXact) + " idle xact"}
                         detail={idleRiskRows[0] ? "Top: " + (idleRiskRows[0].username || "unknown") + " on " + (idleRiskRows[0].database || "unknown") : "No idle transactions visible"}/>
        <ActivityInsight tone="ok"
                         icon={<Icon.LinkIcon size={17}/>}
                         title="Pooler coverage"
                         value={pgbouncerPct.toFixed(1) + "%"}
                         detail={(data.pgbouncer_client_addrs || []).join(", ") || "No PgBouncer source detected"}/>
        <ActivityInsight tone={directTone}
                         icon={<Icon.Wifi size={17}/>}
                         title="Direct access"
                         value={fmtInt(summary.direct_sessions || 0)}
                         detail={directRows.length ? directRows.map(function(row) { return row.client_addr; }).join(", ") : "No direct clients visible"}/>
        <ActivityInsight tone="blue"
                         icon={<Icon.Database size={17}/>}
                         title="DML mix"
                         value={fmtInt(dmlTotal)}
                         detail={fmtInt(dmlTotals.inserted) + " inserts / " + fmtInt(dmlTotals.updated) + " updates / " + fmtInt(dmlTotals.deleted) + " deletes"}/>
      </div>

      <div className="activity-panel-grid">
        <div className="card activity-panel">
          <div className="hd">Connection Source <span className="meta">{fmtInt(totalSessions)} sessions</span></div>
          <div className="bd">
            <ActivityDonut segments={sourceSegments} center={fmtInt(totalSessions)} sub="sessions"/>
            <ActivityStackedBar segments={sourceSegments} total={totalSessions}/>
          </div>
        </div>
        <div className="card activity-panel">
          <div className="hd">Session State <span className="meta">live pg_stat_activity</span></div>
          <div className="bd">
            <ActivityDonut segments={stateSegments} center={fmtInt(idleXact)} sub="idle xact"/>
            <ActivityStackedBar segments={stateSegments} total={totalSessions}/>
          </div>
        </div>
        <div className="card activity-panel">
          <div className="hd">PgBouncer Balance <span className="meta">{pgbouncerRows.length} sources</span></div>
          <div className="bd activity-list">
            {pgbouncerRows.map(function(row) {
              return <ActivityMiniBar key={row.client_addr}
                                      label={row.client_addr}
                                      value={row.sessions}
                                      total={summary.pgbouncer_sessions || 1}
                                      tone="ok"
                                      sub={fmtInt(row.idle_in_transaction || 0) + " idle xact / " + fmtInt(row.users || 0) + " users"}/>;
            })}
            {!loading && pgbouncerRows.length === 0 && <div className="muted txt-xs">No PgBouncer backend sessions visible.</div>}
          </div>
        </div>
        <div className="card activity-panel">
          <div className="hd">DML Command Mix <span className="meta">cumulative</span></div>
          <div className="bd">
            <ActivityDonut segments={dmlSegments} center={fmtInt(dmlTotal)} sub="row changes"/>
            <ActivityStackedBar segments={dmlSegments} total={dmlTotal}/>
          </div>
        </div>
      </div>

      <div className="section-h">Application Perspective</div>
      <div className="activity-panel-grid two">
        <div className="card activity-panel">
          <div className="hd">Top Applications <span className="meta">session mix</span></div>
          <div className="bd activity-list">
            {appRows.map(function(row) {
              var tone = Number(row.idle_in_transaction || 0) > 0 ? "warn" : activityToneForSource(row.connection_source);
              return <ActivityMiniBar key={row.connection_source + ":" + row.application_name}
                                      label={row.application_name || "<unset>"}
                                      value={row.sessions}
                                      total={totalSessions || 1}
                                      tone={tone}
                                      sub={row.connection_source + " / " + fmtInt(row.idle_in_transaction || 0) + " idle xact / " + fmtInt(row.users || 0) + " users"}/>;
            })}
            {!loading && appRows.length === 0 && <div className="muted txt-xs">No application sessions visible.</div>}
          </div>
        </div>
        <div className="card activity-panel">
          <div className="hd">Idle Transaction Leaders <span className="meta">application risk</span></div>
          <div className="bd activity-list">
            {idleRiskRows.map(function(row) {
              return <ActivityMiniBar key={row.connection_source + ":" + row.username + ":" + row.database}
                                      label={(row.username || "unknown") + " / " + (row.database || "unknown")}
                                      value={row.idle_in_transaction}
                                      total={idleXact || 1}
                                      tone="danger"
                                      sub={(row.connection_source || "source") + " / oldest " + oldest(row)}/>;
            })}
            {!loading && idleRiskRows.length === 0 && <div className="muted txt-xs">No idle-in-transaction sessions visible.</div>}
          </div>
        </div>
      </div>

      <div className="activity-panel-grid two">
        <div className="card activity-panel">
          <div className="hd">Top Databases by DML <span className="meta">pg_stat_database</span></div>
          <div className="bd activity-list">
            {topDbDml.map(function(row) {
              return <ActivityMiniBar key={row.database}
                                      label={row.database}
                                      value={row.total_dml}
                                      total={summary.database_dml_total || 1}
                                      tone="blue"
                                      sub={fmtInt(row.tup_inserted || 0) + " ins / " + fmtInt(row.tup_updated || 0) + " upd / " + fmtInt(row.tup_deleted || 0) + " del"}/>;
            })}
          </div>
        </div>
        <div className="card activity-panel">
          <div className="hd">Top Tables by DML <span className="meta">{data.database || database}</span></div>
          <div className="bd activity-list">
            {topTableDml.map(function(row) {
              return <ActivityMiniBar key={row.schemaname + "." + row.table_name}
                                      label={row.schemaname + "." + row.table_name}
                                      value={row.total_dml}
                                      total={summary.selected_database_table_dml_total || 1}
                                      tone="teal"
                                      sub={fmtInt(row.inserts || 0) + " ins / " + fmtInt(row.updates || 0) + " upd / " + fmtInt(row.deletes || 0) + " del"}/>;
            })}
            {data.table_dml_error && <span className="pill danger">{data.table_dml_error}</span>}
          </div>
        </div>
      </div>

      {data.visibility && (
        <div className="card">
          <div className="bd">
            <div className="risk-banner">
              <Icon.Info size={14}/>
              <div>
                <strong>Application DML attribution</strong>
                <div className="txt-xs mt-2">{data.visibility.application_dml_attribution_note}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="hd">Connection Source Split <span className="meta">{sourceRows.length} groups</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Source</th><th className="num">Sessions</th><th className="num">Active</th><th className="num">Idle</th>
                <th className="num">Idle in xact</th><th className="num">Users</th><th className="num">DBs</th>
                <th className="num">Apps</th><th className="num">Oldest xact/query</th>
              </tr>
            </thead>
            <tbody>
              {sourceRows.map(function(row) {
                return (
                  <tr key={row.connection_source}>
                    <td><span className={"pill " + (row.connection_source === "pgbouncer" ? "ok" : row.connection_source === "direct" ? "warn" : "muted")}>{row.connection_source}</span></td>
                    <td className="num">{fmtInt(row.sessions || 0)}</td>
                    <td className="num">{fmtInt(row.active || 0)}</td>
                    <td className="num">{fmtInt(row.idle || 0)}</td>
                    <td className="num">{fmtInt(row.idle_in_transaction || 0)}</td>
                    <td className="num">{fmtInt(row.users || 0)}</td>
                    <td className="num">{fmtInt(row.databases || 0)}</td>
                    <td className="num">{fmtInt(row.applications || 0)}</td>
                    <td className="num">{oldest(row)}</td>
                  </tr>
                );
              })}
              {!loading && sourceRows.length === 0 && <tr><td colSpan="9" style={{textAlign: "center", padding: 24}} className="muted">No client sessions visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="hd">{groupTitle} Groups <span className="meta">{groupRows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>{groupTitle}</th><th>Source</th><th className="num">Sessions</th><th className="num">Active</th>
                <th className="num">Idle</th><th className="num">Idle in xact</th><th className="num">Users</th>
                <th className="num">DBs</th><th className="num">Apps</th><th className="num">Oldest xact/query</th>
              </tr>
            </thead>
            <tbody>
              {groupRows.map(function(row, idx) {
                return (
                  <tr key={group + ":" + idx}>
                    <td className="mono txt-xs">{groupLabel(row)}</td>
                    <td><span className={"pill " + (row.connection_source === "pgbouncer" ? "ok" : row.connection_source === "direct" ? "warn" : "muted")}>{row.connection_source}</span></td>
                    <td className="num">{fmtInt(row.sessions || 0)}</td>
                    <td className="num">{fmtInt(row.active || 0)}</td>
                    <td className="num">{fmtInt(row.idle || 0)}</td>
                    <td className="num">{fmtInt(row.idle_in_transaction || 0)}</td>
                    <td className="num">{row.users == null ? "—" : fmtInt(row.users)}</td>
                    <td className="num">{row.databases == null ? "—" : fmtInt(row.databases)}</td>
                    <td className="num">{row.applications == null ? "—" : fmtInt(row.applications)}</td>
                    <td className="num">{oldest(row)}</td>
                  </tr>
                );
              })}
              {!loading && groupRows.length === 0 && <tr><td colSpan="10" style={{textAlign: "center", padding: 24}} className="muted">No grouped sessions visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="section-h">DML Activity</div>
      <div className="card">
        <div className="hd">Database DML Counters <span className="meta">{dmlDbRows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Database</th><th className="num">Total DML</th><th className="num">Inserted</th><th className="num">Updated</th><th className="num">Deleted</th><th className="num">Commits</th><th className="num">Rollbacks</th><th>Stats reset</th></tr></thead>
            <tbody>
              {dmlDbRows.map(function(row) {
                return (
                  <tr key={row.database}>
                    <td className="mono">{row.database}</td>
                    <td className="num">{fmtInt(row.total_dml || 0)}</td>
                    <td className="num">{fmtInt(row.tup_inserted || 0)}</td>
                    <td className="num">{fmtInt(row.tup_updated || 0)}</td>
                    <td className="num">{fmtInt(row.tup_deleted || 0)}</td>
                    <td className="num">{fmtInt(row.xact_commit || 0)}</td>
                    <td className="num">{fmtInt(row.xact_rollback || 0)}</td>
                    <td>{perfDate(row.stats_reset)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="hd">Table DML Counters <span className="meta">{data.database || database} · {dmlTableRows.length} rows</span></div>
        {data.table_dml_error && <div className="bd"><span className="pill danger">{data.table_dml_error}</span></div>}
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Schema</th><th>Table</th><th className="num">Total DML</th><th className="num">Inserted</th><th className="num">Updated</th><th className="num">Deleted</th><th className="num">Live</th><th className="num">Dead</th><th className="num">Mod since analyze</th></tr></thead>
            <tbody>
              {dmlTableRows.map(function(row) {
                return (
                  <tr key={row.schemaname + "." + row.table_name}>
                    <td className="mono">{row.schemaname}</td>
                    <td className="mono">{row.table_name}</td>
                    <td className="num">{fmtInt(row.total_dml || 0)}</td>
                    <td className="num">{fmtInt(row.inserts || 0)}</td>
                    <td className="num">{fmtInt(row.updates || 0)}</td>
                    <td className="num">{fmtInt(row.deletes || 0)}</td>
                    <td className="num">{fmtInt(row.live_tuples || 0)}</td>
                    <td className="num">{fmtInt(row.dead_tuples || 0)}</td>
                    <td className="num">{fmtInt(row.mod_since_analyze || 0)}</td>
                  </tr>
                );
              })}
              {!loading && dmlTableRows.length === 0 && <tr><td colSpan="9" style={{textAlign: "center", padding: 24}} className="muted">No table DML counters visible for this database.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="hd">Current DML Sessions <span className="meta">{activeDmlRows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th className="num">PID</th><th>Command</th><th>User</th><th>Database</th><th>Application</th><th>Source</th><th className="num">Xact age</th><th>Query</th></tr></thead>
            <tbody>
              {activeDmlRows.map(function(row) {
                return (
                  <tr key={row.pid}>
                    <td className="num">{row.pid}</td>
                    <td><span className="pill info">{row.command}</span></td>
                    <td className="mono txt-xs">{row.username}</td>
                    <td className="mono txt-xs">{row.database}</td>
                    <td className="mono txt-xs">{row.application_name}</td>
                    <td>{row.connection_source}</td>
                    <td className="num">{fmtSec(row.xact_age_sec || row.query_age_sec || 0)}</td>
                    <td>
                      <button className="linklike mono txt-xs"
                              title="Show slow query stats and safe estimated EXPLAIN plan"
                              onClick={function() { openSlowDetail(row); }}
                              style={{maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block", width: "100%", textAlign: "left"}}>
                        {row.query}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!loading && activeDmlRows.length === 0 && <tr><td colSpan="8" style={{textAlign: "center", padding: 24}} className="muted">No current DML sessions visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="hd">Top DML SQL <span className="meta">{topDmlRows.length} rows</span></div>
        {data.top_dml_available === false && <div className="bd"><span className="pill warn">{data.top_dml_error || "pg_stat_statements is not available"}</span></div>}
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Command</th><th>Database</th><th>User</th><th className="num">Calls</th><th className="num">Rows</th><th className="num">Total</th><th className="num">Mean</th><th>Query</th></tr></thead>
            <tbody>
              {topDmlRows.map(function(row, idx) {
                return (
                  <tr key={(row.queryid || "dml") + ":" + idx}>
                    <td><span className="pill info">{row.command}</span></td>
                    <td className="mono txt-xs">{row.database || "—"}</td>
                    <td className="mono txt-xs">{row.username || "—"}</td>
                    <td className="num">{fmtInt(row.calls || 0)}</td>
                    <td className="num">{fmtInt(row.rows || 0)}</td>
                    <td className="num">{fmtMs(row.total_exec_ms || 0)}</td>
                    <td className="num">{fmtMs(row.mean_exec_ms || 0)}</td>
                    <td className="mono txt-xs" style={{maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{row.query}</td>
                  </tr>
                );
              })}
              {!loading && topDmlRows.length === 0 && <tr><td colSpan="8" style={{textAlign: "center", padding: 24}} className="muted">No DML statements visible in pg_stat_statements.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function PerfTopSqlScreen({ lastRefresh }) {
  var sortState = React.useState("total");
  var dbState = React.useState("");
  var dataState = React.useState({ top_sql: [], count: 0, available: true });
  var historyState = React.useState({ available: false, series: [] });
  var selectedHistoryState = React.useState({ available: false, points: [] });
  var loadingState = React.useState(true);
  var historyLoadingState = React.useState(false);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var detailState = React.useState(null);
  var detailLoadingState = React.useState(false);
  var detailErrorState = React.useState(null);
  var sort = sortState[0], setSort = sortState[1];
  var database = dbState[0], setDatabase = dbState[1];
  var data = dataState[0], setData = dataState[1];
  var history = historyState[0], setHistory = historyState[1];
  var selectedHistory = selectedHistoryState[0], setSelectedHistory = selectedHistoryState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var historyLoading = historyLoadingState[0], setHistoryLoading = historyLoadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var detail = detailState[0], setDetail = detailState[1];
  var detailLoading = detailLoadingState[0], setDetailLoading = detailLoadingState[1];
  var detailError = detailErrorState[0], setDetailError = detailErrorState[1];

  React.useEffect(function() {
    setLoading(true);
    setError(null);
    perfJson(clusterPath("/perf/topsql"), { sort: sort, db: database, limit: 75 })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [sort, database, lastRefresh]);

  React.useEffect(function() {
    setHistoryLoading(true);
    perfJson(clusterPath("/perf/topsql/history"), { range: "24h", limit: 8 })
      .then(function(payload) { setHistory(payload); setHistoryLoading(false); })
      .catch(function() { setHistory({ available: false, series: [] }); setHistoryLoading(false); });
  }, [lastRefresh]);

  function openStatementDetail(row) {
    setSelected(row);
    setDetail(null);
    setDetailError(null);
    if (!row.queryid) return;
    setDetailLoading(true);
    setSelectedHistory({ available: false, points: [] });
    perfJson(clusterPath("/perf/plans/") + encodeURIComponent(row.queryid), { database: row.database })
      .then(function(payload) { setDetail(payload); setDetailLoading(false); })
      .catch(function(err) { setDetailError(err.message || String(err)); setDetailLoading(false); });
    perfJson(clusterPath("/perf/topsql/") + encodeURIComponent(row.queryid) + "/history", { database: row.database, range: "7d" })
      .then(function(payload) { setSelectedHistory(payload); })
      .catch(function() { setSelectedHistory({ available: false, points: [] }); });
  }

  function captureHistory() {
    setHistoryLoading(true);
    perfJson(clusterPath("/perf/topsql/capture"), { limit: 200 }, { method: "POST" })
      .then(function() {
        return perfJson(clusterPath("/perf/topsql/history"), { range: "24h", limit: 8 });
      })
      .then(function(payload) { setHistory(payload); setHistoryLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setHistoryLoading(false); });
  }

  var rows = data.top_sql || [];
  var totalMs = rows.reduce(function(sum, row) { return sum + Number(row.total_exec_ms || 0); }, 0);
  var calls = rows.reduce(function(sum, row) { return sum + Number(row.calls || 0); }, 0);
  var maxTotal = Math.max(1, ...rows.map(function(row) { return Number(row.total_exec_ms || 0); }));
  var topTimeRows = rows.slice(0, 8).map(function(row) { return { label: row.database + " / " + row.queryid, value: Number(row.total_exec_ms || 0), sub: fmtInt(row.calls || 0) + " calls", tone: "info" }; });
  var topCallRows = rows.slice(0, 8).map(function(row) { return { label: row.database + " / " + row.queryid, value: Number(row.calls || 0), sub: fmtMs(row.mean_exec_ms || 0) + " mean", tone: "ok" }; });
  var cacheRows = rows.filter(function(row) { return row.cache_hit_pct != null; }).slice(0, 8).map(function(row) { return { label: row.database + " / " + row.queryid, value: Math.max(0, 100 - Number(row.cache_hit_pct || 0)), sub: row.cache_hit_pct.toFixed(1) + "% hit", tone: row.cache_hit_pct > 95 ? "ok" : row.cache_hit_pct > 85 ? "warn" : "danger" }; });

  // QPI (Phase C): ECharts horizontal bar of the top statements by total runtime.
  // Reversed so the heaviest query sits at the top of the chart.
  var qpiRows = rows.slice(0, 8);
  var qpiLabels = qpiRows.map(function(r) { return r.queryid || "unknown query"; }).reverse();
  var qpiTotal = qpiRows.map(function(r) { return Number(r.total_exec_ms || 0); }).reverse();
  var qpiMean = qpiRows.map(function(r) { return Number(r.mean_exec_ms || 0); }).reverse();
  var qpiCalls = qpiRows.map(function(r) { return Number(r.calls || 0); }).reverse();

  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data.source || "pg_stat_statements"}>
        <div className="field" style={{margin: 0, minWidth: 220}}>
          <label>Database filter</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }} placeholder="optional database"/>
        </div>
        {[
          ["total", "Total"], ["mean", "Mean"], ["calls", "Calls"],
          ["rows", "Rows"], ["io", "I/O"], ["temp", "Temp"]
        ].map(function(item) {
          return <button key={item[0]} className={"btn sm " + (sort === item[0] ? "primary" : "")} onClick={function() { setSort(item[0]); }}>{item[1]}</button>;
        })}
        <span className="pill muted">History store unavailable</span>
      </PerfToolbar>

      <div className="section-h">Top SQL / Query Store</div>
      <div className="grid-4">
        <Stat label="Statements" value={rows.length}/>
        <Stat label="Calls" value={fmtInt(calls)}/>
        <Stat label="Total time" value={fmtMs(totalMs)}/>
        <Stat label="Available" value={data.available === false ? "no" : "yes"}/>
      </div>

      {qpiRows.length > 0 && (
        <div className="card">
          <div className="hd">Query Performance Insight <span className="meta">top {qpiRows.length} by total runtime · pg_stat_statements</span></div>
          <div className="bd">
            <EChart height={Math.max(220, qpiRows.length * 38)} option={function() {
              var base = hbzEChartsBase();
              return {
                grid: { left: 8, right: 24, top: 12, bottom: 6, containLabel: true },
                tooltip: Object.assign({}, base.tooltip, {
                  trigger: "axis",
                  axisPointer: { type: "shadow" },
                  formatter: function(ps) {
                    if (!ps || !ps.length) return "";
                    var i = ps[0].dataIndex;
                    return ps[0].axisValue +
                      "<br/>Total: " + fmtMs(qpiTotal[i]) +
                      "<br/>Mean: " + fmtMs(qpiMean[i]) +
                      "<br/>Calls: " + fmtInt(qpiCalls[i]);
                  }
                }),
                xAxis: {
                  type: "value",
                  axisLabel: Object.assign({}, base.yAxis.axisLabel, { formatter: function(v) { return fmtMs(v); } }),
                  splitLine: base.yAxis.splitLine,
                  axisLine: { lineStyle: { color: vizVar("--border", "#e0e0e0") } }
                },
                yAxis: {
                  type: "category",
                  data: qpiLabels,
                  axisTick: { show: false },
                  axisLine: { lineStyle: { color: vizVar("--border", "#e0e0e0") } },
                  axisLabel: {
                    color: vizVar("--fg-dim", "#6c757d"), fontSize: 10,
                    formatter: function(v) { return v.length > 36 ? v.slice(0, 34) + "…" : v; }
                  }
                },
                series: [{
                  type: "bar", data: qpiTotal, barWidth: "55%",
                  itemStyle: { color: vizColor(1), borderRadius: [0, 3, 3, 0] },
                  emphasis: { focus: "series" }
                }]
              };
            }}/>
          </div>
        </div>
      )}

      <div className="card">
        <div className="hd">Historical Query Performance <span className="meta">query_stat_snapshots · 24h</span></div>
        <div className="bd">
          {history.available && (history.series || []).length ? (
            <EChart height={300} option={function() {
              var base = hbzEChartsBase();
              return {
                tooltip: Object.assign({}, base.tooltip, { valueFormatter: function(v) { return fmtMs(v); } }),
                yAxis: Object.assign({}, base.yAxis, {
                  name: "Runtime (ms)",
                  nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10, align: "left" },
                  nameGap: 8,
                  axisLabel: Object.assign({}, base.yAxis.axisLabel, { formatter: function(v) { return fmtMs(v); } }),
                }),
                series: (history.series || []).map(function(s, idx) {
                  var label = (s.database_name || "db") + " / " + s.queryid;
                  return hbzAreaSeries(label, s.points || [], (idx % 8) + 1, { areaStyle: { opacity: 0.08 } });
                }),
              };
            }}/>
          ) : (
            <EmptyState icon={Icon.TrendingUp}
                        title="No query history captured yet"
                        hint="No persisted pg_stat_statements history store is configured; this page shows current snapshots only."/>
          )}
        </div>
      </div>

      <div className="grid-3">
        <div className="card"><div className="bd"><BarList title="Total Runtime" rows={topTimeRows} valueFormatter={fmtMs}/></div></div>
        <div className="card"><div className="bd"><BarList title="Call Volume" rows={topCallRows}/></div></div>
        <div className="card"><div className="bd"><BarList title="Cache Miss Exposure" rows={cacheRows} valueFormatter={function(v) { return v.toFixed(1) + "%"; }}/></div></div>
      </div>

      {data.available === false && (
        <div className="card"><div className="bd muted">{data.error || "pg_stat_statements is not available."}</div></div>
      )}

      <div className="card">
        <div className="hd">Top SQL <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Query ID</th><th>Database</th><th>Query</th>
                <th className="num">Calls</th><th className="num">Total</th>
                <th className="num">Mean</th><th className="num">p95 est.</th>
                <th className="num">Rows</th><th className="num">Cache hit</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(function(row, idx) {
                return (
                  <tr key={(row.database || "") + ":" + (row.queryid || "no-queryid") + ":" + idx}>
                    <td className="mono txt-xs">{row.queryid}</td>
                    <td className="mono txt-xs">{row.database || "—"}</td>
                    <td>
                      <button className="linklike mono txt-xs"
                              title="Show query stats and safe estimated EXPLAIN plan"
                              onClick={function() { openStatementDetail(row); }}
                              style={{maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block", width: "100%", textAlign: "left"}}>
                        {row.query}
                      </button>
                      <div style={{height: 3, background: "var(--surface-3)", borderRadius: 2, marginTop: 4, width: 240}}>
                        <div style={{height: "100%", width: ((Number(row.total_exec_ms || 0) / maxTotal) * 100) + "%", background: "var(--accent)", borderRadius: 2}}/>
                      </div>
                    </td>
                    <td className="num">{fmtInt(row.calls || 0)}</td>
                    <td className="num">{fmtMs(row.total_exec_ms || 0)}</td>
                    <td className="num">{fmtMs(row.mean_exec_ms || 0)}</td>
                    <td className="num">{fmtMs(row.p95_est_exec_ms || 0)}</td>
                    <td className="num">{fmtInt(row.rows || 0)}</td>
                    <td className="num">{row.cache_hit_pct == null ? "—" : <span className={"pill " + (row.cache_hit_pct > 95 ? "ok" : row.cache_hit_pct > 85 ? "warn" : "danger")}>{row.cache_hit_pct.toFixed(1)}%</span>}</td>
                    <td><button className="btn ghost sm" onClick={function() { openStatementDetail(row); }}><Icon.Eye size={12}/>Analyze</button></td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && <tr><td colSpan="10" style={{textAlign: "center", padding: 24}} className="muted">No statements visible.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <PerfPlanDrawer
          title="Statement plan analysis"
          payload={detail ? Object.assign({}, detail, { history: selectedHistory }) : detail}
          fallback={selected}
          loading={detailLoading}
          error={detailError}
          onClose={function() { setSelected(null); setDetail(null); setDetailError(null); }}
        />
      )}
    </div>
  );
}

function PerfWaitsScreen({ lastRefresh }) {
  var dataState = React.useState({ waits: [], classes: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  React.useEffect(function() {
    setLoading(true); setError(null);
    perfJson(clusterPath("/perf/waits"))
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [lastRefresh]);
  var waits = data.waits || [];
  var total = waits.reduce(function(sum, row) { return sum + Number(row.sessions || 0); }, 0);
  var waitClassRows = (data.classes || []).map(function(row) { return { label: row.wait_event_type, value: Number(row.sessions || 0), tone: row.wait_event_type === "Client" ? "warn" : "info" }; });
  var waitAgeRows = waits.map(function(row) { return { label: row.wait_event_type + " / " + row.wait_event, value: Number(row.max_query_age_sec || 0), sub: fmtInt(row.sessions || 0) + " sessions", tone: Number(row.max_query_age_sec || 0) > 60 ? "warn" : "ok" }; });
  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data.source}>
        <span className="pill muted">current waits</span>
        {data.sampling_available === false && <span className="pill warn">pg_wait_sampling pending</span>}
      </PerfToolbar>
      <div className="section-h">Wait Events</div>
      <div className="grid-4">
        <Stat label="Sessions" value={total}/>
        <Stat label="Wait groups" value={waits.length}/>
        <Stat label="Top wait" value={waits[0] ? waits[0].wait_event_type : "—"}/>
        <Stat label="Source" value="live"/>
      </div>
      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Wait Classes" rows={waitClassRows} center={total} sub="sessions"/></div></div>
        <div className="card"><div className="bd"><BarList title="Max Wait Age" rows={waitAgeRows} valueFormatter={fmtSec}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Current Wait Breakdown <span className="meta">{waits.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Class</th><th>Event</th><th>State</th><th className="num">Sessions</th><th className="num">Max age</th><th className="num">Avg age</th></tr></thead>
            <tbody>
              {waits.map(function(row, idx) {
                return <tr key={idx}><td>{row.wait_event_type}</td><td>{row.wait_event}</td><td>{row.state}</td><td className="num">{row.sessions}</td><td className="num">{fmtSec(row.max_query_age_sec || 0)}</td><td className="num">{fmtSec(row.avg_query_age_sec || 0)}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function PerfPlanCacheScreen() {
  var queryIdState = React.useState("");
  var databaseState = React.useState("");
  var dataState = React.useState(null);
  var loadingState = React.useState(false);
  var errorState = React.useState(null);
  var queryid = queryIdState[0], setQueryid = queryIdState[1];
  var database = databaseState[0], setDatabase = databaseState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  function loadPlan() {
    if (!hbzRequired(queryid)) {
      setError("Query ID is required before analyzing a plan.");
      return;
    }
    setLoading(true); setError(null);
    perfJson(clusterPath("/perf/plans/") + encodeURIComponent(queryid.trim()), { database: database })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }
  var canAnalyzePlan = hbzRequired(queryid);
  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data ? data.source : "safe estimated EXPLAIN"}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Query ID</label>
          <input type="text" value={queryid} onChange={function(e) { setQueryid(e.target.value); }} placeholder="pg_stat_statements queryid"/>
        </div>
        <div className="field" style={{margin: 0, minWidth: 220}}>
          <label>Database</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }} placeholder="optional"/>
        </div>
        <button className="btn sm primary" onClick={loadPlan} disabled={loading || !canAnalyzePlan}><Icon.Search size={12}/>Analyze</button>
        <span className="pill muted">no EXPLAIN ANALYZE</span>
      </PerfToolbar>
      <div className="section-h">Plan Cache / Statement Analysis</div>
      <PerfPlanDetailPanel payload={data} fallback={{queryid: queryid}} loading={loading} error={error}/>
    </div>
  );
}

function PerfIndexAdvisorScreen({ lastRefresh }) {
  var databaseState = React.useState("postgres");
  var dataState = React.useState({ recommendations: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var database = databaseState[0], setDatabase = databaseState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  React.useEffect(function() {
    setLoading(true); setError(null);
    perfJson(clusterPath("/perf/index-advisor"), { database: database, limit: 75 })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [database, lastRefresh]);
  var rows = data.recommendations || [];
  var recommendationRows = phaseCountRows(rows, function(row) { return row.recommendation; }, function(key) { return key === "ok" || key === "keep_primary" ? "ok" : key === "review_unused_large" ? "danger" : "warn"; });
  var indexSizeRows = rows.slice(0, 8).map(function(row) { return { label: row.index_name, value: Number(row.size_bytes || 0), sub: row.recommendation, tone: row.recommendation === "review_unused_large" ? "danger" : "info" }; });
  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data.source || "pg_stat_user_indexes"}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Database</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }}/>
        </div>
        {data.hypopg_available === false && <span className="pill warn">hypopg pending</span>}
      </PerfToolbar>
      <div className="section-h">Index Advisor</div>
      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Recommendations" rows={recommendationRows} center={rows.length} sub="indexes"/></div></div>
        <div className="card"><div className="bd"><BarList title="Largest Review Candidates" rows={indexSizeRows} valueFormatter={fmtBytes}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Index Review Candidates <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Schema</th><th>Table</th><th>Index</th><th>Recommendation</th><th className="num">Scans</th><th className="num">Size</th><th>Flags</th></tr></thead>
            <tbody>
              {rows.map(function(row) {
                return <tr key={row.schemaname + "." + row.index_name}><td className="mono">{row.schemaname}</td><td className="mono">{row.table_name}</td><td className="mono">{row.index_name}</td><td><span className={"pill " + (row.recommendation === "ok" ? "ok" : row.recommendation === "review_unused_large" ? "danger" : "warn")}>{row.recommendation}</span></td><td className="num">{fmtInt(row.idx_scan || 0)}</td><td className="num">{fmtBytes(row.size_bytes || 0)}</td><td>{row.is_primary && <span className="pill muted">primary</span>} {row.is_unique && <span className="pill info">unique</span>}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function PerfBloatScreen({ lastRefresh }) {
  var databaseState = React.useState("");
  var dataState = React.useState({ bloat: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var database = databaseState[0], setDatabase = databaseState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  React.useEffect(function() {
    setLoading(true); setError(null);
    perfJson(clusterPath("/perf/bloat"), { database: database, limit: 75 })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [database, lastRefresh]);
  var rows = data.bloat || [];
  var deadPctRows = rows.slice(0, 8).map(function(row) { var pct = Number(row.dead_tuple_percent || 0); return { label: row.schema_name + "." + row.table_name, value: pct, sub: row.database || "current database", tone: pct > 20 ? "danger" : pct > 10 ? "warn" : "ok" }; });
  var deadTupleRows = rows.slice(0, 8).map(function(row) { return { label: row.schema_name + "." + row.table_name, value: Number(row.dead_tuples || 0), sub: fmtBytes(row.size_bytes || 0), tone: Number(row.dead_tuple_percent || 0) > 20 ? "danger" : "warn" }; });
  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data.source}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Database filter</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }} placeholder="optional database"/>
        </div>
        {data.snapshot && <span className="pill muted">snapshot {perfDate(data.snapshot.collected_at)}</span>}
      </PerfToolbar>
      <div className="section-h">Table & Index Bloat</div>
      <div className="grid-2">
        <div className="card"><div className="bd"><BarList title="Dead Tuple Percent" rows={deadPctRows} valueFormatter={function(v) { return v.toFixed(1) + "%"; }}/></div></div>
        <div className="card"><div className="bd"><BarList title="Dead Tuples" rows={deadTupleRows}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Dead Tuple Leaderboard <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Database</th><th>Schema</th><th>Table</th><th className="num">Dead %</th><th className="num">Dead tuples</th><th className="num">Live tuples</th><th className="num">Size</th><th className="num">Mod since analyze</th></tr></thead>
            <tbody>
              {rows.map(function(row, idx) {
                var deadPct = Number(row.dead_tuple_percent || 0);
                return <tr key={idx}><td className="mono">{row.database}</td><td className="mono">{row.schema_name}</td><td className="mono">{row.table_name}</td><td className="num"><span className={"pill " + (deadPct > 20 ? "danger" : deadPct > 10 ? "warn" : "ok")}>{deadPct.toFixed(2)}%</span></td><td className="num">{fmtInt(row.dead_tuples || 0)}</td><td className="num">{fmtInt(row.live_tuples || 0)}</td><td className="num">{fmtBytes(row.size_bytes || 0)}</td><td className="num">{fmtInt(row.mod_since_analyze || 0)}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function PerfVacuumScreen({ lastRefresh }) {
  var databaseState = React.useState("postgres");
  var dataState = React.useState({ vacuum: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var database = databaseState[0], setDatabase = databaseState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  React.useEffect(function() {
    setLoading(true); setError(null);
    perfJson(clusterPath("/perf/vacuum"), { database: database, limit: 75 })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [database, lastRefresh]);
  var rows = data.vacuum || [];
  var vacuumDeadRows = rows.slice(0, 8).map(function(row) { return { label: row.schemaname + "." + row.table_name, value: Number(row.dead_tuples || 0), sub: (row.dead_tuple_percent == null ? "-" : row.dead_tuple_percent.toFixed(1) + "%"), tone: Number(row.dead_tuple_percent || 0) > 20 ? "danger" : "warn" }; });
  var analyzeRows = rows.slice(0, 8).map(function(row) { return { label: row.schemaname + "." + row.table_name, value: Number(row.mod_since_analyze || 0), sub: "auto vacuum " + fmtInt(row.autovacuum_count || 0), tone: Number(row.mod_since_analyze || 0) > 10000 ? "warn" : "info" }; });
  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data.source}>
        <div className="field" style={{margin: 0, minWidth: 260}}>
          <label>Database</label>
          <input type="text" value={database} onChange={function(e) { setDatabase(e.target.value); }}/>
        </div>
      </PerfToolbar>
      <div className="section-h">Vacuum Insights</div>
      <div className="grid-2">
        <div className="card"><div className="bd"><BarList title="Dead Tuple Leaders" rows={vacuumDeadRows}/></div></div>
        <div className="card"><div className="bd"><BarList title="Analyze Churn" rows={analyzeRows}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Autovacuum Health <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Schema</th><th>Table</th><th className="num">Dead tuples</th><th className="num">Dead %</th><th className="num">Mod since analyze</th><th>Last autovacuum</th><th>Last autoanalyze</th><th className="num">Auto vacuum count</th></tr></thead>
            <tbody>
              {rows.map(function(row) {
                return <tr key={row.schemaname + "." + row.table_name}><td className="mono">{row.schemaname}</td><td className="mono">{row.table_name}</td><td className="num">{fmtInt(row.dead_tuples || 0)}</td><td className="num">{row.dead_tuple_percent == null ? "—" : row.dead_tuple_percent.toFixed(2) + "%"}</td><td className="num">{fmtInt(row.mod_since_analyze || 0)}</td><td>{perfDate(row.last_autovacuum)}</td><td>{perfDate(row.last_autoanalyze)}</td><td className="num">{fmtInt(row.autovacuum_count || 0)}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function PerfSlowQueriesScreen({ lastRefresh }) {
  var minState = React.useState(5);
  var dataState = React.useState({ slow_queries: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var selectedState = React.useState(null);
  var detailState = React.useState(null);
  var detailLoadingState = React.useState(false);
  var detailErrorState = React.useState(null);
  var minSeconds = minState[0], setMinSeconds = minState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var selected = selectedState[0], setSelected = selectedState[1];
  var detail = detailState[0], setDetail = detailState[1];
  var detailLoading = detailLoadingState[0], setDetailLoading = detailLoadingState[1];
  var detailError = detailErrorState[0], setDetailError = detailErrorState[1];
  React.useEffect(function() {
    setLoading(true); setError(null);
    perfJson(clusterPath("/perf/slow"), { min_seconds: minSeconds, limit: 100 })
      .then(function(payload) { setData(payload); setLoading(false); })
      .catch(function(err) { setError(err.message || String(err)); setLoading(false); });
  }, [minSeconds, lastRefresh]);
  function openSlowDetail(row) {
    setSelected(row);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    perfJson(clusterPath("/perf/slow/") + encodeURIComponent(row.pid))
      .then(function(payload) { setDetail(payload); setDetailLoading(false); })
      .catch(function(err) { setDetailError(err.message || String(err)); setDetailLoading(false); });
  }
  var rows = data.slow_queries || [];
  var slowWaitRows = phaseCountRows(rows, function(row) { return row.wait_event_type || "none"; }, function(key) { return key === "Client" ? "warn" : "info"; });
  var slowAgeRows = rows.slice(0, 8).map(function(row) { return { label: row.database + " / " + (row.application_name || "<unset>"), value: Number(row.query_age_sec || 0), sub: row.wait_event || "active", tone: Number(row.query_age_sec || 0) > 300 ? "warn" : "info" }; });
  return (
    <div className="page">
      <PerfToolbar loading={loading} error={error} source={data.source}>
        <div className="field" style={{margin: 0, width: 160}}>
          <label>Min seconds</label>
          <input type="number" value={minSeconds} min="0" onChange={function(e) { setMinSeconds(e.target.value); }}/>
        </div>
        {data.log_source_available === false && <span className="pill warn">log correlation pending</span>}
        <span className="pill muted">click Analyze for stats + plan rules</span>
      </PerfToolbar>
      <div className="section-h">Slow Queries</div>
      <div className="grid-2">
        <div className="card"><div className="bd"><DonutChart title="Wait Type" rows={slowWaitRows} center={rows.length} sub="queries"/></div></div>
        <div className="card"><div className="bd"><BarList title="Query Age" rows={slowAgeRows} valueFormatter={fmtSec}/></div></div>
      </div>
      <div className="card">
        <div className="hd">Active Slow Queries <span className="meta">{rows.length} rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th className="num">PID</th><th>Query ID</th><th>User</th><th>Database</th><th>Application</th><th>Wait</th><th className="num">Age</th><th>Query</th><th></th></tr></thead>
            <tbody>
              {rows.map(function(row) {
                return (
                  <tr key={row.pid}>
                    <td className="num">{row.pid}</td>
                    <td className="mono txt-xs">{row.queryid || "-"}</td>
                    <td className="mono">{row.username}</td>
                    <td className="mono">{row.database}</td>
                    <td className="mono txt-xs">{row.application_name}</td>
                    <td>{row.wait_event ? row.wait_event_type + " / " + row.wait_event : "—"}</td>
                    <td className="num">{fmtSec(row.query_age_sec || 0)}</td>
                    <td className="mono txt-xs" style={{maxWidth: 520, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{row.query}</td>
                    <td><button className="btn ghost sm" onClick={function() { openSlowDetail(row); }}><Icon.Eye size={12}/>Analyze</button></td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && <tr><td colSpan="9" style={{textAlign: "center", padding: 24}} className="muted">No active query is above the current threshold.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      {selected && (
        <PerfPlanDrawer
          title="Slow query plan analysis"
          payload={detail}
          fallback={selected}
          loading={detailLoading}
          error={detailError}
          onClose={function() { setSelected(null); setDetail(null); setDetailError(null); }}
        />
      )}
    </div>
  );
}

window.PerformanceScreen = PerformanceScreen;
window.PerformanceInsightsScreen = PerformanceInsightsScreen;
