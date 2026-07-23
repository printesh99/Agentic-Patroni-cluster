// DR & Cutover console module (Enterprise UI Plan, Phase B).
// UI over /api/v1/cutover/* — the vendored UK switchover engine wired into the
// console job/approval system. Backend verified 2026-06-12; this file is UI only.
//
// Flow: region cards → submit (kind + tier + reason) → submit-time engine
// dry-run manifest → 4-eyes approval (rehearsal/armed) → live run view with
// phase progress + log tail + cancel → run history.

const CUTOVER_PHASE_ORDER = [
  "01_planned_switchover",
  "02_rebuild_dc1",
  "03_switchback",
  "04_rebuild_dc2",
];

const CUTOVER_PHASE_LABELS = {
  "01_planned_switchover": "Planned switchover PROD → DR",
  "02_rebuild_dc1": "Rebuild DC1 as standby",
  "03_switchback": "Switchback DR → PROD",
  "04_rebuild_dc2": "Rebuild DC2 as standby",
};

const CUTOVER_ROLE_RANK = { viewer: 0, operator: 1, dba: 2, admin: 3 };

function cutoverUserRole(user) {
  var r = user && (user.role || user.preferred_role);
  return r ? String(r).toLowerCase() : "viewer";
}

function cutoverHasRole(user, required) {
  return (CUTOVER_ROLE_RANK[cutoverUserRole(user)] || 0) >= (CUTOVER_ROLE_RANK[required] || 0);
}

// The stale-LB bug class bit UK twice; flag obviously-unfinished values.
function cutoverPlaceholderIp(value) {
  var v = String(value || "").trim();
  if (!v) return true;
  return /\.0$/.test(v);
}

function cutoverDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", { hour12: false });
}

function cutoverDuration(start, end) {
  if (!start) return "—";
  var ms = (end ? new Date(end) : new Date()) - new Date(start);
  if (!(ms >= 0)) return "—";
  return fmtSec(ms / 1000);
}

function cutoverStatePill(state) {
  if (state === "succeeded") return "ok";
  if (state === "failed" || state === "rejected") return "danger";
  if (state === "pending_approval") return "warn";
  if (state === "running") return "info";
  return "muted";
}

function TierPill({ tier }) {
  var cls = tier === "armed" ? "danger" : tier === "rehearsal" ? "info" : "muted";
  return <span className={"pill " + cls}><span className="dot"/>{tier}</span>;
}

function CutoverRiskPill({ risk }) {
  var cls = risk === "High" ? "danger" : risk === "Medium" ? "warn" : "muted";
  return <span className={"pill " + cls}>{risk || "—"}</span>;
}

async function cutoverJson(path, init) {
  var response = await fetch(path, Object.assign({ cache: "no-store" }, init || {}));
  return hbzJsonResponse(response);
}

/* ── Manifest summary (the approver's step checklist) ───────────────────── */

