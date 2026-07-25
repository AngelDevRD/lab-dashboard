"""Analiza una captura EN VIVO (no simulada) para responder especificamente:
si power_now realmente cambia, cada cuanto, si se congela, por cuanto tiempo,
si coincide con cambios de CPU/potencia, y si ANTES de asumir firmware
defectuoso el patron se sostiene durante toda la captura (no un solo evento).

Reutiliza el mismo parser pipe-delimited de sample.sh/capture_loop.sh.
"""

import statistics
import sys
from pathlib import Path

FIELDS = [
    "elapsed", "timestamp", "capacity", "status", "voltage_now", "current_now",
    "power_now", "energy_now", "energy_full", "charge_now", "charge_full",
    "tte", "ttf", "ac_online", "cpu_line", "cpu_freq", "temp", "mem_total",
    "mem_avail", "disk_read_sectors", "disk_write_sectors", "net_rx", "net_tx",
    "docker_count", "docker_cpu_pct", "docker_mem",
]


def na(v):
    return None if v in ("NA", "") else v


def to_f(v):
    v = na(v)
    return float(v) if v is not None else None


def parse(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split("|")
        if len(parts) != len(FIELDS):
            continue
        d = dict(zip(FIELDS, parts))
        d["elapsed"] = float(d["elapsed"])
        for k in ("voltage_now", "current_now", "power_now", "energy_now", "energy_full",
                   "charge_now", "charge_full", "cpu_freq", "temp", "mem_total", "mem_avail"):
            d[k] = to_f(d[k])
        cpu_parts = d["cpu_line"].split()
        d["cpu_jiffies"] = [float(x) for x in cpu_parts[1:11]] if len(cpu_parts) >= 11 else None
        rows.append(d)
    return rows


def voltage_v(d):
    return d["voltage_now"] / 1_000_000 if d["voltage_now"] else None


def power_w_raw(d):
    """power_now tal cual lo entrega el firmware (o derivado de current*voltage
    si el firmware no expone power_now directamente -- eso NO es lo mismo que
    la derivada de energia, sigue siendo una lectura "instantanea" segun el
    firmware)."""
    if d["power_now"]:
        return d["power_now"] / 1_000_000
    if d["current_now"] and d["voltage_now"]:
        return abs(d["current_now"]) * d["voltage_now"] / 1_000_000_000_000
    return None


def energy_now_wh(d):
    if d["energy_now"]:
        return d["energy_now"] / 1_000_000
    if d["charge_now"] and d["voltage_now"]:
        return (d["charge_now"] / 1_000_000) * (d["voltage_now"] / 1_000_000)
    return None


def energy_full_wh(d):
    if d["energy_full"]:
        return d["energy_full"] / 1_000_000
    if d["charge_full"] and d["voltage_now"]:
        return (d["charge_full"] / 1_000_000) * (d["voltage_now"] / 1_000_000)
    return None


def cpu_pct(prev, cur):
    if not prev or not prev.get("cpu_jiffies") or not cur["cpu_jiffies"]:
        return None
    p, c = prev["cpu_jiffies"], cur["cpu_jiffies"]
    total_p, total_c = sum(p[:8]), sum(c[:8])
    idle_p, idle_c = p[3] + p[4], c[3] + c[4]
    dt, di = total_c - total_p, idle_c - idle_p
    if dt <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (1 - di / dt)))


