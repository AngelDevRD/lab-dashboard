import asyncio
import logging

import paramiko

from . import config

logger = logging.getLogger("dashboard")


class SSHConnection:
    """Reusable SSH connection for one server. Reconnects lazily on failure."""

    def __init__(self, host: str, port: int, username: str):
        self.host = host
        self.port = port
        self.username = username
        self._client: paramiko.SSHClient | None = None
        self._lock = asyncio.Lock()

    def _connect_blocking(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            key_filename=config.SSH_KEY_PATH,
            timeout=config.SSH_TIMEOUT,
            banner_timeout=config.SSH_TIMEOUT,
            auth_timeout=config.SSH_TIMEOUT,
        )
        self._client = client

    def _run_blocking(self, command: str) -> tuple[bool, str]:
        try:
            if self._client is None:
                self._connect_blocking()
            transport = self._client.get_transport() if self._client else None
            if transport is None or not transport.is_active():
                self._connect_blocking()
            stdin, stdout, stderr = self._client.exec_command(
                command, timeout=config.SSH_COMMAND_TIMEOUT
            )
            out = stdout.read().decode("utf-8", errors="replace")
            stdout.channel.recv_exit_status()
            return True, out
        except Exception as exc:  # noqa: BLE001 - any SSH/network failure means offline
            self._client = None
            logger.warning("SSH failed for %s: %s", self.host, exc)
            return False, str(exc)

    async def run(self, command: str) -> tuple[bool, str]:
        async with self._lock:
            return await asyncio.to_thread(self._run_blocking, command)

    async def run_many(self, commands: dict[str, str]) -> dict[str, tuple[bool, str]]:
        """Run several commands over one SSH session sequentially, reusing the connection."""
        async with self._lock:
            results: dict[str, tuple[bool, str]] = {}
            for key, cmd in commands.items():
                ok, out = await asyncio.to_thread(self._run_blocking, cmd)
                results[key] = (ok, out)
                if not ok:
                    # connection is dead, no point running the rest
                    for remaining in commands:
                        if remaining not in results:
                            results[remaining] = (False, out)
                    break
            return results

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


class SSHPool:
    def __init__(self):
        self._connections: dict[str, SSHConnection] = {}

    def get(self, server: dict) -> SSHConnection:
        host = server["host"]
        if host not in self._connections:
            self._connections[host] = SSHConnection(
                host=host,
                port=server.get("ssh_port", 22),
                username=server.get("ssh_user", "ubuntu"),
            )
        return self._connections[host]

    def close_all(self) -> None:
        for conn in self._connections.values():
            conn.close()


pool = SSHPool()
