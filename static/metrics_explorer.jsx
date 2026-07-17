// Metrics Explorer (Enterprise UI Plan, Phase C) — Azure-style pick-a-metric.
// Metric picker (from /metrics/catalog) + aggregation + range → ECharts
// gradient-area over /metrics/series, with summary stat tiles. Honest empty
// state when a metric has no ingested samples (catalog reports has_data).

const MX_RANGES = ["1h", "24h", "7d", "30d"];
const MX_AGGS = [
  ["avg", "Average"],
  ["max", "Max"],
  ["min", "Min"],
  ["sum", "Sum"],
];

// Stable color per metric key so a metric keeps its hue across selections.
const MX_COLOR_INDEX = {
  connections: 1,
  cpu_usage: 2,
  memory_bytes: 3,
  storage_bytes: 3,
  table_bytes: 1,
  index_bytes: 7,
  index_object_bytes: 7,
  db_tables: 4,
  db_indexes: 4,
  db_schemas: 4,
  db_views: 4,
  db_functions: 4,
  live_tuples: 8,
  dead_tuples: 6,
  dead_tuple_percent: 6,
  seq_scans: 5,
  idx_scans: 2,
  mod_since_analyze: 6,
  heap_blocks_read: 7,
  heap_blocks_hit: 1,
  idx_blocks_read: 7,
  idx_blocks_hit: 1,
  replication_slot_wal_bytes: 6,
  replication_slots: 4,
  wal_bytes: 6,
  checkpoint_seconds: 5,
};

function mxFormatValue(unit, v) {
  if (v == null || isNaN(v)) return "—";
  if (unit === "bytes") return fmtBytes(v);
  if (unit === "percent") return Number(v).toFixed(2) + "%";
  return fmtInt(Math.round(v));
}

