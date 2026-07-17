// Application Monitoring — Banking Business Dashboard (BM1)
// Mirrors the Grafana "UAT Application Monitoring — Banking Business Dashboard"
// (read-only SQL panels) via BizMonDashboard. ES5-safe.

function AppMonBusinessScreen(props) {
  return <BizMonDashboard dashboardId="business" timeRange={props.timeRange} lastRefresh={props.lastRefresh}/>;
}

window.AppMonBusinessScreen = AppMonBusinessScreen;
