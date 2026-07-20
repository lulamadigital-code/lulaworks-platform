/* Manager-web progressive enhancement. Everything here is optional polish —
   the pages work with JavaScript disabled (the back-to-top control is a plain
   #top anchor; this only fades it in once there is somewhere to scroll back
   from). Vendored and served by WhiteNoise, consistent with htmx. */
(function () {
  "use strict";

  var toTop = document.querySelector(".totop");
  if (!toTop) return;

  var THRESHOLD = 400;      // px scrolled before scrolling back is a chore
  var scrollable = 0;       // cached so the scroll handler never measures layout

  function sync() {
    var scrolled = window.pageYOffset || document.documentElement.scrollTop || 0;
    // Deliberately no requestAnimationFrame: rAF is throttled in background and
    // headless tabs, which leaves the control stuck hidden. The work here is a
    // class toggle driving opacity/transform only — compositor-cheap, so running
    // it per scroll event costs nothing measurable.
    toTop.classList.toggle("on", scrolled > THRESHOLD && scrollable > THRESHOLD);
  }

  function measure() {
    scrollable = document.documentElement.scrollHeight - window.innerHeight;
    sync();
  }

  window.addEventListener("scroll", sync, { passive: true });
  window.addEventListener("resize", measure, { passive: true });
  window.addEventListener("load", measure);
  // A page can grow after htmx swaps a partial in.
  document.addEventListener("htmx:afterSwap", measure);
  measure();
})();
