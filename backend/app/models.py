from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class StatusResponse(BaseModel):
    servers: list[dict]
    internet: dict
    summary: dict
    events: list[dict]
    timestamp: float
