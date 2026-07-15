(() => {
  "use strict";

  const grid = document.getElementById("server-grid");
  const template = document.getElementById("server-card-template");
  const cards = new Map(); // host -> { el, refs, last: {} }
  const prevOnline = new Map();
  let selectedHost = null;
  let latestServers = new Map(); // host -> server data, for the open detail panel

  const bytesFmt = (n) => {
    if (n == null) return "--";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(1)} ${units[i]}`;
  };
  const bpsFmt = (n) => `${bytesFmt(n)}/s`;

  // Only touch the DOM when a value actually changed - avoids reflow/repaint
  // on every poll cycle for numbers that didn't move.
  function setText(refs, key, text) {
    if (refs.last[key] === text) return;
    refs.last[key] = text;
    refs.el[key].textContent = text;
  }

  function statusLevel(s) {
    if (!s.online) return "red";
    const pct = [s.cpu?.percent, s.mem?.percent, s.disk?.percent].filter((v) => v != null);
    if (pct.some((v) => v >= 90)) return "red";
    if (pct.some((v) => v >= 70)) return "yellow";
    return "green";
  }

  function ensureCard(host) {
    if (cards.has(host)) return cards.get(host);
    const el = template.content.firstElementChild.cloneNode(true);
    el.dataset.host = host;
    el.addEventListener("click", () => openDetail(host));
    grid.appendChild(el);

    const refs = {
      el: {
        name: el.querySelector(".server-name"),
        dot: el.querySelector(".status-dot"),
        cpu: el.querySelector(".cpu-percent"),
        mem: el.querySelector(".mem-percent"),
        disk: el.querySelector(".disk-percent"),
        temp: el.querySelector(".cpu-temp"),
        power: el.querySelector(".power-value"),
        docker: el.querySelector(".docker-summary"),
        uptime: el.querySelector(".uptime"),
        latency: el.querySelector(".latency"),
      },
      last: {},
      card: el,
    };
    cards.set(host, refs);
    return refs;
  }

  function renderServer(s) {
    latestServers.set(s.host, s);
    const refs = ensureCard(s.host);
    const level = statusLevel(s);
    refs.card.classList.toggle("offline", !s.online);
    refs.card.classList.toggle("selected", s.host === selectedHost);
    if (refs.last.level !== level) {
      refs.card.classList.remove("lvl-green", "lvl-yellow", "lvl-red");
      refs.card.classList.add(`lvl-${level}`);
      refs.last.level = level;
    }

    setText(refs, "name", s.name);

    if (!s.online) {
      refs.el.dot.className = "status-dot offline";
      setText(refs, "cpu", "--%");
      setText(refs, "mem", "--%");
      setText(refs, "disk", "--%");
      setText(refs, "temp", "--");
      setText(refs, "power", "--");
      setText(refs, "docker", "--");
      setText(refs, "uptime", "Offline");
      setText(refs, "latency", "");
      flagAlert(s, false);
      if (s.host === selectedHost) renderDetail(s);
      return;
    }

    const dotClass = level === "red" ? "warn" : level === "yellow" ? "warn" : "online";
    const dotTarget = `status-dot ${dotClass}`;
    if (refs.last.dot !== dotTarget) {
      refs.el.dot.className = dotTarget;
      refs.last.dot = dotTarget;
    }

    setText(refs, "cpu", `${s.cpu?.percent ?? 0}%`);
    setText(refs, "mem", `${s.mem?.percent ?? 0}%`);
    setText(refs, "disk", `${s.disk?.percent ?? 0}%`);
    setText(refs, "temp", s.cpu?.temp != null ? `${s.cpu.temp}°C` : "--");

    const power = s.power || {};
    setText(refs, "power", power.available ? `${power.percent ?? "--"}%` : "--");

    const docker = s.docker || {};
    setText(refs, "docker", docker.available ? `${docker.running}/${docker.running + docker.stopped}` : "--");

    setText(refs, "uptime", s.uptime?.pretty || "--");
    setText(refs, "latency", s.latency_ms != null ? `${s.latency_ms} ms` : "");

    flagAlert(s, level !== "green");

    if (s.host === selectedHost) renderDetail(s);
  }

  function renderProcList(ul, procs, suffix) {
    ul.innerHTML = "";
    for (const p of procs || []) {
      const li = document.createElement("li");
      li.innerHTML = `<span>${p.name}</span><span>${p.value}${suffix}</span>`;
      ul.appendChild(li);
    }
  }

  function openDetail(host) {
    selectedHost = host;
    document.getElementById("detail-overlay").classList.remove("hidden");
    const s = latestServers.get(host);
    if (s) renderDetail(s);
    for (const [h, refs] of cards) refs.card.classList.toggle("selected", h === host);
  }

  function closeDetail() {
    selectedHost = null;
    document.getElementById("detail-overlay").classList.add("hidden");
    for (const refs of cards.values()) refs.card.classList.remove("selected");
  }

  function renderDetail(s) {
    document.getElementById("detail-name").textContent = s.name;
    if (!s.online) return;
    document.getElementById("detail-uptime").textContent = s.uptime?.pretty || "--";
    document.getElementById("detail-load").textContent =
      `${s.load?.load1 ?? "--"} / ${s.load?.load5 ?? "--"} / ${s.load?.load15 ?? "--"}`;
    document.getElementById("detail-latency").textContent = s.latency_ms != null ? `${s.latency_ms} ms` : "--";
    document.getElementById("detail-ip").textContent = s.net?.ip || "--";
    document.getElementById("detail-net").textContent = `${bpsFmt(s.net?.download_bps)} / ${bpsFmt(s.net?.upload_bps)}`;
    document.getElementById("detail-traffic").textContent =
      `↓ ${bytesFmt(s.net?.daily_download_bytes)} ↑ ${bytesFmt(s.net?.daily_upload_bytes)}`;
    document.getElementById("detail-mem").textContent = `${bytesFmt(s.mem?.used)} / ${bytesFmt(s.mem?.total)}`;
    const swap = s.mem?.swap;
    document.getElementById("detail-swap").textContent =
      swap && swap.total ? `${bytesFmt(swap.used)} / ${bytesFmt(swap.total)} (${swap.percent}%)` : "sin uso";
    document.getElementById("detail-disk").textContent = `${bytesFmt(s.disk?.used)} / ${bytesFmt(s.disk?.total)}`;
    document.getElementById("detail-docker-disk").textContent = s.docker_disk
      ? bytesFmt(s.docker_disk.total_bytes)
      : "--";
    document.getElementById("detail-updates").textContent =
      s.updates_pending > 0 ? `${s.updates_pending} pendientes` : "Al día";
    document.getElementById("detail-temp-cores").textContent =
      s.cpu?.temp_per_core?.length ? s.cpu.temp_per_core.map((t) => `${t}°C`).join(" · ") : "--";

    const svcGrid = document.getElementById("detail-services");
    svcGrid.innerHTML = "";
    for (const [name, active] of Object.entries(s.services || {})) {
      const chip = document.createElement("span");
      chip.className = `service-chip${active ? "" : " down"}`;
      chip.textContent = name;
      svcGrid.appendChild(chip);
    }

    const containerList = document.getElementById("detail-containers");
    containerList.innerHTML = "";
    const docker = s.docker || {};
    if (!docker.available) {
      containerList.innerHTML = "<li>No disponible</li>";
    } else if (!docker.containers.length) {
      containerList.innerHTML = "<li>Sin contenedores</li>";
    } else {
      for (const c of docker.containers) {
        const li = document.createElement("li");
        li.innerHTML = `<span>${c.name}</span><span class="${c.state === "running" ? "" : "down"}">${c.status}</span>`;
        containerList.appendChild(li);
      }
    }

    renderProcList(document.getElementById("detail-top-cpu"), s.top_cpu, "%");
    renderProcList(document.getElementById("detail-top-mem"), s.top_mem, "%");
  }

  function flagAlert(s, warn) {
    const was = prevOnline.get(s.host);
    prevOnline.set(s.host, s.online);
    if (was === false && s.online) return;
    if (!s.online || warn) playAlert();
  }

  let lastAlertAt = 0;
  function playAlert() {
    const now = Date.now();
    if (now - lastAlertAt < 15000) return;
    lastAlertAt = now;
    const sound = document.getElementById("alert-sound");
    sound?.play().catch(() => {});
  }

  let lastSummary = "";
  function renderSummary(data) {
    const text = `${data.summary.online}|${data.summary.offline}`;
    if (text === lastSummary) return;
    lastSummary = text;
    document.getElementById("pill-online").textContent = `${data.summary.online} online`;
    document.getElementById("pill-offline").textContent = `${data.summary.offline} offline`;
  }

  let lastInternet = null;
  function renderInternet(net) {
    if (lastInternet === net.online) return;
    lastInternet = net.online;
    const dot = document.getElementById("internet-dot");
    dot.className = `status-dot ${net.online ? "online" : "offline"}`;
    dot.title = net.online
      ? `Internet OK (google ${net.google_ms ?? "--"}ms, cloudflare ${net.cloudflare_ms ?? "--"}ms)`
      : "Sin conexión a internet";

    const banner = document.getElementById("alert-banner");
    if (!net.online) {
      banner.textContent = "⚠ Sin conexión a internet";
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  let lastEventsKey = "";
  function renderEvents(events) {
    const key = events.length ? events[0].time + events[0].kind : "";
    if (key === lastEventsKey) return;
    lastEventsKey = key;
    const ul = document.getElementById("event-log");
    ul.innerHTML = "";
    for (const ev of events) {
      const li = document.createElement("li");
      const kind = ev.kind.includes("down") ? "kind-down" : ev.kind.includes("up") ? "kind-up" : "";
      li.className = kind;
      const time = new Date(ev.time * 1000).toLocaleTimeString();
      li.innerHTML = `<span class="ev-time">${time}</span><span>${ev.message}</span>`;
      ul.appendChild(li);
    }
  }

  function render(data) {
    renderSummary(data);
    renderInternet(data.internet);
    renderEvents(data.events);
    for (const s of data.servers) renderServer(s);
  }

  // --- WebSocket with polling fallback ---
  let ws;
  let usingFallback = false;
  let fallbackTimer;

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onmessage = (ev) => render(JSON.parse(ev.data));
    ws.onclose = () => {
      if (!usingFallback) startFallback();
      setTimeout(connectWS, 3000);
    };
    ws.onerror = () => ws.close();
    ws.onopen = () => stopFallback();
  }

  function startFallback() {
    usingFallback = true;
    fallbackTimer = setInterval(async () => {
      try {
        const res = await fetch("/api/status");
        render(await res.json());
      } catch (_) { /* server unreachable, keep retrying */ }
    }, 2000);
  }

  function stopFallback() {
    usingFallback = false;
    clearInterval(fallbackTimer);
  }

  connectWS();

  // --- Detail panel open/close ---
  document.getElementById("detail-close").addEventListener("click", closeDetail);
  document.getElementById("detail-overlay").addEventListener("click", (ev) => {
    if (ev.target.id === "detail-overlay") closeDetail();
  });

  // --- Events log collapse (saves vertical space by default) ---
  const logCard = document.getElementById("log-card");
  document.getElementById("log-toggle").addEventListener("click", () => {
    logCard.classList.toggle("collapsed");
  });
  logCard.classList.add("collapsed");

  // --- Clock (no need for a full date line, saves a text node + width) ---
  function tickClock() {
    document.getElementById("clock").textContent = new Date().toLocaleTimeString();
  }
  tickClock();
  setInterval(tickClock, 1000);

  // --- Theme toggle ---
  const themeBtn = document.getElementById("theme-toggle");
  const savedTheme = localStorage.getItem("dashboard-theme") || "dark";
  document.documentElement.dataset.theme = savedTheme;
  themeBtn.textContent = savedTheme === "dark" ? "🌙" : "☀️";
  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("dashboard-theme", next);
    themeBtn.textContent = next === "dark" ? "🌙" : "☀️";
  });

  // --- Fullscreen ---
  document.getElementById("fullscreen-toggle").addEventListener("click", () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.();
    }
  });
})();
