// Application Monitoring — Replication & Integration + DBA Evidence (AM5)
// Logical subscription inventory, replication slot retained WAL, logical worker
// sessions, plus DBA evidence (analyze backlog, seq scans, big indexes, locks).
// Backed by /appmon/replication and /appmon/dba-evidence. ES5-safe.

function AppMonReplScreen(props) {
  var lastRefresh = props.lastRefresh;

  var replState = useState(null); var repl = replState[0]; var setRepl = replState[1];
  var dbaState = useState(null);  var dba = dbaState[0];   var setDba = dbaState[1];
  var loadState = useState(true); var loading = loadState[0]; var setLoading = loadState[1];

  useEffect(function() {
    var alive = true;
    setLoading(true);
    Promise.all([
      fetch(clusterPath("/appmon/replication"), { cache: "no-store" }).then(hbzJsonResponse).catch(function() { return { available: false }; }),
      fetch(clusterPath("/appmon/dba-evidence?limit=25"), { cache: "no-store" }).then(hbzJsonResponse).catch(function() { return { available: false }; })
    ]).then(function(res) {
      if (!alive) return;
      setRepl(res[0]); setDba(res[1]); setLoading(false);
    });
    return function() { alive = false; };
  }, [lastRefresh]);

  var r = repl || {};
  var e = dba || {};

  var slotRows = (r.slots || []).map(function(s) {
    return { label: s.slot, value: s.retained_wal_bytes, sub: s.database + (s.type ? (" · " + s.type) : "") };
  });
  var totalWal = 0; (r.slots || []).forEach(function(s) { totalWal += (s.retained_wal_bytes || 0); });
  var totalWorkers = 0; (r.workers || []).forEach(function(w) { totalWorkers += (w.sessions || 0); });
  var enabledSubs = 0; (r.subscriptions || []).forEach(function(s) { if (s.enabled) enabledSubs++; });
  var totalLocks = 0; (e.locks || []).forEach(function(l) { totalLocks += (l.value || 0); });

  var modRows = (e.mod_since_analyze || []).map(function(t) {
    return { label: (t.schema ? t.schema + "." : "") + t.relation, value: t.value, sub: t.datname };
  });
  var seqRows = (e.seq_scans || []).map(function(t) {
    return { label: (t.schema ? t.schema + "." : "") + t.relation, value: t.value, sub: t.datname };
  });
  var lockRows = (e.locks || []).map(function(l) { return { label: l.mode, value: l.value }; });

  return (
    <div className="page">

      {/* KPI tiles */}
      <div className="tile-row">
        <KPI color="deepgreen" label="Subscriptions"  value={fmtInt((r.subscriptions || []).length)} sub={enabledSubs + " enabled"}/>
        <KPI color="navy"      label="Replication slots" value={fmtInt((r.slots || []).length)} sub="logical/physical"/>
        <KPI color={totalWal >= 5 * 1024 * 1024 * 1024 ? "red" : "orange"} label="Retained WAL" value={fmtBytes(totalWal)} sub="across all slots"/>
        <KPI color="teal"      label="Logical workers" value={fmtInt(totalWorkers)} sub="walsender / apply"/>
        <KPI color="green"     label="Locks held"     value={fmtInt(totalLocks)} sub="pg_locks_count"/>
      </div>

      {/* subscriptions */}
      <div className="card mt-3">
        <div className="hd">Logical replication subscriptions <span className="meta">{(r.subscriptions || []).length}</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Database</th><th>Subscription</th><th>Publications</th><th>State</th></tr></thead>
            <tbody>
              {(r.subscriptions || []).map(function(s, i) {
                return (
                  <tr key={i}>
                    <td className="mono txt-xs">{s.datname}</td>
                    <td className="txt-xs">{s.subscription}</td>
                    <td className="txt-xs muted">{s.publications || "—"}</td>
                    <td><span className={"pill " + (s.enabled ? "ok" : "muted")}>{s.enabled ? "enabled" : "disabled"}</span></td>
                  </tr>
                );
              })}
              {!(r.subscriptions || []).length ? (
                <tr><td colSpan="4" className="muted" style={{textAlign: "center", padding: 18}}>No logical subscriptions reported.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      {/* slots + workers */}
      <div className="grid-2 mt-3">
        <div className="card">
          <div className="hd">Replication slots — retained WAL</div>
          <div className="bd">
            <BarList rows={slotRows} emptyText="No replication slots reported."
                     valueFormatter={function(v) { return fmtBytes(v); }}/>
          </div>
        </div>
        <div className="card">
          <div className="hd">Logical worker sessions <span className="meta">{(r.workers || []).length} groups</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Database</th><th>Backend type</th><th>Application</th><th className="num">Sessions</th></tr></thead>
              <tbody>
                {(r.workers || []).map(function(w, i) {
                  return (
                    <tr key={i}>
                      <td className="mono txt-xs">{w.datname}</td>
                      <td className="txt-xs">{w.backend_type}</td>
                      <td className="txt-xs muted">{w.application || "—"}</td>
                      <td className="num">{fmtInt(w.sessions)}</td>
                    </tr>
                  );
                })}
                {!(r.workers || []).length ? (
                  <tr><td colSpan="4" className="muted" style={{textAlign: "center", padding: 18}}>No logical replication workers active.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* DBA evidence */}
      <div className="section-title mt-4" style={{fontWeight: 700, fontSize: 13, textTransform: "uppercase", color: "var(--muted)", letterSpacing: ".04em"}}>
        DBA Evidence
      </div>

      <div className="grid-2 mt-2">
        <div className="card">
          <div className="hd">Tables modified since last analyze <span className="meta">analyze backlog</span></div>
          <div className="bd">
            <BarList rows={modRows} emptyText="No analyze backlog reported." valueFormatter={fmtInt}/>
          </div>
        </div>
        <div className="card">
          <div className="hd">High sequential-scan tables</div>
          <div className="bd">
            <BarList rows={seqRows} emptyText="No sequential-scan counts reported." valueFormatter={fmtInt}/>
          </div>
        </div>
      </div>

      <div className="grid-2 mt-3">
        <div className="card">
          <div className="hd">Largest indexes <span className="meta">{(e.indexes || []).length}</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Index</th><th>Table</th><th>Database</th><th className="num">Size</th></tr></thead>
              <tbody>
                {(e.indexes || []).slice(0, 25).map(function(x, i) {
                  return (
                    <tr key={i}>
                      <td className="mono txt-xs">{x.schema ? (x.schema + ".") : ""}{x.index}</td>
                      <td className="txt-xs">{x.table || "—"}</td>
                      <td className="txt-xs muted">{x.datname}</td>
                      <td className="num">{fmtBytes(x.value)}</td>
                    </tr>
                  );
                })}
                {!(e.indexes || []).length ? (
                  <tr><td colSpan="4" className="muted" style={{textAlign: "center", padding: 18}}>No index sizes reported.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="hd">Lock counts by mode</div>
          <div className="bd">
            <BarList rows={lockRows} emptyText="No locks currently held." valueFormatter={fmtInt}/>
          </div>
        </div>
      </div>

    </div>
  );
}

window.AppMonReplScreen = AppMonReplScreen;
