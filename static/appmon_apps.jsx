// Application Monitoring — Charge / Locker / Mobile / Document (AM4)
// Schema-scoped domains (cross-database) over AppMonDomainView.

function AppMonAppsScreen(props) {
  return (
    <AppMonDomainView
      domains={[
        { slug: "charge",   label: "Charge",   sub: "schema = charge" },
        { slug: "locker",   label: "Locker",   sub: "schema = locker" },
        { slug: "mobile",   label: "Mobile",   sub: "schema = mobile" },
        { slug: "document", label: "Document", sub: "schema = document" }
      ]}
      lastRefresh={props.lastRefresh}
      timeRange={props.timeRange}/>
  );
}

window.AppMonAppsScreen = AppMonAppsScreen;
