// Executive Overview - live data from selected cluster agent
// ES5-safe: no ??, no ?., no numeric separators

// Consolidated to the single HBZ palette defined in data.jsx.
var DB_COLORS = (typeof HBZ_CHART_PALETTE !== "undefined") ? HBZ_CHART_PALETTE : [
  "#7c3aed","#00b8d9","#f59e0b","#2f9fe8",
  "#14b8a6","#ef476f","#64748b","#ec4899",
  "#36b37e","#6366f1","#f97316","#a855f7"
];

function safeGet(obj, key, fallback) {
  if (!obj || obj[key] === undefined || obj[key] === null) return fallback;
  return obj[key];
}

function OverviewSkeleton() {
  var tiles = [0,1,2,3,4,5,6];
  var cards = [0,1,2,3];
  return (
    <div className="page">
      <div className="section-h">Loading cluster data <span className="count">…</span></div>
      <div className="tile-row">
        {tiles.map(function(i) {
          return <div key={i} className="kpi tile-skeleton"><div className="label">…</div><div className="value">…</div></div>;
        })}
      </div>
      <div className="grid-4 mt-3">
        {cards.map(function(i) {
          return <div key={i} className="card"><div className="sk sk-block" style={{height:180, borderRadius:6}}/></div>;
        })}
      </div>
      <div className="grid-3 mt-3">
        {cards.slice(0,3).map(function(i) {
          return <div key={i} className="card"><div className="sk sk-block" style={{height:220, borderRadius:6}}/></div>;
        })}
      </div>
    </div>
  );
}

