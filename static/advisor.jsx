// Advisor + Resource Health (Enterprise UI Plan, Phase D).
// AdvisorScreen: parameter recommendations (ported tuning rules via
// /advisor/parameters) + unused/bloated index findings (/perf/index-advisor,
// /perf/bloat) with guarded-job "validate & apply" buttons.
// ResourceHealthScreen: failover/switchover timeline from /replication/history
// (Patroni history + console HA jobs).

function advJson(path) {
  return fetch(clusterPath(path), { cache: "no-store" }).then(hbzJsonResponse);
}

function advApplyToneFor(status) {
  if (status === "advice") return "warn";
  if (status === "ok") return "ok";
  return "muted";
}

function advRecToneFor(severity) {
  if (severity === "critical") return "danger";
  if (severity === "warning") return "warn";
  if (severity === "info") return "info";
  return "muted";
}

function AdvisorScreen({ lastRefresh, currentUser }) {
  var ramState = useState("");
  var cpuState = useState("");
  var paramState = useState({ loaded: false, available: false, recommendations: [], summary: {} });
  var idxState = useState({ loaded: false, recommendations: [] });
  var bloatState = useState({ loaded: false, bloat: [] });
  var aiRecState = useState({ loaded: false, available: false, recommendations: [], summary: {} });
  var applyState = useState({});   // name -> {busy, msg, tone}

  var ram = ramState[0], setRam = ramState[1];
  var cpu = cpuState[0], setCpu = cpuState[1];
  var params = paramState[0], setParams = paramState[1];
  var idx = idxState[0], setIdx = idxState[1];
  var bloat = bloatState[0], setBloat = bloatState[1];
  var aiRecs = aiRecState[0], setAiRecs = aiRecState[1];
  var applied = applyState[0], setApplied = applyState[1];

  // committed capacity (only re-fetch params when the user commits the inputs)
  var capState = useState({ ram: "", cpu: "" });
  var cap = capState[0], setCap = capState[1];

  useEffect(function() {
    var alive = true;
    var qs = [];
    if (cap.ram) qs.push("ram_gib=" + encodeURIComponent(cap.ram));
    if (cap.cpu) qs.push("cpu_cores=" + encodeURIComponent(cap.cpu));
    var q = qs.length ? "?" + qs.join("&") : "";
    advJson("/advisor/parameters" + q)
      .then(function(p) { if (alive) setParams(Object.assign({ loaded: true }, p)); })
      .catch(function() { if (alive) setParams({ loaded: true, available: false, recommendations: [], summary: {} }); });
    return function() { alive = false; };
  }, [cap, lastRefresh]);

  useEffect(function() {
    var alive = true;
    advJson("/perf/index-advisor?limit=200")
      .then(function(p) { if (alive) setIdx({ loaded: true, recommendations: (p && p.recommendations) || [] }); })
      .catch(function() { if (alive) setIdx({ loaded: true, recommendations: [] }); });
    advJson("/perf/bloat?limit=50")
      .then(function(p) { if (alive) setBloat({ loaded: true, bloat: (p && p.bloat) || [] }); })
      .catch(function() { if (alive) setBloat({ loaded: true, bloat: [] }); });
    return function() { alive = false; };
  }, [lastRefresh]);

  useEffect(function() {
    var alive = true;
    fetch(clusterPath("/recommendations/run"), { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) })
      .then(hbzJsonResponse)
      .then(function(p) { if (alive) setAiRecs(Object.assign({ loaded: true }, p)); })
      .catch(function() {
        advJson("/recommendations?status=open&limit=50")
          .then(function(p) { if (alive) setAiRecs(Object.assign({ loaded: true }, p)); })
          .catch(function() { if (alive) setAiRecs({ loaded: true, available: false, recommendations: [], summary: {} }); });
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  function commitCapacity() { setCap({ ram: ram, cpu: cpu }); }

  function applyParam(rec) {
    setApplied(function(prev) { var n = Object.assign({}, prev); n[rec.name] = { busy: true, msg: "validating…", tone: "muted" }; return n; });
    fetch(clusterPath("/config/parameters/validate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: rec.name, value: rec.recommended }),
    })
      .then(hbzJsonResponse)
      .then(function(r) {
        var jobId = r && (r.job_id || r.id || (r.job && r.job.id));
        var msg = jobId ? ("dry-run job " + String(jobId).slice(0, 8) + " created") : "validated (dry-run)";
        setApplied(function(prev) { var n = Object.assign({}, prev); n[rec.name] = { busy: false, msg: msg, tone: "ok" }; return n; });
      })
      .catch(function(err) {
        setApplied(function(prev) { var n = Object.assign({}, prev); n[rec.name] = { busy: false, msg: (err && err.message) || "failed", tone: "danger" }; return n; });
      });
  }

  var recs = (params.recommendations || []).slice().sort(function(a, b) {
    var rank = { advice: 0, unknown: 1, ok: 2 };
    return (rank[a.status] - rank[b.status]) || a.name.localeCompare(b.name);
  });
  var unusedIdx = (idx.recommendations || []).filter(function(r) {
    return r.recommendation === "review_unused" || r.recommendation === "review_unused_large";
  });
  var unusedBytes = unusedIdx.reduce(function(s, r) { return s + Number(r.size_bytes || 0); }, 0);
  var topBloat = (bloat.bloat || []).slice().sort(function(a, b) {
    return Number(b.dead_tuple_percent || 0) - Number(a.dead_tuple_percent || 0);
  }).slice(0, 10);
  var aiRows = (aiRecs.recommendations || []).slice(0, 10);
  var aiSummary = aiRecs.summary || {};

  return (
    <div className="page">

      <div className="toolbar" style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 12 }}>
        <div className="field" style={{ margin: 0, width: 150 }}>
          <label>Container RAM (GiB)</label>
          <input type="number" min="0" value={ram} onChange={function(e) { setRam(e.target.value); }} placeholder="e.g. 100"/>
        </div>
        <div className="field" style={{ margin: 0, width: 150 }}>
          <label>CPU cores</label>
          <input type="number" min="0" value={cpu} onChange={function(e) { setCpu(e.target.value); }} placeholder="e.g. 32"/>
        </div>
        <button className="btn sm primary" onClick={commitCapacity}>Recompute</button>
        {!params.capacity || !params.capacity.capacity_known
          ? <span className="muted txt-xs">Supply RAM/CPU to include memory &amp; parallelism recommendations. Fixed best-practice rules show regardless.</span>
          : <span className="muted txt-xs">Memory/CPU sized for {params.capacity.ram_gib || "?"} GiB · {params.capacity.cpu_cores || "?"} cores</span>}
      </div>

      <div className="section-h">Advisor</div>
      <div className="grid-4">
        <Stat label="AI recommendations" value={aiSummary.total || aiRows.length} sub={(aiSummary.open || 0) + " open"}/>
        <Stat label="Parameter findings" value={(params.summary && params.summary.total) || 0}/>
        <Stat label="Unused indexes" value={unusedIdx.length} sub={fmtBytes(unusedBytes) + " reclaimable"}/>
        <Stat label="Bloated tables" value={topBloat.length} sub="by dead-tuple %"/>
      </div>

      <div className="card">
        <div className="hd">AI DBA recommendations <span className="meta">{aiRecs.source || "live PostgreSQL + rule engine"}</span></div>
        <div style={{ overflowX: "auto", maxHeight: 360 }}>
          <table className="tbl">
            <thead><tr><th>Severity</th><th>Category</th><th>Recommendation</th><th>Object</th><th>Action</th></tr></thead>
            <tbody>
              {aiRows.map(function(r) {
                return (
                  <tr key={r.id || r.fingerprint || r.title}>
                    <td><span className={"pill " + advRecToneFor(r.severity)}><span className="dot"/>{r.severity || "info"}</span></td>
                    <td className="txt-xs muted">{r.category}</td>
                    <td style={{ minWidth: 280 }}>
                      <strong>{r.title}</strong>
                      <div className="muted txt-xs" style={{ maxWidth: 520, whiteSpace: "normal" }}>{r.summary || r.rationale}</div>
                    </td>
                    <td className="mono txt-xs">{r.schema_name ? r.schema_name + "." : ""}{r.object_name || r.object_type || "cluster"}</td>
                    <td className="txt-xs" style={{ maxWidth: 320, whiteSpace: "normal" }}>{r.action_preview || r.action_sql || (r.action_payload && r.action_payload.preview) || r.risk_level}</td>
                  </tr>
                );
              })}
              {aiRecs.loaded && !aiRows.length && (
                <tr><td colSpan="5" className="muted" style={{ textAlign: "center", padding: 18 }}>
                  {aiRecs.available === false ? "Recommendation engine unavailable." : "No open AI DBA recommendations."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="hd">Parameter recommendations <span className="meta">{params.source || "pg_settings + tuning rules"}</span></div>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Parameter</th><th>Category</th><th>Current</th><th>Recommended</th>
                <th>Apply</th><th>Status</th><th>Rationale</th><th></th>
              </tr>
            </thead>
            <tbody>
              {recs.map(function(r) {
                var st = applied[r.name];
                return (
                  <tr key={r.name}>
                    <td className="mono txt-xs">{r.name}</td>
                    <td className="txt-xs muted">{r.category}</td>
                    <td className="mono txt-xs">{r.current}</td>
                    <td className="mono txt-xs"><strong>{r.recommended}</strong></td>
                    <td><span className={"pill " + (r.apply === "restart" ? "warn" : "muted")}><span className="dot"/>{r.apply}</span></td>
                    <td><span className={"pill " + advApplyToneFor(r.status)}><span className="dot"/>{r.status}</span></td>
                    <td className="txt-xs" style={{ maxWidth: 360, whiteSpace: "normal" }}>{r.rationale}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {r.status === "advice" && (
                        <button className="btn ghost sm" disabled={st && st.busy} onClick={function() { applyParam(r); }}>
                          <Icon.Shield size={12}/> Validate
                        </button>
                      )}
                      {st && <div className={"txt-xs " + (st.tone === "danger" ? "" : "muted")} style={st.tone === "danger" ? { color: "var(--viz-critical, #C0392B)" } : null}>{st.msg}</div>}
                    </td>
                  </tr>
                );
              })}
              {params.loaded && !recs.length && (
                <tr><td colSpan="8" className="muted" style={{ textAlign: "center", padding: 20 }}>
                  {params.available ? "No recommendations." : "Parameter advisor unavailable (no live pg_settings connection)."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Unused / large indexes <span className="meta">pg_stat_user_indexes</span></div>
          <div style={{ overflowX: "auto", maxHeight: 360 }}>
            <table className="tbl">
              <thead><tr><th>Index</th><th>Table</th><th className="num">Scans</th><th className="num">Size</th><th>Recommendation</th></tr></thead>
              <tbody>
                {unusedIdx.slice(0, 50).map(function(r, i) {
                  return (
                    <tr key={(r.index_name || "") + i}>
                      <td className="mono txt-xs">{r.index_name}</td>
                      <td className="mono txt-xs">{r.schemaname}.{r.table_name}</td>
                      <td className="num txt-xs">{fmtInt(r.idx_scan || 0)}</td>
                      <td className="num txt-xs">{fmtBytes(r.size_bytes || 0)}</td>
                      <td><span className={"pill " + (r.recommendation === "review_unused_large" ? "warn" : "muted")}><span className="dot"/>{r.recommendation}</span></td>
                    </tr>
                  );
                })}
                {idx.loaded && !unusedIdx.length && <tr><td colSpan="5" className="muted" style={{ textAlign: "center", padding: 16 }}>No unused indexes detected.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="hd">Bloated tables <span className="meta">latest object-metrics snapshot</span></div>
          <div style={{ overflowX: "auto", maxHeight: 360 }}>
            <table className="tbl">
              <thead><tr><th>Table</th><th className="num">Dead %</th><th className="num">Dead tuples</th><th className="num">Size</th></tr></thead>
              <tbody>
                {topBloat.map(function(r, i) {
                  var pct = Number(r.dead_tuple_percent || 0);
                  return (
                    <tr key={(r.table_name || r.relname || "") + i}>
                      <td className="mono txt-xs">{r.schemaname ? r.schemaname + "." : ""}{r.table_name || r.relname}</td>
                      <td className="num"><span className={"pill " + (pct > 20 ? "danger" : pct > 10 ? "warn" : "ok")}>{pct.toFixed(1)}%</span></td>
                      <td className="num txt-xs">{fmtInt(r.dead_tuples || 0)}</td>
                      <td className="num txt-xs">{fmtBytes(r.total_size_bytes || r.table_size_bytes || 0)}</td>
                    </tr>
                  );
                })}
                {bloat.loaded && !topBloat.length && <tr><td colSpan="4" className="muted" style={{ textAlign: "center", padding: 16 }}>No bloat snapshot available.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

    </div>
  );
}

function ResourceHealthScreen({ lastRefresh }) {
  var dataState = useState({ loaded: false, patroni_history: [], jobs: [], summary: {} });
  var data = dataState[0], setData = dataState[1];

  useEffect(function() {
    var alive = true;
    advJson("/replication/history?limit=100")
      .then(function(p) { if (alive) setData(Object.assign({ loaded: true }, p)); })
      .catch(function() { if (alive) setData({ loaded: true, patroni_history: [], jobs: [], summary: {} }); });
    return function() { alive = false; };
  }, [lastRefresh]);

  // Patroni history entries are arrays: [timeline, lsn, reason, timestamp, new_leader].
  var patroniRows = (data.patroni_history || []).slice().reverse().map(function(h, i) {
    var tl = Array.isArray(h) ? h[0] : (h && h.timeline);
    var reason = Array.isArray(h) ? h[2] : (h && h.reason);
    var ts = Array.isArray(h) ? h[3] : (h && h.timestamp);
    var leader = Array.isArray(h) ? h[4] : (h && h.new_leader);
    return {
      key: "p" + i,
      title: "TL " + (tl != null ? tl : "?"),
      sub: (leader ? "→ " + leader + " · " : "") + (ts ? cutoverDateSafe(ts) : ""),
      label: reason || "",
      tone: "info",
    };
  });

  var jobRows = (data.jobs || []).map(function(j, i) {
    var tone = j.state === "succeeded" ? "ok" : (j.state === "failed" || j.state === "rejected") ? "danger" : j.state === "running" ? "info" : "warn";
    return {
      key: "j" + i,
      title: j.kind,
      sub: j.state + (j.submitted_at ? " · " + cutoverDateSafe(j.submitted_at) : ""),
      label: j.reason || "",
      tone: tone,
    };
  });

  var s = data.summary || {};

  return (
    <div className="page">
      <div className="section-h">Resource health &amp; HA timeline</div>
      <div className="grid-4">
        <Stat label="Patroni events" value={s.patroni_events != null ? s.patroni_events : (data.patroni_history || []).length}/>
        <Stat label="Console HA jobs" value={s.console_ha_jobs != null ? s.console_ha_jobs : (data.jobs || []).length}/>
        <Stat label="Current timeline" value={s.latest_timeline != null ? ("TL " + s.latest_timeline) : "—"}/>
        <Stat label="Source" value="Patroni + jobs" sub={data.source || "Patroni /history, console_jobs"}/>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Patroni failover / switchover history <span className="meta">DCS /history</span></div>
          <div className="bd">
            {!data.loaded ? <div className="sk sk-block" style={{ height: 200, borderRadius: 6 }}/>
              : <TimelineStrip rows={patroniRows} emptyText="No Patroni timeline transitions recorded (or Patroni API not reachable from the console)."/>}
          </div>
        </div>
        <div className="card">
          <div className="hd">Console HA jobs <span className="meta">switchover · failover · restart · reinit</span></div>
          <div className="bd">
            {!data.loaded ? <div className="sk sk-block" style={{ height: 200, borderRadius: 6 }}/>
              : <TimelineStrip rows={jobRows} emptyText="No console-initiated HA jobs yet."/>}
          </div>
        </div>
      </div>
    </div>
  );
}

// Date formatter reused from cutover.jsx if present, else local fallback.
function cutoverDateSafe(v) {
  if (typeof cutoverDate === "function") return cutoverDate(v);
  if (!v) return "—";
  try { return new Date(v).toLocaleString("en-GB", { hour12: false }); } catch (e) { return String(v); }
}

window.AdvisorScreen = AdvisorScreen;
window.ResourceHealthScreen = ResourceHealthScreen;
