"""Replays the raw 10-minute audit captures through (a) the OLD algorithm
(plain average of power_now over an unbounded ~120-sample/6min buffer, exactly
what monitor.py did before) and (b) the new hybrid algorithm in
backend/app/collectors/autonomy.py, and compares them against a ground-truth
reference: the true average power over the whole session, computed from
total energy delta / total time (unambiguous physics, unlike any single
instantaneous reading).

angel2's capture is looped 3x (timestamps kept monotonic) to simulate a
~30 minute continuous session — the real dashboard runs indefinitely, and the
hybrid algorithm's confirmation logic needs ~15 min (3 x 5-min cycles) of
sustained frozen power_now before it calibrates, which a single 10-minute
capture cannot exercise on its own.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.collectors import autonomy  # noqa: E402

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
        rows.append({
            "elapsed": float(d["elapsed"]),
            "status": d["status"],
            "voltage_now": to_f(d["voltage_now"]),
            "current_now": to_f(d["current_now"]),
            "power_now": to_f(d["power_now"]),
            "energy_now": to_f(d["energy_now"]),
            "energy_full": to_f(d["energy_full"]),
            "charge_now": to_f(d["charge_now"]),
            "charge_full": to_f(d["charge_full"]),
        })
    return rows


def convert(d):
    """Mirrors parsers.parse_battery's fallback logic: energy/power from
    whichever fields the firmware actually exposes."""
    voltage_v = d["voltage_now"] / 1_000_000 if d["voltage_now"] else None
    power_w = None
    if d["power_now"]:
        power_w = d["power_now"] / 1_000_000
    elif d["current_now"] and voltage_v:
        power_w = abs(d["current_now"]) * voltage_v / 1_000_000
    energy_now_wh = None
    if d["energy_now"]:
        energy_now_wh = d["energy_now"] / 1_000_000
    elif d["charge_now"] and voltage_v:
        energy_now_wh = (d["charge_now"] / 1_000_000) * voltage_v
    energy_full_wh = None
    if d["energy_full"]:
        energy_full_wh = d["energy_full"] / 1_000_000
    elif d["charge_full"] and voltage_v:
        energy_full_wh = (d["charge_full"] / 1_000_000) * voltage_v
    return power_w, energy_now_wh, energy_full_wh


def loop_rows(rows, times):
    """Repeats the capture `times` times with monotonically increasing elapsed
    AND monotonically increasing energy/charge counters — a naive repeat would
    reset energy_now back to its starting value every lap (sawtooth), which
    corrupts the ground-truth true_avg_power (it would see net-zero energy
    change over most of the span). Real continuous charging over a longer
    session keeps accumulating, so each lap's energy/charge fields are offset
    by the previous lap's net delta."""
    out = []
    span = rows[-1]["elapsed"] - rows[0]["elapsed"]
    e_now_delta = (rows[-1]["energy_now"] or 0) - (rows[0]["energy_now"] or 0)
    charge_now_delta = (rows[-1]["charge_now"] or 0) - (rows[0]["charge_now"] or 0)
    for i in range(times):
        t_offset = i * (span + 3)
        e_offset = i * e_now_delta
        c_offset = i * charge_now_delta
        for r in rows:
            r2 = dict(r)
            r2["elapsed"] = r["elapsed"] + t_offset
            if r2["energy_now"] is not None:
                r2["energy_now"] = r2["energy_now"] + e_offset
            if r2["charge_now"] is not None:
                r2["charge_now"] = r2["charge_now"] + c_offset
            out.append(r2)
    return out


def old_algorithm(rows):
    """Exact behavior of the pre-hybrid code: deque(maxlen=120) of power_now
    (~6 min at 3s poll), autonomy from the plain mean whenever len>=3."""
    from collections import deque
    buf = deque(maxlen=120)
    power_series = []
    autonomy_series = []
    for d in rows:
        power_w, e_now, e_full = convert(d)
        if power_w is not None and power_w > 0:
            buf.append(power_w)
        avg = sum(buf) / len(buf) if len(buf) >= 3 else None
        power_series.append(avg)
        autonomy_series.append(autonomy.autonomy_seconds(d["status"], e_now, e_full, avg))
    return power_series, autonomy_series