def analyze(path, host):
    rows = parse(path)
    if len(rows) < 5:
        return None

    prev = None
    prev_raw_power = None
    freeze_start = None
    freeze_events = []  # (start_elapsed, end_elapsed, duration, value)
    charge_events = []
    cpu_events = []
    power_events = []
    change_intervals = []  # gaps between distinct power_now readings (Hz de cambio real)
    last_change_ts = None

    # angel1 no expone power_now (siempre NA) -- su lectura instantanea nativa
    # es current_now. angel2 si expone power_now. Usar el campo NATIVO de cada
    # host, no asumir power_now en ambos -- si no, el analisis de "congelamiento"
    # es sobre un campo que ese firmware ni siquiera reporta.
    native_field = "power_now" if any(d["power_now"] is not None for d in rows) else "current_now"

    samples = []
    for i, d in enumerate(rows):
        raw_p = d[native_field]  # crudo (uW o uA segun el host), para detectar "exactamente igual" sin ruido de redondeo
        pw = power_w_raw(d)
        en = energy_now_wh(d)
        ef = energy_full_wh(d)
        cpu = cpu_pct(prev, d) if prev else None
        t = d["elapsed"]

        if raw_p is not None:
            if prev_raw_power is not None and raw_p != prev_raw_power:
                if last_change_ts is not None:
                    change_intervals.append(t - last_change_ts)
                last_change_ts = t
                if freeze_start is not None and (t - freeze_start) >= 6:
                    freeze_events.append((freeze_start, t, t - freeze_start, prev_raw_power))
                freeze_start = t
            elif prev_raw_power is None:
                last_change_ts = t
                freeze_start = t
            prev_raw_power = raw_p

        if prev is not None:
            if prev["status"] != d["status"]:
                charge_events.append((t, f"{prev['status']} -> {d['status']}"))
            if cpu is not None and prev.get("_cpu_pct") is not None and abs(cpu - prev["_cpu_pct"]) > 20:
                cpu_events.append((t, prev["_cpu_pct"], cpu))
            pp = power_w_raw(prev)
            if pp and pw and pp > 0 and abs(pw - pp) / pp > 0.30:
                power_events.append((t, pp, pw))

        d["_cpu_pct"] = cpu
        samples.append({
            "elapsed": t, "timestamp": d["timestamp"], "status": d["status"],
            "power_raw_w": pw, "power_raw_uw": raw_p, "energy_now_wh": en,
            "energy_full_wh": ef, "cpu_pct": cpu,
        })
        prev = d

    # cerrar el ultimo streak de congelamiento si sigue activo al terminar
    if freeze_start is not None and prev_raw_power is not None:
        end_t = rows[-1]["elapsed"]
        if (end_t - freeze_start) >= 6:
            freeze_events.append((freeze_start, end_t, end_t - freeze_start, prev_raw_power))

    total_span = rows[-1]["elapsed"] - rows[0]["elapsed"]
    frozen_time = sum(f[2] for f in freeze_events)
    frozen_pct = 100 * frozen_time / total_span if total_span else None

    # ventana movil de 30s para potencia derivada y contradiccion vs power_now
    contradiction_samples = 0
    checked_samples = 0
    for i, s in enumerate(samples):
        t0 = s["elapsed"]
        j = i
        while j > 0 and (t0 - samples[j - 1]["elapsed"]) <= 30:
            j -= 1
        e0, e1 = samples[j]["energy_now_wh"], s["energy_now_wh"]
        dt_h = (t0 - samples[j]["elapsed"]) / 3600
        derived = abs(e1 - e0) / dt_h if (e0 is not None and e1 is not None and dt_h > 0) else None
        s["power_derived_30s"] = derived
        if derived is not None and s["power_raw_w"] is not None:
            checked_samples += 1
            base = derived if derived > 0 else s["power_raw_w"]
            if base and abs(s["power_raw_w"] - derived) / base > 0.30:
                contradiction_samples += 1
        s["autonomy_instant_s"] = None
        s["autonomy_avg30_s"] = None
        if s["power_raw_w"] and s["energy_now_wh"] is not None:
            if s["status"] == "Discharging":
                s["autonomy_instant_s"] = (s["energy_now_wh"] / s["power_raw_w"]) * 3600
            elif s["status"] == "Charging" and s["energy_full_wh"]:
                s["autonomy_instant_s"] = ((s["energy_full_wh"] - s["energy_now_wh"]) / s["power_raw_w"]) * 3600

    power_vals = [s["power_raw_w"] for s in samples if s["power_raw_w"] is not None]
    pw_stats = None
    if len(power_vals) >= 2:
        mean = statistics.mean(power_vals)
        sd = statistics.pstdev(power_vals)
        pw_stats = {
            "min": min(power_vals), "max": max(power_vals), "mean": mean,
            "sd": sd, "cv_pct": (sd / mean * 100 if mean else None),
        }

    contradiction_pct = 100 * contradiction_samples / checked_samples if checked_samples else None
    avg_change_interval = statistics.mean(change_intervals) if change_intervals else None

    return {
        "host": host, "n_samples": len(rows), "span_s": total_span,
        "native_field": native_field,
        "power_stats": pw_stats,
        "freeze_events": freeze_events, "frozen_pct": frozen_pct,
        "longest_freeze_s": max((f[2] for f in freeze_events), default=0),
        "n_distinct_changes": len(change_intervals) + 1,
        "avg_change_interval_s": avg_change_interval,
        "contradiction_pct": contradiction_pct,
        "charge_events": charge_events, "cpu_events": cpu_events, "power_events": power_events,
        "samples": samples,
    }


