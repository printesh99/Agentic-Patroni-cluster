/* v31 honest empty states for Overview history cards. Live history is rendered
   by overview.jsx; these components are used only when the API has no points. */
(function () {
  "use strict";
  var e = React.createElement;
  function unavailable(message) {
    return e("div", { style: { padding: "48px 12px", textAlign: "center", color: "var(--fg-dim)" } },
      e("div", { style: { marginBottom: 6 } },
        e("span", { className: "pill warn", style: { fontSize: 10 } }, "live history unavailable")),
      message + " No values are inferred.");
  }
  window.HbzSampleConn = function () { return unavailable("Connection history has not been ingested."); };
  window.HbzSampleStorage = function () { return unavailable("Storage history has not been ingested."); };
})();
