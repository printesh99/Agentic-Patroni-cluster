function readinessTone(status) {
  if (status === "ok") return "ok";
  if (status === "critical") return "danger";
  return "warn";
}

function readinessIcon(key) {
  var map = {
    database: Icon.Database,
    patroni: Icon.Server,
    prometheus: Icon.Activity,
    kubernetes: Icon.Cloud,
    pgbackrest: Icon.HardDrive,
    remote_agents: Icon.Wifi,
    ingest: Icon.Database,
  };
  return map[key] || Icon.CheckCircle;
}

function fmtReadinessAge(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "-";
  var s = Math.max(0, Number(seconds));
  if (s < 90) return Math.round(s) + "s";
  var m = s / 60;
  if (m < 90) return Math.round(m) + "m";
  var h = m / 60;
  if (h < 48) return h.toFixed(1) + "h";
  return (h / 24).toFixed(1) + "d";
}

function ReadinessCheckCard({ check }) {
  var I = readinessIcon(check.key);
  var tone = readinessTone(check.status);
  var details = [];
  if (check.url) details.push(["URL", check.url]);
  if (check.configured_urls && check.configured_urls.length) details.push(["URLs", check.configured_urls.join(", ")]);
  if (check.namespace) details.push(["Namespace", check.namespace]);
  if (check.stanza) details.push(["Stanza", check.stanza]);
  if (check.bucket) details.push(["Bucket", check.bucket]);
  if (check.endpoint) details.push(["Endpoint", check.endpoint]);
  if (check.missing && check.missing.length) details.push(["Missing", check.missing.join(", ")]);
  if (check.configured && check.configured.length) details.push(["Configured", check.configured.join(", ")]);
  if (check.age_seconds != null) details.push(["Snapshot age", fmtReadinessAge(check.age_seconds)]);
  if (check.duration_ms != null) details.push(["Latency", check.duration_ms + " ms"]);
  if (check.latest_snapshot && check.latest_snapshot.collected_at) details.push(["Latest snapshot", String(check.latest_snapshot.collected_at)]);

  return (
    <div className="card">
      <div className="hd">
        <span className="flex-row"><I size={15}/>{check.label}</span>
        <span className={"pill " + tone}><span className="dot"/>{check.status}</span>
      </div>
      <div className="bd">
        <div className="muted txt-xs">{check.source}</div>
        <p style={{marginTop: 8}}>{check.detail}</p>
        {details.length ? (
          <table className="tbl compact" style={{marginTop: 10}}>
            <tbody>
              {details.map(function(row) {
                return (
                  <tr key={row[0]}>
                    <th>{row[0]}</th>
                    <td className="mono txt-xs">{row[1]}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : null}
      </div>
    </div>
  );
}

function StartupFindings({ findings }) {
  var rows = findings || [];
  if (!rows.length) {
    return <EmptyState icon={Icon.CheckCircle} title="No startup findings" hint="Required runtime configuration is explicit for this deployment."/>;
  }
  return (
    <table className="tbl">
      <thead><tr><th>Setting</th><th>Severity</th><th>Finding</th></tr></thead>
      <tbody>
        {rows.map(function(row, index) {
          var tone = row.severity === "critical" ? "danger" : "warn";
          return (
            <tr key={row.name + index}>
              <td className="mono">{row.name}</td>
              <td><span className={"pill " + tone}>{row.severity}</span></td>
              <td>{row.detail}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function SourceMatrix({ checks }) {
  var rows = (checks || []).map(function(check) {
    return {
      key: check.key,
      label: check.label,
      source: check.source,
      status: check.status,
      detail: check.detail,
    };
  });
  if (!rows.length) {
    return <EmptyState icon={Icon.Database} title="No source checks" hint="The readiness endpoint returned no source inventory." source="readiness-checks"/>;
  }
  return (
    <table className="tbl">
      <thead><tr><th>Surface</th><th>Source</th><th>Status</th><th>Operational meaning</th></tr></thead>
      <tbody>
        {rows.map(function(row) {
          return (
            <tr key={row.key}>
              <td>{row.label}</td>
              <td><SourceBadge source={row.source}/></td>
              <td><span className={"pill " + readinessTone(row.status)}><span className="dot"/>{row.status}</span></td>
              <td>{row.detail}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ReadinessScreen({ lastRefresh }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    fetch("/api/v1/readiness", { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(payload) {
        if (!alive) return;
        setData(payload);
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  if (loading && !data) {
    return (
      <div className="page">
        <div className="tile-row">
          <KPI skeleton/><KPI skeleton/><KPI skeleton/>
        </div>
      </div>
    );
  }
  if (error) {
    return <div className="page"><EmptyState icon={Icon.AlertTriangle} title="Readiness unavailable" hint={error}/></div>;
  }
  var summary = data.summary || {};
  var checks = data.items || data.checks || [];
  var readinessStatus = (data.summary || {}).status || data.status || "unknown";
  var statusTone = readinessTone(readinessStatus);
  var cluster = data.cluster || {};
  var deployment = data.deployment || {};
  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={readinessStatus === "critical" ? "red" : readinessStatus === "warning" ? "orange" : "green"}
             label="Readiness" value={readinessStatus} sub={"checked " + (data.generated_at || data.checked_at || "-")}/>
        <KPI color="green" label="Healthy sources" value={fmtInt(summary.ok != null ? summary.ok : Math.max(0, Number(summary.total || 0) - Number(summary.critical || 0) - Number(summary.warnings || 0)))} sub="ready now"/>
        <KPI color="orange" label="Warnings" value={fmtInt(summary.warnings || 0)} sub="visible configuration or source issues"/>
        <KPI color="red" label="Critical" value={fmtInt(summary.critical || 0)} sub="blocks production readiness"/>
      </div>

      <div className="card">
        <div className="hd">
          <span className="flex-row"><Icon.Server size={15}/>Deployment context</span>
          <span className={"pill " + statusTone}><span className="dot"/>{readinessStatus}</span>
        </div>
        <div className="bd">
          <div className="grid-4">
            <Stat label="Cluster" value={cluster.cluster_name || "-"} sub={cluster.cluster_id || "-"}/>
            <Stat label="Mode" value={cluster.mode || "-"} sub={cluster.read_only ? "read only" : "write-capable console"}/>
            <Stat label="Namespace" value={cluster.namespace || "-"}/>
            <Stat label="Service" value={data.service || "-"}/>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">
          <span className="flex-row"><Icon.Cloud size={15}/>OpenShift rollout metadata</span>
          <SourceBadge source="readiness-checks"/>
        </div>
        <div className="bd">
          <div className="grid-4">
            <Stat label="Image tag" value={deployment.image_tag || "not set"}/>
            <Stat label="Route URL" value={deployment.route_url || "not set"}/>
            <Stat label="DB service" value={deployment.db_service || "not set"}/>
            <Stat label="Secrets" value={fmtInt((deployment.secret_names || []).length)} sub={(deployment.secret_names || []).join(", ") || "not set"}/>
          </div>
          <table className="tbl compact" style={{marginTop: 12}}>
            <tbody>
              <tr><th>Prometheus URL</th><td className="mono txt-xs">{deployment.prometheus_url || "not set"}</td></tr>
              <tr><th>Patroni URL</th><td className="mono txt-xs">{deployment.patroni_url || "not set"}</td></tr>
              <tr><th>Remote agents</th><td className="mono txt-xs">{Object.keys(deployment.remote_agent_urls || {}).length ? JSON.stringify(deployment.remote_agent_urls) : "not set"}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid-3">
        {checks.map(function(check) { return <ReadinessCheckCard key={check.key || check.name} check={check}/>; })}
      </div>

      <div className="card">
        <div className="hd"><span className="flex-row"><Icon.Database size={15}/>Data source inventory</span><SourceBadge source="readiness-checks"/></div>
        <div className="bd"><SourceMatrix checks={checks}/></div>
      </div>

      <div className="card">
        <div className="hd"><span className="flex-row"><Icon.Settings size={15}/>Startup configuration findings</span></div>
        <div className="bd"><StartupFindings findings={data.startup_findings || []}/></div>
      </div>
    </div>
  );
}

window.ReadinessScreen = ReadinessScreen;
