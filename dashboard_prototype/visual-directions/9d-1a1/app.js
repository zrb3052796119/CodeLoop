"use strict";

(() => {
  const variants = ["A", "B", "C"];
  const variantMeta = {
    A: {
      name: "Agent Observatory · recommended",
      title: "Agent Observatory",
    },
    B: {
      name: "Night Shift Code Control Room",
      title: "Night Shift Control Room",
    },
    C: {
      name: "Waku Editorial Minimal",
      title: "Waku Editorial Minimal",
    },
  };
  const views = {
    overview: ["Agent observatory / live workspace", "Overview", "Follow the current execution from model intent to tool boundary and memory context."],
    runs: ["Execution archive / 23 records", "Runs", "Inspect active and completed Agent work without leaving the live workspace."],
    sessions: ["Conversation authority / 4 sessions", "Sessions", "Review the local conversation surfaces that organize MiniCode work."],
    memory: ["Context system / 12 memories", "Memory", "Trace what was retrieved, rendered, and made available to the model."],
    skills: ["Capability catalog / 8 skills", "Skills", "Understand which local capabilities shape the current execution."],
    connections: ["Local integrations / 3 connections", "Connections", "Observe connected tools and their current workspace-local authority."],
    ops: ["Runtime operations / 2 notices", "Ops", "Monitor execution health, latency, and pending operational boundaries."],
    system: ["Local system / healthy", "System", "Review Gateway and runtime status for this local MiniCode workspace."],
  };

  const body = document.body;
  const dock = document.querySelector("#chat-dock");
  const nav = document.querySelector("#primary-navigation");
  const scrim = document.querySelector("[data-overlay-scrim]");
  const toast = document.querySelector("[data-toast]");
  const dockOpenButton = document.querySelector("[data-open-dock]");
  const navOpenButton = document.querySelector("[data-open-nav]");
  let lastDockTrigger = dockOpenButton;
  let lastNavTrigger = navOpenButton;
  let toastTimer = 0;

  function safeVariant(value) {
    const normalized = String(value || "").toUpperCase();
    return variants.includes(normalized) ? normalized : "A";
  }

  function getInitialVariant() {
    const params = new URLSearchParams(window.location.search);
    return safeVariant(params.get("variant"));
  }

  function setVariant(next, announce = true) {
    const variant = safeVariant(next);
    body.dataset.variant = variant;
    document.querySelector("[data-variant-letter]").textContent = variant;
    document.querySelector("[data-variant-name]").textContent = variantMeta[variant].name;
    document.querySelectorAll("[data-variant-button]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.variantButton === variant));
    });
    const url = new URL(window.location.href);
    url.searchParams.set("variant", variant);
    window.history.replaceState({ variant }, "", url);
    document.title = `MiniCode · ${variantMeta[variant].title}`;
    if (announce) {
      showToast(`Direction ${variant}: ${variantMeta[variant].name}`);
    }
  }

  function cycleVariant(offset) {
    const currentIndex = variants.indexOf(body.dataset.variant);
    setVariant(variants[(currentIndex + offset + variants.length) % variants.length]);
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2200);
  }

  function isDockOverlay() {
    return window.matchMedia("(max-width: 1100px)").matches;
  }

  function isNavOverlay() {
    return window.matchMedia("(max-width: 760px)").matches;
  }

  function updateScrim() {
    const overlayVisible =
      (isDockOverlay() && body.classList.contains("dock-is-open")) ||
      (isNavOverlay() && body.classList.contains("nav-is-open"));
    scrim.hidden = !overlayVisible;
  }

  function openDock(trigger = dockOpenButton) {
    lastDockTrigger = trigger;
    body.classList.add("dock-is-open");
    dockOpenButton.setAttribute("aria-expanded", "true");
    updateScrim();
    if (isDockOverlay()) {
      window.requestAnimationFrame(() => dock.querySelector("[data-close-dock]").focus());
    }
  }

  function closeDock({ restoreFocus = true } = {}) {
    body.classList.remove("dock-is-open");
    dockOpenButton.setAttribute("aria-expanded", "false");
    updateScrim();
    if (restoreFocus && lastDockTrigger) {
      lastDockTrigger.focus();
    }
  }

  function openNav(trigger = navOpenButton) {
    lastNavTrigger = trigger;
    body.classList.add("nav-is-open");
    navOpenButton.setAttribute("aria-expanded", "true");
    updateScrim();
    window.requestAnimationFrame(() => nav.querySelector("[data-close-nav]").focus());
  }

  function closeNav({ restoreFocus = true } = {}) {
    body.classList.remove("nav-is-open");
    navOpenButton.setAttribute("aria-expanded", "false");
    updateScrim();
    if (restoreFocus && lastNavTrigger) {
      lastNavTrigger.focus();
    }
  }

  function setView(viewName) {
    const view = views[viewName] || views.overview;
    document.querySelector("[data-view-kicker]").textContent = view[0];
    document.querySelector("[data-view-title]").textContent = view[1];
    document.querySelector("[data-view-deck]").textContent = view[2];
    document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
      const active = button.dataset.view === viewName;
      button.classList.toggle("is-active", active);
      if (active) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    body.dataset.view = viewName;
    showToast(`${view[1]} selected · mock view`);
    if (isNavOverlay()) {
      closeNav({ restoreFocus: false });
      document.querySelector("[data-view-title]").focus?.();
    }
  }

  function decidePermission(decision) {
    const card = document.querySelector("[data-permission]");
    const allowed = decision === "allowed";
    card.dataset.state = decision;
    card.querySelector("[data-permission-title]").textContent = allowed ? "write_file allowed once" : "write_file blocked";
    card.querySelector("[data-permission-copy]").textContent = allowed
      ? "Mock approval recorded in page memory. No file was changed."
      : "Mock denial recorded in page memory. The run remains paused.";
    card.querySelector("[data-allow]").hidden = true;
    card.querySelector("[data-deny]").hidden = true;
    card.querySelector("[data-reset-permission]").hidden = false;
    showToast(allowed ? "Mock permission allowed once" : "Mock permission blocked");
  }

  function resetPermission() {
    const card = document.querySelector("[data-permission]");
    card.dataset.state = "pending";
    card.querySelector("[data-permission-title]").textContent = "Allow write_file?";
    card.querySelector("[data-permission-copy]").textContent =
      "MiniCode wants to update one workspace-local prototype file.";
    card.querySelector("[data-allow]").hidden = false;
    card.querySelector("[data-deny]").hidden = false;
    card.querySelector("[data-reset-permission]").hidden = true;
    showToast("Mock permission reset");
  }

  function selectRun(button) {
    document.querySelectorAll("[data-run]").forEach((row) => {
      const selected = row === button;
      row.classList.toggle("is-selected", selected);
      row.setAttribute("aria-selected", String(selected));
    });
    showToast(`#run-${button.dataset.run} selected`);
  }

  document.querySelectorAll("[data-variant-button]").forEach((button) => {
    button.addEventListener("click", () => setVariant(button.dataset.variantButton));
  });
  document.querySelector("[data-previous-variant]").addEventListener("click", () => cycleVariant(-1));
  document.querySelector("[data-next-variant]").addEventListener("click", () => cycleVariant(1));
  document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.querySelector("[data-view-link]").addEventListener("click", () => setView("runs"));
  document.querySelector("[data-close-dock]").addEventListener("click", () => closeDock());
  dockOpenButton.addEventListener("click", (event) => openDock(event.currentTarget));
  document.querySelector("[data-close-nav]").addEventListener("click", () => closeNav());
  navOpenButton.addEventListener("click", (event) => openNav(event.currentTarget));
  scrim.addEventListener("click", () => {
    if (body.classList.contains("dock-is-open") && isDockOverlay()) {
      closeDock();
    } else if (body.classList.contains("nav-is-open") && isNavOverlay()) {
      closeNav();
    }
  });
  document.querySelector("[data-allow]").addEventListener("click", () => decidePermission("allowed"));
  document.querySelector("[data-deny]").addEventListener("click", () => decidePermission("denied"));
  document.querySelector("[data-reset-permission]").addEventListener("click", resetPermission);
  document.querySelectorAll("[data-run]").forEach((button) => {
    button.addEventListener("click", () => selectRun(button));
  });
  document.querySelector("[data-composer]").addEventListener("submit", (event) => {
    event.preventDefault();
    showToast("Mock composer only · draft preserved");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (body.classList.contains("dock-is-open") && isDockOverlay()) {
        closeDock();
        return;
      }
      if (body.classList.contains("nav-is-open") && isNavOverlay()) {
        closeNav();
      }
      return;
    }
    const target = event.target;
    const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target.isContentEditable;
    if (!editing && event.altKey && event.key === "ArrowLeft") {
      event.preventDefault();
      cycleVariant(-1);
    }
    if (!editing && event.altKey && event.key === "ArrowRight") {
      event.preventDefault();
      cycleVariant(1);
    }
  });

  window.addEventListener("popstate", () => setVariant(getInitialVariant(), false));
  window.addEventListener("resize", updateScrim);

  setVariant(getInitialVariant(), false);
  body.dataset.view = "overview";
  body.classList.add("dock-is-open");
  if (isNavOverlay()) {
    body.classList.remove("nav-is-open");
  }
  updateScrim();
  window.requestAnimationFrame(() => body.classList.add("ui-ready"));
})();
