/* ClusterArchitecture — full connected cluster topology diagram.
   Rendered on the Cluster screen in place of the simple 3-node Topology.
   Shows: Applications → PgBouncer → K8s service → Leader/Standbys (Patroni)
   → pgBackRest repo host → S3 bucket, with PGO operator + K8s DCS control. */
(function () {
  "use strict";

  var e = React.createElement;

  function fmtB(n) {
    n = Number(n || 0);
    if (!n) return "0 B";
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (Math.round(n * 10) / 10) + " " + u[i];
  }

  /* ---- tiny SVG icon glyphs (stroke style) ---- */
  function icon(kind, color) {
    var s = { fill: "none", stroke: color, strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round", flex: "0 0 auto" };
    var kids;
    if (kind === "app") kids = [
      e("rect", { key: 1, x: 3, y: 4, width: 18, height: 12, rx: 2 }),
      e("path", { key: 2, d: "M8 20h8M12 16v4" })];
    else if (kind === "pool") kids = [
      e("circle", { key: 1, cx: 12, cy: 5, r: 2.4 }),
      e("circle", { key: 2, cx: 5, cy: 19, r: 2.4 }),
      e("circle", { key: 3, cx: 19, cy: 19, r: 2.4 }),
      e("path", { key: 4, d: "M10.8 7 6.2 16.8M13.2 7l4.6 9.8M7.4 19h9.2" })];
    else if (kind === "db") kids = [
      e("ellipse", { key: 1, cx: 12, cy: 5.5, rx: 8, ry: 3 }),
      e("path", { key: 2, d: "M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13" }),
      e("path", { key: 3, d: "M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" })];
    else if (kind === "vault") kids = [
      e("rect", { key: 1, x: 3, y: 5, width: 18, height: 14, rx: 2 }),
      e("circle", { key: 2, cx: 12, cy: 12, r: 3.4 }),
      e("path", { key: 3, d: "M12 10.2v1.8l1.3 1.3" })];
    else if (kind === "bucket") kids = [
      e("path", { key: 1, d: "M4 6h16l-1.8 13a2 2 0 0 1-2 1.7H7.8a2 2 0 0 1-2-1.7L4 6Z" }),
      e("ellipse", { key: 2, cx: 12, cy: 6, rx: 8, ry: 2.2 })];
    else if (kind === "gear") kids = [
      e("circle", { key: 1, cx: 12, cy: 12, r: 3 }),
      e("path", { key: 2, d: "M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" })];
    else kids = [e("circle", { key: 1, cx: 12, cy: 12, r: 8 })];
    return e("svg", { width: 20, height: 20, viewBox: "0 0 24 24", style: s }, kids);
  }

  function pill(txt, tone) {
    var map = {
      ok:    ["rgba(38,166,91,.12)",  "#1d7a44"],
      warn:  ["rgba(240,165,0,.16)",  "#8a6100"],
      info:  ["rgba(124,58,237,.10)",  "#6d28d9"],
      muted: ["var(--surface-3, #eef2f5)", "var(--fg-dim, #667)"]
    };
    var c = map[tone] || map.muted;
    return e("span", {
      key: txt,
      style: { background: c[0], color: c[1], borderRadius: 999, padding: "1px 7px", fontSize: 9.5, fontWeight: 700, whiteSpace: "nowrap" }
    }, txt);
  }

  /* ---- node card (positions in px) ---- */
  function Node(p) {
    var accent = p.accent || "#7c3aed";
    return e("div", {
      style: {
        position: "absolute", left: p.x, top: p.y, width: p.w, boxSizing: "border-box",
        background: p.ghost ? "transparent"
          : p.hero
            ? "linear-gradient(180deg, rgba(124,58,237,.10) 0%, var(--surface, #fff) 55%)"
            : "var(--surface, #fff)",
        border: p.ghost ? "1.5px dashed var(--border-strong, #b9c6cf)" : "1px solid " + (p.hero ? accent : "var(--border, #dde5ea)"),
        borderTop: p.ghost ? "1.5px dashed var(--border-strong, #b9c6cf)" : "3px solid " + accent,
        borderRadius: 10, padding: "9px 11px", zIndex: 2,
        opacity: p.ghost ? .75 : 1,
        boxShadow: p.ghost ? "none" : p.hero
          ? "0 10px 22px rgba(124,58,237,.20), 0 2px 6px rgba(30,23,64,.10)"
          : "0 6px 14px rgba(30,23,64,.10), 0 1px 3px rgba(30,23,64,.08)"
      }
    },
      e("div", { style: { display: "flex", alignItems: "center", gap: 7, minWidth: 0 } },
        icon(p.icon, accent),
        e("div", { style: { minWidth: 0 } },
          e("div", { style: { fontSize: 9.5, fontWeight: 800, letterSpacing: ".06em", textTransform: "uppercase", color: accent } }, p.kind),
          e("div", { style: { fontFamily: "var(--font-mono, monospace)", fontSize: 11, fontWeight: 700, color: "var(--fg, #1a202c)", overflowWrap: "break-word" } }, p.title))),
      p.sub && p.sub.length ? e("div", { style: { marginTop: 6, fontSize: 10.5, color: "var(--fg-dim, #6c757d)", display: "flex", gap: 5, flexWrap: "wrap", alignItems: "center" } }, p.sub) : null
    );
  }

  window.ClusterArchitecture = function ClusterArchitecture(props) {
    var members = props.members || [];
    var hostRef = React.useRef(null);
    var widthState = React.useState(0);
    var width = widthState[0], setWidth = widthState[1];

    React.useEffect(function () {
      var el = hostRef.current;
      if (!el) return;
      function measure() { setWidth(el.clientWidth); }
      measure();
      var ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
      if (ro) ro.observe(el);
      else window.addEventListener("resize", measure);
      return function () { ro ? ro.disconnect() : window.removeEventListener("resize", measure); };
    }, []);

    var leader = null, standbys = [];
    members.forEach(function (m) {
      if ((m.role === "leader" || m.role === "master" || m.role === "standby_leader") && !leader) leader = m;
      else standbys.push(m);
    });
    if (!leader && members.length) { leader = members[0]; standbys = members.slice(1); }
    var ghost = members.length === 0;

    var roleLabel = { leader: "Leader · Primary", sync_standby: "Sync Standby", replica: "Async Replica", standby_leader: "Standby Leader" };

    var H = 596;
    var W = Math.max(width || 0, 760);

    if (!width) return e("div", { ref: hostRef, style: { width: "100%", height: H } });

    /* ---- layout (px) ---- */
    function col(pct) { return Math.round(W * pct / 100); }
    var Y = { top: 26, pods: 168, patroni: 330, bottom: 436 };
    var nodes = [], edges = [], labels = [];

    var apps  = { x: col(1),  y: Y.top, w: col(15) };
    var pgb   = { x: col(24), y: Y.top, w: col(18) };
    var svc   = { x: col(51), y: Y.top, w: col(18) };
    var pgo   = { x: col(1),  y: Y.bottom, w: col(14) };
    var repo  = { x: col(40), y: Y.bottom, w: col(22) };
    var s3    = { x: col(70), y: Y.bottom, w: col(19) };

    var podW = col(20), podGap = col(4);
    var leaderP = { x: col(27), y: Y.pods, w: podW };
    var sbList = ghost
      ? [{ name: "sync standby pod", role: "sync_standby", state: "streaming", ghost: true }]
      : standbys.slice(0, 2);
    var sbPos = sbList.map(function (_, i) {
      return { x: col(53) + i * (podW + podGap), y: Y.pods, w: podW };
    });

    var cx = function (b) { return b.x + b.w / 2; };

    /* ---- edges (px space, uniform scale) ---- */
    var blue = "#7c3aed", green = "#26a65b", amber = "#b7791f", violet = "#7c5cd6", gray = "#94a3b8";
    var NODE_H = 78;

    function P(d, color, o) {
      o = o || {};
      edges.push(e("path", {
        key: "p" + edges.length, d: d, fill: "none",
        stroke: color, strokeWidth: o.w || 2,
        strokeDasharray: o.dashed ? "4 4" : (o.flow ? "7 5" : "none"),
        className: o.flow ? "arch-flow" : "",
        markerEnd: "url(#archArrow-" + (o.m || "blue") + ")",
        opacity: o.op || 0.9
      }));
    }
    function L(x, y, txt, color) {
      labels.push(e("div", {
        key: "l" + labels.length,
        style: {
          position: "absolute", left: x, top: y, transform: "translate(-50%,-50%)",
          fontSize: 10, fontWeight: 700, color: color || "var(--fg-dim, #64748b)",
          background: "var(--surface, #fff)", padding: "1px 6px", borderRadius: 999,
          border: "1px solid var(--border, #e2e8ec)", zIndex: 2, whiteSpace: "nowrap"
        }
      }, txt));
    }

    // apps -> pgbouncer -> service
    P("M " + (apps.x + apps.w) + " " + (Y.top + 34) + " H " + (pgb.x - 6), blue, { flow: true });
    L((apps.x + apps.w + pgb.x) / 2, Y.top + 18, "SQL · 5432");
    P("M " + (pgb.x + pgb.w) + " " + (Y.top + 34) + " H " + (svc.x - 6), blue, { flow: true });
    L((pgb.x + pgb.w + svc.x) / 2, Y.top + 18, "pooled");
    // service -> leader (drop below the band caption before curving in)
    P("M " + cx(svc) + " " + (Y.top + NODE_H + 12) + " C " + (cx(svc) + 30) + " " + (Y.pods - 20) + ", " + (cx(leaderP) + 60) + " " + (Y.pods - 30) + ", " + cx(leaderP) + " " + (Y.pods - 6), blue, { flow: true });
    // leader -> standbys
    sbPos.forEach(function (sp, i) {
      P("M " + (leaderP.x + leaderP.w) + " " + (Y.pods + 44) + " C " + (leaderP.x + leaderP.w + 30) + " " + (Y.pods + 44) + ", " + (sp.x - 34) + " " + (Y.pods + 44) + ", " + (sp.x - 6) + " " + (Y.pods + 44), green, { flow: true, m: "green" });
      if (i === 0) L((cx(leaderP) + cx(sp)) / 2, Y.pods - 14, "streaming replication", "#1d7a44");
    });
    // pods <-> patroni bar
    [cx(leaderP)].concat(sbPos.map(cx)).forEach(function (x) {
      P("M " + x + " " + (Y.pods + NODE_H + 26) + " V " + (Y.patroni - 6), gray, { dashed: true, w: 1.4, m: "gray", op: .6 });
    });
    // leader -> repo host (WAL) — departs from the leader pod's left edge
    P("M " + (leaderP.x - 4) + " " + (Y.pods + 58) + " C " + (leaderP.x - 56) + " " + (Y.pods + 70) + ", " + (leaderP.x - 60) + " " + (Y.bottom - 60) + ", " + (cx(repo) - repo.w / 2 - 6) + " " + (Y.bottom + 30), amber, { flow: true, m: "amber" });
    L(col(13), Y.patroni - 24, "WAL archive · backups", "#8a6100");
    // repo -> s3
    P("M " + (repo.x + repo.w) + " " + (Y.bottom + 34) + " H " + (s3.x - 6), amber, { flow: true, m: "amber" });
    L((repo.x + repo.w + s3.x) / 2, Y.bottom + 18, "pgBackRest");
    // pgo -> cluster band
    P("M " + cx(pgo) + " " + (Y.bottom - 6) + " C " + cx(pgo) + " " + (Y.patroni + 70) + ", " + (col(24) - 50) + " " + (Y.patroni + 70) + ", " + (col(24) + 4) + " " + (Y.patroni + 40), violet, { dashed: true, m: "violet" });
    L(Math.max(cx(pgo), 92), Y.bottom - 32, "reconciles CR", "#6d4fc4");

    var markers = ["blue|#7c3aed", "green|#26a65b", "amber|#b7791f", "violet|#7c5cd6", "gray|#94a3b8"].map(function (mc) {
      var kv = mc.split("|");
      return e("marker", { key: kv[0], id: "archArrow-" + kv[0], viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 5.5, markerHeight: 5.5, orient: "auto" },
        e("path", { d: "M0 0 L10 5 L0 10 Z", fill: kv[1] }));
    });

    /* ---- nodes ---- */
    nodes.push(e(Node, { key: "apps", x: apps.x, y: apps.y, w: apps.w, icon: "app", accent: "#475569", kind: "Clients", title: "Applications", sub: [pill("core banking · services", "muted")] }));
    nodes.push(e(Node, { key: "pgb", x: pgb.x, y: pgb.y, w: pgb.w, icon: "pool", accent: blue, kind: "Pooler", title: "pgbouncer", sub: [pill("transaction pooling", "info"), pill(":5432", "muted")] }));
    nodes.push(e(Node, { key: "svc", x: svc.x, y: svc.y, w: svc.w, icon: "pool", accent: "#0e7490", kind: "K8s Service", title: "primary/replicas", sub: [pill("rw / ro routing", "info")] }));

    nodes.push(e(Node, {
      key: "leader", x: leaderP.x, y: leaderP.y, w: leaderP.w, icon: "db", hero: !ghost, ghost: ghost,
      accent: "#26a65b", kind: roleLabel[leader && leader.role] || "Leader · Primary",
      title: leader ? leader.name : "leader pod",
      sub: [
        pill(leader && leader.state || "running", (!leader || leader.state === "running" || leader.state === "streaming") ? "ok" : "warn"),
        pill("TL " + (leader && leader.timeline || "—"), "muted"),
        pill("read-write", "info")
      ]
    }));
    sbList.forEach(function (m, i) {
      var lag = Number(m.replay_lag || m.lag || 0);
      nodes.push(e(Node, {
        key: "sb" + i, x: sbPos[i].x, y: sbPos[i].y, w: sbPos[i].w, icon: "db", ghost: !!m.ghost,
        accent: m.role === "sync_standby" ? blue : "#0e7490",
        kind: roleLabel[m.role] || m.role,
        title: m.name,
        sub: [
          pill(m.state || "streaming", (m.state === "running" || m.state === "streaming") ? "ok" : "warn"),
          lag > 0 ? pill("lag " + fmtB(lag), "warn") : pill("in sync", "ok"),
          pill("read-only", "muted")
        ]
      }));
    });

    nodes.push(e(Node, { key: "pgo", x: pgo.x, y: pgo.y, w: pgo.w, icon: "gear", accent: violet, kind: "Operator", title: "PGO · Crunchy", sub: [pill("control plane", "muted")] }));
    nodes.push(e(Node, { key: "repo", x: repo.x, y: repo.y, w: repo.w, icon: "vault", accent: amber, kind: "Backup Repo Host", title: "pgbackrest repo-host", sub: [pill("full · diff · incr", "warn"), pill("WAL archive", "muted")] }));
    nodes.push(e(Node, { key: "s3", x: s3.x, y: s3.y, w: s3.w, icon: "bucket", accent: amber, kind: "Object Storage", title: "S3 bucket", sub: [pill("pgbackrest repo1", "muted"), pill("encrypted", "ok")] }));

    /* ---- chrome: cluster band + patroni bar + legend ---- */
    var band = e("div", {
      key: "band",
      style: {
        position: "absolute", left: col(24), top: Y.pods - 36, width: col(55), height: Y.patroni - Y.pods + 82,
        border: "1.5px dashed rgba(124,58,237,.45)", borderRadius: 14,
        background: "linear-gradient(180deg, rgba(124,58,237,.05), rgba(124,58,237,.015))", zIndex: 0
      }
    },
      e("div", { style: { position: "absolute", top: -9, left: 14, background: "var(--surface, #fff)", padding: "0 8px", fontSize: 10, fontWeight: 800, letterSpacing: ".08em", textTransform: "uppercase", color: "#6d28d9", whiteSpace: "nowrap" } }, "PostgresCluster · OpenShift namespace"));

    var patroniBar = e("div", {
      key: "patroni",
      style: {
        position: "absolute", left: col(27), top: Y.patroni, width: col(49), zIndex: 2,
        background: "var(--surface-2, #f4f8fb)", border: "1px solid var(--border, #dde5ea)",
        borderRadius: 8, padding: "7px 12px", display: "flex", alignItems: "center", gap: 8,
        boxShadow: "0 3px 8px rgba(30,23,64,.08)", boxSizing: "border-box", flexWrap: "wrap"
      }
    },
      icon("gear", "#64748b"),
      e("span", { style: { fontSize: 11, fontWeight: 800, color: "var(--fg, #1a202c)" } }, "Patroni HA"),
      e("span", { style: { fontSize: 10.5, color: "var(--fg-dim, #6c757d)" } }, "leader election · health checks · automatic failover"),
      e("span", { style: { marginLeft: "auto" } }, pill("DCS: Kubernetes endpoints", "info")));

    function leg(color, dashed, txt) {
      return e("span", { key: txt, style: { display: "flex", alignItems: "center", gap: 5 } },
        e("span", { style: { width: 18, height: 0, borderTop: dashed ? "2px dashed " + color : "2.5px solid " + color } }), txt);
    }
    var legend = e("div", { key: "legend", style: { position: "absolute", left: col(1), bottom: 0, display: "flex", gap: 16, fontSize: 10.5, color: "var(--fg-dim, #64748b)", alignItems: "center", zIndex: 2, flexWrap: "wrap" } },
      leg(blue, false, "client traffic"),
      leg(green, false, "streaming replication"),
      leg(amber, false, "backups / WAL"),
      leg(violet, true, "operator control"),
      leg(gray, true, "Patroni coordination"));

    return e("div", { ref: hostRef, style: { position: "relative", width: "100%", height: H, overflow: "hidden" } },
      e("style", { key: "kf" }, "@keyframes archFlowDash{to{stroke-dashoffset:-24}}.arch-flow{animation:archFlowDash 1.4s linear infinite}"),
      band,
      e("svg", { key: "svg", width: W, height: H, viewBox: "0 0 " + W + " " + H, style: { position: "absolute", inset: 0, zIndex: 1, overflow: "visible" } },
        e("defs", null, markers), edges),
      patroniBar,
      labels,
      nodes,
      legend
    );
  };
})();
