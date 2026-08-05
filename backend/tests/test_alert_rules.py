from app.alerts.rules import power_rule
from app.alerts.thresholds import ThresholdSettings


def test_power_rule_triggers_on_low_battery():
    server = {"name": "Servidor X", "power": {"available": True, "percent": 25, "status": "Discharging", "power_now_w": 5.0}}
    alerts = list(power_rule(server, ThresholdSettings()))
    assert len(alerts) == 1
    assert alerts[0]["title"] == "Batería baja"


def test_power_rule_skips_when_battery_unavailable():
    server = {"name": "Servidor X", "power": {"available": False}}
    assert list(power_rule(server, ThresholdSettings())) == []


def test_power_rule_skips_damaged_battery_stuck_at_zero():
    """Bateria fisicamente presente pero danada: 0% fijo, sin power_now_w,
    status "Not charging" -- nunca es una emergencia real, es hardware roto.
    Sin este chequeo dispararia 'Bateria en emergencia' para siempre."""
    server = {
        "name": "Servidor X",
        "power": {"available": True, "percent": 0, "status": "Not charging", "power_now_w": None},
    }
    assert list(power_rule(server, ThresholdSettings())) == []


def test_power_rule_still_triggers_on_real_zero_percent_discharging():
    server = {
        "name": "Servidor X",
        "power": {"available": True, "percent": 0, "status": "Discharging", "power_now_w": 4.2},
    }
    alerts = list(power_rule(server, ThresholdSettings()))
    assert len(alerts) == 1
    assert alerts[0]["title"] == "Batería en emergencia"
