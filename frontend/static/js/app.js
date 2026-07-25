(() => {
  "use strict";

  const grid = document.getElementById("server-grid");
  const template = document.getElementById("server-card-template");
  const cards = new Map();
  const connectivityGrid = document.getElementById("connectivity-grid");
  const connectivityTemplate = document.getElementById("connectivity-card-template");
  const connectivityCards = new Map();
  const prevOnline = new Map();
  const autoState = new Map();
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
    if (!ctx) return;
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

  function round5(sec) {
    return Math.round(sec / 300) * 300;
  }

  function fmtAuto(sec) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return h > 0 && m > 0 ? `${h} h ${m} min` : h > 0 ? `${h} h` : `${m} min`;
  }

  function applyAuto(el, txt) {
    if (el.textContent === txt) return;
    el.textContent = txt;
    el.classList.remove("auto-flash");
    void el.offsetWidth;
    el.classList.add("auto-flash");
  }

  function renderAutonomy(refs, power, host) {
    const el = refs.el.autonomy;
    const sec = power.autonomy_seconds;
    const status = power.status;

    let special = null;
    if (!power.available) special = "--";
    else if (status === "Full" || status === "Not charging") special = "En AC";
    else if (sec == null || sec <= 0) special = "N/A";

    const st = autoState.get(host);

    if (special !== null) {
      if (!st || st.lastText !== special) {
        applyAuto(el, special);
        autoState.set(host, { lastText: special, lastSec: null, lastUpdate: Date.now(), special: true });
      }
      return;
    }

    const rounded = round5(sec);
    const txt = fmtAuto(rounded);

    if (st && st.special) {
      applyAuto(el, txt);
      autoState.set(host, { lastText: txt, lastSec: rounded, lastUpdate: Date.now(), special: false });
      return;
    }

    const now = Date.now();
    const elapsed = st ? now - st.lastUpdate : Infinity;

    if (elapsed < 30000) return;

    if (elapsed >= 60000 || !st || st.lastSec == null) {
      applyAuto(el, txt);
      autoState.set(host, { lastText: txt, lastSec: rounded, lastUpdate: now, special: false });
      return;
    }

    const diff = Math.abs(rounded - st.lastSec);
    const pct = st.lastSec > 0 ? diff / st.lastSec : 1;
    if (diff > 300 || pct > 0.05) {
      applyAuto(el, txt);
      autoState.set(host, { lastText: txt, lastSec: rounded, lastUpdate: now, special: false });
    }
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
        autonomy: el.querySelector(".autonomy-value"),
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
      const offDot = "status-dot offline";
      refs.el.dot.className = offDot;
      refs.last.dot = offDot;
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
      setText(refs, "autonomy", "--");
      autoState.delete(s.host);
      const offCtx = refs.el.sparkline.getContext("2d");
      if (offCtx) offCtx.clearRect(0, 0, refs.el.sparkline.width, refs.el.sparkline.height);
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
    if (hist.cpu.length >= 2 || hist.mem.length >= 2) drawSparkline(refs.el.sparkline, hist.cpu, hist.mem);

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

    renderAutonomy(refs, power, s.host);

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

  function renderConnectivity(devices) {
    const list = (devices || []).filter((d) => d.source !== "ssh");
    const card = document.getElementById("connectivity-card");
    card.classList.toggle("hidden", list.length === 0);
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
    if (was === undefined) return;
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

  function renderSummary(data) {
    document.getElementById("pill-online").textContent = `${data.summary.online} online`;
    document.getElementById("pill-offline").textContent = `${data.summary.offline} offline`;
  }

  function renderInternet(net) {
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

  function renderEvents(events) {
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

  function renderAlerts(alerts) {

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
    for (const s of data.servers) { try { renderServer(s); } catch (e) { console.error("renderServer failed for", s.host, e); } }
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

  let wsConnecting = false;
  function connectWS() {
    if (wsConnecting) return;
    wsConnecting = true;
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
      wsConnecting = false;
      log("WS closed, scheduling reconnect in 3s");
      if (!usingFallback) startFallback();
      setTimeout(connectWS, 3000);
    };
    ws.onerror = () => { wsConnecting = false; ws.close(); };
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
    if (usingFallback) return;
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
    if (fallbackTimer) clearInterval(fallbackTimer);
    fallbackTimer = null;
    usingFallback = false;
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

  // --- View navigation (Servidores <-> Métricas <-> Proyectos IA) ---
  const viewServers = document.getElementById("view-servers");
  const viewMetrics = document.getElementById("view-metrics");
  const viewFramework = document.getElementById("view-framework-telemetry");
  let currentView = "servers";

  function showView(name) {
    currentView = name;
    document.body.dataset.view = name;
    viewServers.classList.toggle("hidden", name !== "servers");
    viewMetrics.classList.toggle("hidden", name !== "metrics");
    viewFramework.classList.toggle("hidden", name !== "framework-telemetry");
    if (name === "metrics") renderClaudeUsage();
    if (name === "framework-telemetry") renderFrameworkTelemetry();
  }
  document.getElementById("nav-metrics-btn").addEventListener("click", () => showView("metrics"));
  document.getElementById("nav-servers-btn").addEventListener("click", () => showView("servers"));
  document.getElementById("nav-framework-btn").addEventListener("click", () => showView("framework-telemetry"));
  document.getElementById("nav-servers-btn-2").addEventListener("click", () => showView("servers"));
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

  // --- Framework telemetry (Herramienta de Desarrollo con IA) ---
  const finalStateClass = (state) => {
    if (state === "Completado") return "ft-ok";
    if (state === "Bloqueado") return "ft-bad";
    return "ft-warn";
  };
  const timeAgoFmt = (iso) => {
    if (!iso) return "--";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  };
  const numFmt = (n) => (n == null ? "--" : n.toLocaleString("es"));
  let ftProjectsCache = [];

  async function renderFrameworkTelemetry() {
    const grid = document.getElementById("ft-project-grid");
    const recentList = document.getElementById("ft-recent-list");
    try {
      const res = await fetch("/api/framework-telemetry");
      if (!res.ok) throw new Error("framework-telemetry unavailable");
      const data = await res.json();

      document.getElementById("ft-project-count").textContent = data.projects?.length ?? "--";
      document.getElementById("ft-total-entries").textContent = data.totalEntries ?? "--";

      grid.textContent = "";
      const projects = [...(data.projects || [])].sort((a, b) =>
        (b.lastTimestamp || "").localeCompare(a.lastTimestamp || "")
      );
      ftProjectsCache = projects;
      for (const p of projects) {
        const card = document.createElement("article");
        card.className = "card server-card ft-clickable";
        card.innerHTML = `
          <div class="card-header">
            <div class="server-title"><h2 class="server-name">${p.project ?? "--"}</h2></div>
            <span class="status-text ${finalStateClass(p.lastFinalState)}">${p.lastFinalState ?? "--"}</span>
          </div>
          <div class="mini-stats">
            <div class="mini"><span class="mini-label">Último agente</span><span class="mini-value">${p.lastAgent ?? "--"}</span></div>
            <div class="mini"><span class="mini-label">Sesiones vistas</span><span class="mini-value">${p.sessionsSeen ?? "--"}</span></div>
            <div class="mini"><span class="mini-label">Última sesión</span><span class="mini-value">${timeAgoFmt(p.lastTimestamp)}</span></div>
            <div class="mini"><span class="mini-label">Versión framework</span><span class="mini-value">${p.frameworkVersion ?? "--"}</span></div>
          </div>
          <div class="card-footer">
            <span>${p.lastSummary ?? ""}</span>
          </div>`;
        card.addEventListener("click", () => openProjectDetail(p));
        grid.appendChild(card);
      }

      recentList.textContent = "";
      for (const entry of data.recent || []) {
        const li = document.createElement("li");
        const project = document.createElement("span");
        project.className = "cu-session-project";
        project.textContent = `${entry.project ?? "--"} — ${entry.agent ?? "--"}`;
        const meta = document.createElement("span");
        meta.className = "cu-session-meta";
        meta.textContent = `${timeAgoFmt(entry.timestamp)} · ${entry.finalState ?? "--"} · ${entry.filesModified ?? 0} archivos`;
        li.appendChild(project);
        li.appendChild(meta);
        recentList.appendChild(li);
      }
    } catch (_) {
      grid.textContent = "";
      const errCard = document.createElement("p");
      errCard.textContent = "No se pudo cargar la telemetría de proyectos IA.";
      grid.appendChild(errCard);
    }
  }
  setInterval(() => {
    if (currentView === "framework-telemetry") renderFrameworkTelemetry();
  }, 120000);

  // --- Detalle de proyecto (tokens/tools/mcps/skills/subagentes/quality real) ---
  const pdOverlay = document.getElementById("project-detail-overlay");
  const pdBody = document.getElementById("pd-body");
  document.getElementById("pd-close").addEventListener("click", () => pdOverlay.classList.add("hidden"));
  pdOverlay.addEventListener("click", (e) => { if (e.target === pdOverlay) pdOverlay.classList.add("hidden"); });

  // --- Render generico + especializado por collector (arquitectura v3) ------
  // countBarsHTML: sirve para cualquier collector con forma {clave: {count,
  // errors}} o {clave: count} (tools/mcps/skills/subagents) -- no hardcodea
  // cuales existen.
  function countBarsHTML(dict, stripMcpPrefix) {
    const rows = Object.entries(dict || {}).sort((a, b) => {
      const ca = typeof a[1] === "object" ? a[1].count : a[1];
      const cb = typeof b[1] === "object" ? b[1].count : b[1];
      return cb - ca;
    });
    if (rows.length === 0) return `<p class="pd-empty">Sin datos reales todavía.</p>`;
    return rows.map(([name, val]) => {
      const count = typeof val === "object" ? val.count : val;
      const errors = typeof val === "object" ? val.errors : 0;
      const errTag = errors > 0 ? `<span class="pd-err">${errors} error${errors === 1 ? "" : "es"}</span>` : "";
      const label = stripMcpPrefix ? name.replace(/^mcp__/, "") : name;
      return `<div class="pd-bar-row"><span class="pd-bar-name">${label}</span><span class="pd-bar-count">${numFmt(count)}</span>${errTag}</div>`;
    }).join("");
  }

  function statusBadgeHTML(status) {
    const cls = status === "passed" || status === "APPROVED" ? "ft-ok"
      : status === "failed" || status === "REJECTED" ? "ft-bad" : "ft-warn";
    return `<span class="status-text ${cls}">${status ?? "--"}</span>`;
  }

  // Checklist de COBERTURA (que collector encontro datos vs. no) -- itera las
  // claves tal cual llegan del backend, sin lista hardcodeada. Un collector
  // nuevo del lado PowerShell aparece aca solo, sin tocar este archivo.
  function coverageChecklistHTML(coverage) {
    const entries = Object.entries(coverage || {}).sort((a, b) => a[0].localeCompare(b[0]));
    if (entries.length === 0) return `<p class="pd-empty">Sin datos de cobertura.</p>`;
    return `<div class="pd-coverage-grid">${entries.map(([name, has]) =>
      `<span class="pd-coverage-chip ${has ? "pd-ok" : "pd-missing"}">${has ? "✓" : "✗"} ${name}</span>`
    ).join("")}</div>`;
  }

  // dependency: tecnologias normalizadas {id, category, source, confidence,
  // version?} -- se agrupan por categoria, la evidencia (source) va en el title.
  function dependencySnapshotHTML(dep) {
    const techs = dep?.technologies || [];
    if (techs.length === 0) return `<p class="pd-empty">Sin tecnologías detectadas.</p>`;
    const byCategory = {};
    for (const t of techs) { if (!byCategory[t.category]) byCategory[t.category] = []; byCategory[t.category].push(t); }
    return Object.entries(byCategory).map(([cat, items]) => `
      <div class="pd-tech-group">
        <span class="pd-tech-cat">${cat}</span>
        ${items.map((t) => `<span class="pd-tech-badge" title="Fuente: ${t.source}">${t.id}${t.version ? " " + t.version : ""}</span>`).join("")}
      </div>`).join("");
  }

  function dockerSnapshotHTML(d) {
    if (!d) return `<p class="pd-empty">Sin Docker detectado.</p>`;
    const services = (d.services || []).map((s) => `<span class="pd-tech-badge">${s}</span>`).join("") || `<span class="pd-empty">Sin servicios en compose.</span>`;
    return `
      <div class="mini-stats">
        <div class="mini"><span class="mini-label">Dockerfile</span><span class="mini-value">${d.hasDockerfile ? "Sí" : "No"}</span></div>
        <div class="mini"><span class="mini-label">Compose</span><span class="mini-value">${d.hasCompose ? d.composeFile : "No"}</span></div>
      </div>
      <div class="pd-tech-group">${services}</div>`;
  }

  function kubernetesSnapshotHTML(k) {
    if (!k) return `<p class="pd-empty">Sin manifests K8s detectados.</p>`;
    return `<div class="pd-tech-group">${(k.kinds || []).map((k2) => `<span class="pd-tech-badge">${k2.kind} × ${k2.count}</span>`).join("")}</div>`;
  }

  function gitHistoryHTML(g) {
    if (!g) return `<p class="pd-empty">Sin historial git todavía.</p>`;
    const contributors = (g.contributors || []).map((c) =>
      `<div class="pd-bar-row"><span class="pd-bar-name">${c.name}</span><span class="pd-bar-count">${numFmt(c.commits)}</span></div>`
    ).join("") || `<p class="pd-empty">Sin contribuidores.</p>`;
    return `
      <div class="mini-stats">
        <div class="mini"><span class="mini-label">Commits totales</span><span class="mini-value">${numFmt(g.totalCommits)}</span></div>
        <div class="mini"><span class="mini-label">Últimos 30 días</span><span class="mini-value">${numFmt(g.commitsLast30Days)}</span></div>
        <div class="mini"><span class="mini-label">Primer commit</span><span class="mini-value">${timeAgoFmt(g.firstCommitDate)}</span></div>
        <div class="mini"><span class="mini-label">Último commit</span><span class="mini-value">${timeAgoFmt(g.lastCommitDate)}</span></div>
      </div>
      ${contributors}`;
  }

  function qualityHistoryHTML(qg) {
    if (!qg) return `<p class="pd-empty">Sin Quality Gate reciente.</p>`;
    const stacks = (qg.stacksDetected || []).join(", ") || "--";
    const checks = Object.entries(qg.checks || {}).map(([name, c]) =>
      `<div class="pd-bar-row"><span class="pd-bar-name">${name}</span>${statusBadgeHTML(c.status)}</div>`
    ).join("");
    return `
      <div class="mini-stats">
        <div class="mini"><span class="mini-label">Resultado</span>${statusBadgeHTML(qg.qualityGate)}</div>
        <div class="mini"><span class="mini-label">Stack</span><span class="mini-value">${stacks}</span></div>
      </div>
      ${checks}`;
  }

  function securityHistoryHTML(sec) {
    if (!sec) return `<p class="pd-empty">Sin escaneo de seguridad reciente.</p>`;
    return `<div class="pd-bar-row"><span class="pd-bar-name">${sec.details ?? "--"}</span>${statusBadgeHTML(sec.status)}</div>`;
  }

  function routerHistoryHTML(r) {
    if (!r || !r.delegations || r.delegations.length === 0) return `<p class="pd-empty">Sin delegaciones a RouterAgent en esta sesión.</p>`;
    return r.delegations.map((d) =>
      `<div class="pd-bar-row"><span class="pd-bar-name">${d.taskType} → ${d.provider}</span><span class="pd-bar-count">${d.ms}ms</span>${d.success ? "" : '<span class="pd-err">error</span>'}</div>`
    ).join("");
  }

  // Fallback generico: cualquier collector futuro no reconocido explicitamente
  // arriba se muestra igual, como tabla clave/valor -- nunca desaparece del
  // overlay solo por ser nuevo.
  function genericJSONHTML(value) {
    return `<pre class="pd-generic-json">${JSON.stringify(value, null, 2)}</pre>`;
  }

  const SNAPSHOT_RENDERERS = {
    dependency: (v) => dependencySnapshotHTML(v),
    docker: (v) => dockerSnapshotHTML(v),
    kubernetes: (v) => kubernetesSnapshotHTML(v),
    metrics: (v) => countBarsHTML(Object.fromEntries((v?.byExtension || []).map((e) => [e.extension, e.count]))),
    project: (v) => `
      <div class="mini-stats">
        <div class="mini"><span class="mini-label">Tipo</span><span class="mini-value">${v.projectType ?? "--"}</span></div>
        <div class="mini"><span class="mini-label">Modo desarrollo</span><span class="mini-value">${v.developmentMode ?? "--"}</span></div>
        <div class="mini"><span class="mini-label">Config</span><span class="mini-value">${v.configSource ?? "--"}</span></div>
        <div class="mini"><span class="mini-label">Creado</span><span class="mini-value">${v.createdAt ?? "--"}</span></div>
      </div>`,
    recommendedAgents: (v) => countBarsHTML(Object.fromEntries((v || []).map((a) => [a.agent, a.score]))),
  };
  const HISTORY_RENDERERS = {
    git: (v) => gitHistoryHTML(v),
    quality: (v) => qualityHistoryHTML(v),
    security: (v) => securityHistoryHTML(v),
    router: (v) => routerHistoryHTML(v),
    transcript: (v) => `
      <div class="mini-stats">
        <div class="mini"><span class="mini-label">Input</span><span class="mini-value">${numFmt(v.tokens?.inputTokens)}</span></div>
        <div class="mini"><span class="mini-label">Output</span><span class="mini-value">${numFmt(v.tokens?.outputTokens)}</span></div>
        <div class="mini"><span class="mini-label">Cache creation</span><span class="mini-value">${numFmt(v.tokens?.cacheCreationTokens)}</span></div>
        <div class="mini"><span class="mini-label">Cache read</span><span class="mini-value">${numFmt(v.tokens?.cacheReadTokens)}</span></div>
      </div>
      <h4 class="pd-subhead">Herramientas</h4>${countBarsHTML(v.tools)}
      <h4 class="pd-subhead">MCPs</h4>${countBarsHTML(v.mcps, true)}
      <h4 class="pd-subhead">Skills</h4>${countBarsHTML(v.skills)}
      <h4 class="pd-subhead">Subagentes</h4>${countBarsHTML(v.subagents)}`,
  };
  const SECTION_LABELS = {
    dependency: "Stack y tecnologías detectadas", docker: "Docker", kubernetes: "Kubernetes",
    metrics: "Distribución de código", project: "Configuración declarada", recommendedAgents: "Agentes recomendados",
    git: "Historial de Git", quality: "Quality Gate", security: "Seguridad", router: "RouterAgent",
    transcript: "Tokens y herramientas (Claude Code)",
  };

  function renderSections(dataDict, renderers) {
    return Object.entries(dataDict || {}).map(([name, value]) => {
      const label = SECTION_LABELS[name] || name;
      const renderer = renderers[name];
      const body = renderer ? renderer(value) : genericJSONHTML(value);
      return `<h3>${label}</h3>${body}`;
    }).join("");
  }

  function openProjectDetail(p) {
    document.getElementById("pd-name").textContent = p.project ?? "--";

    const sessions = [...(p.sessions || [])].sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
    const latestHistory = {};
    // Para el resumen mostramos el ultimo valor no-nulo de cada collector de
    // historial (ademas de sesion por sesion en el timeline de abajo).
    for (const s of sessions) {
      for (const [name, value] of Object.entries(s.history || {})) {
        if (value != null && !(name in latestHistory)) latestHistory[name] = value;
      }
    }

    pdBody.innerHTML = `
      <h3>Cobertura de telemetría</h3>
      ${coverageChecklistHTML(p.coverage)}

      ${renderSections(p.snapshot, SNAPSHOT_RENDERERS)}
      ${renderSections(latestHistory, HISTORY_RENDERERS)}

      <h3>Timeline de sesiones (${sessions.length})</h3>
      <ul class="event-log session-list session-list-full">
        ${sessions.map((s) => {
          const diff = s.history?.git?.thisSessionDiff
            ? ` · ${s.history.git.thisSessionDiff.filesChanged} arch. (+${s.history.git.thisSessionDiff.insertions}/-${s.history.git.thisSessionDiff.deletions})`
            : "";
          return `<li><span class="cu-session-project">${timeAgoFmt(s.timestamp)} — ${s.agent ?? "--"}</span><span class="cu-session-meta">${s.finalState ?? "--"}${diff}${s.backfill ? " · histórico" : ""}</span></li>`;
        }).join("")}
      </ul>`;

    pdOverlay.classList.remove("hidden");
  }

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
