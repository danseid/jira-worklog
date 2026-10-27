// Small glue for the dialog, the filter, the quick buttons and "Add ticket". htmx does the requests.
(function () {
  "use strict";
  const dialog = () => document.getElementById("log-dialog");
  let returnTo = null;

  // Remember the element that opened the dialog, so focus can go back to it.
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-opens-dialog]");
    if (opener) returnTo = opener.dataset.returnTo || opener.id;

    if (e.target.closest("[data-close]")) dialog().close();

    const quick = e.target.closest("[data-quick]");
    if (quick) {
      const time = document.getElementById("dlg-time");
      time.value = quick.dataset.quick;
      time.dispatchEvent(new Event("input", { bubbles: true }));
      time.focus();
    }
  });

  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target.id !== "log-dialog") return;
    const d = dialog();
    if (!d.open) d.showModal();
    document.getElementById("dlg-time").focus();
  });

  document.addEventListener("htmx:beforeRequest", (e) => {
    if (e.detail.elt.id === "log-form") document.getElementById("dlg-error").replaceChildren();
  });

  // Esc, the X button and Cancel all end here. The close event comes later than close(),
  // so a new dialog can be open already. Then leave the focus where it is.
  document.addEventListener("DOMContentLoaded", () => {
    dialog().addEventListener("close", () => {
      if (dialog().open) return;
      const target = returnTo && document.getElementById(returnTo);
      if (target) target.focus();
    });
  });

  // Sent by the server after a successful save, when the new grid is in place.
  document.body.addEventListener("worklogAdded", (e) => {
    returnTo = e.detail.cell;
    dialog().close();
    applyFilter();
    const cell = document.getElementById(e.detail.cell);
    if (cell) setTimeout(() => cell.classList.remove("flash"), 4000);
  });

  // Filter rows by key or summary.
  function applyFilter() {
    const input = document.getElementById("wl-filter");
    const text = input ? input.value.trim().toLowerCase() : "";
    document.querySelectorAll("#grid-area tr[data-filter]").forEach((row) => {
      row.hidden = text !== "" && !row.dataset.filter.includes(text);
    });
  }
  document.addEventListener("input", (e) => {
    if (e.target.id === "wl-filter") applyFilter();
  });

  // "+ Add ticket" opens a search field. Esc closes it.
  document.addEventListener("click", (e) => {
    const button = e.target.closest("#add-ticket");
    if (!button) return;
    const box = document.getElementById("add-box");
    box.hidden = !box.hidden;
    button.setAttribute("aria-expanded", String(!box.hidden));
    if (!box.hidden) document.getElementById("add-query").focus();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.target.id !== "add-query") return;
    document.getElementById("add-box").hidden = true;
    const button = document.getElementById("add-ticket");
    button.setAttribute("aria-expanded", "false");
    button.focus();
  });
})();
