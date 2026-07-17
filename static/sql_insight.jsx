// SQL Insight — live Active Session History.
// Replaces the previous static "representative sample" build. Reads
// /api/v1/insight/ash (pg_stat_activity live snapshot + pg_stat_statements).

function siClusterId(cluster) {
  return (cluster && (cluster.name || cluster.id)) || "";
}

function siFetchAsh(cluster) {
  var url = new URL("/api/v1/insight/ash", window.location.origin);
  var cid = siClusterId(cluster);
  if (cid) url.searchParams.set("cluster_id", cid);
  url.searchParams.set("limit", "200");
  return fetch(url.toString(), { cache: "no-store" }).then(hbzJsonResponse);
}

function SqlInsightScreen({ cluster, lastRefresh }) {
  var dataState = useState(null);
  var loadingState = useState(true);
  var errorState = useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  useEffect(function () {
    var alive = true;
    setLoading(true);
    setError(null);
    siFetchAsh(cluster)
      .then(function (d) { if (alive) { setData(d); setLoading(false); } })
      .catch(function (e) { if (alive) { setError(e.message || String(e)); setLoading(false); } });
    return function () { alive = false; };
  }, [lastRefresh, siClusterId(cluster)]);

  if (loading && !data) {
    return <div className="page"><div className="card"><div className="bd muted">Sampling active sessions…</div></div></div>;
  }
  if (error) {
    return (
      <div className="page">
        <div className="tile-error flex-row" style={{ marginBottom: 10 }}>
          <Icon.AlertCircle size={14} /><strong style={{ marginLeft: 6 }}>SQL Insight error</strong>
          <span className="muted txt-xs" style={{ marginLeft: 8 }}>{hbzErrorText(error)}</span>
        </div>
      </div>
    );
  }
  var d = data || {};
  if (d.available === false) {
    return <div className="page"><EmptyState title="Active Session History unavailable" hint={d.error || "The cluster did not return session data."} icon={Icon.Activity} source={d.source} /></div>;
  }

  var summary = d.summary || {};
  var waitRows = (d.wait_profile || []).map(function (w) {
    return { label: w.wait_event_type, value: w.sessions };
  });
  var sessions = d.active_sessions || [];
  var topSql = d.top_sql || [];

  return (
    <div className="page">
      <div className="grid-4">
        <Stat label="Active sessions" value={summary.active_sessions != null ? summary.active_sessions : sessions.length} sub="non-idle right now" />
        <Stat label="Distinct wait types" value={summary.distinct_wait_types || waitRows.length} sub="across active work" />
        <Stat label="Top wait" value={summary.top_wait || "-"} sub="dominant wait class" />
        <Stat label="pg_stat_statements" value={d.statements_available ? "on" : "off"} sub={d.statements_available ? "top SQL live" : "extension not present"} />
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Wait profile <SourceBadge source={d.source} /></div>
          <div className="bd">
            {waitRows.length ? <DonutChart rows={waitRows} center={sessions.length} sub="active sessions" size={180} valueFormatter={function (v) { return fmtInt(v); }} /> : <EmptyState icon={Icon.Activity} title="No active sessions" hint="The database is idle — nothing waiting or on CPU." />}
          </div>
        </div>
        <div className="card">
          <div className="hd">Top SQL by total time</div>
          <div className="bd" style={{ overflowX: "auto" }}>
            {topSql.length ? (
              <EChart height={Math.max(180, Math.min(topSql.length, 10) * 26)} option={function () {
                var top = topSql.slice(0, 10);
                var labels = top.map(function (r) { return (r.query || "").slice(0, 40); });
                var values = top.map(function (r) { return r.total_ms; });
                return {
                  grid: { left: 8, right: 28, top: 8, bottom: 8, containLabel: true },
                  xAxis: { type: "value", name: "total ms", nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10 }, axisLabel: { fontSize: 10 } },
                  yAxis: { type: "category", data: labels.slice().reverse(), axisLabel: { fontSize: 10 } },
                  tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
                  series: [{ type: "bar", data: values.slice().reverse(), barMaxWidth: 16, itemStyle: { color: vizVar("--viz-1", "#2f9fe8"), borderRadius: [0, 3, 3, 0] } }],
                };
              }} />
            ) : null}
            {topSql.length ? (
              <table className="table" style={{ marginTop: 8 }}>
                <thead><tr><th>Query</th><th className="num">Calls</th><th className="num">Total ms</th><th className="num">Mean ms</th><th className="num">Rows</th></tr></thead>
                <tbody>
                  {topSql.map(function (r, i) {
                    return (
                      <tr key={r.queryid || i}>
                        <td className="mono txt-xs" style={{ maxWidth: 360, whiteSpace: "normal" }}>{r.query}</td>
                        <td className="num">{fmtInt(r.calls)}</td>
                        <td className="num">{fmtInt(r.total_ms)}</td>
                        <td className="num">{r.mean_ms}</td>
                        <td className="num">{fmtInt(r.rows)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <EmptyState icon={Icon.Database} title="No statement stats" hint="pg_stat_statements is not installed or has no rows yet." />}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">Active sessions ({sessions.length})</div>
        <div className="bd" style={{ overflowX: "auto" }}>
          {sessions.length ? (
            <table className="table">
              <thead><tr><th>User</th><th>Database</th><th>Wait</th><th>State</th><th className="num">Active</th><th>Query</th></tr></thead>
              <tbody>
                {sessions.map(function (s, i) {
                  return (
                    <tr key={i}>
                      <td>{s.user || "-"}</td>
                      <td>{s.database || "-"}</td>
                      <td><span className="pill muted"><span className="dot" />{s.wait_event_type}{s.wait_event ? " / " + s.wait_event : ""}</span></td>
                      <td>{s.state}</td>
                      <td className="num">{s.active_sec}s</td>
                      <td className="mono txt-xs" style={{ maxWidth: 420, whiteSpace: "normal" }}>{s.query}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : <EmptyState icon={Icon.Activity} title="No active sessions" hint="Nothing is executing right now." />}
        </div>
      </div>
    </div>
  );
}

window.SqlInsightScreen = SqlInsightScreen;