def report(res):
    field = res["native_field"]
    unit = "W" if field == "power_now" else "A"
    scale = 1e6
    lines = [f"\n## {res['host']} (campo nativo del firmware: `{field}`)\n"]
    lines.append(f"- Muestras: {res['n_samples']}, duracion real: {res['span_s']/60:.1f} min")
    if res["power_stats"]:
        p = res["power_stats"]
        lines.append(
            f"- power_now efectivo (W, ya sea nativo o derivado de current*voltage): "
            f"min={p['min']:.2f} max={p['max']:.2f} mean={p['mean']:.2f} "
            f"sd={p['sd']:.2f} CV={p['cv_pct']:.1f}%"
        )
    lines.append(f"- Lecturas distintas de `{field}` observadas: {res['n_distinct_changes']}")
    if res["avg_change_interval_s"]:
        lines.append(f"- Intervalo promedio entre cambios reales de `{field}`: {res['avg_change_interval_s']:.1f}s")
    lines.append(f"- % del tiempo con `{field}` congelado (>=6s sin cambiar): {res['frozen_pct']:.1f}%" if res['frozen_pct'] is not None else "- % congelado: N/D")
    lines.append(f"- Racha de congelamiento mas larga: {res['longest_freeze_s']:.0f}s ({res['longest_freeze_s']/60:.1f} min)")
    lines.append(f"- % de muestras donde la potencia efectiva difiere >30% de la derivada (30s): {res['contradiction_pct']:.1f}%" if res['contradiction_pct'] is not None else "- contradiccion: N/D")
    lines.append(f"\n### Eventos de congelamiento de `{field}` (>= 6s)\n")
    for start, end, dur, val in res["freeze_events"][:30]:
        lines.append(f"- t={start:.0f}s a t={end:.0f}s (dur={dur:.0f}s / {dur/60:.1f}min), valor congelado: {val/scale:.3f}{unit}")
    lines.append(f"\n### Cambios de estado de carga\n")
    for t, e in res["charge_events"]:
        lines.append(f"- t={t:.0f}s: {e}")
    lines.append(f"\n### Variaciones grandes de CPU (>20 puntos)\n")
    for t, a, b in res["cpu_events"][:30]:
        lines.append(f"- t={t:.0f}s: {a:.1f}% -> {b:.1f}%")
    lines.append(f"\n### Variaciones grandes de potencia (>30%)\n")
    for t, a, b in res["power_events"][:30]:
        lines.append(f"- t={t:.0f}s: {a:.2f}W -> {b:.2f}W")

    # Correlacion cruda: de las variaciones grandes de CPU, cuantas coincidieron
    # (dentro de +/-6s) con una variacion grande de potencia real
    cpu_times = [t for t, _, _ in res["cpu_events"]]
    power_times = [t for t, _, _ in res["power_events"]]
    matched = sum(1 for ct in cpu_times if any(abs(ct - pt) <= 6 for pt in power_times))
    if cpu_times:
        lines.append(
            f"\n- Correlacion CPU<->power_now: {matched}/{len(cpu_times)} cambios grandes de CPU "
            f"coincidieron (+/-6s) con un cambio grande reportado en power_now "
            f"({100*matched/len(cpu_times):.0f}%)"
        )
    else:
        lines.append("\n- No hubo variaciones grandes de CPU en la ventana capturada")

    return "\n".join(lines)


if __name__ == "__main__":
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else base
    out_dir.mkdir(parents=True, exist_ok=True)

    all_report = ["# Auditoria en vivo (SSH, sin simulacion) - power_now\n"]
    results = {}
    for host, fname in [("angel1", "live_angel1.txt"), ("angel2", "live_angel2.txt")]:
        p = base / fname
        if not p.exists():
            continue
        res = analyze(p, host)
        if res:
            results[host] = res
            all_report.append(report(res))

    (out_dir / "REPORTE_LIVE.md").write_text("\n".join(all_report), encoding="utf-8")
    print(f"Listo: {out_dir / 'REPORTE_LIVE.md'}")
