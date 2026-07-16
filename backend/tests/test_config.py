"""Tests para config.py — validación de servers con Pydantic."""

from app.config import ServerConfig, load_servers
from pydantic import ValidationError
import json
import pytest


class TestServerConfig:
    def test_valid_server(self):
        s = ServerConfig(name="Test", host="192.168.1.1")
        assert s.name == "Test"
        assert s.ssh_port == 22
        assert s.ssh_user == "ubuntu"

    def test_invalid_port(self):
        with pytest.raises(ValidationError):
            ServerConfig(name="Test", host="x", ssh_port=99999)

    def test_empty_name_fails(self):
        with pytest.raises(ValidationError):
            ServerConfig(name="", host="x")


class TestLoadServers:
    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.SERVERS_FILE", tmp_path / "nonexistent.json")
        assert load_servers() == []

    def test_valid_file(self, tmp_path, monkeypatch):
        f = tmp_path / "servers.json"
        f.write_text(json.dumps({
            "servers": [{"name": "S1", "host": "10.0.0.1"}]
        }))
        monkeypatch.setattr("app.config.SERVERS_FILE", f)
        result = load_servers()
        assert len(result) == 1
        assert result[0]["name"] == "S1"

    def test_invalid_data_returns_empty(self, tmp_path, monkeypatch):
        f = tmp_path / "servers.json"
        f.write_text(json.dumps({
            "servers": [{"name": "", "host": ""}]
        }))
        monkeypatch.setattr("app.config.SERVERS_FILE", f)
        assert load_servers() == []
