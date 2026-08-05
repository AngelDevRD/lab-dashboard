"""Tests para el módulo de alertas: dedup, histéresis, recovery, tags, umbrales."""

import asyncio

import pytest

from app.alerts.models import Alert, AlertStatus, Severity
from app.alerts.notifier import TAG_MAP, _build_tags, notify_alert, notify_recovery
from app.alerts.service import NotificationService
from app.alerts.thresholds import ThresholdSettings
from pydantic import ValidationError


def make_server(host="10.0.0.1", online=True, **overrides):
    server = {
        "host": host,
        "name": "srv1",
        "online": online,
        "cpu": {"percent": 10, "cores": 4},
        "load": {"load1": 0.1},
        "mem": {"percent": 10},
        "disk": {"percent": 10, "free": 10_000_000_000},
        "docker": {"available": True, "containers": [{"name": "x", "state": "running", "status": "Up"}]},
        "services": {},
        "power": {"available": False},
    }
    server.update(overrides)
    return server


class TestTagDeduplication:
    def test_tag_map_not_mutated_across_calls(self):
        original = list(TAG_MAP["cpu"])
        for _ in range(5):
            _build_tags("cpu", "CRITICAL", recovered=False)
        assert TAG_MAP["cpu"] == original, "TAG_MAP debe permanecer intacto entre llamadas"

    def test_rotating_light_appears_once(self):
        tags = _build_tags("cpu", "CRITICAL", recovered=False)
        assert tags.count("rotating_light") == 1

    def test_recovered_tags_have_no_rotating_light(self):
        tags = _build_tags("cpu", "CRITICAL", recovered=True)
        assert "rotating_light" not in tags
        assert "white_check_mark" in tags


class TestThresholdValidation:
    def test_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            ThresholdSettings(cpu_warning=150)

    def test_rejects_negative(self):
        with pytest.raises(ValidationError):
            ThresholdSettings(latency_critical_ms=-10)

    def test_valid_update_applies(self):
        s = ThresholdSettings()
        updated = ThresholdSettings(**{**s.model_dump(), "cpu_warning": 70})
        assert updated.cpu_warning == 70


@pytest.fixture(autouse=True)
def no_real_notifications(monkeypatch):
    """process_server fires notify_alert/notify_recovery via ensure_future — make
    sure tests never hit the real network."""
    async def noop(*a, **k):
        return None

    monkeypatch.setattr("app.alerts.service.notify_alert", noop)
    monkeypatch.setattr("app.alerts.service.notify_recovery", noop)
    monkeypatch.setattr("app.alerts.service.play_battery_alarm", noop)


@pytest.mark.asyncio
class TestHysteresisAndDedup:
    async def test_single_breach_does_not_create_alert(self, monkeypatch):
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=2, cpu_warning=50, cpu_critical=90),
        )
        svc = NotificationService()
        server = make_server(cpu={"percent": 95, "cores": 4})
        result = svc.process_server(server)
        assert result == [], "una sola muestra por encima del umbral no debe generar alerta"
        assert svc.get_active_alerts_count() == 0

    async def test_alert_created_after_hysteresis_cycles(self, monkeypatch):
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=2, cpu_warning=50, cpu_critical=90),
        )
        svc = NotificationService()
        server = make_server(cpu={"percent": 95, "cores": 4})
        svc.process_server(server)
        result = svc.process_server(server)
        assert len(result) == 1
        assert svc.get_active_alerts_count() == 1

    async def test_no_duplicate_alert_while_condition_persists(self, monkeypatch):
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=1, cpu_warning=50, cpu_critical=90),
        )
        svc = NotificationService()
        server = make_server(cpu={"percent": 95, "cores": 4})
        first = svc.process_server(server)
        assert len(first) == 1
        for _ in range(5):
            again = svc.process_server(server)
            assert again == [], "no debe re-crear/re-emitir la misma alerta activa"
        assert svc.get_active_alerts_count() == 1

    async def test_recovery_emitted_once_after_hysteresis(self, monkeypatch):
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=1, cpu_warning=50, cpu_critical=90),
        )
        svc = NotificationService()
        hot = make_server(cpu={"percent": 95, "cores": 4})
        cold = make_server(cpu={"percent": 10, "cores": 4})
        svc.process_server(hot)
        assert svc.get_active_alerts_count() == 1
        resolved = svc.process_server(cold)
        assert len(resolved) == 1
        assert resolved[0].status == AlertStatus.RESOLVED
        assert svc.get_active_alerts_count() == 0

    async def test_offline_host_produces_only_network_alert(self, monkeypatch):
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=1),
        )
        svc = NotificationService()
        offline = {"host": "10.0.0.9", "name": "srv9", "online": False, "offline_reason": "timeout"}
        result = svc.process_server(offline)
        assert len(result) == 1
        assert result[0].category == "network"
        assert "docker" not in [a.category for a in result]

    async def test_cooldown_prevents_immediate_re_alert(self, monkeypatch):
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=1, cpu_warning=50, cpu_critical=90, alert_cooldown_seconds=9999),
        )
        svc = NotificationService()
        hot = make_server(cpu={"percent": 95, "cores": 4})
        cold = make_server(cpu={"percent": 10, "cores": 4})
        svc.process_server(hot)
        svc.process_server(cold)  # resolves
        again = svc.process_server(hot)
        assert again == [], "el cooldown debe impedir re-disparar la misma alerta de inmediato"

    async def test_restart_does_not_resend_still_active_alert(self, monkeypatch, tmp_path):
        """A process restart must not forget an alert was already notified —
        otherwise every redeploy/crash re-sends a fresh push for an ongoing
        problem (this is exactly what produced two duplicate 'Servidor
        desconectado' pushes during manual testing)."""
        state_file = tmp_path / "notification_state.json"
        monkeypatch.setattr("app.alerts.service.STATE_FILE", state_file)
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=1),
        )
        offline = {"host": "10.0.0.9", "name": "srv9", "online": False, "offline_reason": "timeout"}

        svc1 = NotificationService()
        first = svc1.process_server(offline)
        assert len(first) == 1
        assert state_file.exists()

        # Simulate a restart: brand-new instance, condition still ongoing.
        svc2 = NotificationService()
        assert svc2.get_active_alerts_count() == 1, "el estado activo debe restaurarse al reiniciar"
        second = svc2.process_server(offline)
        assert second == [], "no debe re-crear ni re-notificar una alerta que ya estaba activa"


