import logging

from .rules import RESOURCE_RULES, network_rule
from .thresholds import threshold_manager

logger = logging.getLogger("dashboard")


def evaluate_server(server: dict) -> list[dict]:
    settings = threshold_manager.settings
    # Resource rules (cpu/ram/disk/docker/...) need real metrics; an offline
    # snapshot has none, so running them would misread "no data" as "0%" or
    # "unavailable" and fire spurious alerts alongside the real outage alert.
    # network_rule is connectivity itself, so it always runs.
    rules = RESOURCE_RULES + [network_rule] if server.get("online") else [network_rule]
    alerts = []
    for rule in rules:
        try:
            for result in rule(server, settings):
                result["server"] = server.get("name", server.get("host", "unknown"))
                result["server_host"] = server.get("host", "unknown")
                alerts.append(result)
        except Exception as e:
            logger.error("Rule error for %s: %s", server.get("host"), e)
    return alerts
