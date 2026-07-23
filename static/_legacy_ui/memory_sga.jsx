// Memory / SGA — live Postgres memory configuration + cache efficiency.
// Reads /api/v1/memory-sga (pg_settings + pg_stat_bgwriter + pg_stat_database).

function msClusterId(cluster) {
  return (cluster && (cluster.name || cluster.id)) || "";
}

function msFetch(cluster) {
  var url = new URL("/api/v1/memory-sga", window.location.origin);
  var cid = msClusterId(cluster);
  if (cid) url.searchParams.set("cluster_id", cid);
  return fetch(url.toString(), { cache: "no-store" }).then(hbzJsonResponse);
}

function MemorySgaScreen({ cluster, lastRefresh }) {
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
    msFetch(cluster)
      .then(function (d) { if (alive) { setData(d); setLoading(false); } })
      .catch(function (e) { if (alive) { setError(e.message || String(e)); setLoading(false); } });
    return function () { alive = false; };
  }, [lastRefresh, msClusterId(cluster)]);

  if (loading && !data) {
    return <div className="page"><div className="card"><div className="bd muted">Reading memory configuration…</div></div></div>;
  }
  if (error) {
    return (
      <div className="page">
        <div className="tile-error flex-row" style={{ marginBottom: 10 }}>
          <Icon.AlertCircle size={14} /><strong style={{ marginLeft: 6 }}>Memory / SGA error</strong>
          <span className="muted txt-xs" style={{ marginLeft: 8 }}>{hbzErrorText(error)}</span>
        </div>
      </div>
    );
  }
  var d = data || {};
  if (d.available === false) {
    return <div className="page"><EmptyState title="Memory data unavailable" hint={d.error || "The cluster did not return memory settings."} icon={Icon.Cpu} source={d.source} /></div>;
  }

  var sized = d.sized || [];
  var settings = d.settings || [];
  var bg = d.bgwriter || {};
  var topDb = d.top_databases || [];
  var cacheHit = d.cache_hit_ratio;
  var dbRows = topDb.map(function (r) { return { label: r.database, value: r.blks_hit, sub: r.hit_ratio + "% hit" }; });

  return (
    <div className="page">
      <div className="grid-4">
        <Stat label="Cache hit ratio" value={cacheHit != null ? cacheHit + "%" : "-"} sub={cacheHit != null && cacheHit >= 95 ? "healthy" : "watch"} />
        <Stat label="Checkpoints (req)" value={bg.checkpoints_req != null ? fmtInt(bg.checkpoints_req) : "-"} sub={"timed " + (bg.checkpoints_timed != null ? fmtInt(bg.checkpoints_timed) : "-")} />
        <Stat label="Buffers→backend" value={bg.buffers_backend != null ? fmtInt(bg.buffers_backend) : "-"} sub="written by backends" />
        <Stat label="Memory settings" value={settings.length} sub="from pg_settings" />
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Configured memory <SourceBadge source={d.source} /></div>
          <div className="bd">
            <div className="grid-2">
              {sized.map(function (s, i) {
                return <Stat key={s.name || i} label={s.name} value={s.pretty} />;
              })}
              {!sized.length && <EmptyState icon={Icon.Cpu} title="No sizes" hint="No sized memory settings returned." />}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="hd">Cache hit ratio</div>
          <div className="bd" style={{ display: "flex", justifyContent: "center" }}>
            {cacheHit != null ? <DonutChart rows={[{ label: "Hit", value: cacheHit, tone: "ok" }, { label: "Miss", value: Math.max(0, 100 - cacheHit) }]} center={cacheHit + "%"} sub="buffer hit" size={180} valueFormatter={function (v) { return Number(v).toFixed(1) + "%"; }} /> : <EmptyState icon={Icon.Database} title="No cache stats" hint="pg_stat_database returned no rows." />}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">Cache footprint by database</div>
        <div className="bd">
          {dbRows.length ? (
            <EChart height={Math.max(160, dbRows.length * 28)} option={function () {
              var labels = dbRows.map(function (r) { return r.label; });
              var values = dbRows.map(function (r) { return r.value; });
              return {
                grid: { left: 8, right: 28, top: 8, bottom: 8, containLabel: true },
                xAxis: { type: "value", name: "blks hit", nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10 }, axisLabel: { fontSize: 10 } },
                yAxis: { type: "category", data: labels.slice().reverse(), axisLabel: { fontSize: 10 } },
                tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
                series: [{ type: "bar", data: values.slice().reverse(), barMaxWidth: 16, itemStyle: { color: vizVar("--viz-1", "#2f9fe8"), borderRadius: [0, 3, 3, 0] } }],
              };
            }} />
          ) : <EmptyState icon={Icon.Database} title="No cache stats" hint="pg_stat_database returned no rows." />}
        </div>
      </div>

      <div className="card">
        <div className="hd">All memory settings ({settings.length})</div>
        <div className="bd" style={{ overflowX: "auto" }}>
          {settings.length ? (
            <table className="table">
              <thead><tr><th>Setting</th><th>Value</th><th>Unit</th><th>Description</th></tr></thead>
              <tbody>
                {settings.map(function (s, i) {
                  return (
                    <tr key={s.name || i}>
                      <td className="mono">{s.name}</td>
                      <td className="num">{s.setting}</td>
                      <td>{s.unit || "-"}</td>
                      <td className="muted txt-xs" style={{ maxWidth: 420, whiteSpace: "normal" }}>{s.description}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : <EmptyState icon={Icon.Cpu} title="No settings" hint="pg_settings returned no memory parameters." />}
        </div>
      </div>
    </div>
  );
}

window.MemorySgaScreen = MemorySgaScreen;