def hybrid_algorithm(rows):
    state = autonomy.HostAutonomyState()
    power_series = []
    autonomy_series = []
    modes = []
    for d in rows:
        power_w, e_now, e_full = convert(d)
        now = d["elapsed"]
        autonomy.record_sample(state, now, power_w, e_now)
        result = autonomy.estimate(state, d["status"], e_now, e_full, now=now)
        power_series.append(result["power_used_w"])
        autonomy_series.append(result["autonomy_seconds"])
        modes.append(result["mode"])
    return power_series, autonomy_series, modes


def stats(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None
    mean = sum(v) / len(v)
    var = sum((x - mean) ** 2 for x in v) / len(v)
    sd = var**0.5
    return {"n": len(v), "mean": mean, "sd": sd, "cv_pct": (sd / mean * 100 if mean else None)}


def true_avg_power(rows):
    e0 = None
    e1 = None
    for d in rows:
        _, en, _ = convert(d)
        if en is not None:
            if e0 is None:
                e0 = en
            e1 = en
    t0, t1 = rows[0]["elapsed"], rows[-1]["elapsed"]
    dt_h = (t1 - t0) / 3600
    if e0 is None or dt_h <= 0:
        return None
    return abs(e1 - e0) / dt_h


def mean_abs_error(power_series, true_power):
    errs = [abs(p - true_power) for p in power_series if p is not None]
    return sum(errs) / len(errs) if errs else None


def run(host, raw_path, loop_times=1):
    rows = parse(raw_path)
    rows = loop_rows(rows, loop_times)
    true_power = true_avg_power(rows)

    old_power, old_auto = old_algorithm(rows)
    hyb_power, hyb_auto, hyb_modes = hybrid_algorithm(rows)

    old_power_stats = stats(old_power)
    hyb_power_stats = stats(hyb_power)
    old_auto_stats = stats(old_auto)
    hyb_auto_stats = stats(hyb_auto)

    old_mae = mean_abs_error(old_power, true_power) if true_power else None
    hyb_mae = mean_abs_error(hyb_power, true_power) if true_power else None

    mode_counts = {}
    for m in hyb_modes:
        mode_counts[m] = mode_counts.get(m, 0) + 1

    print(f"\n=== {host} (loop x{loop_times}, {len(rows)} muestras, ~{rows[-1]['elapsed']/60:.0f} min) ===")
    print(f"Potencia real (delta energia total/tiempo total): {true_power:.2f}W" if true_power else "Potencia real: N/D")
    print(f"Modos hibrido observados: {mode_counts}")
    print(f"{'':20s} {'ALGORITMO ACTUAL':>20s} {'ALGORITMO HIBRIDO':>20s} {'MEJORA':>10s}")
    if old_power_stats and hyb_power_stats:
        print(f"{'Potencia CV%':20s} {old_power_stats['cv_pct']:>19.1f}% {hyb_power_stats['cv_pct']:>19.1f}%")
    if old_mae is not None and hyb_mae is not None:
        improvement = (old_mae - hyb_mae) / old_mae * 100 if old_mae else 0
        print(f"{'Error medio (W)':20s} {old_mae:>20.2f} {hyb_mae:>20.2f} {improvement:>9.1f}%")
    if old_auto_stats and hyb_auto_stats:
        print(f"{'Autonomia CV%':20s} {old_auto_stats['cv_pct']:>19.1f}% {hyb_auto_stats['cv_pct']:>19.1f}%")
        print(f"{'Autonomia media (min)':20s} {old_auto_stats['mean']/60:>19.1f} {hyb_auto_stats['mean']/60:>19.1f}")

    return {
        "host": host, "true_power": true_power,
        "old_mae": old_mae, "hyb_mae": hyb_mae,
        "old_power_cv": old_power_stats["cv_pct"] if old_power_stats else None,
        "hyb_power_cv": hyb_power_stats["cv_pct"] if hyb_power_stats else None,
        "mode_counts": mode_counts,
    }


if __name__ == "__main__":
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "sample_data"
    r1 = run("angel1 (power_now sano)", base / "raw_angel1.txt", loop_times=1)
    r2 = run("angel2 (power_now congelado, 10 min)", base / "raw_angel2.txt", loop_times=1)
    r3 = run("angel2 (power_now congelado, sesion larga simulada)", base / "raw_angel2.txt", loop_times=3)
