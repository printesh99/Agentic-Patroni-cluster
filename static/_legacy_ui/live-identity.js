/* live-identity.js - install the real PROD cluster into the picker and make it the
 * default, so the app chrome (breadcrumb, picker, header) reflects the live
 * cluster instead of a placeholder route. Loaded after data.js, before
 * app.js, so window.CLUSTERS already exists. Synchronous on purpose. */
(function () {
  try {
    window.CLUSTERS = window.CLUSTERS || {};
    var live = {
      id: "prod",
      name: "prod-pgcluster-uae",
      label: "PROD",
      role: "PROD",
      namespace: "prod-pgcluster-uae-local",
      region: "UAE · kind dc1 (local)",
      pgVersion: "PostgreSQL 18.3",
      compute: "1 vCPU · 1 GiB",
      cores: 1, ramGiB: 1,
      serverState: "Healthy",
      ha: "Enabled (synchronous)",
      readReplica: "Sync Standby",
      pgBouncer: "1 / 1 ready",
      totalStorageGiB: 6,
      maxConns: 300,
      leader: "prod-pgcluster-uae-dc1-cpkl-0",
    };
    // Merge over existing identity fields but keep array-ish fields the UI may read.
    window.CLUSTERS.prod = Object.assign({}, window.CLUSTERS.prod || {}, live);
    window.ACTIVE_CLUSTER_ID = "prod";

    // If the user landed on a /clusters/uat/... or /clusters/dr/... placeholder path,
    // rewrite it to the live cluster so the first render is correct.
    var p = window.location.pathname || "";
    var m = p.match(/^\/clusters\/(uat|dr)\//);
    if (m) {
      var next = p.replace("/clusters/" + m[1] + "/", "/clusters/prod/");
      window.history.replaceState({}, "", next + window.location.search);
    }
  } catch (e) {
    if (window.console) console.warn("live-identity failed", e);
  }
})();
