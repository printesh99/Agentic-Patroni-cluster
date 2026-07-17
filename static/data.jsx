// Cluster identity registry and shared browser helpers.

const HBZ_CHART_PALETTE = [
  "#7c3aed", "#00b8d9", "#f59e0b", "#2f9fe8",
  "#14b8a6", "#ef476f", "#64748b", "#ec4899",
  "#36b37e", "#6366f1", "#f97316", "#a855f7"
];
window.HBZ_CHART_PALETTE = HBZ_CHART_PALETTE;
// v31: never mount the legacy seeded chart overlays. Live chart wrappers are
// loaded after them and render only API-attributed payloads.
window.HBZ_LIVE_CHARTS_ONLY = true;

function emptyCluster(id, name, label, region, role) {
  return {
    id: id,
    name: name,
    label: label,
    role: role || label,
    namespace: name,
    region: region,
    pgVersion: "-",
    compute: "-",
    cores: 0,
    ramGiB: 0,
    serverState: "Unknown",
    ha: "-",
    readReplica: "-",
    pgBouncer: "-",
    pods: [],
    pgBouncerPods: [],
    slots: [],
    databases: [],
    cpu: 0,
    mem: 0,
    storagePct: 0,
    totalStorageGiB: 0,
    usedStorageGiB: 0,
    walUsedGiB: 0,
    backupStorageGiB: 0,
    activeConns: 0,
    maxConns: 0,
    leader: name,
    lastFailover: "-",
    walArchive: "-",
    walArchiveLagSec: 0,
    cpuSeries: [],
    memSeries: [],
    connSeries: [],
    netInSeries: [],
    netOutSeries: [],
    iopsSeries: []
  };
}

const CLUSTERS = {
  uat: emptyCluster("uat", "uat-pgcluster-uae", "UAT", "UAT - OpenShift dc1", "UAT"),
  prod: emptyCluster("prod", "prod-pgcluster-uae", "PROD", "PROD - UAE - OpenShift dc1", "PROD"),
  dr: emptyCluster("dr", "dr-pgcluster-uae", "DR", "DR - UAE - OpenShift dc2", "DR")
};

const REGION_DEFS = [
  { code: "uk", name: "United Kingdom", dc: "OpenShift uk1" },
  { code: "ch", name: "Switzerland", dc: "OpenShift ch1" },
  { code: "ca", name: "Canada", dc: "OpenShift ca1" },
  { code: "ke", name: "Kenya", dc: "OpenShift ke1" },
  { code: "sa", name: "Saudi Arabia", dc: "OpenShift sa1" },
  { code: "hkg", name: "Hong Kong", dc: "OpenShift hk1" },
  { code: "landlord", name: "Landlord", dc: "OpenShift dc1" }
];

REGION_DEFS.forEach(function (def) {
  CLUSTERS["prod-" + def.code] = emptyCluster(
    "prod-" + def.code,
    "prod-pgcluster-" + def.code,
    "PROD",
    "PROD - " + def.name + " - " + def.dc,
    "PROD"
  );
  CLUSTERS["dr-" + def.code] = emptyCluster(
    "dr-" + def.code,
    "dr-pgcluster-" + def.code,
    "DR",
    "DR - " + def.name + " - " + def.dc,
    "DR"
  );
});

const SESSIONS = Object.freeze({});
const LOCK_TREE = Object.freeze({});
const TOP_SQL = Object.freeze({});

window.ACTIVE_CLUSTER_ID = window.localStorage ? (window.localStorage.getItem("hbz-active-cluster") || "prod") : "prod";
window.activeClusterId = function() { return window.ACTIVE_CLUSTER_ID || "prod"; };
window.clusterPath = function(path) { return "/api/v1/clusters/" + window.activeClusterId() + path; };
window.uiClusterPath = function(kind) { return "/api/v1/ui/" + kind + "/" + window.activeClusterId(); };
window.lifecyclePath = function(action) { return "/api/v1/lifecycle/" + action + "/" + window.activeClusterId(); };
window.activeCluster = function() {
  return (window.CLUSTERS && window.CLUSTERS[window.activeClusterId()]) || (window.CLUSTERS && window.CLUSTERS.prod) || {};
};
window.clusterPrefix = function() {
  var c = window.activeCluster();
  return c.name ? c.name + "-" : "prod-pgcluster-uae-";
};
window.shortClusterName = function(name) {
  return name ? String(name).replace(window.clusterPrefix(), "") : "-";
};

window.CLUSTERS = CLUSTERS;
window.SESSIONS = SESSIONS;
window.LOCK_TREE = LOCK_TREE;
window.TOP_SQL = TOP_SQL;

window.fmtBytes = (n) => {
  if (n == null) return "-";
  const units = ["B","KiB","MiB","GiB","TiB"];
  let i = 0, v = Number(n);
  if (!Number.isFinite(v)) return "-";
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 2 : 1)} ${units[i]}`;
};
window.fmtInt = (n) => n == null || !Number.isFinite(Number(n)) ? "-" : Number(n).toLocaleString();
window.fmtMs = (ms) => {
  if (ms == null || !Number.isFinite(Number(ms))) return "-";
  ms = Number(ms);
  if (ms < 1) return `${(ms * 1000).toFixed(0)} us`;
  if (ms < 1000) return `${ms.toFixed(2)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
};
window.fmtSec = (s) => {
  if (s == null || !Number.isFinite(Number(s))) return "-";
  s = Number(s);
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${Math.round(s%60)}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
};
window.shortUUID = () => {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  const hex = "0123456789abcdef";
  let s = "";
  for (let i = 0; i < 32; i++) {
    s += hex[Math.floor(Math.random() * 16)];
    if (i === 7 || i === 11 || i === 15 || i === 19) s += "-";
  }
  return s;
};
