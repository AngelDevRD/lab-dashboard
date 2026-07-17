import logging

from .rules import RULES
from .thresholds import threshold_manager

logger = logging.getLogger("dashboard")


def evaluate_server(server: dict) -> list[dict]:
    settings = threshold_manager.settings
    alerts = []
    for rule in RULES:
        try:
            for result in rule(server, settings):
                result["server"] = server.get("name", server.get("host", "unknown"))
                result["server_host"] = server.get("host", "unknown")
                alerts.append(result)
        except Exception as e:
            logger.error("Rule error for %s: %s", server.get("host"), e)
    return alerts
