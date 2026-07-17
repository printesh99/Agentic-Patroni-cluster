// Application Monitoring — shared per-domain detail view (AM2–AM4)
// Generic panel set backed by /appmon/domain/{domain}. Screens pass the set of
// domain tabs (and optional schema quick-filters) and reuse this. ES5-safe.

function AppMonDomainEmpty(props) {
  return (
    <EmptyState icon={Icon.Database}
                title={props.title || "No data for this domain"}
                hint={props.hint || "Prometheus returned no pginspector samples for the selected domain and database."}/>
  );
}

function fmtPct(n) {
  if (n == null) return "—";
  return (Math.round(Number(n) * 10) / 10).toLocaleString() + "%";
}

function appmonStatePill(s) {
  if (s === "active") return "ok";
  if (s && s.indexOf("idle in transaction") === 0) return "warn";
  if (s === "idle") return "muted";
  return "info";
}

var APPMON_GRAFANA = "https://uat-pgo18-grafana-uat-pgcluster-uae.apps.ocp-dr.habibbank.local/d/uat-app-monitoring";

function AppMonDomainView(props) {
  var domains = props.domains || [];
  var schemaChips = props.schemaChips || null;
  var lastRefresh = props.lastRefresh;
  var timeRange = props.timeRange;

  var domState = useState(domains.length ? domains[0].slug : "");
  var domain = domState[0];        var setDomain = domState[1];
  var dbState = useState("");       var db = dbState[0];        var setDb = dbState[1];
  var schState = useState("");      var schema = schState[0];   var setSchema = schState[1];
  var filtState = useState(null);   var filters = filtState[0]; var setFilters = filtState[1];
  var dataState = useState(null);   var data = dataState[0];    var setData = dataState[1];
  var loadState = useState(true);   var loading = loadState[0]; var setLoading = loadState[1];

  // Filter options (databases) once per refresh.
  useEffect(function() {
    var alive = true;
    fetch(clusterPath("/appmon/filters"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) { if (alive) setFilters(d); })
      .catch(function() { if (alive) setFilters({ databases: [] }); });
    return function() { alive = false; };
  }, [lastRefresh]);

  // Domain detail on refresh / domain / db / schema / range change.
  useEffect(function() {
    var alive = true;
    setLoading(true);
    var qs = "?range=" + encodeURIComponent(timeRange || "24h") + "&limit=25";
    if (db) qs += "&database=" + encodeURIComponent(db);
    if (schema) qs += "&schema=" + encodeURIComponent(schema);
    fetch(clusterPath("/appmon/domain/" + domain + qs), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) { if (alive) { setData(d); setLoading(false); } })
      .catch(function() { if (alive) { setData({ available: false }); setLoading(false); } });
    return function() { alive = false; };
  }, [lastRefresh, domain, db, schema, timeRange]);

  var d = data || {};
  var unavailable = data && data.available === false;

  function relSub(r) { return r.datname + (r.schema ? (" · " + r.schema) : ""); }
  var sizeRows = (d.top_by_size || []).map(function(r) { return { label: r.relation, value: r.value, sub: relSub(r) }; });
  var rowRows = (d.top_by_rows || []).map(function(r) { return { label: r.relation, value: r.value, sub: relSub(r) }; });
  var churnRows = (d.dml_churn || []).map(function(r) { return { label: r.relation, value: r.value, sub: r.datname }; });

  var totalSessions = 0; (d.sessions || []).forEach(function(s) { totalSessions += (s.sessions || 0); });
  var totalChurn = 0; (d.dml_churn || []).forEach(function(c) { totalChurn += (c.value || 0); });
  var maxDead = 0; (d.dead_tuples || []).forEach(function(x) { if (x.value > maxDead) maxDead = x.value; });

  return (
    <div className="page">

      {/* domain tabs + filter bar */}
      <div className="flex-row" style={{gap: 10, alignItems: "center", marginBottom: 12, flexWrap: "wrap"}}>
        {domains.length > 1 ? (
          <div className="tabs" style={{borderRadius: "var(--r-md)", border: "1px solid var(--border)"}}>
            {domains.map(function(t) {
              return (
                <button key={t.slug} className={domain === t.slug ? "active" : ""}
                        onClick={function() { setDomain(t.slug); }} title={t.sub || ""}>
                  <Icon.TrendingUp size={13} style={{verticalAlign: "-2px", marginRight: 6}}/>{t.label}
                </button>
              );
            })}
          </div>
        ) : null}
        <span className="txt-xs muted" style={{textTransform: "uppercase", fontWeight: 700, marginLeft: domains.length > 1 ? 6 : 0}}>Database</span>
        <select className="appmon-select" value={db} onChange={function(e) { setDb(e.target.value); }}>
          <option value="">All in domain</option>
          {(filters && filters.databases || []).map(function(x) { return <option key={x} value={x}>{x}</option>; })}
        </select>
        {schemaChips ? (
          <div className="flex-row" style={{gap: 6, flexWrap: "wrap", alignItems: "center"}}>
            <span className="txt-xs muted" style={{textTransform: "uppercase", fontWeight: 700}}>Schema</span>
            <button className={"btn sm " + (schema === "" ? "" : "ghost")} onClick={function() { setSchema(""); }}>All</button>
            {schemaChips.map(function(c) {
              return <button key={c.value} className={"btn sm " + (schema === c.value ? "" : "ghost")}
                             onClick={function() { setSchema(c.value); }}>{c.label}</button>;
            })}
          </div>
        ) : null}
        <div className="grow" style={{flex: 1}}/>
        <a className="btn sm ghost" href={APPMON_GRAFANA} target="_blank" rel="noopener noreferrer">
          <Icon.ExternalLink size={12}/> Full dashboard in Grafana
        </a>
      </div>

      {unavailable ? (
        <div className="card"><div className="bd">
          <AppMonDomainEmpty title="Domain metrics unavailable"
                    hint="Prometheus returned no data for this selection. Verify the pginspector exporter is scraping these databases."/>
        </div></div>
      ) : (
        <React.Fragment>

          {/* KPI tiles */}
          <div className="tile-row">
            <KPI color="deepgreen" label="Tables tracked"   value={fmtInt((d.top_by_rows || []).length)} sub="pginspector"/>
            <KPI color="navy"      label="Live sessions"    value={fmtInt(totalSessions)} sub="in-scope pg_stat_activity"/>
            <KPI color="teal"      label="Rows inserted"    value={fmtInt(totalChurn)} sub={"over " + (timeRange || "24h")}/>
            <KPI color={maxDead >= 20 ? "red" : "orange"} label="Max dead-tuple %" value={fmtPct(maxDead)} sub="worst table"/>
          </div>

          {/* footprint */}
          <div className="grid-2 mt-3">
            <div className="card">
              <div className="hd">Top tables by size</div>
              <div className="bd">
                <BarList rows={sizeRows} emptyText="No table-size samples for this selection."
                         valueFormatter={function(v) { return fmtBytes(v); }}/>
              </div>
            </div>
            <div className="card">
              <div className="hd">Top tables by row estimate</div>
              <div className="bd">
                <BarList rows={rowRows} emptyText="No row-estimate samples for this selection." valueFormatter={fmtInt}/>
              </div>
            </div>
          </div>

          {/* dead tuples + churn */}
          <div className="grid-2 mt-3">
            <div className="card">
              <div className="hd">Dead-tuple % (vacuum candidates)
                <span className="meta">{(d.dead_tuples || []).length} tables</span>
              </div>
              <div style={{overflowX: "auto"}}>
                <table className="tbl">
                  <thead><tr><th>Relation</th><th>Database</th><th className="num">Dead %</th></tr></thead>
                  <tbody>
                    {(d.dead_tuples || []).slice(0, 25).map(function(r, i) {
                      return (
                        <tr key={i}>
                          <td className="mono txt-xs">{r.schema ? (r.schema + ".") : ""}{r.relation}</td>
                          <td className="txt-xs muted">{r.datname}</td>
                          <td className="num"><span className={"pill " + (r.value >= 20 ? "warn" : "muted")}>{fmtPct(r.value)}</span></td>
                        </tr>
                      );
                    })}
                    {!(d.dead_tuples || []).length ? (
                      <tr><td colSpan="3" className="muted" style={{textAlign: "center", padding: 18}}>No tables with dead tuples reported.</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="card">
              <div className="hd">DML / ETL churn <span className="meta">rows inserted · {timeRange || "24h"}</span></div>
              <div className="bd">
                <BarList rows={churnRows} emptyText="No insert activity recorded over the selected range." valueFormatter={fmtInt}/>
              </div>
            </div>
          </div>

          {/* sessions & waits */}
          <div className="card mt-3">
            <div className="hd">Sessions &amp; waits
              <span className="meta">{(d.sessions || []).length} groups</span>
            </div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl">
                <thead>
                  <tr><th>Database</th><th>Application</th><th>State</th><th>Wait type</th><th>Wait event</th><th className="num">Sessions</th></tr>
                </thead>
                <tbody>
                  {(d.sessions || []).map(function(r, i) {
                    return (
                      <tr key={i}>
                        <td className="mono txt-xs">{r.datname}</td>
                        <td>{r.application || "—"}</td>
                        <td><span className={"pill " + appmonStatePill(r.state)}>{r.state}</span></td>
                        <td className="txt-xs">{r.wait_event_type || "—"}</td>
                        <td className="txt-xs muted">{r.wait_event || "—"}</td>
                        <td className="num">{fmtInt(r.sessions)}</td>
                      </tr>
                    );
                  })}
                  {!(d.sessions || []).length ? (
                    <tr><td colSpan="6" className="muted" style={{textAlign: "center", padding: 18}}>No active sessions for this selection.</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>

        </React.Fragment>
      )}
    </div>
  );
}

window.AppMonDomainView = AppMonDomainView;
