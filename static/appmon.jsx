// Application Monitoring — Estate Overview (Prometheus-backed)
// Mirrors the "UAT Application Monitoring" Grafana dashboard's overview row,
// natively in the console. ES5-safe style to match the other screens.

function AppMonEmpty(props) {
  return (
    <EmptyState icon={Icon.Activity}
                title={props.title || "No application metrics"}
                hint={props.hint || "Prometheus returned no data. Verify the application-monitoring exporter is scraping this cluster."}/>
  );
}

function AppMonitoringScreen(props) {
  var lastRefresh = props.lastRefresh;
  var timeRange = props.timeRange;

  var ovState = useState(null);      var overview = ovState[0];   var setOverview = ovState[1];
  var trState = useState(null);      var trend = trState[0];      var setTrend = trState[1];
  var topState = useState(null);     var top = topState[0];       var setTop = topState[1];
  var filtState = useState(null);    var filters = filtState[0];  var setFilters = filtState[1];
  var dbState = useState("");        var db = dbState[0];         var setDb = dbState[1];
  var regState = useState("");       var region = regState[0];    var setRegion = regState[1];
  var domState = useState("");       var domain = domState[0];    var setDomain = domState[1];
  var loadState = useState(true);    var loading = loadState[0];  var setLoading = loadState[1];

  // Compose the active scope query. Explicit database wins server-side; region +
  // domain compose a precise datname matcher otherwise.
  var scopeParams = [];
  if (db) scopeParams.push("database=" + encodeURIComponent(db));
  if (region) scopeParams.push("region=" + encodeURIComponent(region));
  if (domain) scopeParams.push("domain=" + encodeURIComponent(domain));
  var dbq = scopeParams.length ? ("?" + scopeParams.join("&")) : "";

  var APPMON_DOMAINS = ["TPS", "TPS_WAREHOUSE", "SERVICE", "COMMON"];

  // Load filter options once.
  useEffect(function() {
    var alive = true;
    fetch(clusterPath("/appmon/filters"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) { if (alive) setFilters(d); })
      .catch(function() { if (alive) setFilters({ databases: [] }); });
    return function() { alive = false; };
  }, [lastRefresh]);

  // Load overview + top sessions (instant) on refresh / db change.
  useEffect(function() {
    var alive = true;
    setLoading(true);
    Promise.all([
      fetch(clusterPath("/appmon/overview" + dbq), { cache: "no-store" }).then(hbzJsonResponse).catch(function() { return null; }),
      fetch(clusterPath("/appmon/top-sessions" + (dbq ? dbq + "&" : "?") + "limit=25"), { cache: "no-store" }).then(hbzJsonResponse).catch(function() { return null; })
    ]).then(function(res) {
      if (!alive) return;
      setOverview(res[0]); setTop(res[1]); setLoading(false);
    });
    return function() { alive = false; };
  }, [lastRefresh, db, region, domain]);

  // Load trend (range query) on refresh / db / timeRange change.
  useEffect(function() {
    var alive = true;
    var sep = dbq ? "&" : "?";
    fetch(clusterPath("/appmon/trend" + dbq + sep + "range=" + encodeURIComponent(timeRange || "24h")), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) { if (alive) setTrend(d); })
      .catch(function() { if (alive) setTrend({ available: false, series: [] }); });
    return function() { alive = false; };
  }, [lastRefresh, db, region, domain, timeRange]);

  var ov = overview || {};
  var unreachable = overview && overview.available === false &&
                    (!ov.by_state || !ov.by_state.length) && (ov.total === 0);

  // Build a total-sessions trend line by summing state series per bucket.
  var trendSeries = (trend && trend.series) || [];
  var trendTotals = [];
  if (trendSeries.length) {
    var n = 0;
    trendSeries.forEach(function(s) { if (s.points && s.points.length > n) n = s.points.length; });
    for (var i = 0; i < n; i++) {
      var sum = 0;
      for (var j = 0; j < trendSeries.length; j++) {
        var p = trendSeries[j].points[i];
        if (p) sum += p[1];
      }
      trendTotals.push(sum);
    }
  }
  var hasTrend = trend && trend.available && trendTotals.length > 1;

  var grafana = "https://uat-pgo18-grafana-uat-pgcluster-uae.apps.ocp-dr.habibbank.local/d/uat-app-monitoring";

  function statePill(s) {
    if (s === "active") return "ok";
    if (s && s.indexOf("idle in transaction") === 0) return "warn";
    if (s === "idle") return "muted";
    return "info";
  }

  return (
    <div className="page">

      {/* filter bar */}
      <div className="flex-row" style={{gap: 10, alignItems: "center", marginBottom: 12, flexWrap: "wrap"}}>
        <span className="txt-xs muted" style={{textTransform: "uppercase", fontWeight: 700}}>Region</span>
        <select className="appmon-select" value={region} onChange={function(e) { setRegion(e.target.value); }}>
          <option value="">All regions</option>
          {(filters && filters.regions || []).map(function(r) {
            return <option key={r} value={r}>{r}</option>;
          })}
        </select>
        <span className="txt-xs muted" style={{textTransform: "uppercase", fontWeight: 700}}>Domain</span>
        <select className="appmon-select" value={domain} onChange={function(e) { setDomain(e.target.value); }}>
          <option value="">All domains</option>
          {APPMON_DOMAINS.map(function(x) {
            return <option key={x} value={x}>{x.replace(/_/g, " ")}</option>;
          })}
        </select>
        <span className="txt-xs muted" style={{textTransform: "uppercase", fontWeight: 700}}>Database</span>
        <select className="appmon-select" value={db} onChange={function(e) { setDb(e.target.value); }}>
          <option value="">All databases</option>
          {(filters && filters.databases || []).map(function(d) {
            return <option key={d} value={d}>{d}</option>;
          })}
        </select>
        {(region || domain || db) ? (
          <button className="btn sm ghost" onClick={function() { setRegion(""); setDomain(""); setDb(""); }}>
            <Icon.X size={12}/> Clear
          </button>
        ) : null}
        <div className="grow" style={{flex: 1}}/>
        <a className="btn sm ghost" href={grafana} target="_blank" rel="noopener noreferrer">
          <Icon.ExternalLink size={12}/> Full dashboard in Grafana
        </a>
      </div>

      {unreachable ? (
        <div className="card"><div className="bd">
          <AppMonEmpty title="Application metrics unavailable"
                       hint="Could not read session metrics from Prometheus for this cluster."/>
        </div></div>
      ) : (
        <React.Fragment>

          {/* KPI tiles */}
          <div className="tile-row">
            <KPI color="deepgreen" label="Total sessions" value={fmtInt(ov.total || 0)} sub="pg_stat_activity" spark={hasTrend ? trendTotals : null}/>
            <KPI color="green"     label="Active"          value={fmtInt(ov.active || 0)} sub="state = active"/>
            <KPI color="navy"      label="Idle"            value={fmtInt(ov.idle || 0)} sub="state = idle"/>
            <KPI color="orange"    label="Idle in Tx"      value={fmtInt(ov.idle_in_transaction || 0)} sub="idle in transaction"/>
            <KPI color="red"       label="Lock waits"      value={fmtInt(ov.lock_waits || 0)} sub="wait_event_type = Lock"/>
            <KPI color="teal"      label="DB coverage"     value={fmtInt(ov.coverage || 0)} sub="inventoried databases"/>
          </div>

          {/* breakdowns */}
          <div className="grid-3 mt-3">
            <div className="card">
              <div className="hd">Session state</div>
              <div className="bd">
                {ov.by_state && ov.by_state.length
                  ? <DonutChart rows={ov.by_state} center={fmtInt(ov.total || 0)} sub="sessions"/>
                  : <AppMonEmpty title="No sessions"/>}
              </div>
            </div>
            <div className="card">
              <div className="hd">Sessions by region</div>
              <div className="bd">
                <BarList rows={ov.by_region || []} emptyText="No regional session data." valueFormatter={fmtInt}/>
              </div>
            </div>
            <div className="card">
              <div className="hd">Sessions by domain</div>
              <div className="bd">
                <BarList rows={ov.by_domain || []} emptyText="No domain session data." valueFormatter={fmtInt}/>
              </div>
            </div>
          </div>

          {/* session trend */}
          <div className="card mt-3">
            <div className="hd">Session trend <span className="meta"><Icon.Clock size={11}/> {timeRange || "24h"}</span></div>
            <div className="bd">
              {hasTrend
                ? <MiniLine data={trendTotals} color="var(--hbz-green)" fill="rgba(20,114,87,.10)" height={110} axis/>
                : <AppMonEmpty icon={Icon.Activity} title="No session history"
                               hint="The trend appears once Prometheus has scraped session samples over the selected range."/>}
            </div>
          </div>

          {/* top sessions */}
          <div className="card mt-3">
            <div className="hd">Top sessions by application &amp; wait
              <span className="meta">{top && top.rows ? top.rows.length : 0} groups</span>
            </div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Database</th><th>Application</th><th>State</th>
                    <th>Wait type</th><th>Wait event</th><th className="num">Sessions</th>
                  </tr>
                </thead>
                <tbody>
                  {(top && top.rows || []).map(function(r, i) {
                    return (
                      <tr key={i}>
                        <td className="mono txt-xs">{r.datname}</td>
                        <td>{r.application || "—"}</td>
                        <td><span className={"pill " + statePill(r.state)}>{r.state}</span></td>
                        <td className="txt-xs">{r.wait_event_type || "—"}</td>
                        <td className="txt-xs muted">{r.wait_event || "—"}</td>
                        <td className="num">{fmtInt(r.sessions)}</td>
                      </tr>
                    );
                  })}
                  {(!top || !top.rows || !top.rows.length) ? (
                    <tr><td colSpan="6" className="muted" style={{textAlign: "center", padding: 18}}>
                      No active sessions for this selection.
                    </td></tr>
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

window.AppMonitoringScreen = AppMonitoringScreen;
