// Phase 8 Lifecycle screens.

function lifecyclePost(path, body, role) {
  return fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-console-role": role || "admin" },
    body: JSON.stringify(body || {})
  }).then(hbzJsonResponse);
}

function lifecycleValue(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback || "-";
  return value;
}

function LifecycleScreen({ view, lastRefresh }) {
  var selectedCluster = activeCluster();
  var dataState = React.useState({ current: {}, jobs: [], source: "" });
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var toastState = React.useState(null);
  var busyState = React.useState(null);

  var clusterNameState = React.useState("uat-pgcluster-uae-new");
  var namespaceState = React.useState("uat-pgcluster-uae");
  var replicasState = React.useState(2);
  var cpuState = React.useState(16);
  var memoryState = React.useState(64);
  var storageState = React.useState(2048);
  var pgbouncerState = React.useState(2);
  var replicaNameState = React.useState("uat-pgcluster-uae-replica");
  var replicaActionState = React.useState("add");
  var targetVersionState = React.useState("18");
  var upgradeModeState = React.useState("preflight");
  var confirmationState = React.useState("");
  var reasonState = React.useState("Phase 8 lifecycle validation");

  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var toast = toastState[0], setToast = toastState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var clusterName = clusterNameState[0], setClusterName = clusterNameState[1];
  var namespace = namespaceState[0], setNamespace = namespaceState[1];
  var replicas = replicasState[0], setReplicas = replicasState[1];
  var cpu = cpuState[0], setCpu = cpuState[1];
  var memory = memoryState[0], setMemory = memoryState[1];
  var storage = storageState[0], setStorage = storageState[1];
  var pgbouncer = pgbouncerState[0], setPgbouncer = pgbouncerState[1];
  var replicaName = replicaNameState[0], setReplicaName = replicaNameState[1];
  var replicaAction = replicaActionState[0], setReplicaAction = replicaActionState[1];
  var targetVersion = targetVersionState[0], setTargetVersion = targetVersionState[1];
  var upgradeMode = upgradeModeState[0], setUpgradeMode = upgradeModeState[1];
  var confirmation = confirmationState[0], setConfirmation = confirmationState[1];
  var reason = reasonState[0], setReason = reasonState[1];

  function showToast(kind, message) {
    setToast({ kind: kind, message: message });
    window.setTimeout(function() { setToast(null); }, 4200);
  }

  function endpoint() {
    if (view === "provision") return "/api/v1/lifecycle/provision/defaults";
    if (view === "scale") return lifecyclePath("scale");
    if (view === "replicas") return lifecyclePath("replicas");
    if (view === "upgrade") return lifecyclePath("upgrade");
    return lifecyclePath("decommission");
  }

  function loadLifecycle() {
    setLoading(true);
    setError(null);
    return v1Json(endpoint(), {})
      .then(function(payload) {
        setData(payload || {});
        var defaults = (payload && payload.defaults) || {};
        var resources = ((payload && payload.current && payload.current.resources) || (payload && payload.current && payload.current.current && payload.current.current.resources) || {});
        if (defaults.replicas || resources.cpu_cores) {
          setReplicas(defaults.replicas || replicas);
          setCpu(defaults.cpuCores || resources.cpu_cores || cpu);
          setMemory(defaults.memoryGiB || resources.ram_gib || memory);
          setStorage(defaults.storageGiB || resources.storage_gib || storage);
          setPgbouncer(defaults.pgbouncerReplicas || resources.pgbouncer_pods || pgbouncer);
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
    v1Json(endpoint(), {})
      .then(function(payload) {
        if (!alive) return;
        setData(payload || {});
        var defaults = payload.defaults || {};
        var resources = ((payload.current || {}).resources || ((payload.current || {}).current || {}).resources || {});
        if (defaults.replicas || resources.cpu_cores) {
          setReplicas(defaults.replicas || replicas);
          setCpu(defaults.cpuCores || resources.cpu_cores || cpu);
          setMemory(defaults.memoryGiB || resources.ram_gib || memory);
          setStorage(defaults.storageGiB || resources.storage_gib || storage);
          setPgbouncer(defaults.pgbouncerReplicas || resources.pgbouncer_pods || pgbouncer);
        }
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [view, lastRefresh]);

  function refreshAfter(message) {
    return loadLifecycle().then(function() { showToast("warn", message); });
  }

  function submitProvision() {
    if (!hbzNameLike(clusterName) || !hbzNameLike(namespace) || !hbzPositiveNumber(replicas) || !hbzPositiveNumber(cpu) || !hbzPositiveNumber(memory) || !hbzPositiveNumber(storage) || !hbzRequired(reason)) {
      showToast("danger", "Provisioning requires valid names, positive resources, and a reason.");
      return;
    }
    setBusy("provision");
    lifecyclePost("/api/v1/lifecycle/provision", {
      clusterName: clusterName,
      namespace: namespace,
      postgresVersion: "18",
      replicas: Number(replicas),
      cpuCores: Number(cpu),
      memoryGiB: Number(memory),
      storageGiB: Number(storage),
      reason: reason
    }, "admin")
      .then(function(payload) { setBusy(null); return refreshAfter("Provision request " + payload.state); })
      .catch(function(err) { setBusy(null); showToast("danger", err.message || String(err)); });
  }

  function submitScale() {
    if (!hbzPositiveNumber(replicas) || !hbzPositiveNumber(cpu) || !hbzPositiveNumber(memory) || !hbzPositiveNumber(storage) || !hbzPositiveNumber(pgbouncer) || !hbzRequired(reason)) {
      showToast("danger", "Scale validation requires positive resources and a reason.");
      return;
    }
    setBusy("scale");
    lifecyclePost(lifecyclePath("scale"), {
      replicas: Number(replicas),
      cpuCores: Number(cpu),
      memoryGiB: Number(memory),
      storageGiB: Number(storage),
      pgbouncerReplicas: Number(pgbouncer),
      reason: reason
    }, "admin")
      .then(function(payload) { setBusy(null); return refreshAfter("Scale request " + payload.state); })
      .catch(function(err) { setBusy(null); showToast("danger", err.message || String(err)); });
  }

  function submitReplicas() {
    if ((replicaAction === "add" || replicaAction === "remove") && !hbzNameLike(replicaName)) { showToast("danger", "Use a valid replica name."); return; }
    if (!hbzRequired(reason)) { showToast("danger", "Reason is required for replica validation."); return; }
    setBusy("replicas");
    lifecyclePost(lifecyclePath("replicas"), {
      action: replicaAction,
      replicaName: replicaName,
      walReceiverStatusIntervalSeconds: 10,
      reason: reason
    }, "dba")
      .then(function(payload) { setBusy(null); return refreshAfter("Replica request " + payload.state); })
      .catch(function(err) { setBusy(null); showToast("danger", err.message || String(err)); });
  }

  function submitUpgrade() {
    if (!hbzRequired(targetVersion) || !hbzRequired(upgradeMode) || !hbzRequired(reason)) { showToast("danger", "Target version, mode, and reason are required."); return; }
    setBusy("upgrade");
    lifecyclePost(lifecyclePath("upgrade"), {
      targetVersion: targetVersion,
      mode: upgradeMode,
      reason: reason
    }, "admin")
      .then(function(payload) { setBusy(null); return refreshAfter("Upgrade preflight " + payload.state); })
      .catch(function(err) { setBusy(null); showToast("danger", err.message || String(err)); });
  }

  function submitDecommission() {
    if (confirmation !== (selectedCluster.name || "uat-pgcluster-uae") || !hbzRequired(reason)) { showToast("danger", "Confirmation must match the cluster name and include a reason."); return; }
    setBusy("decommission");
    lifecyclePost(lifecyclePath("decommission"), {
      confirmation: confirmation,
      finalBackup: true,
      archiveEvidence: true,
      reason: reason
    }, "admin")
      .then(function(payload) { setBusy(null); return refreshAfter("Decommission request " + payload.state); })
      .catch(function(err) { setBusy(null); showToast("danger", err.message || String(err)); });
  }

  var current = data.current || {};
  var currentState = current.current || current;
  var cluster = currentState.cluster || {};
  var patroni = currentState.patroni || {};
  var resources = currentState.resources || {};
  var postgres = currentState.postgres || {};
  var jobs = data.jobs || [];
  var members = data.members || patroni.members || [];
  var pending = jobs.filter(function(job) { return job.state === "pending_approval"; }).length;
  var health = cluster.health || ((data.preflight || {}).cluster_healthy ? "healthy" : "unknown");
  var resourceRows = [
    { label: "CPU cores", value: Number(resources.cpu_cores || 0), tone: "info" },
    { label: "RAM GiB", value: Number(resources.ram_gib || 0), tone: "ok" },
    { label: "Storage GiB", value: Number(resources.storage_gib || 0), tone: "teal" },
    { label: "PgBouncer pods", value: Number(resources.pgbouncer_pods || 0), tone: "purple" },
  ];
  var memberRoleRows = phaseCountRows(members, function(member) { return member.role || "member"; }, function(role) { return role === "leader" ? "ok" : role === "sync_standby" ? "info" : "muted"; });
  var memberLagRows = members.map(function(member) { return { label: member.name, value: Number(member.lag || member.replay_lag || 0), sub: member.state || "-", tone: Number(member.lag || member.replay_lag || 0) > 0 ? "warn" : "ok" }; });
  var jobStateRows = phaseCountRows(jobs, function(job) { return job.state; });
  var canProvision = hbzNameLike(clusterName) && hbzNameLike(namespace) && hbzPositiveNumber(replicas) && hbzPositiveNumber(cpu) && hbzPositiveNumber(memory) && hbzPositiveNumber(storage) && hbzRequired(reason);
  var canScale = hbzPositiveNumber(replicas) && hbzPositiveNumber(cpu) && hbzPositiveNumber(memory) && hbzPositiveNumber(storage) && hbzPositiveNumber(pgbouncer) && hbzRequired(reason);
  var canReplica = hbzRequired(reason) && ((replicaAction !== "add" && replicaAction !== "remove") || hbzNameLike(replicaName));
  var canUpgrade = hbzRequired(targetVersion) && hbzRequired(upgradeMode) && hbzRequired(reason);
  var canDecommission = confirmation === (selectedCluster.name || "uat-pgcluster-uae") && hbzRequired(reason);

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data.source || currentState.source}>
        <span className={"pill " + phase1Pill(health === "healthy" ? "ok" : health === "unknown" ? "muted" : "warn")}><span className="dot"/>{health}</span>
        <span className="pill muted"><Icon.GitBranch size={12}/>Phase 8</span>
        <span className="pill muted"><Icon.Database size={12}/>{selectedCluster.name || "uat-pgcluster-uae"}</span>
        <button className="btn sm" onClick={loadLifecycle} disabled={loading}><Icon.RefreshCw size={12}/> Refresh</button>
      </Phase1Toolbar>

      <div className={view === "scale" || view === "replicas" ? "risk-banner" : "risk-banner info"}>
        <Icon.ShieldAlert size={16}/>
        {view === "scale" || view === "replicas" ? (
          <div>This records a preflight approval request. Once a second approver approves the job, Phase 5 patches the PostgresCluster CR ({view === "scale" ? "replicas/cpu/memory/storage/pgBouncer" : "+/-1 replica for add/remove; rebalance is a Patroni-managed no-op"}). This requires optional postgresclusters patch RBAC (deploy/openshift.yaml) -- without it the approved job fails with a clear RBAC error and nothing changes.</div>
        ) : (
          <div>Phase 8 records lifecycle preflight and approval requests only. No PostgresCluster CR, StatefulSet, PVC, Secret, Route, Patroni, pgBackRest, or PostgreSQL mutation is executed by these screens.</div>
        )}
      </div>

      <div className="grid-4">
        <Stat label="Patroni members" value={patroni.member_count || members.length || "-"} sub={(patroni.ready_members || 0) + " ready"}/>
        <Stat label="PostgreSQL" value={lifecycleValue(postgres.server_version, cluster.pg_version || "-")} sub="current version"/>
        <Stat label="Resources" value={(resources.cpu_cores || "-") + " vCPU"} sub={(resources.ram_gib || "-") + " GiB RAM · " + (resources.storage_gib || "-") + " GiB storage"}/>
        <Stat label="Lifecycle queue" value={pending} sub={jobs.length + " Phase 8 jobs"}/>
      </div>

      <div className="grid-4">
        <div className="card"><div className="bd"><BarList title="Current Resources" rows={resourceRows}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Member Roles" rows={memberRoleRows} center={members.length} sub="members"/></div></div>
        <div className="card"><div className="bd"><BarList title="Member Lag" rows={memberLagRows} valueFormatter={fmtBytes}/></div></div>
        <div className="card"><div className="bd"><DonutChart title="Lifecycle Queue" rows={jobStateRows} center={jobs.length} sub="jobs"/></div></div>
      </div>

      {view === "provision" && (
        <div className="grid-2">
          <div className="card">
            <div className="hd">Provisioning Request</div>
            <div className="bd">
              <div className="grid-2">
                <div className="field" style={{marginTop: 0}}><label>Cluster name</label><input value={clusterName} onChange={function(e) { setClusterName(e.target.value); }}/></div>
                <div className="field" style={{marginTop: 0}}><label>Namespace</label><input value={namespace} onChange={function(e) { setNamespace(e.target.value); }}/></div>
                <div className="field"><label>Replicas</label><input type="number" min="1" max="5" value={replicas} onChange={function(e) { setReplicas(e.target.value); }}/></div>
                <div className="field"><label>Storage GiB</label><input type="number" min="20" max="8192" value={storage} onChange={function(e) { setStorage(e.target.value); }}/></div>
                <div className="field"><label>CPU cores</label><input type="number" min="1" max="64" value={cpu} onChange={function(e) { setCpu(e.target.value); }}/></div>
                <div className="field"><label>Memory GiB</label><input type="number" min="2" max="512" value={memory} onChange={function(e) { setMemory(e.target.value); }}/></div>
              </div>
              <div className="field"><label>Reason</label><input value={reason} onChange={function(e) { setReason(e.target.value); }}/></div>
              <button className="btn sm primary mt-3" onClick={submitProvision} disabled={busy === "provision" || !canProvision}><Icon.Plus size={12}/> {busy === "provision" ? "Submitting" : "Submit Preflight"}</button>
            </div>
          </div>
          <LifecycleJobs jobs={jobs}/>
        </div>
      )}

      {view === "scale" && (
        <div className="grid-2">
          <div className="card">
            <div className="hd">Scale Validation</div>
            <div className="bd">
              <div className="grid-2">
                <div className="field" style={{marginTop: 0}}><label>Postgres replicas</label><input type="number" min="1" max="5" value={replicas} onChange={function(e) { setReplicas(e.target.value); }}/></div>
                <div className="field" style={{marginTop: 0}}><label>PgBouncer replicas</label><input type="number" min="1" max="8" value={pgbouncer} onChange={function(e) { setPgbouncer(e.target.value); }}/></div>
                <div className="field"><label>CPU cores</label><input type="number" min="1" max="64" value={cpu} onChange={function(e) { setCpu(e.target.value); }}/></div>
                <div className="field"><label>Memory GiB</label><input type="number" min="2" max="512" value={memory} onChange={function(e) { setMemory(e.target.value); }}/></div>
                <div className="field"><label>Storage GiB</label><input type="number" min="20" max="8192" value={storage} onChange={function(e) { setStorage(e.target.value); }}/></div>
                <div className="field"><label>Reason</label><input value={reason} onChange={function(e) { setReason(e.target.value); }}/></div>
              </div>
              <button className="btn sm primary mt-3" onClick={submitScale} disabled={busy === "scale" || !canScale}><Icon.Save size={12}/> {busy === "scale" ? "Validating" : "Validate Scale"}</button>
            </div>
          </div>
          <LifecycleJobs jobs={jobs}/>
        </div>
      )}

      {view === "replicas" && (
        <>
          <div className="grid-2">
            <div className="card">
              <div className="hd">Read Replica Request</div>
              <div className="bd">
                <div className="grid-2">
                  <div className="field" style={{marginTop: 0}}><label>Action</label><select value={replicaAction} onChange={function(e) { setReplicaAction(e.target.value); }}><option value="add">Add</option><option value="remove">Remove</option><option value="rebalance">Rebalance</option></select></div>
                  <div className="field" style={{marginTop: 0}}><label>Replica name</label><input value={replicaName} onChange={function(e) { setReplicaName(e.target.value); }}/></div>
                </div>
                <div className="field"><label>Reason</label><input value={reason} onChange={function(e) { setReason(e.target.value); }}/></div>
                <button className="btn sm primary mt-3" onClick={submitReplicas} disabled={busy === "replicas" || !canReplica}><Icon.GitBranch size={12}/> {busy === "replicas" ? "Validating" : "Validate Replica"}</button>
              </div>
            </div>
            <LifecycleJobs jobs={jobs}/>
          </div>
          <LifecycleMembers members={members}/>
        </>
      )}

      {view === "upgrade" && (
        <div className="grid-2">
          <div className="card">
            <div className="hd">Upgrade Preflight</div>
            <div className="bd">
              <div className="grid-2">
                <div className="field" style={{marginTop: 0}}><label>Target version</label><select value={targetVersion} onChange={function(e) { setTargetVersion(e.target.value); }}><option value="18">PostgreSQL 18</option></select></div>
                <div className="field" style={{marginTop: 0}}><label>Mode</label><select value={upgradeMode} onChange={function(e) { setUpgradeMode(e.target.value); }}><option value="preflight">Preflight</option><option value="minor">Minor</option><option value="major">Major</option></select></div>
              </div>
              <div className="field"><label>Reason</label><input value={reason} onChange={function(e) { setReason(e.target.value); }}/></div>
              <button className="btn sm primary mt-3" onClick={submitUpgrade} disabled={busy === "upgrade" || !canUpgrade}><Icon.ArrowRight size={12}/> {busy === "upgrade" ? "Validating" : "Validate Upgrade"}</button>
            </div>
          </div>
          <LifecycleJobs jobs={jobs}/>
        </div>
      )}

      {view === "decommission" && (
        <div className="grid-2">
          <div className="card">
            <div className="hd">Decommission Request</div>
            <div className="bd">
              <div className="risk-banner high"><Icon.AlertTriangle size={16}/><div>This creates an approval request only, but it is still treated as a high-risk lifecycle workflow.</div></div>
              <div className="field"><label>Type cluster name to confirm</label><input value={confirmation} onChange={function(e) { setConfirmation(e.target.value); }} placeholder={selectedCluster.name || "uat-pgcluster-uae"}/></div>
              <div className="field"><label>Reason</label><input value={reason} onChange={function(e) { setReason(e.target.value); }}/></div>
              <button className="btn sm danger mt-3" onClick={submitDecommission} disabled={busy === "decommission" || !canDecommission}><Icon.StopCircle size={12}/> {busy === "decommission" ? "Requesting" : "Request Decommission"}</button>
            </div>
          </div>
          <LifecycleJobs jobs={jobs}/>
        </div>
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

function LifecycleJobs({ jobs }) {
  var jobKindRows = phaseCountRows(jobs || [], function(job) { return job.kind; }, function() { return "info"; });
  return (
    <div className="card">
      <div className="hd">Lifecycle Jobs <span className="meta">{jobs.length} rows</span></div>
      <div className="bd flex-col txt-sm">
        <BarList title="Job Kinds" rows={jobKindRows}/>
        {jobs.slice(0, 8).map(function(job) {
          return <div key={job.id} className="flex-row"><span className={"pill " + phase1Pill(job.state)}>{job.state}</span><span className="mono">{job.kind}</span><span className="muted">{phase1Date(job.submitted_at)}</span></div>;
        })}
        {jobs.length === 0 && <div className="muted">No Phase 8 lifecycle jobs yet.</div>}
      </div>
    </div>
  );
}

function LifecycleMembers({ members }) {
  return (
    <div className="card">
      <div className="hd">Patroni Members <span className="meta">{members.length} rows</span></div>
      <div style={{overflowX: "auto"}}>
        <table className="tbl"><thead><tr><th>Name</th><th>Role</th><th>State</th><th>Lag</th><th>Host</th></tr></thead><tbody>
          {members.map(function(row) {
            return <tr key={row.name}><td className="mono">{row.name}</td><td>{row.role || "-"}</td><td><span className={"pill " + phase1Pill(row.state === "running" || row.state === "streaming" ? "ok" : "warn")}>{row.state || "-"}</span></td><td>{row.lag || 0}</td><td>{row.host || "-"}</td></tr>;
          })}
        </tbody></table>
      </div>
    </div>
  );
}
