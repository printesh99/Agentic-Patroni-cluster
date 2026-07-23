// Business / Management dashboards — generic renderer for the read-only SQL
// panels mirrored from the Grafana "Banking Business Dashboard" and
// "Enterprise Core Banking Master Scorecard". Metadata comes from
// /bizmon/dashboards; each panel lazily fetches /bizmon/panel/{id}. ES5-safe.

// ---- value helpers ---------------------------------------------------------
function bizmonIsNum(v) { return typeof v === "number" && isFinite(v); }

function bizmonNumericCol(columns, rows) {
  // Prefer a column literally named "value", else the last all-numeric column.
  var vi = columns.indexOf("value");
  if (vi !== -1) return vi;
  for (var c = columns.length - 1; c >= 0; c--) {
    var allNum = rows.length > 0;
    for (var r = 0; r < rows.length; r++) {
      if (!bizmonIsNum(rows[r][c])) { allNum = false; break; }
    }
    if (allNum) return c;
  }
  return -1;
}

function bizmonLabelCol(columns, rows, skipIdx) {
  for (var c = 0; c < columns.length; c++) {
    if (c === skipIdx) continue;
    if (columns[c] === "time") continue;
    var hasText = false;
    for (var r = 0; r < rows.length; r++) {
      if (!bizmonIsNum(rows[r][c]) && rows[r][c] != null) { hasText = true; break; }
    }
    if (hasText || rows.length === 0) return c;
  }
  return columns.length > 0 && columns[0] !== "time" ? 0 : -1;
}

function bizmonFmtValue(v, unit) {
  if (v == null) return "—";
  if (!bizmonIsNum(v)) return String(v);
  if (unit === "bytes" || unit === "decbytes") return fmtBytes(v);
  if (unit === "percent" || unit === "percentunit") {
    return (unit === "percentunit" ? (v * 100) : v).toFixed(v % 1 ? 1 : 0) + "%";
  }
  if (unit === "currencyUSD" || unit === "currencyAED") return fmtInt(Math.round(v));
  if (Math.abs(v) >= 1000 || v % 1 === 0) return fmtInt(v);
  return String(v);
}

