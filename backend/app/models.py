from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str


class FieldMeta(BaseModel):
    """Procedencia de un valor derivado o estimado: measured (leido directo
    del hardware), derived (calculado a partir de otros valores medidos, sin
    modelo estadistico), estimated (implica un modelo/supuesto, ej. la
    autonomia) o unavailable (el hardware/firmware de este host no expone el
    campo). Se construye en monitor.py al ensamblar el snapshot -- nunca
    dentro de collectors/autonomy.py ni collectors/confidence.py, que son
    motores de calculo puros y no conocen el contrato de la API."""

    model_config = ConfigDict(extra="allow")

    origin: str
    model: str | None = None


class PowerMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    available: bool
    percent: int | None = None
    status: str | None = None
    voltage: float | None = None
    energy_now_wh: float | None = None
    energy_full_wh: float | None = None
    power_now_w: float | None = None
    autonomy_seconds: float | None = None
    autonomy_mode: str | None = None
    age_s: float | None = None
    validated: bool | None = None
    complete_discharges: int | None = None
    reliability: str | None = None
    meta: dict[str, FieldMeta] = {}


class CpuMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    percent: float
    cores: int
    temp: float | None = None
    temp_per_core: list[float] = []


class SwapMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    total: int
    used: int
    free: int
    percent: float


class MemMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    total: int
    used: int
    free: int
    percent: float
    cache: int
    buffers: int
    swap: SwapMetrics


class DiskMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    total: int
    used: int
    free: int
    percent: float


class NetMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    ip: str
    rx_total: int
    tx_total: int
    download_bps: float
    upload_bps: float
    daily_download_bytes: int | None = None
    daily_upload_bytes: int | None = None


class DockerContainer(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    state: str
    status: str


class DockerMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    available: bool
    running: int
    stopped: int
    containers: list[DockerContainer] = []


class ConfidenceEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    score: int
    source: str
    note: str | None = None
    reasons: list[str] = []


class TelemetryHealthCheck(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    text: str
    reasons: list[str] = []


class TelemetryHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    score: int
    label: str
    checks: list[TelemetryHealthCheck] = []


class ServerHistory(BaseModel):
    model_config = ConfigDict(extra="allow")

    cpu: list[float] = []
    mem: list[float] = []


class ServerStatus(BaseModel):
    """Contrato de un host individual dentro de servers[]. extra="allow"
    preserva campos aun no tipados explicitamente (services, network,
    docker_disk, updates_pending, top_cpu/top_mem, uptime, load, etc.) para
    no romper al frontend mientras se completa la migracion -- ver
    DISEÑO_ARQUITECTURA_DASHBOARD.md."""

    model_config = ConfigDict(extra="allow")

    name: str
    host: str
    online: bool
    last_update: float
    error: str | None = None
    offline_reason: str | None = None
    latency_ms: int | None = None
    cpu: CpuMetrics | None = None
    mem: MemMetrics | None = None
    disk: DiskMetrics | None = None
    net: NetMetrics | None = None
    docker: DockerMetrics | None = None
    power: PowerMetrics | None = None
    disk_temp: float | None = None
    clock_offset_s: float | None = None
    history: ServerHistory | None = None
    confidence: dict[str, ConfidenceEntry] = {}
    telemetry_health: TelemetryHealth | None = None


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    servers: list[ServerStatus]
    connectivity: list[dict] = []
    internet: dict
    summary: dict
    events: list[dict]
    alerts: list[dict] = []
    alert_count: int = 0
    timestamp: float
