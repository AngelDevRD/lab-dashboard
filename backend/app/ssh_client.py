import asyncio
import logging
import time
from pathlib import Path

import paramiko

from . import config

logger = logging.getLogger("dashboard")

KNOWN_HOSTS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "ssh" / "known_hosts"
)


# Marcador que separa la salida de cada comando dentro de una tanda. Ver
# _build_batch: toda la tanda viaja en un solo exec_command en vez de uno por
# comando.
_BATCH_MARK = "__LABDASH_C7F3__"


def _build_batch(commands: dict[str, str]) -> str:
    """Concatena la tanda en un solo script de shell, con un marcador antes de
    la salida de cada comando.

    Cada exec_command abre un canal SSH nuevo (open + exec + datos + close):
    con ~14 comandos por ciclo y varios servidores cada POLL_INTERVAL eso son
    decenas de canales por ciclo, todos secuenciales bajo el mismo lock. Un
    solo canal por tanda deja el costo en un round-trip.

    `exec 2>/dev/null` al inicio manda todo stderr al vacío: _run_blocking
    lee stdout pero nunca drena stderr, así que un comando charlatán podría
    llenar el buffer del canal y bloquear la lectura.
    """
    parts = ["exec 2>/dev/null"]
    for key, cmd in commands.items():
        parts.append(f"echo '{_BATCH_MARK}{key}'")
        parts.append(cmd)
    return "\n".join(parts)


def _split_batch(output: str, commands: dict[str, str]) -> dict[str, tuple[bool, str]]:
    """Parte la salida de la tanda por los marcadores de _build_batch."""
    chunks: dict[str, str] = {}
    key: str | None = None
    lines: list[str] = []
    for line in output.split("\n"):
        if line.startswith(_BATCH_MARK):
            if key is not None:
                chunks[key] = "\n".join(lines)
            key = line[len(_BATCH_MARK):].strip()
            lines = []
        elif key is not None:
            lines.append(line)
    if key is not None:
        chunks[key] = "\n".join(lines)
    # Un comando cuyo marcador no aparece (salida truncada) se reporta como
    # fallo de ese comando, no de toda la tanda.
    return {k: (k in chunks, chunks.get(k, "")) for k in commands}


def classify_ssh_error(message: str) -> str:
    """Map a raw SSH error string to a stable reason code.

    Used to keep 'timeout', 'unreachable' and 'auth error' distinct in alerts
    instead of collapsing everything into a generic 'offline'/'high latency'.
    """
    msg = message.lower()
    if "en backoff" in msg:
        return "backoff"
    if "timed out" in msg or "timeout" in msg:
        return "timeout"
    if (
        "no route to host" in msg
        or "no valid connections" in msg
        or "connection refused" in msg
        or "network is unreachable" in msg
        or "unable to connect" in msg
        or "unreachable" in msg
    ):
        return "unreachable"
    if "authentication" in msg or "auth" in msg:
        return "auth_error"
    return "network_error"


class SSHConnection:
    """Reusable SSH connection for one server.

    Reconnects lazily on failure, backing off exponentially so a downed
    server or bad credentials can't produce a rapid-fire reconnect storm.

    Host key verification is done against config/ssh/known_hosts.
    """

    def __init__(self, host: str, port: int, username: str):
        self.host = host
        self.port = port
        self.username = username
        self._client: paramiko.SSHClient | None = None
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._next_attempt_at = 0.0

    def _backoff_seconds(self) -> float:
        # Cap the exponent, not just the final result: a host that stays down
        # for days drives _consecutive_failures into the thousands, and
        # 2**failures overflows float before min() ever gets a chance to
        # clamp it. 20 doublings already blows past SSH_BACKOFF_MAX for any
        # sane base/max, so it's a safe ceiling regardless of config.
        exponent = min(self._consecutive_failures, 20)
        return min(
            config.SSH_BACKOFF_BASE * (2**exponent),
            config.SSH_BACKOFF_MAX,
        )

    def _in_backoff(self) -> bool:
        return time.monotonic() < self._next_attempt_at

    def _close_client(self, client: paramiko.SSHClient | None) -> None:
        if client is None:
            return
        try:
            client.close()
        except Exception:
            logger.debug("Error closing SSH client for %s", self.host, exc_info=True)

    def _on_failure(self, exc: Exception) -> None:
        self._close_client(self._client)
        self._client = None
        # Cap the counter itself so it stays a meaningful number in logs for
        # a host that's been down for days/weeks, instead of climbing into
        # the tens of thousands. 20 already saturates _backoff_seconds.
        self._consecutive_failures = min(self._consecutive_failures + 1, 20)
        wait = self._backoff_seconds()
        self._next_attempt_at = time.monotonic() + wait
        logger.warning(
            "%s no responde (fallo #%d): %s - próximo intento en %.0fs",
            self.host,
            self._consecutive_failures,
            exc,
            wait,
        )

    def _on_connect_success(self) -> None:
        if self._consecutive_failures:
            logger.info(
                "%s volvió a responder tras %d fallo(s)",
                self.host,
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._next_attempt_at = 0.0

    def _connect_blocking(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        if KNOWN_HOSTS_PATH.exists():
            client.load_host_keys(str(KNOWN_HOSTS_PATH))
        else:
            logger.warning("No known_hosts file at %s — SSH host keys will NOT be verified", KNOWN_HOSTS_PATH)
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                key_filename=config.SSH_KEY_PATH,
                timeout=config.SSH_TIMEOUT,
                banner_timeout=config.SSH_TIMEOUT,
                auth_timeout=config.SSH_TIMEOUT,
            )
        except Exception:
            self._close_client(client)
            raise
        self._client = client
        self._on_connect_success()

    def _run_blocking(self, command: str, timeout: float | None = None) -> tuple[bool, str]:
        try:
            transport = self._client.get_transport() if self._client else None
            needs_connect = transport is None or not transport.is_active()
            if needs_connect:
                if self._in_backoff():
                    remaining = self._next_attempt_at - time.monotonic()
                    return False, (f"en backoff, próximo intento en {remaining:.0f}s")
                self._connect_blocking()
            stdin, stdout, stderr = self._client.exec_command(
                command,
                timeout=config.SSH_COMMAND_TIMEOUT if timeout is None else timeout,
            )
            out = stdout.read().decode("utf-8", errors="replace")
            stdout.channel.recv_exit_status()
            return True, out
        except Exception as exc:
            self._on_failure(exc)
            return False, str(exc)

    async def run(self, command: str) -> tuple[bool, str]:
        async with self._lock:
            return await asyncio.to_thread(self._run_blocking, command)

    async def run_many(self, commands: dict[str, str]) -> dict[str, tuple[bool, str]]:
        """Ejecuta toda la tanda en un único canal SSH (ver _build_batch).

        Un fallo de SSH (conexión caída, backoff, timeout de la tanda entera)
        marca todos los comandos como fallidos, igual que antes; la diferencia
        es que ahora el timeout acota la tanda completa en vez de aplicarse
        por comando (antes el peor caso era SSH_COMMAND_TIMEOUT × n).
        """
        if not commands:
            return {}
        async with self._lock:
            ok, out = await asyncio.to_thread(
                self._run_blocking, _build_batch(commands), config.SSH_BATCH_TIMEOUT
            )
            if not ok:
                return {key: (False, out) for key in commands}
            return _split_batch(out, commands)

    def close(self) -> None:
        self._close_client(self._client)
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
