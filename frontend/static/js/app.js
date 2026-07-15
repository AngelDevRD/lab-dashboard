(() => {
  "use strict";

  const grid = document.getElementById("server-grid");
  const template = document.getElementById("server-card-template");
  const cardEls = new Map();
  const prevOnline = new Map();

  const bytesFmt = (n) => {
    if (n == null) return "--";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(1)} ${units[i]}`;
  };
  const bpsFmt = (n) => `${bytesFmt(n)}/s`;

  const barClass = (pct) => (pct >= 85 ? "high" : pct >= 60 ? "mid" : "");

  function ensureCard(host) {
    if (cardEls.has(host)) return cardEls.get(host);
    const node = template.content.firstElementChild.cloneNode(true);
    node.dataset.host = host;
    grid.appendChild(node);
    cardEls.set(host, node);
    return node;
  }

  function renderServer(s) {
    const card = ensureCard(s.host);
    card.classList.toggle("offline", !s.online);

    const dot = card.querySelector(".status-dot");
    card.querySelector(".server-name").textContent = s.name;

    if (!s.online) {
      dot.className = "status-dot offline";
      card.querySelector(".uptime").textContent = "Offline";
      card.querySelector(".last-update").textContent = new Date(s.last_update * 1000).toLocaleTimeString();
      flagAlert(s, false);
      return;
    }

    let warn = false;
    for (const active of Object.values(s.services || {})) {
      if (!active) warn = true;
    }
    dot.className = warn ? "status-dot warn" : "status-dot online";
    card.classList.toggle("warn", warn);

    card.querySelector(".uptime").textContent = `Uptime: ${s.uptime?.pretty || "--"}`;
    card.querySelector(".last-update").textContent = `Actualizado: ${new Date(s.last_update * 1000).toLocaleTimeString()}`;

    const cpuPct = s.cpu?.percent ?? 0;
    card.querySelector(".cpu-percent").textContent = `${cpuPct}%`;
    const cpuBar = card.querySelector(".cpu-bar");
    cpuBar.style.width = `${cpuPct}%`;
    cpuBar.className = `bar-fill cpu-bar ${barClass(cpuPct)}`;
    card.querySelector(".cpu-temp").textContent = `Temp: ${s.cpu?.temp != null ? s.cpu.temp + "°C" : "No disponible"}`;
    card.querySelector(".cpu-load").textContent = `Load: ${s.load?.load1 ?? "--"}`;

    const memPct = s.mem?.percent ?? 0;
    card.querySelector(".mem-percent").textContent = `${memPct}%`;
    const memBar = card.querySelector(".mem-bar");
    memBar.style.width = `${memPct}%`;
    memBar.className = `bar-fill mem-bar ${barClass(memPct)}`;
    card.querySelector(".mem-detail").textContent = `${bytesFmt(s.mem?.used)} / ${bytesFmt(s.mem?.total)}`;
    const swap = s.mem?.swap;
    card.querySelector(".swap-detail").textContent = swap && swap.total
      ? `Swap: ${bytesFmt(swap.used)} / ${bytesFmt(swap.total)} (${swap.percent}%)`
      : "Swap: sin uso";

    const diskPct = s.disk?.percent ?? 0;
    card.querySelector(".disk-percent").textContent = `${diskPct}%`;
    const diskBar = card.querySelector(".disk-bar");
    diskBar.style.width = `${diskPct}%`;
    diskBar.className = `bar-fill disk-bar ${barClass(diskPct)}`;
    card.querySelector(".disk-detail").textContent = `${bytesFmt(s.disk?.used)} / ${bytesFmt(s.disk?.total)}`;

    card.querySelector(".net-ip").textContent = s.net?.ip || "--";
    card.querySelector(".net-down").textContent = bpsFmt(s.net?.download_bps);
    card.querySelector(".net-up").textContent = bpsFmt(s.net?.upload_bps);
    card.querySelector(".daily-traffic").textContent =
      `Hoy: ↓ ${bytesFmt(s.net?.daily_download_bytes)} ↑ ${bytesFmt(s.net?.daily_upload_bytes)}`;

    const docker = s.docker || {};
    card.querySelector(".docker-summary").textContent = docker.available
      ? `${docker.running} corriendo / ${docker.stopped} detenidos`
      : "No disponible";
    card.querySelector(".docker-disk").textContent = s.docker_disk
      ? `${bytesFmt(s.docker_disk.total_bytes)} en disco`
      : "";

    card.querySelector(".updates-pending").textContent =
      s.updates_pending > 0 ? `${s.updates_pending} pendientes` : "Al día";

    const svcGrid = card.querySelector(".services-grid");
    svcGrid.innerHTML = "";
    for (const [name, active] of Object.entries(s.services || {})) {
      const chip = document.createElement("span");
      chip.className = `service-chip${active ? "" : " down"}`;
      chip.textContent = name;
      svcGrid.appendChild(chip);
    }

    const power = s.power || {};
    card.querySelector(".power-value").textContent = power.available
      ? `${power.percent ?? "--"}% · ${power.status || "--"}${power.voltage ? " · " + power.voltage + "V" : ""}`
      : "No disponible";

    renderProcList(card.querySelector(".top-cpu-list"), s.top_cpu, "%");
    renderProcList(card.querySelector(".top-mem-list"), s.top_mem, "%");

    flagAlert(s, warn);
  }

  function renderProcList(ul, procs, suffix) {
    ul.innerHTML = "";
    for (const p of procs || []) {
      const li = document.createElement("li");
      li.innerHTML = `<span>${p.name}</span><span>${p.value}${suffix}</span>`;
      ul.appendChild(li);
    }
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

  function renderSummary(data) {
    document.getElementById("pill-online").textContent = `${data.summary.online} online`;
    document.getElementById("pill-offline").textContent = `${data.summary.offline} offline`;
  }

  function renderInternet(net) {
    const card = document.getElementById("internet-card");
    const dot = document.getElementById("internet-dot");
    card.classList.toggle("down", !net.online);
    dot.className = `status-dot ${net.online ? "online" : "offline"}`;
    document.getElementById("ping-google").textContent = net.google_ms != null ? `${net.google_ms} ms` : "Sin respuesta";
    document.getElementById("ping-cf").textContent = net.cloudflare_ms != null ? `${net.cloudflare_ms} ms` : "Sin respuesta";
    document.getElementById("internet-state").textContent = net.online ? "Conectado" : "Sin conexión";
    document.getElementById("last-outage").textContent = net.last_outage
      ? new Date(net.last_outage * 1000).toLocaleString()
      : "Sin registros";

    const banner = document.getElementById("alert-banner");
    if (!net.online) {
      banner.textContent = "⚠ Sin conexión a internet";
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  }

  function renderEvents(events) {
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

  // --- Clock ---
  function tickClock() {
    const now = new Date();
    document.getElementById("clock").textContent = now.toLocaleTimeString();
    document.getElementById("date").textContent = now.toLocaleDateString(undefined, {
      weekday: "short", year: "numeric", month: "short", day: "numeric",
    });
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
