// Cluster Health Grid — live per-member + per-check status.
// Reads /api/v1/health-grid (Patroni /cluster + pg_stat_* live).

function hgClusterId(cluster) {
  return (cluster && (cluster.name || cluster.id)) || "";
}

function hgTone(status) {
  var s = String(status || "").toLowerCase();
  if (s === "ok") return "ok";
  if (s === "warn") return "warn";
  if (s === "crit") return "danger";
  return "muted";
}

function hgRoleTone(role) {
  var r = String(role || "").toLowerCase();
  if (r === "leader" || r === "primary") return "ok";
  if (r === "sync_standby") return "info";
  if (r === "replica" || r === "standby") return "muted";
  return "warn";
}

function hgFetch(cluster) {
  var url = new URL("/api/v1/health-grid", window.location.origin);
  var cid = hgClusterId(cluster);
  if (cid) url.searchParams.set("cluster_id", cid);
  return fetch(url.toString(), { cache: "no-store" }).then(hbzJsonResponse);
}

function HealthGridScreen({ cluster, lastRefresh }) {
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
    hgFetch(cluster)
      .then(function (d) { if (alive) { setData(d); setLoading(false); } })
      .catch(function (e) { if (alive) { setError(e.message || String(e)); setLoading(false); } });
    return function () { alive = false; };
  }, [lastRefresh, hgClusterId(cluster)]);

  if (loading && !data) {
    return <div className="page"><div className="card"><div className="bd muted">Checking cluster health…</div></div></div>;
  }
  if (error) {
    return (
      <div className="page">
        <div className="tile-error flex-row" style={{ marginBottom: 10 }}>
          <Icon.AlertCircle size={14} /><strong style={{ marginLeft: 6 }}>Health Grid error</strong>
          <span className="muted txt-xs" style={{ marginLeft: 8 }}>{hbzErrorText(error)}</span>
        </div>
      </div>
    );
  }
  var d = data || {};
  if (d.available === false) {
    return <div className="page"><EmptyState title="Health data unavailable" hint={d.error || "The cluster did not return health data."} icon={Icon.Activity} source={d.source} /></div>;
  }

  var summary = d.summary || {};
  var members = d.members || [];
  var checks = d.checks || [];
  var overall = d.overall || "ok";

  var statusCounts = { ok: 0, warn: 0, crit: 0, unknown: 0 };
  checks.forEach(function (c) { var s = String(c.status || "unknown").toLowerCase(); statusCounts[s] = (statusCounts[s] || 0) + 1; });
  var statusRows = [
    { label: "OK", value: statusCounts.ok, tone: "ok" },
    { label: "Warn", value: statusCounts.warn, tone: "warn" },
    { label: "Crit", value: statusCounts.crit, tone: "danger" },
    { label: "Unknown", value: statusCounts.unknown, tone: "muted" },
  ].filter(function (r) { return r.value > 0; });
  var hasLag = members.some(function (m) { return Number(m.lag_mb) > 0; });

  return (
    <div className="page">
      <div className="grid-4">
        <Stat label="Overall" value={overall.toUpperCase()} sub={<span className={"pill " + hgTone(overall)}><span className="dot" />live</span>} />
        <Stat label="Members" value={summary.members != null ? summary.members : members.length} sub="Patroni cluster" />
        <Stat label="Checks" value={summary.checks != null ? summary.checks : checks.length} sub="health probes" />
        <Stat label="Failing" value={summary.failing || 0} sub="warn + crit" />
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Check status <SourceBadge source={d.source} /></div>
          <div className="bd">
            {statusRows.length ? <DonutChart rows={statusRows} center={checks.length} sub="checks" size={180} valueFormatter={function (v) { return fmtInt(v); }} /> : <EmptyState icon={Icon.Activity} title="No checks" hint="No health probes returned." />}
          </div>
        </div>
        <div className="card">
          <div className="hd">Replication lag by member</div>
          <div className="bd">
            {members.length ? (
              <EChart height={Math.max(160, members.length * 30)} option={function () {
                var labels = members.map(function (m) { return m.name; });
                var values = members.map(function (m) { return Number(m.lag_mb) || 0; });
                return {
                  grid: { left: 8, right: 28, top: 8, bottom: 8, containLabel: true },
                  xAxis: { type: "value", name: "lag MB", nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10 }, axisLabel: { fontSize: 10 } },
                  yAxis: { type: "category", data: labels.slice().reverse(), axisLabel: { fontSize: 10 } },
                  tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
                  series: [{ type: "bar", data: values.slice().reverse(), barMaxWidth: 18, itemStyle: { color: hasLag ? vizVar("--viz-warn", "#B7791F") : vizVar("--viz-2", "#36b37e"), borderRadius: [0, 3, 3, 0] } }],
                };
              }} />
            ) : <EmptyState icon={Icon.Server} title="No members" hint="Patroni /cluster returned no members." />}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">Health checks <SourceBadge source={d.source} /></div>
        <div className="bd">
          <div className="grid-3">
            {checks.map(function (c, i) {
              return (
                <div key={i} className="card" style={{ margin: 0 }}>
                  <div className="bd flex-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <div style={{ fontWeight: 600 }}>{c.check}</div>
                      <div className="muted txt-xs">{c.detail}</div>
                    </div>
                    <span className={"pill " + hgTone(c.status)}><span className="dot" />{String(c.status).toUpperCase()}</span>
                  </div>
                </div>
              );
            })}
            {!checks.length && <EmptyState icon={Icon.Activity} title="No checks" hint="No health probes returned." />}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">Cluster members {d.patroni_error ? <span className="pill warn"><span className="dot" />patroni: {d.patroni_error}</span> : null}</div>
        <div className="bd" style={{ overflowX: "auto" }}>
          {members.length ? (
            <table className="table">
              <thead><tr><th>Member</th><th>Role</th><th>State</th><th className="num">Lag (MB)</th><th className="num">Timeline</th><th>Host</th></tr></thead>
              <tbody>
                {members.map(function (m, i) {
                  return (
                    <tr key={m.name || i}>
                      <td className="mono">{m.name}</td>
                      <td><span className={"pill " + hgRoleTone(m.role)}><span className="dot" />{m.role || "-"}</span></td>
                      <td>{m.state || "-"}</td>
                      <td className="num">{m.lag_mb}</td>
                      <td className="num">{m.timeline != null ? m.timeline : "-"}</td>
                      <td className="mono txt-xs">{m.host || "-"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : <EmptyState icon={Icon.Server} title="No members" hint="Patroni /cluster returned no members." />}
        </div>
      </div>
    </div>
  );
}

window.HealthGridScreen = HealthGridScreen;
