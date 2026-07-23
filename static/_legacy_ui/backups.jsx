function backupPost(path, body, role) {
  return fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-console-role": role || "dba" },
    body: JSON.stringify(body || {})
  }).then(hbzJsonResponse);
}

function backupSeconds(value) {
  if (value === null || value === undefined || value === "") return "-";
  var seconds = Math.max(0, Number(value) || 0);
  var hours = Math.floor(seconds / 3600);
  var minutes = Math.floor((seconds % 3600) / 60);
  var secs = seconds % 60;
  if (hours > 0) return hours + "h " + String(minutes).padStart(2, "0") + "m";
  return String(minutes).padStart(2, "0") + ":" + String(secs).padStart(2, "0");
}

function backupPill(status) {
  if (status === "ok" || status === "succeeded") return "ok";
  if (status === "warning" || status === "pending_approval") return "warn";
  if (status === "critical" || status === "failed") return "danger";
  return "muted";
}

function BackupRecoveryScreen({ lastRefresh }) {
  var tabState = React.useState("backups");
  var dataState = React.useState({ summary: {}, repo: {}, archive: {}, settings: {}, schedules: [], history: [], source: "" });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var toastState = React.useState(null);
  var busyState = React.useState(null);
  var backupTypeState = React.useState("incr");
  var backupReasonState = React.useState("Phase 6 ad-hoc backup validation");
  var validationTypeState = React.useState("archive");
  var validationReasonState = React.useState("Phase 6 backup validation");
  var pitrTypeState = React.useState("time");
  var pitrValueState = React.useState(new Date(Date.now() - 15 * 60 * 1000).toISOString().slice(0, 19) + "Z");
  var pitrActionState = React.useState("pause");
  var pitrReasonState = React.useState("Phase 6 PITR restore approval request");
  var pitrPreviewState = React.useState(null);
  var cloneNameState = React.useState("uat-pgcluster-uae-clone");
  var cloneNsState = React.useState("uat-pgcluster-uae");
  var cloneReasonState = React.useState("Phase 6 clone and fork approval request");
  var scheduleReasonState = React.useState("Phase 6 backup schedule change validation");
  var scheduleState = React.useState([]);

  var tab = tabState[0], setTab = tabState[1];
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var toast = toastState[0], setToast = toastState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var backupType = backupTypeState[0], setBackupType = backupTypeState[1];
  var backupReason = backupReasonState[0], setBackupReason = backupReasonState[1];
  var validationType = validationTypeState[0], setValidationType = validationTypeState[1];
  var validationReason = validationReasonState[0], setValidationReason = validationReasonState[1];
  var pitrType = pitrTypeState[0], setPitrType = pitrTypeState[1];
  var pitrValue = pitrValueState[0], setPitrValue = pitrValueState[1];
  var pitrAction = pitrActionState[0], setPitrAction = pitrActionState[1];
  var pitrReason = pitrReasonState[0], setPitrReason = pitrReasonState[1];
  var pitrPreview = pitrPreviewState[0], setPitrPreview = pitrPreviewState[1];
  var restoreConfirmState = React.useState(false);
  var restoreConfirm = restoreConfirmState[0], setRestoreConfirm = restoreConfirmState[1];
  var cloneName = cloneNameState[0], setCloneName = cloneNameState[1];
  var cloneNs = cloneNsState[0], setCloneNs = cloneNsState[1];
  var cloneReason = cloneReasonState[0], setCloneReason = cloneReasonState[1];
  var scheduleReason = scheduleReasonState[0], setScheduleReason = scheduleReasonState[1];
  var schedules = scheduleState[0], setSchedules = scheduleState[1];

  function showToast(kind, message) {
    setToast({ kind: kind, message: message });
    window.setTimeout(function() { setToast(null); }, 4200);
  }

  function loadBackups() {
    setLoading(true);
    setError(null);
    return v1Json(clusterPath("/backups"), {})
      .then(function(payload) {
        setData(payload);
        if (payload.schedules && payload.schedules.length) {
          setSchedules(payload.schedules.map(function(row) {
            return {
              type: row.type,
              cron: row.cron,
              retention_days: row.retention_days,
              enabled: row.enabled
            };
          }));
        }
        setLoading(false);
        return payload;
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setLoading(false);
      });
  }

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    v1Json(clusterPath("/backups"), {})
      .then(function(payload) {
        if (!alive) return;
        setData(payload);
        if (payload.schedules && payload.schedules.length) {
          setSchedules(payload.schedules.map(function(row) {
            return {
              type: row.type,
              cron: row.cron,
              retention_days: row.retention_days,
              enabled: row.enabled
            };
          }));
        }
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  function refreshAfter(message, kind) {
    return loadBackups().then(function() {
      if (message) showToast(kind || "ok", message);
    });
  }

  function submitBackup() {
    if (!hbzRequired(backupReason)) { showToast("danger", "Reason is required for backup validation."); return; }
    setBusy("backup");
    backupPost(clusterPath("/backups"), {
      backupType: backupType,
      reason: backupReason
    }, "dba")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("Backup validation job " + payload.state, payload.approval_required ? "warn" : "ok");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", err.message || String(err));
      });
  }

  function submitValidation() {
    if (!hbzRequired(validationReason)) { showToast("danger", "Reason is required for backup validation."); return; }
    setBusy("validate");
    backupPost(clusterPath("/backups/validate"), {
      validationType: validationType,
      reason: validationReason
    }, "operator")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("Backup validation " + payload.state, "ok");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", err.message || String(err));
      });
  }

  function pitrBody(reason) {
    var body = { targetType: pitrType, targetAction: pitrAction, reason: reason || pitrReason };
    if (pitrType === "time") body.targetTime = pitrValue;
    if (pitrType === "xid") body.targetXid = pitrValue;
    if (pitrType === "lsn") body.targetLsn = pitrValue;
    if (pitrType === "name") body.targetName = pitrValue;
    return body;
  }

  function submitPreview() {
    if (!hbzRequired(pitrValue) || !hbzRequired(pitrReason)) { showToast("danger", "Enter a recovery target and a reason before previewing."); return; }
    if (pitrTimeInvalid === "not-a-date") { showToast("danger", "Recovery target time is not a valid timestamp."); return; }
    if (pitrTimeInvalid === "future") { showToast("danger", "Recovery target time cannot be in the future."); return; }
    setBusy("preview");
    backupPost(clusterPath("/pitr/preview"), pitrBody(pitrReason), "dba")
      .then(function(payload) {
        setPitrPreview(payload.preview || null);
        setBusy(null);
        showToast("ok", "PITR preview generated");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", hbzErrorText(err));
      });
  }

  function submitRestore() {
    if (!hbzRequired(pitrValue) || !hbzRequired(pitrReason)) { showToast("danger", "Enter a recovery target and a reason before requesting a restore."); return; }
    if (pitrTimeInvalid === "not-a-date") { showToast("danger", "Recovery target time is not a valid timestamp."); return; }
    if (pitrTimeInvalid === "future") { showToast("danger", "Recovery target time cannot be in the future."); return; }
    setRestoreConfirm(true);
  }

  function runRestore() {
    setRestoreConfirm(false);
    setBusy("restore");
    backupPost(clusterPath("/pitr/restore"), pitrBody(pitrReason), "admin")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("PITR restore request " + payload.state, "warn");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", hbzErrorText(err));
      });
  }

  function updateSchedule(index, key, value) {
    setSchedules(schedules.map(function(row, i) {
      if (i !== index) return row;
      var next = Object.assign({}, row);
      next[key] = key === "retention_days" ? Number(value) : value;
      return next;
    }));
  }

  function submitSchedules() {
    var validSchedules = schedules.every(function(row) { return hbzRequired(row.cron) && hbzPositiveNumber(row.retention_days); });
    if (!validSchedules || !hbzRequired(scheduleReason)) { showToast("danger", "Each schedule needs a cron, positive retention, and reason."); return; }
    setBusy("schedules");
    backupPost(clusterPath("/backups/schedules"), {
      schedules: schedules,
      reason: scheduleReason
    }, "dba")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("Schedule change request " + payload.state, payload.approval_required ? "warn" : "ok");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", err.message || String(err));
      });
  }

  function submitClone() {
    if (!hbzNameLike(cloneName) || !hbzNameLike(cloneNs) || !hbzRequired(cloneReason)) { showToast("danger", "Use valid target names and provide a clone reason."); return; }
    setBusy("clone");
    backupPost(clusterPath("/clone"), {
      targetCluster: cloneName,
      targetNamespace: cloneNs,
      targetType: "immediate",
      targetAction: "pause",
      reason: cloneReason
    }, "dba")
      .then(function(payload) {
        setBusy(null);
        return refreshAfter("Clone request " + payload.state, "warn");
      })
      .catch(function(err) {
        setBusy(null);
        showToast("danger", err.message || String(err));
      });
  }

  var summary = data.summary || {};
  var archive = data.archive || {};
  var repo = data.repo || {};
  var settings = data.settings || {};
  var history = data.history || [];
  var pending = history.filter(function(row) { return row.state === "pending_approval"; }).length;
  var failures = Number(summary.archive_failed_count || archive.failed_count || 0);
  var status = summary.status || "unknown";
  var archiveRows = [
    { label: "Archived WAL", value: Number(archive.archived_count || 0), tone: "ok" },
    { label: "Failed WAL", value: failures, tone: failures ? "danger" : "muted" },
  ];
  var rpoRows = [
    { label: "RPO age", value: Number(summary.last_archive_age_seconds || 0), tone: Number(summary.last_archive_age_seconds || 0) > 900 ? "warn" : "ok" },
  ];
  var jobStateRows = phaseCountRows(history, function(row) { return row.state; });
  var jobKindRows = phaseCountRows(history, function(row) { return row.kind; }, function() { return "info"; });
  var scheduleRows = schedules.map(function(row) { return { label: row.type, value: Number(row.retention_days || 0), sub: row.cron, tone: row.enabled ? "ok" : "muted" }; });
  var pgo = data.pgo || {};
  var pgbackrest = pgo.pgbackrest || {};
  var pgoRepos = pgbackrest.repos || [];
  var standby = pgo.standby || {};
  var canSubmitBackup = hbzRequired(backupReason);
  var canSubmitValidation = hbzRequired(validationReason);
  var pitrTimeInvalid = pitrType === "time" && hbzRequired(pitrValue) && (function() {
    var t = Date.parse(String(pitrValue).trim());
    if (!Number.isFinite(t)) return "not-a-date";
    if (t > Date.now() + 60 * 1000) return "future";
    return null;
  })();
  var canSubmitPitr = hbzRequired(pitrValue) && hbzRequired(pitrReason) && !pitrTimeInvalid;
  var canSubmitSchedules = hbzRequired(scheduleReason) && schedules.every(function(row) { return hbzRequired(row.cron) && hbzPositiveNumber(row.retention_days); });
  var canSubmitClone = hbzNameLike(cloneName) && hbzNameLike(cloneNs) && hbzRequired(cloneReason);

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source}>
        <span className={"pill " + backupPill(status)}><span className="dot"/>{status}</span>
        <span className="pill muted"><Icon.HardDrive size={12}/>{repo.repo || "repo1"}</span>
        <span className="pill muted"><Icon.Database size={12}/>{repo.stanza || (activeCluster().name || "uat-pgcluster-uae")}</span>
        <button className="btn sm" onClick={loadBackups} disabled={loading}>
          <Icon.RefreshCw size={12}/> Refresh
        </button>
      </Phase1Toolbar>

      <div className="grid-4">
        <Stat label="Repo status" value={String(status).toUpperCase()} sub={repo.uri || "-"}/>
        <Stat label="Archive RPO age" value={backupSeconds(summary.last_archive_age_seconds)} sub={archive.last_archived_wal || "no archived WAL observed"}/>
        <Stat label="Archive failures" value={failures} sub={archive.last_failed_wal || "no failed WAL recorded"}/>
        <Stat label="Approval queue" value={pending} sub="Phase 6 backup/recovery jobs"/>
      </div>

      <div className="grid-4">
        <div className="card"><div className="bd"><DonutChart title="Archive Result" rows={archiveRows} center={fmtInt(Number(archive.archived_count || 0))} sub="archived"/></div></div>
        <div className="card"><div className="bd"><BarList title="RPO Age" rows={rpoRows} valueFormatter={backupSeconds}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Job State" rows={jobStateRows} center={history.length} sub="jobs"/></div></div>
        <div className="card"><div className="bd"><BarList title="Retention Days" rows={scheduleRows} valueFormatter={function(v) { return Math.round(v) + (Math.round(v) === 1 ? " day" : " days"); }}/></div></div>
      </div>

      {(archive.error || settings.error) && (
        <div className="risk-banner high">
          <Icon.AlertTriangle size={16}/>
          <div>{archive.error || settings.error}</div>
        </div>
      )}
      <div className="risk-banner info">
        <Icon.ShieldAlert size={16}/>
        <div>Phase 6 starts with read-only PostgreSQL archive checks and console job records. Restore, clone, pgBackRest, pod exec, and schedule mutation commands are not executed by this screen.</div>
      </div>


      {(pgo.available || pgo.error) && (
        <div className="grid-2">
          <div className="card">
            <div className="hd">PGO pgBackRest Repository <span className="meta">live CR</span></div>
            <div className="bd">
              <div className="grid-2">
                <Stat label="PostgresCluster CR" value={pgo.available ? "Reachable" : "Unavailable"} sub={pgo.error || pgo.name || "-"}/>
                <Stat label="Repo fallback" value={standby.enabled ? "Standby enabled" : "Primary repo"} sub={standby.repoName || repo.repo || "repo1"}/>
              </div>
              <table className="tbl mt-3">
                <thead><tr><th>Repo</th><th>Bucket</th><th>Endpoint</th><th>Schedules</th></tr></thead>
                <tbody>
                  {pgoRepos.map(function(row) {
                    var s3 = row.s3 || {};
                    var rs = row.schedules || {};
                    return <tr key={row.name || s3.bucket}><td className="mono">{row.name || "repo"}</td><td>{s3.bucket || "-"}</td><td>{s3.endpoint || "-"}</td><td className="mono txt-xs">{[rs.full, rs.diff, rs.incr].filter(Boolean).join(" | ") || "-"}</td></tr>;
                  })}
                  {pgoRepos.length === 0 && <tr><td colSpan="4" style={{textAlign:"center", padding:18}} className="muted">No repo entries visible from the PostgresCluster CR.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card">
            <div className="hd">Fallback Readiness <span className="meta">streaming plus repo</span></div>
            <div className="bd">
              <div className="risk-banner info" style={{margin: 0}}>
                <Icon.ShieldAlert size={16}/>
                <div>Streaming replication remains the primary HA path. pgBackRest repo metadata is shown here as the repo-based fallback path for DR bootstrap/PITR evidence.</div>
              </div>
              <pre className="logbox mt-3" style={{whiteSpace:"pre-wrap"}}>{JSON.stringify({standby: standby, pgbackrest_status: pgbackrest.status || null}, null, 2)}</pre>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="tabs">
          {[
            ["backups", "Backups"],
            ["pitr", "PITR Wizard"],
            ["schedules", "Schedules"],
            ["validation", "Validation"],
            ["clone", "Clone & Fork"]
          ].map(function(item) {
            return <button key={item[0]} className={tab === item[0] ? "active" : ""} onClick={function() { setTab(item[0]); }}>{item[1]}</button>;
          })}
        </div>
      </div>

      {tab === "backups" && (
        <>
          <div className="grid-2">
            <div className="card">
              <div className="hd">Repository <span className="meta">{repo.s3_endpoint || "Repository endpoint unavailable"}</span></div>
              <div className="bd">
                <div className="grid-2 txt-sm">
                  <div><div className="txt-xs muted">Repo</div><span className="mono">{repo.repo || "-"}</span></div>
                  <div><div className="txt-xs muted">Stanza</div><span className="mono">{repo.stanza || "-"}</span></div>
                  <div><div className="txt-xs muted">Bucket</div><span className="mono">{repo.bucket || "-"}</span></div>
                  <div><div className="txt-xs muted">Archive mode</div><span className="mono">{settings.archive_mode || "-"}</span></div>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="hd">Ad-hoc Backup Validation</div>
              <div className="bd">
                <div className="grid-2">
                  <div className="field" style={{marginTop: 0}}>
                    <label>Type</label>
                    <select value={backupType} onChange={function(e) { setBackupType(e.target.value); }}>
                      <option value="incr">Incremental</option>
                      <option value="diff">Differential</option>
                      <option value="full">Full</option>
                    </select>
                  </div>
                  <div className="field" style={{marginTop: 0}}>
                    <label>Reason</label>
                    <input type="text" value={backupReason} onChange={function(e) { setBackupReason(e.target.value); }}/>
                  </div>
                </div>
                <button className="btn sm primary mt-3" onClick={submitBackup} disabled={busy === "backup" || !canSubmitBackup}>
                  <Icon.Play size={12}/> {busy === "backup" ? "Submitting" : "Submit"}
                </button>
              </div>
            </div>
          </div>

          {(function() {
            var pgbk = data.backups || [];
            var pgSummary = data.summary || {};
            var backupCount = pgbk.length;
            var fullCount = pgbk.filter(function(b) { return b.type === "full"; }).length;
            var diffCount = pgbk.filter(function(b) { return b.type === "diff"; }).length;
            var incrCount = pgbk.filter(function(b) { return b.type === "incr"; }).length;
            var latestBackup = pgbk.length ? pgbk[0] : null;
            if (pgbk.length === 0) return null;
            var typeColor = { full: "var(--clr-ok)", diff: "var(--clr-info)", incr: "var(--clr-teal)" };
            var bkTypeRows = [
              { label: "Full", value: fullCount, tone: "ok" },
              { label: "Diff", value: diffCount, tone: "info" },
              { label: "Incr", value: incrCount, tone: "teal" },
            ];
            var bkSizeRows = pgbk.map(function(b) {
              return { label: b.label || "?", value: Math.max(1, Number(b.repo_size_bytes || 0)), sub: b.type + " / " + (b.duration_human || "?"), tone: b.type === "full" ? "ok" : b.type === "diff" ? "info" : "teal" };
            });
            return React.createElement(React.Fragment, null,
              React.createElement("div", { className: "grid-4" },
                React.createElement(Stat, { label: "pgBackRest backups", value: fmtInt(backupCount), sub: "full/diff/incr chain" }),
                React.createElement(Stat, { label: "Latest backup", value: latestBackup ? latestBackup.type : "none", sub: latestBackup ? latestBackup.duration_human : "-" }),
                React.createElement(Stat, { label: "Latest full", value: (pgbk.find(function(b) { return b.type === "full"; }) || {}).stop_time || "none", sub: (pgbk.find(function(b) { return b.type === "full"; }) || {}).duration_human || "-" }),
                React.createElement(Stat, { label: "Repo size", value: pgbk.length ? fmtBytes(pgbk.reduce(function(sum, b) { return sum + Number(b.repo_size_bytes || 0); }, 0)) : "Unavailable", sub: "compressed" })
              ),
              React.createElement("div", { className: "grid-2" },
                React.createElement("div", { className: "card" }, React.createElement("div", { className: "bd" }, React.createElement(DonutChart, { title: "Backup Types", rows: bkTypeRows, center: fmtInt(backupCount), sub: "backups" }))),
                React.createElement("div", { className: "card" }, React.createElement("div", { className: "bd" }, React.createElement(BarList, { title: "Backup Sizes (repo)", rows: bkSizeRows, valueFormatter: fmtBytes })))
              ),
              React.createElement("div", { className: "card" },
                React.createElement("div", { className: "hd" }, "pgBackRest Backup History ", React.createElement("span", { className: "meta" }, pgbk.length + " backups")),
                React.createElement("div", { style: { overflowX: "auto" } },
                  React.createElement("table", { className: "tbl" },
                    React.createElement("thead", null, React.createElement("tr", null,
                      React.createElement("th", null, "Label"),
                      React.createElement("th", null, "Type"),
                      React.createElement("th", null, "Stop time"),
                      React.createElement("th", null, "Duration"),
                      React.createElement("th", { className: "num" }, "DB size"),
                      React.createElement("th", { className: "num" }, "Repo size"),
                      React.createElement("th", null, "WAL range")
                    )),
                    React.createElement("tbody", null,
                      pgbk.map(function(b, i) {
                        return React.createElement("tr", { key: i },
                          React.createElement("td", { className: "mono txt-xs" }, b.label),
                          React.createElement("td", null, React.createElement("span", { className: "pill " + (b.type === "full" ? "ok" : b.type === "diff" ? "info" : "teal") }, b.type)),
                          React.createElement("td", null, b.stop_time ? b.stop_time.replace("T", " ").substring(0, 19) : "-"),
                          React.createElement("td", null, b.duration_human || "-"),
                          React.createElement("td", { className: "num" }, fmtBytes(Number(b.database_size_bytes || 0))),
                          React.createElement("td", { className: "num" }, fmtBytes(Number(b.repo_size_bytes || 0))),
                          React.createElement("td", { className: "mono txt-xs" }, (b.wal_start || "-") + " → " + (b.wal_stop || "-"))
                        );
                      }),
                      pgbk.length === 0 ? React.createElement("tr", null, React.createElement("td", { colSpan: "7", className: "muted", style: { textAlign: "center", padding: 24 } }, "pgBackRest info not available from cluster.")) : null
                    )
                  )
                )
              )
            );
          })()}

          <div className="card">
            <div className="bd"><BarList title="Console Job Kinds" rows={jobKindRows}/></div>
          </div>

          <div className="card">
            <div className="hd">Console Backup Jobs <span className="meta">{history.length} rows</span></div>
            <div style={{overflowX: "auto"}}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Submitted</th><th>Type</th><th>Kind</th><th>Target</th><th>Actor</th><th>State</th><th>Request</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map(function(row) {
                    return (
                      <tr key={row.id} className={row.state === "pending_approval" ? "row-warn" : row.state === "failed" ? "row-danger" : ""}>
                        <td>{phase1Date(row.submitted_at)}</td>
                        <td className="mono">{row.type}</td>
                        <td className="mono">{row.kind}</td>
                        <td>{row.target || "-"}</td>
                        <td>{row.submitted_by || "-"}</td>
                        <td><span className={"pill " + phase1Pill(row.state)}><span className="dot"/>{row.state}</span></td>
                        <td className="mono txt-xs">{row.request_id}</td>
                      </tr>
                    );
                  })}
                  {!loading && history.length === 0 && (
                    <tr><td colSpan="7" className="muted" style={{textAlign: "center", padding: 26}}>No Phase 6 backup or recovery jobs yet.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {tab === "pitr" && (
        <div className="grid-2">
          <div className="card">
            <div className="hd">Recovery Target</div>
            <div className="bd">
              <div className="grid-2">
                <div className="field" style={{marginTop: 0}}>
                  <label>Target type</label>
                  <select value={pitrType} onChange={function(e) { setPitrType(e.target.value); }}>
                    <option value="time">Time</option>
                    <option value="xid">XID</option>
                    <option value="lsn">LSN</option>
                    <option value="name">Name</option>
                  </select>
                </div>
                <div className="field" style={{marginTop: 0}}>
                  <label>Target value</label>
                  <input type="text" value={pitrValue} placeholder={pitrType === "time" ? "YYYY-MM-DDTHH:MM:SSZ" : ""} onChange={function(e) { setPitrValue(e.target.value); }}/>
                  {pitrTimeInvalid === "not-a-date" && <div className="field-hint warn">Enter a valid timestamp, e.g. 2026-06-17T09:30:00Z.</div>}
                  {pitrTimeInvalid === "future" && <div className="field-hint warn">Recovery target cannot be in the future — choose a time at or before now.</div>}
                </div>
                <div className="field">
                  <label>Target action</label>
                  <select value={pitrAction} onChange={function(e) { setPitrAction(e.target.value); }}>
                    <option value="pause">Pause</option>
                    <option value="promote">Promote</option>
                    <option value="shutdown">Shutdown</option>
                  </select>
                </div>
                <div className="field">
                  <label>Reason</label>
                  <input type="text" value={pitrReason} onChange={function(e) { setPitrReason(e.target.value); }}/>
                </div>
              </div>
              <div className="flex-row mt-3">
                <button className="btn sm primary" onClick={submitPreview} disabled={busy === "preview" || !canSubmitPitr}>
                  <Icon.Eye size={12}/> {busy === "preview" ? "Previewing" : "Preview"}
                </button>
                <button className="btn sm danger" onClick={submitRestore} disabled={busy === "restore" || !canSubmitPitr}>
                  <Icon.RotateCcw size={12}/> {busy === "restore" ? "Requesting" : "Request Restore"}
                </button>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="hd">Preview</div>
            <div className="bd">
              {pitrPreview ? (
                <div className="flex-col txt-sm">
                  <div><div className="txt-xs muted">Repo</div><span className="mono">{pitrPreview.repo.repo}</span></div>
                  <div><div className="txt-xs muted">Target</div><span className="mono">{pitrPreview.recovery_target.type}: {pitrPreview.recovery_target.value}</span></div>
                  <div><div className="txt-xs muted">Action</div><span className="mono">{pitrPreview.recovery_target.action}</span></div>
                  <span className="pill warn"><span className="dot"/>Second approval required</span>
                </div>
              ) : (
                <div className="muted">No preview generated for the current target.</div>
              )}
            </div>
          </div>
        </div>
      )}

      {tab === "schedules" && (
        <div className="card">
          <div className="hd">Backup Schedules <span className="meta">repo {repo.repo || "repo1"}</span></div>
          <div className="bd">
            <div style={{overflowX: "auto"}}>
              <table className="tbl">
                <thead><tr><th>Type</th><th>Cron</th><th>Retention days</th><th>Enabled</th></tr></thead>
                <tbody>
                  {schedules.map(function(row, index) {
                    return (
                      <tr key={row.type}>
                        <td className="mono">{row.type}</td>
                        <td><input type="text" value={row.cron} onChange={function(e) { updateSchedule(index, "cron", e.target.value); }} style={{width: "100%", minWidth: 180}}/></td>
                        <td><input type="number" min="1" max="365" value={row.retention_days} onChange={function(e) { updateSchedule(index, "retention_days", e.target.value); }} style={{width: 110}}/></td>
                        <td><span className={"pill " + (row.enabled ? "ok" : "muted")}><span className="dot"/>{row.enabled ? "enabled" : "disabled"}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="field">
              <label>Reason</label>
              <input type="text" value={scheduleReason} onChange={function(e) { setScheduleReason(e.target.value); }}/>
            </div>
            <button className="btn sm primary mt-3" onClick={submitSchedules} disabled={busy === "schedules" || !canSubmitSchedules}>
              <Icon.Save size={12}/> {busy === "schedules" ? "Validating" : "Validate Change"}
            </button>
          </div>
        </div>
      )}

      {tab === "validation" && (
        <div className="card">
          <div className="hd">Backup Validation</div>
          <div className="bd">
            <div className="grid-2">
              <div className="field" style={{marginTop: 0}}>
                <label>Validation</label>
                <select value={validationType} onChange={function(e) { setValidationType(e.target.value); }}>
                  <option value="archive">Archive continuity</option>
                  <option value="repo">Repository metadata</option>
                  <option value="latest">Latest backup</option>
                  <option value="restore_drill">Restore drill</option>
                </select>
              </div>
              <div className="field" style={{marginTop: 0}}>
                <label>Reason</label>
                <input type="text" value={validationReason} onChange={function(e) { setValidationReason(e.target.value); }}/>
              </div>
            </div>
            <button className="btn sm primary mt-3" onClick={submitValidation} disabled={busy === "validate" || !canSubmitValidation}>
              <Icon.CheckCircle size={12}/> {busy === "validate" ? "Validating" : "Validate"}
            </button>
          </div>
        </div>
      )}

      {tab === "clone" && (
        <div className="card">
          <div className="hd">Clone &amp; Fork</div>
          <div className="bd">
            <div className="grid-3">
              <div className="field" style={{marginTop: 0}}>
                <label>Target cluster</label>
                <input type="text" value={cloneName} onChange={function(e) { setCloneName(e.target.value); }}/>
              </div>
              <div className="field" style={{marginTop: 0}}>
                <label>Target namespace</label>
                <input type="text" value={cloneNs} onChange={function(e) { setCloneNs(e.target.value); }}/>
              </div>
              <div className="field" style={{marginTop: 0}}>
                <label>Reason</label>
                <input type="text" value={cloneReason} onChange={function(e) { setCloneReason(e.target.value); }}/>
              </div>
            </div>
            <div className="risk-banner mt-3">
              <Icon.ShieldAlert size={16}/>
              <div>Clone requests are stored as approval-pending jobs. The source cluster remains untouched by this Phase 6 screen.</div>
            </div>
            <button className="btn sm danger mt-3" onClick={submitClone} disabled={busy === "clone" || !canSubmitClone}>
              <Icon.GitBranch size={12}/> {busy === "clone" ? "Requesting" : "Request Clone"}
            </button>
          </div>
        </div>
      )}

      {restoreConfirm && (
        <Modal onClose={function() { setRestoreConfirm(false); }}>
          <div className="hd">Confirm point-in-time restore request</div>
          <div className="bd">
            <p className="modal-lead">This submits an <strong>approval-pending PITR restore</strong> request to pgBackRest for the cluster below. On approval and execution it recovers PostgreSQL to the chosen target.</p>
            <ul className="impact-list">
              <li><span>Cluster</span><strong>{(activeCluster() || {}).name || "\u2014"}</strong></li>
              <li><span>Recovery target</span><strong>{pitrType.toUpperCase()} · {pitrValue}</strong></li>
              <li><span>Target action</span><strong>{pitrAction}</strong></li>
              <li><span>Reason</span><strong>{pitrReason}</strong></li>
            </ul>
            <div className="risk-banner high">
              <Icon.AlertTriangle size={14}/>
              <div>A PITR restore rewinds the database to the recovery target. Transactions committed after that point are lost once the restore is executed.</div>
            </div>
          </div>
          <div className="ft">
            <button className="btn sm" onClick={function() { setRestoreConfirm(false); }}>Cancel</button>
            <button className="btn sm danger" onClick={runRestore}><Icon.RotateCcw size={12}/> Submit restore request</button>
          </div>
        </Modal>
      )}

      {toast && (
        <div className={"session-toast " + toast.kind}>
          {toast.kind === "danger" ? <Icon.AlertTriangle size={15}/> : <Icon.CheckCircle size={15}/>}
          <span>{toast.message}</span>
          <button className="btn ghost icon" onClick={function() { setToast(null); }}><Icon.X size={12}/></button>
        </div>
      )}
    </div>
  );
}
window.BackupRecoveryScreen = BackupRecoveryScreen;
