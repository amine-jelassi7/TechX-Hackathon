// Motion engine for the owner console. Installed once per browser page (guarded), driven by a
// MutationObserver on Streamlit's DOM. It only reacts to markup the server marked for animation
// (.sc-count) or to rows it has not seen before (live feed), so reruns and fragment refreshes stay quiet.
(() => {
  if (window.__scMotion) return;
  window.__scMotion = true;
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const seenRows = {};
  const scrollPos = {};

  function countUp(el) {
    el.dataset.done = "1";
    const to = parseFloat(el.dataset.to);
    if (reduced() || !isFinite(to)) return;
    const dec = parseInt(el.dataset.dec || "0", 10);
    const group = el.dataset.group === "1";
    const final = el.textContent;
    const fmt = (v) => v.toLocaleString("en-US", {
      minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: group });
    el.textContent = fmt(0);
    const start = performance.now() + parseFloat(el.dataset.delay || "0");
    const dur = 900;
    const tick = (now) => {
      const k = Math.max(0, Math.min(1, (now - start) / dur));
      el.textContent = k < 1 ? fmt(to * easeOut(k)) : final;
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function scanFeed(wrap) {
    wrap._sc = true;
    const id = wrap.dataset.feed;
    // the fragment replaces the whole table every refresh: keep the reader's scroll position
    if (scrollPos[id]) wrap.scrollTop = scrollPos[id];
    wrap.addEventListener("scroll", () => { scrollPos[id] = wrap.scrollTop; }, { passive: true });
    const rows = Array.from(wrap.querySelectorAll("tbody tr[data-id]"));
    const prev = seenRows[id];
    if (prev && !reduced()) {
      rows.forEach((r) => {
        if (prev.has(r.dataset.id)) return;
        r.classList.add("sc-newrow");
        if (r.classList.contains("flag")) r.classList.add("sc-flash");
      });
    }
    const all = prev || new Set();
    rows.forEach((r) => all.add(r.dataset.id));
    seenRows[id] = all;
  }

  let queued = false;
  function scan() {
    queued = false;
    document.querySelectorAll(".sc-count:not([data-done])").forEach(countUp);
    document.querySelectorAll(".sc-tablewrap[data-feed]").forEach((w) => { if (!w._sc) scanFeed(w); });
  }
  const schedule = () => { if (!queued) { queued = true; requestAnimationFrame(scan); } };
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  schedule();

  // approvals: show the decision on the card right away, then collapse it while the server catches up
  document.addEventListener("click", (e) => {
    const btn = e.target.closest ? e.target.closest("button") : null;
    const box = btn ? btn.closest('[class*="st-key-appr_"]') : null;
    if (!box || reduced()) return;
    const label = (btn.textContent || "").trim().toLowerCase();
    if (label !== "approve" && label !== "reject") return;
    const state = label === "approve" ? "Approved" : "Rejected";
    box.dataset.state = state;
    box.classList.add("sc-decided");
    // the rerun can remove the card before any transition finishes, so animate a fixed-position copy
    const r = box.getBoundingClientRect();
    const ghost = box.cloneNode(true);
    ghost.classList.add("sc-ghost");
    Object.assign(ghost.style, { position: "fixed", left: r.left + "px", top: r.top + "px",
      width: r.width + "px", margin: "0", zIndex: "60", pointerEvents: "none" });
    document.body.appendChild(ghost);
    setTimeout(() => ghost.classList.add("sc-ghost-out"), 420);
    setTimeout(() => ghost.remove(), 1000);
    setTimeout(() => box.classList.add("sc-leaving"), 60);
    setTimeout(() => {
      if (box.isConnected) { box.classList.remove("sc-decided", "sc-leaving"); delete box.dataset.state; }
    }, 6000);
  }, true);
})();
