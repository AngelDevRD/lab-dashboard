import json
import logging
from pathlib import Path
from pydantic import BaseModel, Field

logger = logging.getLogger("dashboard")

SETTINGS_FILE = Path("/logs/alert_settings.json")

_PCT = {"ge": 0, "le": 100}


class ThresholdSettings(BaseModel):
    battery_warning: int = Field(30, **_PCT)
    battery_critical: int = Field(20, **_PCT)
    battery_emergency: int = Field(10, **_PCT)
    cpu_warning: int = Field(80, **_PCT)
    cpu_critical: int = Field(95, **_PCT)
    cpu_max_temp: float = Field(85.0, ge=0)
    ram_critical: int = Field(95, **_PCT)
    ram_warning: int = Field(80, **_PCT)
    disk_critical: int = Field(95, **_PCT)
    disk_warning: int = Field(80, **_PCT)
    disk_min_free_bytes: int = Field(2000000000, ge=0)
    disk_max_temp: float = Field(60.0, ge=0)
    swap_warning: int = Field(50, **_PCT)
    swap_critical: int = Field(80, **_PCT)
    latency_warning_ms: int = Field(300, ge=0)
    latency_critical_ms: int = Field(800, ge=0)
    load_warning_multiplier: float = Field(2.0, ge=0)
    check_interval: int = Field(10, ge=1)
    alert_cooldown_seconds: int = Field(300, ge=0)
    # Consecutive poll cycles a condition must hold before an alert is raised
    # or cleared. Smooths out single-sample noise near a threshold so a brief
    # blip doesn't create/resolve/re-create the same alert repeatedly.
    hysteresis_cycles: int = Field(2, ge=1, le=20)
    enable_battery_alerts: bool = True
    enable_cpu_alerts: bool = True
    enable_ram_alerts: bool = True
    enable_disk_alerts: bool = True
    enable_docker_alerts: bool = True
    enable_temp_alerts: bool = True
    enable_network_alerts: bool = True
    enable_service_alerts: bool = True
    enable_system_alerts: bool = True
    enable_security_alerts: bool = True


class ThresholdManager:
    def __init__(self):
        self._settings_file = SETTINGS_FILE
        self._settings = self._load()

    def _load(self) -> ThresholdSettings:
        if self._settings_file.exists():
            try:
                with open(self._settings_file) as f:
                    return ThresholdSettings(**json.load(f))
            except Exception as e:
                logger.error("Error loading alert settings: %s", e)
        return ThresholdSettings()

    def _save(self) -> None:
        try:
            with open(self._settings_file, "w") as f:
                json.dump(self._settings.model_dump(), f, indent=2)
        except Exception as e:
            logger.error("Error saving alert settings: %s", e)

    @property
    def settings(self) -> ThresholdSettings:
        return self._settings

    def update(self, updates: dict) -> ThresholdSettings:
        # Re-validate through the constructor (not model_copy, which skips
        # validation) so an invalid/out-of-range/null value from the API
        # can't silently corrupt live thresholds.
        merged = {**self._settings.model_dump(), **updates}
        self._settings = ThresholdSettings(**merged)
        self._save()
        return self._settings


threshold_manager = ThresholdManager()
