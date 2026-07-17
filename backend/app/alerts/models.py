import time
from enum import Enum
from pydantic import BaseModel, Field


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


class AlertStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"


class Alert(BaseModel):
    id: str
    timestamp: float = Field(default_factory=time.time)
    server: str
    server_host: str
    severity: Severity
    category: str
    title: str
    description: str
    current_value: float | str | None = None
    threshold_value: float | str | None = None
    status: AlertStatus = AlertStatus.ACTIVE
    resolved_at: float | None = None


class AlertCount(BaseModel):
    total: int = 0
    active: int = 0
    resolved: int = 0
    info: int = 0
    warning: int = 0
    critical: int = 0
    emergency: int = 0