// ---- per-panel component ---------------------------------------------------
function BizMonPanel(props) {
  var panel = props.panel;
  var range = props.range;
  var st = useState(null); var data = st[0]; var setData = st[1];
  var ls = useState(false); var loading = ls[0]; var setLoading = ls[1];

  useEffect(function() {
    if (panel.type === "text" || !panel.has_query) return;
    var alive = true; setLoading(true);
    fetch(clusterPath("/bizmon/panel/" + encodeURIComponent(panel.id) + "?range=" + encodeURIComponent(range)),
          { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) { if (alive) { setData(d); setLoading(false); } })
      .catch(function() { if (alive) { setData({ available: false, error: "request failed" }); setLoading(false); } });
    return function() { alive = false; };
  }, [panel.id, range, props.lastRefresh]);

  // grid width: Grafana uses a 24-column grid.
  var basis = Math.max(20, Math.min(100, Math.round((panel.w || 12) / 24 * 100)));
  var style = { flexGrow: 1, flexBasis: "calc(" + basis + "% - 12px)", minWidth: 260 };

  // text/legend panel
  if (panel.type === "text") {
    return (
      <div className="card" style={style}>
        <div className="bd bizmon-text txt-xs muted" style={{whiteSpace: "pre-wrap", lineHeight: 1.5}}>
          {(panel.markdown || "").replace(/[#*`>]/g, "").trim()}
        </div>
      </div>
    );
  }

  var body;
  if (loading || data == null) {
    body = <div className="bd muted txt-xs" style={{padding: 14}}>Loading…</div>;
  } else if (!data.available) {
    body = <EmptyState icon={Icon.Database}
                       title={data.error ? "Unavailable" : "No data"}
                       hint={data.error || "No rows for the current range."}/>;
  } else {
    body = <BizMonContent panel={panel} data={data}/>;
  }

  return (
    <div className="card" style={style}>
      <div className="hd">
        {panel.title}
        {panel.db ? <span className="meta">{panel.db.replace(/^ae_|_uat$/g, "")}</span> : null}
      </div>
      {body}
    </div>
  );
}

// ---- content renderer by panel type ---------------------------------------
function BizMonContent(props) {
  var panel = props.panel, data = props.data;
  var cols = data.columns || [], rows = data.rows || [];
  var unit = panel.unit;

  if (!rows.length) return <EmptyState icon={Icon.Database} title="No data" hint="Query returned no rows."/>;

  var vi = bizmonNumericCol(cols, rows);

  // single-value tiles
  if (panel.type === "stat" || panel.type === "gauge") {
    if (rows.length === 1 && vi !== -1) {
      return (
        <div className="bd" style={{paddingTop: 6}}>
          <div className="val" style={{fontSize: 26, fontWeight: 700}}>{bizmonFmtValue(rows[0][vi], unit)}</div>
        </div>
      );
    }
    // multiple rows -> small list
    var li = bizmonLabelCol(cols, rows, vi);
    return (
      <div className="bd">
        <BarList rows={rows.map(function(r) { return { label: li !== -1 ? String(r[li]) : "—", value: Number(r[vi]) }; })}
                 valueFormatter={function(v) { return bizmonFmtValue(v, unit); }}/>
      </div>
    );
  }

  // bar/pie -> chart from (label, value)
  if (panel.type === "bargauge" || panel.type === "piechart") {
    var li2 = bizmonLabelCol(cols, rows, vi);
    var chartRows = rows.map(function(r) {
      return { label: li2 !== -1 ? String(r[li2]) : "—", value: Number(r[vi]) };
    });
    if (panel.type === "piechart") {
      return <div className="bd"><DonutChart rows={chartRows} valueFormatter={function(v) { return bizmonFmtValue(v, unit); }}/></div>;
    }
    return <div className="bd"><BarList rows={chartRows} limit={12} valueFormatter={function(v) { return bizmonFmtValue(v, unit); }}/></div>;
  }

  // timeseries -> group long-format (time, metric, value) into mini lines
  if (panel.type === "timeseries") {
    var ti = cols.indexOf("time");
    var mi = -1;
    for (var c = 0; c < cols.length; c++) { if (c !== ti && c !== vi && !bizmonIsNum((rows[0] || [])[c])) { mi = c; break; } }
    var series = {};
    rows.forEach(function(r) {
      var key = mi !== -1 ? String(r[mi]) : "series";
      (series[key] = series[key] || []).push(Number(r[vi]) || 0);
    });
    var keys = Object.keys(series).slice(0, 6);
    if (!keys.length) return <EmptyState icon={Icon.TrendingUp} title="No series"/>;
    return (
      <div className="bd">
        {keys.map(function(k, i) {
          var arr = series[k];
          var last = arr.length ? arr[arr.length - 1] : null;
          return (
            <div key={k} style={{marginBottom: 8}}>
              <div className="txt-xs muted" style={{display: "flex", justifyContent: "space-between"}}>
                <span title={k} style={{overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "70%"}}>{k}</span>
                <strong>{bizmonFmtValue(last, unit)}</strong>
              </div>
              <MiniLine data={arr} color={chartColor(i)} height={36}/>
            </div>
          );
        })}
      </div>
    );
  }

  // default: table
  return (
    <div style={{overflowX: "auto", maxHeight: 360, overflowY: "auto"}}>
      <table className="tbl">
        <thead><tr>{cols.map(function(c, i) { return <th key={i} className={i === vi ? "num" : ""}>{c}</th>; })}</tr></thead>
        <tbody>
          {rows.map(function(r, ri) {
            return (
              <tr key={ri}>
                {r.map(function(cell, ci) {
                  var isNum = bizmonIsNum(cell);
                  return <td key={ci} className={(isNum ? "num " : "") + "txt-xs"}>{isNum ? bizmonFmtValue(cell, ci === vi ? unit : null) : (cell == null ? "—" : String(cell))}</td>;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {data.truncated ? <div className="muted txt-xs" style={{padding: "4px 8px"}}>Showing first {rows.length} rows.</div> : null}
    </div>
  );
}

// ---- dashboard component ---------------------------------------------------
function BizMonDashboard(props) {
  var dashId = props.dashboardId;
  var ms = useState(null); var meta = ms[0]; var setMeta = ms[1];
  var ls = useState(true); var loading = ls[0]; var setLoading = ls[1];
  var range = props.timeRange || "24h";

  useEffect(function() {
    var alive = true; setLoading(true);
    fetch(clusterPath("/bizmon/dashboards"), { cache: "no-store" })
      .then(hbzJsonResponse)
      .then(function(d) {
        if (!alive) return;
        var dash = (d.dashboards || []).filter(function(x) { return x.id === dashId; })[0] || null;
        setMeta({ dash: dash, credential: d.credential }); setLoading(false);
      })
      .catch(function() { if (alive) { setMeta(null); setLoading(false); } });
    return function() { alive = false; };
  }, [dashId, props.lastRefresh]);

  if (loading) return <div className="page"><div className="card"><div className="bd muted">Loading dashboard…</div></div></div>;
  if (!meta || !meta.dash) return <div className="page"><EmptyState icon={Icon.Database} title="Dashboard unavailable" hint="The panel registry could not be loaded."/></div>;

  var dash = meta.dash;
  return (
    <div className="page">
      {meta.credential === "app" ? (
        <div className="card mt-1" style={{borderLeft: "3px solid var(--warn, #B8893C)"}}>
          <div className="bd txt-xs muted">
            Read access uses the application role. If panels show “permission denied”, grant the read-only role
            <strong> USAGE + SELECT</strong> on the banking schemas (see deploy notes).
          </div>
        </div>
      ) : null}
      {dash.rows.map(function(row, ri) {
        return (
          <div key={ri} className="mt-3">
            {row.title ? (
              <div className="section-title" style={{fontWeight: 700, fontSize: 13, textTransform: "uppercase", color: "var(--muted)", letterSpacing: ".04em", marginBottom: 8}}>
                {row.title}
              </div>
            ) : null}
            <div style={{display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-start"}}>
              {row.panels.map(function(p) {
                return <BizMonPanel key={p.id} panel={p} range={range} lastRefresh={props.lastRefresh}/>;
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

window.BizMonPanel = BizMonPanel;
window.BizMonContent = BizMonContent;
window.BizMonDashboard = BizMonDashboard;
