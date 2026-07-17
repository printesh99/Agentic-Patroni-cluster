// AI Assistant screen — configured provider + deterministic live-data tools.
// POST /api/v1/assistant/ask  |  GET /api/v1/assistant/status
// SSE streaming via fetch + ReadableStream (EventSource doesn't support POST).
// Phase 6: intent classification + evidence-based answers + metadata badges.

var TOOL_LABELS = {
  get_patroni_state:          "Patroni State",
  get_replication_topology:   "Replication Topology",
  get_backup_status:          "Backup Status",
  get_recent_jobs_and_alerts: "Jobs & Alerts",
  get_server_parameters:      "Server Parameters",
  get_object_metrics:         "Object Metrics",
  search_logs:                "Log Search",
  query_prometheus:           "Prometheus",
  get_grafana_alerts:         "Grafana Alerts",
  search_log_history:         "Log History (RAG)",
  // Phase 6 evidence sources
  patroni_api:                "Patroni API",
  log_history_rag:            "Log History (RAG)",
  pgbackrest_api:             "pgBackRest API",
  pgbouncer_api:              "PgBouncer API",
  loki:                       "Loki Logs",
  prometheus:                 "Prometheus",
  postgresql:                 "PostgreSQL",
  openshift_events:           "OpenShift Events",
};

// Intent → human-readable label
var INTENT_LABELS = {
  patroni_status:        "Patroni Status",
  failover_rca:          "Failover RCA",
  switchover_readiness:  "Switchover Readiness",
  replication_lag:       "Replication Lag",
  backup_health:         "Backup Health",
  pgbackrest_failure:    "pgBackRest Failure",
  pgbouncer_connections: "PgBouncer Connections",
  postgresql_errors:     "PostgreSQL Errors",
  audit_activity:        "Audit Activity",
  openshift_pod_issue:   "OpenShift Pod Issue",
  configuration_question:"Configuration",
  general_dba_question:  "General DBA",
};

// Example prompts — each has an implicit intent (shown as comment)
var EXAMPLE_PROMPTS = [
  "Who is the current Patroni leader and are all replicas in sync?",           // patroni_status
  "When was the last successful full backup and is the WAL archive healthy?",  // backup_health
  "Are there any replication lag alerts right now?",                           // replication_lag
  "What are the current shared_buffers and work_mem settings?",                // configuration_question
  "Show me connection count trends over the last 24 hours.",                   // general_dba_question
  "Is the cluster ready for a planned switchover?",                            // switchover_readiness
  "Search logs for recovery conflict errors in the last hour.",                // postgresql_errors
  "Show CPU and replication lag metrics for the last 6 hours.",                // replication_lag
  "What Grafana alerts are currently firing?",                                 // general_dba_question
  "Why did the Patroni failover happen?",                                      // failover_rca
];

var RCA_STEP_LABELS = {
  fan_out:              "Dispatching sub-agents",
  logs_agent:           "Logs agent complete",
  metrics_agent:        "Metrics agent complete",
  cluster_state_agent:  "Cluster state agent complete",
  synthesize:           "Synthesizing RCA",
  gate_check_skipped:   "Gate check: non-production",
  gate_check_passed:    "Gate check: no dangerous actions",
  gate_check_approval:  "Gate check: DBA approval requested",
  answer_finalized:     "RCA complete",
};

var CAPABILITIES = [
  { name: "Patroni & HA",    desc: "Leader, replicas, DCS state, timeline, sync status" },
  { name: "Replication",     desc: "WAL lag, sync states, slots, data-centre placement" },
  { name: "Backups",         desc: "pgBackRest schedule, repo health, WAL archive status" },
  { name: "Jobs & Alerts",   desc: "Console job history, active replication or collector alerts" },
  { name: "Configuration",   desc: "PostgreSQL server parameters, pending-restart flags" },
  { name: "Object Metrics",  desc: "Connections, storage, live/dead tuples, index sizes" },
  { name: "Runbook RCA",     desc: "Failure pattern detection with step-by-step runbook guidance" },
  { name: "Log Intelligence",desc: "Loki LogQL search across PostgreSQL, Patroni, pgBackRest logs" },
  { name: "Observability",   desc: "Prometheus metrics + Grafana alert correlation" },
  { name: "Evidence-Based",  desc: "Deterministic evidence gathering with an attributed provider call when needed" },
  { name: "Multi-Agent RCA", desc: "Phase 7: parallel sub-agents (logs, metrics, cluster-state) → correlated RCA" },
];

