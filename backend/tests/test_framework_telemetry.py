"""Tests para el endpoint de telemetría de Herramienta de Desarrollo con IA."""

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def client():
    from app.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


VALID_PAYLOAD = {
    "project": "01-gestor-habitos",
    "projectRemote": "https://github.com/negriuspro/gestorhabitos.git",
    "branch": "main",
    "timestamp": "2026-07-21T14:32:00",
    "agent": "ArchitectAgent",
    "finalState": "Completado",
    "summary": "Sesion de prueba",
    "filesModified": 3,
    "durationMinutes": 12.5,
    "totalSessions": 14,
    "frameworkVersion": "3.9.0",
}


@pytest.mark.asyncio
class TestFrameworkTelemetryEndpoint:
    async def test_missing_token_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.FRAMEWORK_TELEMETRY_TOKEN", "secret123")
        resp = await client.post("/api/framework-telemetry/report", json=VALID_PAYLOAD)
        assert resp.status_code == 401

    async def test_wrong_token_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.FRAMEWORK_TELEMETRY_TOKEN", "secret123")
        resp = await client.post(
            "/api/framework-telemetry/report",
            json=VALID_PAYLOAD,
            headers={"X-Framework-Token": "wrong"},
        )
        assert resp.status_code == 401

    async def test_missing_project_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.FRAMEWORK_TELEMETRY_TOKEN", "secret123")
        payload = {**VALID_PAYLOAD}
        del payload["project"]
        resp = await client.post(
            "/api/framework-telemetry/report",
            json=payload,
            headers={"X-Framework-Token": "secret123"},
        )
        assert resp.status_code == 422

    async def test_valid_report_stored_and_summarized(
        self, client, monkeypatch, tmp_path
    ):
        telemetry_file = tmp_path / "framework_telemetry.jsonl"
        monkeypatch.setattr("app.config.FRAMEWORK_TELEMETRY_TOKEN", "secret123")
        monkeypatch.setattr("app.config.FRAMEWORK_TELEMETRY_FILE", telemetry_file)

        resp = await client.post(
            "/api/framework-telemetry/report",
            json=VALID_PAYLOAD,
            headers={"X-Framework-Token": "secret123"},
        )
        assert resp.status_code == 204
        assert telemetry_file.exists()

        resp = await client.get("/api/framework-telemetry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["totalEntries"] == 1
        assert len(body["projects"]) == 1
        assert body["projects"][0]["project"] == "01-gestor-habitos"
        assert body["projects"][0]["lastAgent"] == "ArchitectAgent"
        assert body["projects"][0]["sessionsSeen"] == 1

    async def test_extensibility_unknown_collector_flows_through(
        self, client, monkeypatch, tmp_path
    ):
        """El backend nunca debe conocer los nombres de collector de antemano:
        un collector inventado ("benchmark", que no existe en ningun script
        real del framework) debe agregarse igual, sin tocar el modelo Pydantic
        ni summary(). Es la prueba explicita de extensibilidad del plan."""
        telemetry_file = tmp_path / "framework_telemetry.jsonl"
        monkeypatch.setattr("app.config.FRAMEWORK_TELEMETRY_TOKEN", "secret123")
        monkeypatch.setattr("app.config.FRAMEWORK_TELEMETRY_FILE", telemetry_file)

        session_1 = {
            **VALID_PAYLOAD,
            "coverage": {"dependency": True, "git": True, "benchmark": True},
            "snapshot": {
                "dependency": {
                    "technologies": [
                        {
                            "id": "flutter",
                            "category": "framework",
                            "source": "pubspec.yaml",
                            "confidence": 1.0,
                        }
                    ]
                },
                "benchmark": {"opsPerSecond": 12345},
            },
            "history": {
                "git": {
                    "totalCommits": 10,
                    "contributors": [{"name": "Angel", "commits": 10}],
                },
            },
        }
        session_2 = {
            **VALID_PAYLOAD,
            "timestamp": "2026-07-21T15:00:00",
            "coverage": {"dependency": True, "git": True, "benchmark": False},
            "snapshot": {
                "dependency": {
                    "technologies": [
                        {
                            "id": "flutter",
                            "category": "framework",
                            "source": "pubspec.yaml",
                            "confidence": 1.0,
                        }
                    ]
                },
            },
            "history": {
                "git": {
                    "totalCommits": 11,
                    "contributors": [{"name": "Angel", "commits": 11}],
                },
            },
        }

        for payload in (session_1, session_2):
            resp = await client.post(
                "/api/framework-telemetry/report",
                json=payload,
                headers={"X-Framework-Token": "secret123"},
            )
            assert resp.status_code == 204

        resp = await client.get("/api/framework-telemetry")
        proj = resp.json()["projects"][0]

        # snapshot.benchmark solo vino en la sesion 1 -- se conserva (la
        # sesion 2 no trajo nada nuevo para esa clave, no lo borra).
        assert proj["snapshot"]["benchmark"]["opsPerSecond"] == 12345
        assert proj["snapshot"]["dependency"]["technologies"][0]["id"] == "flutter"
        # coverage: OR acumulado -- benchmark=True en sesion 1 debe pisar el
        # False de la sesion 2 (si alguna sesion lo tuvo, cuenta como cubierto).
        assert proj["coverage"]["benchmark"] is True
        assert proj["coverage"]["dependency"] is True
        assert len(proj["sessions"]) == 2
        # el historial completo (no agregado) queda disponible por sesion para
        # reconstruir series de tiempo en el frontend sin logica nueva aca.
        assert proj["sessions"][0]["history"]["git"]["totalCommits"] == 10
        assert proj["sessions"][1]["history"]["git"]["totalCommits"] == 11
