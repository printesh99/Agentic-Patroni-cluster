/* ai_platform2.js - compatibility loader.
 * The live AI Platform implementation is in ai_platform.js. This file is kept
 * because static/index.html from the v22 deployment loads it after ai_platform.js.
 */
(function () {
  "use strict";
  if (!window.AIPlatformScreen && window.__AIPLAT && window.React) {
    var e = window.React.createElement;
    window.AIPlatformScreen = function (props) {
      return e(window.__AIPLAT.Overview, props || {});
    };
  }
})();