function renderAnswer(content) {
  return content.split("\n").map(function(line, i) {
    if (line.startsWith("PROPOSED_ACTION:")) {
      return (
        <div key={i} className="ai-proposal">
          <span className="ai-proposal-label">Proposed Action</span>
          <span className="ai-proposal-text">{line.slice("PROPOSED_ACTION:".length).trim()}</span>
        </div>
      );
    }
    if (line === "") return <br key={i}/>;
    return <p key={i} className="ai-line">{line}</p>;
  });
}

function assistantResponseError(resp, text, body) {
  var ctype = (resp.headers.get("content-type") || "unknown").split(";")[0];
  var detail = body && (body.detail || body.error || body.message);
  if (detail) return String(detail);
  var preview = String(text || "").replace(/\s+/g, " ").trim().slice(0, 160);
  if (preview.charAt(0) === "<") {
    return "Assistant endpoint returned HTML instead of JSON (HTTP " + resp.status + ", " + ctype + "). This usually means the OpenShift route/proxy returned an app shell, auth page, or timeout page.";
  }
  return "Assistant request failed (HTTP " + resp.status + ", " + ctype + ")" + (preview ? ": " + preview : "");
}

function readAssistantJson(resp) {
  return resp.text().then(function(text) {
    var body = null;
    var ctype = (resp.headers.get("content-type") || "").toLowerCase();
    var looksJson = /^\s*[\[{]/.test(text || "");
    if (text && (ctype.indexOf("json") !== -1 || looksJson)) {
      try {
        body = JSON.parse(text);
      } catch (ex) {
        throw new Error("Assistant endpoint returned invalid JSON (HTTP " + resp.status + "): " + ex.message);
      }
    }
    if (!resp.ok) {
      throw new Error(assistantResponseError(resp, text, body));
    }
    if (body === null) {
      throw new Error(assistantResponseError(resp, text, body));
    }
    return body;
  });
}

function ToolBadges({ tools, dim }) {
  if (!tools || tools.length === 0) return null;
  return (
    <div className={"ai-tools" + (dim ? " dim" : "")}>
      <span className="ai-tools-label">{dim ? "Calling: " : "Used: "}</span>
      {tools.map(function(t, i) {
        return (
          <span key={i} className="pill ai-tool-pill">
            {TOOL_LABELS[t] || t}
          </span>
        );
      })}
    </div>
  );
}

// Phase 6: evidence metadata row shown below each assistant answer
var RISK_LABELS = {
  safe_read_only: "Safe (Read-Only)",
  dba_approval:   "DBA Approval Required",
  dangerous:      "Dangerous — Not Allowed",
};

var RISK_CLASSES = {
  safe_read_only: "ai-rec-safe",
  dba_approval:   "ai-rec-approval",
  dangerous:      "ai-rec-dangerous",
};

function RecommendationBadges({ recommendations }) {
  if (!recommendations || recommendations.length === 0) return null;
  return (
    <div className="ai-recommendations">
      <span className="ai-rec-label">Recommendations:</span>
      {recommendations.map(function(rec, i) {
        return (
          <div key={i} className={"ai-rec-item " + (RISK_CLASSES[rec.risk_level] || "ai-rec-approval")}>
            <span className="ai-rec-risk">{RISK_LABELS[rec.risk_level] || rec.risk_level}</span>
            <span className="ai-rec-text">{rec.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function responseAttribution(msg) {
  var model = String((msg && msg.model) || "");
  var provider = String((msg && msg.provider) || "");
  var mode = String((msg && msg.responseMode) || "");
  var fallback = !!(msg && msg.fallbackUsed);
  if (fallback || mode === "heuristic_fallback" || model === "heuristic") {
    return {
      label: "Heuristic fallback",
      detail: (msg && msg.fallbackReasonCode) || "PROVIDER_ERROR",
      className: "ai-ev-conf-low",
    };
  }
  if (model.indexOf("azure_openai:") === 0 || provider === "azure_openai") {
    return {
      label: "Azure " + (model.split(":")[1] || model || "OpenAI"),
      detail: "LLM-generated",
      className: "ai-ev-conf-high",
    };
  }
  if (model.indexOf("live-data") === 0 || mode === "deterministic") {
    return {
      label: "Live evidence",
      detail: model || "read-only tools",
      className: "ai-ev-readonly",
    };
  }
  if (model) {
    return { label: model, detail: mode || "attributed response", className: "" };
  }
  return { label: "Unattributed response", detail: "metadata unavailable", className: "ai-ev-conf-low" };
}

function ResponseAttribution({ msg }) {
  var attr = responseAttribution(msg);
  return (
    <React.Fragment>
      <span className={"ai-ev-badge " + attr.className}>{attr.label}</span>
      <span className="ai-ev-badge dim">{attr.detail}</span>
      {msg && msg.providerLatencyMs !== null && msg.providerLatencyMs !== undefined && (
        <span className="ai-ev-badge dim">{msg.providerLatencyMs} ms</span>
      )}
      {msg && msg.providerHttpStatus !== null && msg.providerHttpStatus !== undefined && (
        <span className="ai-ev-badge dim">HTTP {msg.providerHttpStatus}</span>
      )}
      {msg && msg.providerRequestId && (
        <span className="ai-ev-badge dim">request {msg.providerRequestId}</span>
      )}
    </React.Fragment>
  );
}

function EvidenceMeta({ msg }) {
  if (!msg) return null;
  var showIntent = !!msg.intent && msg.intent !== "general_dba_question";
  var confClass = msg.confidence ? "ai-ev-conf-" + msg.confidence.toLowerCase() : "";
  return (
    <div>
      <div className="ai-evidence-meta">
        <ResponseAttribution msg={msg}/>
        <span className="ai-ev-badge ai-ev-readonly">Read-only</span>
        {showIntent && (
          <span className="ai-ev-badge ai-ev-intent">
            {INTENT_LABELS[msg.intent] || msg.intent}
          </span>
        )}
        {msg.confidence && (
          <span className={"ai-ev-badge " + confClass}>
            {msg.confidence} confidence
          </span>
        )}
        {msg.evidenceCount > 0 && (
          <span className="ai-ev-badge dim">{msg.evidenceCount} evidence items</span>
        )}
        {msg.sessionId && (
          <span className="ai-ev-session">#{msg.sessionId.slice(0, 8)}</span>
        )}
      </div>
      {msg.missingEvidence && msg.missingEvidence.length > 0 && (
        <div className="ai-missing-ev">
          <span className="ai-missing-label">Unavailable sources:</span>
          {msg.missingEvidence.map(function(s, i) {
            return <span key={i} className="pill dim sm">{s}</span>;
          })}
        </div>
      )}
      {msg.phase === "phase7_langgraph" && msg.rcaSteps && msg.rcaSteps.length > 0 && (
        <div className="ai-rca-steps">
          <span className="ai-rca-label">RCA Pipeline:</span>
          {msg.rcaSteps.map(function(step, i) {
            return (
              <span key={i} className="pill sm ai-rca-step-pill">
                {RCA_STEP_LABELS[step] || step}
              </span>
            );
          })}
        </div>
      )}
      {msg.dbaApprovalRequired && (
        <div className="ai-dba-approval-banner">
          DBA approval required for PROD/DR recommendations — review before executing.
        </div>
      )}
      <RecommendationBadges recommendations={msg.recommendations}/>
    </div>
  );
}

function AssistantNotConfigured({ status }) {
  return (
    <div className="ai-not-configured">
      <div className="ai-nc-icon">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
      </div>
      <h2 className="ai-nc-title">AI Assistant Not Configured</h2>
      <p className="ai-nc-body">
        Configure an approved AI provider through Secret-backed deployment settings. Deterministic read-only tools remain available without a model.
      </p>
      <div className="card ai-nc-steps">
        <div className="hd"><h3>Phase 0 Setup Steps</h3></div>
        <div className="bd">
          <ol className="ai-setup-list">
            <li>Select the approved provider and model for this environment.</li>
            <li>Reference credentials from an authorized Secret; never enter them in the UI.</li>
            <li>Validate provider status and a fixed-prompt canary before rollout.</li>
            <li>Deploy through the reviewed change workflow with automatic rollback.</li>
          </ol>
          {status && (
            <p className="ai-nc-footer">
              Endpoint: <code>/api/v1/assistant/ask</code> &nbsp;·&nbsp; Model: <code>{status.model || "not configured"}</code>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function AssistantScreen({ cluster, lastRefresh, currentUser }) {
  const [messages,  setMessages]  = useState([]);
  const [input,     setInput]     = useState("");
  const [busy,      setBusy]      = useState(false);
  const [status,    setStatus]    = useState(null);
  const [streaming, setStreaming] = useState("");
  const [liveTools, setLiveTools] = useState([]);

  // Phase 6 live state
  const [liveIntent,    setLiveIntent]    = useState(null);
  const [liveEvCount,   setLiveEvCount]   = useState(0);
  const [liveSources,   setLiveSources]   = useState([]);
  const [liveConfidence,setLiveConfidence]= useState(null);

  // Phase 7 live state
  const [livePhase,     setLivePhase]     = useState(null);
  const [liveRcaSteps,  setLiveRcaSteps]  = useState([]);

  const bottomRef = useRef(null);
  const inputRef  = useRef(null);

  // Single place to clear all the in-flight "live" state, so the streaming and
  // non-streaming (JSON) paths — plus clear/error — all reset identically.
  function resetLiveState() {
    setStreaming("");
    setLiveTools([]);
    setLiveIntent(null);
    setLiveEvCount(0);
    setLiveSources([]);
    setLiveConfidence(null);
    setLivePhase(null);
    setLiveRcaSteps([]);
  }

  // Append a finished assistant message, then clear live state + unbusy. Used by
  // both the SSE "done" branch and the plain-JSON response path.
  function appendAssistantMessage(fields) {
    setMessages(function(prev) {
      return prev.concat(Object.assign({ role: "assistant" }, fields));
    });
    resetLiveState();
    setBusy(false);
  }

  useEffect(function() {
    fetch("/api/v1/assistant/status", { cache: "no-store" })
      .then(readAssistantJson)
      .then(setStatus)
      .catch(function() { setStatus({ enabled: false }); });
  }, []);

  useEffect(function() {
    if (bottomRef.current) bottomRef.current.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming, busy]);

  function sendQuestion(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    setMessages(function(prev) { return prev.concat({ role: "user", content: q }); });
    setInput("");
    setBusy(true);
    resetLiveState();

    var clusterId  = cluster && cluster.id ? cluster.id : "uat";
    var accText    = "";
    var accTools   = [];
    // Phase 6 accumulators
    var accIntent        = null;
    var accEvidenceCount = 0;
    var accSources       = [];
    var accConfidence    = null;
    var accSessionId     = null;
    var accMissingEvidence = [];
    var accRecommendations = [];
    // Phase 7 accumulators
    var accPhase         = null;
    var accRcaSteps      = [];
    var accSubAgents     = null;
    var accDbaApproval   = false;
    var accModel         = null;
    var accProvider      = null;
    var accProviderAttempted = false;
    var accResponseMode  = null;
    var accFallbackUsed  = false;
    var accFallbackReason= null;
    var accProviderHttpStatus = null;
    var accProviderLatency = null;
    var accProviderRequestId = null;

    fetch("/api/v1/assistant/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cluster_id: clusterId, question: q, stream: true, time_range: "1h" }),
    }).then(function(resp) {
      // The backend may answer either with an SSE token stream
      // (text/event-stream) or a single plain-JSON object. Only stream when the
      // server explicitly says so; otherwise read one JSON answer.
      var ctype = (resp.headers.get("content-type") || "").toLowerCase();
      var isStream = resp.ok && ctype.indexOf("text/event-stream") !== -1 &&
                     resp.body && typeof resp.body.getReader === "function";

      if (!isStream) {
        return readAssistantJson(resp).then(function(body) {
          body = body || {};
          appendAssistantMessage({
            content:         body.answer || body.content || body.text || "(no response)",
            tools:           body.tools || [],
            intent:          body.intent || null,
            confidence:      body.confidence || null,
            evidenceCount:   body.evidence_count || 0,
            sourcesChecked:  body.sources_checked || body.sources || [],
            sessionId:       body.session_id || null,
            missingEvidence: body.missing_evidence || [],
            recommendations: body.recommendations || [],
            phase:           body.phase || null,
            rcaSteps:        body.steps || [],
            subAgents:       body.sub_agents || null,
            dbaApprovalRequired: !!body.dba_approval_required,
            model:           body.model || null,
            provider:        body.provider || null,
            providerAttempted: !!body.provider_attempted,
            responseMode:    body.response_mode || null,
            fallbackUsed:    !!body.fallback_used,
            fallbackReasonCode: body.fallback_reason_code || null,
            providerHttpStatus: body.provider_http_status,
            providerLatencyMs: body.provider_latency_ms,
            providerRequestId: body.provider_request_id || null,
          });
        });
      }

      var reader  = resp.body.getReader();
      var decoder = new TextDecoder();

      function pump() {
        return reader.read().then(function(result) {
          if (result.done) {
            var finalText    = accText;
            var finalTools   = accTools.slice();
            var finalIntent  = accIntent;
            var finalConf    = accConfidence;
            var finalEvCount = accEvidenceCount;
            var finalSources = accSources.slice();
            var finalSession = accSessionId;
            var finalMissing = accMissingEvidence.slice();
            var finalRecs    = accRecommendations.slice();
            var finalPhase   = accPhase;
            var finalSteps   = accRcaSteps.slice();
            var finalSubAg   = accSubAgents;
            var finalDba     = accDbaApproval;
            var finalModel   = accModel;
            var finalProvider= accProvider;
            var finalProviderAttempted = accProviderAttempted;
            var finalMode    = accResponseMode;
            var finalFallback= accFallbackUsed;
            var finalFallbackReason = accFallbackReason;
            var finalProviderHttpStatus = accProviderHttpStatus;
            var finalProviderLatency = accProviderLatency;
            var finalProviderRequestId = accProviderRequestId;
            appendAssistantMessage({
              content:         finalText || "(no response)",
              tools:           finalTools,
              intent:          finalIntent,
              confidence:      finalConf,
              evidenceCount:   finalEvCount,
              sourcesChecked:  finalSources,
              sessionId:       finalSession,
              missingEvidence: finalMissing,
              recommendations: finalRecs,
              phase:           finalPhase,
              rcaSteps:        finalSteps,
              subAgents:       finalSubAg,
              dbaApprovalRequired: finalDba,
              model:           finalModel,
              provider:        finalProvider,
              providerAttempted: finalProviderAttempted,
              responseMode:    finalMode,
              fallbackUsed:    finalFallback,
              fallbackReasonCode: finalFallbackReason,
              providerHttpStatus: finalProviderHttpStatus,
              providerLatencyMs: finalProviderLatency,
              providerRequestId: finalProviderRequestId,
            });
            return;
          }
          var chunk = decoder.decode(result.value, { stream: true });
          chunk.split("\n").forEach(function(line) {
            if (!line.startsWith("data: ")) return;
            var payload = line.slice(6).trim();
            if (!payload) return;
            try {
              var evt = JSON.parse(payload);
              if (evt.type === "token") {
                accText += evt.text;
                setStreaming(accText);
              } else if (evt.type === "tool_call") {
                accTools = accTools.concat(evt.tool);
                setLiveTools(accTools.slice());
              } else if (evt.type === "intent") {
                // Phase 6: intent classified
                accIntent = evt.intent;
                setLiveIntent(evt.intent);
              } else if (evt.type === "evidence_ready") {
                // Phase 6: evidence pack built
                accEvidenceCount = evt.count || 0;
                accSources       = evt.sources || [];
                setLiveEvCount(accEvidenceCount);
                setLiveSources(accSources);
              } else if (evt.type === "confidence") {
                // Phase 6: confidence level
                accConfidence = evt.level;
                setLiveConfidence(evt.level);
              } else if (evt.type === "phase") {
                // Phase 7: graph phase indicator
                accPhase = evt.phase;
                setLivePhase(evt.phase);
              } else if (evt.type === "rca_step") {
                // Phase 7: sub-agent step completion
                accRcaSteps = accRcaSteps.concat(evt.step);
                setLiveRcaSteps(accRcaSteps.slice());
              } else if (evt.type === "done") {
                // Capture any extra fields from done event
                if (evt.session_id)        accSessionId       = evt.session_id;
                if (evt.intent)            accIntent          = accIntent || evt.intent;
                if (evt.confidence)        accConfidence      = accConfidence || evt.confidence;
                if (evt.evidence_count)    accEvidenceCount   = evt.evidence_count;
                if (evt.missing_evidence)  accMissingEvidence = evt.missing_evidence;
                if (evt.recommendations)   accRecommendations = evt.recommendations;
                if (evt.tools && accTools.length === 0) accTools = evt.tools || [];
                // Phase 7 fields
                if (evt.phase)             accPhase           = evt.phase;
                if (evt.sub_agents)        accSubAgents       = evt.sub_agents;
                if (evt.steps)             accRcaSteps        = evt.steps;
                if (evt.dba_approval_required) accDbaApproval = true;
                if (evt.model)             accModel           = evt.model;
                if (evt.provider)          accProvider        = evt.provider;
                if (evt.provider_attempted) accProviderAttempted = true;
                if (evt.response_mode)     accResponseMode    = evt.response_mode;
                if (evt.fallback_used)     accFallbackUsed    = true;
                if (evt.fallback_reason_code) accFallbackReason = evt.fallback_reason_code;
                if (evt.provider_http_status !== undefined) accProviderHttpStatus = evt.provider_http_status;
                if (evt.provider_latency_ms !== undefined) accProviderLatency = evt.provider_latency_ms;
                if (evt.provider_request_id) accProviderRequestId = evt.provider_request_id;
              } else if (evt.type === "error") {
                throw new Error(evt.detail || "AI error");
              }
            } catch (ex) {
              if (ex.message && ex.message.indexOf("JSON") === -1) {
                accText += "\n\nError: " + ex.message;
                setStreaming(accText);
              }
            }
          });
          return pump();
        });
      }
      return pump();
    }).catch(function(err) {
      setMessages(function(prev) {
        return prev.concat({ role: "assistant", content: "Error: " + (err.message || "unknown"), error: true });
      });
      resetLiveState();
      setBusy(false);
    });
  }

  function handleSubmit(e) {
    if (e) e.preventDefault();
    sendQuestion(input);
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuestion(input); }
  }

  function handleClear() {
    if (busy) return;
    setMessages([]);
    resetLiveState();
  }

  if (status === null) {
    return <div className="ai-loading"><span className="ai-spinner-lg"/></div>;
  }

  if (!status.enabled) {
    return <AssistantNotConfigured status={status}/>;
  }

  var isProd  = cluster && (cluster.id === "prod" || cluster.id === "dr");
  var phases  = status && status.phases ? status.phases : {};
  var isEmpty = messages.length === 0 && !busy;

  // Live thinking indicator: show intent and evidence status while busy
  var thinkingLabel = "Thinking…";
  if (livePhase === "phase7_langgraph") {
    var lastStep = liveRcaSteps.length > 0 ? liveRcaSteps[liveRcaSteps.length - 1] : "fan_out";
    thinkingLabel = "Multi-Agent RCA: " + (RCA_STEP_LABELS[lastStep] || lastStep);
    if (liveEvCount > 0) {
      thinkingLabel += " · " + liveEvCount + " evidence items";
    }
  } else if (liveIntent && liveIntent !== "general_dba_question") {
    thinkingLabel = "Intent: " + (INTENT_LABELS[liveIntent] || liveIntent);
    if (liveEvCount > 0) {
      thinkingLabel += " · " + liveEvCount + " evidence items gathered";
    }
  }

  return (
    <div className="ai-screen">

      {isProd && (
        <div className="ai-prod-banner">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          PRODUCTION / DR CLUSTER — All proposed actions require dual DBA approval before execution.
        </div>
      )}
      <div className="ai-chat-pane">
        {isEmpty && (
          <div className="ai-welcome">
            <div className="ai-welcome-icon">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </div>
            <h2 className="ai-welcome-title">AI DBA Assistant</h2>
            <p className="ai-welcome-sub">Ask about Patroni, replication, backups, or configuration. The assistant calls live read-only tools to answer based on real cluster state.</p>
            <div className="ai-examples-grid">
              {EXAMPLE_PROMPTS.map(function(p, i) {
                return (
                  <button key={i} className="ai-example-btn" onClick={function() { sendQuestion(p); }}>
                    {p}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {!isEmpty && (
          <div className="ai-messages">
            {messages.map(function(msg, i) {
              return (
                <div key={i} className={"ai-msg ai-msg-" + msg.role + (msg.error ? " ai-msg-error" : "")}>
                  <div className="ai-msg-avatar">
                    {msg.role === "user" ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                    )}
                  </div>
                  <div className="ai-msg-body">
                    <span className="ai-msg-role">{msg.role === "user" ? (currentUser && currentUser.name ? currentUser.name : "You") : "AI Assistant"}</span>
                    <div className="ai-msg-content">
                      {msg.role === "assistant" ? renderAnswer(msg.content) : <p className="ai-line">{msg.content}</p>}
                    </div>
                    {msg.role === "assistant" && <ToolBadges tools={msg.tools}/>}
                    {msg.role === "assistant" && <EvidenceMeta msg={msg}/>}
                  </div>
                </div>
              );
            })}

            {busy && (streaming || liveTools.length > 0) && (
              <div className="ai-msg ai-msg-assistant">
                <div className="ai-msg-avatar">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                </div>
                <div className="ai-msg-body">
                  <span className="ai-msg-role">AI Assistant</span>
                  <div className="ai-msg-content">
                    {streaming ? renderAnswer(streaming) : null}
                    {liveTools.length > 0 && <ToolBadges tools={liveTools} dim={true}/>}
                    {liveSources.length > 0 && <ToolBadges tools={liveSources} dim={true}/>}
                  </div>
                </div>
              </div>
            )}

            {busy && !streaming && liveTools.length === 0 && (
              <div className="ai-msg ai-msg-assistant ai-msg-thinking">
                <div className="ai-msg-avatar">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                </div>
                <div className="ai-msg-body">
                  <span className="ai-msg-role">AI Assistant</span>
                  <div className="ai-thinking-dots"><span/><span/><span/></div>
                  {liveIntent && (
                    <div className="ai-live-status">{thinkingLabel}</div>
                  )}
                </div>
              </div>
            )}

            <div ref={bottomRef}/>
          </div>
        )}

        <form className="ai-input-form" onSubmit={handleSubmit}>
          <textarea
            ref={inputRef}
            className="ai-input"
            value={input}
            onChange={function(e) { setInput(e.target.value); }}
            onKeyDown={handleKeyDown}
            placeholder={"Ask about cluster " + (cluster && cluster.id ? cluster.id : "uat") + " — Patroni, replication, backups, config… (Enter to send)"}
            rows={3}
            disabled={busy}
          />
          <div className="ai-input-actions">
            <button className="btn sm ghost" type="button" onClick={handleClear} disabled={busy || isEmpty} title="Clear conversation">
              Clear
            </button>
            <button className="btn sm primary" type="submit" disabled={busy || !input.trim()}>
              {busy ? "…" : "Ask"}
            </button>
          </div>
        </form>

        <div className="ai-footer-note">
          Read-only &middot; Answers cite live tool data &middot; Proposed actions require operator approval &middot; Every query is audit-logged
        </div>
      </div>

      <div className="ai-sidebar">
        <div className="card ai-cap-card">
          <div className="hd"><h3>Capabilities</h3></div>
          <div className="bd ai-cap-list">
            {CAPABILITIES.map(function(cap, i) {
              return (
                <div key={i} className="ai-cap-item">
                  <strong>{cap.name}</strong>
                  <span>{cap.desc}</span>
                </div>
              );
            })}
          </div>
        </div>

        {!isEmpty && (
          <div className="card ai-ex-card">
            <div className="hd"><h3>Try Asking</h3></div>
            <div className="bd ai-ex-list">
              {EXAMPLE_PROMPTS.slice(0, 4).map(function(p, i) {
                return (
                  <button key={i} className="ai-ex-btn" onClick={function() { sendQuestion(p); }} disabled={busy}>
                    {p}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="card ai-status-card">
          <div className="hd"><h3>Session Info</h3></div>
          <div className="bd">
            <div className="ai-status-row"><span>Model</span><code>{status.model}</code></div>
            <div className="ai-status-row"><span>Backend</span><code>{status.backend || "not configured"}</code></div>
            <div className="ai-status-row"><span>Cluster</span><code>{cluster && cluster.id ? cluster.id : "uat"}</code></div>
            <div className="ai-status-row"><span>Max iterations</span><code>{status.max_tool_iterations}</code></div>
            {phases.phase4_loki     && <div className="ai-status-row"><span>Loki logs</span><code className="green">enabled</code></div>}
            {phases.phase5_prometheus && <div className="ai-status-row"><span>Prometheus</span><code className="green">enabled</code></div>}
            {phases.phase5_grafana  && <div className="ai-status-row"><span>Grafana</span><code className="green">enabled</code></div>}
            {phases.phase6_evidence && <div className="ai-status-row"><span>Evidence</span><code className="green">enabled</code></div>}
            {phases.phase7_langgraph && <div className="ai-status-row"><span>Multi-Agent RCA</span><code className="green">enabled</code></div>}
            {liveIntent && (
              <div className="ai-status-row">
                <span>Intent</span>
                <code>{INTENT_LABELS[liveIntent] || liveIntent}</code>
              </div>
            )}
          </div>
        </div>
      </div>

    </div>
  );
}
