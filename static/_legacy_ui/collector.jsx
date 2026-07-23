// Collector observability screens: hourly health timeline and findings.

function CollectorHealthScreen({ cluster, timeRange, lastRefresh }) {
  var rangeState = React.useState(timeRange || "24h");
  var severityState = React.useState("all");
  var dataState = React.useState({ timeline: [], findings: [], runs: [] });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);

  var range = rangeState[0], setRange = rangeState[1];
  var severity = severityState[0], setSeverity = severityState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    Promise.all([
      v1Json("/api/v1/clusters/" + encodeURIComponent(cluster.id) + "/health/timeline", { range: range }),
      v1Json("/api/v1/clusters/" + encodeURIComponent(cluster.id) + "/findings", { status: "open", severity: severity }),
      v1Json("/api/v1/collector/runs", { cluster: cluster.id, range: range }),
      v1Json("/api/v1/collector/alert-bundle-requests", { cluster: cluster.id, range: "7d" })
    ]).then(function(results) {
      if (!alive) return;
      setData({
        timeline: results[0].timeline || [],
        findings: results[1].findings || [],
        runs: results[2].runs || [],
        bundleRequests: results[3].requests || [],
        source: results[0].source || "collector-history"
      });
      setLoading(false);
    }).catch(function(err) {
      if (!alive) return;
      setError(err.message || String(err));
      setLoading(false);
    });
    return function() { alive = false; };
  }, [cluster.id, range, severity, lastRefresh]);

  var timeline = data.timeline || [];
  var findings = data.findings || [];
  var bundleRequests = data.bundleRequests || [];
  var okRuns = timeline.filter(function(r) { return r.status === "ok"; }).length;
  var warnRuns = timeline.filter(function(r) { return r.status === "warn"; }).length;
  var criticalRuns = timeline.filter(function(r) { return r.status === "critical" || r.status === "failed"; }).length;
  var latest = timeline[0] || null;
  var criticalFindings = findings.filter(function(f) { return f.severity === "critical"; }).length;
  var warningFindings = findings.filter(function(f) { return f.severity === "warning"; }).length;

  function findingTone(finding) {
    if (finding.severity === "critical") return "danger";
    if (finding.severity === "warning") return "warn";
    return "info";
  }

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <div className="field" style={{margin: 0, minWidth: 150}}>
          <label>Window</label>
          <select value={range} onChange={function(e) { setRange(e.target.value); }}>
            <option value="1h">Last hour</option>
            <option value="24h">Last 24 hours</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="90d">Last 90 days</option>
          </select>
        </div>
        <div className="field" style={{margin: 0, minWidth: 160}}>
          <label>Finding severity</label>
          <select value={severity} onChange={function(e) { setSeverity(e.target.value); }}>
            <option value="all">All severities</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
        </div>
      </Phase1Toolbar>

      <div className="section-h">Hourly Collector Health</div>
      <div className="grid-4">
        <Stat label="Runs in window" value={timeline.length} sub={latest ? "latest " + phase1Date(latest.collected_at) : "no runs"}/>
        <Stat label="OK runs" value={okRuns} sub="read-only checks passed"/>
        <Stat label="Warn / failed" value={warnRuns + criticalRuns} sub={warnRuns + " warn · " + criticalRuns + " critical"}/>
        <Stat label="Open findings" value={findings.length} sub={criticalFindings + " critical · " + warningFindings + " warning"}/>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">
            <div><strong>Run Timeline</strong><small>{cluster.name}</small></div>
            <span className="pill muted">{range}</span>
          </div>
          <div className="bd">
            {!timeline.length && !loading
              ? <EmptyState icon={Icon.Clock} title="No collector runs" hint="Hourly summaries will appear here after the collector posts to the console API."/>
              : <table className="tbl">
                  <thead><tr><th>Collected</th><th>Status</th><th>Mode</th><th>Checks</th><th>Duration</th></tr></thead>
                  <tbody>
                    {timeline.slice(0, 24).map(function(run) {
                      return (
                        <tr key={run.id}>
                          <td>{phase1Date(run.collected_at)}</td>
                          <td><span className={"pill " + phase1Pill(run.status)}><span className="dot"/>{run.status}</span></td>
                          <td>{run.collector_mode}</td>
                          <td>{run.check_count || 0} / {run.unhealthy_checks || 0}</td>
                          <td>{run.duration_ms ? run.duration_ms + " ms" : "-"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>}
          </div>
        </div>

        <div className="card">
          <div className="hd">
            <div><strong>Open Findings</strong><small>deduped by fingerprint</small></div>
            <span className="pill muted">{severity === "all" ? "all" : severity}</span>
          </div>
          <div className="bd">
            {!findings.length && !loading
              ? <EmptyState icon={Icon.CheckCircle} title="No open findings" hint="The selected cluster has no open collector findings for this filter."/>
              : <div className="stack">
                  {findings.slice(0, 12).map(function(finding) {
                    return (
                      <div className="rowline" key={finding.id}>
                        <span className={"pill " + findingTone(finding)}><span className="dot"/>{finding.severity}</span>
                        <div className="grow">
                          <strong>{finding.title}</strong>
                          <small>{finding.component} · {finding.finding_type} · last {phase1Date(finding.last_seen_at)}</small>
                        </div>
                      </div>
                    );
                  })}
                </div>}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">
          <div><strong>Alert Bundle Requests</strong><small>critical alert driven evidence collection</small></div>
          <span className="pill muted">7d</span>
        </div>
        <div className="bd">
          {!bundleRequests.length && !loading
            ? <EmptyState icon={Icon.FileText} title="No bundle requests" hint="Critical alerts can create pending incident bundle requests through the collector webhook endpoint."/>
            : <table className="tbl">
                <thead><tr><th>Requested</th><th>Alert</th><th>Issue</th><th>Status</th><th>Bundle</th></tr></thead>
                <tbody>
                  {bundleRequests.slice(0, 12).map(function(req) {
                    return (
                      <tr key={req.id}>
                        <td>{phase1Date(req.requested_at)}</td>
                        <td>{req.alert_name}</td>
                        <td>{req.issue_id}</td>
                        <td><span className={"pill " + phase1Pill(req.status)}><span className="dot"/>{req.status}</span></td>
                        <td>{req.support_bundle_id ? "#" + req.support_bundle_id : "-"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>}
        </div>
      </div>
    </div>
  );
}

window.CollectorHealthScreen = CollectorHealthScreen;
