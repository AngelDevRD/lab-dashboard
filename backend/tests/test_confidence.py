"""Tests para confidence.py — ver AUDITORIA_PRECISION.md."""

from app.collectors import confidence


def test_static_scores_only_for_present_fields():
    snapshot = {"cpu": {"percent": 10}, "mem": {"percent": 20}}
    result = confidence.for_server(snapshot)
    assert "cpu" in result and "mem" in result
    assert "disk" not in result  # no estaba en el snapshot


def test_disk_temp_omitted_when_none():
    snapshot = {"cpu": {"percent": 10}, "disk_temp": None}
    assert "disk_temp" not in confidence.for_server(snapshot)


def test_disk_temp_present_when_available():
    snapshot = {"disk_temp": 42.0}
    result = confidence.for_server(snapshot)
    assert result["disk_temp"]["score"] == confidence.STATIC_SCORES["disk_temp"]["score"]


def test_power_unavailable_scores_zero():
    result = confidence.score_power({"available": False}, None)
    assert result["score"] == 0


def test_power_stale_lowers_score():
    fresh = confidence.score_power({"available": True}, power_age_s=5)
    stale = confidence.score_power({"available": True}, power_age_s=700)
    assert stale["score"] < fresh["score"]
    assert "min" in stale["note"]


def test_autonomy_unvalidated_is_low_confidence():
    result = confidence.score_autonomy(None)
    assert result["score"] < 30
    result2 = confidence.score_autonomy({"validated": False, "complete_discharges": 0})
    assert result2["score"] < 30


def test_autonomy_validated_raises_confidence_with_more_discharges():
    one = confidence.score_autonomy({"validated": True, "complete_discharges": 1})
    three = confidence.score_autonomy({"validated": True, "complete_discharges": 3})
    assert three["score"] > one["score"]


class TestDynamicScoring:
    """El score no es una tabla fija por tipo de sensor -- baja si el dato
    esta ausente, es fisicamente imposible, o esta congelado (ver pedido de
    hacerlo dinamico, sesion de seguimiento a la auditoria)."""

    def test_sensor_missing_scores_zero(self):
        result = confidence.score_bounded_metric("cpu", None, None, 0, 100)
        assert result["score"] == 0

    def test_impossible_value_scores_very_low(self):
        result = confidence.score_bounded_metric("cpu", 150, None, 0, 100)
        assert result["score"] <= 5
        assert "rango fisico" in result["note"]

    def test_frozen_reading_lowers_score(self):
        fresh = confidence.score_bounded_metric("cpu", 12, [10, 11, 12, 13, 12], 0, 100)
        frozen = confidence.score_bounded_metric("cpu", 12, [12, 12, 12, 12, 12], 0, 100)
        assert frozen["score"] < fresh["score"]
        assert "sin cambios" in frozen["note"]

    def test_too_few_history_samples_not_flagged_frozen(self):
        result = confidence.score_bounded_metric("cpu", 12, [12, 12], 0, 100)
        assert result["note"] is None

    def test_cpu_temp_impossible_value(self):
        result = confidence.score_cpu_temp({"temp": 300, "temp_per_core": []})
        assert result["score"] <= 5

    def test_cpu_temp_discrepancy_with_per_core(self):
        result = confidence.score_cpu_temp({"temp": 90, "temp_per_core": [45, 46, 44, 45]})
        assert result["score"] < confidence.STATIC_SCORES["cpu_temp"]["score"]
        assert "discrepancia" in result["note"]

    def test_power_impossible_value(self):
        result = confidence.score_power({"available": True, "power_now_w": 999}, power_age_s=5)
        assert result["score"] <= 5
        assert "imposible" in result["note"]

    def test_clock_synced(self):
        result = confidence.score_clock(0.5)
        assert result["score"] >= 90
        assert result["note"] is None

    def test_clock_desynced(self):
        result = confidence.score_clock(120.0)
        assert result["score"] < 30
        assert "desincronizado" in result["note"]

    def test_connectivity_stable(self):
        result = confidence.score_connectivity(1.0)
        assert result["score"] >= 90

    def test_connectivity_flaky(self):
        result = confidence.score_connectivity(0.5)
        assert result["score"] < 60
        assert "no respondieron" in result["note"]


class TestTelemetryHealth:
    def test_empty_confidence_scores_healthy_zero(self):
        result = confidence.telemetry_health({})
        assert result["score"] == 0
        assert result["checks"] == []

    def test_all_high_scores_label_excelente(self):
        scores = {"cpu": {"score": 98, "note": None}, "mem": {"score": 97, "note": None}}
        result = confidence.telemetry_health(scores)
        assert result["label"] == "Excelente"
        assert all(c["ok"] for c in result["checks"])

    def test_low_scores_flagged_as_warn_and_sorted_first(self):
        scores = {
            "cpu": {"score": 98, "note": None},
            "power": {"score": 20, "note": "sin cambiar hace 14 min"},
        }
        result = confidence.telemetry_health(scores)
        assert result["checks"][0]["ok"] is False
        assert "14 min" in result["checks"][0]["text"]

    def test_label_matches_average_score_tiers(self):
        assert confidence.telemetry_health({"a": {"score": 95, "note": None}})["label"] == "Excelente"
        assert confidence.telemetry_health({"a": {"score": 80, "note": None}})["label"] == "Buena"
        assert confidence.telemetry_health({"a": {"score": 60, "note": None}})["label"] == "Advertencia"
        assert confidence.telemetry_health({"a": {"score": 20, "note": None}})["label"] == "Crítica"
