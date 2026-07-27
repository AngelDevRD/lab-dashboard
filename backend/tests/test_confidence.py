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
