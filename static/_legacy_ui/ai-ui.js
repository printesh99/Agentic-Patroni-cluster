/* ai-ui.js — Autonomous DBA console overlay + assistant enhancement. */
(function () {
  "use strict";

  var React = window.React;
  var ReactDOM = window.ReactDOM;
  if (!React || !ReactDOM) return;

  var h = React.createElement;
  var useEffect = React.useEffect;
  var useMemo = React.useMemo;
  var useState = React.useState;

  function api(path, opts) {
    return fetch(path, Object.assign({ headers: { "content-type": "application/json" } }, opts || {}))
      .then(function (r) {
        return r.text().then(function (text) {
          var body = text ? JSON.parse(text) : null;
          if (!r.ok) throw new Error((body && (body.error || body.detail)) || r.statusText);
          return body;
        });
      });
  }

  function asArray(value, keys) {
    if (Array.isArray(value)) return value;
    for (var i = 0; i < keys.length; i += 1) {
      if (value && Array.isArray(value[keys[i]])) return value[keys[i]];
    }
    return [];
  }

  function severityRank(value) {
    return { emergency: 5, critical: 4, warning: 3, anomaly: 2, info: 1, normal: 0 }[String(value || "normal").toLowerCase()] || 0;
  }

  function severityClass(value) {
    var s = String(value || "normal").toLowerCase();
    if (s === "emergency" || s === "critical") return "ai-sev ai-sev-critical";
    if (s === "warning" || s === "anomaly") return "ai-sev ai-sev-warning";
    return "ai-sev";
  }

  function shortTime(value) {
    if (!value) return "n/a";
    try { return new Date(value).toLocaleString(); } catch (_e) { return String(value); }
  }

  function num(value, digits) {
    if (value == null || value === "" || isNaN(Number(value))) return null;
    var n = Number(value);
    return digits != null ? n.toFixed(digits) : String(n);
  }

  function firstIncident(incidents) {
    return incidents.slice().sort(function (a, b) {
      return severityRank(b.severity) - severityRank(a.severity) || (b.id || 0) - (a.id || 0);
    })[0] || null;
  }

  // --- field accessors resilient to backend shape -------------------------
  function incidentRisk(item) {
    var ev = item.evidence || {};
    var risk = ev.risk || {};
    if (risk.risk_score != null) return risk.risk_score;
    if (ev.risk_score != null) return ev.risk_score;
    if (item.risk_score != null) return item.risk_score;
    return null;
  }

  function incidentRunbook(item) {
    var rag = item.rag_context || {};
    if (rag.recommended_runbook_id) return rag.recommended_runbook_id;
    if (rag.runbook_id) return rag.runbook_id;
    var rules = item.rule_findings || [];
    for (var i = 0; i < rules.length; i += 1) {
      if (rules[i] && rules[i].recommended_runbook_id) return rules[i].recommended_runbook_id;
    }
    var ev = item.evidence || {};
    return ev.runbook_id || null;
  }

  function MetricCell(props) {
    return h("div", { className: "ai-metric" + (props.tone ? " ai-metric-" + props.tone : "") },
      h("span", null, props.label),
      h("strong", null, props.value == null ? "n/a" : props.value)
    );
  }

  function IncidentRow(props) {
    var item = props.item;
    var active = props.active;
    return h("button", { className: "ai-row" + (active ? " ai-row-active" : ""), onClick: function () { props.onSelect(item); } },
      h("span", { className: severityClass(item.severity) }, item.severity || "normal"),
      h("span", { className: "ai-row-main" }, item.title || ("Incident " + item.id)),
      h("span", { className: "ai-muted" }, item.status || "open")
    );
  }

  function RulesTable(props) {
    var rules = props.rules || [];
    if (!rules.length) return h("p", { className: "ai-muted" }, "No rule findings.");
    return h("div", { className: "ai-table" }, rules.map(function (r, idx) {
      return h("div", { className: "ai-table-row ai-rules-row", key: r.rule_id || idx },
        h("span", { className: severityClass(r.severity) }, r.severity || "info"),
        h("span", { title: r.message }, r.message || r.rule_id || "rule"),
        h("span", { className: "ai-mono" }, r.metric || "—"),
        h("span", { className: "ai-mono" }, r.value == null ? "—" : ("=" + r.value + (r.threshold != null ? " / " + r.threshold : ""))),
        h("span", { className: "ai-muted", title: r.recommended_runbook_id }, r.recommended_runbook_id || "—")
      );
    }));
  }

  function MlSummary(props) {
    var ml = props.ml || {};
    if (!ml || ml.available === false) return h("p", { className: "ai-muted" }, "No ML score attached.");
    var feats = ml.top_features || [];
    return h("div", { className: "ai-grid-two" },
      h(MetricCell, { label: "Anomaly", value: ml.is_anomaly ? "yes" : "no" }),
      h(MetricCell, { label: "Score", value: num(ml.anomaly_score, 3) || "n/a" }),
      h(MetricCell, { label: "Severity", value: ml.severity || "info" }),
      h(MetricCell, { label: "Top features", value: feats.length ? feats.join(", ") : "n/a" })
    );
  }

  function ForecastTable(props) {
    var rows = props.forecasts || [];
    if (!rows.length) return h("p", { className: "ai-muted" }, "No forecasts.");
    return h("div", { className: "ai-table" },
      h("div", { className: "ai-table-row ai-fc-row ai-table-head" },
        h("span", null, "Metric"), h("span", null, "Current"), h("span", null, "Growth/h"),
        h("span", null, "Severity"), h("span", null, "Warn ETA"), h("span", null, "Crit ETA")
      ),
      rows.map(function (f, idx) {
        return h("div", { className: "ai-table-row ai-fc-row", key: f.id || idx },
          h("span", { className: "ai-mono", title: f.metric_name }, f.metric_name || "—"),
          h("span", { className: "ai-mono" }, num(f.current_value, 1) || "—"),
          h("span", { className: "ai-mono" }, num(f.growth_per_hour, 2) || "—"),
          h("span", { className: severityClass(f.severity) }, f.severity || "normal"),
          h("span", { className: "ai-muted" }, f.predicted_warning_time ? shortTime(f.predicted_warning_time) : "—"),
          h("span", { className: "ai-muted" }, f.predicted_critical_time ? shortTime(f.predicted_critical_time) : "—")
        );
      })
    );
  }

  function Timeline(props) {
    var items = props.items || [];
    if (!items.length) return null;
    var ordered = items.slice(-8).reverse();
    return h("div", null,
      h("div", { className: "ai-section-title" }, "Timeline"),
      h("ol", { className: "ai-timeline" }, ordered.map(function (t, idx) {
        return h("li", { key: idx },
          h("span", { className: "ai-tl-time" }, shortTime(t.ts)),
          h("span", { className: severityClass(t.severity) }, t.severity || "info"),
          h("span", { className: "ai-tl-risk" }, t.risk_score != null ? ("risk " + t.risk_score) : ""),
          (t.reasons && t.reasons.length) ? h("span", { className: "ai-tl-reason", title: t.reasons.join("; ") }, t.reasons[0]) : null
        );
      }))
    );
  }

  function IncidentDetail(props) {
    var item = props.item;
    var explain = props.explain;
    if (!item) return h("div", { className: "ai-empty" }, "No AI incidents are open.");
    var ev = item.evidence || {};
    var rag = item.rag_context || {};
    var runbook = incidentRunbook(item) || "n/a";
    var risk = incidentRisk(item);
    var confidence = item.confidence != null ? Math.round(Number(item.confidence) * 100) + "%" : "n/a";
    return h("div", { className: "ai-detail" },
      h("div", { className: "ai-detail-head" },
        h("div", null,
          h("div", { className: severityClass(item.severity) }, item.severity || "normal"),
          h("h3", null, item.title || ("Incident " + item.id)),
          h("p", null, item.ai_summary || "RCA has not been generated yet.")
        ),
        h("button", { className: "ai-btn", disabled: props.explaining, onClick: function () { explain(item.id); } }, props.explaining ? "Explaining…" : "Explain")
      ),
      h("div", { className: "ai-grid-two" },
        h(MetricCell, { label: "Risk score", value: risk == null ? "n/a" : risk, tone: severityRank(item.severity) >= 4 ? "crit" : null }),
        h(MetricCell, { label: "Type", value: item.incident_type || "n/a" }),
        h(MetricCell, { label: "Runbook", value: runbook }),
        h(MetricCell, { label: "Confidence", value: confidence })
      ),
      h("div", { className: "ai-section-title" }, "Recommended action"),
      h("p", { className: "ai-copy" }, item.recommended_action || "Review evidence and run the linked runbook."),
      (rag && (rag.snippets || rag.context || rag.matches)) ? h("div", null,
        h("div", { className: "ai-section-title" }, "Knowledge base (RAG)"),
        h("pre", { className: "ai-pre ai-pre-soft" }, JSON.stringify(rag.snippets || rag.context || rag.matches, null, 2))
      ) : null,
      h("div", { className: "ai-section-title" }, "Rule findings"),
      h(RulesTable, { rules: item.rule_findings }),
      h("div", { className: "ai-section-title" }, "ML score"),
      h(MlSummary, { ml: item.ml_findings }),
      h("div", { className: "ai-section-title" }, "Forecasts"),
      h(ForecastTable, { forecasts: item.forecast_findings }),
      (ev.log_findings && ev.log_findings.length) ? h("div", null,
        h("div", { className: "ai-section-title" }, "Log findings"),
        h("div", { className: "ai-table" }, ev.log_findings.slice(0, 8).map(function (l, idx) {
          return h("div", { className: "ai-table-row ai-log-row", key: l.signature_id || idx },
            h("span", { className: severityClass(l.severity) }, l.severity || "info"),
            h("span", { className: "ai-mono" }, l.category || "—"),
            h("span", { title: l.title }, l.title || "—"),
            h("span", { className: "ai-muted" }, l.count != null ? (l.count + "×") : "")
          );
        }))
      ) : null,
      h(Timeline, { items: ev.timeline })
    );
  }

  function MlModelCard(props) {
    var m = props.model;
    var feats = m.feature_list || [];
    return h("div", { className: "ai-model-card" },
      h("div", { className: "ai-model-head" },
        h("strong", null, m.model_name || ("model " + m.id)),
        h("span", { className: "ai-sev" }, m.status || "unknown")
      ),
      h("div", { className: "ai-grid-two" },
        h(MetricCell, { label: "Cluster", value: m.cluster_name || "global" }),
        h(MetricCell, { label: "Type", value: m.model_type || "—" }),
        h(MetricCell, { label: "Training rows", value: m.training_rows == null ? "n/a" : m.training_rows }),
        h(MetricCell, { label: "Contamination", value: num(m.contamination, 3) || "n/a" }),
        h(MetricCell, { label: "Trained", value: shortTime(m.created_at || m.training_end) }),
        h(MetricCell, { label: "Features", value: feats.length })
      ),
      feats.length ? h("div", { className: "ai-chips" }, feats.map(function (f) {
        return h("span", { className: "ai-chip", key: f }, f);
      })) : null,
      h("div", { className: "ai-head-actions" },
        h("button", { className: "ai-btn", disabled: props.busy, onClick: function () { props.onRetrain(m.cluster_name); } }, props.busy === "train" ? "Training…" : "Retrain"),
        h("button", { className: "ai-btn", disabled: props.busy, onClick: function () { props.onScore(m.cluster_name); } }, props.busy === "score" ? "Scoring…" : "Re-score")
      )
    );
  }

  function ActionRow(props) {
    var a = props.action;
    return h("div", { className: "ai-action-card" },
      h("div", { className: "ai-action-head" },
        h("span", { className: "ai-sev" }, a.action_level || "L?"),
        h("span", { className: "ai-action-type" }, a.action_type || "action"),
        h("span", { className: severityClass(a.execution_status === "executed" ? "warning" : (a.execution_status === "blocked" ? "critical" : "info")) }, a.execution_status || "unknown")
      ),
      h("pre", { className: "ai-pre ai-pre-cmd" }, a.command_preview || "(no command preview)"),
      h("div", { className: "ai-grid-two" },
        h(MetricCell, { label: "Requested by", value: a.requested_by || "—" }),
        h(MetricCell, { label: "Approved by", value: a.approved_by || "—" }),
        h(MetricCell, { label: "Approvals req.", value: a.approvals_required == null ? "—" : a.approvals_required }),
        h(MetricCell, { label: "Mutations", value: a.mutations_enabled ? "enabled" : "disabled" })
      )
    );
  }

  function AssistantBox(props) {
    var _q = useState(""), q = _q[0], setQ = _q[1];
    var _r = useState(null), res = _r[0], setRes = _r[1];
    var _l = useState(false), loading = _l[0], setLoading = _l[1];
    function ask() {
      var question = q.trim();
      if (!question) return;
      setLoading(true);
      setRes(null);
      api("/api/v1/assistant/ask", { method: "POST", body: JSON.stringify({ question: question }) })
        .then(function (body) { setRes(body); })
        .catch(function (err) { setRes({ answer: "Error: " + err.message, model: "error" }); })
        .then(function () { setLoading(false); });
    }
    return h("div", { className: "ai-body" },
      h("p", { className: "ai-copy" }, "Ask a read-only question grounded in live logs, readiness, and AI DBA evidence. Routed to the backend assistant tool, not generic chat."),
      h("div", { className: "ai-ask-row" },
        h("input", {
          className: "ai-input", value: q, placeholder: "e.g. why is the cluster at risk right now?",
          onChange: function (e) { setQ(e.target.value); },
          onKeyDown: function (e) { if (e.key === "Enter") ask(); }
        }),
        h("button", { className: "ai-btn", disabled: loading, onClick: ask }, loading ? "Asking…" : "Ask")
      ),
      res ? h("div", null,
        h("div", { className: "ai-section-title" }, "Answer" + (res.model ? " · " + res.model : "")),
        h("p", { className: "ai-copy" }, res.answer || "(no answer)"),
        (res.intent || res.evidence_count != null) ? h("p", { className: "ai-muted" },
          (res.intent ? ("intent: " + res.intent) : "") + (res.evidence_count != null ? ("  ·  evidence: " + res.evidence_count) : "")) : null
      ) : null
    );
  }

  function AiConsolePanel(props) {
    var open = props.open;
    var onClose = props.onClose;
    var embedded = props.embedded;
    var _a = useState("health"), tab = _a[0], setTab = _a[1];
    var _b = useState({ loading: true, error: null, incidents: [], models: [], anomalies: [], forecasts: [], alerts: [], actions: [], scheduler: null }), state = _b[0], setState = _b[1];
    var _c = useState(null), selected = _c[0], setSelected = _c[1];
    var _d = useState(null), busy = _d[0], setBusy = _d[1];       // {cluster, op}
    var _e = useState(null), notice = _e[0], setNotice = _e[1];
    var _f = useState(false), explaining = _f[0], setExplaining = _f[1];

    function load(evaluate) {
      setState(function (prev) { return Object.assign({}, prev, { loading: true, error: null }); });
      var incidentLoad = evaluate ? api("/api/v1/ai/incidents/evaluate", { method: "POST", body: "{}" }).then(function () { return api("/api/v1/ai/incidents"); }) : api("/api/v1/ai/incidents");
      Promise.all([
        incidentLoad,
        api("/api/v1/ml/models").catch(function () { return []; }),
        api("/api/v1/ml/anomalies").catch(function () { return []; }),
        api("/api/v1/ml/forecasts?limit=12").catch(function () { return []; }),
        api("/api/v1/alerts/notifications").catch(function () { return []; }),
        api("/api/v1/scheduler/status").catch(function () { return null; }),
        api("/api/v1/actions/audit").catch(function () { return []; })
      ]).then(function (values) {
        var incidents = asArray(values[0], ["incidents", "items", "results"]);
        var next = {
          loading: false,
          error: null,
          incidents: incidents,
          models: asArray(values[1], ["models", "items", "results"]),
          anomalies: asArray(values[2], ["anomalies", "items", "results"]),
          forecasts: asArray(values[3], ["forecasts", "items", "results"]),
          alerts: asArray(values[4], ["alerts", "notifications", "items", "results"]),
          scheduler: values[5],
          actions: asArray(values[6], ["actions", "items", "results"])
        };
        setState(next);
        if (!selected) setSelected(firstIncident(incidents));
        else {
          var match = incidents.filter(function (i) { return i.id === selected.id; })[0];
          if (match) setSelected(match);
        }
      }).catch(function (err) {
        setState(function (prev) { return Object.assign({}, prev, { loading: false, error: err.message }); });
      });
    }

    function explain(id) {
      setExplaining(true);
      api("/api/v1/ai/incidents/" + id + "/explain", { method: "POST", body: "{}" })
        .then(function (updated) { setSelected(updated); load(false); })
        .catch(function (err) { setNotice("Explain failed: " + err.message); })
        .then(function () { setExplaining(false); });
    }

    function runModelOp(op, cluster) {
      if (!cluster) { setNotice("Model has no cluster name; cannot " + op + "."); return; }
      setBusy({ cluster: cluster, op: op });
      setNotice(null);
      var url = op === "train"
        ? "/api/v1/ml/train/" + encodeURIComponent(cluster) + "?force=true"
        : "/api/v1/ml/score/" + encodeURIComponent(cluster);
      api(url, { method: "POST", body: "{}" })
        .then(function (res) {
          var status = res && (res.status || res.detail) ? (res.status || res.detail) : "done";
          setNotice((op === "train" ? "Retrain" : "Re-score") + " (" + cluster + "): " + status);
          load(false);
        })
        .catch(function (err) { setNotice((op === "train" ? "Retrain" : "Re-score") + " failed: " + err.message); })
        .then(function () { setBusy(null); });
    }

    useEffect(function () {
      if (open || embedded) load(false);
    }, [open, embedded]);

    var worst = firstIncident(state.incidents);
    var riskyForecasts = state.forecasts.filter(function (f) { return severityRank(f.severity) >= 3; });
    var anomalous = state.anomalies.filter(function (a) { return a.is_anomaly || severityRank(a.severity) >= 2; });

    // cluster severity breakdown + top risky region (derived from incidents + models)
    var breakdown = useMemo(function () {
      var byCluster = {};
      state.models.forEach(function (m) { if (m.cluster_name) byCluster[m.cluster_name] = { region: m.region, rank: 0 }; });
      state.incidents.forEach(function (i) {
        if (!i.cluster_name) return;
        var r = severityRank(i.severity);
        if (!byCluster[i.cluster_name] || r > byCluster[i.cluster_name].rank) {
          byCluster[i.cluster_name] = { region: i.region, rank: r, severity: i.severity };
        }
      });
      var names = Object.keys(byCluster);
      var crit = 0, warn = 0, healthy = 0, regionRank = {}, topRegion = null, topRank = -1;
      names.forEach(function (n) {
        var c = byCluster[n];
        if (c.rank >= 4) crit += 1; else if (c.rank >= 2) warn += 1; else healthy += 1;
        var reg = c.region || "—";
        regionRank[reg] = Math.max(regionRank[reg] || 0, c.rank);
      });
      Object.keys(regionRank).forEach(function (reg) {
        if (regionRank[reg] > topRank) { topRank = regionRank[reg]; topRegion = reg; }
      });
      return { total: names.length || 1, crit: crit, warn: warn, healthy: healthy, topRegion: topRegion || "—" };
    }, [state.incidents, state.models]);

    if (!open && !embedded) return null;
    return h("div", { className: embedded ? "ai-console embedded" : "ai-console" },
      !embedded && h("div", { className: "ai-scrim", onClick: onClose }),
      h("section", { className: "ai-panel", "aria-label": "AI DBA Console" },
        h("header", { className: "ai-head" },
          h("div", null, h("strong", null, "AI DBA Console"), h("span", null, "Risk, incidents, ML, forecasts, approvals")),
          h("div", { className: "ai-head-actions" },
            h("button", { className: "ai-btn", onClick: function () { load(true); } }, state.loading ? "Loading" : "Evaluate"),
            !embedded && h("button", { className: "ai-icon-btn", onClick: onClose, "aria-label": "Close AI console" }, "×")
          )
        ),
        h("nav", { className: "ai-tabs" }, ["health", "incidents", "ml", "approval", "assistant"].map(function (key) {
          return h("button", { key: key, className: tab === key ? "active" : "", onClick: function () { setTab(key); } }, key);
        })),
        state.error && h("div", { className: "ai-error" }, state.error),
        notice && h("div", { className: "ai-notice", onClick: function () { setNotice(null); } }, notice),
        tab === "health" && h("div", { className: "ai-body" },
          h("div", { className: "ai-grid" },
            h(MetricCell, { label: "Clusters", value: breakdown.total }),
            h(MetricCell, { label: "Healthy", value: breakdown.healthy }),
            h(MetricCell, { label: "Warning", value: breakdown.warn, tone: breakdown.warn ? "warn" : null }),
            h(MetricCell, { label: "Critical", value: breakdown.crit, tone: breakdown.crit ? "crit" : null }),
            h(MetricCell, { label: "Open incidents", value: state.incidents.length }),
            h(MetricCell, { label: "Top risky region", value: breakdown.topRegion }),
            h(MetricCell, { label: "ML anomalies", value: anomalous.length }),
            h(MetricCell, { label: "Forecast risks", value: riskyForecasts.length }),
            h(MetricCell, { label: "Worst severity", value: worst ? worst.severity : "normal" }),
            h(MetricCell, { label: "Scheduler", value: state.scheduler && state.scheduler.enabled ? "enabled" : "disabled" })
          ),
          h(IncidentDetail, { item: worst, explain: explain, explaining: explaining })
        ),
        tab === "incidents" && h("div", { className: "ai-split" },
          h("div", { className: "ai-list" }, state.incidents.length
            ? state.incidents.map(function (i) { return h(IncidentRow, { key: i.id, item: i, active: selected && selected.id === i.id, onSelect: setSelected }); })
            : h("div", { className: "ai-empty" }, "No incidents.")),
          h(IncidentDetail, { item: selected || worst, explain: explain, explaining: explaining })
        ),
        tab === "ml" && h("div", { className: "ai-body" },
          h("div", { className: "ai-section-title" }, "Models"),
          state.models.length
            ? state.models.map(function (m) {
                return h(MlModelCard, {
                  key: m.id, model: m,
                  busy: busy && busy.cluster === m.cluster_name ? busy.op : null,
                  onRetrain: function (c) { runModelOp("train", c); },
                  onScore: function (c) { runModelOp("score", c); }
                });
              })
            : h("p", { className: "ai-muted" }, "No trained models."),
          h("div", { className: "ai-section-title" }, "Recent anomaly scores"),
          h("div", { className: "ai-table" },
            h("div", { className: "ai-table-row ai-an-row ai-table-head" },
              h("span", null, "Scored"), h("span", null, "Anomaly"), h("span", null, "Score"), h("span", null, "Severity"), h("span", null, "Top features")),
            state.anomalies.slice(0, 8).map(function (a) {
              return h("div", { className: "ai-table-row ai-an-row", key: a.id },
                h("span", { className: "ai-muted" }, shortTime(a.scored_at)),
                h("span", null, a.is_anomaly ? "yes" : "no"),
                h("span", { className: "ai-mono" }, num(a.anomaly_score, 3) || "—"),
                h("span", { className: severityClass(a.severity) }, a.severity || "info"),
                h("span", { className: "ai-muted", title: (a.top_features || []).join(", ") }, (a.top_features || []).join(", ") || "—"));
            })
          ),
          h("div", { className: "ai-section-title" }, "Forecasts"),
          h(ForecastTable, { forecasts: state.forecasts })
        ),
        tab === "approval" && h("div", { className: "ai-body" },
          h("p", { className: "ai-copy" }, "Guarded action workflow. The LLM may recommend and preview commands; mutating actions stay blocked unless backend mutation mode is enabled and required approvals are met."),
          h("div", { className: "ai-grid-two" },
            h(MetricCell, { label: "Audited actions", value: state.actions.length }),
            h(MetricCell, { label: "Mutation floor", value: state.actions.some(function (a) { return a.mutations_enabled; }) ? "enabled" : "disabled" }),
            h(MetricCell, { label: "Blocked", value: state.actions.filter(function (a) { return a.execution_status === "blocked"; }).length }),
            h(MetricCell, { label: "Alert notifications", value: state.alerts.length })
          ),
          h("div", { className: "ai-section-title" }, "Action audit"),
          state.actions.length
            ? state.actions.map(function (a) { return h(ActionRow, { key: a.id, action: a }); })
            : h("p", { className: "ai-muted" }, "No action requests recorded.")
        ),
        tab === "assistant" && h(AssistantBox, null)
      )
    );
  }

  function Launcher() {
    var _a = useState(false), open = _a[0], setOpen = _a[1];
    return h(React.Fragment, null,
      h("button", { className: "ai-launch", onClick: function () { setOpen(true); }, "aria-label": "Open AI DBA Console" }, "AI"),
      h(AiConsolePanel, { open: open, onClose: function () { setOpen(false); } })
    );
  }

  function mountLauncher() {
    if (document.getElementById("ai-console-root")) return;
    var root = document.createElement("div");
    root.id = "ai-console-root";
    document.body.appendChild(root);
    ReactDOM.createRoot(root).render(h(Launcher));
  }

  function injectStyles() {
    if (document.getElementById("ai-console-style")) return;
    var style = document.createElement("style");
    style.id = "ai-console-style";
    style.textContent = [
      ".ai-launch{position:fixed;right:18px;bottom:18px;z-index:90;width:44px;height:44px;border:0;border-radius:8px;background:#1e8467;color:#fff;font-weight:800;box-shadow:0 10px 24px rgba(0,0,0,.22);cursor:pointer}",
      ".ai-scrim{position:fixed;inset:0;background:rgba(10,16,24,.36);z-index:91}",
      ".ai-panel{position:fixed;right:18px;bottom:74px;z-index:92;width:min(1040px,calc(100vw - 36px));height:min(760px,calc(100vh - 96px));background:var(--panel,#fff);color:var(--text,#1d2433);border:1px solid var(--border,#dce3ec);border-radius:8px;box-shadow:0 20px 60px rgba(0,0,0,.28);display:flex;flex-direction:column;overflow:hidden}",
      ".ai-console.embedded .ai-panel{position:relative;right:auto;bottom:auto;width:100%;height:auto;min-height:620px;box-shadow:none}",
      ".ai-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid var(--border,#dce3ec)}",
      ".ai-head span{display:block;color:var(--muted,#64748b);font-size:12px;margin-top:2px}.ai-head-actions{display:flex;gap:8px;align-items:center}",
      ".ai-btn,.ai-icon-btn{border:1px solid var(--border,#cbd5e1);background:var(--button,#f8fafc);color:inherit;border-radius:6px;padding:7px 10px;cursor:pointer}.ai-btn[disabled]{opacity:.6;cursor:default}.ai-icon-btn{font-size:22px;line-height:18px;padding:4px 9px}",
      ".ai-tabs{display:flex;gap:4px;padding:8px 10px;border-bottom:1px solid var(--border,#dce3ec)}.ai-tabs button{border:0;background:transparent;color:inherit;padding:7px 10px;border-radius:6px;text-transform:capitalize;cursor:pointer}.ai-tabs .active{background:#e8f5f1;color:#14624d;font-weight:700}",
      ".ai-body{padding:12px;overflow:auto}.ai-grid{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:8px}.ai-grid-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}",
      ".ai-metric{border:1px solid var(--border,#dce3ec);border-radius:8px;padding:10px;min-width:0}.ai-metric span{display:block;color:var(--muted,#64748b);font-size:12px}.ai-metric strong{display:block;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ai-metric-crit{border-color:#fecaca;background:#fef2f2}.ai-metric-warn{border-color:#fed7aa;background:#fff7ed}",
      ".ai-split{display:grid;grid-template-columns:320px minmax(0,1fr);gap:10px;padding:12px;overflow:hidden;min-height:0}.ai-list{overflow:auto;border-right:1px solid var(--border,#dce3ec);padding-right:8px}.ai-row{width:100%;display:grid;grid-template-columns:86px minmax(0,1fr) 60px;gap:8px;align-items:center;border:0;background:transparent;color:inherit;text-align:left;padding:8px;border-radius:6px;cursor:pointer}.ai-row:hover{background:rgba(30,132,103,.08)}.ai-row-active{background:rgba(30,132,103,.12)}.ai-row-main{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".ai-detail{padding:12px;overflow:auto}.ai-detail-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.ai-detail h3{margin:8px 0 4px;font-size:17px}.ai-detail p,.ai-copy{color:var(--text,#1d2433);line-height:1.45}.ai-muted{color:var(--muted,#64748b);font-size:12px}.ai-mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}",
      ".ai-sev{display:inline-flex;align-items:center;justify-content:center;min-width:68px;border-radius:999px;background:#e8f5f1;color:#14624d;font-size:11px;font-weight:800;text-transform:uppercase;padding:3px 7px}.ai-sev-warning{background:#fff7ed;color:#9a3412}.ai-sev-critical{background:#fef2f2;color:#991b1b}",
      ".ai-section-title{font-weight:800;margin:14px 0 6px}.ai-pre{background:var(--code-bg,#0f172a);color:#e2e8f0;border-radius:8px;padding:10px;overflow:auto;max-height:260px;font-size:12px}.ai-pre-soft{max-height:160px}.ai-pre-cmd{max-height:120px;margin:6px 0}.ai-error,.ai-empty{margin:12px 0;padding:10px;border-radius:8px;background:#fef2f2;color:#991b1b}.ai-notice{margin:8px 12px 0;padding:8px 10px;border-radius:8px;background:#eff6ff;color:#1e3a8a;cursor:pointer;font-size:13px}",
      ".ai-table{display:grid;gap:6px;overflow:auto}.ai-table-row{display:grid;gap:8px;border:1px solid var(--border,#dce3ec);border-radius:8px;padding:8px;align-items:center}.ai-table-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ai-table-head{background:#f8fafc;font-weight:700;font-size:12px;color:#475569}",
      ".ai-rules-row{grid-template-columns:84px minmax(0,1.6fr) .9fr .9fr 1.1fr}.ai-fc-row{grid-template-columns:1.4fr .8fr .8fr 96px 1.1fr 1.1fr;min-width:720px}.ai-an-row{grid-template-columns:1.4fr .7fr .7fr 84px minmax(0,1.6fr);min-width:680px}.ai-log-row{grid-template-columns:84px .9fr minmax(0,1.8fr) .6fr}",
      ".ai-timeline{list-style:none;margin:6px 0 0;padding:0;display:grid;gap:6px}.ai-timeline li{display:grid;grid-template-columns:160px 84px 70px minmax(0,1fr);gap:8px;align-items:center;font-size:12px}.ai-tl-time{color:var(--muted,#64748b)}.ai-tl-risk{color:#475569}.ai-tl-reason{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".ai-model-card,.ai-action-card{border:1px solid var(--border,#dce3ec);border-radius:8px;padding:10px;margin-bottom:10px}.ai-model-head,.ai-action-head{display:flex;align-items:center;gap:8px;justify-content:space-between;margin-bottom:8px}.ai-action-head{justify-content:flex-start}.ai-action-type{font-weight:700}.ai-model-card .ai-head-actions{margin-top:8px}",
      ".ai-chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.ai-chip{background:#f1f5f9;border:1px solid var(--border,#e2e8f0);border-radius:999px;padding:2px 8px;font-size:11px;font-family:ui-monospace,Menlo,monospace}",
      ".ai-ask-row{display:flex;gap:8px;margin:8px 0}.ai-input{flex:1;border:1px solid var(--border,#cbd5e1);border-radius:6px;padding:8px 10px;color:inherit;background:var(--panel,#fff)}",
      "@media (max-width:900px){.ai-panel{left:10px;right:10px;width:auto}.ai-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ai-split{grid-template-columns:1fr}.ai-list{border-right:0;border-bottom:1px solid var(--border,#dce3ec);max-height:220px}}"
    ].join("");
    document.head.appendChild(style);
  }

  var PreviousAssistantScreen = window.AssistantScreen;
  window.AssistantScreen = function AiAssistantScreen(props) {
    return h("div", null,
      PreviousAssistantScreen ? h(PreviousAssistantScreen, props || {}) : null,
      h(AiConsolePanel, { embedded: true, open: true })
    );
  };
  window.AiConsolePanel = AiConsolePanel;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { injectStyles(); mountLauncher(); });
  } else {
    injectStyles();
    mountLauncher();
  }
})();
