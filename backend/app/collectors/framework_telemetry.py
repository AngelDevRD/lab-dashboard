"""Telemetría de proyectos que usan el framework "Herramienta de Desarrollo con IA".

Arquitectura de collectors (ver scripts/telemetry/collectors/*.ps1 del otro
repo): cada fuente de datos (transcript de Claude Code, git, dependencias,
Docker, Quality Gate, RouterAgent, etc.) es independiente y arma su propia
sección del payload bajo "snapshot" (estado actual del proyecto, se sobre-
escribe) o "history" (cambia sesión a sesión, se acumula), más un mapa
"coverage" de qué collector encontró datos. Este backend es deliberadamente
genérico respecto al *contenido* de cada collector — nunca conoce sus claves
de antemano — para que agregar una fuente nueva en el framework no requiera
tocar este archivo ni el modelo Pydantic.

A diferencia del push de Network Guardian (monitor._pushed_devices, solo en
memoria), acá se quiere historial persistente por proyecto — cada sesión
reportada se apendea como una línea a un JSONL en disco (mismo formato que ya
usa el propio framework para sus logs internos: events.jsonl/audit.jsonl/
self-healing.jsonl). El archivo es chico (uso personal, pocas sesiones/día)
así que se relee entero en cada GET en vez de cachear.

Todo lo que llega es 100% real (nunca estimado/inventado) — si un collector no
encontró datos, esa clave simplemente no está presente en el payload.
"""

import json
import logging
import time
from pathlib import Path

from pydantic import BaseModel

from .. import config

logger = logging.getLogger("dashboard")


class FrameworkTelemetryReport(BaseModel):
    project: str
    projectRemote: str | None = None
    branch: str | None = None
    timestamp: str
    agent: str | None = None
    finalState: str | None = None
    summary: str | None = None
    filesModified: int = 0
    durationMinutes: float = 0
    totalSessions: int | None = None
    frameworkVersion: str | None = None
    backfill: bool = False
    # Genéricos: el dispatcher de PowerShell arma estas claves collector por
    # collector, sin que este modelo necesite conocer cuáles existen.
    coverage: dict[str, bool] | None = None
    snapshot: dict | None = None
    history: dict | None = None


def _project_key(entry: dict) -> str:
    return entry.get("projectRemote") or entry.get("project") or "desconocido"


def record(report: FrameworkTelemetryReport) -> None:
    path: Path = config.FRAMEWORK_TELEMETRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    line = {**report.model_dump(), "received_at": time.time()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _read_all() -> list[dict]:
    path: Path = config.FRAMEWORK_TELEMETRY_FILE
    if not path.exists():
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Línea inválida en %s, se ignora", path)
    return entries


def summary() -> dict:
    entries = _read_all()
    projects: dict[str, dict] = {}

    for entry in entries:
        key = _project_key(entry)
        proj = projects.setdefault(
            key,
            {
                "project": entry.get("project"),
                "projectRemote": entry.get("projectRemote"),
                "sessionsSeen": 0,
                "lastAgent": None,
                "lastFinalState": None,
                "lastTimestamp": None,
                "lastSummary": None,
                "frameworkVersion": None,
                "snapshot": {},
                "coverage": {},
                "sessions": [],
            },
        )
        proj["sessionsSeen"] += 1
        # Las entradas se procesan en orden de archivo (append-only), así que
        # la última vista siempre es la más reciente sin necesidad de ordenar.
        proj["lastAgent"] = entry.get("agent") or proj["lastAgent"]
        proj["lastFinalState"] = entry.get("finalState") or proj["lastFinalState"]
        proj["lastTimestamp"] = entry.get("timestamp") or proj["lastTimestamp"]
        proj["lastSummary"] = entry.get("summary") or proj["lastSummary"]
        proj["frameworkVersion"] = (
            entry.get("frameworkVersion") or proj["frameworkVersion"]
        )
        proj["project"] = entry.get("project") or proj["project"]

        # snapshot: generico, sobreescribe con el valor mas reciente por clave
        # -- un collector nuevo del lado PowerShell fluye solo, sin cambios aca.
        for collector_name, value in (entry.get("snapshot") or {}).items():
            if value is not None:
                proj["snapshot"][collector_name] = value

        # coverage: OR acumulado por clave entre todas las sesiones del proyecto.
        for collector_name, has_data in (entry.get("coverage") or {}).items():
            proj["coverage"][collector_name] = proj["coverage"].get(
                collector_name, False
            ) or bool(has_data)

        proj["sessions"].append(entry)

    recent = sorted(entries, key=lambda e: e.get("timestamp") or "", reverse=True)[:20]

    return {
        "projects": list(projects.values()),
        "recent": recent,
        "totalEntries": len(entries),
    }
