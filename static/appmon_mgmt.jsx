// Application Monitoring — Enterprise Core Banking Master Scorecard (BM2)
// Mirrors the Grafana "Enterprise Core Banking Master Observability Scorecard"
// (read-only SQL panels) via BizMonDashboard. ES5-safe.

function AppMonMgmtScreen(props) {
  return <BizMonDashboard dashboardId="management" timeRange={props.timeRange} lastRefresh={props.lastRefresh}/>;
}

window.AppMonMgmtScreen = AppMonMgmtScreen;
