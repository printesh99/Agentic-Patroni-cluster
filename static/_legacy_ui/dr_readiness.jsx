// Phase 1 DR readiness score.
// Read-only UI aggregation over existing console APIs. This screen does not
// submit jobs or execute live cluster commands.

function drFetch(path, params) {
  return v1Json(path, params)
    .then(function(payload) { return { ok: true, data: payload }; })
    .catch(function(err) { return { ok: false, error: err.message || String(err) }; });
}

function drPill(status) {
  if (status === "ready" || status === "ok" || status === "healthy" || status === "succeeded") return "ok";
  if (status === "blocked" || status === "critical" || status === "failed" || status === "blocker") return "danger";
  if (status === "running" || status === "info") return "info";
  if (status === "warning" || status === "caution" || status === "pending_approval") return "warn";
  return "muted";
}

function drScoreColor(status) {
  if (status === "ready") return "green";
  if (status === "blocked") return "red";
  return "orange";
}

function drDate(value) {
  if (!value) return "-";
  try { return new Date(value).toLocaleString("en-GB", { hour12: false }); }
  catch (e) { return String(value); }
}

function drPlaceholderIp(value) {
  var v = String(value || "").trim().toLowerCase();
  if (!v || v === "0.0.0.0" || v.indexOf("placeholder") >= 0 || v.indexOf("replace") >= 0) return true;
  return /\.0$/.test(v);
}

function drBoolLabel(value) {
  return value ? "configured" : "missing";
}

function drAddFinding(list, severity, component, summary, detail, source) {
  list.push({
    severity: severity,
    component: component,
    summary: summary,
    detail: detail || "",
    source: source || "console",
  });
}

function drFindCluster(clusters, id, namePart) {
  var lowered = String(namePart || "").toLowerCase();
  for (var i = 0; i < clusters.length; i++) {
    var c = clusters[i];
    if (id && c.id === id) return c;
    if (lowered && String(c.name || "").toLowerCase().indexOf(lowered) >= 0) return c;
  }
  return null;
}

function drConfigRows(configs) {
  return (configs || []).map(function(row) {
    var cfg = row.config || {};
    var missing = (row.missing_keys || []).slice();
    if (drPlaceholderIp(cfg.dr_primary_lb)) missing.push("dr_primary_lb");
    if (!cfg.prod_primary_lb) missing.push("prod_primary_lb");
    if (!cfg.pgbackrest_s3_bucket) missing.push("pgbackrest_s3_bucket");
    if (!cfg.pgbackrest_s3_endpoint) missing.push("pgbackrest_s3_endpoint");
    return {
      id: row.id || "-",
      enabled: row.enabled !== false,
      prod_cluster: cfg.prod_cluster || "-",
      dr_cluster: cfg.dr_cluster || "-",
      prod_namespace: cfg.prod_namespace || "-",
      dr_namespace: cfg.dr_namespace || "-",
      prod_lb: !!cfg.prod_primary_lb && !drPlaceholderIp(cfg.prod_primary_lb),
      dr_lb: !!cfg.dr_primary_lb && !drPlaceholderIp(cfg.dr_primary_lb),
      repo: !!cfg.pgbackrest_s3_bucket && !!cfg.pgbackrest_s3_endpoint,
      missing: missing,
      updated_at: row.updated_at || null,
    };
  });
}

