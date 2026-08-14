"""Fixtures compartidos para tests."""

import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch, tmp_path):
    """Evita que los tests lean .env real o archivos del sistema."""
    monkeypatch.setenv("POLL_INTERVAL", "99")
    monkeypatch.setenv("WS_HEARTBEAT_INTERVAL", "99")
    monkeypatch.setenv("SSH_TIMEOUT", "1")
    monkeypatch.setenv("SSH_COMMAND_TIMEOUT", "1")
    monkeypatch.setenv("SSH_KEY_PATH", "/tmp/test_key")

    # Estado de alertas aislado por test. Mientras estas rutas fueron
    # Path("/logs/...") hardcodeadas, fuera del contenedor no escribían nada y
    # los tests quedaban aislados por accidente; ahora apuntan a un archivo
    # real, así que un NotificationService() nuevo cargaría las alertas activas
    # de la instancia y las sumaría a los asserts del test.
    monkeypatch.setattr("app.alerts.service.STATE_FILE", tmp_path / "notification_state.json")
    monkeypatch.setattr("app.alerts.center.ALERTS_FILE", tmp_path / "alerts_history.json")
    monkeypatch.setattr("app.alerts.center.alert_center._alerts", {})
    monkeypatch.setattr("app.alerts.center.alert_center._order", [])