function ManifestPhase({ phaseKey, phase }) {
  var openState = useState(false);
  var open = openState[0], setOpen = openState[1];
  var steps = phase.steps || [];
  return (
    <div style={{border:"1px solid var(--border)", borderRadius:6, marginBottom:8}}>
      <div className="flex-row" style={{padding:"8px 10px", cursor:"pointer", gap:8, flexWrap:"wrap"}}
           onClick={function() { setOpen(!open); }}>
        {open ? <Icon.ChevronDown size={13}/> : <Icon.ChevronRight size={13}/>}
        <strong className="txt-sm">{CUTOVER_PHASE_LABELS[phaseKey] || phaseKey}</strong>
        <span className="mono txt-xs muted">{phase.mode}</span>
        <div className="grow"/>
        <span className={"pill " + (phase.rc === 0 ? "ok" : "danger")}>dry-run rc {phase.rc}</span>
        <span className="pill muted">{phase.step_count != null ? phase.step_count : steps.length} steps</span>
        <span className="pill warn">{phase.state_changing || 0} state-changing</span>
        <span className="pill danger">{phase.high_risk || 0} high-risk</span>
        {phase.destructive && <span className="pill danger"><Icon.AlertTriangle size={10}/> destructive</span>}
      </div>
      {phase.manifest_error && (
        <div className="tile-error" style={{margin:"0 10px 8px"}}>
          <Icon.AlertCircle size={12}/> manifest parse error: {phase.manifest_error}
        </div>
      )}
      {open && steps.length > 0 && (
        <div style={{overflowX:"auto", borderTop:"1px solid var(--divider)"}}>
          <table className="tbl">
            <thead>
              <tr><th>#</th><th>Step</th><th>Purpose</th><th>Risk</th><th>Changes state</th><th>Required gates</th></tr>
            </thead>
            <tbody>
              {steps.map(function(s, i) {
                return (
                  <tr key={s.id || i}>
                    <td className="num">{i + 1}</td>
                    <td className="mono txt-xs">{s.id || "—"}</td>
                    <td className="txt-xs">
                      {s.purpose || "—"}
                      {s.internal_action && <div className="muted mono txt-xs">{s.internal_action}</div>}
                    </td>
                    <td><CutoverRiskPill risk={s.risk}/></td>
                    <td>{s.state_changing
                      ? <span className="pill warn"><span className="dot"/>yes</span>
                      : <span className="pill muted">no</span>}</td>
                    <td className="mono txt-xs">{(s.required_gates || []).join(", ") || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ManifestSummary({ preview }) {
  if (!preview || !preview.phases) return <EmptyState icon={Icon.FileText} title="No manifest" hint="The submit-time engine dry-run produced no manifest summary."/>;
  var keys = Object.keys(preview.phases).sort();
  return (
    <div>
      {keys.map(function(k) { return <ManifestPhase key={k} phaseKey={k} phase={preview.phases[k]}/>; })}
    </div>
  );
}

/* ── Submit modal ────────────────────────────────────────────────────────── */

function CutoverSubmitModal({ config, kinds, tiers, currentUser, onClose, onSubmitted }) {
  var kindState = useState(kinds.length ? kinds[0].kind : "cutover_switchover");
  var tierState = useState("preview");
  var reasonState = useState("");
  var advState = useState(false);
  var optState = useState({ max_lag_bytes: "", settle_timeout: "", timeout_seconds: "", allow_archive_only: false });
  var busyState = useState(false);
  var errState = useState(null);
  var resultState = useState(null);

  var kind = kindState[0], setKind = kindState[1];
  var tier = tierState[0], setTier = tierState[1];
  var reason = reasonState[0], setReason = reasonState[1];
  var adv = advState[0], setAdv = advState[1];
  var opts = optState[0], setOpts = optState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var error = errState[0], setError = errState[1];
  var result = resultState[0], setResult = resultState[1];

  var kindMeta = null;
  kinds.forEach(function(k) { if (k.kind === kind) kindMeta = k; });
  var approverOk = !kindMeta || tier === "preview" || true; // submit needs operator only; approval gate is later

  function setOpt(key, value) {
    setOpts(Object.assign({}, opts, (function() { var o = {}; o[key] = value; return o; })()));
  }

  function submit() {
    setBusy(true);
    setError(null);
    var options = {};
    if (String(opts.max_lag_bytes).trim() !== "") options.max_lag_bytes = Number(opts.max_lag_bytes);
    if (String(opts.settle_timeout).trim() !== "") options.settle_timeout = Number(opts.settle_timeout);
    if (String(opts.timeout_seconds).trim() !== "") options.timeout_seconds = Number(opts.timeout_seconds);
    if (opts.allow_archive_only) options.allow_archive_only = true;
    cutoverJson("/api/v1/cutover/" + encodeURIComponent(config.id) + "/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind: kind, tier: tier, reason: reason, options: options }),
    })
      .then(function(resp) {
        setResult(resp);
        setBusy(false);
        if (onSubmitted) onSubmitted(resp);
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setBusy(false);
      });
  }

  var reasonOk = reason.trim().length >= 8;

  return (
    <Modal onClose={onClose}>
      <div style={{padding:18, maxWidth:760, maxHeight:"80vh", overflowY:"auto"}}>
        <div className="flex-row" style={{marginBottom:12, gap:8}}>
          <Icon.GitBranch size={16}/>
          <strong>New cutover run — region {config.id}</strong>
          <div className="grow"/>
          <button className="btn sm ghost" onClick={onClose}><Icon.X size={12}/></button>
        </div>

        {!result && (
          <div>
            <div className="field">
              <label>Operation</label>
              <select value={kind} onChange={function(e) { setKind(e.target.value); }}>
                {kinds.map(function(k) {
                  return <option key={k.kind} value={k.kind}>{k.label}</option>;
                })}
              </select>
              {kindMeta && (
                <div className="flex-row txt-xs muted" style={{marginTop:4, gap:8}}>
                  <span>approver role ≥ <strong>{kindMeta.approver_role}</strong> (4-eyes)</span>
                  {kindMeta.destructive && <span className="pill danger"><Icon.AlertTriangle size={10}/> destructive (PVC delete + restore)</span>}
                </div>
              )}
            </div>

            <div className="field">
              <label>Tier</label>
              <div style={{display:"flex", flexDirection:"column", gap:6}}>
                {tiers.map(function(t) {
                  var active = tier === t.tier;
                  return (
                    <div key={t.tier}
                         onClick={function() { setTier(t.tier); }}
                         style={{
                           padding:"8px 10px", borderRadius:6, cursor:"pointer",
                           border: "1px solid " + (active ? (t.tier === "armed" ? "var(--danger)" : "var(--accent)") : "var(--border)"),
                           background: active ? (t.tier === "armed" ? "var(--danger-soft)" : "var(--accent-soft)") : "transparent",
                         }}>
                      <div className="flex-row" style={{gap:8}}>
                        <TierPill tier={t.tier}/>
                        <span className="txt-xs muted">{t.description}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="field">
              <label>Reason (audit trail, min 8 characters)</label>
              <textarea rows={2} value={reason}
                        placeholder="e.g. Q2 DR drill rehearsal per change CHG-1234"
                        onChange={function(e) { setReason(e.target.value); }}
                        style={{width:"100%", fontFamily:"inherit", fontSize:13}}/>
            </div>

            <div className="field">
              <a href="#" className="txt-xs" onClick={function(e) { e.preventDefault(); setAdv(!adv); }}>
                {adv ? "Hide" : "Show"} advanced orchestrator options
              </a>
              {adv && (
                <div className="grid-3" style={{marginTop:8}}>
                  <div className="field" style={{margin:0}}>
                    <label>max_lag_bytes</label>
                    <input type="number" value={opts.max_lag_bytes} placeholder="engine default"
                           onChange={function(e) { setOpt("max_lag_bytes", e.target.value); }}/>
                  </div>
                  <div className="field" style={{margin:0}}>
                    <label>settle_timeout (s)</label>
                    <input type="number" value={opts.settle_timeout} placeholder="engine default"
                           onChange={function(e) { setOpt("settle_timeout", e.target.value); }}/>
                  </div>
                  <div className="field" style={{margin:0}}>
                    <label>timeout_seconds</label>
                    <input type="number" value={opts.timeout_seconds} placeholder="engine default"
                           onChange={function(e) { setOpt("timeout_seconds", e.target.value); }}/>
                  </div>
                  <label className="flex-row txt-xs" style={{gap:6}}>
                    <input type="checkbox" checked={opts.allow_archive_only}
                           onChange={function(e) { setOpt("allow_archive_only", e.target.checked); }}/>
                    allow_archive_only (streaming-era runs should leave this OFF)
                  </label>
                </div>
              )}
            </div>

            {error && (
              <div className="tile-error" style={{marginBottom:10}}>
                <Icon.AlertCircle size={13}/> {error}
              </div>
            )}

            <div className="flex-row" style={{justifyContent:"flex-end", gap:8}}>
              <button className="btn sm" onClick={onClose}>Cancel</button>
              <button className={"btn sm " + (tier === "armed" ? "danger" : "primary")}
                      disabled={busy || !reasonOk}
                      onClick={submit}>
                {busy ? <Icon.Loader size={12}/> : <Icon.Play size={12}/>}
                {tier === "preview" ? " Generate manifest" : tier === "armed" ? " Submit ARMED run" : " Submit rehearsal"}
              </button>
            </div>
            {!reasonOk && reason.length > 0 && (
              <div className="muted txt-xs" style={{textAlign:"right", marginTop:4}}>reason needs ≥ 8 characters</div>
            )}
          </div>
        )}

        {result && (
          <div>
            <div className="flex-row" style={{gap:8, marginBottom:10, flexWrap:"wrap"}}>
              <span className={"pill " + cutoverStatePill(result.state)}><span className="dot"/>{result.state}</span>
              <TierPill tier={result.tier}/>
              <span className="mono txt-xs muted">job {String(result.job_id).slice(0, 8)}…</span>
            </div>
            {result.state === "pending_approval" && (
              <div className="tile-error" style={{background:"var(--warn-soft)", color:"var(--warn)", marginBottom:10}}>
                <Icon.Clock size={13}/> Parked for 4-eyes approval — a second {kindMeta ? kindMeta.approver_role : "dba"}+ user must approve before anything runs.
              </div>
            )}
            {result.state === "failed" && (
              <div className="tile-error" style={{marginBottom:10}}>
                <Icon.AlertCircle size={13}/> Submit-time engine dry-run failed (rc {result.preview && result.preview.rc}); job not queued.
              </div>
            )}
            <div className="section-h" style={{fontSize:13}}>Engine dry-run manifest</div>
            <ManifestSummary preview={result.preview}/>
            <div className="flex-row" style={{justifyContent:"flex-end", marginTop:10}}>
              <button className="btn sm primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

/* ── Region card ─────────────────────────────────────────────────────────── */

function CutoverRegionCard({ config, activeRun, readOnly, vendorOk, currentUser, onNewRun, onOpenRun }) {
  var c = config.config || {};
  var missing = config.missing_keys || [];
  var prodLbWarn = cutoverPlaceholderIp(c.prod_primary_lb);
  var drLbWarn = cutoverPlaceholderIp(c.dr_primary_lb);
  var hooks = config.hooks || {};
  var hookChips = ["freeze_hook", "unfreeze_hook", "route_hook"].map(function(k) {
    var configured = typeof hooks[k] === "boolean" ? hooks[k] : !!String(hooks[k] || "").trim();
    return { key: k, configured: configured };
  });
  var canSubmit = cutoverHasRole(currentUser, "operator") && config.enabled && !missing.length &&
                  !readOnly && vendorOk && !activeRun;

  var blockReason = !config.enabled ? "config disabled"
    : missing.length ? "missing required keys"
    : readOnly ? "console is read-only"
    : !vendorOk ? "vendored engine failed integrity check"
    : activeRun ? "a run is already active"
    : !cutoverHasRole(currentUser, "operator") ? "requires operator role"
    : null;

  return (
    <div className="card">
      <div className="hd">
        <span className="flex-row" style={{gap:6}}><Icon.Globe size={13}/> {config.id.toUpperCase()}</span>
        <span className="meta">
          {config.enabled
            ? <span className="pill ok"><span className="dot"/>enabled</span>
            : <span className="pill muted">disabled</span>}
        </span>
      </div>
      <div className="bd">
        <table className="tbl" style={{marginBottom:8}}>
          <tbody>
            <tr><td className="muted txt-xs">PROD</td>
                <td className="mono txt-xs">{c.prod_cluster || "—"} <span className="muted">· {c.prod_namespace || "—"}</span></td></tr>
            <tr><td className="muted txt-xs">DR</td>
                <td className="mono txt-xs">{c.dr_cluster || "—"} <span className="muted">· {c.dr_namespace || "—"}</span></td></tr>
            <tr><td className="muted txt-xs">PROD LB</td>
                <td className="mono txt-xs">{c.prod_primary_lb || "—"}{" "}
                  {prodLbWarn && <span className="pill warn"><Icon.AlertTriangle size={10}/> placeholder?</span>}</td></tr>
            <tr><td className="muted txt-xs">DR LB</td>
                <td className="mono txt-xs">{c.dr_primary_lb || "—"}{" "}
                  {drLbWarn && <span className="pill warn"><Icon.AlertTriangle size={10}/> placeholder?</span>}</td></tr>
            <tr><td className="muted txt-xs">Contexts</td>
                <td className="mono txt-xs" style={{wordBreak:"break-all"}}>{c.prod_context || "—"} → {c.dr_context || "—"}</td></tr>
          </tbody>
        </table>

        {(prodLbWarn || drLbWarn) && (
          <div className="tile-error" style={{background:"var(--warn-soft)", color:"var(--warn)", marginBottom:8}}>
            <Icon.AlertTriangle size={12}/> LB value looks like a placeholder — collect real values
            with prod_dr_cutover_env_collect.py before rehearsal/armed runs.
          </div>
        )}

        {missing.length > 0 && (
          <div className="tile-error" style={{marginBottom:8}}>
            <Icon.AlertCircle size={12}/> Missing required config: {missing.join(", ")}
          </div>
        )}

        <div className="flex-row" style={{gap:6, flexWrap:"wrap", marginBottom:8}}>
          {hookChips.map(function(h) {
            return (
              <span key={h.key} className={"pill " + (h.configured ? "info" : "muted")}>
                {h.configured ? <Icon.Check size={10}/> : null} {h.key}
              </span>
            );
          })}
        </div>

        {activeRun && (
          <div className="flex-row" style={{gap:8, marginBottom:8, padding:"6px 8px",
               background:"var(--accent-soft)", borderRadius:6, cursor:"pointer"}}
               onClick={function() { onOpenRun(activeRun.job_id); }}>
            <Icon.Activity size={13}/>
            <span className="txt-xs">
              Active: <strong>{activeRun.job_kind}</strong> <TierPill tier={activeRun.tier}/>{" "}
              <span className={"pill " + cutoverStatePill(activeRun.job_state)}>{activeRun.job_state}</span>
            </span>
            <div className="grow"/>
            <Icon.ChevronRight size={13}/>
          </div>
        )}

        <div className="flex-row" style={{gap:8}}>
          <button className="btn sm primary" disabled={!canSubmit} onClick={onNewRun}
                  title={blockReason || "Submit a new cutover run"}>
            <Icon.Play size={12}/> New run
          </button>
          {blockReason && <span className="muted txt-xs">{blockReason}</span>}
          <div className="grow"/>
          <span className="muted txt-xs">updated {cutoverDate(config.updated_at)}</span>
        </div>
      </div>
    </div>
  );
}

/* ── Run detail (approval + live execution view) ─────────────────────────── */

function CutoverPhaseProgress({ run }) {
  var progress = run.progress || {};
  var phases = progress.phases || {};
  var order = run.mode === "all" ? CUTOVER_PHASE_ORDER : [run.mode];
  return (
    <div>
      {order.map(function(key) {
        var p = phases[key] || {};
        var status = p.status || (progress.current_phase === key ? "running" : "pending");
        var pill = status === "completed" ? "ok" : status === "running" ? "info" : "muted";
        var icon = status === "completed" ? <Icon.Check size={12}/> :
                   status === "running" ? <Icon.Loader size={12}/> : <Icon.Clock size={12}/>;
        return (
          <div key={key} className="flex-row" style={{padding:"7px 0", borderBottom:"1px solid var(--divider)", gap:8}}>
            <span className={"pill " + pill}>{icon} {status}</span>
            <span className="txt-sm">{CUTOVER_PHASE_LABELS[key] || key}</span>
            <div className="grow"/>
            {p.last_step && <span className="mono txt-xs muted" style={{maxWidth:340, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>{p.last_step}</span>}
          </div>
        );
      })}
      {progress.settling && (
        <div className="flex-row" style={{padding:"7px 0", gap:8}}>
          <span className="pill info"><Icon.Loader size={12}/> settling</span>
          <span className="muted txt-xs">waiting for replication to settle</span>
        </div>
      )}
    </div>
  );
}

function CutoverRunDetail({ jobId, currentUser, kinds, onBack }) {
  var dataState = useState(null);
  var logsState = useState([]);
  var errState = useState(null);
  var busyState = useState(false);

  var data = dataState[0], setData = dataState[1];
  var logs = logsState[0], setLogs = logsState[1];
  var error = errState[0], setError = errState[1];
  var busy = busyState[0], setBusy = busyState[1];

  var job = (data && data.job) || {};
  var run = (data && data.run) || {};
  var stateActive = job.state === "pending" || job.state === "pending_approval" || job.state === "running";

  function load() {
    return Promise.all([
      cutoverJson("/api/v1/cutover/runs/" + jobId),
      cutoverJson("/api/v1/jobs/" + jobId + "/logs?limit=5000"),
    ])
      .then(function(out) {
        setData(out[0]);
        setLogs((out[1] && out[1].logs) || []);
        setError(null);
      })
      .catch(function(err) { setError(err.message || String(err)); });
  }

  useEffect(function() {
    var alive = true;
    var timer = null;
    function tick() {
      if (!alive) return;
      load().then(function() {
        if (alive) timer = setTimeout(tick, 3000);
      });
    }
    tick();
    return function() { alive = false; if (timer) clearTimeout(timer); };
  }, [jobId]);

  function act(label, fn) {
    setBusy(true);
    setError(null);
    fn()
      .then(function() { return load(); })
      .then(function() { setBusy(false); })
      .catch(function(err) {
        setError(label + ": " + (err.message || String(err)));
        setBusy(false);
      });
  }

  function approve() {
    act("approve", function() {
      return cutoverJson("/api/v1/jobs/" + jobId + "/approve", { method: "POST" });
    });
  }

  function reject() {
    var why = window.prompt("Rejection reason:", "");
    if (why === null) return;
    act("reject", function() {
      return cutoverJson("/api/v1/jobs/" + jobId + "/reject", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason: why || "Rejected by approver" }),
      });
    });
  }

  function cancel() {
    if (!window.confirm("Cancel this cutover run? The orchestrator is stopped with SIGINT→TERM→KILL and keeps resumable state.")) return;
    act("cancel", function() {
      if (job.state === "running") {
        return cutoverJson("/api/v1/cutover/runs/" + jobId + "/cancel", { method: "POST" });
      }
      return cutoverJson("/api/v1/jobs/" + jobId, { method: "DELETE" });
    });
  }

  var payload = job.payload || {};
  var preview = payload.preview || (job.result && job.result.preview);
  var kindMeta = null;
  (kinds || []).forEach(function(k) { if (k.kind === job.kind) kindMeta = k; });
  var approverRole = payload.required_approver_role || (kindMeta && kindMeta.approver_role) || "dba";
  var isSubmitter = currentUser && job.submitted_by_sub &&
    (currentUser.oidc_sub === job.submitted_by_sub);
  var canApprove = cutoverHasRole(currentUser, approverRole);

  if (!data) {
    return (
      <div className="page">
        <button className="btn sm" onClick={onBack}><Icon.ChevronLeft size={12}/> Back to DR &amp; Cutover</button>
        {error
          ? <div className="tile-error" style={{marginTop:10}}><Icon.AlertCircle size={13}/> {error}</div>
          : <div className="card mt-3"><div className="bd"><div className="sk sk-block" style={{height:200, borderRadius:6}}/></div></div>}
      </div>
    );
  }

  return (
    <div className="page">
      <div className="flex-row" style={{gap:8, flexWrap:"wrap", marginBottom:10}}>
        <button className="btn sm" onClick={onBack}><Icon.ChevronLeft size={12}/> Back</button>
        <strong>{kindMeta ? kindMeta.label : job.kind}</strong>
        <TierPill tier={run.tier}/>
        <span className={"pill " + cutoverStatePill(job.state)}><span className="dot"/>{job.state}</span>
        {stateActive && <span className="pill muted"><Icon.RefreshCw size={10}/> auto-refresh 3s</span>}
        <div className="grow"/>
        {job.state === "pending_approval" && (
          <span className="flex-row" style={{gap:6}}>
            <button className="btn sm primary" disabled={busy || !canApprove || isSubmitter}
                    title={isSubmitter ? "4-eyes: the submitter cannot approve" :
                           !canApprove ? "requires role ≥ " + approverRole : "Approve and dispatch"}
                    onClick={approve}>
              <Icon.CheckCircle size={12}/> Approve &amp; dispatch
            </button>
            <button className="btn sm" disabled={busy || !canApprove || isSubmitter} onClick={reject}>
              <Icon.XCircle size={12}/> Reject
            </button>
          </span>
        )}
        {(job.state === "running" || job.state === "pending" || job.state === "pending_approval") && (
          <button className="btn sm danger" disabled={busy || !cutoverHasRole(currentUser, "operator")} onClick={cancel}>
            <Icon.StopCircle size={12}/> Cancel
          </button>
        )}
      </div>

      {error && <div className="tile-error" style={{marginBottom:10}}><Icon.AlertCircle size={13}/> {error}</div>}

      {job.state === "pending_approval" && (
        <div className="tile-error" style={{background:"var(--warn-soft)", color:"var(--warn)", marginBottom:10}}>
          <Icon.Shield size={13}/> 4-eyes approval required: role ≥ <strong>{approverRole}</strong>, and the
          approver must not be the submitter. Review the manifest below before approving.
        </div>
      )}

      <div className="grid-3">
        <div className="card">
          <div className="hd">Run metadata</div>
          <div className="bd">
            <table className="tbl"><tbody>
              <tr><td className="muted txt-xs">Region</td><td className="mono txt-xs">{run.config_id || "—"}</td></tr>
              <tr><td className="muted txt-xs">Job</td><td className="mono txt-xs" style={{wordBreak:"break-all"}}>{String(job.id || jobId)}</td></tr>
              <tr><td className="muted txt-xs">Reason</td><td className="txt-xs">{job.reason || "—"}</td></tr>
              <tr><td className="muted txt-xs">Submitted</td><td className="txt-xs">{cutoverDate(job.submitted_at)} <span className="muted mono">{job.submitted_by_sub || ""}</span></td></tr>
              <tr><td className="muted txt-xs">Approved</td><td className="txt-xs">{job.approved_by_sub ? cutoverDate(job.approved_at) : "—"} <span className="muted mono">{job.approved_by_sub || ""}</span></td></tr>
              <tr><td className="muted txt-xs">Duration</td><td className="txt-xs">{cutoverDuration(run.started_at || job.submitted_at, run.finished_at || job.completed_at)}</td></tr>
              <tr><td className="muted txt-xs">Run root</td><td className="mono txt-xs" style={{wordBreak:"break-all"}}>{run.run_root || "—"}</td></tr>
            </tbody></table>
          </div>
        </div>

        <div className="card" style={{gridColumn:"span 2"}}>
          <div className="hd">Phase progress {run.mode === "all" ? <span className="meta">full 4-phase drill</span> : null}</div>
          <div className="bd">
            <CutoverPhaseProgress run={run}/>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">Engine manifest (submit-time dry-run)</div>
        <div className="bd">
          <ManifestSummary preview={preview}/>
        </div>
      </div>

      <div className="card">
        <div className="hd">Log tail <span className="meta">{logs.length} lines</span></div>
        <div className="bd">
          {logs.length
            ? (
              <div className="logbox" style={{maxHeight:420, overflowY:"auto"}}>
                {logs.map(function(l) {
                  var cls = l.stream === "stderr" ? "err" : l.stream === "event" ? "ok" : "";
                  return <div key={l.id} className={cls}>{l.line}</div>;
                })}
              </div>
            )
            : <EmptyState icon={Icon.Terminal} title="No log lines yet"/>}
        </div>
      </div>
    </div>
  );
}

/* ── Main screen ─────────────────────────────────────────────────────────── */

function CutoverScreen({ lastRefresh, currentUser }) {
  var cfgState = useState(null);
  var modesState = useState(null);
  var runsState = useState([]);
  var loadState = useState(true);
  var errState = useState(null);
  var submitState = useState(null);   // config object being submitted against
  var runViewState = useState(null);  // job_id opened in detail view

  var cfg = cfgState[0], setCfg = cfgState[1];
  var modes = modesState[0], setModes = modesState[1];
  var runs = runsState[0], setRuns = runsState[1];
  var loading = loadState[0], setLoading = loadState[1];
  var error = errState[0], setError = errState[1];
  var submitFor = submitState[0], setSubmitFor = submitState[1];
  var runView = runViewState[0], setRunView = runViewState[1];

  function reload() {
    return Promise.all([
      cutoverJson("/api/v1/cutover/config"),
      cutoverJson("/api/v1/cutover/modes"),
      cutoverJson("/api/v1/cutover/runs?limit=100"),
    ])
      .then(function(out) {
        setCfg(out[0]);
        setModes(out[1]);
        setRuns((out[2] && out[2].runs) || []);
        setError(null);
        setLoading(false);
      })
      .catch(function(err) {
        setError(err.message || String(err));
        setLoading(false);
      });
  }

  useEffect(function() {
    var alive = true;
    setLoading(true);
    reload().then(function() { if (!alive) return; });
    return function() { alive = false; };
  }, [lastRefresh]);

  var kinds = (modes && modes.kinds) || [];
  var tiers = (modes && modes.tiers) || [];
  var kindLabel = {};
  kinds.forEach(function(k) { kindLabel[k.kind] = k.label; });

  if (runView) {
    return <CutoverRunDetail jobId={runView} currentUser={currentUser} kinds={kinds}
                             onBack={function() { setRunView(null); reload(); }}/>;
  }

  if (loading && !cfg) {
    return (
      <div className="page">
        <div className="grid-3">
          {[0,1,2].map(function(i) {
            return <div key={i} className="card"><div className="bd"><div className="sk sk-block" style={{height:220, borderRadius:6}}/></div></div>;
          })}
        </div>
      </div>
    );
  }

  var configs = (cfg && cfg.configs) || [];
  var vendorOk = !!(cfg && cfg.vendor && cfg.vendor.ok);
  var readOnly = !!(cfg && cfg.read_only);
  var ocAvailable = !!(cfg && cfg.oc_available);

  var activeByConfig = {};
  var pendingApproval = [];
  var activeRuns = [];
  runs.forEach(function(r) {
    if (!r.finished_at && (r.job_state === "pending" || r.job_state === "pending_approval" || r.job_state === "running")) {
      activeByConfig[r.config_id] = r;
      if (r.job_state === "pending_approval") pendingApproval.push(r);
      if (r.job_state === "running") activeRuns.push(r);
    }
  });

  return (
    <div className="page">

      {error && (
        <div className="tile-error flex-row" style={{marginBottom:8}}>
          <Icon.AlertCircle size={14}/>
          <strong style={{marginLeft:6}}>Couldn't load data</strong>
          <span className="muted txt-xs" style={{marginLeft:8}}>{hbzErrorText(error)}</span>
        </div>
      )}

      {/* ── Engine / environment posture ── */}
      <div className="flex-row" style={{gap:8, flexWrap:"wrap", marginBottom:12}}>
        <span className={"pill " + (vendorOk ? "ok" : "danger")}>
          <Icon.Shield size={10}/> vendored engine {vendorOk ? "integrity ok" : "INTEGRITY FAILED"}
        </span>
        <span className={"pill " + (ocAvailable ? "ok" : "warn")}>
          <Icon.Terminal size={10}/> oc {ocAvailable ? "available" : "not in container (preview only)"}
        </span>
        {readOnly && <span className="pill danger"><Icon.Lock size={10}/> console read-only</span>}
        <span className="muted txt-xs mono">run root: {(cfg && cfg.run_root) || "—"}</span>
      </div>

      {!vendorOk && cfg && cfg.vendor && (
        <div className="tile-error" style={{marginBottom:10}}>
          <Icon.AlertCircle size={13}/> {cfg.vendor.error || "vendored engine checksum mismatch — cutover submissions are blocked"}
        </div>
      )}

      {/* ── Pending approvals ── */}
      {pendingApproval.length > 0 && (
        <div>
          <div className="section-h">Awaiting 4-eyes approval <span className="count">{pendingApproval.length}</span></div>
          {pendingApproval.map(function(r) {
            return (
              <div key={r.job_id} className="card" style={{marginBottom:8}}>
                <div className="bd flex-row" style={{gap:10, flexWrap:"wrap"}}>
                  <Icon.Shield size={14}/>
                  <strong className="txt-sm">{kindLabel[r.job_kind] || r.job_kind}</strong>
                  <TierPill tier={r.tier}/>
                  <span className="pill muted">{r.config_id}</span>
                  <span className="muted txt-xs">by {r.submitted_by_sub} · {cutoverDate(r.submitted_at)}</span>
                  <span className="txt-xs" style={{flexBasis:"100%"}}>{r.reason}</span>
                  <div className="grow"/>
                  <button className="btn sm primary" onClick={function() { setRunView(String(r.job_id)); }}>
                    Review manifest &amp; approve <Icon.ChevronRight size={12}/>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Active runs ── */}
      {activeRuns.length > 0 && (
        <div>
          <div className="section-h">Running <span className="count">{activeRuns.length}</span></div>
          {activeRuns.map(function(r) {
            var progress = r.progress || {};
            return (
              <div key={r.job_id} className="card" style={{marginBottom:8, cursor:"pointer"}}
                   onClick={function() { setRunView(String(r.job_id)); }}>
                <div className="bd flex-row" style={{gap:10, flexWrap:"wrap"}}>
                  <Icon.Activity size={14}/>
                  <strong className="txt-sm">{kindLabel[r.job_kind] || r.job_kind}</strong>
                  <TierPill tier={r.tier}/>
                  <span className="pill muted">{r.config_id}</span>
                  <span className="pill info"><Icon.Loader size={10}/> {progress.current_phase ? (CUTOVER_PHASE_LABELS[progress.current_phase] || progress.current_phase) : "starting"}</span>
                  {progress.settling && <span className="pill info">settling</span>}
                  <div className="grow"/>
                  <span className="muted txt-xs">{cutoverDuration(r.started_at, null)}</span>
                  <Icon.ChevronRight size={13}/>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Region configurations ── */}
      <div className="section-h">Region configurations <span className="count">{configs.length}</span></div>
      {configs.length ? (
        <div className="grid-3">
          {configs.map(function(c) {
            return <CutoverRegionCard key={c.id} config={c}
                                      activeRun={activeByConfig[c.id]}
                                      readOnly={readOnly} vendorOk={vendorOk}
                                      currentUser={currentUser}
                                      onNewRun={function() { setSubmitFor(c); }}
                                      onOpenRun={function(id) { setRunView(String(id)); }}/>;
          })}
        </div>
      ) : (
        <EmptyState icon={Icon.Globe} title="No cutover regions configured"
                    hint="Seed configs via CUTOVER_CONFIG_JSON / CUTOVER_CONFIG_PATH or PUT /api/v1/cutover/config/{id} as admin."/>
      )}

      {/* ── Run history ── */}
      <div className="section-h mt-2">Run history <span className="count">{runs.length}</span></div>
      <div className="card">
        <div style={{overflowX:"auto"}}>
          <table className="tbl">
            <thead>
              <tr>
                <th>Submitted</th><th>Operation</th><th>Region</th><th>Tier</th><th>State</th>
                <th>Submitted by</th><th>Approved by</th><th className="num">Duration</th><th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map(function(r) {
                return (
                  <tr key={r.job_id} style={{cursor:"pointer"}} onClick={function() { setRunView(String(r.job_id)); }}>
                    <td className="txt-xs">{cutoverDate(r.submitted_at)}</td>
                    <td className="txt-xs">{kindLabel[r.job_kind] || r.job_kind}</td>
                    <td className="mono txt-xs">{r.config_id}</td>
                    <td><TierPill tier={r.tier}/></td>
                    <td><span className={"pill " + cutoverStatePill(r.job_state)}><span className="dot"/>{r.job_state}</span></td>
                    <td className="mono txt-xs">{r.submitted_by_sub || "—"}</td>
                    <td className="mono txt-xs">{r.approved_by_sub || "—"}</td>
                    <td className="num txt-xs">{cutoverDuration(r.started_at || r.submitted_at, r.finished_at || r.completed_at)}</td>
                    <td><Icon.ChevronRight size={12}/></td>
                  </tr>
                );
              })}
              {!runs.length && (
                <tr><td colSpan="9" className="muted" style={{textAlign:"center", padding:20}}>No cutover runs yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {submitFor && (
        <CutoverSubmitModal config={submitFor} kinds={kinds} tiers={tiers} currentUser={currentUser}
                            onClose={function() { setSubmitFor(null); reload(); }}
                            onSubmitted={function() { reload(); }}/>
      )}
    </div>
  );
}

window.CutoverScreen = CutoverScreen;