function drBuildModel(clusterId, payloads) {
  var findings = [];
  var readinessRes = payloads.readiness;
  var clustersRes = payloads.clusters;
  var cutoverConfigRes = payloads.cutoverConfig;
  var cutoverRunsRes = payloads.cutoverRuns;
  var backupsRes = payloads.backups;
  var replRes = payloads.replication;

  [
    ["readiness", readinessRes],
    ["clusters", clustersRes],
    ["cutover config", cutoverConfigRes],
    ["cutover runs", cutoverRunsRes],
    ["backups", backupsRes],
    ["replication", replRes],
  ].forEach(function(row) {
    if (!row[1].ok) {
      drAddFinding(findings, "blocker", "API", "Unable to load " + row[0], row[1].error, row[0]);
    }
  });

  var readiness = readinessRes.ok ? readinessRes.data : {};
  var readinessChecks = readiness.checks || [];
  if (readiness.status === "critical") {
    drAddFinding(findings, "blocker", "Environment", "Environment readiness is critical", "Open the Environment Readiness page for source-level details.", "readiness");
  } else if (readiness.status === "warning") {
    drAddFinding(findings, "warning", "Environment", "Environment readiness has warnings", "Review source configuration and ingest freshness before DR work.", "readiness");
  }
  readinessChecks.forEach(function(check) {
    if (check.status === "ok") return;
    var severity = check.status === "critical" ? "blocker" : "warning";
    if (check.key === "remote_agents" || check.key === "pgbackrest") severity = "blocker";
    drAddFinding(findings, severity, check.label || check.key, check.detail || "Source is not healthy", check.source || "", "readiness");
  });

  var clusters = clustersRes.ok ? (clustersRes.data.clusters || []) : [];
  var prodCluster = drFindCluster(clusters, "prod", "prod");
  var drCluster = drFindCluster(clusters, "dr", "dr");
  if (!prodCluster) drAddFinding(findings, "blocker", "Inventory", "PROD cluster metadata is missing", "Add PROD cluster metadata or remote-agent configuration.", "clusters");
  if (!drCluster) drAddFinding(findings, "blocker", "Inventory", "DR cluster metadata is missing", "Add DR cluster metadata or remote-agent configuration.", "clusters");
  [prodCluster, drCluster].forEach(function(c) {
    if (!c) return;
    if (!c.agent_configured) {
      drAddFinding(findings, "blocker", "Remote agent", c.label + " remote agent is not configured", "DR readiness cannot verify the remote side from the central console.", "clusters");
    }
    if (!c.latest_snapshot) {
      drAddFinding(findings, "warning", "Metrics ingest", c.label + " has no latest snapshot", "Historical evidence and freshness checks are incomplete.", "clusters");
    }
    if (c.health && c.health !== "healthy") {
      drAddFinding(findings, "warning", "Cluster health", c.label + " is " + c.health, "Review cluster details before DR operation.", "clusters");
    }
  });

  var cutover = cutoverConfigRes.ok ? cutoverConfigRes.data : {};
  var configRows = drConfigRows(cutover.configs || []);
  if (!configRows.length) {
    drAddFinding(findings, "blocker", "Cutover config", "No cutover configuration is present", "Create a region cutover configuration before rehearsal.", "cutover");
  }
  configRows.forEach(function(row) {
    if (!row.enabled) drAddFinding(findings, "blocker", "Cutover config", "Cutover config " + row.id + " is disabled", "Enable only after values are verified.", "cutover");
    if (row.missing.length) {
      drAddFinding(findings, "blocker", "Cutover config", "Cutover config " + row.id + " has incomplete values", "Missing or placeholder: " + row.missing.join(", "), "cutover");
    }
  });
  if (cutover.vendor && cutover.vendor.ok === false) {
    drAddFinding(findings, "blocker", "Cutover engine", "Vendored cutover engine checksum failed", "Do not rehearse or arm cutover until engine integrity is restored.", "cutover");
  }
  if (cutover.oc_available === false) {
    drAddFinding(findings, "blocker", "Cutover runtime", "OpenShift CLI is not available to the cutover runtime", "Local Docker preview can still render, but live cutover cannot run here.", "cutover");
  }

  var runs = cutoverRunsRes.ok ? (cutoverRunsRes.data.runs || []) : [];
  var rehearsalRuns = runs.filter(function(run) { return run.tier === "rehearsal" || run.tier === "armed"; });
  var latestRun = runs.length ? runs[0] : null;
  var latestRehearsal = rehearsalRuns.length ? rehearsalRuns[0] : null;
  if (!rehearsalRuns.length) {
    drAddFinding(findings, "warning", "Rehearsal", "No rehearsal or armed cutover run is recorded", "Run preview first, then a rehearsal during an approved window.", "cutover");
  } else if (latestRehearsal.job_state === "failed") {
    drAddFinding(findings, "blocker", "Rehearsal", "Latest rehearsal failed", "Fix the failed step and rerun rehearsal before any armed cutover.", "cutover");
  }

  var backups = backupsRes.ok ? backupsRes.data : {};
  var backupSummary = backups.summary || {};
  var backupRepo = backups.repo || {};
  var backupArchive = backups.archive || {};
  var backupSettings = backups.settings || {};
  if (backupSummary.status === "critical") {
    drAddFinding(findings, "blocker", "Backups", "Backup posture is critical", "Review pgBackRest repo, archive mode, and validation evidence.", "backups");
  } else if (backupSummary.status === "warning") {
    drAddFinding(findings, "warning", "Backups", "Backup posture has warnings", "Review schedules and recent validation evidence.", "backups");
  }
  if (!backupRepo.bucket || !backupRepo.s3_endpoint) {
    drAddFinding(findings, "blocker", "pgBackRest repo", "pgBackRest S3 bucket or endpoint is missing", "Restore and PITR evidence cannot be trusted without repo configuration.", "backups");
  }
  if (backupSettings.archive_mode !== "on") {
    drAddFinding(findings, "blocker", "WAL archive", "archive_mode is not enabled", "PITR and DR rebuild require valid WAL archive coverage.", "backups");
  }
  if (backupArchive.failed_count > 0) {
    drAddFinding(findings, "warning", "WAL archive", "Archive failures are recorded", "Check latest failed WAL and pgBackRest archive-push logs.", "backups");
  }
  if (!(backups.history || []).length) {
    drAddFinding(findings, "warning", "Backup validation", "No backup validation history is recorded", "Run a validation job and store evidence before DR approval.", "backups");
  }

  var repl = replRes.ok ? replRes.data : {};
  var replSummary = repl.summary || {};
  if (replSummary.patroni_ok === false) {
    drAddFinding(findings, "blocker", "Patroni", "Patroni topology is unavailable", "Leader, member, and timeline evidence cannot be verified.", "replication");
  }
  if (!Number(replSummary.members || 0)) {
    drAddFinding(findings, "blocker", "Replication", "No Patroni members are visible", "Verify Patroni API and remote-agent visibility.", "replication");
  }
  if (Number(replSummary.max_lag_bytes || 0) > 1024 * 1024 * 1024) {
    drAddFinding(findings, "blocker", "Replication lag", "Replication lag exceeds 1 GiB", "Do not cut over until lag is understood and reduced.", "replication");
  } else if (Number(replSummary.max_lag_bytes || 0) > 16 * 1024 * 1024) {
    drAddFinding(findings, "warning", "Replication lag", "Replication lag exceeds 16 MiB", "Confirm RPO impact before rehearsal or cutover.", "replication");
  }
  if (Number(replSummary.inactive_slots || 0) > 0) {
    drAddFinding(findings, "warning", "Replication slots", "Inactive replication slots are present", "Review retained WAL and slot ownership before DR work.", "replication");
  }

  var blockers = findings.filter(function(f) { return f.severity === "blocker"; }).length;
  var warnings = findings.filter(function(f) { return f.severity === "warning"; }).length;
  var score = Math.max(0, Math.min(100, 100 - blockers * 14 - warnings * 5));
  var status = blockers > 0 ? "blocked" : warnings > 0 ? "caution" : "ready";

  return {
    cluster_id: clusterId,
    status: status,
    score: score,
    blockers: blockers,
    warnings: warnings,
    findings: findings,
    readiness: readiness,
    clusters: clusters,
    prodCluster: prodCluster,
    drCluster: drCluster,
    cutover: cutover,
    configRows: configRows,
    runs: runs,
    latestRun: latestRun,
    latestRehearsal: latestRehearsal,
    backups: backups,
    backupSummary: backupSummary,
    backupArchive: backupArchive,
    backupSettings: backupSettings,
    replication: repl,
    replSummary: replSummary,
    observed_at: new Date().toISOString(),
  };
}

