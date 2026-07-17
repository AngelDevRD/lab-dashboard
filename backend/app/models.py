from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class StatusResponse(BaseModel):
    servers: list[dict]
    internet: dict
    summary: dict
    events: list[dict]
    alerts: list[dict] = []
    alert_count: int = 0
    timestamp: float