@pytest.mark.asyncio
class TestSoundAlarm:
    """La alarma sonora (sound_alarm.play_battery_alarm) debe sonar en el
    propio servidor cuando se activa una alerta de bateria real, y solo
    para esa categoria -- no para cpu/mem/disco/etc."""

    async def test_battery_alert_triggers_sound_alarm(self, monkeypatch):
        calls: list[str] = []

        async def spy(host):
            calls.append(host)

        monkeypatch.setattr("app.alerts.service.play_battery_alarm", spy)
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=1, battery_critical=20, battery_emergency=10),
        )
        svc = NotificationService()
        server = make_server(power={"available": True, "percent": 15, "status": "Discharging"})
        svc.process_server(server)
        await asyncio.sleep(0)  # deja correr la corrutina disparada via ensure_future
        assert calls == ["10.0.0.1"]

    async def test_cpu_alert_does_not_trigger_sound_alarm(self, monkeypatch):
        calls: list[str] = []

        async def spy(host):
            calls.append(host)

        monkeypatch.setattr("app.alerts.service.play_battery_alarm", spy)
        monkeypatch.setattr(
            "app.alerts.service.threshold_manager._settings",
            ThresholdSettings(hysteresis_cycles=1, cpu_warning=50, cpu_critical=90),
        )
        svc = NotificationService()
        server = make_server(cpu={"percent": 95, "cores": 4})
        svc.process_server(server)
        assert calls == []


@pytest.mark.asyncio
class TestNotifierRobustness:
    async def test_notify_alert_survives_missing_aiohttp(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "aiohttp":
                raise ImportError("no aiohttp")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        alert = Alert(
            id="x", server="srv1", server_host="10.0.0.1", severity=Severity.CRITICAL,
            category="cpu", title="CPU crítica", description="test",
        )
        await notify_alert(alert)  # must not raise

    async def test_notify_alert_rejects_invalid_alert(self):
        await notify_alert(None)  # must not raise

    async def test_send_ntfy_rate_limited(self, monkeypatch):
        from app.alerts import notifier
        monkeypatch.setattr(notifier, "NTFY_MAX_PER_WINDOW", 1)
        notifier._send_timestamps.clear()
        calls = []

        async def fake_import_ok():
            return True

        sent = {"count": 0}

        class FakeResp:
            status = 200
            async def text(self):
                return "ok"
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False

        class FakeSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            def post(self, *a, **k):
                sent["count"] += 1
                return FakeResp()

        import sys
        import types
        fake_aiohttp = types.SimpleNamespace(ClientSession=lambda: FakeSession(), ClientTimeout=lambda **k: None)
        monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

        await notifier.send_ntfy("t1", "m1")
        await notifier.send_ntfy("t2", "m2")
        assert sent["count"] == 1, "el segundo envío debe descartarse por rate limit"
