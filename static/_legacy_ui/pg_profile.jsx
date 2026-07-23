// Historical Performance / pg_profile. Live API only; no fallback datasets.

function pgpFetch(path, init) {
  return fetch("/api/v1/pg-profile" + path, Object.assign({ cache: "no-store" }, init || {})).then(hbzJsonResponse);
}

function pgpStatusTone(status) {
  var value = String(status || "UNAVAILABLE").toUpperCase();
  if (["LIVE", "SUCCEEDED", "REGISTERED", "VERIFIED", "READY"].indexOf(value) >= 0) return "ok";
  if (["PARTIAL", "PARTIAL_DATA", "COLD_START", "RUNNING", "PENDING", "PREVIEW"].indexOf(value) >= 0) return "warn";
  if (["FAILED", "TOO_LARGE"].indexOf(value) >= 0) return "danger";
  return "muted";
}

function PgpPill({ value }) {
  return <span className={"pill " + pgpStatusTone(value)}><span className="dot"/>{value || "UNAVAILABLE"}</span>;
}

function PerformanceHistoryScreen({ lastRefresh }) {
  var tabState = useState("overview");
  var state = useState({ loading: true, status: {}, servers: [], runs: [], reports: [], history: [], baselines: [] });
  var errorState = useState(null);
  var busyState = useState(null);
  var viewerState = useState(null);
  var formState = useState({ serverId: "", startSample: "", endSample: "", startTime: "", endTime: "",
                             reportType: "REGULAR", compareStartSample: "", compareEndSample: "" });
  var tab = tabState[0], setTab = tabState[1];
  var data = state[0], setData = state[1];
  var error = errorState[0], setError = errorState[1];
  var busy = busyState[0], setBusy = busyState[1];
  var viewer = viewerState[0], setViewer = viewerState[1];
  var reportForm = formState[0], setReportForm = formState[1];

  function load() {
    setError(null);
    Promise.all([
      pgpFetch("/status"), pgpFetch("/servers?limit=200"), pgpFetch("/runs?limit=200"),
      pgpFetch("/reports?limit=200"), pgpFetch("/query-history?limit=200"), pgpFetch("/baselines?limit=200")
    ]).then(function(rows) {
      setData({ loading: false, status: rows[0] || {}, servers: rows[1].items || [], runs: rows[2].items || [],
                reports: rows[3].items || [], history: rows[4].items || [], baselines: rows[5].items || [] });
      if (!reportForm.serverId && (rows[1].items || []).length) {
        setReportForm(function(f) { return Object.assign({}, f, { serverId: String(rows[1].items[0].id) }); });
      }
    }).catch(function(err) {
      setData(function(d) { return Object.assign({}, d, { loading: false }); });
      setError(err && (err.message || String(err)));
    });
  }

  useEffect(function() { load(); }, [lastRefresh]);

  function action(key, path, body) {
    setBusy(key); setError(null);
    return pgpFetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                           body: JSON.stringify(body || {}) })
      .then(function() { setBusy(null); load(); })
      .catch(function(err) { setBusy(null); setError(err && (err.message || String(err))); });
  }

  function generateReport() {
    var body = { pgprofile_server_id: Number(reportForm.serverId), report_type: reportForm.reportType };
    if (reportForm.startSample && reportForm.endSample) {
      body.start_sample_id = Number(reportForm.startSample); body.end_sample_id = Number(reportForm.endSample);
    } else {
      body.period_start = new Date(reportForm.startTime).toISOString(); body.period_end = new Date(reportForm.endTime).toISOString();
    }
    if (reportForm.reportType === "DIFF") {
      body.compare_start_sample_id = Number(reportForm.compareStartSample);
      body.compare_end_sample_id = Number(reportForm.compareEndSample);
    }
    action("report", "/reports", body);
  }

  var status = data.status || {};
  var lastSuccess = data.runs.filter(function(r) { return r.status === "SUCCEEDED"; })[0];
  var tabs = [["overview", "Overview"], ["servers", "Servers"], ["samples", "Samples"],
              ["reports", "Reports"], ["queries", "Query History"], ["ml", "ML / Baselines"]];
  var hist = data.history.slice().reverse();
  var historyOption = {
    tooltip: { trigger: "axis" }, grid: { top: 20, right: 20, bottom: 42, left: 58 },
    xAxis: { type: "category", data: hist.map(function(r) { return r.period_end ? new Date(r.period_end).toLocaleString() : "—"; }), axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", name: "mean ms", axisLabel: { fontSize: 10 } },
    series: [{ name: "Mean execution", type: "line", smooth: true, symbol: "circle",
               data: hist.map(function(r) { return Number((r.features || {}).mean_execution_ms || 0); }) }]
  };
  var callsLatencyOption = {
    tooltip: { trigger: "axis" }, legend: { data: ["Calls", "Mean ms"] }, grid: { top: 34, right: 58, bottom: 42, left: 58 },
    xAxis: { type: "category", data: historyOption.xAxis.data, axisLabel: { fontSize: 10 } },
    yAxis: [{ type: "value", name: "calls" }, { type: "value", name: "ms" }],
    series: [
      { name: "Calls", type: "bar", data: hist.map(function(r) { return Number((r.features || {}).calls || 0); }) },
      { name: "Mean ms", type: "line", yAxisIndex: 1, data: hist.map(function(r) { return Number((r.features || {}).mean_execution_ms || 0); }) }
    ]
  };
  var ioOption = {
    tooltip: { trigger: "axis" }, legend: { data: ["Buffer reads", "Temp blocks"] }, grid: { top: 34, right: 20, bottom: 42, left: 62 },
    xAxis: { type: "category", data: historyOption.xAxis.data, axisLabel: { fontSize: 10 } }, yAxis: { type: "value", name: "blocks" },
    series: [
      { name: "Buffer reads", type: "line", areaStyle: {}, data: hist.map(function(r) { return Number((r.features || {}).shared_blocks_read || 0); }) },
      { name: "Temp blocks", type: "line", data: hist.map(function(r) { var f = r.features || {}; return Number(f.temp_blocks_read || 0) + Number(f.temp_blocks_written || 0); }) }
    ]
  };
  var anomalyOption = {
    tooltip: { trigger: "axis" }, legend: { data: ["Contribution %", "Anomaly score"] }, grid: { top: 34, right: 58, bottom: 42, left: 58 },
    xAxis: { type: "category", data: historyOption.xAxis.data, axisLabel: { fontSize: 10 } },
    yAxis: [{ type: "value", name: "%" }, { type: "value", name: "score", min: 0, max: 1 }],
    series: [
      { name: "Contribution %", type: "bar", data: hist.map(function(r) { return Number((r.features || {}).workload_contribution_pct || 0); }) },
      { name: "Anomaly score", type: "line", yAxisIndex: 1, data: hist.map(function(r) { return r.anomaly_score == null ? null : Number(r.anomaly_score); }) }
    ]
  };

  return <div className="page">
    <div className="toolbar" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
      <div className="seg">{tabs.map(function(t) {
        return <button key={t[0]} className={"btn sm " + (tab === t[0] ? "primary" : "")}
                       onClick={function() { setTab(t[0]); }}>{t[1]}</button>;
      })}</div>
      <div className="grow" style={{ flex: 1 }}/>
      <PgpPill value={status.status || "UNAVAILABLE"}/>
      <SourceBadge source="central pg_profile repository"/>
    </div>

    {error && <div className="card" style={{ borderColor: "var(--danger)", marginBottom: 12 }}><div className="bd txt-xs">{error}</div></div>}
    {data.loading && <div className="card"><div className="bd muted">Loading historical performance metadata…</div></div>}

    {!data.loading && tab === "overview" && <React.Fragment>
      <div className="section-h">Historical performance repository</div>
      <div className="grid-4">
        <Stat label="Extension" value={status.version || "Unavailable"} sub={status.schema || "profile"}/>
        <Stat label="Registered servers" value={status.registered_servers || 0} sub="authorized environments"/>
        <Stat label="Failed sample runs" value={status.failed_sample_runs || 0} sub="requires collector review"/>
        <Stat label="Last successful sample" value={lastSuccess && lastSuccess.sample_id ? ("#" + lastSuccess.sample_id) : "—"}
              sub={lastSuccess && lastSuccess.sample_time ? new Date(lastSuccess.sample_time).toLocaleString() : "no history"}/>
        <Stat label="Repository size" value={fmtBytes(status.repository_size_bytes || 0)} sub="extension-managed objects"/>
      </div>
      <div className="grid-3">
        <div className="card"><div className="hd">Collection health</div><div className="bd">
          <PgpPill value={status.available ? "LIVE" : "UNAVAILABLE"}/>
          <div className="muted txt-xs mt-3">{status.reason || "Extension and required function signatures verified."}</div>
        </div></div>
        <div className="card"><div className="hd">Feature extraction</div><div className="bd">
          <PgpPill value={data.history.length ? "HISTORICAL" : "COLD_START"}/>
          <div className="muted txt-xs mt-3">{data.history.length} query interval records available.</div>
        </div></div>
        <div className="card"><div className="hd">Retention</div><div className="bd">
          <PgpPill value={status.enabled ? "READY" : "UNAVAILABLE"}/>
          <div className="muted txt-xs mt-3">{((status.configured || {}).retention_days || 90)} days · linked incident evidence protected.</div>
        </div></div>
      </div>
    </React.Fragment>}

    {!data.loading && tab === "servers" && <div className="card">
      <div className="hd">Registered pg_profile servers <span className="meta">operator-managed read-write service endpoints</span></div>
      <div style={{ overflowX: "auto" }}><table className="tbl"><thead><tr>
        <th>Server</th><th>Region / DC</th><th>Environment</th><th>Namespace / cluster</th><th>Database</th>
        <th>SSL</th><th>Status</th><th>Last sample</th><th>Actions</th>
      </tr></thead><tbody>{data.servers.map(function(s) { return <tr key={s.id}>
        <td className="mono txt-xs">{s.server_name}</td><td>{s.region || "—"} / {s.dc || "—"}</td>
        <td><PgpPill value={String(s.environment || "UNAVAILABLE").toUpperCase()}/></td>
        <td><div className="mono txt-xs">{s.namespace}</div><div className="muted txt-xs">{s.cluster_name}</div></td>
        <td>{s.database_name}</td><td>{s.sslmode}</td><td><PgpPill value={s.registration_status}/></td>
        <td>{s.last_sample_at ? new Date(s.last_sample_at).toLocaleString() : "—"}</td>
        <td style={{ whiteSpace: "nowrap" }}>
          <button className="btn ghost sm" disabled={!!busy} onClick={function() { action("verify-" + s.id, "/servers/" + s.id + "/verify"); }}>Verify</button>
          <button className="btn ghost sm" disabled={!!busy} onClick={function() { action("sample-" + s.id, "/servers/" + s.id + "/sample", { trigger_type: "MANUAL" }); }}>Take Sample</button>
        </td>
      </tr>; })}
      {!data.servers.length && <tr><td colSpan="9"><EmptyState title="No pg_profile servers registered" hint="Registration is DBA-controlled and uses secret references only."/></td></tr>}
      </tbody></table></div>
    </div>}

    {!data.loading && tab === "samples" && <div className="card">
      <div className="hd">Sample collection runs <span className="meta">scheduled, manual and incident-triggered</span></div>
      <div style={{ overflowX: "auto" }}><table className="tbl"><thead><tr><th>Run</th><th>Server</th><th>Sample</th><th>Time</th><th>Duration</th><th>Trigger</th><th>Incident</th><th>Status</th></tr></thead>
      <tbody>{data.runs.map(function(r) { return <tr key={r.id}><td>#{r.id}</td><td>#{r.pgprofile_server_id}</td>
        <td>{r.sample_id ? ("#" + r.sample_id) : "—"}</td><td>{r.sample_time ? new Date(r.sample_time).toLocaleString() : "—"}</td>
        <td>{r.duration_ms == null ? "—" : fmtMs(r.duration_ms)}</td><td>{r.trigger_type}</td>
        <td>{r.incident_id ? ("#" + r.incident_id) : "—"}</td><td><PgpPill value={r.status}/></td></tr>; })}
      {!data.runs.length && <tr><td colSpan="8"><EmptyState title="No historical samples" hint="The subsystem is disabled or collection has not started."/></td></tr>}
      </tbody></table></div>
    </div>}

    {!data.loading && tab === "reports" && <React.Fragment>
      <div className="card"><div className="hd">Generate report <span className="meta">bounded sample range · sanitized storage</span></div>
        <div className="bd" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div className="field" style={{ margin: 0 }}><label>Server</label><select value={reportForm.serverId} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { serverId: e.target.value })); }}>
            <option value="">Select</option>{data.servers.map(function(s) { return <option key={s.id} value={s.id}>{s.server_name}</option>; })}</select></div>
          <div className="field" style={{ margin: 0 }}><label>Start sample</label><input type="number" min="1" value={reportForm.startSample} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { startSample: e.target.value })); }}/></div>
          <div className="field" style={{ margin: 0 }}><label>End sample</label><input type="number" min="1" value={reportForm.endSample} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { endSample: e.target.value })); }}/></div>
          <div className="field" style={{ margin: 0 }}><label>Or start time</label><input type="datetime-local" value={reportForm.startTime} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { startTime: e.target.value })); }}/></div>
          <div className="field" style={{ margin: 0 }}><label>End time</label><input type="datetime-local" value={reportForm.endTime} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { endTime: e.target.value })); }}/></div>
          <div className="field" style={{ margin: 0 }}><label>Type</label><select value={reportForm.reportType} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { reportType: e.target.value })); }}><option>REGULAR</option><option>DIFF</option></select></div>
          {reportForm.reportType === "DIFF" && <React.Fragment>
            <div className="field" style={{ margin: 0 }}><label>Compare start sample</label><input type="number" min="1" value={reportForm.compareStartSample} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { compareStartSample: e.target.value })); }}/></div>
            <div className="field" style={{ margin: 0 }}><label>Compare end sample</label><input type="number" min="1" value={reportForm.compareEndSample} onChange={function(e) { setReportForm(Object.assign({}, reportForm, { compareEndSample: e.target.value })); }}/></div>
          </React.Fragment>}
          <button className="btn sm primary" disabled={!!busy || !reportForm.serverId || !((reportForm.startSample && reportForm.endSample) || (reportForm.startTime && reportForm.endTime)) || (reportForm.reportType === "DIFF" && !(reportForm.compareStartSample && reportForm.compareEndSample))} onClick={generateReport}>Generate</button>
        </div>
      </div>
      <div className="card"><div className="hd">Reports</div><div style={{ overflowX: "auto" }}><table className="tbl"><thead><tr><th>ID</th><th>Server</th><th>Samples</th><th>Period</th><th>Type</th><th>Status</th><th>Size</th><th>Incident</th><th></th></tr></thead>
        <tbody>{data.reports.map(function(r) { return <tr key={r.id}><td>#{r.id}</td><td>#{r.pgprofile_server_id}</td><td>#{r.start_sample_id} → #{r.end_sample_id}</td>
          <td>{r.period_start ? new Date(r.period_start).toLocaleString() : "—"}<br/>{r.period_end ? new Date(r.period_end).toLocaleString() : "—"}</td>
          <td>{r.report_type}</td><td><PgpPill value={r.generation_status}/></td><td>{fmtBytes(r.stored_size_bytes || 0)}</td><td>{r.incident_id ? ("#" + r.incident_id) : "—"}</td>
          <td><button className="btn ghost sm" disabled={!r.content_available} onClick={function() { setViewer(r.id); }}>View</button></td></tr>; })}
        {!data.reports.length && <tr><td colSpan="9"><EmptyState title="No reports generated" hint="Choose a server and two bounding samples."/></td></tr>}
        </tbody></table></div></div>
      {viewer && <div className="card"><div className="hd">Sanitized report #{viewer}<button className="btn ghost sm" style={{ float: "right" }} onClick={function() { setViewer(null); }}>Close</button></div>
        <div className="bd"><iframe title={"pg_profile report " + viewer} sandbox="" referrerPolicy="no-referrer"
          src={"/api/v1/pg-profile/reports/" + viewer + "/content"} style={{ width: "100%", height: "70vh", border: "1px solid var(--border)", borderRadius: 8 }}/></div></div>}
    </React.Fragment>}

    {!data.loading && tab === "queries" && <React.Fragment>
      {!hist.length && <div className="card"><EmptyState title="Query history cold start" hint="No structured pg_profile query intervals are available yet."/></div>}
      {!!hist.length && <div className="grid-2">
        <div className="card"><div className="hd">Execution-time history</div><div className="bd"><EChart height={250} option={historyOption}/></div></div>
        <div className="card"><div className="hd">Calls versus latency</div><div className="bd"><EChart height={250} option={callsLatencyOption}/></div></div>
        <div className="card"><div className="hd">Buffer and temporary I/O</div><div className="bd"><EChart height={250} option={ioOption}/></div></div>
        <div className="card"><div className="hd">Workload contribution and anomaly</div><div className="bd"><EChart height={250} option={anomalyOption}/></div></div>
      </div>}
      <div className="card"><div className="hd">Query history</div><div style={{ overflowX: "auto" }}><table className="tbl"><thead><tr><th>Fingerprint</th><th>Database</th><th>Period mean</th><th>Baseline median / p95</th><th>Change</th><th>Calls</th><th>Contribution</th><th>Buffer reads</th><th>Temp I/O</th><th>Status</th></tr></thead>
        <tbody>{data.history.map(function(r) { var f = r.features || {}; return <tr key={r.id}><td className="mono txt-xs">{r.query_fingerprint ? r.query_fingerprint.slice(0, 14) : (r.query_id || "—")}</td>
          <td>{r.database_name}</td><td>{Number(f.mean_execution_ms || 0).toFixed(2)} ms</td>
          <td>{r.baseline_median_ms == null ? "—" : Number(r.baseline_median_ms).toFixed(2)} / {r.baseline_p95_ms == null ? "—" : Number(r.baseline_p95_ms).toFixed(2)} ms</td>
          <td>{r.percentage_change == null ? "—" : Number(r.percentage_change).toFixed(1) + "%"}</td><td>{fmtInt(f.calls || 0)}</td>
          <td>{Number(f.workload_contribution_pct || 0).toFixed(1)}%</td><td>{fmtInt(f.shared_blocks_read || 0)}</td>
          <td>{fmtBytes(8192 * (Number(f.temp_blocks_read || 0) + Number(f.temp_blocks_written || 0)))}</td><td><PgpPill value={r.history_status}/></td></tr>; })}
        {!data.history.length && <tr><td colSpan="10"><EmptyState title="No query features" hint="Missing pg_profile data is reported as partial, not healthy."/></td></tr>}
        </tbody></table></div></div>
    </React.Fragment>}

    {!data.loading && tab === "ml" && <div className="card"><div className="hd">Robust query baselines <span className="meta">median / MAD · same weekday and hour</span></div>
      <div style={{ overflowX: "auto" }}><table className="tbl"><thead><tr><th>Fingerprint</th><th>Database</th><th>Window</th><th>Samples</th><th>Median</th><th>MAD</th><th>p95</th><th>Model</th><th>Validation</th><th>Status</th></tr></thead>
      <tbody>{data.baselines.map(function(b) { return <tr key={b.id}><td className="mono txt-xs">{b.query_fingerprint ? b.query_fingerprint.slice(0, 14) : b.query_id}</td><td>{b.database_name}</td>
        <td>{b.weekday == null ? "all" : ("weekday " + b.weekday)} · {b.hour == null ? "all hours" : (b.hour + ":00")}</td><td>{b.sample_count}</td>
        <td>{b.median_execution_ms == null ? "—" : Number(b.median_execution_ms).toFixed(2) + " ms"}</td><td>{b.mad_execution_ms == null ? "—" : Number(b.mad_execution_ms).toFixed(2)}</td>
        <td>{b.p95_execution_ms == null ? "—" : Number(b.p95_execution_ms).toFixed(2) + " ms"}</td><td>{b.model_version}</td><td>{b.feedback_state || "NEEDS_MORE_EVIDENCE"}</td><td><PgpPill value={b.history_status}/></td></tr>; })}
      {!data.baselines.length && <tr><td colSpan="10"><EmptyState title="Baseline cold start" hint="Minimum reviewed historical intervals have not been collected."/></td></tr>}
      </tbody></table></div></div>}
  </div>;
}

window.PerformanceHistoryScreen = PerformanceHistoryScreen;
