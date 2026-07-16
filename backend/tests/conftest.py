"""Fixtures compartidos para tests."""

import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Evita que los tests lean .env real o archivos del sistema."""
    monkeypatch.setenv("POLL_INTERVAL", "99")
    monkeypatch.setenv("BROADCAST_INTERVAL", "99")
    monkeypatch.setenv("SSH_TIMEOUT", "1")
    monkeypatch.setenv("SSH_COMMAND_TIMEOUT", "1")
    monkeypatch.setenv("SSH_KEY_PATH", "/tmp/test_key")
