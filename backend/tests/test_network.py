"""Tests para Network Guardian: parse_network_status y POST /api/network/report."""

import pytest
from httpx import AsyncClient, ASGITransport

from app.collectors.parsers import parse_network_status


class TestParseNetworkStatus:
    def test_empty(self):
        assert parse_network_status("") == {"available": False}

    def test_malformed_json(self):
        assert parse_network_status("{bad") == {"available": False}

    def test_empty_json_object(self):
        assert parse_network_status("{}") == {"available": False}

    def test_valid(self):
        raw = '{"current_network": "repetidor", "rssi": -55, "status": "ok"}'
        result = parse_network_status(raw)
        assert result["available"] is True
        assert result["current_network"] == "repetidor"
        assert result["rssi"] == -55


@pytest.fixture
def client():
    from app.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
class TestNetworkReportEndpoint:
    async def test_missing_token_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.NETWORK_REPORT_TOKEN", "secret123")
        resp = await client.post(
            "/api/network/report", json={"device_id": "pc-principal"}
        )
        assert resp.status_code == 401

    async def test_wrong_token_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.NETWORK_REPORT_TOKEN", "secret123")
        resp = await client.post(
            "/api/network/report",
            json={"device_id": "pc-principal"},
            headers={"X-Guardian-Token": "wrong"},
        )
        assert resp.status_code == 401

    async def test_missing_device_id_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.NETWORK_REPORT_TOKEN", "secret123")
        resp = await client.post(
            "/api/network/report",
            json={"status": "ok"},
            headers={"X-Guardian-Token": "secret123"},
        )
        assert resp.status_code == 422

    async def test_valid_report_stored(self, client, monkeypatch):
        from app.monitor import monitor

        monkeypatch.setattr("app.config.NETWORK_REPORT_TOKEN", "secret123")
        resp = await client.post(
            "/api/network/report",
            json={"device_id": "pc-principal", "status": "ok", "rssi": -50},
            headers={"X-Guardian-Token": "secret123"},
        )
        assert resp.status_code == 204
        assert "pc-principal" in monitor._pushed_devices
        assert monitor._pushed_devices["pc-principal"]["rssi"] == -50
