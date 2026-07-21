(() => {
  "use strict";

  const grid = document.getElementById("server-grid");
  const template = document.getElementById("server-card-template");
  const cards = new Map();
  const connectivityGrid = document.getElementById("connectivity-grid");
  const connectivityTemplate = document.getElementById("connectivity-card-template");
  const connectivityCards = new Map();
  const prevOnline = new Map();
  let selectedHost = null;
  let latestServers = new Map();
  let firstRender = true;

  const bytesFmt = (n) => {
    if (n == null) return "--";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(1)} ${units[i]}`;
  };
  const bpsFmt = (n) => `${bytesFmt(n)}/s`;

  function setText(refs, key, text) {
    if (refs.last[key] === text) return;
    refs.last[key] = text;
    refs.el[key].textContent = text;
  }

  function setHTML(refs, key, html) {
    if (refs.last[key] === html) return;
    refs.last[key] = html;
    refs.el[key].innerHTML = html;
  }

  function setBar(refs, key, pct) {
    if (refs.last[key] === pct) return;
    refs.last[key] = pct;
    const el = refs.el[key];
    el.style.width = `${pct}%`;
    const level = pct >= 90 ? "bad" : pct >= 70 ? "warn" : "";
    const target = level ? `bar-fill ${level}` : "bar-fill";
    if (el.className !== target) el.className = target;
  }

  function drawSparkline(canvas, cpuSeries, memSeries) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const style = getComputedStyle(document.documentElement);
    const gap = 2;
    const bandH = (h - gap) / 2;
    const plot = (series, color, top) => {
      if (series.length < 2) return;
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      const n = series.length;
      series.forEach((v, i) => {
        const x = (i / (n - 1)) * w;
        const y = top + bandH - (Math.min(v, 100) / 100) * bandH;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    };
    plot(memSeries, style.getPropertyValue("--text-2").trim(), 0);
    plot(cpuSeries, style.getPropertyValue("--accent").trim(), bandH + gap);
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
        statusText: el.querySelector(".status-text"),
        cpu: el.querySelector(".cpu-percent"),
        mem: el.querySelector(".mem-percent"),
        disk: el.querySelector(".disk-percent"),
        cpuBar: el.querySelector(".cpu-bar"),
        memBar: el.querySelector(".mem-bar"),
        diskBar: el.querySelector(".disk-bar"),
        temp: el.querySelector(".cpu-temp"),
        power: el.querySelector(".power-value"),
        docker: el.querySelector(".docker-summary"),
        uptime: el.querySelector(".uptime"),
        latency: el.querySelector(".latency"),
        wifiNetwork: el.querySelector(".wifi-network"),
        wifiPing: el.querySelector(".wifi-ping"),
        sparkline: el.querySelector(".sparkline"),
      },
      last: {},
      card: el,
    };
    cards.set(host, refs);
    return refs;
  }

  function setStatusText(el, online, level) {
    if (online) {
      const text = level === "red" ? "CRITICAL" : level === "yellow" ? "WARNING" : "ONLINE";
      el.textContent = text;
      el.className = "status-text";
    } else {
      el.textContent = "OFFLINE";
      el.className = "status-text offline";
    }
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
      setStatusText(refs.el.statusText, false);
      setText(refs, "cpu", "--%");
      setText(refs, "mem", "--%");
      setText(refs, "disk", "--%");
      setBar(refs, "cpuBar", 0);
      setBar(refs, "memBar", 0);
      setBar(refs, "diskBar", 0);
      setText(refs, "temp", "--");
      setText(refs, "power", "--");
      setText(refs, "docker", "--");
      setText(refs, "uptime", "Offline");
      setText(refs, "latency", "");
      setText(refs, "wifiNetwork", "--");
      setText(refs, "wifiPing", "");
      refs.el.sparkline.getContext("2d").clearRect(0, 0, refs.el.sparkline.width, refs.el.sparkline.height);
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

    setStatusText(refs.el.statusText, true, level);

    const cpuPct = s.cpu?.percent ?? 0;
    const memPct = s.mem?.percent ?? 0;
    const diskPct = s.disk?.percent ?? 0;
    setText(refs, "cpu", `${cpuPct}%`);
    setText(refs, "mem", `${memPct}%`);
    setText(refs, "disk", `${diskPct}%`);
    setBar(refs, "cpuBar", cpuPct);
    setBar(refs, "memBar", memPct);
    setBar(refs, "diskBar", diskPct);

    const hist = s.history || { cpu: [], mem: [] };
    drawSparkline(refs.el.sparkline, hist.cpu, hist.mem);

    setText(refs, "temp", s.cpu?.temp != null ? `${s.cpu.temp}°C` : "--");

    const power = s.power || {};
    if (power.available) {
      const pct = power.percent ?? 0;
      const charging = (power.status || "").toLowerCase() === "charging";
      const level = pct <= 20 ? "bat-critical" : pct <= 30 ? "bat-warning" : "";
      const iconClasses = ["bat-icon", charging ? "bat-charging" : "", level].filter(Boolean).join(" ");
      const icon = `<span class="${iconClasses}" style="--bat-lvl:${pct}%"></span>`;
      setHTML(refs, "power",
        `${icon}<span class="bat-pct ${level}">${pct}%</span>`
      );
    } else {
      setText(refs, "power", "--");
    }

    const docker = s.docker || {};
    setText(refs, "docker", docker.available ? `${docker.running}/${docker.running + docker.stopped}` : "--");

    setText(refs, "uptime", s.uptime?.pretty || "--");
    setText(refs, "latency", s.latency_ms != null ? `${s.latency_ms} ms` : "");

    const net = s.network || {};
    setText(refs, "wifiNetwork", net.available ? (net.current_network || "Sin red") : "--");
    setText(refs, "wifiPing", net.available && net.ping_ms != null ? `WiFi: ${net.ping_ms} ms` : "");

    flagAlert(s, level !== "green");

    if (s.host === selectedHost) renderDetail(s);
  }

  function ensureConnectivityCard(deviceId) {
    if (connectivityCards.has(deviceId)) return connectivityCards.get(deviceId);
    const el = connectivityTemplate.content.firstElementChild.cloneNode(true);
    connectivityGrid.appendChild(el);
    const refs = {
      el: {
        name: el.querySelector(".device-name"),
        dot: el.querySelector(".status-dot"),
        statusText: el.querySelector(".status-text"),
        network: el.querySelector(".device-network"),
        rssi: el.querySelector(".device-rssi"),
        linkSpeed: el.querySelector(".device-link-speed"),
        ping: el.querySelector(".device-ping"),
        ip: el.querySelector(".device-ip"),
        connectedSince: el.querySelector(".device-connected-since"),
        lastFailover: el.querySelector(".device-last-failover"),
      },
      last: {},
      card: el,
    };
    connectivityCards.set(deviceId, refs);
    return refs;
  }

  function renderConnectivityDevice(dev) {
    const refs = ensureConnectivityCard(dev.device_id);
    const status = dev.status || (dev.available === false ? "unknown" : "ok");
    const dotClass = status === "ok" ? "online" : status === "stale" || status === "degraded" ? "warn" : "offline";
    setText(refs, "name", dev.name || dev.device_id);
    const dotTarget = `status-dot ${dotClass}`;
    if (refs.last.dot !== dotTarget) {
      refs.el.dot.className = dotTarget;
      refs.last.dot = dotTarget;
    }
    setText(refs, "statusText", status.toUpperCase());
    setText(refs, "network", dev.current_network || dev.network || "--");
    setText(refs, "rssi", dev.rssi != null ? `${dev.rssi} dBm` : "--");
    setText(refs, "linkSpeed", dev.link_speed_mbps != null ? `${dev.link_speed_mbps} Mbps` : "--");
    setText(refs, "ping", dev.ping_ms != null ? `${dev.ping_ms} ms` : "--");
    setText(refs, "ip", dev.ip || "--");
    setText(refs, "connectedSince", dev.connected_since ? new Date(dev.connected_since * 1000).toLocaleTimeString() : "--");
    setText(refs, "lastFailover", dev.last_failover ? `${dev.last_failover.reason || "failover"} (${new Date(dev.last_failover.time * 1000).toLocaleTimeString()})` : "--");
  }

  let lastConnectivityKey = "";
  function renderConnectivity(devices) {
    // Los dispositivos con source "ssh" ya son servidores con su propia card (ver
    // wifi-network/wifi-ping en renderServer) — esta sección de arriba solo muestra
    // dispositivos que reportan por push y no tienen card propia (PC, tablet).
    const list = (devices || []).filter((d) => d.source !== "ssh");
    const card = document.getElementById("connectivity-card");
    card.classList.toggle("hidden", list.length === 0);
    const key = list.map((d) => `${d.device_id}:${d.status}:${d.current_network || d.network}:${d.rssi}:${d.ping_ms}`).join("|");
    if (key === lastConnectivityKey) return;
    lastConnectivityKey = key;
    for (const dev of list) renderConnectivityDevice(dev);
  }

  function renderProcList(ul, procs, suffix) {
    ul.textContent = "";
    for (const p of procs || []) {
      const li = document.createElement("li");
      const nameSpan = document.createElement("span");
      nameSpan.textContent = p.name;
      const valSpan = document.createElement("span");
      valSpan.textContent = `${p.value}${suffix}`;
      li.appendChild(nameSpan);
      li.appendChild(valSpan);
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

    const net = s.network || {};
    document.getElementById("detail-wifi-network").textContent = net.available ? (net.current_network || "Sin red") : "--";
    document.getElementById("detail-wifi-rssi").textContent = net.available && net.rssi != null ? `${net.rssi} dBm` : "--";
    document.getElementById("detail-wifi-linkspeed").textContent = net.available && net.link_speed_mbps != null ? `${net.link_speed_mbps} Mbps` : "--";
    document.getElementById("detail-wifi-ping").textContent = net.available && net.ping_ms != null ? `${net.ping_ms} ms` : "--";
    document.getElementById("detail-wifi-since").textContent = net.available && net.connected_since ? new Date(net.connected_since * 1000).toLocaleTimeString() : "--";
    document.getElementById("detail-wifi-failover").textContent = net.available && net.last_failover
      ? `${net.last_failover.reason || "failover"} (${new Date(net.last_failover.time * 1000).toLocaleTimeString()})`
      : "--";

    const svcGrid = document.getElementById("detail-services");
    svcGrid.innerHTML = "";
    for (const [name, active] of Object.entries(s.services || {})) {
      const chip = document.createElement("span");
      chip.className = `service-chip${active ? "" : " down"}`;
      chip.textContent = name;
      svcGrid.appendChild(chip);
    }

    const containerList = document.getElementById("detail-containers");
    containerList.textContent = "";
    const docker = s.docker || {};
    if (!docker.available) {
      const li = document.createElement("li");
      li.textContent = "No disponible";
      containerList.appendChild(li);
    } else if (!docker.containers.length) {
      const li = document.createElement("li");
      li.textContent = "Sin contenedores";
      containerList.appendChild(li);
    } else {
      for (const c of docker.containers) {
        const li = document.createElement("li");
        const nameSpan = document.createElement("span");
        nameSpan.textContent = c.name;
        const valSpan = document.createElement("span");
        valSpan.textContent = c.status;
        if (c.state !== "running") valSpan.className = "down";
        li.appendChild(nameSpan);
        li.appendChild(valSpan);
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
  let _alertCtx = null;
  function playAlert() {
    const now = Date.now();
    if (now - lastAlertAt < 15000) return;
    lastAlertAt = now;
    try {
      if (!_alertCtx) _alertCtx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = _alertCtx.createOscillator();
      const gain = _alertCtx.createGain();
      osc.connect(gain);
      gain.connect(_alertCtx.destination);
      osc.frequency.value = 800;
      gain.gain.setValueAtTime(0.15, _alertCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, _alertCtx.currentTime + 0.3);
      osc.start(_alertCtx.currentTime);
      osc.stop(_alertCtx.currentTime + 0.3);
    } catch (_) { /* Web Audio no disponible */ }
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
    ul.textContent = "";
    for (const ev of events) {
      const li = document.createElement("li");
      const kind = ev.kind.includes("down") ? "kind-down" : ev.kind.includes("up") ? "kind-up" : "";
      li.className = kind;
      const time = new Date(ev.time * 1000).toLocaleTimeString();
      const timeSpan = document.createElement("span");
      timeSpan.className = "ev-time";
      timeSpan.textContent = time;
      const msgSpan = document.createElement("span");
      msgSpan.textContent = ev.message;
      li.appendChild(timeSpan);
      li.appendChild(msgSpan);
      ul.appendChild(li);
    }
  }

  let lastAlertsKey = "";
  function renderAlerts(alerts) {
    const key = alerts.map((a) => a.id + a.status).join(",");
    if (key === lastAlertsKey) return;
    lastAlertsKey = key;

    const active = alerts.filter((a) => a.status === "active");
    const badge = document.getElementById("alert-badge");
    badge.textContent = active.length;
    badge.classList.toggle("hidden", active.length === 0);

    const list = document.getElementById("alert-list");
    list.textContent = "";
    if (!active.length) {
      const li = document.createElement("li");
      li.className = "alert-empty";
      li.textContent = "Sin alertas activas";
      list.appendChild(li);
      return;
    }
    for (const a of active) {
      const li = document.createElement("li");
      const top = document.createElement("div");
      top.className = "alert-row-top";
      const label = document.createElement("span");
      label.textContent = `${a.server}: ${a.title}`;
      const sev = document.createElement("span");
      sev.className = `alert-sev alert-sev-${a.severity.toLowerCase()}`;
      sev.textContent = a.severity;
      top.appendChild(label);
      top.appendChild(sev);
      const desc = document.createElement("div");
      desc.className = "alert-desc";
      desc.textContent = a.description;
      li.appendChild(top);
      li.appendChild(desc);
      list.appendChild(li);
    }
  }

  function render(data) {
    if (firstRender) {
      firstRender = false;
      grid.querySelectorAll(".server-card.skeleton").forEach((el) => el.remove());
    }
    renderSummary(data);
    renderInternet(data.internet);
    renderConnectivity(data.connectivity);
    renderEvents(data.events);
    renderAlerts(data.alerts || []);
    const count = data.servers ? data.servers.length : 0;
    grid.classList.remove(
      "servers-1", "servers-2", "servers-3",
      "servers-4", "servers-5", "servers-6"
    );
    if (count >= 1 && count <= 6) grid.classList.add(`servers-${count}`);
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
      } catch (_) {
        if (!startFallback._log || Date.now() - startFallback._log > 30000) {
          startFallback._log = Date.now();
          console.warn("Polling fallback: servidor no alcanzable, reintentando...");
        }
      }
    }, 2000);
  }

  function stopFallback() {
    usingFallback = false;
    clearInterval(fallbackTimer);
  }

  connectWS();

  // --- Alert bell panel ---
  const alertBell = document.getElementById("alert-bell");
  const alertPanel = document.getElementById("alert-panel");
  alertBell.addEventListener("click", (ev) => {
    ev.stopPropagation();
    alertPanel.classList.toggle("hidden");
  });
  document.addEventListener("click", (ev) => {
    if (!alertPanel.classList.contains("hidden") && !alertPanel.contains(ev.target)) {
      alertPanel.classList.add("hidden");
    }
  });

  // --- Detail panel open/close ---
  document.getElementById("detail-close").addEventListener("click", closeDetail);
  document.getElementById("detail-overlay").addEventListener("click", (ev) => {
    if (ev.target.id === "detail-overlay") closeDetail();
  });

  // --- Events log collapse ---
  const logCard = document.getElementById("log-card");
  document.getElementById("log-toggle").addEventListener("click", () => {
    logCard.classList.toggle("collapsed");
  });
  logCard.classList.add("collapsed");

  // --- Clock ---
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

  // --- Orientation detection ---
  const rotateOverlay = document.createElement("div");
  rotateOverlay.id = "rotate-overlay";
  rotateOverlay.innerHTML = "&#x21BA; Gire la tablet";
  document.body.appendChild(rotateOverlay);

  function checkOrientation() {
    const isLandscape = window.innerWidth > window.innerHeight;
    rotateOverlay.classList.toggle("visible", !isLandscape);
  }
  checkOrientation();
  window.addEventListener("resize", checkOrientation);
})();
