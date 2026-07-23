// OpenShift Overview module backed by Grafana extractor metadata.

function ocpText(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback || "-";
  return String(value);
}

function ocpShort(value, limit) {
  var text = ocpText(value, "");
  var max = limit || 120;
  return text.length > max ? text.slice(0, max) + "..." : text;
}

function ocpDashboardTitle(row) {
  return row.title || row.name || row.dashboard_title || row.uid || "Dashboard";
}

function ocpPanelTitle(row) {
  return row.panel_title || row.title || row.panel_id || "Panel";
}

function ocpQueryText(row) {
  var target = row.target || {};
  return row.query || row.expr || row.rawQuery || target.expr || target.query || target.rawQuery || "";
}

function ocpPostJson(path, payload) {
  return fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload || {})
  }).then(hbzJsonResponse);
}

function OpenShiftOverviewScreen({ lastRefresh }) {
  var dataState = React.useState(null);
  var loadingState = React.useState(true);
  var errorState = React.useState(null);
  var frameState = React.useState(true);
  var ragState = React.useState(null);
  var ragBusyState = React.useState(null);
  var ragErrorState = React.useState(null);
  var askState = React.useState("");
  var answerState = React.useState(null);
  var data = dataState[0], setData = dataState[1];
  var loading = loadingState[0], setLoading = loadingState[1];
  var error = errorState[0], setError = errorState[1];
  var showFrame = frameState[0], setShowFrame = frameState[1];
  var rag = ragState[0], setRag = ragState[1];
  var ragBusy = ragBusyState[0], setRagBusy = ragBusyState[1];
  var ragError = ragErrorState[0], setRagError = ragErrorState[1];
  var askText = askState[0], setAskText = askState[1];
  var answer = answerState[0], setAnswer = answerState[1];

  React.useEffect(function() {
    var alive = true;
    setLoading(true);
    setError(null);
    Promise.all([
      fetch("/api/v1/openshift/grafana/overview?log_limit=8", { cache: "no-store" }).then(hbzJsonResponse),
      fetch("/api/v1/openshift/rag/status", { cache: "no-store" }).then(hbzJsonResponse).catch(function() { return null; })
    ]).then(function(out) {
        if (!alive) return;
        setData(out[0]);
        setRag(out[1]);
        setLoading(false);
      })
      .catch(function(err) {
        if (!alive) return;
        setError(err.message || String(err));
        setLoading(false);
      });
    return function() { alive = false; };
  }, [lastRefresh]);

  function refreshRagStatus() {
    return fetch("/api/v1/openshift/rag/status", { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(payload) { setRag(payload); return payload; });
  }

  function ingestRag() {
    setRagBusy("ingest");
    setRagError(null);
    ocpPostJson("/api/v1/openshift/rag/ingest", {
      limit: 5000,
      backfill_embeddings: true,
      persist_ml_snapshot: true
    }).then(function(payload) {
      setAnswer({ answer: "Ingest complete: " + (payload.ingested || 0) + " new RAG rows, " + (payload.skipped_existing || 0) + " existing rows skipped.", model: "ingest", documents: [] });
      return refreshRagStatus();
    }).catch(function(err) {
      setRagError(err.message || String(err));
    }).then(function() {
      setRagBusy(null);
    });
  }

  function trainMl() {
    setRagBusy("train");
    setRagError(null);
    ocpPostJson("/api/v1/openshift/ml/train", { force: true })
      .then(function(payload) {
        setAnswer({ answer: "ML train status: " + (payload.status || "unknown") + " / rows=" + (payload.rows || 0), model: "ml-train", documents: [] });
        return refreshRagStatus();
      }).catch(function(err) {
        setRagError(err.message || String(err));
      }).then(function() {
        setRagBusy(null);
      });
  }

  function askRag() {
    var q = askText.trim();
    if (!q) return;
    setRagBusy("ask");
    setRagError(null);
    setAnswer(null);
    ocpPostJson("/api/v1/openshift/rag/ask", { question: q, limit: 8 })
      .then(function(payload) {
        setAnswer(payload);
      }).catch(function(err) {
        setRagError(err.message || String(err));
      }).then(function() {
        setRagBusy(null);
      });
  }

  var cfg = (data && data.config) || {};
  var summary = (data && data.summary) || {};
  var dashboards = (data && data.dashboards) || [];
  var datasources = (data && data.datasources) || [];
  var panels = (data && data.panels) || [];
  var queries = (data && data.queries) || [];
  var logs = (data && data.logs) || { sample: [], count: 0 };
  var provider = (rag && rag.provider) || {};
  var dashboardUrl = cfg.dashboard_url || "https://grafana-route-grafana.apps.ocp-dr.habibbank.local/d/ocpdr-overview/ocp-dr-1-cluster-overview?from=now-6h&to=now&refresh=30s";
  var logSample = logs.sample || [];
  var datasourceRows = datasources.map(function(ds) {
    return {
      label: ds.name || ds.uid || "datasource",
      value: ds.type || "-",
      sub: ds.uid || ds.access || "",
      tone: ds.type === "loki" ? "warn" : ds.type === "prometheus" ? "ok" : "info"
    };
  });
  var queryLanguageRows = {};
  queries.forEach(function(row) {
    var lang = row.query_language || row.datasource_type || "query";
    queryLanguageRows[lang] = (queryLanguageRows[lang] || 0) + 1;
  });
  var queryRows = Object.keys(queryLanguageRows).map(function(key) {
    return { label: key, value: queryLanguageRows[key], sub: "panel queries", tone: key === "loki" ? "warn" : "ok" };
  });

  return (
    <div className="page">
      <Phase1Toolbar loading={loading} error={error} source={data && data.source}>
        <button className="btn sm primary" onClick={function() { window.open(dashboardUrl, "_blank", "noopener,noreferrer"); }}>
          <Icon.ExternalLink size={12}/> Open Grafana
        </button>
        <button className="btn sm" onClick={function() { setShowFrame(!showFrame); }}>
          <Icon.Eye size={12}/> {showFrame ? "Hide Frame" : "Show Frame"}
        </button>
        <span className={"pill " + (data && data.available ? "ok" : "warn")}>
          <span className="dot"/>{data && data.available ? "extract available" : "extract not mounted"}
        </span>
        {data && data.extract && data.extract.name && <span className="pill muted">{data.extract.name}</span>}
      </Phase1Toolbar>

      <div className="grid-4">
        <Stat label="Dashboards" value={summary.dashboard_count || dashboards.length || 1} sub={cfg.folder_uid || "Grafana folder"}/>
        <Stat label="Panels" value={summary.panel_count || panels.length || 0} sub="extracted inventory"/>
        <Stat label="Queries" value={summary.query_count || queries.length || 0} sub="PromQL / LogQL"/>
        <Stat label="Loki ML rows" value={summary.loki_log_rows || logs.count || 0} sub="ml/loki_logs_for_ml.jsonl"/>
      </div>

      <div className="card">
        <div className="hd">
          <Icon.Bot size={16}/> OpenShift AI / ML
          <span className="meta">{provider.provider || "disabled"} · {rag && rag.semantic_enabled ? "semantic RAG" : "keyword RAG"}</span>
        </div>
        <div className="bd">
          {ragError && <div className="tile-error" style={{marginBottom: 10}}><Icon.AlertCircle size={13}/> {ragError}</div>}
          <div className="grid-4">
            <Stat label="RAG docs" value={rag && rag.kb_openshift_docs || 0} sub="OpenShift Loki"/>
            <Stat label="KB total" value={rag && rag.kb_total_docs || 0} sub="PostgreSQL"/>
            <Stat label="Rows on disk" value={rag && rag.loki_rows_on_disk || 0} sub={rag && rag.extract_name || "extract"}/>
            <Stat label="LLM" value={provider.configured ? "on" : "off"} sub={provider.model || "fallback"}/>
          </div>
          <div className="flex-row" style={{gap: 8, flexWrap: "wrap", marginTop: 10}}>
            <button className="btn sm primary" disabled={!!ragBusy} onClick={ingestRag}>
              {ragBusy === "ingest" ? <Icon.Loader size={12}/> : <Icon.Database size={12}/>} Ingest RAG + ML
            </button>
            <button className="btn sm" disabled={!!ragBusy} onClick={trainMl}>
              {ragBusy === "train" ? <Icon.Loader size={12}/> : <Icon.Activity size={12}/>} Train ML
            </button>
            <input
              className="input mono"
              style={{minWidth: 320, flex: "1 1 360px"}}
              value={askText}
              onChange={function(e) { setAskText(e.target.value); }}
              onKeyDown={function(e) { if (e.key === "Enter") askRag(); }}
              placeholder="Ask OpenShift logs or Grafana evidence"
            />
            <button className="btn sm" disabled={!!ragBusy || !askText.trim()} onClick={askRag}>
              {ragBusy === "ask" ? <Icon.Loader size={12}/> : <Icon.Send size={12}/>} Ask
            </button>
          </div>
          {answer && (
            <div className="logbox" style={{marginTop: 10, whiteSpace: "pre-wrap"}}>
              <div className="muted txt-xs">model: {answer.model || "-"}</div>
              <div>{answer.answer || answer.error || "-"}</div>
              {answer.documents && answer.documents.length > 0 && (
                <div style={{marginTop: 8}}>
                  <div className="muted txt-xs">evidence: {answer.documents.length} RAG rows</div>
                  {answer.documents.slice(0, 4).map(function(doc, idx) {
                    return <div key={idx} className="mono txt-xs">- {ocpShort(doc.title || doc.runbook_id, 160)}</div>;
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="hd">
          <Icon.Cloud size={16}/> OpenShift Cluster Overview
          <span className="meta">{cfg.dashboard_uid || "ocpdr-overview"} · {cfg.refresh || "30s"}</span>
        </div>
        <div className="bd">
          {showFrame ? (
            <iframe
              title="OpenShift Grafana cluster overview"
              src={dashboardUrl}
              style={{width: "100%", height: 620, border: "1px solid var(--border)", borderRadius: 6, background: "var(--panel)"}}
            />
          ) : (
            <div className="empty-state">
              <Icon.LayoutDashboard size={28}/>
              <div className="empty-title">Grafana frame hidden</div>
              <div className="muted mono txt-xs">{dashboardUrl}</div>
            </div>
          )}
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Datasource Map <span className="meta">{datasources.length} rows</span></div>
          <div className="bd">
            <BarList title="Types" rows={datasourceRows} emptyText="No datasource inventory mounted"/>
          </div>
        </div>
        <div className="card">
          <div className="hd">Query Inventory <span className="meta">{queries.length} sampled</span></div>
          <div className="bd">
            <BarList title="Languages" rows={queryRows} emptyText="No panel query inventory mounted"/>
          </div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="hd">Dashboards <span className="meta">{dashboards.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Dashboard</th><th>UID</th><th>Source</th></tr></thead>
              <tbody>
                {dashboards.slice(0, 12).map(function(row, idx) {
                  return (
                    <tr key={(row.uid || row.dashboard_uid || idx)}>
                      <td>{ocpDashboardTitle(row)}</td>
                      <td className="mono txt-xs">{row.uid || row.dashboard_uid || "-"}</td>
                      <td>{row.folder_title || row.folder_uid || row.url || "-"}</td>
                    </tr>
                  );
                })}
                {!loading && dashboards.length === 0 && (
                  <tr><td colSpan="3" className="muted" style={{textAlign: "center", padding: 24}}>No dashboard inventory mounted.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="hd">Panel Queries <span className="meta">{queries.length} rows</span></div>
          <div style={{overflowX: "auto"}}>
            <table className="tbl">
              <thead><tr><th>Panel</th><th>Datasource</th><th>Query</th></tr></thead>
              <tbody>
                {queries.slice(0, 12).map(function(row, idx) {
                  return (
                    <tr key={(row.dashboard_uid || "q") + "-" + (row.panel_id || idx) + "-" + idx}>
                      <td>{ocpPanelTitle(row)}</td>
                      <td className="mono txt-xs">{row.datasource_uid || row.datasource_name || "-"}</td>
                      <td className="mono txt-xs">{ocpShort(ocpQueryText(row), 130) || "-"}</td>
                    </tr>
                  );
                })}
                {!loading && queries.length === 0 && (
                  <tr><td colSpan="3" className="muted" style={{textAlign: "center", padding: 24}}>No query inventory mounted.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="hd">Loki Infrastructure Sample <span className="meta">{logs.count || 0} ML rows</span></div>
        <div style={{overflowX: "auto"}}>
          <table className="tbl">
            <thead><tr><th>Time</th><th>Namespace</th><th>Pod / node</th><th>Message</th></tr></thead>
            <tbody>
              {logSample.map(function(row, idx) {
                return (
                  <tr key={(row.timestamp_utc || "log") + idx}>
                    <td className="mono txt-xs">{row.timestamp_utc || "-"}</td>
                    <td>{row.namespace || "-"}</td>
                    <td className="mono txt-xs">{row.pod || row.node || "-"}</td>
                    <td className="mono txt-xs">{ocpShort(row.line, 220)}</td>
                  </tr>
                );
              })}
              {!loading && logSample.length === 0 && (
                <tr><td colSpan="4" className="muted" style={{textAlign: "center", padding: 24}}>No Loki ML log sample mounted.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
