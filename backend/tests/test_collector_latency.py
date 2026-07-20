"""Tests para collector.py: la latencia debe ser el RTT real, nunca un timeout disfrazado."""

import pytest

from app.collectors.collector import collect_server


class FakeConnTimeout:
    """Simulates a server whose SSH handshake times out."""

    async def run(self, command):
        return False, "timed out"

    async def run_many(self, commands):
        raise AssertionError("no debe ejecutar el batch de comandos si la conexión falló")


class FakeConnUnreachable:
    async def run(self, command):
        return False, "No route to host"

    async def run_many(self, commands):
        raise AssertionError("no debe ejecutar el batch de comandos si la conexión falló")


class FakeConnHealthy:
    async def run(self, command):
        assert command == "hostname"
        return True, "srv1\n"

    async def run_many(self, commands):
        return {key: (True, "") for key in commands}


@pytest.mark.asyncio
class TestOfflineNeverReportsLatency:
    async def test_timeout_has_no_latency_ms(self):
        snapshot = await collect_server({"host": "10.0.0.1", "name": "srv1"}, FakeConnTimeout())
        assert snapshot["online"] is False
        assert "latency_ms" not in snapshot, "un timeout nunca debe aparecer como latencia (ej. 6522ms)"
        assert snapshot["offline_reason"] == "timeout"

    async def test_unreachable_has_no_latency_ms(self):
        snapshot = await collect_server({"host": "10.0.0.1", "name": "srv1"}, FakeConnUnreachable())
        assert snapshot["online"] is False
        assert "latency_ms" not in snapshot
        assert snapshot["offline_reason"] == "unreachable"

    async def test_healthy_host_has_latency_ms(self):
        snapshot = await collect_server({"host": "10.0.0.1", "name": "srv1"}, FakeConnHealthy())
        assert snapshot["online"] is True
        assert isinstance(snapshot["latency_ms"], int)
        assert snapshot["latency_ms"] >= 0