function OverviewScreen(props) {
  var cluster = props.cluster;
  var timeRange = props.timeRange;
  var lastRefresh = props.lastRefresh;
  var onCommand = props.onCommand;

  var dataState       = useState(null);
  var loadingState    = useState(true);
  var errorState      = useState(null);
  var selectedDbState = useState(null);   // selected database name for drill-down
  var dbSearchState   = useState("");     // search filter for db list

  var data       = dataState[0];       var setData       = dataState[1];
  var loading    = loadingState[0];    var setLoading    = loadingState[1];
  var error      = errorState[0];      var setError      = errorState[1];
  var selectedDb = selectedDbState[0]; var setSelectedDb = selectedDbState[1];
  var dbSearch   = dbSearchState[0];   var setDbSearch   = dbSearchState[1];

  var connTrendState = useState({ available: false, points: [], loaded: false });
  var connTrend = connTrendState[0]; var setConnTrend = connTrendState[1];

  var storeTrendState = useState({ available: false, points: [], loaded: false });
  var storeTrend = storeTrendState[0]; var setStoreTrend = storeTrendState[1];

  useEffect(function() {
    var alive = true;
    fetch(uiClusterPath("overview") + "?range=" + encodeURIComponent(timeRange || "24h"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) {
        if (alive) { setData(d); setLoading(false); setError(null); }
      })
      .catch(function(e) {
        if (alive) { setError(e.message || String(e)); setLoading(false); }
      });
    return function() { alive = false; };
  }, [lastRefresh, timeRange, cluster && cluster.id]);

  // Phase 4b/4c: real connection trend. Prefer ingested metric samples; when
  // none exist yet, fall back to the Prometheus session trend (/appmon/trend,
  // pg_stat_activity_count summed across states) so the card is never empty.
  useEffect(function() {
    var alive = true;
    var rng = encodeURIComponent(timeRange || "24h");
    fetch(clusterPath("/metrics/series?metric=connections&range=" + rng), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) {
        if (d && d.available && d.points && d.points.length > 1) {
          if (alive) setConnTrend({ available: true, points: d.points, metric: d.metric, source: "samples", loaded: true });
          return null;
        }
        // Phase 4c fallback: total sessions per bucket from Prometheus.
        return fetch(clusterPath("/appmon/trend?range=" + rng), { cache: "no-store" })
          .then(hbzJsonResponse)
          .then(function(t) {
            if (!alive) return;
            var series = (t && t.series) || [];
            var n = 0;
            series.forEach(function(s) { if (s.points && s.points.length > n) n = s.points.length; });
            var pts = [];
            for (var i = 0; i < n; i++) {
              var sum = 0; var ts = null;
              for (var j = 0; j < series.length; j++) {
                var p = series[j].points[i];
                if (p) { sum += p[1]; ts = p[0]; }
              }
              if (ts != null) pts.push([ts, sum]);
            }
            setConnTrend({ available: pts.length > 1, points: pts, metric: "pg_stat_activity_count", source: "prometheus", loaded: true });
          });
      })
      .catch(function() {
        if (alive) setConnTrend({ available: false, points: [], loaded: true });
      });
    return function() { alive = false; };
  }, [lastRefresh, timeRange, cluster && cluster.id]);

  // Phase 4b: real storage-growth trend from ingested pg_inspector samples.
  useEffect(function() {
    var alive = true;
    fetch(clusterPath("/metrics/series?metric=storage_bytes&range=" + encodeURIComponent(timeRange || "24h")), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) {
        if (alive) setStoreTrend({ available: !!(d && d.available), points: (d && d.points) || [], metric: d && d.metric, loaded: true });
      })
      .catch(function() {
        if (alive) setStoreTrend({ available: false, points: [], loaded: true });
      });
    return function() { alive = false; };
  }, [lastRefresh, timeRange, cluster && cluster.id]);

  if (loading && !data) return <OverviewSkeleton/>;

  // ── Safe value extraction ────────────────────────────────────────────────
  var pg      = (data && data.pg)       || {};
  var cl      = (data && data.cluster)  || {};
  var cfg     = (data && data.config)   || {};
  var backup  = (data && data.backup)   || {};
  var capacity= cfg.capacity              || {};
  var location= cfg.location              || {};
  var schedules = backup.schedules        || [];
  var repository = backup.repository      || {};
  var pgb     = (data && data.pgbouncer)|| { pods_ready: 0, pods_total: 0 };
  var settings= pg.settings             || {};
  var conns   = pg.connections          || {};

  var pgVersionFull = pg.version || "";
  var versionMatch  = pgVersionFull.match(/PostgreSQL (\S+)/);
  var pgVersionShort = versionMatch ? versionMatch[1] : "—";

  var members     = cl.members || [];
  var patroni_ok  = cl.patroni_ok === true;

  var leaderMbr  = null;
  var standbys   = [];
  var syncStandby = null;
  for (var mi = 0; mi < members.length; mi++) {
    var m = members[mi];
    if (m.role === "leader")         leaderMbr = m;
    else                             standbys.push(m);
    if (m.role === "sync_standby")   syncStandby = m;
  }

  var leaderRunning  = leaderMbr && leaderMbr.state === "running";
  var allStreaming    = standbys.every(function(s) { return s.state === "streaming"; });
  var allHealthy     = patroni_ok && leaderRunning && allStreaming;

  var maxLagBytes = 0;
  for (var si = 0; si < standbys.length; si++) {
    var lag = standbys[si].replay_lag != null ? standbys[si].replay_lag : (standbys[si].lag || 0);
    if (lag > maxLagBytes) maxLagBytes = lag;
  }

  var totalConns  = safeGet(conns, "total",  0);
  var activeConns = safeGet(conns, "active", 0);
  var idleConns   = safeGet(conns, "idle",   0);
  var idleTxConns = safeGet(conns, "idle_in_transaction", 0);
  var maxConns    = safeGet(pg, "max_connections", null);
  var connPct     = maxConns > 0 ? Math.min(100, Math.round((totalConns / maxConns) * 100)) : null;

  var totalDbBytes  = safeGet(pg, "total_db_size_bytes", 0);
  var usedDbGiB     = totalDbBytes ? totalDbBytes / 1073741824 : 0;
  var primaryDataGiB = capacity.primary_data_available ? Number(capacity.primary_data_gib) : null;
  var replicatedDataGiB = capacity.available ? Number(capacity.replicated_data_gib || 0) : null;
  var walGiB = capacity.available ? Number(capacity.wal_gib || 0) : null;
  var repositoryGiB = capacity.available ? Number(capacity.repository_gib || 0) : null;
  var storagePct = primaryDataGiB > 0 ? Math.min(100, Math.round((usedDbGiB / primaryDataGiB) * 100)) : null;
  var storageClass = (capacity.storage_classes || []).join(", ") || "—";
  var syncNames = settings.synchronous_standby_names || "";
  var syncMode = syncNames ? "Synchronous" : (standbys.length ? "Asynchronous" : "Unavailable");

  var rawDbs = pg.databases || [];
  var databases = [];
  var excludeDbs = ["template0","template1","postgres"];
  for (var di = 0; di < rawDbs.length; di++) {
    if (excludeDbs.indexOf(rawDbs[di].datname) === -1) {
      databases.push({
        name:    rawDbs[di].datname,
        sizeGiB: rawDbs[di].size_gib || 0,
        color:   DB_COLORS[databases.length % DB_COLORS.length]
      });
    }
  }
  var pieData  = databases.map(function(d) { return { name: d.name, value: d.sizeGiB, color: d.color }; });
  var pieTotal = databases.reduce(function(s, d) { return s + d.sizeGiB; }, 0);

  var connTrendValues = (connTrend.points || []).map(function(p) { return p[1]; });
  var hasConnTrend = connTrend.available && connTrendValues.length > 1;

  var storeTrendValues = (storeTrend.points || []).map(function(p) { return p[1]; });
  var hasStoreTrend = storeTrend.available && storeTrendValues.length > 1;

  function shortName(n) { return n ? shortClusterName(n) : "—"; }

  var leaderTimeline = leaderMbr ? (leaderMbr.timeline || "—") : "—";

  return (
    <div className="page">

      {error ? (
        <div className="tile-error flex-row" style={{marginBottom:8}}>
          <Icon.AlertCircle size={14}/>
          <strong style={{marginLeft:6}}>Couldn't load data</strong>
          <span className="muted txt-xs" style={{marginLeft:8}}>{hbzErrorText(error)}</span>
        </div>
      ) : null}

      {/* ── KPI tiles ── */}
      <div className="section-h">
        Cluster properties
        <span className="count">{members.length} member{members.length !== 1 ? "s" : ""}</span>
        <SourceBadge source={(data && data.source) || "live PostgreSQL + object metrics"}/>
      </div>
      <div className="tile-row">
        <KPI color="blue"      label="PostgreSQL version" value={pgVersionShort}        sub="PostgreSQL" info/>
        <KPI color="yellow" label="Location"
             value={location.role || "—"}
             sub={location.region ? (location.region + " · " + (location.namespace || "namespace unavailable")) : "Location unavailable"} info/>
        <KPI color="purple" label="Compute"
             value={cfg.compute_available && cfg.cores != null ? (cfg.cores + " vCPU") : "Unavailable"}
             sub={cfg.compute_available && cfg.ram_gib != null ? (cfg.ram_gib + " GiB RAM") : "Resource limits not reported"} info/>
        <KPI color={allHealthy ? "green" : patroni_ok ? "red" : "slate"}
             label="Cluster health"
             value={!patroni_ok ? "Unknown" : allHealthy ? "Healthy" : "Degraded"}
             sub={allHealthy ? "All members running" : patroni_ok ? "Check members" : "Patroni API unreachable"}
             info/>
        <KPI color="deepgreen"
             label="Sync mode"
             value={syncMode}
             sub={syncNames || (settings.synchronous_commit ? ("synchronous_commit=" + settings.synchronous_commit) : "Sync configuration unavailable")}
             info/>
        <KPI color={syncStandby ? "deepgreen" : standbys.length ? "blue" : "red"}
             label="Standby"
             value={syncStandby ? "Sync" : standbys.length ? "Async" : "—"}
             sub={standbys.length ? shortName(standbys[0].name) : "No standby"}
             info/>
        <KPI color="teal"
             label="PgBouncer"
             value={pgb.pods_ready + "/" + pgb.pods_total}
             sub="pods ready" info/>
      </div>

      {/* ── Utilization ── */}
      <div className="section-h mt-2">Utilization</div>
      <div className="grid-4">

        <div className="card">
          <div className="hd">Connections <span className="meta"><Icon.Clock size={11}/> live</span></div>
          <div className="bd" style={{paddingTop:6}}>
            {connPct == null ? (
              <EmptyState icon={Icon.Activity} title="Connection limit unavailable" hint="max_connections was not returned by PostgreSQL."/>
            ) : (
              <div><Gauge value={connPct} label="Used" thresholds={[60, 85]}/><div className="muted txt-xs" style={{textAlign:"center",marginTop:6}}>{totalConns} of {maxConns} max_connections</div></div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="hd">Replication <span className="meta"><Icon.GitBranch size={11}/> Patroni</span></div>
          <div className="bd" style={{paddingTop:6}}>
            <Gauge value={allHealthy ? 0 : patroni_ok ? 80 : 50}
                   label={allHealthy ? "Healthy" : "Issue"}
                   thresholds={[1, 50]}/>
            <div className="muted txt-xs" style={{textAlign:"center",marginTop:6}}>
              {allHealthy ? "All members in sync" : patroni_ok ? "Issue detected" : "Patroni unreachable"}
            </div>
          </div>
        </div>

        <div className="card">
          <div className="hd">Storage usage <span className="meta">{primaryDataGiB == null ? "unavailable" : (usedDbGiB.toFixed(2) + "/" + primaryDataGiB + " GiB")}</span></div>
          <div className="bd" style={{paddingTop:6}}>
            {storagePct == null ? <EmptyState icon={Icon.Database} title="Capacity unavailable" hint="Primary data PVC capacity was not returned."/> : <div><Gauge value={storagePct} label="Used" thresholds={[70, 85]}/><div className="muted txt-xs" style={{textAlign:"center",marginTop:6}}>Logical database size vs primary data PVC</div></div>}
          </div>
        </div>

        <div className="card">
          <div className="hd">PVC capacity <span className="meta">{storageClass}</span></div>
          <div className="bd">
            {primaryDataGiB == null ? <EmptyState icon={Icon.Database} title="PVC inventory unavailable" hint="Capacity is not inferred from defaults."/> : (
              <div className="grid-2">
                <Stat label="Primary data" value={primaryDataGiB} unit="GiB" sub="usable data capacity"/>
                <Stat label="Replicated data" value={replicatedDataGiB} unit="GiB" sub="all data PVCs"/>
                <Stat label="WAL" value={walGiB} unit="GiB" sub="all WAL PVCs"/>
                <Stat label="Repository" value={repositoryGiB || "—"} unit={repositoryGiB ? "GiB" : ""} sub={repositoryGiB ? "repository PVCs" : "not a PVC or unavailable"}/>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Performance trends (Phase A — ECharts over ingested samples) ── */}
      <div className="section-h mt-2">
        Performance trends
        <span className="count">{timeRange || "24h"}</span>
      </div>
      <div className="grid-2">

        <div className="card">
          <div className="hd">
            Connections
            <span className="meta">
              <Icon.Activity size={11}/>{" "}
              {hasConnTrend ? (connTrend.source === "prometheus" ? "Prometheus · sessions" : "ingested samples") : "trend"}
            </span>
          </div>
          <div className="bd">
            {hasConnTrend ? (
              <EChart height={210} option={function() {
                var base = hbzEChartsBase();
                var s = hbzAreaSeries("Sessions", connTrend.points, 1);
                var lim = Number(maxConns) || 0;
                if (lim > 0) {
                  var critC = vizVar("--viz-critical", "#C0392B");
                  var warnC = vizVar("--viz-warn", "#B7791F");
                  s.markLine = { silent: true, symbol: "none", data: [{ yAxis: lim }],
                    lineStyle: { color: critC, type: "dashed", width: 1 },
                    label: { formatter: "max_connections " + lim, position: "insideEndTop", color: critC, fontSize: 10 } };
                  s.markArea = { silent: true, itemStyle: { color: vizAlpha(warnC, 0.1) },
                    data: [[{ yAxis: Math.round(lim * 0.8) }, { yAxis: lim }]] };
                }
                return {
                  series: [s],
                  yAxis: Object.assign({}, base.yAxis, { minInterval: 1,
                    name: "Active sessions",
                    nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10, align: "left" },
                    nameGap: 8 }),
                  tooltip: Object.assign({}, base.tooltip, {
                    valueFormatter: function(v) { return v == null ? "\u2014" : Math.round(v).toLocaleString() + (Math.abs(v) === 1 ? " connection" : " connections"); }
                  })
                };
              }}/>
            ) : connTrend.loaded ? (
              <EmptyState icon={Icon.Activity} title="No connection history yet"
                          hint="The trend appears once metric samples have been ingested for this cluster."/>
            ) : (
              <div className="sk sk-block" style={{height:210, borderRadius:6}}/>
            )}
          </div>
        </div>

        <div className="card">
          <div className="hd">
            Storage growth
            <span className="meta"><Icon.Database size={11}/> pg_inspector · table totals</span>
          </div>
          <div className="bd">
            {hasStoreTrend ? (
              <EChart height={210} option={function() {
                var base = hbzEChartsBase();
                var s = hbzAreaSeries("Total size", storeTrend.points, 3);
                var cap = (Number(primaryDataGiB) || 0) * 1073741824;
                if (cap > 0) {
                  var critC = vizVar("--viz-critical", "#C0392B");
                  var warnC = vizVar("--viz-warn", "#B7791F");
                  s.markLine = { silent: true, symbol: "none", data: [{ yAxis: cap }],
                    lineStyle: { color: critC, type: "dashed", width: 1 },
                    label: { formatter: "capacity", position: "insideEndTop", color: critC, fontSize: 10 } };
                  s.markArea = { silent: true, itemStyle: { color: vizAlpha(warnC, 0.1) },
                    data: [[{ yAxis: Math.round(cap * 0.8) }, { yAxis: cap }]] };
                }
                return {
                  series: [s],
                  yAxis: Object.assign({}, base.yAxis, {
                    name: "Database size",
                    nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10, align: "left" },
                    nameGap: 8,
                    axisLabel: Object.assign({}, base.yAxis.axisLabel, {
                      formatter: function(v) { return fmtBytes(v); }
                    }),
                    min: function(extent) { return Math.floor(extent.min * 0.999); }
                  }),
                  tooltip: Object.assign({}, base.tooltip, {
                    valueFormatter: function(v) { return fmtBytes(v); }
                  })
                };
              }}/>
            ) : storeTrend.loaded ? (
              <EmptyState icon={Icon.Database} title="No storage history yet"
                          hint="Growth appears once pg_inspector snapshots have been ingested for this cluster."/>
            ) : (
              <div className="sk sk-block" style={{height:210, borderRadius:6}}/>
            )}
          </div>
        </div>
      </div>

      {/* ── Stats ── */}
      <div className="section-h mt-2">Storage &amp; connections</div>
      <div className="grid-4">
        <Stat label="Primary data capacity" info="Primary pod data PVC"
              value={primaryDataGiB == null ? "—" : primaryDataGiB} unit={primaryDataGiB == null ? "" : "GiB"} sub={storageClass}/>
        <Stat label="Total DB logical size" info="pg_database_size()"
              value={fmtBytes(totalDbBytes)} sub={rawDbs.length + " databases"}
              chart={hasStoreTrend ? <Sparkline data={storeTrendValues} color="var(--viz-3)" fill="var(--viz-area-3)"/> : null}/>
        <Stat label="Shared buffers" info="shared_buffers GUC"
              value={settings.shared_buffers_gib || "—"} unit="GiB" sub="PostgreSQL buffer pool"/>
        <Stat label="Active connections" info="pg_stat_activity"
              value={activeConns} sub={totalConns + " total · " + maxConns + " max"}
              chart={hasConnTrend ? <Sparkline data={connTrendValues}/> : null}/>
      </div>

      <div className="grid-4">
        <Stat label="Idle connections"         value={idleConns}    sub="state = idle"/>
        <Stat label="Idle-in-transaction"       value={idleTxConns}  sub="idle in transaction"/>
        <Stat label="Max WAL size"              value={settings.max_wal_size_gib || "—"} unit="GiB" sub="max_wal_size"/>
        <Stat label="Archive mode" value={settings.archive_mode === "on" ? "Enabled" : settings.archive_mode === "off" ? "Off" : "—"} sub={repository.available ? (repository.source || "pgBackRest") : "Repository unavailable"}/>
      </div>

      {/* ── Members + Connections + DB pie ── */}
      <div className="grid-3">

        <div className="card">
          <div className="hd">
            Patroni members
            <span className="meta">{members.length} total · timeline {leaderTimeline}</span>
          </div>
          <div style={{overflowX:"auto"}}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Member</th><th>Role</th><th>State</th>
                  <th className="num">Replay lag</th><th className="num">LSN</th>
                </tr>
              </thead>
              <tbody>
                {members.map(function(m) {
                  var rolePill = m.role === "leader" ? "ok" : m.role === "sync_standby" ? "info" : "muted";
                  var statePill = (m.state === "running" || m.state === "streaming") ? "ok" : "danger";
                  var roleLabel = m.role === "leader" ? "Leader" : m.role === "sync_standby" ? "Sync Standby" : m.role;
                  var lagVal = m.role === "leader" ? "—" : fmtBytes(m.replay_lag != null ? m.replay_lag : (m.lag || 0));
                  return (
                    <tr key={m.name}>
                      <td className="mono txt-xs">{shortName(m.name)}</td>
                      <td><span className={"pill " + rolePill}><span className="dot"/>{roleLabel}</span></td>
                      <td><span className={"pill " + statePill}>{m.state}</span></td>
                      <td className="num">{lagVal}</td>
                      <td className="num mono txt-xs">{m.lsn || "—"}</td>
                    </tr>
                  );
                })}
                {members.length === 0 ? (
                  <tr><td colSpan="5" className="muted" style={{textAlign:"center",padding:20}}>
                    <Icon.AlertCircle size={13}/> Patroni API not reachable
                  </td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
          {!patroni_ok ? (
            <div className="tile-error" style={{margin:"8px 12px 12px"}}>
              <Icon.AlertCircle size={13}/> Cannot reach Patroni API
            </div>
          ) : null}
        </div>

        <div className="card">
          <div className="hd">Connections <span className="meta"><Icon.Clock size={11}/> live</span></div>
          <div className="bd">
            <div className="grid-3">
              <Stat label="Active"     value={activeConns}/>
              <Stat label="Idle"       value={idleConns}/>
              <Stat label="Idle-in-Tx" value={idleTxConns}/>
            </div>
            <div className="flex-row mt-3" style={{justifyContent:"space-between"}}>
              <span className="txt-xs muted">max_connections</span>
              <strong className="txt-xs">{maxConns}</strong>
            </div>
            <div style={{height:6,background:"var(--surface-3)",borderRadius:3,marginTop:4,overflow:"hidden"}}>
              <div style={{width:connPct+"%",height:"100%",background:"var(--hbz-green)",borderRadius:3}}/>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="hd" style={{flexWrap:"wrap",gap:8}}>
            <span>Database sizes</span>
            <span className="meta">
              {selectedDb
                ? <span style={{color:"#7c3aed",fontWeight:700}}>{selectedDb}</span>
                : databases.length + " databases"}
            </span>
            <span className="meta" style={{marginLeft:"auto"}}>
              Total: {fmtBytes(pieTotal * 1073741824)}
            </span>
          </div>

          {databases.length > 0 ? (
            <div className="bd" style={{padding:"10px 14px"}}>

              {/* Search + clear filter row */}
              <div className="flex-row" style={{marginBottom:10,gap:6}}>
                <div style={{flex:1,position:"relative"}}>
                  <input
                    type="text"
                    placeholder="Search database..."
                    value={dbSearch}
                    onChange={function(e) { setDbSearch(e.target.value); setSelectedDb(null); }}
                    style={{
                      width:"100%", padding:"5px 28px 5px 8px",
                      border:"1px solid #c4ccc7", borderRadius:6,
                      fontSize:12, fontFamily:"inherit", outline:"none",
                      background:"#f8f9fa", color:"#212529"
                    }}
                  />
                  {dbSearch && (
                    <span onClick={function() { setDbSearch(""); }}
                          style={{position:"absolute",right:6,top:"50%",transform:"translateY(-50%)",
                                  cursor:"pointer",color:"#6c757d",fontSize:14,lineHeight:1}}>×</span>
                  )}
                </div>
                {selectedDb && (
                  <button className="btn sm" onClick={function() { setSelectedDb(null); setDbSearch(""); }}>
                    Show all
                  </button>
                )}
              </div>

              {/* Pie + list layout */}
              <div style={{display:"flex",gap:14,alignItems:"flex-start"}}>

                {/* Pie — highlight selected slice */}
                <div style={{flexShrink:0}}>
                  <PieChart
                    data={selectedDb
                      ? databases.map(function(d) { return { name:d.name, value: d.name===selectedDb ? d.sizeGiB : 0.001, color: d.name===selectedDb ? d.color : "#e9ecef" }; })
                      : pieData
                    }
                    size={140}
                  />
                  {selectedDb && (
                    <div style={{textAlign:"center",marginTop:4,fontSize:11,color:"#7c3aed",fontWeight:700}}>
                      {(function() {
                        var sel = databases.filter(function(d) { return d.name===selectedDb; })[0];
                        return sel ? fmtBytes(sel.sizeGiB * 1073741824) : "";
                      })()}
                    </div>
                  )}
                </div>

                {/* Database list with progress bars */}
                <div style={{flex:1,display:"flex",flexDirection:"column",gap:2,overflow:"auto",maxHeight:200}}>
                  {(function() {
                    var filtered = databases.filter(function(d) {
                      if (selectedDb) return d.name === selectedDb;
                      if (dbSearch) return d.name.toLowerCase().indexOf(dbSearch.toLowerCase()) >= 0;
                      return true;
                    });
                    var maxSize = databases.length > 0 ? Math.max.apply(null, databases.map(function(d) { return d.sizeGiB; })) : 1;

                    if (filtered.length === 0) {
                      return <div className="muted txt-xs" style={{padding:"8px 0"}}>No databases match "{dbSearch}"</div>;
                    }

                    return filtered.map(function(d) {
                      var isSelected = selectedDb === d.name;
                      var barPct = maxSize > 0 ? Math.max(2, (d.sizeGiB / maxSize) * 100) : 2;
                      return (
                        <div key={d.name}
                             onClick={function() { setSelectedDb(isSelected ? null : d.name); setDbSearch(""); }}
                             style={{
                               padding:"6px 8px",
                               borderRadius:6,
                               cursor:"pointer",
                               border: isSelected ? "1px solid "+d.color : "1px solid transparent",
                               background: isSelected ? "rgba(3,102,74,.06)" : "transparent",
                               transition:"background .12s, border .12s"
                             }}>
                          {/* Name row */}
                          <div className="flex-row" style={{justifyContent:"space-between",marginBottom:4}}>
                            <span className="flex-row" style={{gap:6}}>
                              <span style={{width:10,height:10,background:d.color,borderRadius:3,display:"inline-block",flexShrink:0,boxShadow:"0 1px 3px rgba(0,0,0,.2)"}}/>
                              <span style={{fontSize:12,fontFamily:"var(--font-mono)",fontWeight: isSelected ? 700 : 500, color: isSelected ? "#7c3aed" : "#212529"}}>
                                {d.name}
                              </span>
                            </span>
                            <span style={{fontSize:12,fontWeight:700,color: isSelected ? d.color : "#2d3748",whiteSpace:"nowrap"}}>
                              {fmtBytes(d.sizeGiB * 1073741824)}
                            </span>
                          </div>
                          {/* Progress bar */}
                          <div style={{height:4,background:"#e9ecef",borderRadius:2,overflow:"hidden"}}>
                            <div style={{
                              height:"100%",
                              width: barPct + "%",
                              background: isSelected
                                ? "linear-gradient(90deg,"+d.color+",#00a07a)"
                                : d.color,
                              borderRadius:2,
                              opacity: isSelected ? 1 : 0.7,
                              transition:"width .4s"
                            }}/>
                          </div>
                        </div>
                      );
                    });
                  })()}
                </div>
              </div>
            </div>
          ) : (
            <div className="bd muted txt-xs">No database data</div>
          )}
        </div>
      </div>

      {/* ── WAL + Patroni config + Status ── */}
      <div className="grid-3">

        <div className="card">
          <div className="hd">WAL &amp; archiving <span className="meta"><Icon.Cloud size={11}/> {repository.available ? (repository.source || "pgBackRest") : "repository unavailable"}</span></div>
          <div className="bd">
            <div className="grid-2">
              <Stat label="Archive mode"  value={settings.archive_mode === "on" ? "Enabled" : settings.archive_mode === "off" ? "Off" : "Unavailable"}/>
              <Stat label="WAL level"     value={settings.wal_level || "—"}/>
              <Stat label="Max WAL size"  value={settings.max_wal_size_gib || "—"} unit="GiB"/>
              <Stat label="Current LSN"   value={pg.current_lsn || "—"}/>
            </div>
            <div className="mt-3">
              <div className="txt-xs muted" style={{marginBottom:4}}>Current WAL file</div>
              <div className="mono txt-xs" style={{wordBreak:"break-all",color:"var(--fg)"}}>{pg.current_wal_file || "—"}</div>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="hd">Patroni configuration</div>
          <div className="bd">
            <div className="grid-2">
              <Stat label="Sync commit" value={settings.synchronous_commit || "—"}/>
              <Stat label="Timeline"    value={"" + leaderTimeline}/>
              <Stat label="Leader"      value={shortName(cl.leader)}/>
              <Stat label="Shared bufs" value={settings.shared_buffers_gib || "—"} unit="GiB"/>
            </div>
            <div className="mt-3">
              <div className="txt-xs muted" style={{marginBottom:4}}>Synchronous standby names</div>
              <div className="mono txt-xs"
                   style={{padding:"4px 6px",background:"var(--surface-2)",borderRadius:3,border:"1px solid var(--border)",wordBreak:"break-all"}}>
                {settings.synchronous_standby_names || "—"}
              </div>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="hd">Cluster status</div>
          <div className="bd">
            {allHealthy ? (
              <div>
                <div className="pill ok" style={{marginBottom:12}}><span className="dot"/>All members healthy</div>
                <div className="grid-2">
                  <Stat label="Members"   value={members.length}/>
                  <Stat label="Standbys"  value={standbys.length}/>
                  <Stat label="Max lag"   value={fmtBytes(maxLagBytes)} sub="replay"/>
                  <Stat label="Databases" value={rawDbs.length}/>
                </div>
              </div>
            ) : (
              <div className="tile-error" style={{marginBottom:12}}>
                <div className="flex-row" style={{marginBottom:6}}>
                  <Icon.AlertCircle size={14}/>
                  <strong style={{marginLeft:4}}>{!patroni_ok ? "Patroni API unreachable" : "Issue detected"}</strong>
                </div>
                <div className="txt-xs">{leaderMbr ? ("Leader: " + leaderMbr.state) : "Leader not found"}</div>
              </div>
            )}
            <div className="section-h" style={{fontSize:13,marginTop:12}}>Backup schedules</div>
            <div style={{display:"flex",flexDirection:"column",gap:4,marginTop:6}}>
              {schedules.map(function(b, i) {
                return (
                  <div key={i} className="flex-row" style={{padding:"5px 0",borderBottom:"1px solid var(--divider)"}}>
                    <span className="led" style={{background:b.enabled === false ? "var(--muted)" : "var(--ok)"}}/>
                    <span className="txt-sm" style={{flex:1,marginLeft:8}}>{b.name || b.type || b.kind || "Schedule"}</span>
                    <span className="muted txt-xs">{b.cron || "Cron unavailable"}</span>
                  </div>
                );
              })}
              {!schedules.length ? <div className="muted txt-xs">{backup.schedules_available === false ? "Backup schedules unavailable" : "No backup schedules configured"}</div> : null}
            </div>
            <div className="flex-row mt-3" style={{padding:"6px 0",borderTop:"1px solid var(--divider)"}}>
              <span className="txt-xs muted">Repository</span>
              <span className="mono txt-xs" style={{marginLeft:"auto"}}>{repository.available ? (repository.descriptor || repository.stanza || "pgBackRest") : "Unavailable"}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.OverviewScreen = OverviewScreen;
