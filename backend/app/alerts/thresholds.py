import json
import logging
from pathlib import Path
from pydantic import BaseModel

logger = logging.getLogger("dashboard")

SETTINGS_FILE = Path("/logs/alert_settings.json")


class ThresholdSettings(BaseModel):
    battery_warning: int = 30
    battery_critical: int = 20
    battery_emergency: int = 10
    cpu_warning: int = 80
    cpu_critical: int = 95
    cpu_max_temp: float = 85.0
    ram_critical: int = 95
    ram_warning: int = 80
    disk_critical: int = 95
    disk_warning: int = 80
    disk_min_free_bytes: int = 2000000000
    disk_max_temp: float = 60.0
    swap_warning: int = 50
    swap_critical: int = 80
    latency_warning_ms: int = 300
    latency_critical_ms: int = 800
    load_warning_multiplier: float = 2.0
    check_interval: int = 10
    alert_cooldown_seconds: int = 300
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
        self._settings = self._settings.model_copy(update=updates)
        self._save()
        return self._settings


threshold_manager = ThresholdManager()