function MetricsExplorerScreen({ lastRefresh }) {
  var catState = useState({ metrics: [], loaded: false });
  var metricState = useState("connections");
  var aggState = useState("avg");
  var rangeState = useState("24h");
  var seriesState = useState({ loaded: false, available: false, points: [] });
  var entitiesState = useState({ loaded: false, available: false, entities: [] });
  var forecastState = useState({ loaded: false, available: false });
  var loadingState = useState(true);
  var errorState = useState(null);

  var catalog = catState[0], setCatalog = catState[1];
  var metric = metricState[0], setMetric = metricState[1];
  var agg = aggState[0], setAgg = aggState[1];
  var range = rangeState[0], setRange = rangeState[1];
  var series = seriesState[0], setSeries = seriesState[1];
  var entities = entitiesState[0], setEntities = entitiesState[1];
  var forecast = forecastState[0], setForecast = forecastState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  // Catalog → default to the first metric that actually has data.
  useEffect(function() {
    var alive = true;
    fetch(clusterPath("/metrics/catalog"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(p) {
        if (!alive) return;
        var metrics = (p && p.metrics) || [];
        setCatalog({ metrics: metrics, loaded: true });
        if (metrics.length) {
          var withData = metrics.filter(function(m) { return m.has_data; });
          var pick = withData[0] || metrics[0];
          setMetric(pick.key);
          if (pick.agg) setAgg(pick.agg);
        }
      })
      .catch(function() { if (alive) setCatalog({ metrics: [], loaded: true }); });
    return function() { alive = false; };
  }, [lastRefresh]);

  // Series whenever metric / agg / range change.
  useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    var url = clusterPath("/metrics/series?metric=" + encodeURIComponent(metric) +
                          "&range=" + encodeURIComponent(range) +
                          "&agg=" + encodeURIComponent(agg));
    fetch(url, { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(p) {
        if (!alive) return;
        setSeries({
          loaded: true,
          available: !!(p && p.available),
          points: (p && p.points) || [],
          metric: p && p.metric,
          bucket_seconds: p && p.bucket_seconds,
          source_table: p && p.source_table,
        });
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setSeries({ loaded: true, available: false, points: [] });
        setError(err && (err.message || String(err)));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [metric, agg, range, lastRefresh]);

  useEffect(function() {
    var alive = true;
    var dimension = metric.indexOf("index") >= 0 ? "index"
      : metric.indexOf("replication_slot") >= 0 || metric.indexOf("slot") >= 0 ? "slot"
      : metric.indexOf("db_") === 0 || metric === "connections" ? "database"
      : "table";
    setEntities({ loaded: false, available: false, entities: [] });
    fetch(clusterPath("/metrics/entities?metric=" + encodeURIComponent(metric) +
                      "&range=" + encodeURIComponent(range) +
                      "&dimension=" + encodeURIComponent(dimension) +
                      "&limit=10"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(p) {
        if (!alive) return;
        setEntities({
          loaded: true,
          available: !!(p && p.available),
          entities: (p && p.entities) || [],
          dimension: p && p.dimension,
          metric: p && p.metric,
        });
      })
      .catch(function() { if (alive) setEntities({ loaded: true, available: false, entities: [] }); });

    setForecast({ loaded: false, available: false });
    fetch(clusterPath("/metrics/forecast?metric=" + encodeURIComponent(metric) +
                      "&range=" + encodeURIComponent(range) +
                      "&horizon_days=30"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(p) {
        if (!alive) return;
        setForecast(Object.assign({ loaded: true }, p || {}));
      })
      .catch(function() { if (alive) setForecast({ loaded: true, available: false }); });
    return function() { alive = false; };
  }, [metric, range, lastRefresh]);

  var meta = useMemo(function() {
    var m = (catalog.metrics || []).filter(function(x) { return x.key === metric; })[0];
    return m || { key: metric, label: metric, unit: "count", group: "Metric", hint: "" };
  }, [catalog, metric]);

  var unit = meta.unit || "count";
  var pts = series.points || [];
  var values = pts.map(function(p) { return p[1]; });
  var latest = values.length ? values[values.length - 1] : null;
  var minV = values.length ? Math.min.apply(null, values) : null;
  var maxV = values.length ? Math.max.apply(null, values) : null;
  var avgV = values.length ? values.reduce(function(a, b) { return a + b; }, 0) / values.length : null;

  // Group catalog entries for the picker.
  var groups = useMemo(function() {
    var byGroup = {};
    var order = [];
    (catalog.metrics || []).forEach(function(m) {
      var g = m.group || "Metric";
      if (!byGroup[g]) { byGroup[g] = []; order.push(g); }
      byGroup[g].push(m);
    });
    return order.map(function(g) { return { name: g, items: byGroup[g] }; });
  }, [catalog]);

  var colorIndex = MX_COLOR_INDEX[metric] || 1;

  return (
    <div className="page">
      <div className="toolbar" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <div className="seg">
          {MX_RANGES.map(function(r) {
            return <button key={r} className={"btn sm " + (range === r ? "primary" : "")}
                           onClick={function() { setRange(r); }}>{r}</button>;
          })}
        </div>
        <div className="seg">
          {MX_AGGS.map(function(a) {
            return <button key={a[0]} className={"btn sm " + (agg === a[0] ? "primary" : "")}
                           onClick={function() { setAgg(a[0]); }}>{a[1]}</button>;
          })}
        </div>
        <div className="grow" style={{ flex: 1 }}/>
        {loading && <span className="muted txt-xs"><span className="dot"/> loading…</span>}
        {error && <span className="pill danger txt-xs"><span className="dot"/> {error}</span>}
        {series.source_table && !loading && <SourceBadge source={series.source_table}/>}
      </div>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>

        {/* Metric picker */}
        <div className="card" style={{ width: 260, flex: "0 0 260px", minWidth: 220 }}>
          <div className="hd">Metrics <span className="meta">{(catalog.metrics || []).length}</span></div>
          <div className="bd" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {!catalog.loaded && <div className="sk sk-block" style={{ height: 180, borderRadius: 6 }}/>}
            {catalog.loaded && !groups.length && (
              <div className="muted txt-xs">No metric catalog available.</div>
            )}
            {groups.map(function(g) {
              return (
                <div key={g.name}>
                  <div className="section-h" style={{ margin: "0 0 6px" }}>{g.name}</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    {g.items.map(function(m) {
                      var active = m.key === metric;
                      return (
                        <button key={m.key}
                                className={"mx-metric-btn" + (active ? " active" : "")}
                                title={m.hint || m.label}
                                onClick={function() { setMetric(m.key); if (m.agg) setAgg(m.agg); }}
                                style={{
                                  display: "flex", alignItems: "center", gap: 8,
                                  textAlign: "left", width: "100%", padding: "7px 9px",
                                  border: "1px solid " + (active ? "var(--accent)" : "transparent"),
                                  background: active ? "var(--surface-2)" : "transparent",
                                  borderRadius: 6, cursor: "pointer", color: "var(--fg)",
                                }}>
                          <span className="dot" style={{ background: m.has_data ? "var(--viz-ok, #1A7F4B)" : "var(--fg-dim)" }}/>
                          <span style={{ flex: 1, fontSize: 12.5 }}>{m.label}</span>
                          {!m.has_data && <span className="muted txt-xs">no data</span>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Chart + summary */}
        <div style={{ flex: 1, minWidth: 360, display: "flex", flexDirection: "column", gap: 12 }}>

          <div className="grid-4">
            <Stat label="Latest" value={mxFormatValue(unit, latest)} sub={meta.label}/>
            <Stat label={"Average (" + range + ")"} value={mxFormatValue(unit, avgV)}/>
            <Stat label="Min" value={mxFormatValue(unit, minV)}/>
            <Stat label="Max" value={mxFormatValue(unit, maxV)}/>
          </div>

          <div className="grid-3">
            <Stat label="30d forecast" value={mxFormatValue(unit, forecast.available ? forecast.forecast : null)}
                  sub={forecast.available ? "linear projection" : (forecast.reason || "insufficient history")}/>
            <Stat label="Daily change" value={mxFormatValue(unit, forecast.available ? forecast.slope_per_day : null)}
                  sub="slope per day"/>
            <Stat label="Entity leaders" value={fmtInt((entities.entities || []).length)}
                  sub={(entities.dimension || "entity") + " dimension"}/>
          </div>

          <div className="card">
            <div className="hd">
              {meta.label}
              <span className="meta">
                {meta.hint || meta.key} · {agg} · {range}
                {series.metric ? " · " + series.metric : ""}
              </span>
            </div>
            <div className="bd">
              {!series.loaded ? (
                <div className="sk sk-block" style={{ height: 320, borderRadius: 6 }}/>
              ) : series.available && pts.length ? (
                <EChart height={340} option={function() {
                  var base = hbzEChartsBase();
                  return {
                    series: [hbzAreaSeries(meta.label, pts, colorIndex)],
                    yAxis: Object.assign({}, base.yAxis, {
                      axisLabel: Object.assign({}, base.yAxis.axisLabel, {
                        formatter: function(v) { return mxFormatValue(unit, v); },
                      }),
                      minInterval: unit === "bytes" ? 0 : 1,
                    }),
                    tooltip: Object.assign({}, base.tooltip, {
                      valueFormatter: function(v) { return mxFormatValue(unit, v); },
                    }),
                  };
                }}/>
              ) : (
                <EmptyState icon={Icon.Activity}
                            title={meta.has_data === false ? "No samples for this metric yet" : "No data in this range"}
                            hint="The Metrics Explorer plots real ingested object-metrics samples. A metric appears once pg_inspector / exporter snapshots have been ingested for this cluster and range."/>
              )}
            </div>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="hd">Top Entities <span className="meta">{entities.dimension || "entity"}</span></div>
              <div style={{overflowX: "auto"}}>
                <table className="tbl">
                  <thead><tr><th>Entity</th><th className="num">Latest</th><th className="num">Avg</th><th className="num">Max</th><th className="num">Samples</th></tr></thead>
                  <tbody>
                    {(entities.entities || []).map(function(row) {
                      return (
                        <tr key={row.entity}>
                          <td className="mono txt-xs">{row.entity}</td>
                          <td className="num">{mxFormatValue(unit, row.latest)}</td>
                          <td className="num">{mxFormatValue(unit, row.avg_value)}</td>
                          <td className="num">{mxFormatValue(unit, row.max_value)}</td>
                          <td className="num">{fmtInt(row.samples || 0)}</td>
                        </tr>
                      );
                    })}
                    {entities.loaded && !(entities.entities || []).length && (
                      <tr><td colSpan="5" style={{textAlign: "center", padding: 18}} className="muted">No entity-level samples for this metric.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="card">
              <div className="hd">Projection <span className="meta">next 30 days</span></div>
              <div className="bd">
                {forecast.available && (forecast.projected_points || []).length ? (
                  <EChart height={220} option={function() {
                    var base = hbzEChartsBase();
                    return {
                      series: [hbzAreaSeries("Forecast", forecast.projected_points || [], colorIndex)],
                      yAxis: Object.assign({}, base.yAxis, {
                        axisLabel: Object.assign({}, base.yAxis.axisLabel, {
                          formatter: function(v) { return mxFormatValue(unit, v); },
                        }),
                      }),
                      tooltip: Object.assign({}, base.tooltip, {
                        valueFormatter: function(v) { return mxFormatValue(unit, v); },
                      }),
                    };
                  }}/>
                ) : (
                  <EmptyState icon={Icon.TrendingUp} title="No projection yet" hint={forecast.reason || "At least two historical buckets are required."}/>
                )}
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}

window.MetricsExplorerScreen = MetricsExplorerScreen;
