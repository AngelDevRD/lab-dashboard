"""Tests para events.py."""

from app.events import log_event, recent_events
import time


class TestEvents:
    def test_log_and_recover(self):
        log_event("test", "mensaje de prueba")
        events = recent_events(5)
        matching = [e for e in events if e["kind"] == "test"]
        assert len(matching) >= 1
        assert matching[0]["message"] == "mensaje de prueba"

    def test_limit(self):
        for i in range(10):
            log_event("bulk", str(i))
        assert len(recent_events(3)) <= 3
