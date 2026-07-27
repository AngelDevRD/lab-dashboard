"""Tests para el tracking de descargas completas / validacion del modelo
(autonomy.py) -- ver AUDITORIA_PRECISION.md: ningun metodo de autonomia se
podia evaluar de forma valida porque los tramos de descarga terminaban por
reconexion de cargador, no por agotamiento real."""

from app.collectors.autonomy import HostAutonomyState, estimate, record_sample


def test_discharge_ending_by_ac_reconnect_does_not_count():
    state = HostAutonomyState(host="test")
    t0 = 1000.0
    for i in range(5):
        record_sample(state, t0 + i, 3.0, 20.0 - i * 0.5)
        estimate(state, "Discharging", 20.0 - i * 0.5, 40.0, now=t0 + i, capacity_pct=50 - i * 5)
    # se reconecta el cargador en 25%, nunca llego a critico
    result = estimate(state, "Charging", 20.0, 40.0, now=t0 + 6, capacity_pct=25)
    assert result["validated"] is False
    assert result["complete_discharges"] == 0


def test_discharge_reaching_critical_level_counts_as_complete():
    state = HostAutonomyState(host="test")
    t0 = 1000.0
    result = None
    for i, cap in enumerate([80, 60, 40, 20, 10, 5]):
        record_sample(state, t0 + i, 3.0, 30.0 - i * 2)
        result = estimate(state, "Discharging", 30.0 - i * 2, 40.0, now=t0 + i, capacity_pct=cap)
    assert result["validated"] is True
    assert result["complete_discharges"] == 1
    assert result["reliability"] == "media"


def test_reliability_upgrades_to_alta_after_three_discharges():
    state = HostAutonomyState(host="test")
    t = 1000.0
    result = None
    for cycle in range(3):
        for cap in (80, 40, 10):
            t += 1
            record_sample(state, t, 3.0, 20.0)
            result = estimate(state, "Discharging", 20.0, 40.0, now=t, capacity_pct=cap)
        t += 1
        result = estimate(state, "Charging", 20.0, 40.0, now=t, capacity_pct=90)
    assert result["complete_discharges"] == 3
    assert result["reliability"] == "alta"


def test_power_age_s_grows_only_when_value_stays_frozen():
    state = HostAutonomyState(host="test")
    t0 = 2000.0
    record_sample(state, t0, 1.52, 30.0)  # power_now "congelado" tipico de angel2
    record_sample(state, t0 + 300, 1.52, 29.5)  # mismo valor 300s despues
    result = estimate(state, "Discharging", 29.5, 40.0, now=t0 + 300, capacity_pct=70)
    assert result["power_age_s"] == 300

    record_sample(state, t0 + 310, 1.80, 29.4)  # cambia de verdad
    result = estimate(state, "Discharging", 29.4, 40.0, now=t0 + 310, capacity_pct=70)
    assert result["power_age_s"] == 0
