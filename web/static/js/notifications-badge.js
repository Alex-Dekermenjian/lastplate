/* LP-41: the bell's unread count. Same-origin session requests; JWTs stay on the server. */
(function () {
  "use strict";
  var badge = document.querySelector("[data-notifications-badge]");
  if (!badge) return;
  var pending = false;
  function show(count) {
    var link = badge.closest("a");
    if (count > 0) {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = false;
      if (link) link.setAttribute("aria-label", "Alerts, " + count + " unread");
    } else {
      badge.hidden = true;
      if (link) link.removeAttribute("aria-label");
    }
  }
  async function refresh() {
    if (pending) return;
    pending = true;
    try {
      var response = await fetch(badge.dataset.url, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok || response.redirected) throw new Error("Unavailable");
      var data = await response.json();
      if (!Number.isInteger(data.unread) || data.unread < 0) throw new Error("Invalid count");
      show(data.unread);
    } catch (error) {
      /* Keep whatever was shown; the next check retries. */
    } finally {
      pending = false;
    }
  }
  refresh();
  setInterval(refresh, 30000);
})();
