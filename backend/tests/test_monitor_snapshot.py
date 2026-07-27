"""Test de integracion de Monitor.snapshot(): entrada simulada de un
collect_server() real -> snapshot() -> validar el JSON final completo.
A diferencia de los tests unitarios de confidence.py/autonomy.py por
separado, esto ejercita los tres modulos juntos tal como los consume
main.py, para detectar incompatibilidades entre ellos (pedido de revision)."""

from collections import deque

from app.collectors.autonomy import HostAutonomyState
from app.monitor import Monitor

HOST = "192.168.100.7"


def fake_server_snapshot(**overrides) -> dict:
    """Mismo shape que devuelve collectors.collector.collect_server()."""
    base = {
        "name": "Servidor 2",
        "host": HOST,
        "online": True,
        "last_update": 1_800_000_000.0,
        "latency_ms": 45,
        "uptime": {"pretty": "up 2 days", "seconds": 172800.0},
        "load": {"load1": 0.3, "load5": 0.2, "load15": 0.1},
        "cpu": {"percent": 12.5, "cores": 4, "temp": 49.0, "temp_per_core": [49.0, 49.0, 49.0, 48.0]},
        "mem": {"total": 4_000_000_000, "used": 1_000_000_000, "free": 3_000_000_000,
                "percent": 25.0, "cache": 0, "buffers": 0,
                "swap": {"total": 0, "used": 0, "free": 0, "percent": 0.0}},
        "disk": {"total": 100_000_000_000, "used": 10_000_000_000, "free": 90_000_000_000, "percent": 10.0},
        "net": {"ip": HOST, "rx_total": 1000, "tx_total": 1000, "download_bps": 100, "upload_bps": 100},
        "docker": {"available": True, "running": 4, "stopped": 0, "containers": []},
        "docker_disk": None,
        "updates_pending": 0,
        "services": {"ssh": "active", "docker": "active"},
        "power": {
            "available": True, "percent": 70, "status": "Discharging", "voltage": 7.8,
            "energy_now_wh": 19.1, "energy_full_wh": 27.29, "power_now_w": 2.85,
            "autonomy_seconds": None,
        },
        "disk_temp": None,
        "clock_offset_s": 0.4,
        "top_cpu": [],
        "top_mem": [],
        "network": {"available": False},
    }
    base.update(overrides)
    return base


def test_snapshot_end_to_end_has_confidence_and_health():
    monitor = Monitor()
    monitor._server_order = [HOST]
    monitor.servers_status[HOST] = fake_server_snapshot()
    # En produccion, _record_history() crea este estado durante el poll
    # (Monitor._poll_server -> _record_history -> autonomy.record_sample).
    # snapshot() solo lo LEE, no lo crea -- hay que sembrarlo a mano aca
    # para simular un host que ya paso por al menos un ciclo de poll.
    monitor._autonomy_state[HOST] = HostAutonomyState(host=HOST)

    result = monitor.snapshot()

    assert result["summary"] == {"total": 1, "online": 1, "offline": 0}
    server = result["servers"][0]
    assert server["host"] == HOST

    # Confidence: presentes las claves esperadas, con reasons[] en cada una.
    conf = server["confidence"]
    for key in ("cpu", "mem", "disk", "power", "battery_percent", "autonomy", "clock", "connectivity"):
        assert key in conf, f"falta '{key}' en confidence"
        assert "reasons" in conf[key]

    # Modelo de autonomia: sin descargas completas -> no validado (estado
    # real esperado la primera vez que se ve este host).
    assert server["power"]["validated"] is False
    assert server["power"]["complete_discharges"] == 0
    assert conf["autonomy"]["reasons"] == ["model_unvalidated"]

    # Health Score: rollup presente, con checklist no vacio.
    health = server["telemetry_health"]
    assert 0 <= health["score"] <= 100
    assert health["label"] in ("Excelente", "Buena", "Advertencia", "Crítica")
    assert len(health["checks"]) == len(conf)


def test_snapshot_reflects_frozen_cpu_history():
    monitor = Monitor()
    monitor._server_order = [HOST]
    monitor.servers_status[HOST] = fake_server_snapshot()
    monitor._cpu_history[HOST] = deque([12.5, 12.5, 12.5, 12.5, 12.5], maxlen=40)

    result = monitor.snapshot()
    conf = result["servers"][0]["confidence"]
    assert conf["cpu"]["reasons"] == ["frozen"]
    assert conf["cpu"]["score"] < 98


def test_snapshot_offline_server_has_no_confidence_block_crash():
    monitor = Monitor()
    monitor._server_order = [HOST]
    monitor.servers_status[HOST] = {
        "name": "Servidor 2", "host": HOST, "online": False,
        "last_update": 1_800_000_000.0, "error": "timeout",
    }
    result = monitor.snapshot()
    server = result["servers"][0]
    assert server["online"] is False
    # No debe explotar aunque el snapshot offline no tenga cpu/mem/power.
    assert "confidence" in server


def test_snapshot_validated_autonomy_after_complete_discharge():
    monitor = Monitor()
    monitor._server_order = [HOST]
    state = HostAutonomyState(host=HOST)
    state.complete_discharges = 2
    state.last_status = "Discharging"
    state.discharge_counted = True
    monitor._autonomy_state[HOST] = state
    monitor.servers_status[HOST] = fake_server_snapshot()

    result = monitor.snapshot()
    server = result["servers"][0]
    assert server["power"]["validated"] is True
    assert server["power"]["complete_discharges"] == 2
    assert server["confidence"]["autonomy"]["score"] > 15