function DrFindingTable({ findings }) {
  var rows = findings || [];
  if (!rows.length) {
    return <EmptyState icon={Icon.CheckCircle} title="No blockers or warnings" hint="Current evidence indicates DR readiness is clear."/>;
  }
  return (
    <table className="tbl">
      <thead><tr><th>Severity</th><th>Component</th><th>Finding</th><th>Source</th></tr></thead>
      <tbody>
        {rows.map(function(row, index) {
          return (
            <tr key={index}>
              <td><span className={"pill " + drPill(row.severity)}><span className="dot"/>{row.severity}</span></td>
              <td>{row.component}</td>
              <td>
                <strong>{row.summary}</strong>
                {row.detail ? <div className="muted txt-xs mt-2">{row.detail}</div> : null}
              </td>
              <td className="mono txt-xs">{row.source}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DrConfigTable({ rows }) {
  if (!rows || !rows.length) return <EmptyState icon={Icon.Settings} title="No cutover config" hint="No DR cutover configuration was returned by the console."/>;
  return (
    <table className="tbl">
      <thead><tr><th>Region</th><th>PROD</th><th>DR</th><th>LBs</th><th>Repo</th><th>State</th></tr></thead>
      <tbody>
        {rows.map(function(row) {
          return (
            <tr key={row.id}>
              <td className="mono">{row.id}</td>
              <td><span className="mono txt-xs">{row.prod_cluster}</span><div className="muted txt-xs">{row.prod_namespace}</div></td>
              <td><span className="mono txt-xs">{row.dr_cluster}</span><div className="muted txt-xs">{row.dr_namespace}</div></td>
              <td>
                <span className={"pill " + (row.prod_lb ? "ok" : "danger")}>prod {drBoolLabel(row.prod_lb)}</span>{" "}
                <span className={"pill " + (row.dr_lb ? "ok" : "danger")}>dr {drBoolLabel(row.dr_lb)}</span>
              </td>
              <td><span className={"pill " + (row.repo ? "ok" : "danger")}>{drBoolLabel(row.repo)}</span></td>
              <td>
                <span className={"pill " + (row.enabled ? "ok" : "danger")}>{row.enabled ? "enabled" : "disabled"}</span>
                {row.missing.length ? <div className="muted txt-xs mt-2">missing: {row.missing.join(", ")}</div> : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DrRunTable({ runs }) {
  var rows = runs || [];
  if (!rows.length) return <EmptyState icon={Icon.Clock} title="No cutover runs" hint="Preview and rehearsal evidence will appear here after jobs are submitted."/>;
  return (
    <table className="tbl">
      <thead><tr><th>Submitted</th><th>Tier</th><th>Kind</th><th>State</th><th>Reason</th></tr></thead>
      <tbody>
        {rows.slice(0, 6).map(function(run) {
          return (
            <tr key={run.job_id}>
              <td className="mono txt-xs">{drDate(run.submitted_at || run.started_at)}</td>
              <td><span className={"pill " + (run.tier === "armed" ? "danger" : run.tier === "rehearsal" ? "info" : "muted")}>{run.tier || "-"}</span></td>
              <td className="mono txt-xs">{run.job_kind || run.mode || "-"}</td>
              <td><span className={"pill " + drPill(run.job_state)}><span className="dot"/>{run.job_state || "-"}</span></td>
              <td>{run.reason || "-"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DrClusterMini({ label, cluster }) {
  if (!cluster) {
    return (
      <div className="card">
        <div className="bd">
          <Stat label={label} value="missing" sub="cluster metadata not found"/>
        </div>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="bd">
        <div className="flex-row" style={{gap: 8}}>
          <Icon.Database size={15}/>
          <strong>{label}</strong>
          <div className="grow"/>
          <span className={"pill " + drPill(cluster.health)}><span className="dot"/>{cluster.health || "unknown"}</span>
        </div>
        <div className="grid-2 mt-3">
          <Stat label="Cluster" value={cluster.name || "-"} sub={cluster.k8s_namespace || "-"}/>
          <Stat label="Agent" value={cluster.agent_configured ? "configured" : "missing"} sub={cluster.read_only ? "read-only" : "write capable"}/>
        </div>
        <div className="muted txt-xs mt-2">Latest snapshot: {cluster.latest_snapshot ? drDate(cluster.latest_snapshot.collected_at) : "none"}</div>
      </div>
    </div>
  );
}

function DrReadinessScreen({ cluster, lastRefresh }) {
  var dataState = React.useState(null);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var clusterId = cluster && cluster.id ? cluster.id : (window.ACTIVE_CLUSTER_ID || "uat");

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    Promise.all([
      drFetch("/api/v1/readiness"),
      drFetch("/api/v1/clusters"),
      drFetch("/api/v1/cutover/config"),
      drFetch("/api/v1/cutover/runs", { limit: 10 }),
      drFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/backups"),
      drFetch("/api/v1/clusters/" + encodeURIComponent(clusterId) + "/replication/topology"),
    ]).then(function(results) {
      if (!alive) return;
      setData(drBuildModel(clusterId, {
        readiness: results[0],
        clusters: results[1],
        cutoverConfig: results[2],
        cutoverRuns: results[3],
        backups: results[4],
        replication: results[5],
      }));
      setLoading(false);
    }).catch(function(err) {
      if (!alive) return;
      setError(err.message || String(err));
      setLoading(false);
    });
    return function() { alive = false; };
  }, [clusterId, lastRefresh]);

  if (loading && !data) {
    return (
      <div className="page">
        <div className="tile-row"><KPI skeleton/><KPI skeleton/><KPI skeleton/><KPI skeleton/></div>
      </div>
    );
  }
  if (error) {
    return <div className="page"><EmptyState icon={Icon.AlertTriangle} title="DR readiness unavailable" hint={error}/></div>;
  }

  var score = data.score;
  var scoreTone = drPill(data.status);
  var backupStatus = data.backupSummary.status || "unknown";
  var archiveMode = data.backupSettings.archive_mode || "-";
  var patroniOk = data.replSummary.patroni_ok === true;
  var configComplete = data.configRows.length > 0 && data.configRows.every(function(row) { return row.enabled && row.missing.length === 0; });

  return (
    <div className="page">
      <div className="tile-row">
        <KPI color={drScoreColor(data.status)} label="DR readiness score" value={score + "%"} sub={data.status}/>
        <KPI color="red" label="Blockers" value={fmtInt(data.blockers)} sub="must clear before armed cutover"/>
        <KPI color="orange" label="Warnings" value={fmtInt(data.warnings)} sub="review before rehearsal"/>
        <KPI color={configComplete ? "green" : "red"} label="Cutover config" value={configComplete ? "complete" : "blocked"} sub="PROD/DR/LB/repo checks"/>
      </div>

      <div className="card">
        <div className="hd">
          <span className="flex-row"><Icon.Shield size={15}/>DR decision summary</span>
          <span className={"pill " + scoreTone}><span className="dot"/>{data.status}</span>
        </div>
        <div className="bd">
          <div className="grid-4">
            <Stat label="Backup posture" value={backupStatus} sub={"archive_mode=" + archiveMode}/>
            <Stat label="Patroni topology" value={patroniOk ? "available" : "unavailable"} sub={(data.replSummary.members || 0) + " members visible"}/>
            <Stat label="Max lag" value={fmtBytes(data.replSummary.max_lag_bytes || 0)} sub="from replication topology"/>
            <Stat label="Latest rehearsal" value={data.latestRehearsal ? data.latestRehearsal.job_state : "none"} sub={data.latestRehearsal ? drDate(data.latestRehearsal.submitted_at) : "no rehearsal evidence"}/>
          </div>
          <div className="tile-error" style={{marginTop: 12}}>
            <Icon.AlertTriangle size={13}/>
            <span>Read-only assessment only. Use Cutover & Switchover for preview/rehearsal/armed workflows with approval gates.</span>
          </div>
        </div>
      </div>

      <div className="grid-2">
        <DrClusterMini label="PROD / DC1" cluster={data.prodCluster}/>
        <DrClusterMini label="DR / DC2" cluster={data.drCluster}/>
      </div>

      <div className="card">
        <div className="hd">
          <span className="flex-row"><Icon.AlertTriangle size={15}/>Blockers and warnings</span>
          <SourceBadge source="readiness + cutover + backup + replication"/>
        </div>
        <div className="bd"><DrFindingTable findings={data.findings}/></div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd"><span className="flex-row"><Icon.Settings size={15}/>Cutover configuration</span></div>
          <div className="bd"><DrConfigTable rows={data.configRows}/></div>
        </div>
        <div className="card">
          <div className="hd"><span className="flex-row"><Icon.Clock size={15}/>Recent cutover evidence</span></div>
          <div className="bd"><DrRunTable runs={data.runs}/></div>
        </div>
      </div>

      <div className="grid-3">
        <div className="card">
          <div className="bd">
            <Stat label="pgBackRest repo" value={data.backups.repo && data.backups.repo.repo ? data.backups.repo.repo : "-"} sub={data.backups.repo && data.backups.repo.stanza ? "stanza " + data.backups.repo.stanza : "stanza unknown"}/>
            <div className="muted txt-xs mt-2">Bucket and endpoint are checked for presence but not displayed.</div>
          </div>
        </div>
        <div className="card">
          <div className="bd">
            <Stat label="Archive failures" value={fmtInt(data.backupArchive.failed_count || 0)} sub={"last archive age " + (data.backupArchive.last_archive_age_seconds == null ? "-" : fmtSec(data.backupArchive.last_archive_age_seconds))}/>
          </div>
        </div>
        <div className="card">
          <div className="bd">
            <Stat label="Replication slots" value={fmtInt(data.replSummary.replication_slots || 0)} sub={fmtInt(data.replSummary.inactive_slots || 0) + " inactive"}/>
          </div>
        </div>
      </div>
    </div>
  );
}

window.DrReadinessScreen = DrReadinessScreen;
