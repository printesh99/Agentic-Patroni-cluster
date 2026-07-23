/* countup.js — animate KPI numbers on first paint */
(function () {
  "use strict";
  var seen = new WeakSet();
  function animate(el) {
    if (seen.has(el)) return;
    var txt = (el.textContent || "").trim();
    var m = /^([^0-9-]*)(-?[0-9][0-9,]*(?:\.[0-9]+)?)(.*)$/.exec(txt);
    if (!m) { seen.add(el); return; }
    var target = parseFloat(m[2].replace(/,/g, ""));
    if (!isFinite(target) || Math.abs(target) < 1) { seen.add(el); return; }
    seen.add(el);
    var pre = m[1], post = m[3];
    var dec = (m[2].split(".")[1] || "").length;
    var hasComma = m[2].indexOf(",") >= 0;
    var t0 = null, DUR = 420;
    function fmt(v) {
      var s = v.toFixed(dec);
      if (hasComma) s = s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
      return pre + s + post;
    }
    function step(ts) {
      if (!t0) t0 = ts;
      var p = Math.min(1, (ts - t0) / DUR);
      var ease = 1 - Math.pow(1 - p, 3);
      el.textContent = p < 1 ? fmt(target * ease) : txt;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  function scan() {
    document.querySelectorAll(".kpi .value, .stat .val, .activity-metric-value").forEach(animate);
  }
  var scheduled = false;
  var mo = new MutationObserver(function () {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(function () { scheduled = false; scan(); });
  });
  function start() {
    scan();
    mo.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
