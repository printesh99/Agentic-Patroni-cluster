// Live object-metrics page backed by the FastAPI/PostgreSQL snapshot store.

function apiUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v != null && v !== "" && v !== "ALL") url.searchParams.set(k, v);
  });
  return url.toString();
}

async function apiJson(path, params = {}) {
  const response = await fetch(apiUrl(path, params), { cache: "no-store" });
  return hbzJsonResponse(response);
}

function compactNumber(value) {
  if (value == null) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function compactPct(value) {
  if (value == null) return "—";
  return `${Number(value).toFixed(2)}%`;
}

function snapshotAge(snapshot) {
  if (!snapshot || !snapshot.collected_at) return "No snapshot";
  const at = new Date(snapshot.collected_at);
  return at.toLocaleString("en-GB", { hour12: false });
}

function ObjectMetricsScreen({ lastRefresh }) {
  var selectedClusterId = activeClusterId();
  var selectedCluster = activeCluster();
  const [region, setRegion] = React.useState("ALL");
  const [database, setDatabase] = React.useState("ALL");
  const [state, setState] = React.useState({
    loading: true,
    error: null,
    regions: [],
    databases: [],
    overview: null,
    tables: [],
    indexes: [],
    publications: [],
    subscriptions: [],
    slots: [],
  });
  const [forecast, setForecast] = React.useState({ loaded: false, available: false });

  React.useEffect(() => {
    if (selectedClusterId !== "uat") {
      setState(s => ({ ...s, loading: false, error: null }));
      return;
    }
    let alive = true;
    setState(s => ({ ...s, loading: true, error: null }));
    Promise.all([
      apiJson("/api/regions"),
      apiJson("/api/databases", { region }),
      apiJson("/api/overview", { region, database }),
      apiJson("/api/tables", { region, database, limit: 30 }),
      apiJson("/api/indexes", { region, database, limit: 20 }),
      apiJson("/api/pubsub", { region, database }),
      apiJson("/api/slots"),
    ])
      .then(([regions, databases, overview, tables, indexes, pubsub, slots]) => {
        if (!alive) return;
        setState({
          loading: false,
          error: null,
          regions: regions.regions || [],
          databases: databases.databases || [],
          overview,
          tables: tables.tables || [],
          indexes: indexes.indexes || [],
          publications: pubsub.publications || [],
          subscriptions: pubsub.subscriptions || [],
          slots: slots.slots || [],
        });
      })
      .catch(error => {
        if (!alive) return;
        setState(s => ({ ...s, loading: false, error: error.message || String(error) }));
      });
    return () => { alive = false; };
  }, [region, database, lastRefresh, selectedClusterId]);

  React.useEffect(() => {
    if (selectedClusterId !== "uat") return;
    let alive = true;
    const threshold = Number(selectedCluster.totalStorageGiB || 0) > 0
      ? Number(selectedCluster.totalStorageGiB) * 1024 * 1024 * 1024
      : null;
    setForecast({ loaded: false, available: false });
    apiJson(clusterPath("/metrics/forecast"), {
      metric: "storage_bytes",
      range: "30d",
      horizon_days: 30,
      threshold_value: threshold,
    })
      .then(p => { if (alive) setForecast({ loaded: true, ...p }); })
      .catch(() => { if (alive) setForecast({ loaded: true, available: false }); });
    return () => { alive = false; };
  }, [lastRefresh, selectedClusterId, selectedCluster.totalStorageGiB]);

  if (selectedClusterId !== "uat") {
    return (
      <div className="page">
        <div className="section-h">Object Metrics <span className="count">collector not attached</span></div>
        <div className="risk-banner info">
          <Icon.Database size={16}/>
          <div>{selectedCluster.name || selectedClusterId} is selected. This module reads pg_inspector snapshots from the UAT console store, so it is hidden here to avoid showing stale UAT data as live environment data.</div>
        </div>
      </div>
    );
  }

  const totals = state.overview?.totals || {};
  const snapshot = state.overview?.snapshot;
  const storageRows = [
    { label: "Table", value: Number(totals.table_bytes || 0), tone: "ok" },
    { label: "Index", value: Number(totals.index_bytes || 0), tone: "info" },
  ];
  const objectRows = [
    { label: "Tables", value: Number(totals.tables || 0), tone: "ok" },
    { label: "Indexes", value: Number(totals.indexes || 0), tone: "info" },
    { label: "Views", value: Number(totals.views || 0), tone: "teal" },
    { label: "Functions", value: Number(totals.functions || 0), tone: "purple" },
    { label: "Sequences", value: Number(totals.sequences || 0), tone: "warn" },
  ];
  const topTableRows = state.tables.slice(0, 8).map(t => ({
    label: t.schemaname + "." + t.relname,
    value: Number(t.total_size_bytes || 0),
    sub: t.datname,
    tone: "ok"
  }));
  const deadRows = state.tables.slice(0, 8).map(t => ({
    label: t.schemaname + "." + t.relname,
    value: Number(t.dead_tuples || 0),
    sub: compactPct(t.dead_tuple_percent),
    tone: Number(t.dead_tuple_percent || 0) > 20 ? "danger" : Number(t.dead_tuple_percent || 0) > 5 ? "warn" : "info"
  }));
  const topIndexRows = state.indexes.slice(0, 8).map(i => ({
    label: i.indexname,
    value: Number(i.index_size_bytes || 0),
    sub: i.schemaname + "." + i.tablename,
    tone: i.is_valid !== "true" ? "danger" : i.is_primary === "true" ? "ok" : "info"
  }));
  const slotRows = state.slots.map(s => ({
    label: s.slot_name,
    value: Math.max(1, Number(s.retained_wal_bytes || 0)),
    sub: fmtBytes(Number(s.retained_wal_bytes || 0)),
    tone: Number(s.active || 0) > 0 ? "ok" : "warn"
  }));

  return (
    <div className="page">
      <div className="card">
        <div className="bd" style={{display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap"}}>
          <div className="field" style={{margin: 0, minWidth: 160}}>
            <label>Region</label>
            <select value={region} onChange={e => { setRegion(e.target.value); setDatabase("ALL"); }}>
              <option value="ALL">ALL</option>
              {state.regions.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div className="field" style={{margin: 0, minWidth: 260}}>
            <label>Database</label>
            <select value={database} onChange={e => setDatabase(e.target.value)}>
              <option value="ALL">ALL</option>
              {state.databases.map(d => (
                <option key={d.datname} value={d.datname}>{d.region} / {d.category} / {d.datname}</option>
              ))}
            </select>
          </div>
          <div className="grow"/>
          <SourceBadge source="object-metrics" detail="Latest object_metrics snapshot and metric_samples tables"/>
          <div className="pill info"><span className="dot"/>Snapshot {snapshotAge(snapshot)}</div>
          {state.loading && <div className="pill muted"><Icon.Loader size={12}/>Loading</div>}
          {state.error && <div className="pill danger"><span className="dot"/>{state.error}</div>}
        </div>
      </div>

      <div className="section-h">Catalog Scope</div>
      <div className="grid-4">
        <Stat label="Databases" value={compactNumber(totals.databases)}/>
        <Stat label="Schemas" value={compactNumber(totals.schemas)}/>
        <Stat label="Tables" value={compactNumber(totals.tables)}/>
        <Stat label="Indexes" value={compactNumber(totals.indexes)}/>
      </div>
      <div className="grid-4">
        <Stat label="Views" value={compactNumber(totals.views)}/>
        <Stat label="Materialized views" value={compactNumber(totals.materialized_views)}/>
        <Stat label="Functions" value={compactNumber(totals.functions)}/>
        <Stat label="Triggers" value={compactNumber(totals.triggers)}/>
      </div>

      <div className="section-h mt-2">Object Storage & Churn</div>
      <div className="grid-4">
        <Stat label="Table bytes" value={fmtBytes(totals.table_bytes || 0)}/>
        <Stat label="Index bytes" value={fmtBytes(totals.index_bytes || 0)}/>
        <Stat label="Total relation bytes" value={fmtBytes(totals.total_relation_bytes || 0)}/>
        <Stat label="Dead tuples" value={compactNumber(totals.dead_tuples)}/>
      </div>

      <div className="card">
        <div className="hd">Capacity Forecast <span className="meta">30 day projection · object metrics</span></div>
        <div className="bd">
          <div className="grid-4">
            <Stat label="Current storage" value={forecast.available ? fmtBytes(forecast.current || 0) : "—"} sub="latest historical bucket"/>
            <Stat label="Projected storage" value={forecast.available ? fmtBytes(forecast.forecast || 0) : "—"} sub="30 days"/>
            <Stat label="Daily growth" value={forecast.available ? fmtBytes(forecast.slope_per_day || 0) : "—"} sub="linear slope"/>
            <Stat label="Threshold breach" value={forecast.breach_at ? new Date(forecast.breach_at).toLocaleDateString("en-GB") : "none"} sub={forecast.threshold_value ? fmtBytes(forecast.threshold_value) : "no threshold"}/>
          </div>
          {forecast.available && (forecast.projected_points || []).length ? (
            <EChart height={220} option={function() {
              var base = hbzEChartsBase();
              return {
                series: [hbzAreaSeries("Projected storage", forecast.projected_points || [], 3)],
                yAxis: Object.assign({}, base.yAxis, {
                  name: "Projected size",
                  nameTextStyle: { color: vizVar("--fg-dim", "#6c757d"), fontSize: 10, align: "left" },
                  nameGap: 8,
                  axisLabel: Object.assign({}, base.yAxis.axisLabel, { formatter: function(v) { return fmtBytes(v); } }),
                }),
                tooltip: Object.assign({}, base.tooltip, { valueFormatter: function(v) { return fmtBytes(v); } }),
              };
            }}/>
          ) : (
            <EmptyState icon={Icon.TrendingUp} title="No capacity projection yet" hint={forecast.reason || "At least two storage history buckets are required."}/>
          )}
        </div>
      </div>

      <div className="grid-4">
        <div className="card"><div className="bd"><DonutChart title="Storage Split" rows={storageRows} center={fmtBytes((totals.table_bytes || 0) + (totals.index_bytes || 0))} sub="table + index" valueFormatter={fmtBytes}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Object Mix" rows={objectRows} center={compactNumber(Number(totals.tables || 0) + Number(totals.indexes || 0))} sub="tables + indexes"/></div></div>
        <div className="card"><div className="bd"><BarList title="Largest Tables" rows={topTableRows} valueFormatter={fmtBytes}/></div></div>
        <div className="card"><div className="bd"><BarList title="Largest Indexes" rows={topIndexRows} valueFormatter={fmtBytes}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card"><div className="bd"><BarList title="Dead Tuple Leaders" rows={deadRows}/></div></div>
        <div className="card"><div className="bd"><BarList title="Replication Slot WAL" rows={slotRows} valueFormatter={fmtBytes}/></div></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Largest Tables <span className="meta">{state.tables.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Database</th><th>Table</th>
                  <th className="num">Total</th><th className="num">Heap</th><th className="num">Index</th>
                  <th className="num">Live</th><th className="num">Dead %</th><th className="num">Seq scans</th><th className="num">Idx scans</th>
                </tr>
              </thead>
              <tbody>
                {state.tables.map(t => (
                  <tr key={`${t.datname}.${t.schemaname}.${t.relname}`}>
                    <td><span className="mono">{t.datname}</span><div className="muted txt-xs">{t.region} / {t.category}</div></td>
                    <td className="mono">{t.schemaname}.{t.relname}</td>
                    <td className="num">{fmtBytes(t.total_size_bytes || 0)}</td>
                    <td className="num">{fmtBytes(t.table_size_bytes || 0)}</td>
                    <td className="num">{fmtBytes(t.index_size_bytes || 0)}</td>
                    <td className="num">{compactNumber(t.live_tuples)}</td>
                    <td className="num">{compactPct(t.dead_tuple_percent)}</td>
                    <td className="num">{compactNumber(t.seq_scan)}</td>
                    <td className="num">{compactNumber(t.idx_scan)}</td>
                  </tr>
                ))}
                {!state.loading && state.tables.length === 0 && (
                  <tr><td colSpan="9" style={{textAlign: "center", padding: 24}} className="muted">No table metrics in the selected scope.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="hd">Largest Indexes <span className="meta">{state.indexes.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Database</th><th>Index</th><th>Table</th>
                  <th className="num">Size</th><th className="num">Scans</th><th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {state.indexes.map(i => (
                  <tr key={`${i.datname}.${i.schemaname}.${i.indexname}`}>
                    <td><span className="mono">{i.datname}</span><div className="muted txt-xs">{i.region} / {i.category}</div></td>
                    <td className="mono">{i.indexname}</td>
                    <td className="mono">{i.schemaname}.{i.tablename}</td>
                    <td className="num">{fmtBytes(i.index_size_bytes || 0)}</td>
                    <td className="num">{compactNumber(i.idx_scan)}</td>
                    <td>
                      {i.is_primary === "true" && <span className="pill ok">primary</span>}
                      {i.is_unique === "true" && <span className="pill info">unique</span>}
                      {i.is_valid !== "true" && <span className="pill danger">invalid</span>}
                    </td>
                  </tr>
                ))}
                {!state.loading && state.indexes.length === 0 && (
                  <tr><td colSpan="6" style={{textAlign: "center", padding: 24}} className="muted">No index metrics in the selected scope.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Publication / Subscription Inventory <span className="meta">{state.publications.length} pub · {state.subscriptions.length} sub</span></div>
          <div className="bd">
            <div className="grid-2">
              <Stat label="Publications" value={compactNumber(totals.publications)}/>
              <Stat label="Subscriptions" value={compactNumber(totals.subscriptions)}/>
            </div>
            <div style={{overflowX: "auto", marginTop: 10}}>
              <table className="tbl">
                <thead><tr><th>Type</th><th>Database</th><th>Name</th><th>Details</th></tr></thead>
                <tbody>
                  {state.publications.map(p => (
                    <tr key={`pub-${p.datname}-${p.pubname}`}>
                      <td><span className="pill info">publication</span></td>
                      <td className="mono">{p.datname}</td>
                      <td className="mono">{p.pubname}</td>
                      <td className="txt-xs">insert={p.insert} update={p.update} delete={p.delete} truncate={p.truncate}</td>
                    </tr>
                  ))}
                  {state.subscriptions.map(s => (
                    <tr key={`sub-${s.datname}-${s.subname}`}>
                      <td><span className="pill ok">subscription</span></td>
                      <td className="mono">{s.datname}</td>
                      <td className="mono">{s.subname}</td>
                      <td className="txt-xs">enabled={s.enabled} publications={s.publications || "—"}</td>
                    </tr>
                  ))}
                  {!state.loading && state.publications.length + state.subscriptions.length === 0 && (
                    <tr><td colSpan="4" style={{textAlign: "center", padding: 18}} className="muted">No publication or subscription objects in this scope.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="hd">Replication Slots <span className="meta">{state.slots.length} slots</span></div>
          <div className="bd">
            <Stat label="Slot count" value={compactNumber(totals.replication_slots)}/>
            <div style={{overflowX: "auto", marginTop: 10}}>
              <table className="tbl">
                <thead><tr><th>Slot</th><th>Type</th><th>Database</th><th>Active</th><th className="num">Retained WAL</th></tr></thead>
                <tbody>
                  {state.slots.map(s => (
                    <tr key={s.slot_name}>
                      <td className="mono">{s.slot_name}</td>
                      <td>{s.slot_type}</td>
                      <td className="mono">{s.database || "—"}</td>
                      <td>{Number(s.active || 0) > 0 ? <span className="pill ok">active</span> : <span className="pill muted">inactive</span>}</td>
                      <td className="num">{fmtBytes(s.retained_wal_bytes || 0)}</td>
                    </tr>
                  ))}
                  {!state.loading && state.slots.length === 0 && (
                    <tr><td colSpan="5" style={{textAlign: "center", padding: 18}} className="muted">No replication slots found.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
