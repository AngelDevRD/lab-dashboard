"""Tests para ssh_client.py — pool, backoff y tanda de comandos."""

import pytest

from app.ssh_client import (
    _BATCH_MARK,
    SSHConnection,
    SSHPool,
    _build_batch,
    _split_batch,
)


class TestSSHPool:
    def test_get_or_create(self):
        pool = SSHPool()
        s1 = pool.get({"host": "10.0.0.1", "ssh_port": 22, "ssh_user": "test"})
        s2 = pool.get({"host": "10.0.0.1", "ssh_port": 22, "ssh_user": "test"})
        assert s1 is s2

    def test_close_all(self):
        pool = SSHPool()
        pool.get({"host": "10.0.0.1"})
        pool.get({"host": "10.0.0.2"})
        pool.close_all()
        # No exception = pass


class TestBackoffNeverOverflows:
    def test_backoff_stays_capped_after_thousands_of_failures(self):
        """A host that's been down for days can rack up thousands of
        consecutive failures. 2**failures used to be computed before the
        min() clamp ever ran, which raised OverflowError and broke backoff
        entirely (it kept retrying every cycle instead of backing off) —
        this reproduces that exact scenario."""
        from app import config

        conn = SSHConnection(host="10.0.0.1", port=22, username="test")
        conn._consecutive_failures = 100_000
        assert conn._backoff_seconds() == config.SSH_BACKOFF_MAX  # must not raise OverflowError

    def test_on_failure_caps_the_counter(self):
        conn = SSHConnection(host="10.0.0.1", port=22, username="test")
        conn._consecutive_failures = 10_000
        conn._on_failure(RuntimeError("boom"))
        assert conn._consecutive_failures <= 20


def fake_shell(script: str, outputs: dict[str, str]) -> str:
    """Simula el shell remoto ejecutando el script de _build_batch: `echo` del
    marcador imprime la línea, y cualquier otro comando imprime lo que diga
    `outputs` para él."""
    lines = []
    for line in script.split("\n"):
        if line == "exec 2>/dev/null":
            continue
        if line.startswith(f"echo '{_BATCH_MARK}"):
            lines.append(line[len("echo '"):-1])
        else:
            lines.append(outputs.get(line, ""))
    return "\n".join(lines) + "\n"


class TestCommandBatch:
    def test_roundtrip_keeps_each_output_with_its_key(self):
        commands = {"uptime": "cat /proc/uptime", "mem": "cat /proc/meminfo"}
        outputs = {"cat /proc/uptime": "1234.5 900.1", "cat /proc/meminfo": "MemTotal: 4\nMemFree: 2"}
        result = _split_batch(fake_shell(_build_batch(commands), outputs), commands)
        assert result["uptime"][0] is True
        assert result["uptime"][1].strip() == "1234.5 900.1"
        assert result["mem"][1].strip() == "MemTotal: 4\nMemFree: 2"

    def test_stderr_is_silenced_for_the_whole_batch(self):
        # exec_command no drena stderr; sin esto un comando charlatán puede
        # llenar el buffer del canal y bloquear la lectura de stdout.
        assert _build_batch({"a": "cmd"}).startswith("exec 2>/dev/null\n")

    def test_empty_command_output_is_not_a_failure(self):
        commands = {"disk_temp": "smartctl -A /dev/sda"}
        ok, out = _split_batch(fake_shell(_build_batch(commands), {}), commands)["disk_temp"]
        assert ok is True
        assert out.strip() == ""

    def test_missing_marker_fails_only_that_command(self):
        # Salida truncada: 'b' nunca llegó a imprimirse.
        commands = {"a": "cmd_a", "b": "cmd_b"}
        result = _split_batch(f"{_BATCH_MARK}a\nhola\n", commands)
        assert result["a"] == (True, "hola\n")
        assert result["b"] == (False, "")


@pytest.mark.asyncio
class TestRunMany:
    async def test_uses_a_single_channel_for_the_whole_batch(self, monkeypatch):
        calls = []
        conn = SSHConnection(host="10.0.0.1", port=22, username="test")
        commands = {"a": "cmd_a", "b": "cmd_b", "c": "cmd_c"}

        def fake_run(script, timeout=None):
            calls.append(script)
            return True, fake_shell(script, {"cmd_a": "A", "cmd_b": "B", "cmd_c": "C"})

        monkeypatch.setattr(conn, "_run_blocking", fake_run)
        result = await conn.run_many(commands)

        assert len(calls) == 1, "la tanda debe viajar en un solo exec_command"
        assert {k: v[1].strip() for k, v in result.items()} == {"a": "A", "b": "B", "c": "C"}

    async def test_ssh_failure_marks_every_command_as_failed(self, monkeypatch):
        conn = SSHConnection(host="10.0.0.1", port=22, username="test")
        monkeypatch.setattr(conn, "_run_blocking", lambda *a, **k: (False, "timed out"))
        result = await conn.run_many({"a": "cmd_a", "b": "cmd_b"})
        assert result == {"a": (False, "timed out"), "b": (False, "timed out")}

    async def test_no_commands_does_not_touch_the_connection(self, monkeypatch):
        conn = SSHConnection(host="10.0.0.1", port=22, username="test")

        def boom(*a, **k):
            raise AssertionError("no debe abrir un canal para una tanda vacía")

        monkeypatch.setattr(conn, "_run_blocking", boom)
        assert await conn.run_many({}) == {}
