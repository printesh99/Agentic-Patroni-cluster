// Application Monitoring — TPS Posting & TPS Warehouse (AM2)
// Thin wrapper over AppMonDomainView (see appmon_shared.jsx).

function AppMonTpsScreen(props) {
  return (
    <AppMonDomainView
      domains={[
        { slug: "tps",           label: "TPS Posting",   sub: "category = TPS" },
        { slug: "tps_warehouse", label: "TPS Warehouse", sub: "category = TPS_WAREHOUSE" }
      ]}
      lastRefresh={props.lastRefresh}
      timeRange={props.timeRange}/>
  );
}

window.AppMonTpsScreen = AppMonTpsScreen;
