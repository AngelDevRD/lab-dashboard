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
  // The server broadcasts every BROADCAST_INTERVAL (~2s). A WS can go "half-open"
  // (wifi drop, device sleep, NAT rebind) without ever firing onclose/onerror,
  // since nothing here sends TCP keepalives or WS ping/pong. A watchdog that
  // forces a reconnect when no message has arrived in a while is what actually
  // detects that case client-side.
  const WS_STALE_MS = 10000;
  // Neither the WS nor the fallback poller has produced a fresh update in
  // this long -> the backend itself is unreachable (process crashed, host
  // down, network to it gone), not just this one WS. Show a blocking notice.
  const SERVER_DOWN_MS = 15000;
  let ws;
  let usingFallback = false;
  let fallbackTimer;
  let lastMessageAt = 0;
  let watchdogTimer;
  let serverDownShown = false;

  function log(msg) {
    console.log(`[dashboard ${new Date().toISOString()}] ${msg}`);
  }

  function setServerDown(down) {
    if (down === serverDownShown) return;
    serverDownShown = down;
    document.getElementById("server-down-overlay").classList.toggle("hidden", !down);
    log(down ? "Server unreachable — showing down overlay" : "Server reachable again — hiding down overlay");
  }

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    if (!lastMessageAt) lastMessageAt = Date.now(); // starts the down-overlay clock even if this first attempt never connects
    log("WS connecting...");
    ws.onmessage = (ev) => {
      lastMessageAt = Date.now();
      setServerDown(false);
      try {
        render(JSON.parse(ev.data));
      } catch (err) {
        console.error("render() failed on WS message:", err);
      }
    };
    ws.onclose = () => {
      log("WS closed, scheduling reconnect in 3s");
      if (!usingFallback) startFallback();
      setTimeout(connectWS, 3000);
    };
    ws.onerror = () => ws.close();
    ws.onopen = () => {
      log("WS connected");
      lastMessageAt = Date.now();
      stopFallback();
    };
  }

  function startWatchdog() {
    if (watchdogTimer) return;
    watchdogTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastMessageAt > WS_STALE_MS) {
        log(`WS stale (no message in ${WS_STALE_MS}ms), forcing reconnect`);
        ws.close();
      }
      setServerDown(Date.now() - lastMessageAt > SERVER_DOWN_MS);
    }, 3000);
  }

  let fallbackInFlight = false;
  function startFallback() {
    usingFallback = true;
    fallbackTimer = setInterval(async () => {
      if (fallbackInFlight) return; // avoid piling up overlapping requests
      fallbackInFlight = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const res = await fetch("/api/status", { signal: controller.signal });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        lastMessageAt = Date.now();
        setServerDown(false);
        render(data);
      } catch (err) {
        if (!startFallback._log || Date.now() - startFallback._log > 30000) {
          startFallback._log = Date.now();
          console.warn("Polling fallback: servidor no alcanzable, reintentando...", err);
        }
      } finally {
        clearTimeout(timeout);
        fallbackInFlight = false;
      }
    }, 2000);
  }

  function stopFallback() {
    usingFallback = false;
    clearInterval(fallbackTimer);
  }

  connectWS();
  startWatchdog();

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

  // --- View navigation (Servidores <-> Métricas) ---
  const viewServers = document.getElementById("view-servers");
  const viewMetrics = document.getElementById("view-metrics");
  let currentView = "servers";

  function showView(name) {
    currentView = name;
    document.body.dataset.view = name;
    viewServers.classList.toggle("hidden", name !== "servers");
    viewMetrics.classList.toggle("hidden", name !== "metrics");
    if (name === "metrics") renderClaudeUsage();
  }
  document.getElementById("nav-metrics-btn").addEventListener("click", () => showView("metrics"));
  document.getElementById("nav-servers-btn").addEventListener("click", () => showView("servers"));
  showView("servers");

  // --- Claude usage (ccusage) ---
  const costFmt = (n) => `$${(n ?? 0).toFixed(2)}`;
  const tokensFmt = (n) => {
    if (n == null) return "--";
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
    return `${n}`;
  };

  function roundRectPath(ctx, x, y, w, h, r) {
    const rr = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  // Rounds an axis max up to a "nice" number (1/2/5 * 10^n) so gridline
  // labels read as $20/$50 instead of $23.87.
  function niceCeil(value) {
    if (value <= 0) return 1;
    const exp = Math.floor(Math.log10(value));
    const base = Math.pow(10, exp);
    const residual = value / base;
    const niceResidual = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
    return niceResidual * base;
  }

  function drawDailyCostChart(days) {
    const canvas = document.getElementById("cu-daily-chart");
    // Canvas has a fixed internal pixel buffer that CSS then stretches to fill
    // the card — if that buffer is smaller than the on-screen size the bars
    // come out blurry/blocky. Size the buffer to the actual displayed size
    // (times devicePixelRatio) so it renders crisp on any screen.
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 600;
    const cssH = canvas.clientHeight || 140;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const w = cssW;
    const h = cssH;
    ctx.clearRect(0, 0, w, h);
    if (!days.length) return;

    const style = getComputedStyle(document.documentElement);
    const accent = style.getPropertyValue("--accent").trim();
    const gridColor = style.getPropertyValue("--panel-border").trim();
    const textColor = style.getPropertyValue("--text-2").trim();

    const leftPad = 40; // room for $ axis labels
    const labelH = 18; // room for date labels
    const topPad = 16; // headroom above the tallest bar
    const plotW = w - leftPad;
    const plotH = h - labelH - topPad;
    const plotBottom = topPad + plotH;

    const rawMax = Math.max(...days.map((d) => d.totalCost), 0.01);
    const axisMax = niceCeil(rawMax * 1.05);

    // Horizontal gridlines with $ labels — without these, a flat block of
    // bars has no reference scale and reads as an ugly, meaningless shape.
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textColor;
    ctx.font = "9px sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 1;
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const y = plotBottom - (plotH * i) / steps;
      ctx.beginPath();
      ctx.moveTo(leftPad, y + 0.5);
      ctx.lineTo(w, y + 0.5);
      ctx.stroke();
      ctx.fillText(`$${Math.round((axisMax * i) / steps)}`, leftPad - 6, y);
    }

    // Cap how wide a single bar can get and center the group — otherwise a
    // single bar (e.g. the "1d" filter) stretches across the whole canvas
    // and looks like a solid block instead of a chart.
    const maxBarW = 44;
    const naturalW = plotW / days.length;
    const barW = Math.min(naturalW, maxBarW);
    const gap = barW > 12 ? Math.min(6, barW * 0.2) : barW > 4 ? 2 : 0.5;
    const startX = leftPad + Math.max(0, (plotW - barW * days.length) / 2);
    const barInnerW = Math.max(barW - gap, 1);

    ctx.fillStyle = accent;
    days.forEach((d, i) => {
      const barH = Math.max((d.totalCost / axisMax) * plotH, d.totalCost > 0 ? 2 : 0);
      const x = startX + i * barW + gap / 2;
      const y = plotBottom - barH;
      roundRectPath(ctx, x, y, barInnerW, barH, Math.min(3, barInnerW / 2));
      ctx.fill();
    });

    // A lone bar (1d filter) has nothing to compare against, so label its
    // exact cost directly instead of leaving a bare block.
    if (days.length === 1) {
      ctx.fillStyle = textColor;
      ctx.font = "600 12px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(costFmt(days[0].totalCost), startX + barW / 2, topPad - 4);
    }

    ctx.fillStyle = textColor;
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    // Skip labels when bars are too narrow to fit a date under each one.
    const labelStride = Math.max(1, Math.ceil(32 / barW));
    days.forEach((d, i) => {
      if (i % labelStride !== 0) return;
      const [, month, dayNum] = (d.date || "").split("-");
      if (!dayNum) return;
      ctx.fillText(`${dayNum}/${month}`, startX + i * barW + barW / 2, h - 4);
    });
  }

  const daysLabel = (days) => (days === "all" ? "todo el historial" : days === "1" ? "hoy" : `últimos ${days} días`);

  let currentDays = "30";
  document.getElementById("cu-days-filter").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".days-btn");
    if (!btn) return;
    currentDays = btn.dataset.days;
    for (const b of document.querySelectorAll(".days-btn")) b.classList.toggle("active", b === btn);
    document.getElementById("cu-chart-title").textContent = `Coste diario (${daysLabel(currentDays)})`;
    document.getElementById("cu-summary-title").textContent = `Resumen (${daysLabel(currentDays)})`;
    document.getElementById("cu-cost-label").textContent = currentDays === "all" ? "Coste" : `Coste (${currentDays}d)`;
    document.getElementById("cu-tokens-label").textContent = currentDays === "all" ? "Tokens" : `Tokens (${currentDays}d)`;
    renderClaudeUsage();
  });

  let renderRequestId = 0;
  async function renderClaudeUsage() {
    const requestId = ++renderRequestId;
    // ccusage rescans local log files on a cache miss (first load, or every 5
    // min after the backend's TTL expires), which can take several seconds
    // with no visual feedback otherwise — show a loading state immediately.
    document.getElementById("cu-total-cost").textContent = "…";
    document.getElementById("cu-total-tokens").textContent = "…";
    document.getElementById("cu-session-count").textContent = "…";
    document.getElementById("cu-today-cost").textContent = "…";
    const list = document.getElementById("cu-session-list");
    list.textContent = "";
    const loadingLi = document.createElement("li");
    loadingLi.textContent = "Cargando datos de Claude Code…";
    list.appendChild(loadingLi);
    try {
      const [dailyRes, sessionRes] = await Promise.all([
        fetch(`/api/claude-usage?period=daily&days=${currentDays}`),
        fetch(`/api/claude-usage?period=session&days=${currentDays}`),
      ]);
      if (!dailyRes.ok || !sessionRes.ok) throw new Error("claude-usage unavailable");
      const dailyData = await dailyRes.json();
      const sessionData = await sessionRes.json();
      // A newer request (e.g. the user clicked another day filter) already
      // started and will render; drop this now-stale response.
      if (requestId !== renderRequestId) return;

      document.getElementById("cu-total-cost").textContent = costFmt(dailyData.totals?.totalCost);
      document.getElementById("cu-total-tokens").textContent = tokensFmt(dailyData.totals?.totalTokens);
      document.getElementById("cu-session-count").textContent = sessionData.sessions?.length ?? "--";

      const days = dailyData.daily || [];
      const today = days[days.length - 1];
      document.getElementById("cu-today-cost").textContent = today ? costFmt(today.totalCost) : "$0.00";
      drawDailyCostChart(days);

      list.textContent = "";
      const allSessions = [...(sessionData.sessions || [])].sort((a, b) => b.totalCost - a.totalCost);
      for (const s of allSessions) {
        const li = document.createElement("li");
        const project = document.createElement("span");
        project.className = "cu-session-project";
        project.textContent = (s.projectPath || s.sessionId).split(/[/\\-]/).pop() || s.sessionId;
        const meta = document.createElement("span");
        meta.className = "cu-session-meta";
        meta.textContent = `${costFmt(s.totalCost)} · ${tokensFmt(s.totalTokens)} tok`;
        li.appendChild(project);
        li.appendChild(meta);
        list.appendChild(li);
      }
    } catch (_) {
      if (requestId !== renderRequestId) return;
      document.getElementById("cu-total-cost").textContent = "N/D";
      document.getElementById("cu-total-tokens").textContent = "N/D";
      document.getElementById("cu-session-count").textContent = "N/D";
      document.getElementById("cu-today-cost").textContent = "N/D";
      list.textContent = "";
      const errLi = document.createElement("li");
      errLi.textContent = "No se pudo cargar el uso de Claude Code (ccusage no disponible).";
      list.appendChild(errLi);
    }
  }
  setInterval(() => {
    if (currentView === "metrics") renderClaudeUsage();
  }, 120000);

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
